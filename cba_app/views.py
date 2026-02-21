from django.shortcuts import render, redirect, get_object_or_404
from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.utils.http import urlencode
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout as auth_logout
from django.contrib.auth.forms import UserCreationForm
import os
import time
import requests

try:
    import cloudinary.uploader as cloudinary_uploader  # type: ignore
except Exception:  # pragma: no cover
    cloudinary_uploader = None  # type: ignore

from urllib.parse import urlparse, parse_qsl, urlencode as urlencode_qs, urlunparse

import secrets
import json

from .models import (
    Criterion,
    Alternative,
    Attribute,
    Advantage,
    CBAResult,
    ResultadoCBA,
    GraficaCostoVentaja,
    SharedGuideLink,
    UserProfile,
    GuideDocument,
)
from .forms import (
    AlternativeForm,
    CriterionForm,
    GuidePdfUploadForm,
    CBASetupForm,
    GuideShareLinkForm,
    GuideSharedPasswordForm,
    SignUpForm,
    ProfileForm,
    ProfilePhotoForm,
)
from .ai import generate_decision_assistant_text, generate_inconsistency_report_text
from .guide_meta import ensure_guide_meta, compute_and_store_guide_meta


def _delete_cloudinary_image_if_possible(value) -> None:
    """Intenta borrar el recurso en Cloudinary (si aplica).

    CloudinaryField suele exponer `public_id` (CloudinaryResource). En algunos casos puede
    ser un string persistido. Si no hay Cloudinary disponible o no hay public_id, no hace nada.
    """

    if cloudinary_uploader is None or not value:
        return

    public_id = None
    try:
        public_id = getattr(value, "public_id", None) or None
    except Exception:
        public_id = None

    if not public_id and isinstance(value, str):
        public_id = value.strip() or None

    if not public_id:
        return

    try:
        cloudinary_uploader.destroy(public_id, invalidate=True, resource_type="image")
    except Exception:
        # No queremos tumbar el guardado del perfil si Cloudinary falla.
        return


def _stream_pdf_from_storage(request, storage_name: str, *, as_attachment: bool, filename: str):
    """Entrega un PDF desde default_storage.

    - Primero intenta `default_storage.open()` (rápido en storage local y algunos remotos).
    - Si falla (frecuente con Cloudinary/raw), intenta hacer proxy streaming desde `default_storage.url()`.
    - Soporta header Range para que PDF.js pueda pedir porciones.
    """

    try:
        fh = default_storage.open(storage_name, "rb")
        return FileResponse(
            fh,
            content_type="application/pdf",
            as_attachment=as_attachment,
            filename=filename,
        )
    except Exception:
        pass

    try:
        source_url = default_storage.url(storage_name)
    except Exception:
        raise Http404("No hay guía disponible.")

    headers = {}
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    try:
        upstream = requests.get(source_url, stream=True, headers=headers, timeout=(5, 30))
    except Exception:
        raise Http404("No hay guía disponible.")

    if upstream.status_code not in (200, 206):
        try:
            upstream.close()
        except Exception:
            pass
        raise Http404("No hay guía disponible.")

    def body_iter():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 512):
                if chunk:
                    yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass

    resp = StreamingHttpResponse(body_iter(), content_type="application/pdf", status=upstream.status_code)
    if as_attachment:
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    else:
        resp["Content-Disposition"] = f'inline; filename="{filename}"'

    # Propagar headers útiles para PDF.js si Cloudinary respondió 206.
    for h in ("Accept-Ranges", "Content-Range", "Content-Length"):
        v = upstream.headers.get(h)
        if v:
            resp[h] = v
    return resp


def _safe_storage_exists(storage_name: str) -> bool:
    """Devuelve si existe un archivo en default_storage sin tumbar la vista.

    En producción, `default_storage` puede apuntar a Cloudinary u otro backend.
    Si hay mala configuración (credenciales/URL), `exists()` puede lanzar excepción.
    Para pantallas como Home/Login preferimos degradar (mostrar que no hay PDF) y no 500.
    """

    try:
        return bool(default_storage.exists(storage_name))
    except Exception:
        return False


def _get_guide_storage_name() -> str | None:
    """Devuelve el nombre real del PDF de guía en el storage, si está registrado."""

    try:
        doc = GuideDocument.objects.order_by("-updated_at").first()
    except Exception:
        doc = None
    name = (getattr(doc, "storage_name", "") or "").strip() if doc else ""
    return name or None


def _powerbi_token_ok(request) -> bool:
    """Valida el token para endpoints de feed (Power BI).

    Configuración esperada:
    - settings.POWER_BI_FEED_TOKEN (recomendado)
    Token recibido:
    - ?token=... o header X-PowerBI-Token
    """

    expected = (getattr(settings, "POWER_BI_FEED_TOKEN", "") or "").strip()
    if not expected:
        return False

    candidate = (request.GET.get("token") or request.headers.get("X-PowerBI-Token") or "").strip()
    if not candidate:
        return False

    try:
        return secrets.compare_digest(candidate, expected)
    except Exception:
        return False


def powerbi_feed_results(request):
    """Tabla resumen de resultados guardados para Power BI (JSON)."""

    if not _powerbi_token_ok(request):
        return JsonResponse({"ok": False, "error": "Token inválido"}, status=403)

    limit = request.GET.get("limit")
    try:
        limit_n = int(limit) if limit else 200
    except (TypeError, ValueError):
        limit_n = 200
    limit_n = max(1, min(1000, limit_n))

    results = CBAResult.objects.order_by("-created_at")[:limit_n]
    rows = []

    for r in results:
        setup = None
        project_name = None
        sector = None
        location = None
        try:
            payload = json.loads(r.data_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            setup = payload.get("setup")
        if isinstance(setup, dict):
            project_name = setup.get("project_name")
            sector = setup.get("sector")
            location = setup.get("location")

        rows.append(
            {
                "result_id": r.id,
                "result_name": r.name,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "project_name": project_name,
                "sector": sector,
                "location": location,
                "winner_name": r.winner_name,
                "winner_total": r.winner_total,
                "winner_cost": float(r.winner_cost) if r.winner_cost is not None else None,
                "winner_ratio": r.winner_ratio,
                "power_bi_url": r.power_bi_url,
            }
        )

    return JsonResponse(rows, safe=False)


def powerbi_feed_dashboard_rows(request):
    """Tabla plana (1 fila por alternativa por resultado) para Power BI (JSON)."""

    if not _powerbi_token_ok(request):
        return JsonResponse({"ok": False, "error": "Token inválido"}, status=403)

    result_id = request.GET.get("result_id")
    qs = CBAResult.objects.order_by("-created_at")

    if result_id:
        try:
            rid = int(result_id)
            qs = qs.filter(id=rid)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "result_id inválido"}, status=400)

    limit = request.GET.get("limit")
    try:
        limit_n = int(limit) if limit else 50
    except (TypeError, ValueError):
        limit_n = 50
    limit_n = max(1, min(200, limit_n))

    results = list(qs[:limit_n])
    rows = []

    for r in results:
        setup = None
        project_name = None
        sector = None
        location = None
        dashboard_payload = []

        try:
            payload = json.loads(r.data_json or "{}")
        except json.JSONDecodeError:
            payload = {}

        if isinstance(payload, dict):
            setup = payload.get("setup")
            dashboard_payload = payload.get("dashboard") or payload.get("chart_data") or []
        elif isinstance(payload, list):
            dashboard_payload = payload

        if isinstance(setup, dict):
            project_name = setup.get("project_name")
            sector = setup.get("sector")
            location = setup.get("location")

        for item in dashboard_payload or []:
            if not isinstance(item, dict):
                continue

            name = (item.get("name") or "").strip()
            cost = item.get("cost")
            total = item.get("total")
            ratio = item.get("ratio")

            try:
                cost_value = float(cost) if cost is not None else None
            except (TypeError, ValueError):
                cost_value = None
            try:
                total_value = int(total) if total is not None else None
            except (TypeError, ValueError):
                total_value = None
            try:
                ratio_value = float(ratio) if ratio is not None else None
            except (TypeError, ValueError):
                ratio_value = None

            rows.append(
                {
                    "result_id": r.id,
                    "result_name": r.name,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "project_name": project_name,
                    "sector": sector,
                    "location": location,
                    "alternative": name,
                    "cost": cost_value,
                    "total": total_value,
                    "ratio": ratio_value,
                    "winner_name": r.winner_name,
                }
            )

    return JsonResponse(rows, safe=False)


def powerbi_feed_grafica_costo_ventaja(request):
    """Tabla para Power BI (2 filas por candidato: 0/0 y valor) (JSON)."""

    if not _powerbi_token_ok(request):
        return JsonResponse({"ok": False, "error": "Token inválido"}, status=403)

    limit = request.GET.get("limit")
    try:
        limit_n = int(limit) if limit else 1000
    except (TypeError, ValueError):
        limit_n = 1000
    limit_n = max(1, min(10000, limit_n))

    proyecto = (request.GET.get("proyecto") or "").strip()
    puesto = (request.GET.get("puesto") or "").strip()

    qs = GraficaCostoVentaja.objects.all()
    if proyecto:
        qs = qs.filter(proyectos=proyecto)
    if puesto:
        qs = qs.filter(puesto=puesto)

    qs = qs.order_by("proyectos", "puesto", "candidatos", "id")[:limit_n]

    rows = []
    for r in qs:
        rows.append(
            {
                "PROYECTOS": r.proyectos,
                "PUESTO": r.puesto,
                "CANDIDATOS": r.candidatos,
                "COSTO": float(r.costo or 0),
                "VENTAJA": float(r.ventaja or 0),
            }
        )

    return JsonResponse(rows, safe=False)


@login_required
def cba_home(request):
    """Panel principal con las opciones generales."""
    import json

    total_results = CBAResult.objects.count()
    guide_pdf_available = _safe_storage_exists("guides/guia.pdf")

    latest_result = CBAResult.objects.order_by("-created_at").first()
    latest_setup = None
    latest_items_raw = []

    if latest_result:
        try:
            payload = json.loads(latest_result.data_json or "{}")
        except json.JSONDecodeError:
            payload = {}

        if isinstance(payload, dict):
            latest_setup = payload.get("setup")
            latest_items_raw = payload.get("dashboard") or payload.get("chart_data") or []
        elif isinstance(payload, list):
            latest_items_raw = payload

    normalized_items = []
    for item in (latest_items_raw or []):
        if not isinstance(item, dict):
            continue

        name = (
            item.get("name")
            or item.get("candidatos")
            or item.get("CANDIDATOS")
            or ""
        ).strip()

        cost = item.get("cost")
        if cost is None:
            cost = item.get("costo")
        if cost is None:
            cost = item.get("COSTO")

        total = item.get("total")
        if total is None:
            total = item.get("ventaja")
        if total is None:
            total = item.get("VENTAJA")

        ratio = item.get("ratio")
        if ratio is None:
            ratio = item.get("RATIO")

        try:
            cost_value = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_value = None

        try:
            total_value = float(total) if total is not None else 0
        except (TypeError, ValueError):
            total_value = 0

        if ratio is None and cost_value is not None and total_value not in (None, 0):
            try:
                ratio = float(cost_value) / float(total_value)
            except (ZeroDivisionError, ValueError):
                ratio = None
        else:
            try:
                ratio = float(ratio) if ratio is not None else None
            except (TypeError, ValueError):
                ratio = None

        if not name:
            continue

        normalized_items.append(
            {
                "name": name,
                "cost": cost_value,
                "total": total_value,
                "ratio": ratio,
            }
        )

    normalized_items.sort(key=lambda p: p.get("total") or 0, reverse=True)

    context = {
        "total_results": total_results,
        "guide_pdf_available": guide_pdf_available,
        "latest_result": latest_result,
        "latest_setup": latest_setup,
        "latest_items": normalized_items,
        "latest_dashboard_json": json.dumps(normalized_items, ensure_ascii=False),
    }
    return render(request, "cba_app/home.html", context)


# Paso 1: Identificación de alternativas


@login_required
def cba_step1(request):
    setup = request.session.get("cba_setup")
    force_setup = request.GET.get("setup") == "1"

    if force_setup or not setup:
        if request.method == "POST" and request.POST.get("form_name") == "cba_setup":
            form = CBASetupForm(request.POST)
            if form.is_valid():
                request.session["cba_setup"] = {
                    "sector": form.cleaned_data["sector"],
                    "project_name": form.cleaned_data["project_name"].strip(),
                    "location": form.cleaned_data["location"].strip(),
                    "requesting_area": form.cleaned_data["requesting_area"].strip(),
                    "reference_budget": (form.cleaned_data.get("reference_budget") or "").strip(),
                    "objective": form.cleaned_data["objective"].strip(),
                    "public_entity": (form.cleaned_data.get("public_entity") or "").strip(),
                    "private_company": (form.cleaned_data.get("private_company") or "").strip(),
                }
                return redirect("cba_step1")
        else:
            form = CBASetupForm(initial=setup or None)

        return render(request, "cba_app/setup.html", {"form": form, "setup": setup})

    # Identificar si se está editando alguna alternativa (modo GET)
    edit_id = request.GET.get("edit")
    editing_alt = None
    if edit_id:
        try:
            editing_alt = Alternative.objects.get(id=edit_id)
        except Alternative.DoesNotExist:
            editing_alt = None

    if request.method == "POST":
        # Eliminar alternativa si viene un id de borrado
        delete_id = request.POST.get("delete_id")
        if delete_id:
            Alternative.objects.filter(id=delete_id).delete()
            return redirect("cba_step1")

        # Actualizar nombre si viene un id de actualización
        update_id = request.POST.get("update_id")
        if update_id:
            alt = Alternative.objects.filter(id=update_id).first()
            if alt:
                new_name = request.POST.get("name", "").strip()
                if new_name:
                    alt.name = new_name
                    alt.save()
            return redirect("cba_step1")

        # Alta normal de alternativa
        form = AlternativeForm(request.POST)
        if form.is_valid():
            form.save()
            # Se queda en el Paso 1 para poder seguir agregando postores
            return redirect("cba_step1")
    else:
        form = AlternativeForm()

    alternatives = Alternative.objects.all()
    context = {
        "alternatives": alternatives,
        "form": form,
        "editing_alt": editing_alt,
        "setup": setup,
    }
    return render(request, "cba_app/step1.html", context)


# Paso 2: Definir los factores de decisión


@login_required
def cba_step2(request):
    setup = request.session.get("cba_setup")
    # Identificar si se está editando algún factor (modo GET)
    edit_id = request.GET.get("edit")
    editing_crit = None
    if edit_id:
        try:
            editing_crit = Criterion.objects.get(id=edit_id)
        except Criterion.DoesNotExist:
            editing_crit = None

    if request.method == "POST":
        # Eliminar factor si viene un id de borrado
        delete_id = request.POST.get("delete_id")
        if delete_id:
            Criterion.objects.filter(id=delete_id).delete()
            return redirect("cba_step2")

        # Actualizar nombre si viene un id de actualización
        update_id = request.POST.get("update_id")
        if update_id:
            crit = Criterion.objects.filter(id=update_id).first()
            if crit:
                new_name = request.POST.get("name", "").strip()
                if new_name:
                    crit.name = new_name
                    crit.save()
            return redirect("cba_step2")

        # Alta normal de factor
        form = CriterionForm(request.POST)
        if form.is_valid():
            form.save()
            # Se queda en el Paso 2 para poder seguir agregando factores
            return redirect("cba_step2")
    else:
        form = CriterionForm()

    criteria = Criterion.objects.all()
    context = {
        "criteria": criteria,
        "form": form,
        "editing_crit": editing_crit,
        "setup": setup,
    }
    return render(request, "cba_app/step2.html", context)


# Paso 3: Definir si cada criterio debe tener o desea tener


@login_required
def cba_step3(request):
    setup = request.session.get("cba_setup")
    if request.method == "POST":
        # Actualizar el tipo (indispensable/deseable) y descripción de cada criterio
        for c in Criterion.objects.all():
            type_key = f"type_{c.id}"
            desc_key = f"desc_{c.id}"
            criterion_type = request.POST.get(type_key)
            description = request.POST.get(desc_key, "")
            if criterion_type in {Criterion.TYPE_MUST, Criterion.TYPE_WANT}:
                c.criterion_type = criterion_type
            c.description = description
            c.save()
        return redirect("cba_step4")
    criteria = Criterion.objects.all()
    return render(request, "cba_app/step3.html", {"criteria": criteria, "setup": setup})


# Paso 4: Describir los atributos de cada alternativa


@login_required
def cba_step4(request):
    setup = request.session.get("cba_setup")
    criteria = list(Criterion.objects.all())
    alternatives = list(Alternative.objects.all())

    if request.method == "POST":
        # Guardar cada celda de la matriz Factor vs Postor como un Attribute
        for criterion in criteria:
            for alternative in alternatives:
                field_name = f"attr_{criterion.id}_{alternative.id}"
                value = request.POST.get(field_name, "").strip()
                if not value:
                    continue
                attr, _ = Attribute.objects.get_or_create(
                    criterion=criterion,
                    alternative=alternative,
                )
                attr.description = value
                attr.save()
        return redirect("cba_step5")

    # Preparar matriz para la plantilla: filas = factores, columnas = postores
    attributes = Attribute.objects.select_related("criterion", "alternative")
    attr_map = {
        (a.criterion_id, a.alternative_id): a.description for a in attributes
    }

    rows = []
    for criterion in criteria:
        cells = []
        for alternative in alternatives:
            existing = attr_map.get((criterion.id, alternative.id), "")
            cells.append(
                {
                    "alternative": alternative,
                    "name": f"attr_{criterion.id}_{alternative.id}",
                    "value": existing,
                }
            )
        rows.append({"criterion": criterion, "cells": cells})

    # Opciones típicas tipo Excelente/Bueno/Regular/Cumple
    options = ["Excelente", "Bueno", "Regular", "Cumple"]

    context = {
        "alternatives": alternatives,
        "rows": rows,
        "options": options,
        "setup": setup,
    }
    return render(request, "cba_app/step4.html", context)


# Paso 5: Subrayar el atributo menos preferido de cada factor


@login_required
def cba_step5(request):
    setup = request.session.get("cba_setup")
    # Recalcular automáticamente el atributo menos preferido por factor
    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    attributes = list(
        Attribute.objects.select_related("criterion", "alternative").all()
    )

    # Agrupar por criterio y encontrar el peor valor (menor puntuación)
    by_criterion = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    for attrs in by_criterion.values():
        worst_score = None
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score is None:
                continue
            if worst_score is None or score < worst_score:
                worst_score = score
        if worst_score is None:
            continue
        for attr in attrs:
            score = rating_order.get(attr.description)
            attr.is_least_preferred = score == worst_score
            attr.save()

    # Preparar matriz FACTORES x Postores solo con el valor del menos preferido
    criteria = list(Criterion.objects.all())
    alternatives = list(Alternative.objects.all())
    attributes = Attribute.objects.select_related("criterion", "alternative")
    least_map = {
        (a.criterion_id, a.alternative_id): a.description
        for a in attributes
        if a.is_least_preferred
    }

    rows = []
    least_summary = []
    for criterion in criteria:
        cells = []
        for alternative in alternatives:
            value = least_map.get((criterion.id, alternative.id), "")
            if value:
                least_summary.append(
                    {"criterion": criterion, "alternative": alternative, "value": value}
                )
            cells.append({"alternative": alternative, "value": value})
        rows.append({"criterion": criterion, "cells": cells})

    if request.method == "POST":
        return redirect("cba_step6")

    context = {
        "alternatives": alternatives,
        "rows": rows,
        "least_summary": least_summary,
        "setup": setup,
    }
    return render(request, "cba_app/step5.html", context)


# Paso 6: Decidir las ventajas de cada alternativa


@login_required
def cba_step6(request):
    setup = request.session.get("cba_setup")
    # Calcular automáticamente, para cada factor, la mayor ventaja (mejor valoración)
    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    attributes = list(
        Attribute.objects.select_related("criterion", "alternative").all()
    )

    # Agrupar por criterio y encontrar el mejor valor (mayor puntuación)
    by_criterion = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    best_map = {}
    for crit_id, attrs in by_criterion.items():
        best_score = None
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
        if best_score is None:
            continue
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score == best_score:
                best_map[(attr.criterion_id, attr.alternative_id)] = attr.description

    criteria = list(Criterion.objects.all())
    alternatives = list(Alternative.objects.all())

    rows = []
    for criterion in criteria:
        cells = []
        for alternative in alternatives:
            value = best_map.get((criterion.id, alternative.id), "")
            cells.append({"alternative": alternative, "value": value})
        rows.append({"criterion": criterion, "cells": cells})

    if request.method == "POST":
        return redirect("cba_step7")

    context = {
        "alternatives": alternatives,
        "rows": rows,
        "setup": setup,
    }
    return render(request, "cba_app/step6.html", context)


# Paso 7: Definir la ventaja principal


@login_required
def cba_step7(request):
    setup = request.session.get("cba_setup")
    # Usar como referencia la misma lógica del Paso 6 (mejor ventaja por factor)
    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    attributes = list(
        Attribute.objects.select_related("criterion", "alternative").all()
    )

    # Repetimos el cálculo del Paso 6: best_map solo contiene las celdas donde el postor
    # tiene la mejor valoración de ese factor.
    by_criterion = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    best_map = {}
    for crit_id, attrs in by_criterion.items():
        best_score = None
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
        if best_score is None:
            continue
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score == best_score:
                best_map[(attr.criterion_id, attr.alternative_id)] = attr

    criteria = list(Criterion.objects.all())  # orden: el de más arriba es más importante
    alternatives = list(Alternative.objects.all())

    # Para cada postor, solo consideramos las celdas donde aparece en best_map (es decir,
    # donde ya tiene ventaja en el Paso 6) y de esas elegimos el factor que esté más arriba.
    main_by_alt = {}
    for alternative in alternatives:
        chosen_attr = None
        for criterion in criteria:
            key = (criterion.id, alternative.id)
            attr = best_map.get(key)
            if attr is not None:
                chosen_attr = attr
                break
        if chosen_attr is not None:
            main_by_alt[alternative.id] = chosen_attr

    # Actualizar/crear objetos Advantage y marcar solo uno como principal por postor
    main_rows = []
    for alternative in alternatives:
        attr = main_by_alt.get(alternative.id)
        if not attr:
            continue
        criterion = attr.criterion
        desc_text = attr.description or ""

        adv, _ = Advantage.objects.get_or_create(
            criterion=criterion,
            alternative=alternative,
            defaults={"description": desc_text, "importance": 0},
        )
        adv.description = desc_text
        adv.is_main = True
        adv.save()

        # Poner en False otras ventajas de este postor
        Advantage.objects.filter(alternative=alternative).exclude(id=adv.id).update(
            is_main=False
        )

        main_rows.append(
            {
                "alternative": alternative,
                "criterion": criterion,
                "value": desc_text,
            }
        )

    # Construir matriz FACTORES x Postores con solo la ventaja principal marcada
    main_map = {
        (row["criterion"].id, row["alternative"].id): row["value"]
        for row in main_rows
    }

    matrix_rows = []
    for criterion in criteria:
        cells = []
        for alternative in alternatives:
            value = main_map.get((criterion.id, alternative.id), "")
            cells.append({"alternative": alternative, "value": value})
        matrix_rows.append({"criterion": criterion, "cells": cells})

    if request.method == "POST":
        return redirect("cba_step8")

    context = {"alternatives": alternatives, "rows": matrix_rows, "setup": setup}
    return render(request, "cba_app/step7.html", context)


# Paso 8: Decidir la importancia de cada ventaja


@login_required
def cba_step8(request):
    setup = request.session.get("cba_setup")
    # En este paso se asigna importancia numérica usando como referencia
    # la misma matriz de "mejor ventaja" del Paso 6.

    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    alternatives = list(Alternative.objects.all())
    criteria = list(Criterion.objects.all())

    attributes = list(
        Attribute.objects.select_related("criterion", "alternative").all()
    )

    # Igual que en Paso 6: best_map contiene solo las celdas con mejor valoración por factor
    by_criterion = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    best_attrs = {}
    for crit_id, attrs in by_criterion.items():
        best_score = None
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
        if best_score is None:
            continue
        for attr in attrs:
            score = rating_order.get(attr.description)
            if score == best_score:
                best_attrs[(attr.criterion_id, attr.alternative_id)] = attr

    # Crear/actualizar ventajas (Advantage) solo para estas celdas de mejor ventaja
    adv_map = {}
    for (crit_id, alt_id), attr in best_attrs.items():
        adv, _ = Advantage.objects.get_or_create(
            criterion=attr.criterion,
            alternative=attr.alternative,
            defaults={"description": attr.description, "importance": 0},
        )
        # Mantener la descripción alineada con el atributo
        adv.description = attr.description
        adv_map[(crit_id, alt_id)] = adv

    if request.method == "POST":
        # Leer los valores numéricos de importancia solo donde hay ventaja en Paso 6
        for (crit_id, alt_id), adv in adv_map.items():
            field_name = f"imp_{crit_id}_{alt_id}"
            val = request.POST.get(field_name, "").strip()
            if not val:
                continue
            try:
                adv.importance = int(val)
                adv.save()
            except ValueError:
                continue
        return redirect("cba_step9")

    # Construir matriz FACTORES x Postores con inputs numéricos
    # solo en las celdas con mejor ventaja (como en la imagen de ejemplo)
    rows = []
    for criterion in criteria:
        cells = []
        for alternative in alternatives:
            key = (criterion.id, alternative.id)
            adv = adv_map.get(key)
            cells.append(
                {
                    "alternative": alternative,
                    "has_advantage": adv is not None,
                    "name": f"imp_{criterion.id}_{alternative.id}",
                    "value": adv.importance if adv else "",
                }
            )
        rows.append({"criterion": criterion, "cells": cells})

    context = {"alternatives": alternatives, "rows": rows, "setup": setup}
    return render(request, "cba_app/step8.html", context)


# Paso 9: Calcular la importancia total de las ventajas


@login_required
def cba_step9(request):
    setup = request.session.get("cba_setup")
    # Si el usuario pulsa el botón, avanzamos al Paso 10
    if request.method == "POST":
        return redirect("cba_step10")

    alternatives = Alternative.objects.all()
    totals = []
    for alt in alternatives:
        # Sumar únicamente las importancias cargadas en el Paso 8
        total_importance = sum(a.importance for a in alt.advantages.all())
        totals.append({"alternative": alt, "total_importance": total_importance})
    totals.sort(key=lambda x: x["total_importance"], reverse=True)
    return render(request, "cba_app/step9.html", {"totals": totals, "setup": setup})


# Paso 10: Evaluar costo versus ventaja


@login_required
def cba_step10(request):
    import json
    from decimal import Decimal, InvalidOperation
    from django.utils import timezone

    setup = request.session.get("cba_setup")
    alternatives = list(Alternative.objects.all())

    save_and_close = False

    # Si vienen costos editados, los actualizamos primero
    if request.method == "POST":
        save_and_close = "save_close" in request.POST

        for alt in alternatives:
            field_name = f"cost_{alt.id}"
            raw_value = request.POST.get(field_name, "").strip()
            if not raw_value:
                continue
            try:
                alt.cost = Decimal(raw_value)
                alt.save()
            except InvalidOperation:
                # Si el valor no es numérico, lo ignoramos
                continue

    # Recalcular totales y relación costo / ventaja
    rows = []
    best_row = None
    for alt in alternatives:
        total_importance = sum(a.importance for a in alt.advantages.all())
        cost = alt.cost
        ratio = None
        if total_importance and cost is not None:
            try:
                ratio = float(cost) / float(total_importance)
            except (ZeroDivisionError, ValueError):
                ratio = None
        row = {
            "alternative": alt,
            "total_importance": total_importance,
            "cost": cost,
            "ratio": ratio,
        }
        rows.append(row)

        if ratio is not None:
            if best_row is None or ratio < best_row["ratio"]:
                best_row = row

    # Ordenamos por total de ventajas, como en la tabla de ejemplo
    rows.sort(key=lambda x: x["total_importance"], reverse=True)

    # Datos simples para que el usuario pueda graficar
    chart_data = [
        {
            "name": row["alternative"].name,
            "cost": float(row["cost"]) if row["cost"] is not None else None,
            "total": row["total_importance"],
            "ratio": row.get("ratio"),
        }
        for row in rows
    ]

    # Si el usuario pulsa Guardar y cerrar, registramos el resultado y volvemos al panel principal
    if save_and_close:
        winner_name = best_row["alternative"].name if best_row else "Sin ganador"
        winner_total = best_row["total_importance"] if best_row else 0
        winner_cost = best_row["cost"] if best_row else None
        winner_ratio = best_row["ratio"] if best_row else None

        base_name = "Análisis CBA"
        if setup and isinstance(setup, dict) and setup.get("project_name"):
            base_name = f"CBA - {setup.get('project_name')}"

        saved = CBAResult.objects.create(
            name=f"{base_name} {timezone.now().strftime('%Y-%m-%d %H:%M')}",
            winner_name=winner_name,
            winner_total=winner_total,
            winner_cost=winner_cost,
            winner_ratio=winner_ratio,
            data_json=json.dumps({"setup": setup, "dashboard": chart_data}, ensure_ascii=False),
        )

        # Insertar tabla plana para Power BI
        proyecto = None
        puesto = None
        if setup and isinstance(setup, dict):
            proyecto = (setup.get("project_name") or "").strip() or None
            puesto = (setup.get("requesting_area") or "").strip() or None

        proyecto = (proyecto or saved.name or "").strip()[:255]
        if puesto:
            puesto = puesto[:150]

        from decimal import Decimal, InvalidOperation

        def _to_decimal(value):
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return None

        flat_rows = []
        chart_rows = []
        for item in chart_data or []:
            if not isinstance(item, dict):
                continue
            candidato = (item.get("name") or "").strip()[:150]

            costo_dec = _to_decimal(item.get("cost"))
            ventaja_dec = _to_decimal(item.get("total"))
            flat_rows.append(
                ResultadoCBA(
                    result=saved,
                    proyecto=proyecto,
                    puesto=puesto,
                    candidato=candidato,
                    costo=costo_dec,
                    ventaja=ventaja_dec,
                    costo_ventaja=_to_decimal(item.get("ratio")),
                    recomendado=bool(winner_name and candidato == winner_name),
                    fecha=saved.created_at or timezone.now(),
                )
            )

            # Tabla para Power BI: fila base 0/0 + fila valor
            chart_rows.append(
                GraficaCostoVentaja(
                    result=saved,
                    proyectos=proyecto,
                    puesto=puesto,
                    candidatos=candidato,
                    costo=_to_decimal(0) or 0,
                    ventaja=_to_decimal(0) or 0,
                )
            )
            chart_rows.append(
                GraficaCostoVentaja(
                    result=saved,
                    proyectos=proyecto,
                    puesto=puesto,
                    candidatos=candidato,
                    costo=costo_dec or 0,
                    ventaja=ventaja_dec or 0,
                )
            )

        if flat_rows:
            ResultadoCBA.objects.bulk_create(flat_rows, batch_size=200)

        if chart_rows:
            GraficaCostoVentaja.objects.bulk_create(chart_rows, batch_size=400)

        return redirect("cba_home")

    chart_data_json = json.dumps(chart_data)

    context = {
        "rows": rows,
        "best_row": best_row,
        "chart_data": chart_data,
        "chart_data_json": chart_data_json,
        "setup": setup,
    }
    return render(request, "cba_app/step10.html", context)


@login_required
def cba_saved_results(request):
    """Listado de resultados CBA guardados desde el Paso 10."""

    results = CBAResult.objects.order_by("-created_at")
    return render(request, "cba_app/saved_results.html", {"results": results})


@login_required
@require_POST
def cba_saved_result_delete(request, result_id: int):
    result = get_object_or_404(CBAResult, id=result_id)

    # Limpia también tablas Power BI (Postgres) para que no queden datos huérfanos.
    # 1) Camino ideal: filas nuevas ya tienen FK result_id.
    # 2) Fallback: si hay filas legacy (sin result_id), intenta limpiar por proyecto/puesto/candidatos.

    # Extrae llaves desde data_json (sirve para fallback y no depende del esquema de BD).
    proyecto = None
    puesto = None
    candidatos = []
    try:
        payload = json.loads(result.data_json or "{}")
    except Exception:
        payload = {}

    setup = None
    dashboard_payload = []
    if isinstance(payload, dict):
        setup = payload.get("setup")
        dashboard_payload = payload.get("dashboard") or payload.get("chart_data") or []
    elif isinstance(payload, list):
        dashboard_payload = payload

    if isinstance(setup, dict):
        proyecto = (setup.get("project_name") or "").strip() or None
        puesto = (setup.get("requesting_area") or "").strip() or None

    proyecto = (proyecto or result.name or "").strip()[:255]
    if puesto:
        puesto = puesto[:150]

    for item in dashboard_payload or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if name:
            candidatos.append(name[:150])

    # Borra por FK (si existe) y, además, limpia legacy por matching.
    try:
        ResultadoCBA.objects.filter(result=result).delete()
    except Exception:
        pass

    try:
        GraficaCostoVentaja.objects.filter(result=result).delete()
    except Exception:
        pass

    if candidatos:
        try:
            # ResultadoCBA sí tiene fecha; esto lo hace más exacto en legacy.
            ResultadoCBA.objects.filter(
                result__isnull=True,
                proyecto=proyecto,
                puesto=puesto,
                candidato__in=candidatos,
                fecha=result.created_at,
            ).delete()
        except Exception:
            pass

        try:
            # GraficaCostoVentaja no tiene fecha; filtramos por proyecto/puesto/candidatos.
            GraficaCostoVentaja.objects.filter(
                result__isnull=True,
                proyectos=proyecto,
                puesto=puesto,
                candidatos__in=candidatos,
            ).delete()
        except Exception:
            pass

    result.delete()
    return redirect("cba_saved_results")


def _build_step10_rows_and_best():
    from decimal import Decimal

    alternatives = list(Alternative.objects.all())
    rows = []
    best_row = None

    for alt in alternatives:
        total_importance = sum(a.importance for a in alt.advantages.all())
        cost = alt.cost
        ratio = None
        if total_importance and cost is not None:
            try:
                ratio = float(cost) / float(total_importance)
            except (ZeroDivisionError, ValueError):
                ratio = None

        row = {
            "alternative": alt,
            "total_importance": total_importance,
            "cost": cost,
            "ratio": ratio,
        }
        rows.append(row)

        if ratio is not None:
            if best_row is None or ratio < best_row["ratio"]:
                best_row = row

    rows.sort(key=lambda x: x["total_importance"], reverse=True)
    return rows, best_row


def _normalize_dashboard_payload(payload: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for item in payload or []:
        if not isinstance(item, dict):
            continue

        clean = dict(item)
        ratio_value = clean.get("ratio")
        try:
            ratio_value = float(ratio_value)
        except (TypeError, ValueError):
            ratio_value = None

        if ratio_value is None:
            try:
                cost_val = float(clean.get("cost"))
                total_val = float(clean.get("total"))
                if total_val:
                    ratio_value = cost_val / total_val
            except (TypeError, ValueError, ZeroDivisionError):
                ratio_value = None

        clean["ratio"] = ratio_value
        normalized.append(clean)
    return normalized


def _compute_ratio_stats(dashboard_payload: list[dict]):
    valid = [p for p in _normalize_dashboard_payload(dashboard_payload) if p.get("ratio") is not None]
    valid.sort(key=lambda p: p["ratio"])

    best = valid[0] if len(valid) > 0 else None
    second = valid[1] if len(valid) > 1 else None
    delta = None
    delta_pct = None
    if best and second:
        try:
            delta = float(second["ratio"]) - float(best["ratio"])
            if float(second["ratio"]) != 0:
                delta_pct = (delta / float(second["ratio"])) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            delta = None
            delta_pct = None
    return best, second, delta, delta_pct


def _winner_least_attributes(best_row):
    if not best_row:
        return []

    alternative = best_row.get("alternative") if isinstance(best_row, dict) else None
    alt_obj = None

    if alternative is not None:
        alt_id = getattr(alternative, "id", None)
        if alt_id:
            alt_obj = Alternative.objects.filter(id=alt_id).first()
        else:
            alt_name = getattr(alternative, "name", None)
            if alt_name:
                alt_obj = Alternative.objects.filter(name=alt_name).first()

    if not alt_obj:
        return []

    least_attrs = (
        Attribute.objects.filter(alternative=alt_obj, is_least_preferred=True)
        .select_related("criterion")
        .order_by("criterion__id")
    )

    output = []
    for attr in least_attrs:
        output.append(
            {
                "criterion": getattr(attr.criterion, "name", None),
                "description": attr.description,
            }
        )
    return output


def _build_saved_result_viewmodel(result: CBAResult) -> dict:
    setup = None
    dashboard_payload = []
    best_row = None

    try:
        payload = json.loads(result.data_json or "{}")
    except json.JSONDecodeError:
        payload = {}

    if isinstance(payload, dict):
        setup = payload.get("setup")
        dashboard_payload = payload.get("dashboard") or payload.get("chart_data") or []
        winner = payload.get("winner") or {}
        if winner.get("ratio") is not None:
            best_row = {
                "alternative": type("Alt", (), {"name": winner.get("name"), "id": None})(),
                "total_importance": winner.get("total") or result.winner_total,
                "cost": winner.get("cost") or result.winner_cost,
                "ratio": winner.get("ratio") or result.winner_ratio,
            }
    elif isinstance(payload, list):
        dashboard_payload = payload

    if not best_row and result.winner_name:
        best_row = {
            "alternative": type("Alt", (), {"name": result.winner_name, "id": None})(),
            "total_importance": result.winner_total,
            "cost": result.winner_cost,
            "ratio": result.winner_ratio,
        }

    dashboard_payload = _normalize_dashboard_payload(dashboard_payload)

    best_item, second_item, delta_ratio, delta_pct = _compute_ratio_stats(dashboard_payload)
    winner_main_advantage, winner_disadvantage = _winner_strengths_and_gaps(dashboard_payload)
    winner_least_attributes = _winner_least_attributes(best_row)

    return {
        "setup": setup,
        "best_row": best_row,
        "best_item": best_item,
        "second_item": second_item,
        "delta_ratio": delta_ratio,
        "delta_pct": delta_pct,
        "table_rows": dashboard_payload,
        "dashboard_json": json.dumps(dashboard_payload, ensure_ascii=False),
        "winner_main_advantage": winner_main_advantage,
        "winner_disadvantage": winner_disadvantage,
        "winner_least_attributes": winner_least_attributes,
        "summary_text": result.summary_text,
        "inconsistency_text": result.inconsistency_text,
    }


def _winner_strengths_and_gaps(dashboard_payload: list[dict]):
    normalized = [
        p
        for p in _normalize_dashboard_payload(dashboard_payload)
        if isinstance(p.get("name"), str) and p.get("name").strip()
    ]
    if not normalized:
        return None, None

    valid = [p for p in normalized if p.get("ratio") is not None]
    valid.sort(key=lambda p: p["ratio"])
    winner = valid[0] if valid else None
    if not winner:
        return None, None

    names = [p["name"] for p in normalized]
    alt_by_name = {
        alt.name: alt
        for alt in Alternative.objects.filter(name__in=names)
        .prefetch_related("advantages__criterion")
    }

    win_alt = alt_by_name.get(winner["name"])
    if not win_alt:
        return None, None

    win_advs = list(win_alt.advantages.all())

    main_advantage = None
    if win_advs:
        main = next((a for a in win_advs if getattr(a, "is_main", False)), None)
        if main is None:
            main = max(win_advs, key=lambda a: getattr(a, "importance", 0) or 0)
        if main is not None:
            main_advantage = {
                "criterion": getattr(main.criterion, "name", None),
                "description": main.description,
                "importance": getattr(main, "importance", None),
            }

    disadvantage = None
    if win_advs:
        criterion_ids = {a.criterion_id for a in win_advs if a.criterion_id}
        alt_adv_map = {name: list(alt.advantages.all()) for name, alt in alt_by_name.items()}

        deficits = []
        for crit_id in criterion_ids:
            win_adv = next((a for a in win_advs if a.criterion_id == crit_id), None)
            win_pts = getattr(win_adv, "importance", 0) if win_adv else 0

            best_other_name = None
            best_other_pts = None
            best_other_adv = None

            for alt_name, advs in alt_adv_map.items():
                if alt_name == win_alt.name:
                    continue
                peer_adv = next((a for a in advs if a.criterion_id == crit_id), None)
                pts = getattr(peer_adv, "importance", 0) if peer_adv else 0
                if best_other_pts is None or pts > best_other_pts:
                    best_other_pts = pts
                    best_other_name = alt_name
                    best_other_adv = peer_adv

            if best_other_pts is None:
                continue

            deficit = (best_other_pts or 0) - (win_pts or 0)
            if deficit <= 0:
                continue

            criterion_name = None
            try:
                criterion_name = (
                    win_adv.criterion.name
                    if win_adv and getattr(win_adv, "criterion", None)
                    else best_other_adv.criterion.name
                )
            except Exception:
                criterion_name = None

            deficits.append(
                {
                    "criterion": criterion_name,
                    "deficit": deficit,
                    "winner_points": win_pts,
                    "winner_adv": getattr(win_adv, "description", None),
                    "best_other": best_other_name,
                    "best_other_points": best_other_pts,
                    "best_other_adv": getattr(best_other_adv, "description", None),
                }
            )

        if deficits:
            deficits.sort(key=lambda d: d.get("deficit") or 0, reverse=True)
            disadvantage = deficits[0]

    return main_advantage, disadvantage


@login_required
def cba_dashboard(request):
    import json
    from django.utils import timezone
    from decimal import Decimal, InvalidOperation

    setup = request.session.get("cba_setup")
    rows, best_row = _build_step10_rows_and_best()

    dashboard_payload = [
        {
            "name": r["alternative"].name,
            "cost": float(r["cost"]) if r["cost"] is not None else None,
            "total": r["total_importance"],
            "ratio": r["ratio"],
        }
        for r in rows
    ]

    best_item, second_item, delta_ratio, delta_pct = _compute_ratio_stats(
        dashboard_payload
    )

    winner_main_advantage, winner_disadvantage = _winner_strengths_and_gaps(
        dashboard_payload
    )
    winner_least_attributes = _winner_least_attributes(best_row)

    if request.method == "POST" and "save_result" in request.POST:
        winner_name = best_row["alternative"].name if best_row else "Sin ganador"
        winner_total = best_row["total_importance"] if best_row else 0
        winner_cost = best_row["cost"] if best_row else None
        winner_ratio = best_row["ratio"] if best_row else None

        payload = {
            "setup": setup,
            "dashboard": dashboard_payload,
            "winner": {
                "name": winner_name,
                "total": winner_total,
                "cost": float(winner_cost) if winner_cost is not None else None,
                "ratio": winner_ratio,
            },
        }

        origin = request.build_absolute_uri("/")

        try:
            summary_text = generate_decision_assistant_text(
                setup=setup,
                dashboard=dashboard_payload,
                request_origin=origin,
            )
        except Exception as exc:  # noqa: BLE001
            summary_text = (
                "No fue posible generar el resumen IA automáticamente al guardar este análisis. "
                "Vuelve a Paso 10 para reintentar. "
                f"Detalle: {exc}"
            )

        try:
            inconsistency_text, _computed, warning = generate_inconsistency_report_text(
                dashboard=dashboard_payload,
                request_origin=origin,
            )
            if warning:
                inconsistency_text = f"{inconsistency_text}\n\n(Advertencia: {warning})"
        except ValueError as exc:
            inconsistency_text = (
                "No se pudo ejecutar la auditoría de inconsistencias al guardar este análisis. "
                f"Detalle: {exc}"
            )

        base_name = "Análisis CBA"
        if setup and setup.get("project_name"):
            base_name = f"CBA - {setup.get('project_name')}"

        saved = CBAResult.objects.create(
            name=f"{base_name} {timezone.now().strftime('%Y-%m-%d %H:%M')}",
            winner_name=winner_name,
            winner_total=winner_total,
            winner_cost=winner_cost,
            winner_ratio=winner_ratio,
            data_json=json.dumps(payload, ensure_ascii=False),
            summary_text=summary_text,
            inconsistency_text=inconsistency_text,
        )

        proyecto = (setup or {}).get("project_name") if isinstance(setup, dict) else None
        proyecto = (proyecto or saved.name or "").strip()[:255]
        puesto = None
        if isinstance(setup, dict):
            puesto = (setup.get("requesting_area") or "").strip() or None
        if puesto:
            puesto = puesto[:150]

        winner_alt = (best_row["alternative"].name if best_row else "")

        flat_rows = []
        chart_rows = []
        for item in dashboard_payload or []:
            if not isinstance(item, dict):
                continue

            candidato = (item.get("name") or "").strip()[:150]

            raw_cost = item.get("cost")
            raw_total = item.get("total")
            raw_ratio = item.get("ratio")

            def _to_decimal(value):
                if value is None:
                    return None
                try:
                    return Decimal(str(value))
                except (InvalidOperation, ValueError, TypeError):
                    return None

            costo = _to_decimal(raw_cost)
            ventaja = _to_decimal(raw_total)
            costo_ventaja = _to_decimal(raw_ratio)

            flat_rows.append(
                ResultadoCBA(
                    result=saved,
                    proyecto=proyecto,
                    puesto=puesto,
                    candidato=candidato,
                    costo=costo,
                    ventaja=ventaja,
                    costo_ventaja=costo_ventaja,
                    recomendado=bool(winner_alt and candidato == winner_alt),
                    fecha=saved.created_at or timezone.now(),
                )
            )

            chart_rows.append(
                GraficaCostoVentaja(
                    result=saved,
                    proyectos=proyecto,
                    puesto=puesto,
                    candidatos=candidato,
                    costo=_to_decimal(0) or 0,
                    ventaja=_to_decimal(0) or 0,
                )
            )
            chart_rows.append(
                GraficaCostoVentaja(
                    result=saved,
                    proyectos=proyecto,
                    puesto=puesto,
                    candidatos=candidato,
                    costo=costo or 0,
                    ventaja=ventaja or 0,
                )
            )

        if flat_rows:
            ResultadoCBA.objects.bulk_create(flat_rows, batch_size=200)

        if chart_rows:
            GraficaCostoVentaja.objects.bulk_create(chart_rows, batch_size=400)

        return redirect("cba_saved_results")

    context = {
        "setup": setup,
        "best_row": best_row,
        "best_item": best_item,
        "second_item": second_item,
        "delta_ratio": delta_ratio,
        "delta_pct": delta_pct,
        "table_rows": dashboard_payload,
        "dashboard_json": json.dumps(dashboard_payload, ensure_ascii=False),
        "winner_main_advantage": winner_main_advantage,
        "winner_disadvantage": winner_disadvantage,
        "winner_least_attributes": winner_least_attributes,
        "saved_result": None,
        "public_view": False,
        "allow_powerbi_form": False,
    }
    return render(request, "cba_app/dashboard.html", context)


@login_required
def cba_saved_result_detail(request, result_id: int):
    result = get_object_or_404(CBAResult, id=result_id)
    data = _build_saved_result_viewmodel(result)
    context = {
        **data,
        "saved_result": result,
        "public_view": False,
        "allow_powerbi_form": True,
    }
    return render(request, "cba_app/dashboard.html", context)


def cba_saved_result_public(request, result_id: int):
    result = get_object_or_404(CBAResult, id=result_id)
    data = _build_saved_result_viewmodel(result)
    context = {
        **data,
        "saved_result": result,
        "public_view": True,
        "allow_powerbi_form": False,
    }
    return render(request, "cba_app/dashboard.html", context)


def cba_saved_result_public_json(request, result_id: int):
    """Entrega datos estructurados (JSON) para consumo externo/Power BI."""

    result = get_object_or_404(CBAResult, id=result_id)
    data = _build_saved_result_viewmodel(result)
    alternatives = data.get("table_rows") or []

    response = {
        "result": {
            "id": result.id,
            "name": result.name,
            "created_at": result.created_at.isoformat() if result.created_at else None,
            "winner_name": result.winner_name,
            "winner_total": result.winner_total,
            "winner_cost": float(result.winner_cost) if result.winner_cost is not None else None,
            "winner_ratio": result.winner_ratio,
            "power_bi_url": result.power_bi_url,
        },
        "setup": data.get("setup"),
        "alternatives": alternatives,
        "insights": {
            "best_item": data.get("best_item"),
            "second_item": data.get("second_item"),
            "delta_ratio": data.get("delta_ratio"),
            "delta_pct": data.get("delta_pct"),
            "winner_main_advantage": data.get("winner_main_advantage"),
            "winner_disadvantage": data.get("winner_disadvantage"),
            "winner_least_attributes": data.get("winner_least_attributes"),
        },
        "ai": {
            "summary_text": result.summary_text,
            "inconsistency_text": result.inconsistency_text,
        },
        "links": {
            "dashboard": request.build_absolute_uri(
                reverse("cba_saved_result_public", args=[result_id])
            ),
            "json": request.build_absolute_uri(
                reverse("cba_saved_result_public_json", args=[result_id])
            ),
        },
    }

    return JsonResponse(response)


@login_required
def cba_saved_result_powerbi(request, result_id: int):
    """Opción 'Ir a Power BI' desde un resultado guardado.

    Requiere configurar settings.POWER_BI_DASHBOARD_URL (variable de entorno POWER_BI_DASHBOARD_URL).
    """

    result = get_object_or_404(CBAResult, id=result_id)

    base_url = (result.power_bi_url or "").strip() or (getattr(settings, "POWER_BI_DASHBOARD_URL", "") or "").strip()
    if not base_url:
        messages.error(
            request,
            "No hay un link de Power BI para este proyecto. Cárgalo aquí o define POWER_BI_DASHBOARD_URL.",
        )
        return redirect("cba_saved_result_detail", result_id=result_id)

    try:
        parsed = urlparse(base_url)
        qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
        qs.setdefault("result_id", str(result_id))
        new_query = urlencode_qs(qs)
        target = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
        )
    except Exception:
        target = base_url

    return redirect(target)


@login_required
@require_POST
def cba_saved_result_powerbi_config(request, result_id: int):
    """Guarda/edita el link Power BI asociado a un resultado guardado."""

    result = get_object_or_404(CBAResult, id=result_id)
    url = (request.POST.get("power_bi_url") or "").strip()
    result.power_bi_url = url
    result.save(update_fields=["power_bi_url"])
    messages.success(request, "Link de Power BI actualizado.")
    return redirect("cba_saved_result_detail", result_id=result_id)


@login_required
def cba_guide(request):
    """Página con una guía básica de uso de CBA en el sistema."""

    legacy_name = "guides/guia.pdf"
    storage_name = _get_guide_storage_name() or legacy_name

    pdf_url = None
    # Si tenemos un storage_name registrado, asumimos que existe y dejamos que
    # los endpoints /guia/pdf manejen errores de lectura/streaming.
    if _get_guide_storage_name() or _safe_storage_exists(storage_name):
        # En producción (Cloudinary u otro storage remoto) MEDIA_URL puede no servir el archivo.
        # PDF.js necesita una URL same-origin que entregue bytes del PDF.
        pdf_url = reverse("cba_guide_pdf")
    download_url = reverse("cba_guide_download") if pdf_url else None
    pdf_version = None
    if pdf_url:
        meta = ensure_guide_meta(pdf_storage_name=storage_name)
        if isinstance(meta, dict):
            pdf_version = meta.get("version")

    page_raw = request.GET.get("page", "1")
    try:
        page = max(1, int(page_raw))
    except ValueError:
        page = 1

    uploaded_ok = request.GET.get("uploaded") == "1"
    error_message = None
    share_error = request.GET.get("share_error") == "1"

    share_created = None
    share_url = None
    share_password_protected = False
    share_token = (request.GET.get("share") or "").strip()
    if share_token:
        try:
            share_created = SharedGuideLink.objects.get(token=share_token, is_active=True)
            share_url = request.build_absolute_uri(
                reverse("cba_guide_shared", args=[share_created.token])
            )
            share_password_protected = share_created.requires_password()
        except SharedGuideLink.DoesNotExist:
            share_created = None

    if request.method == "POST":
        form = GuidePdfUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                # Borrar anterior si lo conocemos (Cloudinary puede renombrar).
                previous = _get_guide_storage_name()
                if previous:
                    try:
                        default_storage.delete(previous)
                    except Exception:
                        pass

                saved_name = default_storage.save(legacy_name, form.cleaned_data["pdf_file"])

                # Persistir el nombre real.
                GuideDocument.objects.create(storage_name=saved_name)
                storage_name = saved_name
            except Exception:
                messages.error(request, "No se pudo guardar la guía (storage no disponible).")
                return redirect("cba_guide")
            try:
                compute_and_store_guide_meta(pdf_storage_name=storage_name)
            except Exception:
                # Si falla el hash/metadata, el visor sigue funcionando sin cache persistente
                pass
            return redirect(reverse("cba_guide") + "?" + urlencode({"uploaded": "1"}))
        error_message = "Revisa el archivo e inténtalo de nuevo."
    else:
        form = GuidePdfUploadForm()

    share_form = GuideShareLinkForm()

    context = {
        "form": form,
        "share_form": share_form,
        "pdf_url": pdf_url,
        "download_url": download_url,
        "page": page,
        "pdf_version": pdf_version,
        "uploaded_ok": uploaded_ok,
        "error_message": error_message,
        "share_created": share_created,
        "share_url": share_url,
        "share_password_protected": share_password_protected,
        "share_error": share_error,
    }
    return render(request, "cba_app/guide.html", context)


@login_required
def cba_guide_pdf(request):
    """Entrega el PDF para el visor (PDF.js) desde default_storage.

    Usar esta ruta evita problemas de CORS y de MEDIA_URL en storages remotos.
    """

    storage_name = _get_guide_storage_name() or "guides/guia.pdf"
    if not _get_guide_storage_name() and not _safe_storage_exists(storage_name):
        raise Http404("No hay guía disponible.")

    return _stream_pdf_from_storage(request, storage_name, as_attachment=False, filename="guia.pdf")


@login_required
@require_POST
def cba_guide_share_create(request):
    storage_name = "guides/guia.pdf"
    if not _safe_storage_exists(storage_name):
        return redirect(reverse("cba_guide") + "?" + urlencode({"share_error": "1"}))

    form = GuideShareLinkForm(request.POST)
    if not form.is_valid():
        return redirect(reverse("cba_guide") + "?" + urlencode({"share_error": "1"}))

    title = (form.cleaned_data.get("title") or "").strip()
    subtitle = (form.cleaned_data.get("subtitle") or "").strip()
    password = (form.cleaned_data.get("password") or "").strip()

    for _ in range(6):
        token = secrets.token_urlsafe(24)
        if not SharedGuideLink.objects.filter(token=token).exists():
            break
    else:
        token = secrets.token_urlsafe(32)

    SharedGuideLink.objects.create(
        token=token,
        title=title,
        subtitle=subtitle,
        password_hash=(make_password(password) if password else ""),
        is_active=True,
    )
    return redirect(reverse("cba_guide") + "?" + urlencode({"share": token}))


def _shared_guide_session_key(token: str) -> str:
    return f"shared_guide_ok:{token}"


def _shared_guide_has_access(request, link: SharedGuideLink) -> bool:
    if not link.requires_password():
        return True
    return bool(request.session.get(_shared_guide_session_key(link.token)))


def cba_guide_shared(request, token: str):
    link = get_object_or_404(SharedGuideLink, token=token, is_active=True)

    password_error = None
    if link.requires_password() and not _shared_guide_has_access(request, link):
        if request.method == "POST":
            pw_form = GuideSharedPasswordForm(request.POST)
            if pw_form.is_valid():
                candidate = pw_form.cleaned_data.get("password")
                if check_password(candidate, link.password_hash):
                    request.session[_shared_guide_session_key(link.token)] = True
                    return redirect(reverse("cba_guide_shared", args=[link.token]))
                password_error = "Contraseña incorrecta."
        else:
            pw_form = GuideSharedPasswordForm()

        return render(
            request,
            "cba_app/guide_shared.html",
            {
                "link": link,
                "requires_password": True,
                "pw_form": pw_form,
                "password_error": password_error,
            },
        )

    storage_name = _get_guide_storage_name() or "guides/guia.pdf"
    if not _get_guide_storage_name() and not _safe_storage_exists(storage_name):
        raise Http404("No hay guía disponible.")

    page_raw = request.GET.get("page", "1")
    try:
        page = max(1, int(page_raw))
    except ValueError:
        page = 1

    protected_pdf_url = reverse("cba_guide_shared_pdf", args=[link.token])
    download_url = reverse("cba_guide_shared_download", args=[link.token])
    pdf_version = None
    meta = ensure_guide_meta(pdf_storage_name=storage_name)
    if isinstance(meta, dict):
        pdf_version = meta.get("version")

    return render(
        request,
        "cba_app/guide_shared.html",
        {
            "link": link,
            "requires_password": False,
            "pdf_url": protected_pdf_url,
            "download_url": download_url,
            "page": page,
            "pdf_version": pdf_version,
        },
    )


def cba_guide_shared_pdf(request, token: str):
    link = get_object_or_404(SharedGuideLink, token=token, is_active=True)
    if not _shared_guide_has_access(request, link):
        raise Http404("No autorizado")

    storage_name = _get_guide_storage_name() or "guides/guia.pdf"
    if not _get_guide_storage_name() and not _safe_storage_exists(storage_name):
        raise Http404("No hay guía disponible.")

    # PDF.js necesita acceso directo al contenido
    return _stream_pdf_from_storage(request, storage_name, as_attachment=False, filename="guia.pdf")


def cba_guide_shared_download(request, token: str):
    link = get_object_or_404(SharedGuideLink, token=token, is_active=True)
    if not _shared_guide_has_access(request, link):
        raise Http404("No autorizado")

    storage_name = _get_guide_storage_name() or "guides/guia.pdf"
    if not _get_guide_storage_name() and not _safe_storage_exists(storage_name):
        raise Http404("No hay guía disponible.")

    safe_title = slugify(link.title.strip()) if (link.title or "").strip() else "guia"
    filename = f"{safe_title}.pdf"

    return _stream_pdf_from_storage(request, storage_name, as_attachment=True, filename=filename)


@login_required
def cba_guide_download(request):
    storage_name = _get_guide_storage_name() or "guides/guia.pdf"
    if not _get_guide_storage_name() and not _safe_storage_exists(storage_name):
        raise Http404("No hay guía disponible.")

    try:
        return _stream_pdf_from_storage(request, storage_name, as_attachment=True, filename="guia.pdf")
    except Http404:
        raise


def cba_signup(request):
    """Registro de usuario (crear cuenta)."""

    if request.user.is_authenticated:
        return redirect("cba_home")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("cba_home")
    else:
        form = SignUpForm()

    return render(request, "registration/signup.html", {"form": form})


def cba_logout(request):
    """Cerrar sesión (acepta GET o POST para evitar 405)."""

    auth_logout(request)
    return redirect("cba_login")


@login_required
def cba_profile(request):
    """Perfil minimalista del usuario."""

    saved = request.GET.get("saved") == "1"
    profile, _created = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        # Evita caching/colisiones en storages (ej. Cloudinary/CDN) usando nombre único por subida.
        uploaded = request.FILES.get("avatar")
        if uploaded is not None:
            _root, ext = os.path.splitext(getattr(uploaded, "name", "") or "")
            safe_ext = (ext or "").lower()[:10]
            uploaded.name = f"avatar_{request.user.id}_{int(time.time())}{safe_ext}"

        form = ProfileForm(request.POST, instance=request.user)
        photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)

        if form.is_valid() and photo_form.is_valid():
            form.save()

            if photo_form.cleaned_data.get("delete_avatar"):
                if profile.avatar:
                    try:
                        _delete_cloudinary_image_if_possible(profile.avatar)
                        # CloudinaryField no siempre soporta el mismo API que FileField.
                        try:
                            profile.avatar.delete(save=False)
                        except TypeError:
                            profile.avatar.delete()
                    except Exception:
                        pass
                profile.avatar = None
                profile.save(update_fields=["avatar", "updated_at"])
            else:
                if "avatar" in request.FILES and profile.avatar:
                    try:
                        _delete_cloudinary_image_if_possible(profile.avatar)
                        try:
                            profile.avatar.delete(save=False)
                        except TypeError:
                            profile.avatar.delete()
                    except Exception:
                        pass
                photo_form.save()

            return redirect(f"{reverse('cba_profile')}?saved=1")
    else:
        form = ProfileForm(instance=request.user)
        photo_form = ProfilePhotoForm(instance=profile)

    return render(
        request,
        "cba_app/profile.html",
        {
            "form": form,
            "photo_form": photo_form,
            "profile": profile,
            "saved": saved,
        },
    )

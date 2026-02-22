from __future__ import annotations

import json
import re
from datetime import datetime
import urllib.error
import urllib.request
from difflib import SequenceMatcher

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from .models import AIProviderSetting, Alternative, Advantage, Attribute, Criterion


def _clamp_int(value: float | int, min_value: int, max_value: int) -> int:
    try:
        iv = int(round(float(value)))
    except Exception:
        iv = min_value
    if iv < min_value:
        return min_value
    if iv > max_value:
        return max_value
    return iv


def _median(values: list[float]) -> float | None:
    try:
        nums = [float(v) for v in (values or []) if v is not None]
    except Exception:
        nums = []
    if not nums:
        return None
    nums.sort()
    n = len(nums)
    mid = n // 2
    if n % 2 == 1:
        return float(nums[mid])
    return float((nums[mid - 1] + nums[mid]) / 2.0)


def _criterion_cap_by_rank(*, rank: int, total: int) -> int:
    """Cap máximo por factor según orden (1 = más importante)."""

    # Para UX y consistencia CBA: mantener una escala gradual.
    # El primer factor tiene 100; luego baja de 10 en 10 (mínimo 40).
    # Esto evita sugerencias tipo 100 → 40 cuando solo hay pocos factores.
    if rank <= 1:
        return 100
    cap = 100 - (rank - 1) * 10
    return _clamp_int(cap, 40, 100)


def _range_multipliers_by_diff(diff: int) -> tuple[float, float]:
    """Rango sugerido según la diferencia ordinal entre mejor y segundo mejor."""

    if diff <= 0:
        return 0.75, 0.85
    if diff == 1:
        return 0.80, 0.90
    if diff == 2:
        return 0.85, 0.95
    return 0.90, 1.00


def _normalize_name_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = " ".join(s.split())
    return s


def _similarity(a: str, b: str) -> float:
    try:
        return float(SequenceMatcher(None, _normalize_name_key(a), _normalize_name_key(b)).ratio())
    except Exception:
        return 0.0


_PERSON_ROLE_TOKENS = {
    "residente",
    "gerente",
    "jefe",
    "director",
    "coordinador",
    "supervisor",
    "ingeniero",
    "arquitecto",
    "analista",
    "especialista",
    "asistente",
    "consultor",
}

_COMPANY_TOKENS = {
    "sac",
    "s.a.c",
    "sa",
    "s.a.",
    "srl",
    "s.r.l",
    "eirl",
    "e.i.r.l",
    "cia",
    "compañia",
    "compania",
    "empresa",
    "consorcio",
    "corporacion",
    "corporación",
    "ltda",
}


def _infer_cost_context_from_setup(setup: object) -> tuple[str, str]:
    """Heurística simple para interpretar si los montos parecen de persona vs empresa.

    Retorna (context, reason) donde context ∈ {'persona', 'empresa', 'desconocido'}.
    """

    if not isinstance(setup, dict):
        return "desconocido", "setup no disponible"

    requesting_area = _normalize_name_key(str(setup.get("requesting_area") or ""))
    public_entity = _normalize_name_key(str(setup.get("public_entity") or ""))
    private_company = _normalize_name_key(str(setup.get("private_company") or ""))

    if requesting_area and any(tok in requesting_area for tok in _PERSON_ROLE_TOKENS):
        return "persona", f"área solicitante contiene rol ({setup.get('requesting_area')})"

    ent = " ".join([public_entity, private_company]).strip()
    if ent and any(tok in ent for tok in _COMPANY_TOKENS):
        return "empresa", "solicitante parece empresa (siglas/razón social)"

    if ent and ("gobierno" in ent or "municipal" in ent or "minister" in ent or "regional" in ent):
        return "empresa", "solicitante parece entidad pública"

    return "desconocido", "sin señales claras"


def _build_generic_audit_prompts(*, title: str, payload: dict) -> tuple[str, str]:
    system_prompt = (
        "Eres un auditor técnico de calidad de datos para SIDEO (Choosing By Advantages - CBA). "
        "NO inventes datos: solo usa el JSON entregado. "
        "Responde SIEMPRE en español, directo y accionable."
    )

    user_prompt = (
        f"Genera un reporte de auditoría para: {title}.\n"
        "Formato: subtítulos en **negrita** y viñetas '-'.\n"
        "Incluye: **Hallazgos**, **Por qué importa**, **Cómo corregir (paso a paso)**.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


def _build_scores_assistant_prompts(*, payload: dict) -> tuple[str, str]:
    """Prompt corto (estilo Paso 1/2) para la auditoría de puntajes (Paso 8)."""

    system_prompt = (
        "Eres un asistente auditor de coherencia para SIDEO (CBA), Paso 8. "
        "Solo puedes usar el JSON entregado; NO inventes datos. "
        "Responde SIEMPRE en español, corto, concreto y profesional."
    )

    user_prompt = (
        "Devuelve un mensaje CORTO y CONCRETO (máximo 9 líneas).\n"
        "Formato EXACTO: una idea por línea, sin títulos, sin viñetas, sin párrafos largos.\n"
        "Usa solo estos prefijos según aplique: ✔, 🕒, ⚠, 💡, ℹ.\n"
        "Incluye SIEMPRE estas dos primeras líneas:\n"
        "1) ✔ Puntajes revisados\n"
        "2) 🕒 Revisión: {reviewed_at}\n"
        "Luego, solo si aplica: múltiples puntajes por factor, empates, puntajes faltantes, valores inválidos, puntajes en celdas no habilitadas.\n"
        "Cierra con máximo 2 líneas de 💡 (acciones concretas).\n"
        "Puedes usar números SOLO para conteos (ej. '⚠ 2 factores...'). No uses porcentajes ni decimales.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


def _render_scores_audit_assistant_text(
    *,
    reviewed_at: str,
    multiple_factors: list[str],
    tie_factors: list[str],
    missing_factors: list[str],
    invalid_examples: list[str],
    disabled_examples: list[str],
) -> str:
    lines: list[str] = []
    lines.append("✔ Puntajes revisados")
    lines.append(f"🕒 Revisión: {reviewed_at}")

    def _join_some(items: list[str], max_items: int) -> str:
        shown = [x for x in items if x][:max_items]
        s = ", ".join(shown)
        if len(items) > max_items:
            s = f"{s}…"
        return s

    if multiple_factors:
        lines.append(
            f"⚠ {len(multiple_factors)} factores con múltiples puntajes: {_join_some(multiple_factors, 3)}"
        )

    if missing_factors:
        lines.append(
            f"⚠ {len(missing_factors)} factores con ventaja sin puntaje: {_join_some(missing_factors, 3)}"
        )

    if tie_factors:
        lines.append(f"⚠ Empates en: {_join_some(tie_factors, 4)}")

    if invalid_examples:
        lines.append(f"⚠ Valores inválidos: {_join_some(invalid_examples, 3)}")

    if disabled_examples:
        lines.append(
            f"⚠ Puntajes en celdas no habilitadas: {_join_some(disabled_examples, 2)}"
        )

    if len(lines) == 2:
        lines.append("✔ No se detectaron inconsistencias críticas")

    # Acciones (máx. 2)
    if multiple_factors or tie_factors or missing_factors or invalid_examples or disabled_examples:
        lines.append("💡 Deja un único puntaje por factor")
        lines.append("💡 Si hay empate real, valida la ventaja y documenta")

    # Enforce max 9
    return "\n".join(lines[:9]).strip()


def _build_costs_assistant_prompts(*, payload: dict) -> tuple[str, str]:
    """Prompt corto (estilo Paso 1/2) para la auditoría de costos (Paso 10)."""

    system_prompt = (
        "Eres un asistente auditor de costos para SIDEO (CBA), Paso 10. "
        "Solo puedes usar el JSON entregado; NO inventes datos. "
        "Responde SIEMPRE en español, corto, concreto y profesional."
    )

    user_prompt = (
        "Devuelve un mensaje CORTO y CONCRETO (máximo 9 líneas).\n"
        "Formato EXACTO: una idea por línea, sin títulos, sin viñetas, sin párrafos largos.\n"
        "Usa solo estos prefijos según aplique: ✔, 🕒, ⚠, 💡, ℹ.\n"
        "Incluye SIEMPRE estas dos primeras líneas:\n"
        "1) ✔ Costos revisados\n"
        "2) 🕒 Revisión: {reviewed_at}\n"
        "Luego, solo si aplica: costos faltantes, costos no numéricos, costos muy disparejos, posibles outliers, y si los costos son iguales.\n"
        "Cierra con máximo 2 líneas de 💡 (acciones concretas).\n"
        "No uses porcentajes ni decimales largos.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


def _render_costs_audit_assistant_text(
    *,
    reviewed_at: str,
    missing: list[str],
    non_numeric: list[str],
    min_cost: float | None,
    max_cost: float | None,
    outlier_high: list[str],
    ratio_low: list[str],
    context_note: str | None,
    context_warnings: list[str],
) -> str:
    lines: list[str] = []
    lines.append("✔ Costos revisados")
    lines.append(f"🕒 Revisión: {reviewed_at}")

    def _join_some(items: list[str], max_items: int) -> str:
        shown = [x for x in items if x][:max_items]
        s = ", ".join(shown)
        if len(items) > max_items:
            s = f"{s}…"
        return s

    if missing:
        lines.append(f"⚠ {len(missing)} alternativas sin costo: {_join_some(missing, 4)}")

    if non_numeric:
        lines.append(f"⚠ Costos no numéricos en: {_join_some(non_numeric, 3)}")

    if min_cost is not None and max_cost is not None:
        # Mostrar simple (sin decimales largos)
        def _fmt(v: float) -> str:
            if abs(v - round(v)) < 0.00001:
                return str(int(round(v)))
            return f"{v:.2f}".rstrip("0").rstrip(".")

        if abs(max_cost - min_cost) < 0.00001:
            lines.append(f"ℹ Costos iguales en las alternativas con costo (S/ {_fmt(min_cost)})")
        else:
            lines.append(f"ℹ Rango de costos: S/ {_fmt(min_cost)} – S/ {_fmt(max_cost)}")

    if outlier_high:
        lines.append(f"⚠ Posibles costos outlier altos: {_join_some(outlier_high, 3)}")

    if ratio_low:
        lines.append(f"⚠ Ventajas/costo muy bajo en: {_join_some(ratio_low, 3)}")

    if context_note:
        lines.append(f"ℹ {context_note}")

    for w in (context_warnings or [])[:2]:
        if w:
            lines.append(f"⚠ {w}")

    if len(lines) == 2:
        lines.append("✔ No se detectaron alertas de costo")

    # Acciones (máx. 2)
    if missing or non_numeric or outlier_high or ratio_low:
        lines.append("💡 Completa costos faltantes y recalcula")
        lines.append("💡 Revisa unidades (mensual/anual, miles) si hay outliers")

    if context_warnings:
        lines.append("💡 Verifica si el monto es mensual o total")

    return "\n".join(lines[:9]).strip()


def _build_cba_inconsistency_prompts(*, payload: dict) -> tuple[str, str]:
    system_prompt = (
        "Eres un auditor técnico de coherencia para SIDEO (Choosing By Advantages - CBA). "
        "NO inventes datos: solo usa el JSON entregado. "
        "Responde SIEMPRE en español, directo y accionable."
    )

    user_prompt = (
        "Genera un reporte de inconsistencias CBA.\n"
        "Formato: subtítulos en **negrita** y viñetas '-'.\n"
        "Incluye: **Hallazgos críticos**, **Inconsistencias**, **Cómo corregir (pasos)**.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    return system_prompt, user_prompt


def _build_alternatives_assistant_prompts(*, payload: dict) -> tuple[str, str]:
    system_prompt = (
        "Eres un asistente de validación de nombres para SIDEO (CBA) en el Paso 1. "
        "Solo puedes usar el JSON entregado; NO inventes datos. "
        "Responde SIEMPRE en español. "
        "NO muestres métricas ni decimales (nada de 0.98, 88%, etc.). "
        "No pidas experiencia, CV, ni datos técnicos."
    )

    user_prompt = (
        "Devuelve un mensaje CORTO y CONCRETO (máximo 9 líneas).\n"
        "Formato EXACTO: una idea por línea, sin títulos, sin viñetas, sin párrafos largos.\n"
        "Usa solo estos prefijos según aplique: ✔, 🕒, ⚠, 💡, ℹ.\n"
        "Incluye SIEMPRE estas dos primeras líneas:\n"
        "1) ✔ {n} alternativas registradas\n"
        "2) 🕒 Revisión: {reviewed_at}\n"
        "Luego, solo si aplica: duplicados, posibles duplicados, nombres genéricos, nombres informales/apodos, mezcla de tipos, nombres inválidos.\n"
        "Si detectas nombres informales (ej. apodos tipo 'pablito', 'pato'), repórtalo como '⚠ Nombres informales: ...'.\n"
        "No incluyas números excepto el conteo de alternativas y la hora de revisión.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


def _build_criteria_assistant_prompts(*, payload: dict) -> tuple[str, str]:
    system_prompt = (
        "Eres un asistente de validación de factores (criterios) para SIDEO (CBA) en el Paso 2. "
        "Solo puedes usar el JSON entregado; NO inventes datos. "
        "Responde SIEMPRE en español. "
        "NO muestres métricas ni decimales (nada de 0.98, 88%, etc.). "
        "Sé corto, concreto y profesional."
    )

    user_prompt = (
        "Devuelve un mensaje CORTO y CONCRETO (máximo 9 líneas).\n"
        "Formato EXACTO: una idea por línea, sin títulos, sin viñetas, sin párrafos largos.\n"
        "Usa solo estos prefijos según aplique: ✔, 🕒, ⚠, 💡, ℹ.\n"
        "Incluye SIEMPRE estas dos primeras líneas:\n"
        "1) ✔ {n} factores registrados\n"
        "2) 🕒 Revisión: {reviewed_at}\n"
        "Luego, solo si aplica, reporta (con ejemplos cortos): duplicados, posibles duplicados (muy similares), "
        "factores poco específicos, factores mezclados (dos en uno), factores poco evaluables/medibles y factores fuera de contexto "
        "según objetivo y/o rol inferido (si hay).\n"
        "Las recomendaciones deben ser simples (máx. 2 líneas con 💡).\n"
        "Puedes usar números SOLO para conteos (ej. '⚠ 1 duplicado'). No uses porcentajes ni decimales.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


def _build_mustwant_assistant_prompts(*, payload: dict) -> tuple[str, str]:
    system_prompt = (
        "Eres un asistente de validación MUST/WANT para SIDEO (CBA) en el Paso 3. "
        "Solo puedes usar el JSON entregado; NO inventes datos. "
        "Responde SIEMPRE en español. "
        "NO muestres métricas ni decimales (nada de 0.98, 88%, etc.). "
        "Sé corto, concreto y profesional."
    )

    user_prompt = (
        "Devuelve un mensaje CORTO y CONCRETO (máximo 9 líneas).\n"
        "Formato EXACTO: una idea por línea, sin títulos, sin viñetas, sin párrafos largos.\n"
        "Usa solo estos prefijos según aplique: ✔, 🕒, ⚠, 💡, ℹ.\n"
        "Incluye SIEMPRE estas dos primeras líneas:\n"
        "1) ✔ Clasificación revisada ({n} factores)\n"
        "2) 🕒 Revisión: {reviewed_at}\n"
        "Luego, solo si aplica: demasiados MUST, falta de MUST, MUST dudosos, WANT que podrían ser MUST, MUST sin comentario y coherencia básica con el objetivo.\n"
        "Puedes usar números SOLO para conteos (ej. '⚠ 2 MUST dudosos'). No uses porcentajes ni decimales.\n\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


_CRIT_GENERIC_NAMES = {
    "calidad",
    "bueno",
    "rendimiento",
    "capacidad",
    "cumplimiento",
    "experiencia",
    "rapidez",
    "responsabilidad",
    "seriedad",
    "compromiso",
    "garantia",
    "garantía",
}


def _is_unclear_criterion_name(name: str) -> bool:
    raw = (name or "").strip()
    if not raw:
        return True
    k = _normalize_name_key(raw)
    if k in _CRIT_GENERIC_NAMES:
        return True

    letters = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", raw)
    if len(letters) <= 3:
        return True

    tokens = [t for t in re.split(r"\s+", re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]", " ", k)) if t]
    if len(tokens) == 1 and tokens[0] in _CRIT_GENERIC_NAMES:
        return True

    # Si es una sola palabra y no trae ningún indicador (unidad, contexto), suele ser ambiguo.
    if len(tokens) == 1 and len(tokens[0]) >= 4:
        return True

    return False


def _is_mixed_criterion(name: str) -> bool:
    s = " ".join((name or "").strip().split())
    if not s:
        return False

    # Dos en uno típico: "costo y tiempo", "precio/plazo", "costo & plazo".
    if re.search(r"\s(y|e)\s", s, flags=re.IGNORECASE):
        parts = re.split(r"\s(?:y|e)\s", s, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and len(parts[0].strip()) >= 3 and len(parts[1].strip()) >= 3:
            return True

    if any(sep in s for sep in ["/", "&", "+"]):
        parts = re.split(r"\s*[/&+]\s*", s)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) >= 2 and all(len(p) >= 3 for p in parts[:2]):
            return True

    return False


def _is_unmeasurable_criterion(name: str) -> bool:
    k = _normalize_name_key(name)
    if not k:
        return True
    subjective = {
        "me cae bien",
        "trato",
        "amabilidad",
        "confianza",
        "buena onda",
        "opinion",
        "opinión",
        "percepcion",
        "percepción",
        "carisma",
    }
    if any(x in k for x in subjective):
        return True

    # Adjetivos solos suelen ser no comparables.
    if k in {"bueno", "malo", "regular", "excelente", "bonito"}:
        return True

    return False


def _suggest_clarification_for_criterion(*, name: str, setup: dict | None = None) -> str | None:
    k = _normalize_name_key(name)
    if not k:
        return "Define el factor con un nombre concreto (qué y cómo se evalúa)"

    project = ""
    if isinstance(setup, dict):
        project = (setup.get("project_name") or "").strip()

    if k == "experiencia":
        return "Usa algo tipo: 'Experiencia en proyectos similares'"
    if k in {"plazo", "tiempo", "rapidez"}:
        return "Usa algo tipo: 'Plazo (días calendario)'"
    if k in {"costo", "precio", "presupuesto"}:
        return "Usa algo tipo: 'Costo total (S/)'"
    if k == "calidad":
        return "Especifica: 'Calidad de metodología / plan de trabajo'"

    if project and k in _CRIT_GENERIC_NAMES:
        return f"Especifica el contexto: '{name.strip()} para {project}'"

    return None


def _infer_roles_from_alternatives(alternatives: list[str]) -> list[str]:
    roles: list[str] = []
    for a in alternatives or []:
        s = (a or "").strip()
        if not s:
            continue
        if _has_role_hint(s):
            roles.append(s)
    # únicos, preservando orden
    return list(dict.fromkeys(roles))


def _role_mismatch_flags(*, roles: list[str], criteria: list[str]) -> list[str]:
    if not roles or not criteria:
        return []

    roles_k = " ".join(_normalize_name_key(r) for r in roles)
    is_engineering = any(t in roles_k for t in ["ingeniero", "supervisor", "residente", "arquitect", "civil", "obra"]) 
    is_legal = any(t in roles_k for t in ["abogado", "legal", "jurid", "juríd", "asesor"]) 

    legal_terms = ["defensa", "legal", "jurid", "juríd", "litig", "juicio", "penal", "civil", "contrato"]
    build_terms = ["obra", "constru", "construc", "metr", "concreto", "ciment", "plano", "supervis", "ingenier"]

    flags: list[str] = []
    for c in criteria:
        ck = _normalize_name_key(c)
        if is_engineering and any(t in ck for t in legal_terms):
            flags.append(c)
        if is_legal and any(t in ck for t in build_terms):
            flags.append(c)

    return list(dict.fromkeys(flags))


_MUST_SUSPICIOUS_TOKENS = {
    "color",
    "uniforme",
    "logo",
    "marca",
    "diseño",
    "diseno",
    "decoracion",
    "decoración",
    "presentacion",
    "presentación",
    "carisma",
    "amabilidad",
    "trato",
}


_MUST_LIKELY_TOKENS = {
    "seguridad",
    "cumplimiento",
    "legal",
    "jurid",
    "juríd",
    "riesgo",
    "garantia",
    "garantía",
    "plazo",
    "tiempo",
    "costo",
    "precio",
    "presupuesto",
    "calidad",
}


def _extract_keywords(text: str) -> set[str]:
    raw = _normalize_name_key(text)
    if not raw:
        return set()
    raw = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]", " ", raw)
    tokens = [t for t in raw.split() if t]
    stop = {
        "de",
        "del",
        "la",
        "el",
        "y",
        "en",
        "para",
        "por",
        "con",
        "sin",
        "un",
        "una",
        "los",
        "las",
        "que",
        "se",
        "a",
        "al",
        "o",
    }
    return {t for t in tokens if len(t) >= 4 and t not in stop}


def _render_mustwant_assistant_text(
    *,
    total: int,
    reviewed_at: str,
    must_count: int,
    want_count: int,
    must_suspicious: list[str],
    want_should_be_must: list[str],
    missing_desc_must: list[str],
    objective_hint: str | None,
) -> str:
    lines: list[str] = []
    lines.append(f"✔ Clasificación revisada ({total} factores)")
    lines.append(f"🕒 Revisión: {reviewed_at}")

    if total <= 0:
        lines.append("⚠ No hay factores para clasificar")
        return "\n".join(lines[:9])

    if must_count == 0:
        lines.append("⚠ No se definieron factores MUST")
    else:
        # Señal de demasiados MUST: deja espacio para WANT
        if total >= 3 and must_count >= max(2, total - 1):
            lines.append(f"⚠ Demasiados MUST: {must_count} (deja espacio para WANT)")
        elif total >= 4 and must_count > (total * 7) // 10:
            lines.append(f"⚠ Demasiados MUST: {must_count} (posible falta de priorización)")

    if must_suspicious:
        shown = ", ".join(must_suspicious[:4])
        lines.append(f"⚠ {len(must_suspicious)} MUST dudoso(s): {shown}")

    if want_should_be_must:
        shown = ", ".join(want_should_be_must[:4])
        lines.append(f"⚠ {len(want_should_be_must)} WANT podría(n) ser MUST: {shown}")

    if missing_desc_must:
        shown = ", ".join(missing_desc_must[:4])
        lines.append(f"💡 Agrega comentario en MUST: {shown}")

    if objective_hint:
        lines.append(f"ℹ Objetivo/proyecto: {objective_hint}")

    return "\n".join(lines[:9])


def _render_criteria_assistant_text(
    *,
    criteria: list[str],
    reviewed_at: str | None = None,
    dup_groups: list[list[str]] | None = None,
    similar_pairs: list[dict] | None = None,
    unclear: list[str] | None = None,
    mixed: list[str] | None = None,
    unmeasurable: list[str] | None = None,
    out_of_context: list[str] | None = None,
    setup: dict | None = None,
) -> str:
    total = len(criteria or [])
    lines: list[str] = []
    lines.append(f"✔ {total} factores registrados")
    if reviewed_at:
        lines.append(f"🕒 Revisión: {reviewed_at}")

    if total < 2:
        lines.append("⚠ Agrega al menos 2 factores para comparar")

    dup_groups = dup_groups or []
    if dup_groups:
        dup_total = sum(max(0, len(g) - 1) for g in dup_groups)
        flat: list[str] = []
        for g in dup_groups:
            flat.extend(g)
        shown = ", ".join(list(dict.fromkeys(flat))[:6])
        lines.append(f"⚠ {dup_total} duplicado(s): {shown}")

    similar_pairs = similar_pairs or []
    if similar_pairs:
        a = (similar_pairs[0].get("a") or "").strip()
        b = (similar_pairs[0].get("b") or "").strip()
        if a and b:
            lines.append(f"⚠ Posible duplicado: '{a}' y '{b}'")

    unclear = [x for x in (unclear or []) if x]
    if unclear:
        shown = ", ".join(unclear[:6])
        lines.append(f"⚠ Factores poco específicos: {shown}")

    mixed = [x for x in (mixed or []) if x]
    if mixed:
        shown = ", ".join(mixed[:4])
        lines.append(f"⚠ Factores mezclados (sepáralos): {shown}")

    unmeasurable = [x for x in (unmeasurable or []) if x]
    if unmeasurable:
        shown = ", ".join(unmeasurable[:4])
        lines.append(f"⚠ Poco evaluables/medibles: {shown}")

    out_of_context = [x for x in (out_of_context or []) if x]
    if out_of_context:
        shown = ", ".join(out_of_context[:4])
        lines.append(f"⚠ Fuera de contexto para el rol/objetivo: {shown}")

    # Sugerencias (máx 2)
    sug1 = None
    for n in unclear[:3]:
        sug1 = _suggest_clarification_for_criterion(name=n, setup=setup)
        if sug1:
            break
    if sug1:
        lines.append(f"💡 {sug1}")

    if mixed:
        lines.append("💡 Separa factores en 1 idea cada uno")

    return "\n".join(lines[:9])


def _try_openrouter_or_fallback(*, request, title: str, payload: dict, fallback_text: str) -> dict:
    """Intenta formatear con OpenRouter; si falla, retorna fallback determinista."""

    try:
        system_prompt, user_prompt = _build_generic_audit_prompts(title=title, payload=payload)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
        return {"ok": True, "content": content, "computed": payload.get("computed")}
    except RuntimeError as e:
        return {
            "ok": True,
            "content": fallback_text,
            "computed": payload.get("computed"),
        }


def _render_inconsistency_report_text(*, computed: dict) -> str:
    """Genera el reporte de inconsistencias en texto a partir del payload computado."""
    duplicates = computed.get("duplicates") or []
    score_flags = computed.get("score_flags") or []
    cost_flags = computed.get("cost_flags") or []
    meta = computed.get("meta") or {}
    lines: list[str] = []

    lines.append("**Hallazgos críticos**")
    critical: list[str] = []
    if duplicates:
        critical.append("- Hay factores duplicados o repetidos por nombre.")
    if any(f.get("type") == "over_cap" for f in score_flags):
        critical.append("- Hay puntajes que superan el máximo sugerido por orden del factor.")
    if any(f.get("type") == "missing_cost" for f in cost_flags):
        critical.append("- Faltan costos en una o más alternativas.")
    if not critical:
        critical.append("- No se detectan críticos evidentes con las reglas actuales.")
    lines.extend(critical)

    lines.append("\n**Inconsistencias de factores**")


_ALT_GENERIC_TOKENS = {
    "alternativa",
    "alternativas",
    "opcion",
    "opciones",
    "postor",
    "postores",
    "proveedor",
    "proveedores",
    "empresa",
    "empresas",
    "consorcio",
    "sin",
    "nombre",
    "prueba",
    "test",
    "demo",
    "xxx",
    "asd",
}


_ALT_COMPANY_HINTS = {
    "sac",
    "s.a.c",
    "sa",
    "s.a",
    "srl",
    "s.r.l",
    "eirl",
    "e.i.r.l",
    "ltda",
    "ltda.",
    "cia",
    "cia.",
    "compañia",
    "compania",
    "sociedad",
    "consorcio",
    "asociacion",
    "asociación",
    "constructora",
    "contratista",
    "servicios",
    "comercial",
    "inversiones",
    "ingenieria",
    "ingeniería",
    "logistica",
    "logística",
    "transportes",
    "importaciones",
    "exportaciones",
    "grupo",
    "corporacion",
    "corporación",
}


_ALT_ROLE_HINTS = {
    "residente",
    "supervisor",
    "inspectora",
    "inspector",
    "coordinador",
    "coordinadora",
    "analista",
    "jefe",
    "gerente",
    "director",
    "consultor",
    "asesor",
    "auditor",
}


_ALT_PERSON_PREFIXES = {
    "ing",
    "ing.",
    "ingeniero",
    "ingeniera",
    "sr",
    "sr.",
    "sra",
    "sra.",
    "dr",
    "dr.",
    "dra",
    "dra.",
    "arq",
    "arq.",
    "abg",
    "abg.",
}


def _alt_tokens(name: str) -> list[str]:
    compact = re.sub(r"\s+", " ", (name or "").strip())
    cleaned = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ\. ]", " ", compact)
    tokens = [t for t in re.split(r"\s+", cleaned.lower()) if t]
    return tokens


def _classify_alternative_name(name: str) -> str:
    """Clasifica el nombre solo para organizar: 'empresa', 'persona', 'rol', 'desconocido'."""
    raw = (name or "").strip()
    if not raw:
        return "desconocido"

    tokens = _alt_tokens(raw)
    token_set = set(tokens)

    if token_set & _ALT_ROLE_HINTS:
        return "rol"

    if token_set & _ALT_COMPANY_HINTS:
        return "empresa"

    if tokens and tokens[0] in _ALT_PERSON_PREFIXES:
        return "persona"

    # Heurística: 2-4 palabras alfabéticas suele ser nombre de persona.
    words = [t for t in tokens if re.fullmatch(r"[a-záéíóúüñ\.]+", t)]
    if 2 <= len(words) <= 4:
        return "persona"

    return "desconocido"


def _has_role_hint(name: str) -> bool:
    tokens = set(_alt_tokens(name or ""))
    return bool(tokens & _ALT_ROLE_HINTS)


def _is_generic_alternative_name(name: str) -> bool:
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return True

    tokens = _alt_tokens(raw)
    if not tokens:
        return True

    # Patrones típicos: "Empresa 1", "Postor 2", "Residente 1", "Ingeniero A"
    joined = " ".join(tokens)
    if re.fullmatch(r"(empresa|postor|alternativa|opcion|opción|proveedor|constructora)\s+[0-9]+", joined):
        return True
    if re.fullmatch(r"(residente|supervisor|inspector|ingeniero|ing\.?)+\s+[0-9]+", joined):
        return True
    if len(tokens) == 2 and tokens[0] in _ALT_GENERIC_TOKENS and re.fullmatch(r"[a-z]", tokens[1]):
        return True

    # Muy poco informativo
    non_stop = [t for t in tokens if t not in {"de", "del", "la", "el", "y", "en", "sa", "sac", "srl", "eirl", "s"}]
    if non_stop and all(t in _ALT_GENERIC_TOKENS for t in non_stop):
        return True

    return False


_ALT_INFORMAL_TOKENS = {
    "pablito",
    "pablita",
    "pepito",
    "pepita",
    "luisito",
    "juanito",
    "marquitos",
    "pato",
    "patito",
    "mono",
    "monito",
    "gato",
    "gatito",
    "perro",
    "perrito",
    "conejo",
    "conejito",
}


def _is_informal_alternative_name(name: str) -> bool:
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return False

    tokens = _alt_tokens(raw)
    if not tokens:
        return False

    token_set = set(tokens)
    if token_set & _ALT_COMPANY_HINTS:
        return False

    # Muy típico de apodo: una sola palabra corta.
    if len(tokens) == 1:
        t = tokens[0]
        if t in _ALT_INFORMAL_TOKENS:
            return True
        if len(t) <= 9 and (t.endswith("ito") or t.endswith("ita") or t.endswith("illo") or t.endswith("illa")):
            return True

    # Patrón "Pato Perez" o "Pablito Gomez" => informal
    if tokens and tokens[0] in _ALT_INFORMAL_TOKENS:
        return True

    return False


def _audit_alternative_name(*, name: str) -> list[str]:
    issues: list[str] = []
    raw = (name or "").strip()
    if not raw:
        return ["está vacío"]

    compact = re.sub(r"\s+", " ", raw)
    letters_only = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", compact)
    if len(letters_only) <= 2:
        issues.append("es demasiado corto")

    if re.fullmatch(r"[0-9\s\-_.]+", compact):
        issues.append("tiene solo números/símbolos")

    tokens = [t for t in re.split(r"\s+", re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]", " ", compact).lower()) if t]
    if tokens:
        non_stop = [t for t in tokens if t not in {"de", "del", "la", "el", "y", "en", "sa", "sac", "srl", "eirl", "s"}]
        if non_stop and all(t in _ALT_GENERIC_TOKENS for t in non_stop):
            issues.append("suena genérico (no identifica al postor)")

    if any(t in {"prueba", "test", "demo", "xxx", "asd"} for t in tokens):
        issues.append("parece un nombre de prueba")

    if len(compact) >= 70:
        issues.append("es muy largo")

    return issues


def _render_alternatives_assistant_text(*, alternatives: list[str], dup_groups: list[list[str]], similar_pairs: list[dict], reviewed_at: str | None = None) -> str:
    """Reporte corto: solo identificación básica, sin datos técnicos."""

    clean = [(a or "").strip() for a in alternatives]
    clean = [a for a in clean if a]

    total = len(alternatives)

    # Clasificación (solo Persona/Empresa); roles se reportan como alerta.
    type_counts = {"empresa": 0, "persona": 0, "rol": 0, "desconocido": 0}
    role_names: list[str] = []
    for n in clean:
        t = _classify_alternative_name(n)
        type_counts[t] = type_counts.get(t, 0) + 1
        if _has_role_hint(n):
            role_names.append(n)

    # Genéricos
    generic_names = [n for n in clean if _is_generic_alternative_name(n)]

    # Informales / apodos
    informal_names = [n for n in clean if _is_informal_alternative_name(n)]

    # Incompletos / inválidos
    invalid_names = []
    for n in alternatives:
        issues = _audit_alternative_name(name=n)
        if issues:
            invalid_names.append((n or "").strip() or "(vacío)")

    # Mezcla de tipos (persona/empresa) y mezcla con roles
    mixed_person_company = type_counts.get("empresa", 0) > 0 and type_counts.get("persona", 0) > 0
    mixed_role_with_others = bool(role_names) and (type_counts.get("empresa", 0) + type_counts.get("persona", 0)) > 0

    # Salida corta
    lines: list[str] = []
    lines.append(f"✔ {total} alternativas registradas")

    if reviewed_at:
        lines.append(f"🕒 Revisión: {reviewed_at}")

    if total < 2:
        lines.append("⚠ Agrega al menos 2 postores para poder comparar")

    if dup_groups:
        flat = []
        for g in dup_groups:
            flat.extend(g)
        shown = ", ".join(list(dict.fromkeys(flat))[:6])
        lines.append(f"⚠ Duplicados detectados: {shown}")

    if similar_pairs:
        a = (similar_pairs[0].get("a") or "").strip()
        b = (similar_pairs[0].get("b") or "").strip()
        if a and b:
            lines.append(f"⚠ Posible duplicado: '{a}' y '{b}'")

    if generic_names:
        shown = ", ".join(generic_names[:6])
        lines.append(f"⚠ Nombres genéricos: {shown}")
        lines.append("💡 Sug.: usa razón social/nombre completo para diferenciarlos")

    if informal_names:
        shown = ", ".join(informal_names[:6])
        lines.append(f"⚠ Nombres informales: {shown}")
        lines.append("💡 Sug.: evita apodos; usa nombre completo o razón social")

    # Tipo (solo persona/empresa)
    if type_counts.get("empresa", 0) or type_counts.get("persona", 0):
        parts = []
        if type_counts.get("empresa", 0):
            parts.append(f"{type_counts['empresa']} empresas")
        if type_counts.get("persona", 0):
            parts.append(f"{type_counts['persona']} personas")
        lines.append("ℹ Tipo detectado: " + ", ".join(parts))

    if role_names:
        shown = ", ".join(role_names[:6])
        lines.append(f"⚠ Parecen roles (no postores): {shown}")

    if mixed_role_with_others:
        lines.append("⚠ Mezcla de empresa/persona con roles (ej. supervisor/residente)")
    elif mixed_person_company:
        lines.append("⚠ Mezcla de tipos (persona/empresa). Mantén comparables")

    if invalid_names:
        shown = ", ".join(invalid_names[:6])
        lines.append(f"⚠ Nombres incompletos/raros: {shown}")

    return "\n".join(lines[:9])
    if not duplicates:
        lines.append("- No se detectaron duplicados exactos por nombre normalizado.")
    else:
        for group in duplicates:
            try:
                joined = "; ".join(group)
            except Exception:
                joined = str(group)
            lines.append(f"- Duplicado: {joined}")

    lines.append("\n**Inconsistencias de puntajes**")
    if not score_flags:
        lines.append("- No se detectaron banderas de puntaje con las reglas actuales.")
    else:
        for f in score_flags[:30]:
            crit = f.get("criterion")
            alt = f.get("alternative")
            imp = f.get("importance")
            detail = f.get("detail") or ""
            extra = ""
            if f.get("cap") is not None:
                extra += f" cap={f.get('cap')}"
            if f.get("diff") is not None:
                extra += f" diff={f.get('diff')}"
            if f.get("suggested_low") is not None and f.get("suggested_high") is not None:
                extra += f" sugerido={f.get('suggested_low')}-{f.get('suggested_high')}"
            lines.append(f"- {crit} → {alt}: {imp}. {detail}{extra}")
        if len(score_flags) > 30:
            lines.append(f"- (Mostrando 30 de {len(score_flags)} hallazgos de puntajes)")

    lines.append("\n**Inconsistencias de costos**")
    if not cost_flags:
        lines.append("- No se detectaron banderas de costo con las reglas actuales.")
    else:
        for f in cost_flags[:30]:
            t = f.get("type")
            detail = f.get("detail") or ""
            if t == "missing_cost":
                lines.append(f"- Costos faltantes: {', '.join(f.get('alternatives') or [])}. {detail}")
            else:
                alt = f.get("alternative")
                tail = ""
                if f.get("cost") is not None:
                    tail += f" cost={f.get('cost')}"
                if f.get("ratio") is not None:
                    tail += f" ratio={f.get('ratio')}"
                lines.append(f"- {alt}: {detail}{tail}")
        if len(cost_flags) > 30:
            lines.append(f"- (Mostrando 30 de {len(cost_flags)} hallazgos de costos)")

    lines.append("\n**Acciones sugeridas**")
    lines.append("- Paso 2/3: renombra o elimina factores duplicados.")
    lines.append("- Paso 8: ajusta puntajes fuera de cap o fuera de rango sugerido; revisa criterios con diff bajo y puntaje alto.")
    lines.append("- Paso 10: revisa unidades de costo si hay outliers; completa costos faltantes.")

    # Meta informativa
    try:
        lines.append(
            "\n**Meta**\n"
            f"- Factores: {meta.get('criteria_count')} | Alternativas: {meta.get('alternatives_count')}"
        )
    except Exception:
        pass

    return "\n".join(lines).strip()


def _render_simple_list_report(*, title: str, findings: list[str], actions: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"**{title}**")
    if not findings:
        lines.append("- No se detectaron inconsistencias evidentes con las reglas actuales.")
    else:
        for f in findings:
            lines.append(f"- {f}")
    lines.append("\n**Acciones sugeridas**")
    for a in actions:
        lines.append(f"- {a}")
    return "\n".join(lines).strip()


def _build_inconsistency_payload(dashboard: list[dict]) -> tuple[list[dict], dict]:
    """Normaliza el dashboard y calcula banderas de inconsistencias."""

    if not isinstance(dashboard, list) or len(dashboard) == 0:
        raise ValueError("No hay datos del dashboard para auditar.")

    # --- FACTORES duplicados / similares ---
    criteria = list(Criterion.objects.all())
    crit_keys: dict[str, list[str]] = {}
    for c in criteria:
        key = _normalize_name_key(c.name)
        if not key:
            continue
        crit_keys.setdefault(key, []).append(c.name)

    duplicates = [names for _k, names in crit_keys.items() if len(names) > 1]

    # --- PUNTAJES incoherentes ---
    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    alternatives = list(Alternative.objects.all())
    alt_name_by_id = {a.id: a.name for a in alternatives}

    attributes = list(Attribute.objects.select_related("criterion", "alternative").all())
    by_criterion: dict[int, list[Attribute]] = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    crit_rank: dict[int, int] = {c.id: idx for idx, c in enumerate(criteria, start=1)}
    total_criteria = len(criteria) or 1

    score_flags: list[dict] = []
    for criterion in criteria:
        attrs = by_criterion.get(criterion.id) or []
        scored: list[tuple[int, Attribute]] = []
        for attr in attrs:
            s = rating_order.get((attr.description or "").strip())
            if s is None:
                continue
            scored.append((s, attr))
        if not scored:
            continue
        scored.sort(key=lambda t: t[0], reverse=True)
        best_score = scored[0][0]
        second_best_score = None
        for s, _a in scored:
            if s < best_score:
                second_best_score = s
                break
        if second_best_score is None:
            if len(scored) > 1:
                second_best_score = best_score
            else:
                second_best_score = max(1, best_score - 3)
        diff = best_score - second_best_score

        rank = crit_rank.get(criterion.id, total_criteria)
        cap = _criterion_cap_by_rank(rank=rank, total=total_criteria)
        low_m, high_m = _range_multipliers_by_diff(diff)
        suggested_low = _clamp_int(cap * low_m, 0, cap)
        suggested_high = _clamp_int(cap * high_m, 0, cap)
        if suggested_low > suggested_high:
            suggested_low, suggested_high = suggested_high, suggested_low

        best_alt_ids = {attr.alternative_id for s, attr in scored if s == best_score}
        for alt_id in best_alt_ids:
            adv = (
                Advantage.objects.filter(criterion_id=criterion.id, alternative_id=alt_id)
                .only("importance", "description")
                .first()
            )
            if not adv:
                continue

            imp = int(adv.importance or 0)
            desc = (adv.description or "").strip()
            alt_name = alt_name_by_id.get(alt_id, str(alt_id))

            if imp > cap:
                score_flags.append(
                    {
                        "type": "over_cap",
                        "criterion": criterion.name,
                        "alternative": alt_name,
                        "importance": imp,
                        "cap": cap,
                        "detail": "El puntaje supera el máximo sugerido por orden del factor.",
                    }
                )

            if diff <= 1 and imp >= int(0.90 * cap):
                score_flags.append(
                    {
                        "type": "diff_low_but_high",
                        "criterion": criterion.name,
                        "alternative": alt_name,
                        "importance": imp,
                        "cap": cap,
                        "diff": diff,
                        "detail": "La diferencia frente al 2° mejor es baja, pero el puntaje está cerca del máximo.",
                    }
                )

            if desc in {"Cumple", "Regular"} and imp >= int(0.85 * cap):
                score_flags.append(
                    {
                        "type": "weak_label_high",
                        "criterion": criterion.name,
                        "alternative": alt_name,
                        "importance": imp,
                        "cap": cap,
                        "label": desc,
                        "detail": "La calificación cualitativa es baja, pero el puntaje asignado es muy alto.",
                    }
                )

            tol = 5
            if imp < max(0, suggested_low - tol) or imp > min(cap, suggested_high + tol):
                score_flags.append(
                    {
                        "type": "outside_suggested_range",
                        "criterion": criterion.name,
                        "alternative": alt_name,
                        "importance": imp,
                        "cap": cap,
                        "suggested_low": suggested_low,
                        "suggested_high": suggested_high,
                        "diff": diff,
                        "detail": "El puntaje está fuera del rango sugerido por diferencia de atributos.",
                    }
                )

    # --- COSTOS desproporcionados / outliers ---
    costs: list[float] = []
    totals: list[float] = []
    ratios: list[float] = []

    items_norm: list[dict] = []
    for it in dashboard:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        cost = it.get("cost")
        total = it.get("total")
        ratio = it.get("ratio")

        try:
            cost_f = float(cost) if cost is not None else None
        except Exception:
            cost_f = None
        try:
            total_f = float(total) if total is not None else None
        except Exception:
            total_f = None
        try:
            ratio_f = float(ratio) if ratio is not None else None
        except Exception:
            ratio_f = None

        if cost_f is not None:
            costs.append(cost_f)
        if total_f is not None:
            totals.append(total_f)
        if ratio_f is not None:
            ratios.append(ratio_f)

        items_norm.append({"name": name, "cost": cost_f, "total": total_f, "ratio": ratio_f})

    med_cost = _median(costs)
    med_total = _median(totals)
    med_ratio = _median(ratios)

    cost_flags: list[dict] = []
    missing_cost = [x["name"] for x in items_norm if x.get("cost") is None]
    if missing_cost:
        cost_flags.append(
            {
                "type": "missing_cost",
                "alternatives": missing_cost,
                "detail": "Faltan costos: impide evaluar costo/ventaja con confianza.",
            }
        )

    if med_cost is not None and med_cost > 0:
        for x in items_norm:
            c = x.get("cost")
            if c is None:
                continue
            if c >= 2.5 * med_cost:
                cost_flags.append(
                    {
                        "type": "high_cost_outlier",
                        "alternative": x.get("name"),
                        "cost": c,
                        "median_cost": med_cost,
                        "ratio": x.get("ratio"),
                        "detail": "Costo muy por encima de la mediana (posible desproporción o unidad incorrecta).",
                    }
                )

    if med_total is not None:
        for x in items_norm:
            t = x.get("total")
            if t is None:
                continue
            if t == 0:
                cost_flags.append(
                    {
                        "type": "zero_total",
                        "alternative": x.get("name"),
                        "detail": "Ventaja total = 0 (revisa si faltan puntajes en Paso 8).",
                    }
                )

    if med_ratio is not None and med_ratio > 0:
        for x in items_norm:
            r = x.get("ratio")
            if r is None:
                continue
            if r <= 0.25 * med_ratio:
                cost_flags.append(
                    {
                        "type": "low_ratio_outlier",
                        "alternative": x.get("name"),
                        "ratio": r,
                        "median_ratio": med_ratio,
                        "detail": "Relación ventajas/costo muy por debajo de la mediana.",
                    }
                )

    computed = {
        "duplicates": duplicates,
        "score_flags": score_flags,
        "cost_flags": cost_flags,
        "meta": {
            "criteria_count": len(criteria),
            "alternatives_count": len(alternatives),
            "median_cost": med_cost,
            "median_total": med_total,
            "median_ratio": med_ratio,
        },
    }

    return items_norm, computed


def generate_inconsistency_report_text(*, dashboard: list[dict], request_origin: str | None = None) -> tuple[str, dict, str | None]:
    """Ejecuta la auditoría IA y devuelve (texto, computed, warning)."""

    items_norm, computed = _build_inconsistency_payload(dashboard)
    payload = {"dashboard": items_norm, "computed": computed}
    warning = None

    try:
        system_prompt, user_prompt = _build_cba_inconsistency_prompts(payload=payload)
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=request_origin,
        )
    except RuntimeError as exc:
        content = _render_inconsistency_report_text(computed=computed)
        warning = str(exc)

    return content, computed, warning


@login_required
@require_POST
def cba_ai_alternatives_audit(request):
    """Auditoría temprana (Paso 1): alternativas duplicadas / muy similares."""

    alternatives = list(Alternative.objects.all())
    names = [a.name for a in alternatives]
    norm_map: dict[str, list[str]] = {}
    for n in names:
        k = _normalize_name_key(n)
        if not k:
            continue
        norm_map.setdefault(k, []).append(n)

    dup_groups = [g for g in norm_map.values() if len(g) > 1]
    similar_pairs: list[dict] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            r = _similarity(names[i], names[j])
            if r >= 0.88 and _normalize_name_key(names[i]) != _normalize_name_key(names[j]):
                similar_pairs.append({"a": names[i], "b": names[j], "similarity": round(r, 2)})
    similar_pairs.sort(key=lambda x: x["similarity"], reverse=True)
    similar_pairs = similar_pairs[:10]

    # Salida concreta y sin métricas (no mostrar similitud 0.xx)
    reviewed_at = datetime.now().strftime("%H:%M:%S")

    # Payload solo con lo necesario para Paso 1 (nombres)
    generic_names = [n for n in names if _is_generic_alternative_name(n)]
    informal_names = [n for n in names if _is_informal_alternative_name(n)]
    role_names = [n for n in names if _has_role_hint(n)]
    type_counts = {"empresa": 0, "persona": 0, "desconocido": 0}
    for n in names:
        t = _classify_alternative_name(n)
        if t in ("empresa", "persona"):
            type_counts[t] += 1
        else:
            type_counts["desconocido"] += 1

    invalid_names = []
    for n in names:
        if _audit_alternative_name(name=n):
            invalid_names.append(n)

    payload = {
        "n": len(names),
        "reviewed_at": reviewed_at,
        "alternatives": names,
        "computed": {
            "duplicates": dup_groups,
            "similar_pairs": [{"a": p.get("a"), "b": p.get("b")} for p in similar_pairs],
            "generic_names": generic_names,
            "informal_names": informal_names,
            "role_names": role_names,
            "type_counts": type_counts,
            "invalid_names": invalid_names,
        },
    }

    fallback = _render_alternatives_assistant_text(
        alternatives=names,
        dup_groups=dup_groups,
        similar_pairs=similar_pairs,
        reviewed_at=reviewed_at,
    )

    try:
        system_prompt, user_prompt = _build_alternatives_assistant_prompts(payload=payload)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
    except RuntimeError:
        content = fallback

    return JsonResponse({"ok": True, "content": content, "computed": payload.get("computed")})


@login_required
@require_POST
def cba_ai_criteria_audit(request):
    """Asistente (Paso 2): validador de calidad/coherencia de factores."""

    criteria = list(Criterion.objects.all())
    names = [(c.name or "").strip() for c in criteria if (c.name or "").strip()]
    norm_map: dict[str, list[str]] = {}
    for n in names:
        k = _normalize_name_key(n)
        if not k:
            continue
        norm_map.setdefault(k, []).append(n)

    dup_groups = [g for g in norm_map.values() if len(g) > 1]
    similar_pairs: list[dict] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            r = _similarity(names[i], names[j])
            if r >= 0.88 and _normalize_name_key(names[i]) != _normalize_name_key(names[j]):
                similar_pairs.append({"a": names[i], "b": names[j]})
    similar_pairs = similar_pairs[:8]

    setup = request.session.get("cba_setup")
    alternatives = list(Alternative.objects.all())
    alt_names = [(a.name or "").strip() for a in alternatives if (a.name or "").strip()]
    roles_inferred = _infer_roles_from_alternatives(alt_names)

    unclear = [n for n in names if _is_unclear_criterion_name(n)]
    mixed = [n for n in names if _is_mixed_criterion(n)]
    unmeasurable = [n for n in names if _is_unmeasurable_criterion(n)]
    out_of_context = _role_mismatch_flags(roles=roles_inferred, criteria=names)

    reviewed_at = datetime.now().strftime("%H:%M:%S")

    payload = {
        "n": len(names),
        "reviewed_at": reviewed_at,
        "setup": setup or {},
        "alternatives": alt_names,
        "roles_inferred": roles_inferred,
        "criteria": names,
        "computed": {
            "duplicates": dup_groups,
            "similar_pairs": similar_pairs,
            "unclear": unclear,
            "mixed": mixed,
            "unmeasurable": unmeasurable,
            "out_of_context": out_of_context,
        },
    }

    fallback = _render_criteria_assistant_text(
        criteria=names,
        reviewed_at=reviewed_at,
        dup_groups=dup_groups,
        similar_pairs=similar_pairs,
        unclear=unclear,
        mixed=mixed,
        unmeasurable=unmeasurable,
        out_of_context=out_of_context,
        setup=setup if isinstance(setup, dict) else None,
    )

    try:
        system_prompt, user_prompt = _build_criteria_assistant_prompts(payload=payload)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
    except RuntimeError:
        content = fallback

    return JsonResponse({"ok": True, "content": content, "computed": payload.get("computed")})


@login_required
@require_POST
def cba_ai_criteria_type_audit(request):
    """Asistente (Paso 3): validador de lógica MUST/WANT."""

    setup = request.session.get("cba_setup")
    criteria = list(Criterion.objects.all())

    must = [c for c in criteria if c.criterion_type == Criterion.TYPE_MUST]
    want = [c for c in criteria if c.criterion_type == Criterion.TYPE_WANT]

    reviewed_at = datetime.now().strftime("%H:%M:%S")
    total = len(criteria)

    missing_desc_must = [c.name for c in must if not (c.description or "").strip()]

    # MUST dudosos (no parecen indispensables)
    must_suspicious: list[str] = []
    for c in must:
        ck = _normalize_name_key(c.name)
        if not ck:
            continue
        if any(t in ck for t in _MUST_SUSPICIOUS_TOKENS):
            must_suspicious.append(c.name)

    # WANT que podrían ser MUST (criterios típicamente mínimos)
    want_should_be_must: list[str] = []
    for c in want:
        ck = _normalize_name_key(c.name)
        if not ck:
            continue
        if any(t in ck for t in _MUST_LIKELY_TOKENS):
            want_should_be_must.append(c.name)

    # Coherencia básica con el objetivo/proyecto: generar solo un hint corto
    objective_hint = None
    objective = ""
    project_name = ""
    if isinstance(setup, dict):
        objective = (setup.get("objective") or "").strip()
        project_name = (setup.get("project_name") or "").strip()

    goal_kw = _extract_keywords(objective) | _extract_keywords(project_name)
    if goal_kw:
        crit_kw = set()
        for c in criteria:
            crit_kw |= _extract_keywords(c.name)
        overlap = goal_kw & crit_kw
        if not overlap and total >= 3:
            objective_hint = "revisa alineación de factores con el objetivo"
        else:
            # hints por dominio (simple)
            goal_text = " ".join(sorted(goal_kw))
            if any(x in goal_text for x in ["hospital", "salud", "clinica", "clínica"]):
                objective_hint = "salud/hospital (seguridad e higiene suelen ser críticas)"
            elif any(x in goal_text for x in ["ti", "software", "sistema", "implementacion", "implementación"]):
                objective_hint = "TI/sistema (seguridad y continuidad suelen ser críticas)"

    payload = {
        "n": total,
        "reviewed_at": reviewed_at,
        "setup": setup or {},
        "criteria": [
            {
                "id": c.id,
                "name": (c.name or "").strip(),
                "type": c.criterion_type,
                "description": (c.description or "").strip(),
            }
            for c in criteria
        ],
        "computed": {
            "must_count": len(must),
            "want_count": len(want),
            "missing_desc_must": missing_desc_must,
            "must_suspicious": must_suspicious,
            "want_should_be_must": want_should_be_must,
            "objective_hint": objective_hint,
        },
    }

    fallback = _render_mustwant_assistant_text(
        total=total,
        reviewed_at=reviewed_at,
        must_count=len(must),
        want_count=len(want),
        must_suspicious=must_suspicious,
        want_should_be_must=want_should_be_must,
        missing_desc_must=missing_desc_must,
        objective_hint=objective_hint,
    )

    try:
        system_prompt, user_prompt = _build_mustwant_assistant_prompts(payload=payload)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
    except RuntimeError:
        content = fallback

    return JsonResponse({"ok": True, "content": content, "computed": payload.get("computed")})


@login_required
@require_POST
def cba_ai_scores_audit(request):
    """Auditoría en caliente (Paso 8): valida consistencia CBA de los puntajes de importancia.

    Reglas principales:
    - Solo debe haber puntaje en celdas habilitadas (ganador/es en Paso 6).
    - Un único puntaje por factor (si hay empate, advertir).
    - Puntajes faltantes, no numéricos, negativos o fuera de cap.
    - Coherencia básica con la magnitud de la ventaja (diff) y MUST/WANT.
    """

    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    scores = body.get("scores")
    if not isinstance(scores, dict):
        return JsonResponse({"ok": False, "error": "Falta 'scores'."}, status=400)

    reviewed_at = datetime.now().strftime("%H:%M")

    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    alternatives = list(Alternative.objects.all())
    criteria = list(Criterion.objects.all())
    total_criteria = len(criteria) or 1

    attributes = list(Attribute.objects.select_related("criterion", "alternative").all())
    by_criterion: dict[int, list[Attribute]] = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    enabled_fields: dict[str, dict] = {}
    enabled_fields_by_criterion: dict[int, list[str]] = {}

    errors: list[str] = []
    warnings: list[str] = []

    multiple_factors: list[str] = []
    tie_factors: list[str] = []
    missing_factors: list[str] = []
    invalid_examples: list[str] = []
    disabled_examples: list[str] = []

    for idx, criterion in enumerate(criteria, start=1):
        attrs = by_criterion.get(criterion.id) or []
        scored: list[tuple[int, Attribute]] = []
        for attr in attrs:
            s = rating_order.get((attr.description or "").strip())
            if s is None:
                continue
            scored.append((s, attr))
        if not scored:
            continue
        scored.sort(key=lambda t: t[0], reverse=True)
        best_score = scored[0][0]
        second_best_score = None
        for s, _a in scored:
            if s < best_score:
                second_best_score = s
                break
        if second_best_score is None:
            if len(scored) > 1:
                second_best_score = best_score
            else:
                second_best_score = max(1, best_score - 3)

        diff = best_score - second_best_score
        cap = _criterion_cap_by_rank(rank=idx, total=total_criteria)
        low_m, high_m = _range_multipliers_by_diff(diff)
        suggested_low = _clamp_int(cap * low_m, 0, cap)
        suggested_high = _clamp_int(cap * high_m, 0, cap)
        if suggested_low > suggested_high:
            suggested_low, suggested_high = suggested_high, suggested_low

        for s, attr in scored:
            if s != best_score:
                continue
            field = f"imp_{criterion.id}_{attr.alternative_id}"
            enabled_fields[field] = {
                "criterion": criterion.name,
                "criterion_id": criterion.id,
                "criterion_type": getattr(criterion, "criterion_type", None),
                "alternative": attr.alternative.name,
                "alternative_id": attr.alternative_id,
                "cap": cap,
                "diff": diff,
                "suggested_low": suggested_low,
                "suggested_high": suggested_high,
                "label": (attr.description or "").strip(),
            }
            enabled_fields_by_criterion.setdefault(criterion.id, []).append(field)

    # 1) Puntajes en celdas NO habilitadas (si alguien manipula el payload)
    for k, v in scores.items():
        if not isinstance(k, str):
            continue
        if not k.startswith("imp_"):
            continue
        if k in enabled_fields:
            continue
        raw = str(v).strip()
        if raw != "":
            errors.append(f"Puntaje en celda no habilitada: {k} = '{raw}'.")
            if len(disabled_examples) < 4:
                disabled_examples.append(k)

    # Helpers
    def _parse_int(raw_value: object) -> tuple[int | None, str | None]:
        raw = str(raw_value if raw_value is not None else "").strip()
        if raw == "":
            return None, None
        try:
            return int(float(raw)), None
        except Exception:
            return None, raw

    # 2-7) Auditoría por factor
    must_norms: list[float] = []
    want_norms: list[float] = []
    per_rank_norms: list[tuple[int, float, str]] = []

    for idx, criterion in enumerate(criteria, start=1):
        fields = enabled_fields_by_criterion.get(criterion.id) or []
        if not fields:
            continue

        numeric_values: list[tuple[str, int]] = []
        cap = _criterion_cap_by_rank(rank=idx, total=total_criteria)

        for field in fields:
            meta = enabled_fields.get(field) or {}
            parsed, non_numeric = _parse_int(scores.get(field, ""))
            if non_numeric is not None:
                errors.append(
                    f"Puntaje no numérico en {meta.get('criterion', criterion.name)} → {meta.get('alternative', field)}: '{non_numeric}'."
                )
                if len(invalid_examples) < 6 and criterion.name not in invalid_examples:
                    invalid_examples.append(f"{criterion.name}")
                continue
            if parsed is None:
                continue
            numeric_values.append((field, parsed))

            if parsed < 0:
                errors.append(
                    f"Puntaje negativo en {meta.get('criterion', criterion.name)} → {meta.get('alternative', field)}: {parsed}."
                )
                if len(invalid_examples) < 6 and criterion.name not in invalid_examples:
                    invalid_examples.append(f"{criterion.name}")
            if parsed > cap:
                errors.append(
                    f"Puntaje fuera de rango (cap) en {meta.get('criterion', criterion.name)} → {meta.get('alternative', field)}: {parsed} > {cap}."
                )
                if len(invalid_examples) < 6 and criterion.name not in invalid_examples:
                    invalid_examples.append(f"{criterion.name}")

            low = int(meta.get("suggested_low", 0))
            high = int(meta.get("suggested_high", cap))
            tol = 5
            if parsed < max(0, low - tol) or parsed > min(cap, high + tol):
                warnings.append(
                    f"{meta.get('criterion', criterion.name)} → {meta.get('alternative', field)}: puntaje {parsed} fuera del rango sugerido {low}–{high} (cap {cap})."
                )

            diff = int(meta.get("diff", 0) or 0)
            # 3) Escala incoherente vs magnitud de ventaja
            if diff >= 2 and parsed <= int(0.35 * cap):
                warnings.append(
                    f"{meta.get('criterion', criterion.name)}: ventaja marcada como fuerte (diff {diff}) pero puntaje bajo ({parsed}/{cap})."
                )
            if diff <= 1 and parsed >= int(0.85 * cap):
                warnings.append(
                    f"{meta.get('criterion', criterion.name)}: ventaja muy similar (diff {diff}) pero puntaje casi máximo ({parsed}/{cap})."
                )

            # 5) MUST con baja importancia
            ctype = (getattr(criterion, "criterion_type", "") or "").strip().upper()
            norm = (float(parsed) / float(cap)) if cap else 0.0
            if ctype == "MUST":
                must_norms.append(norm)
                if norm < 0.20:
                    errors.append(
                        f"{criterion.name}: criterio MUST con puntaje muy bajo ({parsed}/{cap})."
                    )
                elif norm < 0.35:
                    warnings.append(
                        f"{criterion.name}: criterio MUST con puntaje bajo ({parsed}/{cap})."
                    )
            elif ctype == "WANT":
                want_norms.append(norm)

            per_rank_norms.append((idx, norm, criterion.name))

        # 1) Puntajes donde NO hay mayor ventaja / múltiples por factor
        if len(numeric_values) == 0:
            errors.append(
                f"{criterion.name}: hay ventaja identificada en Paso 6 pero no se asignó ningún puntaje."
            )
            if criterion.name not in missing_factors:
                missing_factors.append(criterion.name)
        elif len(numeric_values) > 1:
            # Siempre inconsistencia según regla: solo uno por factor
            alts = []
            for f, val in numeric_values:
                meta = enabled_fields.get(f) or {}
                alts.append(f"{meta.get('alternative', f)} → {val}")
            errors.append(
                f"{criterion.name}: múltiples puntajes asignados ({'; '.join(alts)})."
            )
            if criterion.name not in multiple_factors:
                multiple_factors.append(criterion.name)

        # 2) Empates sin justificación (si hay más de una celda habilitada)
        if len(fields) > 1:
            warnings.append(
                f"Existe empate en el factor {criterion.name}. Verificar si realmente tienen la misma ventaja."
            )
            if criterion.name not in tie_factors:
                tie_factors.append(criterion.name)

    # 4) Saltos ilógicos en la escala (heurística simple por cambios fuertes entre factores)
    per_rank_norms.sort(key=lambda t: t[0])
    for (r1, n1, name1), (r2, n2, name2) in zip(per_rank_norms, per_rank_norms[1:]):
        # Umbral más sensible para detectar bajones muy grandes (ej. 100 → 40)
        if abs(n2 - n1) >= 0.45:
            warnings.append(
                f"Saltos fuertes en la escala: {name1} → {name2}. Revisa jerarquía/proporcionalidad."
            )

    # 5) WANT por encima de MUST sin justificación (comparación global)
    if must_norms and want_norms:
        min_must = min(must_norms)
        max_want = max(want_norms)
        if max_want - min_must >= 0.30:
            warnings.append(
                "Hay factores WANT con mayor importancia relativa que al menos un MUST. Verifica si eso está justificado."
            )

    # Texto visible (simple, tipo Paso 1/2)
    fallback = _render_scores_audit_assistant_text(
        reviewed_at=reviewed_at,
        multiple_factors=multiple_factors,
        tie_factors=tie_factors,
        missing_factors=missing_factors,
        invalid_examples=invalid_examples,
        disabled_examples=disabled_examples,
    )

    payload = {
        "computed": {
            "reviewed_at": reviewed_at,
            "errors": errors,
            "warnings": warnings,
            "enabled_fields": enabled_fields,
            "summary": {
                "multiple_factors": multiple_factors,
                "tie_factors": tie_factors,
                "missing_factors": missing_factors,
                "invalid_examples": invalid_examples,
                "disabled_examples": disabled_examples,
            },
        },
    }

    # Intentar redacción con IA, pero forzando formato corto tipo Paso 1/2.
    try:
        system_prompt, user_prompt = _build_scores_assistant_prompts(payload=payload)
        user_prompt = user_prompt.replace("{reviewed_at}", reviewed_at)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
    except RuntimeError:
        content = fallback

    # Guardrail: si la IA devuelve algo largo o con formato tipo "reporte" extenso, usar fallback.
    try:
        raw_lines = [ln.strip() for ln in str(content or "").splitlines() if ln.strip()]
        looks_like_report = any(
            key in str(content) for key in ["**Hallazgos", "**Por qué", "Cómo corregir", "Reporte de Auditoría"]
        )
        if looks_like_report or len(raw_lines) > 9:
            content = fallback
    except Exception:
        content = fallback

    return JsonResponse({"ok": True, "content": content, "computed": payload.get("computed")})


@login_required
@require_POST
def cba_ai_costs_audit(request):
    """Auditoría en caliente (Paso 10): costos faltantes/atípicos y coherencia básica."""

    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    items = body.get("items")
    if not isinstance(items, list) or not items:
        return JsonResponse({"ok": False, "error": "Falta 'items'."}, status=400)

    reviewed_at = datetime.now().strftime("%H:%M")

    setup = request.session.get("cba_setup")
    cost_context, cost_context_reason = _infer_cost_context_from_setup(setup)
    persona_max_expected = 25000.0

    norm: list[dict] = []
    costs: list[float] = []
    ratios: list[float] = []
    missing: list[str] = []
    non_numeric: list[str] = []

    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        try:
            cost = float(it.get("cost")) if it.get("cost") not in (None, "") else None
        except Exception:
            cost = None
            if name and name not in non_numeric:
                non_numeric.append(name)
        try:
            total = float(it.get("total")) if it.get("total") not in (None, "") else None
        except Exception:
            total = None

        ratio = None
        if cost is not None and cost != 0 and total is not None:
            try:
                ratio = float(total) / float(cost)
            except Exception:
                ratio = None

        norm.append({"name": name, "cost": cost, "total": total, "ratio": ratio})
        if cost is not None:
            costs.append(cost)
        else:
            if name and name not in missing:
                missing.append(name)
        if ratio is not None:
            ratios.append(ratio)

    med_cost = _median(costs)
    med_ratio = _median(ratios)

    outlier_high: list[str] = []
    if med_cost is not None and med_cost > 0:
        for x in norm:
            c = x.get("cost")
            if c is None:
                continue
            if c >= 2.5 * float(med_cost):
                outlier_high.append(x["name"])

    ratio_low: list[str] = []
    if med_ratio is not None and med_ratio > 0:
        for x in norm:
            r = x.get("ratio")
            if r is None:
                continue
            if r <= 0.25 * float(med_ratio):
                ratio_low.append(x["name"])

    min_cost = min(costs) if costs else None
    max_cost = max(costs) if costs else None

    context_note = None
    context_warnings: list[str] = []
    if cost_context != "desconocido":
        context_note = f"Contexto detectado: {cost_context} ({cost_context_reason})."

    # Reglas solicitadas: umbral simple por contexto.
    if max_cost is not None:
        if cost_context == "persona" and float(max_cost) > persona_max_expected:
            context_warnings.append(
                f"Monto alto para rol de persona: S/ {int(round(max_cost))} (esperable ≤ S/ {int(persona_max_expected)})."
            )
        if cost_context == "empresa" and float(max_cost) <= persona_max_expected:
            context_warnings.append(
                f"Monto bajo para empresa/entidad: máximo S/ {int(round(max_cost))} (revisa si falta un cero o si es mensual)."
            )

    fallback = _render_costs_audit_assistant_text(
        reviewed_at=reviewed_at,
        missing=missing,
        non_numeric=non_numeric,
        min_cost=min_cost,
        max_cost=max_cost,
        outlier_high=outlier_high,
        ratio_low=ratio_low,
        context_note=context_note,
        context_warnings=context_warnings,
    )

    payload = {
        "items": norm,
        "setup": setup or {},
        "computed": {
            "reviewed_at": reviewed_at,
            "missing": missing,
            "non_numeric": non_numeric,
            "min_cost": min_cost,
            "max_cost": max_cost,
            "median_cost": med_cost,
            "median_ratio": med_ratio,
            "outlier_high": outlier_high,
            "ratio_low": ratio_low,
            "cost_context": cost_context,
            "cost_context_reason": cost_context_reason,
            "persona_max_expected": persona_max_expected,
            "context_warnings": context_warnings,
        },
    }

    try:
        system_prompt, user_prompt = _build_costs_assistant_prompts(payload=payload)
        user_prompt = user_prompt.replace("{reviewed_at}", reviewed_at)
        origin = request.build_absolute_uri("/")
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=origin,
        )
    except RuntimeError:
        content = fallback

    # Guardrail: evitar que vuelva el "reporte" largo
    try:
        raw_lines = [ln.strip() for ln in str(content or "").splitlines() if ln.strip()]
        looks_like_report = any(
            key in str(content) for key in ["**Hallazgos", "**Por qué", "Cómo corregir", "Reporte de Auditoría"]
        )
        if looks_like_report or len(raw_lines) > 9:
            content = fallback
    except Exception:
        content = fallback

    return JsonResponse({"ok": True, "content": content, "computed": payload.get("computed")})


@login_required
@require_POST
def cba_ai_inconsistency_audit(request):
    """Detecta inconsistencias (factores, puntajes, costos) y las redacta con IA.

    La detección base es determinista y la IA solo redacta/ordena el informe.
    """

    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    dashboard = body.get("dashboard")
    try:
        content, computed, warning = generate_inconsistency_report_text(
            dashboard=dashboard,
            request_origin=request.build_absolute_uri("/"),
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    response = {"ok": True, "content": content, "computed": computed}
    if warning:
        response["warning"] = warning
    return JsonResponse(response)


@login_required
@require_POST
def cba_ai_suggest_scores(request):
    """Sugiere puntajes de importancia para el Paso 8 sin imponerlos.

    - Usa el orden de factores (Criterion.objects.all()) como jerarquía: el primero tiene cap 100.
    - Analiza diferencias ordinales entre alternativas (Excelente/Bueno/Regular/Cumple).
    - Devuelve un rango sugerido y un valor por defecto (punto medio) para autocompletar.
    """

    rating_order = {
        "Excelente": 4,
        "Bueno": 3,
        "Regular": 2,
        "Cumple": 1,
    }

    alternatives = list(Alternative.objects.all())
    criteria = list(Criterion.objects.all())  # orden: el de más arriba es más importante
    if not alternatives or not criteria:
        return JsonResponse(
            {"ok": False, "error": "No hay alternativas o factores cargados."}, status=400
        )

    attributes = list(Attribute.objects.select_related("criterion", "alternative").all())
    if not attributes:
        return JsonResponse(
            {"ok": False, "error": "No hay atributos para analizar (Paso 4/5)."},
            status=400,
        )

    # Agrupar por criterio
    by_criterion: dict[int, list[Attribute]] = {}
    for attr in attributes:
        by_criterion.setdefault(attr.criterion_id, []).append(attr)

    # Detectar mejor/segundo mejor por criterio
    suggestions: list[dict] = []
    total_criteria = len(criteria)

    for idx, criterion in enumerate(criteria, start=1):
        attrs = by_criterion.get(criterion.id) or []
        if not attrs:
            continue

        scored: list[tuple[int, Attribute]] = []
        for attr in attrs:
            score = rating_order.get((attr.description or "").strip())
            if score is None:
                continue
            scored.append((score, attr))

        if not scored:
            continue

        scored.sort(key=lambda t: t[0], reverse=True)
        best_score = scored[0][0]
        second_best_score = None
        for s, _a in scored:
            if s < best_score:
                second_best_score = s
                break

        # Si no existe un "2° mejor" porque todos empataron, la diferencia es 0.
        # Si solo hay una alternativa evaluada, asumimos diferencia alta.
        if second_best_score is None:
            if len(scored) > 1:
                second_best_score = best_score
            else:
                second_best_score = max(1, best_score - 3)

        diff = best_score - second_best_score
        cap = _criterion_cap_by_rank(rank=idx, total=total_criteria)
        low_m, high_m = _range_multipliers_by_diff(diff)

        # Regla UX: el factor más importante (el primero) inicia en 100 puntos.
        if idx == 1:
            low = cap
            high = cap
            value = cap
        else:
            low = _clamp_int(cap * low_m, 0, cap)
            high = _clamp_int(cap * high_m, 0, cap)
            if low > high:
                low, high = high, low
            # Piso para evitar sugerencias demasiado bajas (p.ej. 40) en factores altos.
            floor_value = _clamp_int(cap * 0.80, 0, cap)
            low = max(low, floor_value)
            high = max(high, floor_value)
            if low > high:
                low, high = high, low
            value = _clamp_int((low + high) / 2.0, 0, cap)

        # Puede haber empate: sugerir para todas las alternativas con mejor score.
        for s, attr in scored:
            if s != best_score:
                continue
            suggestions.append(
                {
                    "criterion_id": criterion.id,
                    "criterion_name": criterion.name,
                    "alternative_id": attr.alternative_id,
                    "alternative_name": attr.alternative.name,
                    "field": f"imp_{criterion.id}_{attr.alternative_id}",
                    "cap": cap,
                    "diff": diff,
                    "suggested_low": low,
                    "suggested_high": high,
                    "suggested_value": value,
                    "reason": (
                        f"Factor #{idx}/{total_criteria} (cap {cap}). "
                        f"Diferencia ordinal vs 2° mejor: {diff}."
                    ),
                }
            )

    if not suggestions:
        return JsonResponse(
            {
                "ok": False,
                "error": "No se pudieron generar sugerencias (revisa que los atributos usen Excelente/Bueno/Regular/Cumple).",
            },
            status=400,
        )

    return JsonResponse({"ok": True, "suggestions": suggestions})


def _get_openrouter_config() -> tuple[str, str, float]:
    """Obtiene (api_key, model, timeout) desde BD (Admin) o fallback a settings."""

    api_key = ""
    model = ""
    timeout = None

    try:
        cfg = AIProviderSetting.objects.filter(
            provider=AIProviderSetting.PROVIDER_OPENROUTER
        ).first()
    except Exception:
        cfg = None

    if cfg:
        api_key = (cfg.api_key or "").strip()
        model = (cfg.model or "").strip()
        timeout = cfg.timeout_seconds

    if not api_key:
        api_key = getattr(settings, "OPENROUTER_API_KEY", "") or ""
    if not model:
        model = getattr(settings, "OPENROUTER_MODEL", "") or ""
    if timeout is None:
        timeout = float(getattr(settings, "OPENROUTER_TIMEOUT_SECONDS", 30) or 30)

    return api_key, model, float(timeout)


def _is_upstream_rate_limited(detail: str) -> bool:
    """Detecta el patrón típico de rate-limit upstream devuelto por OpenRouter."""

    if not detail:
        return False

    try:
        data = json.loads(detail)
    except Exception:
        return "rate-limited" in detail.lower() or "rate limited" in detail.lower()

    err = (data or {}).get("error") or {}
    code = err.get("code")
    meta = err.get("metadata") or {}
    raw = (meta.get("raw") or "")
    if code == 429:
        return True
    return "rate-limited" in raw.lower() or "rate limited" in raw.lower()


def _default_free_fallback_models() -> list[str]:
    # Lista corta de modelos :free vistos comúnmente en OpenRouter.
    # Se usa solo si el modelo principal está temporalmente limitado.
    return [
        "mistralai/mistral-small-3.1-24b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free",
        "google/gemma-3-12b-it:free",
        "google/gemma-3-4b-it:free",
    ]


def _is_developer_instruction_not_enabled(detail: str) -> bool:
    if not detail:
        return False
    # Error típico de Google AI Studio cuando el modelo no acepta system/developer.
    needle = "Developer instruction is not enabled"
    if needle.lower() in detail.lower():
        return True
    try:
        data = json.loads(detail)
    except Exception:
        return False
    raw = (((data or {}).get("error") or {}).get("metadata") or {}).get("raw")
    if isinstance(raw, str) and needle.lower() in raw.lower():
        return True
    # A veces el mensaje viene directo en raw JSON del provider
    if isinstance(data, dict):
        msg = ((data.get("error") or {}).get("message") or "")
        if isinstance(msg, str) and needle.lower() in msg.lower():
            return True
    return False


def _collapse_messages_to_user(messages: list[dict]) -> list[dict]:
    """Convierte system+user en un solo user para modelos que no aceptan system/developer."""

    if not messages:
        return []
    parts: list[str] = []
    for m in messages:
        role = (m.get("role") or "").strip().lower()
        content = (m.get("content") or "")
        if not content:
            continue
        if role in {"system", "developer"}:
            parts.append(f"INSTRUCCIONES:\n{content}")
        elif role == "user":
            parts.append(content)
        else:
            # cualquier otro rol lo tratamos como texto adicional
            parts.append(content)
    merged = "\n\n".join(parts).strip()
    return [{"role": "user", "content": merged}] if merged else []


def _openrouter_chat(*, messages: list[dict], request_origin: str | None = None) -> str:
    api_key, model, timeout = _get_openrouter_config()

    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY no está configurada")
    if not model:
        raise RuntimeError("OPENROUTER_MODEL no está configurado")

    # Si el modelo gratuito está rate-limited upstream, probamos con alternativas.
    candidate_models: list[str] = []
    if model:
        candidate_models.append(model)
    for m in _default_free_fallback_models():
        if m not in candidate_models:
            candidate_models.append(m)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Headers recomendados por OpenRouter (opcionales)
    if request_origin:
        headers["HTTP-Referer"] = request_origin
    headers["X-Title"] = "SIDEO (CBA)"

    last_error: RuntimeError | None = None
    raw = ""
    for model_id in candidate_models:
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.2,
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            # éxito
            break
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")
            except Exception:
                detail = ""

            code = getattr(e, "code", "error")

            if code == 400 and _is_developer_instruction_not_enabled(detail):
                # Algunos modelos (p.ej. Gemma via Google AI Studio) no aceptan system/developer.
                # Reintenta una sola vez con mensajes colapsados a user.
                collapsed = _collapse_messages_to_user(messages)
                if collapsed and collapsed != messages:
                    payload2 = {
                        "model": model_id,
                        "messages": collapsed,
                        "temperature": 0.2,
                    }
                    req2 = urllib.request.Request(
                        "https://openrouter.ai/api/v1/chat/completions",
                        data=json.dumps(payload2).encode("utf-8"),
                        headers=headers,
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(req2, timeout=timeout) as resp:
                            raw = resp.read().decode("utf-8")
                        break
                    except urllib.error.HTTPError:
                        # Si sigue fallando, probamos el siguiente modelo
                        last_error = RuntimeError(
                            "OpenRouter: el modelo actual no acepta instrucciones de sistema. "
                            "Se intentó compatibilidad y falló; prueba con otro modelo en Admin."
                        )
                        continue
                last_error = RuntimeError(
                    "OpenRouter: el modelo actual no acepta instrucciones de sistema. "
                    "Cambia el modelo en Admin a uno tipo '...instruct...' o '...chat...'."
                )
                continue

            if code == 404 and "No endpoints found" in (detail or ""):
                last_error = RuntimeError(
                    "OpenRouter: el modelo configurado no está disponible. "
                    "Cambia el modelo en Admin (Configuraciones de IA) por un id válido."
                )
                # si hay más modelos, intenta el siguiente
                continue

            if code == 429 and _is_upstream_rate_limited(detail) and model_id != candidate_models[-1]:
                # Modelo temporalmente limitado, intentamos con el siguiente
                last_error = RuntimeError(
                    "OpenRouter: el modelo gratuito está temporalmente saturado (rate-limit upstream). "
                    "Reintenta en unos segundos o cambia el modelo en Admin."
                )
                continue

            last_error = RuntimeError(f"OpenRouter HTTP {code}: {detail or str(e)}")
            # Para 400/429/etc, intentamos con el siguiente modelo si hay.
            if model_id != candidate_models[-1]:
                continue
            raise last_error
        except Exception as e:
            last_error = RuntimeError(f"Error llamando a OpenRouter: {str(e)}")
            raise last_error
    else:
        # nunca debería entrar, pero por seguridad
        raise RuntimeError("No se pudo contactar OpenRouter")

    if not raw:
        if last_error:
            raise last_error
        raise RuntimeError("OpenRouter no devolvió respuesta")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Respuesta inválida de OpenRouter")

    try:
        return (data.get("choices") or [])[0]["message"]["content"]
    except Exception:
        raise RuntimeError("OpenRouter no devolvió contenido")


def _build_cba_decision_prompts(*, setup: dict | None, dashboard: list[dict]) -> tuple[str, str]:
    # --- Cálculos deterministas (la IA NO decide el ganador) ---
    normalized: list[dict] = []
    for it in dashboard or []:
        name = (it.get("name") or "").strip()
        if not name:
            continue

        cost = it.get("cost")
        total = it.get("total")
        ratio = it.get("ratio")

        try:
            cost_v = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_v = None

        try:
            total_v = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_v = 0

        # ratio = costo por punto de ventaja (menor es mejor)
        ratio_v = None
        if ratio is not None:
            try:
                ratio_v = float(ratio)
            except (TypeError, ValueError):
                ratio_v = None

        if ratio_v is None and cost_v not in (None, 0.0) and total_v:
            ratio_v = float(cost_v) / float(total_v)

        normalized.append(
            {
                "name": name,
                "cost": cost_v,
                "total": total_v,
                "ratio": ratio_v,
            }
        )

    # Orden por ratio asc (criterio del dashboard: menor costo por punto)
    by_ratio = [x for x in normalized if x.get("ratio") is not None]
    by_ratio.sort(key=lambda x: x["ratio"])
    winner = by_ratio[0] if by_ratio else None
    second = by_ratio[1] if len(by_ratio) > 1 else None

    delta_ratio = None
    delta_pct = None
    if winner and second and winner.get("ratio") is not None and second.get("ratio") is not None:
        try:
            delta_ratio = float(second["ratio"]) - float(winner["ratio"])
            if float(second["ratio"]) > 0:
                delta_pct = (delta_ratio / float(second["ratio"])) * 100.0
        except Exception:
            delta_ratio = None
            delta_pct = None

    # --- Contexto desde BD (ventaja principal y desventaja por criterio) ---
    winner_main_adv = None
    winner_main_adv_points = None
    winner_main_adv_criterion = None

    winner_disadvantage = None

    names = [x["name"] for x in normalized]
    alt_by_name = {}
    if names:
        # Prefetch ventajas para análisis comparativo
        for alt in (
            Alternative.objects.filter(name__in=names)
            .prefetch_related("advantages__criterion")
            .all()
        ):
            alt_by_name[alt.name] = alt

    if winner and winner.get("name") in alt_by_name:
        win_alt = alt_by_name[winner["name"]]
        win_advs = list(win_alt.advantages.all())

        # Ventaja principal: marcada como is_main, si no, la de mayor importancia
        main = next((a for a in win_advs if getattr(a, "is_main", False)), None)
        if main is None and win_advs:
            main = max(win_advs, key=lambda a: getattr(a, "importance", 0) or 0)

        if main is not None:
            winner_main_adv = main.description
            winner_main_adv_points = main.importance
            try:
                winner_main_adv_criterion = main.criterion.name
            except Exception:
                winner_main_adv_criterion = None

        # Desventaja: criterio donde el ganador queda más atrás vs el mejor de otros
        # Se calcula por diferencia de importancia en ese criterio.
        crit_ids = set(a.criterion_id for a in win_advs)
        deficits = []
        for crit_id in crit_ids:
            win_adv = next((a for a in win_advs if a.criterion_id == crit_id), None)
            win_pts = getattr(win_adv, "importance", 0) if win_adv else 0

            best_other_alt = None
            best_other_adv = None
            best_other_pts = None

            for alt_name, alt in alt_by_name.items():
                if alt_name == win_alt.name:
                    continue
                adv = next((a for a in alt.advantages.all() if a.criterion_id == crit_id), None)
                pts = getattr(adv, "importance", 0) if adv else 0
                if best_other_pts is None or pts > best_other_pts:
                    best_other_pts = pts
                    best_other_alt = alt
                    best_other_adv = adv

            if best_other_pts is None:
                continue

            deficit = (best_other_pts or 0) - (win_pts or 0)
            if deficit > 0:
                crit_name = None
                try:
                    crit_name = win_adv.criterion.name if win_adv else best_other_adv.criterion.name
                except Exception:
                    crit_name = None

                deficits.append(
                    {
                        "criterion": crit_name,
                        "deficit": deficit,
                        "winner_points": win_pts,
                        "winner_adv": getattr(win_adv, "description", None),
                        "best_other": getattr(best_other_alt, "name", None),
                        "best_other_points": best_other_pts,
                        "best_other_adv": getattr(best_other_adv, "description", None),
                    }
                )

        if deficits:
            deficits.sort(key=lambda d: d.get("deficit") or 0, reverse=True)
            winner_disadvantage = deficits[0]

    # Alertas deterministas
    alerts: list[str] = []
    missing_cost = [x["name"] for x in normalized if x.get("cost") is None]
    missing_ratio = [x["name"] for x in normalized if x.get("ratio") is None]

    if missing_cost:
        alerts.append(
            f"Costos faltantes en: {', '.join(missing_cost)} (no se puede calcular la relación Ventajas/Costo)."
        )
    if missing_ratio:
        alerts.append(
            f"Relación Ventajas/Costo no calculable en: {', '.join(missing_ratio)}."
        )

    if isinstance(setup, dict):
        if not (setup.get("reference_budget") or "").strip():
            alerts.append("El Presupuesto de Referencia está vacío.")
        sector = (setup.get("sector") or "").strip().upper()
        if sector == "PUBLICO" and not (setup.get("public_entity") or "").strip():
            alerts.append("No se especifica la entidad pública (Solicitante).")
        if sector == "PRIVADO" and not (setup.get("private_company") or "").strip():
            alerts.append("No se especifica la empresa privada (Solicitante).")

    system_prompt = (
        "Eres un asistente institucional de toma de decisiones para SIDEO (metodología Choosing By Advantages - CBA). "
        "NO inventes datos: usa solo el JSON entregado (alternativa, costo, ventaja total, ratio). "
        "IMPORTANTE: la recomendación (ganador) la calcula el sistema; NO la cambies. "
        "Redacta un resumen técnico muy breve, directo y numérico. "
        "Responde SIEMPRE en español."
    )

    user_payload = {
        "setup": setup,
        "dashboard": normalized,
        "computed": {
            "winner": winner,
            "second": second,
            "delta_ratio": delta_ratio,
            "delta_pct": delta_pct,
            "alerts": alerts,
            "winner_main_advantage": {
                "criterion": winner_main_adv_criterion,
                "description": winner_main_adv,
                "importance": winner_main_adv_points,
            }
            if winner_main_adv is not None
            else None,
            "winner_disadvantage": winner_disadvantage,
        },
    }

    user_prompt = (
        "Redacta UNA SOLA ORACIÓN completa (una sola línea, sin saltos de línea), clara para cualquier lector, usando SOLO el JSON.\n"
        "Requisitos de estilo (obligatorio):\n"
        "- No uses etiquetas tipo 'Recomendación:' ni formato de campos 'ratio:', 'total:', 'costo:' ni nombres de variables ('delta_pct').\n"
        "- Usa conectores: 'por lo que', 'porque', 'frente a', 'aunque', 'además'.\n"
        "- No inventes: si un dato no está, omítelo.\n"
        "Requisitos de contenido (si existe):\n"
        "- Proyecto y objetivo.\n"
        "- Alternativa recomendada (EXACTAMENTE computed.winner.name) y por qué (menor costo por unidad de ventaja), incorporando costo/unidad, ventaja total y costo en texto natural.\n"
        "- Comparación frente a la segunda alternativa usando computed.delta_ratio y computed.delta_pct (si existen).\n"
        "- Ventaja principal del ganador (computed.winner_main_advantage) y la principal desventaja (computed.winner_disadvantage) si existe.\n"
        "Longitud: 1 oración, máximo 420 caracteres.\n\n"
        f"JSON:\n{json.dumps(user_payload, ensure_ascii=False)}"
    )

    return system_prompt, user_prompt


def _format_soles(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        # Mantener formato simple estilo ES: coma decimal, sin separador de miles
        raw = f"{float(value):.{decimals}f}"
        return f"S/ {raw.replace('.', ',')}"
    except Exception:
        return "-"


def _format_decimal_es(value: float | None, *, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        raw = f"{float(value):.{decimals}f}"
        return raw.replace(".", ",")
    except Exception:
        return "-"


def _build_decision_assistant_fallback(*, setup: dict | None, dashboard: list[dict]) -> str:
    """Resumen determinista y corto para el dashboard (sin inventar datos)."""

    # Rehacemos el cálculo mínimo necesario sin depender del prompt (mismas reglas)
    normalized: list[dict] = []
    for it in dashboard or []:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        cost = it.get("cost")
        total = it.get("total")
        ratio = it.get("ratio")

        try:
            cost_v = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_v = None

        try:
            total_v = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_v = 0

        ratio_v = None
        if ratio is not None:
            try:
                ratio_v = float(ratio)
            except (TypeError, ValueError):
                ratio_v = None
        if ratio_v is None and cost_v not in (None, 0.0) and total_v:
            ratio_v = float(cost_v) / float(total_v)

        normalized.append({"name": name, "cost": cost_v, "total": total_v, "ratio": ratio_v})

    valid = [p for p in normalized if p.get("ratio") is not None]
    valid.sort(key=lambda p: p["ratio"])
    winner = valid[0] if valid else None
    second = valid[1] if len(valid) > 1 else None

    project_name = (setup.get("project_name") if isinstance(setup, dict) else None) or None
    objective = (setup.get("objective") if isinstance(setup, dict) else None) or None

    parts: list[str] = []
    if project_name:
        parts.append(f"Para el proyecto \"{project_name}\"")
    else:
        parts.append("Para este proyecto")

    if objective:
        parts.append(f"cuyo objetivo es \"{objective}\" ")

    if not winner:
        parts.append(
            "se requiere completar costos y/o ventajas para emitir una recomendación basada en costo por unidad de ventaja."
        )
        paragraph = " ".join(parts).strip()
        return " ".join(paragraph.split())

    winner_name = winner.get("name")
    winner_total = winner.get("total")
    winner_cost = winner.get("cost")
    winner_ratio = winner.get("ratio")

    rec_tail = ""
    if objective:
        rec_tail = " para cumplirlo"

    parts.append(
        f"se recomienda {winner_name}{rec_tail} porque presenta el menor costo por unidad de ventaja ({_format_soles(winner_ratio, decimals=2)} por unidad), alcanzando {winner_total} de ventaja total con un costo total de {_format_soles(winner_cost)}"
    )

    if second and second.get("ratio") is not None and winner_ratio is not None:
        try:
            delta_ratio = float(second["ratio"]) - float(winner_ratio)
            delta_pct = (delta_ratio / float(second["ratio"])) * 100.0 if float(second["ratio"]) > 0 else None
        except Exception:
            delta_ratio = None
            delta_pct = None

        if delta_ratio is not None:
            pct_part = f" ({delta_pct:.2f}% menos)" if delta_pct is not None else ""
            second_ratio = None
            try:
                second_ratio = float(second.get("ratio"))
            except Exception:
                second_ratio = None

            ratio_clause = ""
            if second_ratio is not None:
                ratio_clause = f" (frente a {second.get('name')}, {_format_soles(second_ratio, decimals=2)} por unidad)"

            parts.append(
                f", lo que implica un ahorro de {_format_soles(delta_ratio, decimals=2)}{pct_part} por unidad{ratio_clause}"
            )

    # Ventaja principal y desventaja: reutilizamos la lógica DB ya existente, con un cálculo mínimo aquí
    try:
        # Reuso del bloque ya implementado en _build_cba_decision_prompts (consultas) vía Alternative/advantages
        names = [x["name"] for x in normalized if x.get("name")]
        alt_by_name = {
            alt.name: alt
            for alt in (
                Alternative.objects.filter(name__in=names)
                .prefetch_related("advantages__criterion")
                .all()
            )
        }
        win_alt = alt_by_name.get(winner_name)
        if win_alt:
            win_advs = list(win_alt.advantages.all())
            main = next((a for a in win_advs if getattr(a, "is_main", False)), None)
            if main is None and win_advs:
                main = max(win_advs, key=lambda a: getattr(a, "importance", 0) or 0)
            if main is not None:
                crit = getattr(getattr(main, "criterion", None), "name", None)
                pts = getattr(main, "importance", None)
                desc = getattr(main, "description", None)
                if desc:
                    extra = []
                    if crit:
                        extra.append(f"Criterio: {crit}")
                    if pts is not None:
                        extra.append(f"{pts} pts")
                    suffix = f" ({' · '.join(extra)})" if extra else ""
                    suffix_txt = ""
                    if suffix:
                        suffix_txt = suffix.strip()
                    parts.append(f"; su ventaja principal es \"{desc}\"{suffix_txt}")

            # Desventaja principal por criterio (si existe)
            crit_ids = set(a.criterion_id for a in win_advs)
            deficits = []
            for crit_id in crit_ids:
                win_adv = next((a for a in win_advs if a.criterion_id == crit_id), None)
                win_pts = getattr(win_adv, "importance", 0) if win_adv else 0

                best_other_alt = None
                best_other_pts = None
                for alt_name, alt in alt_by_name.items():
                    if alt_name == win_alt.name:
                        continue
                    adv = next((a for a in alt.advantages.all() if a.criterion_id == crit_id), None)
                    pts = getattr(adv, "importance", 0) if adv else 0
                    if best_other_pts is None or pts > best_other_pts:
                        best_other_pts = pts
                        best_other_alt = alt

                if best_other_pts is None:
                    continue
                deficit = (best_other_pts or 0) - (win_pts or 0)
                if deficit > 0:
                    crit_name = getattr(getattr(win_adv, "criterion", None), "name", None)
                    deficits.append({"criterion": crit_name, "deficit": deficit, "best_other": getattr(best_other_alt, "name", None)})

            if deficits:
                deficits.sort(key=lambda d: d.get("deficit") or 0, reverse=True)
                d0 = deficits[0]
                if d0.get("criterion") and d0.get("best_other"):
                    parts.append(
                        f", y aunque en {d0['criterion']} queda {d0['deficit']} punto(s) por debajo de {d0['best_other']}, mantiene la mejor eficiencia costo/unidad"
                    )
    except Exception:
        pass

    paragraph = " ".join(parts).strip().rstrip(".;,") + "."
    return " ".join(paragraph.split())


def generate_decision_assistant_text(*, setup: dict | None, dashboard: list[dict], request_origin: str | None = None) -> str:
    """Genera el texto del asistente de decisión (resumen IA) reutilizable en otras vistas."""

    fallback = _build_decision_assistant_fallback(setup=setup, dashboard=dashboard)
    system_prompt, user_prompt = _build_cba_decision_prompts(setup=setup, dashboard=dashboard)

    try:
        content = _openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            request_origin=request_origin,
        )
    except Exception:
        return fallback

    # Guardrail anti-verbosidad / anti-invención + normalización a 1 párrafo
    if not isinstance(content, str):
        return fallback
    content_stripped = " ".join(content.strip().split())
    if len(content_stripped) > 900:
        return fallback
    if "- " in content_stripped:
        return fallback

    lowered = content_stripped.lower()
    # Si el modelo responde como "campos" en vez de una oración conectada, caemos al fallback.
    if "recomendación:" in lowered or "ratio:" in lowered or "total:" in lowered or "costo:" in lowered or "delta_pct" in lowered:
        return fallback

    if "recomend" not in lowered:
        return fallback

    return content_stripped


@login_required
@require_POST
def cba_ai_decision_assistant(request):
    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "JSON inválido."}, status=400)

    setup = body.get("setup") if isinstance(body.get("setup"), dict) else None
    dashboard = body.get("dashboard")
    if not isinstance(dashboard, list) or not dashboard:
        return JsonResponse({"ok": False, "error": "No hay datos del dashboard."}, status=400)

    items: list[dict] = []
    for it in dashboard:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "cost": it.get("cost"),
                "total": it.get("total"),
                "ratio": it.get("ratio"),
            }
        )

    if not items:
        return JsonResponse({"ok": False, "error": "Datos insuficientes."}, status=400)

    try:
        origin = request.build_absolute_uri("/")
        content = generate_decision_assistant_text(
            setup=setup,
            dashboard=items,
            request_origin=origin,
        )
    except RuntimeError as e:
        msg = str(e)
        if "OPENROUTER_API_KEY" in msg:
            msg = "Falta configurar OPENROUTER_API_KEY en el servidor."
        status = 500
        lowered = msg.lower()
        if "rate-limit" in lowered or "rate limit" in lowered or "saturado" in lowered:
            status = 429
        return JsonResponse({"ok": False, "error": msg}, status=status)

    return JsonResponse({"ok": True, "content": content})

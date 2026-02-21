from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import ai

urlpatterns = [
    path("powerbi/feed/results/", views.powerbi_feed_results, name="powerbi_feed_results"),
    path(
        "powerbi/feed/dashboard/",
        views.powerbi_feed_dashboard_rows,
        name="powerbi_feed_dashboard_rows",
    ),
    path(
        "powerbi/feed/grafica-costo-ventaja/",
        views.powerbi_feed_grafica_costo_ventaja,
        name="powerbi_feed_grafica_costo_ventaja",
    ),
    path(
        "cuentas/ingresar/",
        auth_views.LoginView.as_view(
            template_name="registration/login.html",
            redirect_authenticated_user=True,
        ),
        name="cba_login",
    ),
    path("cuentas/registrar/", views.cba_signup, name="cba_signup"),
    path("cuentas/perfil/", views.cba_profile, name="cba_profile"),
    path("cuentas/salir/", views.cba_logout, name="cba_logout"),
    path("", views.cba_home, name="cba_home"),
    path("ai/decision/", ai.cba_ai_decision_assistant, name="cba_ai_decision_assistant"),
    path(
        "ai/suggest-scores/",
        ai.cba_ai_suggest_scores,
        name="cba_ai_suggest_scores",
    ),
    path(
        "ai/inconsistencies/",
        ai.cba_ai_inconsistency_audit,
        name="cba_ai_inconsistency_audit",
    ),
    path(
        "ai/audit/alternatives/",
        ai.cba_ai_alternatives_audit,
        name="cba_ai_alternatives_audit",
    ),
    path(
        "ai/audit/criteria/",
        ai.cba_ai_criteria_audit,
        name="cba_ai_criteria_audit",
    ),
    path(
        "ai/audit/criteria-type/",
        ai.cba_ai_criteria_type_audit,
        name="cba_ai_criteria_type_audit",
    ),
    path(
        "ai/audit/scores/",
        ai.cba_ai_scores_audit,
        name="cba_ai_scores_audit",
    ),
    path(
        "ai/audit/costs/",
        ai.cba_ai_costs_audit,
        name="cba_ai_costs_audit",
    ),
    path("seleccionar/", views.cba_step1, name="cba_step1"),
    path("paso2/", views.cba_step2, name="cba_step2"),
    path("paso3/", views.cba_step3, name="cba_step3"),
    path("paso4/", views.cba_step4, name="cba_step4"),
    path("paso5/", views.cba_step5, name="cba_step5"),
    path("paso6/", views.cba_step6, name="cba_step6"),
    path("paso7/", views.cba_step7, name="cba_step7"),
    path("paso8/", views.cba_step8, name="cba_step8"),
    path("paso9/", views.cba_step9, name="cba_step9"),
    path("paso10/", views.cba_step10, name="cba_step10"),
    path("paso10/dashboard/", views.cba_dashboard, name="cba_dashboard"),
    path("resultados/", views.cba_saved_results, name="cba_saved_results"),
    path(
        "resultados/<int:result_id>/",
        views.cba_saved_result_detail,
        name="cba_saved_result_detail",
    ),
    path(
        "resultados/<int:result_id>/public/",
        views.cba_saved_result_public,
        name="cba_saved_result_public",
    ),
    path(
        "resultados/<int:result_id>/public.json",
        views.cba_saved_result_public_json,
        name="cba_saved_result_public_json",
    ),
    path(
        "resultados/<int:result_id>/powerbi/",
        views.cba_saved_result_powerbi,
        name="cba_saved_result_powerbi",
    ),
    path(
        "resultados/<int:result_id>/powerbi/config/",
        views.cba_saved_result_powerbi_config,
        name="cba_saved_result_powerbi_config",
    ),
    path(
        "resultados/<int:result_id>/eliminar/",
        views.cba_saved_result_delete,
        name="cba_saved_result_delete",
    ),
    path("guia/", views.cba_guide, name="cba_guide"),
    path("guia/pdf/", views.cba_guide_pdf, name="cba_guide_pdf"),
    path("guia/descargar/", views.cba_guide_download, name="cba_guide_download"),
    path("guia/compartir/", views.cba_guide_share_create, name="cba_guide_share_create"),
    path(
        "guia/compartida/<str:token>/",
        views.cba_guide_shared,
        name="cba_guide_shared",
    ),
    path(
        "guia/compartida/<str:token>/pdf/",
        views.cba_guide_shared_pdf,
        name="cba_guide_shared_pdf",
    ),
    path(
        "guia/compartida/<str:token>/descargar/",
        views.cba_guide_shared_download,
        name="cba_guide_shared_download",
    ),
]

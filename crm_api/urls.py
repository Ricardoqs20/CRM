from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework.routers import DefaultRouter
from core.views import (
    PersonViewSet, StageViewSet, PropertyLeadViewSet, PropertyViewSet,
    kanban_view, lead_detail_drawer_view, lead_add_note_view,
    lead_move_stage_view, lead_quick_create_view, lead_toggle_property_link,
    property_list_view, property_detail_view,
    property_create_view, property_edit_view, property_quick_create_view,
    visit_schedule_view, visit_schedule_create_view, visit_create_from_lead_view,
    visit_feedback_form_view, visit_feedback_save_view, visit_key_action_view,
    visit_whatsapp_confirm_view,
    task_list_view, task_create_view, task_toggle_complete_view,
    dashboard_view,
    lead_webhook_ingest_view, portal_xml_feed_view,
    whatsapp_template_list_view, whatsapp_template_save_view,
    whatsapp_template_delete_view, lead_template_message_view,
    contact_list_view, contact_create_view, contact_edit_view, contact_detail_view,
    crm_login_view, crm_logout_view, google_login_start, google_login_callback
)

router = DefaultRouter()
router.register(r'persons', PersonViewSet)
router.register(r'stages', StageViewSet)
router.register(r'leads', PropertyLeadViewSet)
router.register(r'properties', PropertyViewSet)

urlpatterns = [
    # Autenticação frontend
    path('login/', crm_login_view, name='crm_login'),
    path('logout/', crm_logout_view, name='crm_logout'),
    path('login/google/', google_login_start, name='google_login'),
    path('login/google/callback/', google_login_callback, name='google_callback'),

    # Dashboard BI & Indicadores
    path('dashboard/', dashboard_view, name='dashboard'),

    # Funil Kanban & Lead 360°
    path('', kanban_view, name='kanban'),
    path('leads/quick/', lead_quick_create_view, name='lead_quick_create'),
    path('leads/<int:pk>/drawer/', lead_detail_drawer_view, name='lead_detail_drawer'),
    path('leads/<int:pk>/add_note/', lead_add_note_view, name='lead_add_note'),
    path('leads/<int:pk>/move_stage/', lead_move_stage_view, name='lead_move_stage'),
    path('leads/<int:lead_id>/toggle_property/<int:property_id>/', lead_toggle_property_link, name='lead_toggle_property_link'),
    path('leads/<int:lead_id>/template/<int:template_id>/', lead_template_message_view, name='lead_template_message'),
    
    # Módulo de Imóveis & Estoque
    path('properties/', property_list_view, name='property_list'),
    path('properties/new/', property_create_view, name='property_create'),
    path('properties/quick/', property_quick_create_view, name='property_quick_create'),
    path('properties/<int:pk>/', property_detail_view, name='property_detail'),
    path('properties/<int:pk>/edit/', property_edit_view, name='property_edit'),

    # Módulo de Visitas & Controle de Chaves
    path('visits/', visit_schedule_view, name='visit_schedule'),
    path('visits/schedule/', visit_schedule_create_view, name='visit_schedule_create'),
    path('visits/create/<int:lead_id>/', visit_create_from_lead_view, name='visit_create_from_lead'),
    path('visits/<int:visit_id>/feedback/form/', visit_feedback_form_view, name='visit_feedback_form'),
    path('visits/<int:visit_id>/feedback/save/', visit_feedback_save_view, name='visit_feedback_save'),
    path('visits/<int:visit_id>/key_action/', visit_key_action_view, name='visit_key_action'),
    path('visits/<int:visit_id>/whatsapp/', visit_whatsapp_confirm_view, name='visit_whatsapp_confirm'),

    # Módulo de Tarefas & Follow-ups
    path('tasks/', task_list_view, name='task_list'),
    path('tasks/create/', task_create_view, name='task_create'),
    path('tasks/<int:task_id>/toggle/', task_toggle_complete_view, name='task_toggle_complete'),


    # Módulo de Contatos (Frontend)
    path('contatos/', contact_list_view, name='contact_list'),
    path('contatos/novo/', contact_create_view, name='contact_create'),
    path('contatos/<int:pk>/', contact_detail_view, name='contact_detail'),
    path('contatos/<int:pk>/editar/', contact_edit_view, name='contact_edit'),

    # Módulo de Templates de WhatsApp
    path('templates/', whatsapp_template_list_view, name='whatsapp_template_list'),
    path('templates/save/', whatsapp_template_save_view, name='whatsapp_template_save'),
    path('templates/<int:template_id>/delete/', whatsapp_template_delete_view, name='whatsapp_template_delete'),

    # Integrações Externas: Webhooks & Feed XML
    path('api/webhooks/leads/', lead_webhook_ingest_view, name='webhook_lead_ingest'),
    path('integrations/feed-portais.xml', portal_xml_feed_view, name='portal_xml_feed'),

    # Admin & REST API
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0] if settings.STATICFILES_DIRS else settings.STATIC_ROOT)
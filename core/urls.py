from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/meta/', views.update_monthly_goal_view, name='update_monthly_goal'),
    path('privacidade/', views.privacy_policy_view, name='privacy_policy'),
    path('contatos/<int:person_id>/preferencias/', views.client_preferences_save_view, name='client_preferences_save'),
    path('contatos/<int:pk>/anonimizar/', views.contact_anonymize_lgpd_view, name='contact_anonymize_lgpd'),
    path('contatos/<int:pk>/exportar-lgpd/', views.contact_export_lgpd_view, name='contact_export_lgpd'),
    path('leads/<int:pk>/anonimizar/', views.lead_anonymize_lgpd_view, name='lead_anonymize_lgpd'),
]

from django.urls import path
from . import views

urlpatterns = [
    path('contatos/<int:person_id>/preferencias/', views.client_preferences_save_view, name='client_preferences_save'),
]

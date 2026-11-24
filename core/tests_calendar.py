from unittest.mock import patch, MagicMock
from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from core.models import (
    Person, Property, PropertyLead, Pipeline, Stage,
    PropertyVisit, CalendarIntegration
)


class CalendarSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='corretor_test', email='test@example.com', password='123')
        self.client_obj = Person.objects.create(name='Cliente Teste', phone='(98) 98111-2222', email='cliente@teste.com')
        self.property_obj = Property.objects.create(
            code='APT-100',
            title='Apartamento Luxo',
            neighborhood='Renascença',
            city='São Luís',
            state='MA',
            sale_price=800000.00
        )
        self.pipeline = Pipeline.objects.create(name='Padrão')
        self.stage = Stage.objects.create(pipeline=self.pipeline, name='Visita Agendada', order=1)
        self.lead = PropertyLead.objects.create(
            title='Lead Teste',
            client=self.client_obj,
            pipeline=self.pipeline,
            stage=self.stage,
            agent=self.user
        )

        # Configura integração do usuário
        self.integration = CalendarIntegration.objects.create(
            user=self.user,
            google_connected=True,
            google_access_token='fake_access_token',
            google_refresh_token='fake_refresh_token'
        )

    @patch('core.calendar_sync.get_calendar_service_for_user')
    def test_post_save_signal_creates_google_event(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = (mock_service, self.integration)
        mock_events = MagicMock()
        mock_service.events.return_value = mock_events
        mock_insert = MagicMock()
        mock_events.insert.return_value = mock_insert
        mock_insert.execute.return_value = {'id': 'google_evt_12345'}

        # Criar visita dispara o post_save signal
        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.property_obj,
            agent=self.user,
            scheduled_date=timezone.now() + timedelta(days=1),
            status='confirmed'
        )

        # Recarrega visita do banco e confirma id do evento
        visit.refresh_from_db()
        self.assertEqual(visit.google_event_id, 'google_evt_12345')
        mock_events.insert.assert_called_once()

    @patch('core.calendar_sync.get_calendar_service_for_user')
    def test_post_save_signal_updates_google_event(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = (mock_service, self.integration)
        mock_events = MagicMock()
        mock_service.events.return_value = mock_events
        mock_patch = MagicMock()
        mock_events.patch.return_value = mock_patch
        mock_patch.execute.return_value = {'id': 'google_evt_existing'}

        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.property_obj,
            agent=self.user,
            scheduled_date=timezone.now() + timedelta(days=1),
            status='confirmed',
            google_event_id='google_evt_existing'
        )

        visit.meeting_point = 'Portaria Torre B'
        visit.save()

        mock_events.patch.assert_called()

    @patch('core.calendar_sync.get_calendar_service_for_user')
    def test_post_delete_signal_removes_google_event(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = (mock_service, self.integration)
        mock_events = MagicMock()
        mock_service.events.return_value = mock_events
        mock_delete = MagicMock()
        mock_events.delete.return_value = mock_delete
        mock_delete.execute.return_value = {}

        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.property_obj,
            agent=self.user,
            scheduled_date=timezone.now() + timedelta(days=1),
            status='confirmed',
            google_event_id='evt_to_delete_999'
        )

        visit.delete()
        mock_events.delete.assert_called_with(calendarId='primary', eventId='evt_to_delete_999')

    @patch('core.calendar_sync.get_calendar_service_for_user')
    def test_ical_feed_endpoint(self, mock_get_service):
        mock_get_service.return_value = (None, None)
        # Cria visita para o usuário
        PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.property_obj,
            agent=self.user,
            scheduled_date=timezone.now() + timedelta(days=1),
            status='confirmed'
        )

        client = Client()
        url = f'/visits/feed/{self.integration.ical_token}.ics'
        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        content = response.content.decode('utf-8')
        self.assertIn('BEGIN:VCALENDAR', content)
        self.assertIn('APT-100', content)
        self.assertIn('Cliente Teste', content)
        self.assertIn('END:VCALENDAR', content)

    def test_visit_schedule_view_renders_calendar_sync_bar(self):
        client = Client()
        client.force_login(self.user)
        response = client.get('/visits/')

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('Sincronização Automática de Agenda', content)
        self.assertIn('Google Agenda Conectado ✓', content)
        self.assertIn('Sincronizar com Apple Calendário', content)

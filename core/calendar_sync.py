import logging
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from core.models import CalendarIntegration, PropertyVisit

logger = logging.getLogger(__name__)

CALENDAR_SCOPES = ['https://www.googleapis.com/auth/calendar.events']


def get_calendar_service_for_user(user):
    """
    Constrói e retorna o client do Google Calendar API para o usuário se a integração estiver ativa.
    Atualiza automaticamente o access token expirado usando o refresh_token.
    """
    if not user or not user.is_authenticated:
        return None, None

    try:
        integration = CalendarIntegration.objects.filter(user=user, google_connected=True).first()
        if not integration or not integration.google_refresh_token:
            return None, None

        creds = Credentials(
            token=integration.google_access_token,
            refresh_token=integration.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=getattr(settings, 'GOOGLE_CLIENT_ID', ''),
            client_secret=getattr(settings, 'GOOGLE_CLIENT_SECRET', ''),
            scopes=CALENDAR_SCOPES
        )

        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        return service, integration
    except Exception as e:
        logger.error(f"Erro ao obter serviço Google Calendar para usuário {user}: {e}", exc_info=True)
        return None, None


def format_visit_event_body(visit):
    """Monta o payload do evento no padrão da Google Calendar API"""
    prop = visit.visit_property
    lead = visit.lead
    client_name = lead.client.name if lead and lead.client else "Cliente"
    client_phone = lead.client.phone if lead and lead.client else ""

    summary = f"Visita Imóvel {prop.code} - {client_name}"
    
    address_parts = [prop.street, prop.number, prop.neighborhood, prop.city, prop.state]
    location = ", ".join([p for p in address_parts if p]) or (prop.neighborhood or "Imóvel")
    
    start_dt = visit.scheduled_date
    end_dt = start_dt + timedelta(hours=1)

    description = (
        f"Visita agendada pelo ImobiCRM\n"
        f"-----------------------------\n"
        f"Imóvel: [{prop.code}] {prop.title}\n"
        f"Endereço: {location}\n"
        f"Ponto de Encontro: {visit.meeting_point or 'No imóvel'}\n"
        f"Cliente: {client_name}\n"
        f"Telefone: {client_phone}\n"
        f"Status da Visita: {visit.get_status_display()}\n"
        f"Controle de Chaves: {visit.get_key_status_display()}\n"
    )

    return {
        'summary': summary,
        'location': location,
        'description': description,
        'start': {
            'dateTime': start_dt.isoformat(),
        },
        'end': {
            'dateTime': end_dt.isoformat(),
        },
    }


def sync_visit_to_google(visit_id):
    """
    Sincroniza uma visita específica com o Google Calendar do corretor responsável.
    Insere, atualiza (patch) ou remove (delete) conforme o status e google_event_id.
    """
    try:
        visit = PropertyVisit.objects.select_related('agent', 'visit_property', 'lead', 'lead__client').filter(pk=visit_id).first()
        if not visit or not visit.agent:
            return None

        service, integration = get_calendar_service_for_user(visit.agent)
        if not service or not integration:
            return None

        calendar_id = integration.google_calendar_id or 'primary'

        # Se visita foi cancelada e possui evento no Google, removemos o evento
        if visit.status == 'canceled':
            if visit.google_event_id:
                try:
                    service.events().delete(calendarId=calendar_id, eventId=visit.google_event_id).execute()
                    visit.google_event_id = None
                    PropertyVisit.objects.filter(pk=visit.pk).update(google_event_id=None)
                except Exception as e:
                    logger.warning(f"Erro ao remover evento cancelado no Google Calendar ({visit.google_event_id}): {e}")
            return None

        event_body = format_visit_event_body(visit)

        if visit.google_event_id:
            try:
                updated_event = service.events().patch(
                    calendarId=calendar_id,
                    eventId=visit.google_event_id,
                    body=event_body
                ).execute()
                return updated_event.get('id')
            except Exception as e:
                logger.warning(f"Erro ao atualizar evento no Google Calendar, tentando recriar: {e}")
                # Se falhar (ex: 404 evento deletado manualmente no Google), recria abaixo
                visit.google_event_id = None

        if not visit.google_event_id:
            created_event = service.events().insert(
                calendarId=calendar_id,
                body=event_body
            ).execute()
            new_id = created_event.get('id')
            if new_id:
                visit.google_event_id = new_id
                PropertyVisit.objects.filter(pk=visit.pk).update(google_event_id=new_id)
            return new_id

    except Exception as e:
        logger.error(f"Erro ao sincronizar visita {visit_id} com Google Calendar: {e}", exc_info=True)
        return None


def delete_visit_from_google(agent, google_event_id, calendar_id='primary'):
    """Remove o evento do Google Calendar quando uma visita é excluída do CRM"""
    if not agent or not google_event_id:
        return

    try:
        service, integration = get_calendar_service_for_user(agent)
        if not service or not integration:
            return
        cal_id = integration.google_calendar_id or calendar_id or 'primary'
        service.events().delete(calendarId=cal_id, eventId=google_event_id).execute()
    except Exception as e:
        logger.warning(f"Falha ao deletar evento {google_event_id} do Google Calendar: {e}")


def initial_sync_future_visits(user):
    """
    Executa a carga inicial enviando todas as visitas futuras do usuário para o Google Calendar.
    Retorna o total de visitas sincronizadas.
    """
    service, integration = get_calendar_service_for_user(user)
    if not service or not integration:
        return 0

    now = timezone.now()
    future_visits = PropertyVisit.objects.filter(
        agent=user,
        scheduled_date__gte=now
    ).exclude(status='canceled').select_related('visit_property', 'lead', 'lead__client')

    synced_count = 0
    for visit in future_visits:
        res = sync_visit_to_google(visit.id)
        if res:
            synced_count += 1

    return synced_count

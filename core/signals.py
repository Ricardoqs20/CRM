import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from core.models import PropertyVisit
from core.calendar_sync import sync_visit_to_google, delete_visit_from_google

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PropertyVisit)
def property_visit_saved_handler(sender, instance, created, **kwargs):
    """
    Sempre que uma visita for criada ou atualizada no CRM (tela de visitas, kanban ou admin),
    sincroniza automaticamente em tempo real com o Google Agenda do visit.agent.
    """
    # Evita recursão caso update() tenha sido usado
    if kwargs.get('raw', False):
        return

    try:
        if instance.agent:
            sync_visit_to_google(instance.id)
    except Exception as e:
        logger.error(f"Erro no signal post_save de PropertyVisit #{instance.id}: {e}", exc_info=True)


@receiver(post_delete, sender=PropertyVisit)
def property_visit_deleted_handler(sender, instance, **kwargs):
    """
    Quando uma visita for excluída do CRM, remove o evento correspondente do Google Calendar.
    """
    try:
        if instance.agent and instance.google_event_id:
            delete_visit_from_google(instance.agent, instance.google_event_id)
    except Exception as e:
        logger.error(f"Erro no signal post_delete de PropertyVisit #{instance.id}: {e}", exc_info=True)

import logging
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_login_failed
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from core.calendar_sync import delete_visit_from_google, sync_visit_to_google
from core.models import PropertyVisit, UserProfile
from core.security import log_security_event

logger = logging.getLogger(__name__)


@receiver(post_save, sender=PropertyVisit)
def property_visit_saved_handler(sender, instance, created, **kwargs):
    """
    Sempre que uma visita for criada ou atualizada no CRM (tela de visitas, kanban ou admin),
    sincroniza automaticamente em tempo real com o Google Agenda do visit.agent.
    """
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


# ==========================================
# AUDITORIA DE SEGURANÇA & PERMISSÕES (LGPD)
# ==========================================

@receiver(user_login_failed)
def audit_user_login_failed_handler(sender, credentials, request=None, **kwargs):
    """Registra falhas de autenticação no SecurityAuditLog (login frontend e Django Admin)."""
    username = ''
    if isinstance(credentials, dict):
        username = str(credentials.get('username') or credentials.get('email') or '')[:150]
    log_security_event(
        action='login_failed',
        target=username or 'credencial_desconhecida',
        details='Tentativa de autenticação com credenciais inválidas.',
        request=request,
    )


@receiver(pre_save, sender=UserProfile)
def audit_user_profile_role_change(sender, instance, **kwargs):
    """Detecta e audita alterações de papel/permissão no perfil do usuário (ex: agent -> manager)."""
    if kwargs.get('raw', False) or not instance.pk:
        return
    try:
        old = UserProfile.objects.filter(pk=instance.pk).values('role').first()
        if old and old['role'] != instance.role:
            log_security_event(
                action='permission_changed',
                target=f'User #{instance.user_id} ({instance.user.username})',
                details=f"Papel UserProfile alterado de '{old['role']}' para '{instance.role}'.",
            )
    except Exception as exc:
        logger.warning('Falha ao auditar mudança de papel em UserProfile: %s', exc)


@receiver(pre_save, sender=User)
def audit_user_privilege_change(sender, instance, **kwargs):
    """Detecta alterações em flags sensíveis de privilégio (is_superuser, is_staff, is_active)."""
    if kwargs.get('raw', False) or not instance.pk:
        return
    try:
        old = User.objects.filter(pk=instance.pk).values('is_superuser', 'is_staff', 'is_active').first()
        if not old:
            return
        changes = []
        for flag in ('is_superuser', 'is_staff', 'is_active'):
            new_val = getattr(instance, flag)
            if old[flag] != new_val:
                changes.append(f'{flag}: {old[flag]} -> {new_val}')
        if changes:
            log_security_event(
                action='permission_changed',
                target=f'User #{instance.pk} ({instance.username})',
                details='Flags de privilégio alteradas: ' + ', '.join(changes),
            )
    except Exception as exc:
        logger.warning('Falha ao auditar mudança de privilégio em User: %s', exc)


@receiver(m2m_changed, sender=User.groups.through)
def audit_user_groups_changed(sender, instance, action, pk_set, **kwargs):
    """Audita adição/remoção de grupos de permissões em usuários."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        log_security_event(
            action='permission_changed',
            target=f'User #{instance.pk} ({instance.username})',
            details=f'Grupos alterados ({action}): IDs={sorted(pk_set) if pk_set else "all"}.',
        )


@receiver(m2m_changed, sender=User.user_permissions.through)
def audit_user_permissions_changed(sender, instance, action, pk_set, **kwargs):
    """Audita adição/remoção direta de permissões em usuários."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        log_security_event(
            action='permission_changed',
            target=f'User #{instance.pk} ({instance.username})',
            details=f'Permissões diretas alteradas ({action}): IDs={sorted(pk_set) if pk_set else "all"}.',
        )

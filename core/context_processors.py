from core.security import is_user_manager


def user_role_context(request):
    """Expõe flags de permissão de gestão/administração para todos os templates."""
    user = getattr(request, 'user', None)
    return {
        'is_user_manager': is_user_manager(user),
    }

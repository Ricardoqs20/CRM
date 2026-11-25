import functools
import ipaddress
import json
import logging
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from core.models import (
    Person,
    Property,
    PropertyLead,
    SecurityAuditLog,
    UserProfile,
)

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str:
    """Extrai o IP do cliente considerando proxy reverso (X-Forwarded-For)."""
    if request is None:
        return ''
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '127.0.0.1').strip()


def anonymize_ip(ip_str: str) -> str:
    """
    Anonimiza endereços IPv4 (/24) e IPv6 (/48) em conformidade com a LGPD,
    evitando armazenamento de identificadores de rede completos nos logs.
    """
    if not ip_str:
        return ''
    cleaned = str(ip_str).strip()
    try:
        addr = ipaddress.ip_address(cleaned)
        if addr.version == 4:
            net = ipaddress.ip_network(f'{cleaned}/24', strict=False)
            return str(net.network_address)
        net = ipaddress.ip_network(f'{cleaned}/48', strict=False)
        return str(net.network_address)
    except ValueError:
        return '0.0.0.0'


def log_security_event(
    action: str,
    target: str = '',
    details: str = '',
    user=None,
    request=None,
    ip_address: str = '',
):
    """Registra evento crítico em SecurityAuditLog com IP anonimizado."""
    try:
        resolved_user = user
        if resolved_user is None and request is not None:
            req_user = getattr(request, 'user', None)
            if req_user is not None and getattr(req_user, 'is_authenticated', False):
                resolved_user = req_user

        raw_ip = ip_address or (get_client_ip(request) if request is not None else '')
        anon_ip = anonymize_ip(raw_ip)

        return SecurityAuditLog.objects.create(
            user=resolved_user,
            action=action,
            target=str(target or '')[:255],
            ip_address=anon_ip,
            details=str(details or ''),
        )
    except Exception as exc:
        logger.error('Falha ao gravar SecurityAuditLog (%s): %s', action, exc, exc_info=True)
        return None


def anonymize_person_lgpd(person: Person, actor=None, request=None, ip_address: str = '') -> Person:
    """
    Anonimiza dados pessoais do titular (Person) atendendo ao Art. 18 da LGPD,
    preservando integralmente os registros históricos e métricas financeiras (VGV, funil, conversões).
    """
    old_name = person.name or ''
    anonymized_label = f'Titular Anonimizado #{person.id}'

    person.name = anonymized_label
    person.phone = ''
    person.secondary_phone = None
    person.email = None
    person.document = None
    person.notes = None
    person.lgpd_consent = False
    person.lgpd_origin = 'anonimizado_lgpd'
    person.save()

    # Limpa observações comportamentais mantendo estrutura estatística de preferências
    prefs = getattr(person, 'preferences', None)
    if prefs is not None and prefs.notes:
        prefs.notes = None
        prefs.save(update_fields=['notes', 'updated_at'])

    # Higieniza notas pessoais e referências nominais nos leads vinculados sem apagar métricas de CRM
    for lead in person.leads.all():
        updated_fields = []
        if lead.notes:
            lead.notes = None
            updated_fields.append('notes')
        if old_name and old_name in (lead.title or ''):
            lead.title = lead.title.replace(old_name, anonymized_label)
            updated_fields.append('title')
        if updated_fields:
            lead.save(update_fields=updated_fields + ['updated_at'])

    log_security_event(
        action='lgpd_anonymize',
        target=f'Person #{person.id}',
        details=f'Titular anonimizado para "{anonymized_label}" preservando histórico estatístico do CRM.',
        user=actor,
        request=request,
        ip_address=ip_address,
    )
    return person


def safe_json_dumps(data) -> str:
    """Serializa JSON escapando caracteres HTML perigosos (<, >, &) contra XSS em blocos <script>."""
    raw = json.dumps(data)
    return (
        raw.replace('&', '\\u0026')
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
    )


def get_safe_redirect_url(request, candidate_url: str | None, fallback: str = '/') -> str:
    """Previne Open Redirect e XSS via javascript:/data: validando host e esquema."""
    if not candidate_url:
        return fallback
    allowed_hosts = {request.get_host()} if request is not None else set()
    if url_has_allowed_host_and_scheme(
        url=candidate_url,
        allowed_hosts=allowed_hosts,
        require_https=request.is_secure() if request is not None else False,
    ):
        return candidate_url
    return fallback


# ==========================================
# CONTROLE DE ACESSO E ANTI-IDOR (ISOLAMENTO DE CARTEIRA)
# ==========================================

def is_user_manager(user) -> bool:
    """Verifica se o usuário é Administrador (superuser) ou Gestor da imobiliária."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    return UserProfile.objects.filter(user=user, role='manager').exists()


def can_access_lead(user, lead: PropertyLead) -> bool:
    """Garante que não-gestores acessem apenas leads de sua própria carteira."""
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    return lead.agent_id == user.id


def can_access_person(user, person: Person) -> bool:
    """
    Garante que corretores não-administradores acessem apenas contatos atribuídos à sua carteira,
    clientes de leads sob sua responsabilidade ou proprietários de imóveis que captaram.
    """
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    if person.assigned_agent_id == user.id:
        return True
    if person.assigned_agent_id is not None and person.assigned_agent_id != user.id:
        return False
    if PropertyLead.objects.filter(client=person, agent=user).exists():
        return True
    if Property.objects.filter(owner=person, captured_by=user).exists():
        return True
    return False


def can_edit_property(user, prop: Property) -> bool:
    """Apenas gestores/administradores ou o corretor captador podem editar/excluir o imóvel."""
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    return prop.captured_by_id == user.id


def can_access_visit(user, visit) -> bool:
    """Apenas gestores/administradores, o corretor da visita ou o corretor do lead acessam a visita."""
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    if visit.agent_id == user.id:
        return True
    if visit.lead_id and visit.lead.agent_id == user.id:
        return True
    return False


def can_access_task(user, task) -> bool:
    """Apenas gestores/administradores, o responsável pela tarefa ou o corretor do lead acessam a tarefa."""
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    if task.assigned_to_id == user.id:
        return True
    if task.lead_id and task.lead.agent_id == user.id:
        return True
    return False


def can_edit_whatsapp_template(user, template) -> bool:
    """Modelos globais só podem ser alterados/excluídos por gestores; modelos próprios pelo criador."""
    if not user or not user.is_authenticated:
        return False
    if is_user_manager(user):
        return True
    return template.created_by_id == user.id


# ==========================================
# RATE LIMITING NA APLICAÇÃO (django.core.cache)
# ==========================================

def rate_limit(
    key_prefix: str,
    max_requests: int,
    window_seconds: int,
    methods: tuple[str, ...] | list[str] | None = None,
    scope: str = 'ip',
):
    """
    Decorator de Rate Limiting baseado em django.core.cache (LocMemCache / DatabaseCache).
    Retorna HTTP 429 Too Many Requests quando o limite por janela é excedido
    e registra excessos no SecurityAuditLog.
    """
    allowed_methods = {m.upper() for m in methods} if methods else None

    def decorator(view_func):
        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if allowed_methods and request.method.upper() not in allowed_methods:
                return view_func(request, *args, **kwargs)

            req_user = getattr(request, 'user', None)
            if scope == 'user_or_ip' and req_user is not None and getattr(req_user, 'is_authenticated', False):
                identifier = f'user:{req_user.pk}'
            else:
                identifier = f'ip:{get_client_ip(request)}'

            cache_key = f'imobicrm_rl:{key_prefix}:{identifier}'

            if cache.add(cache_key, 1, timeout=window_seconds):
                current_count = 1
            else:
                try:
                    current_count = cache.incr(cache_key)
                except ValueError:
                    cache.set(cache_key, 1, timeout=window_seconds)
                    current_count = 1

            if current_count > max_requests:
                log_security_event(
                    action='rate_limit_exceeded',
                    target=f'{key_prefix} ({request.path})',
                    details=f'Limite excedido: {current_count}/{max_requests} requisições em {window_seconds}s.',
                    request=request,
                )
                retry_headers = {'Retry-After': str(window_seconds)}
                wants_json = (
                    request.path.startswith('/api/')
                    or request.content_type == 'application/json'
                    or 'application/json' in request.headers.get('Accept', '')
                )
                if wants_json:
                    return JsonResponse(
                        {'error': 'Muitas requisições. Aguarde alguns instantes e tente novamente.'},
                        status=429,
                        headers=retry_headers,
                    )
                return HttpResponse(
                    'Muitas tentativas realizadas. Por segurança, aguarde um minuto antes de tentar novamente.',
                    status=429,
                    content_type='text/plain; charset=utf-8',
                    headers=retry_headers,
                )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator

import hmac
import json
import random
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Avg, Case, Count, Prefetch, Q, Sum, When
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import (
    Activity,
    CalendarIntegration,
    ClientPreference,
    InteractionLog,
    Person,
    Pipeline,
    Property,
    PropertyImage,
    PropertyLead,
    PropertyVisit,
    Stage,
    UserProfile,
    WhatsAppTemplate,
)
from .security import (
    anonymize_person_lgpd,
    can_access_lead,
    can_access_person,
    can_access_task,
    can_access_visit,
    can_edit_property,
    can_edit_whatsapp_template,
    get_safe_redirect_url,
    is_user_manager,
    log_security_event,
    rate_limit,
    safe_json_dumps,
)
from .serializers import (
    ActivitySerializer,
    PersonSerializer,
    PropertyLeadSerializer,
    PropertySerializer,
    PropertyVisitSerializer,
    StageSerializer,
)
from .services.lead_router import assign_lead_smart
from .services.match_engine import (
    get_matching_leads_for_property,
    get_matching_properties_for_lead,
)
from .utils import format_brl, parse_brl_decimal


# ==========================================
# REST API VIEWSETS (DRF) — COM ISOLAMENTO ANTI-IDOR
# ==========================================

class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if is_user_manager(self.request.user):
            return Person.objects.all()
        return Person.objects.filter(assigned_agent=self.request.user)

    def perform_create(self, serializer):
        extra = {}
        if not is_user_manager(self.request.user):
            extra['assigned_agent'] = self.request.user
        if serializer.validated_data.get('lgpd_consent', True) and not serializer.validated_data.get('lgpd_consent_at'):
            extra['lgpd_consent_at'] = timezone.now()
            extra['lgpd_origin'] = serializer.validated_data.get('lgpd_origin') or 'api_drf'
        serializer.save(**extra)


class StageViewSet(viewsets.ModelViewSet):
    queryset = Stage.objects.all()
    serializer_class = StageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def check_permissions(self, request):
        super().check_permissions(request)
        if request.method not in permissions.SAFE_METHODS and not is_user_manager(request.user):
            raise PermissionDenied('Apenas gestores podem alterar etapas do funil.')


class PropertyLeadViewSet(viewsets.ModelViewSet):
    queryset = PropertyLead.objects.all()
    serializer_class = PropertyLeadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if is_user_manager(self.request.user):
            return PropertyLead.objects.all()
        return PropertyLead.objects.filter(agent=self.request.user)

    def perform_create(self, serializer):
        if not is_user_manager(self.request.user):
            serializer.save(agent=self.request.user)
        else:
            serializer.save()

    @action(detail=True, methods=['patch'])
    def move_stage(self, request, pk=None):
        lead = self.get_object()
        stage_id = request.data.get('stage_id')
        try:
            stage = Stage.objects.get(id=stage_id)
            lead.stage = stage
            lead.save()
            return Response({'status': f'Movido para {stage.name}'})
        except Stage.DoesNotExist:
            return Response({'error': 'Estágio não encontrado'}, status=status.HTTP_404_NOT_FOUND)


class PropertyViewSet(viewsets.ModelViewSet):
    queryset = Property.objects.all()
    serializer_class = PropertySerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        if not is_user_manager(self.request.user):
            serializer.save(captured_by=self.request.user)
        else:
            serializer.save()

    def perform_update(self, serializer):
        if not can_edit_property(self.request.user, serializer.instance):
            raise PermissionDenied('Você só pode editar imóveis captados por sua carteira.')
        serializer.save()

    def perform_destroy(self, instance):
        if not can_edit_property(self.request.user, instance):
            raise PermissionDenied('Você só pode excluir imóveis captados por sua carteira.')
        instance.delete()


# ==========================================
# VIEWS DO FUNIL KANBAN & VISÃO 360° DO LEAD
# ==========================================

@login_required(login_url='crm_login')
@rate_limit('search_kanban', 30, 60, scope='user_or_ip')
def kanban_view(request):
    """View do Funil Kanban com Filtros Reativos HTMX"""
    user_is_mgr = is_user_manager(request.user)
    pipeline = Pipeline.objects.filter(is_default=True).first() or Pipeline.objects.first()

    q = request.GET.get('q', '').strip()
    agent_id = request.GET.get('agent_id', '').strip()
    transaction_type = request.GET.get('transaction_type', '').strip()

    lead_filter = Q()
    if q:
        lead_filter &= (Q(title__icontains=q) | Q(client__name__icontains=q) | Q(client__phone__icontains=q))
    if transaction_type:
        lead_filter &= Q(transaction_type=transaction_type)

    if not user_is_mgr:
        lead_filter &= Q(agent=request.user)
    elif agent_id:
        lead_filter &= Q(agent_id=agent_id)

    stages = Stage.objects.filter(pipeline=pipeline).prefetch_related(
        Prefetch(
            'leads',
            queryset=PropertyLead.objects.filter(lead_filter).select_related('client', 'agent')
        )
    ).order_by('order') if pipeline else []

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')

    context = {
        'pipeline': pipeline,
        'stages': stages,
        'agents': agents,
        'q': q,
        'current_agent': agent_id,
        'current_trans': transaction_type,
        'is_manager': user_is_mgr,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'kanban/partials/board.html', context)

    return render(request, 'kanban/kanban.html', context)


@login_required(login_url='crm_login')
def lead_detail_drawer_view(request, pk):
    """Carrega o conteúdo da gaveta lateral da Visão 360° do Lead com Match de Imóveis, Visitas e Tarefas"""
    lead = get_object_or_404(
        PropertyLead.objects.select_related('client', 'client__preferences', 'stage', 'agent', 'pipeline')
                            .prefetch_related(
                                'interested_properties',
                                'interactions__user',
                                'visits__visit_property',
                                'visits__agent',
                                'activities__assigned_to',
                                'activities__related_property'
                            ),
        pk=pk
    )

    user_is_mgr = is_user_manager(request.user)
    if not can_access_lead(request.user, lead):
        log_security_event(
            action='unauthorized_access',
            target=f'PropertyLead #{pk}',
            details='Tentativa bloqueada de visualização de lead de outra carteira.',
            request=request,
        )
        raise Http404("Você não tem permissão para visualizar este lead.")

    stages = Stage.objects.filter(pipeline=lead.pipeline).order_by('order')
    matching_properties = get_matching_properties_for_lead(lead)
    all_properties = Property.objects.filter(status__in=['available', 'draft', 'reserved']).order_by('code')

    tpl_qs = WhatsAppTemplate.objects.filter(is_active=True)
    if not user_is_mgr:
        tpl_qs = tpl_qs.filter(Q(created_by__isnull=True) | Q(created_by=request.user))
    active_templates = tpl_qs.order_by('category', 'title')

    return render(request, 'kanban/partials/lead_drawer.html', {
        'lead': lead,
        'stages': stages,
        'matching_properties': matching_properties,
        'all_properties': all_properties,
        'active_templates': active_templates,
        'is_manager': user_is_mgr,
    })


@login_required(login_url='crm_login')
def lead_toggle_property_link(request, lead_id, property_id):
    """Vincula ou desvincula um imóvel do lead a partir da seção de Match"""
    lead = get_object_or_404(PropertyLead, pk=lead_id)
    if not can_access_lead(request.user, lead):
        raise Http404("Lead não encontrado na sua carteira.")

    prop = get_object_or_404(Property, pk=property_id)

    if prop in lead.interested_properties.all():
        lead.interested_properties.remove(prop)
        action_msg = f"Imóvel [{prop.code}] desvinculado das opções de interesse."
    else:
        lead.interested_properties.add(prop)
        action_msg = f"Imóvel [{prop.code}] ({prop.title}) vinculado com sucesso ao perfil de interesse do cliente."

    InteractionLog.objects.create(
        lead=lead,
        user=request.user,
        action_type='note',
        content=action_msg
    )

    matching_properties = get_matching_properties_for_lead(lead)
    return render(request, 'kanban/partials/lead_matches.html', {
        'lead': lead,
        'matching_properties': matching_properties
    })


@login_required(login_url='crm_login')
def lead_add_note_view(request, pk):
    """Adiciona uma nova anotação/interação na timeline do lead e atualiza a última interação"""
    lead = get_object_or_404(PropertyLead, pk=pk)
    if not can_access_lead(request.user, lead):
        raise Http404("Lead não encontrado na sua carteira.")

    if request.method == 'POST':
        action_type = request.POST.get('action_type', 'note')
        content = request.POST.get('content', '').strip()

        if content:
            InteractionLog.objects.create(
                lead=lead,
                user=request.user,
                action_type=action_type,
                content=content
            )
            lead.last_contact_at = timezone.now()
            lead.save()

    return render(request, 'kanban/partials/timeline.html', {'lead': lead})


@login_required(login_url='crm_login')
def lead_move_stage_view(request, pk):
    """Move o lead entre os estágios do funil, salvando motivo de perda ou sucesso"""
    lead = get_object_or_404(PropertyLead, pk=pk)
    if not can_access_lead(request.user, lead):
        raise Http404("Lead não encontrado na sua carteira.")

    if request.method == 'POST':
        try:
            data = json.loads(request.body) if request.body else request.POST
        except json.JSONDecodeError:
            data = request.POST

        stage_id = data.get('stage_id')
        lost_reason = data.get('lost_reason')

        try:
            new_stage = Stage.objects.get(id=stage_id)
            old_stage_name = lead.stage.name
            lead.stage = new_stage

            if new_stage.stage_type == 'won':
                lead.status = 'won'
                lead.closed_at = timezone.now()
            elif new_stage.stage_type == 'lost':
                lead.status = 'lost'
                lead.closed_at = timezone.now()
                if lost_reason:
                    lead.lost_reason = str(lost_reason)[:255]
            else:
                lead.status = 'open'
                lead.closed_at = None

            lead.last_contact_at = timezone.now()
            lead.save()

            log_text = f"Movido de '{old_stage_name}' para '{new_stage.name}'"
            if lost_reason:
                log_text += f". Motivo da perda: {lost_reason}"

            InteractionLog.objects.create(
                lead=lead,
                user=request.user,
                action_type='stage_change',
                content=log_text
            )

            return JsonResponse({'success': True, 'stage_name': new_stage.name})
        except Stage.DoesNotExist:
            return JsonResponse({'error': 'Estágio não encontrado'}, status=404)

    return JsonResponse({'error': 'Método inválido'}, status=405)


@login_required(login_url='crm_login')
def lead_quick_create_view(request):
    """Cadastro Rápido de Novo Lead no Funil (Etapa Inicial)"""
    if request.method == 'POST':
        title = request.POST.get('title')
        client_name = request.POST.get('client_name')
        client_phone = request.POST.get('client_phone')
        transaction_type = request.POST.get('transaction_type', 'buy')
        property_type = request.POST.get('property_type', 'apartment')
        budget = parse_brl_decimal(request.POST.get('budget'))
        origin = request.POST.get('origin', 'manual')

        client, _ = Person.objects.get_or_create(
            phone=client_phone,
            defaults={
                'name': client_name,
                'client_type': 'buyer' if transaction_type == 'buy' else 'renter',
                'assigned_agent': request.user,
                'lgpd_consent': True,
                'lgpd_consent_at': timezone.now(),
                'lgpd_origin': 'kanban_rapido',
            }
        )

        pipeline = Pipeline.objects.filter(is_default=True).first() or Pipeline.objects.first()
        first_stage = Stage.objects.filter(pipeline=pipeline).order_by('order').first()

        lead = PropertyLead.objects.create(
            title=title,
            client=client,
            pipeline=pipeline,
            stage=first_stage,
            transaction_type=transaction_type,
            property_type=property_type,
            budget=budget,
            origin=origin,
            agent=request.user,
            last_contact_at=timezone.now()
        )

        InteractionLog.objects.create(
            lead=lead,
            user=request.user,
            action_type='note',
            content=f"Lead cadastrado no sistema via {lead.get_origin_display()}."
        )

        messages.success(request, f'Lead "{lead.title}" adicionado com sucesso ao funil!')
        return redirect('kanban')

    return redirect('kanban')


@login_required(login_url='crm_login')
def lead_anonymize_lgpd_view(request, pk):
    """Anonimiza os dados pessoais do titular vinculado ao Lead (restrito a administradores/gestores)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if not is_user_manager(request.user):
        log_security_event(
            action='unauthorized_access',
            target=f'LGPD Anonymize Lead #{pk}',
            details='Usuário não-administrador tentou executar anonimização LGPD.',
            request=request,
        )
        return HttpResponseForbidden('Acesso restrito a administradores/gestores.')

    lead = get_object_or_404(PropertyLead.objects.select_related('client'), pk=pk)
    anonymize_person_lgpd(lead.client, actor=request.user, request=request)
    messages.success(request, f'Titular vinculado ao negócio #{lead.id} anonimizado conforme a LGPD.')
    return redirect('kanban')


# ==========================================
# VIEWS DO ESTOQUE DE IMÓVEIS
# ==========================================

@login_required(login_url='crm_login')
@rate_limit('search_properties', 30, 60, scope='user_or_ip')
def property_list_view(request):
    """Listagem do Estoque de Imóveis com Filtros Dinâmicos HTMX"""
    queryset = Property.objects.all().prefetch_related('images')

    q = request.GET.get('q', '').strip()
    if q:
        queryset = queryset.filter(
            Q(code__icontains=q) |
            Q(title__icontains=q) |
            Q(building_name__icontains=q) |
            Q(neighborhood__icontains=q) |
            Q(street__icontains=q)
        )

    neighborhood = request.GET.get('neighborhood', '')
    if neighborhood:
        queryset = queryset.filter(neighborhood__iexact=neighborhood)

    property_type = request.GET.get('property_type', '')
    if property_type:
        queryset = queryset.filter(property_type=property_type)

    transaction_type = request.GET.get('transaction_type', '')
    if transaction_type:
        queryset = queryset.filter(Q(transaction_type=transaction_type) | Q(transaction_type='both'))

    status_filter = request.GET.get('status', '')
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    is_exclusive = request.GET.get('is_exclusive', '')
    if is_exclusive == 'true':
        queryset = queryset.filter(is_exclusive=True)

    my_captures = request.GET.get('my_captures', '')
    if my_captures == 'true' and request.user.is_authenticated:
        queryset = queryset.filter(captured_by=request.user)

    view_mode = request.GET.get('view_mode', 'grid')

    neighborhoods = Property.objects.values_list('neighborhood', flat=True).distinct().order_by('neighborhood')

    context = {
        'properties': queryset,
        'neighborhoods': neighborhoods,
        'view_mode': view_mode,
        'q': q,
        'current_neighborhood': neighborhood,
        'current_type': property_type,
        'current_trans': transaction_type,
        'current_status': status_filter,
        'current_exclusive': is_exclusive,
        'current_my_captures': my_captures,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'properties/partials/property_items.html', context)

    return render(request, 'properties/list.html', context)


@login_required(login_url='crm_login')
def property_detail_view(request, pk):
    """Ficha Completa do Imóvel com Match de Leads Interessados e Proteção de Privacidade"""
    property_obj = get_object_or_404(Property.objects.prefetch_related('images'), pk=pk)
    can_view_owner = property_obj.can_view_owner(request.user)
    matching_leads = get_matching_leads_for_property(property_obj, user=request.user)

    return render(request, 'properties/detail.html', {
        'property': property_obj,
        'can_view_owner': can_view_owner,
        'can_edit_property': can_edit_property(request.user, property_obj),
        'matching_leads': matching_leads,
    })


def _get_permitted_owners_qs(user):
    if is_user_manager(user):
        qs = Person.objects.all()
    else:
        qs = Person.objects.filter(Q(assigned_agent=user) | Q(assigned_agent__isnull=True))
    return qs.order_by(
        Case(When(client_type='owner', then=0), default=1),
        'name',
    )


@login_required(login_url='crm_login')
def property_create_view(request):
    """Cadastro Completo de Imóvel"""
    owners_qs = _get_permitted_owners_qs(request.user)

    if request.method == 'POST':
        code = request.POST.get('code')
        title = request.POST.get('title')
        property_type = request.POST.get('property_type', 'apartment')
        transaction_type = request.POST.get('transaction_type', 'sale')
        sale_price = parse_brl_decimal(request.POST.get('sale_price'))
        rental_price = parse_brl_decimal(request.POST.get('rental_price'))
        condo_fee = parse_brl_decimal(request.POST.get('condo_fee'))
        iptu = parse_brl_decimal(request.POST.get('iptu'))
        neighborhood = request.POST.get('neighborhood', '')
        street = request.POST.get('street', '')
        number = request.POST.get('number', '')
        building_name = request.POST.get('building_name', '')
        city = request.POST.get('city', 'São Luís')
        usable_area = request.POST.get('usable_area') or 0.00
        bedrooms = request.POST.get('bedrooms') or 0
        suites = request.POST.get('suites') or 0
        bathrooms = request.POST.get('bathrooms') or 1
        parking_spaces = request.POST.get('parking_spaces') or 0
        key_location = request.POST.get('key_location', 'Portaria')
        description = request.POST.get('description', '')
        status_val = request.POST.get('status', 'available')
        agreed_commission = request.POST.get('agreed_commission_rate') or 5.00

        is_exclusive = 'is_exclusive' in request.POST
        share_owner = 'share_owner_contact' in request.POST
        morning_sun = 'morning_sun' in request.POST
        gourmet_balcony = 'gourmet_balcony' in request.POST
        pets_allowed = 'pets_allowed' in request.POST
        pool = 'pool' in request.POST
        gym = 'gym' in request.POST
        elevator = 'elevator' in request.POST
        furnished = 'furnished' in request.POST

        owner_id = request.POST.get('owner') or None
        owner = owners_qs.filter(pk=owner_id).first() if owner_id else None

        prop = Property.objects.create(
            code=code,
            title=title,
            property_type=property_type,
            transaction_type=transaction_type,
            sale_price=sale_price,
            rental_price=rental_price,
            condo_fee=condo_fee,
            iptu=iptu,
            neighborhood=neighborhood,
            street=street,
            number=number,
            building_name=building_name,
            city=city,
            usable_area=usable_area,
            bedrooms=bedrooms,
            suites=suites,
            bathrooms=bathrooms,
            parking_spaces=parking_spaces,
            key_location=key_location,
            description=description,
            status=status_val,
            agreed_commission_rate=agreed_commission,
            is_exclusive=is_exclusive,
            share_owner_contact=share_owner,
            morning_sun=morning_sun,
            gourmet_balcony=gourmet_balcony,
            pets_allowed=pets_allowed,
            pool=pool,
            gym=gym,
            elevator=elevator,
            furnished=furnished,
            owner=owner,
            captured_by=request.user,
        )

        images = request.FILES.getlist('images')
        for idx, img_file in enumerate(images):
            PropertyImage.objects.create(
                property=prop,
                image=img_file,
                is_featured=(idx == 0),
            )

        messages.success(request, f'Imóvel {prop.code} cadastrado com sucesso!')
        return redirect('property_detail', pk=prop.id)

    return render(request, 'properties/form.html', {'property': None, 'owners': owners_qs})


@login_required(login_url='crm_login')
@rate_limit('property_import_url', 30, 60, methods=('POST',), scope='user_or_ip')
def property_import_url_view(request):
    """Importador assistido multi-portal (OLX, VivaReal, ZAP, Imovelweb e demais anúncios)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido. Utilize POST.'}, status=405)

    target_url = ''
    if request.content_type == 'application/json' and request.body:
        try:
            body = json.loads(request.body.decode('utf-8'))
            target_url = str(body.get('url') or '').strip()
        except Exception:
            target_url = ''
    if not target_url:
        target_url = str(request.POST.get('url') or '').strip()

    from .services.portal_importer import SSRFBlockedError, fetch_and_extract_property

    try:
        result = fetch_and_extract_property(target_url)
    except SSRFBlockedError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)
    except RuntimeError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=422)

    found = result.get('found_fields') or []
    missing = result.get('missing_fields') or []
    domain = result.get('source_domain') or 'portal externo'

    if found:
        msg = f"Dados importados de {domain}: {', '.join(found)}."
        if missing:
            msg += f" Confira/preencha manualmente: {', '.join(missing)}."
    else:
        msg = (
            f"O link de {domain} não expôs metadados estruturados automaticamente. "
            "Preencha os campos do imóvel manualmente abaixo."
        )

    return JsonResponse({
        'success': True,
        'source_domain': domain,
        'data': result['data'],
        'found_fields': found,
        'missing_fields': missing,
        'message': msg,
    })


@login_required(login_url='crm_login')
def property_edit_view(request, pk):
    """Edição Completa de Imóvel (restrita ao captador ou gestor/admin)"""
    prop = get_object_or_404(Property, pk=pk)
    if not can_edit_property(request.user, prop):
        log_security_event(
            action='unauthorized_access',
            target=f'Property #{pk}',
            details='Tentativa bloqueada de edição de imóvel pertencente a outro captador.',
            request=request,
        )
        return HttpResponseForbidden('Você não tem permissão para editar este imóvel.')

    owners_qs = _get_permitted_owners_qs(request.user)

    if request.method == 'POST':
        prop.code = request.POST.get('code')
        prop.title = request.POST.get('title')
        prop.property_type = request.POST.get('property_type', 'apartment')
        prop.transaction_type = request.POST.get('transaction_type', 'sale')
        prop.sale_price = parse_brl_decimal(request.POST.get('sale_price'))
        prop.rental_price = parse_brl_decimal(request.POST.get('rental_price'))
        prop.condo_fee = parse_brl_decimal(request.POST.get('condo_fee'))
        prop.iptu = parse_brl_decimal(request.POST.get('iptu'))
        prop.neighborhood = request.POST.get('neighborhood', '')
        prop.street = request.POST.get('street', '')
        prop.number = request.POST.get('number', '')
        prop.building_name = request.POST.get('building_name', '')
        prop.city = request.POST.get('city', 'São Luís')
        prop.usable_area = request.POST.get('usable_area') or 0.00
        prop.bedrooms = request.POST.get('bedrooms') or 0
        prop.suites = request.POST.get('suites') or 0
        prop.bathrooms = request.POST.get('bathrooms') or 1
        prop.parking_spaces = request.POST.get('parking_spaces') or 0
        prop.key_location = request.POST.get('key_location', 'Portaria')
        prop.description = request.POST.get('description', '')
        prop.status = request.POST.get('status', 'available')
        prop.agreed_commission_rate = request.POST.get('agreed_commission_rate') or 5.00

        prop.is_exclusive = 'is_exclusive' in request.POST
        prop.share_owner_contact = 'share_owner_contact' in request.POST
        prop.morning_sun = 'morning_sun' in request.POST
        prop.gourmet_balcony = 'gourmet_balcony' in request.POST
        prop.pets_allowed = 'pets_allowed' in request.POST
        prop.pool = 'pool' in request.POST
        prop.gym = 'gym' in request.POST
        prop.elevator = 'elevator' in request.POST
        prop.furnished = 'furnished' in request.POST

        owner_id = request.POST.get('owner') or None
        if owner_id:
            prop.owner = owners_qs.filter(pk=owner_id).first() or prop.owner
        else:
            prop.owner = None

        prop.save()

        delete_images_ids = request.POST.getlist('delete_images')
        if delete_images_ids:
            prop.images.filter(id__in=delete_images_ids).delete()

        featured_image_id = request.POST.get('featured_image')
        if featured_image_id:
            prop.images.all().update(is_featured=False)
            prop.images.filter(id=featured_image_id).update(is_featured=True)

        new_images = request.FILES.getlist('images')
        has_featured = prop.images.filter(is_featured=True).exists()
        for idx, img_file in enumerate(new_images):
            is_feat = False
            if not has_featured and idx == 0:
                is_feat = True
                has_featured = True
            PropertyImage.objects.create(
                property=prop,
                image=img_file,
                is_featured=is_feat,
            )

        if not prop.images.filter(is_featured=True).exists() and prop.images.exists():
            first_img = prop.images.first()
            first_img.is_featured = True
            first_img.save()

        messages.success(request, f'Imóvel {prop.code} atualizado com sucesso!')
        return redirect('property_detail', pk=prop.id)

    return render(request, 'properties/form.html', {'property': prop, 'owners': owners_qs})


@login_required(login_url='crm_login')
def property_quick_create_view(request):
    """Captação Rápida de Imóvel (30 segundos) - Salva como Rascunho"""
    if request.method == 'POST':
        title = request.POST.get('title')
        transaction_type = request.POST.get('transaction_type', 'sale')
        property_type = request.POST.get('property_type', 'apartment')
        neighborhood = request.POST.get('neighborhood', '')
        price = parse_brl_decimal(request.POST.get('price'))
        bedrooms = request.POST.get('bedrooms') or 3
        parking_spaces = request.POST.get('parking_spaces') or 2
        owner_name = request.POST.get('owner_name', '').strip()
        owner_phone = request.POST.get('owner_phone', '').strip()

        owner = None
        if owner_name or owner_phone:
            owner, _ = Person.objects.get_or_create(
                phone=owner_phone or f"sem-fone-{random.randint(1000, 9999)}",
                defaults={
                    'name': owner_name or 'Proprietário a Identificar',
                    'client_type': 'owner',
                    'assigned_agent': request.user,
                    'lgpd_consent': True,
                    'lgpd_consent_at': timezone.now(),
                    'lgpd_origin': 'captacao_rapida',
                }
            )

        prefix = 'AP' if property_type == 'apartment' else ('CS' if property_type == 'house' else 'IM')
        auto_code = f"{prefix}{random.randint(1000, 9999)}"

        sale_val = price if transaction_type in ['sale', 'both'] else 0.00
        rent_val = price if transaction_type == 'rent' else 0.00

        prop = Property.objects.create(
            code=auto_code,
            title=title,
            property_type=property_type,
            transaction_type=transaction_type,
            sale_price=sale_val,
            rental_price=rent_val,
            neighborhood=neighborhood,
            bedrooms=bedrooms,
            parking_spaces=parking_spaces,
            owner=owner,
            status='draft',
            captured_by=request.user,
        )

        messages.success(request, f'⚡ Captação rápida salva como Rascunho ({prop.code})! Complete a ficha quando desejar.')
        return redirect('property_detail', pk=prop.id)

    return redirect('property_list')


# ==========================================
# FASE 5: GESTÃO DE VISITAS & CONTROLE DE CHAVES
# ==========================================

@login_required(login_url='crm_login')
def visit_schedule_view(request):
    """Tela de Calendário Visual Semanal de Visitas com KPIs e Controle de Chaves"""
    user_is_mgr = is_user_manager(request.user)
    try:
        week_offset = int(request.GET.get('week_offset', 0))
    except (TypeError, ValueError):
        week_offset = 0
    selected_agent = request.GET.get('agent', '')

    today = timezone.localdate()
    start_of_current_week = today - timedelta(days=today.weekday())
    week_start = start_of_current_week + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6)

    visits_qs = PropertyVisit.objects.filter(
        scheduled_date__date__range=[week_start, week_end]
    ).select_related('lead', 'lead__client', 'visit_property', 'agent').order_by('scheduled_date')

    if not user_is_mgr:
        visits_qs = visits_qs.filter(Q(agent=request.user) | Q(lead__agent=request.user))
    elif selected_agent:
        visits_qs = visits_qs.filter(agent_id=selected_agent)

    weekday_names = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
    days_data = []
    for i in range(7):
        current_day = week_start + timedelta(days=i)
        day_visits = [v for v in visits_qs if timezone.localtime(v.scheduled_date).date() == current_day]
        days_data.append({
            'date': current_day,
            'weekday_name': weekday_names[i],
            'is_today': current_day == today,
            'visits': day_visits
        })

    total_visits_week = visits_qs.count()
    confirmed_visits_count = visits_qs.filter(status='confirmed').count()

    keys_qs = PropertyVisit.objects.filter(key_status='with_agent').select_related('visit_property', 'agent', 'lead__client')
    if not user_is_mgr:
        keys_qs = keys_qs.filter(agent=request.user)

    keys_with_agent_count = keys_qs.count()
    overdue_keys = [v for v in keys_qs if v.is_key_overdue]
    overdue_keys_count = len(overdue_keys)

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')
    leads_qs = PropertyLead.objects.filter(status='open').select_related('client')
    if not user_is_mgr:
        leads_qs = leads_qs.filter(agent=request.user)
    active_leads = leads_qs.order_by('-created_at')[:30]
    available_properties = Property.objects.filter(status__in=['available', 'reserved']).order_by('code')[:30]

    context = {
        'week_offset': week_offset,
        'prev_week_offset': week_offset - 1,
        'next_week_offset': week_offset + 1,
        'week_start': week_start,
        'week_end': week_end,
        'days_data': days_data,
        'total_visits_week': total_visits_week,
        'confirmed_visits_count': confirmed_visits_count,
        'keys_with_agent_count': keys_with_agent_count,
        'overdue_keys': overdue_keys,
        'overdue_keys_count': overdue_keys_count,
        'agents': agents,
        'selected_agent': selected_agent,
        'active_leads': active_leads,
        'available_properties': available_properties,
    }

    cal_integration, _ = CalendarIntegration.objects.get_or_create(user=request.user)
    context['calendar_integration'] = cal_integration
    context['ical_feed_url'] = request.build_absolute_uri(
        reverse('visit_ical_feed', kwargs={'token': cal_integration.ical_token})
    )
    context['webcal_feed_url'] = context['ical_feed_url'].replace('http://', 'webcal://').replace('https://', 'webcal://')

    return render(request, 'visits/calendar.html', context)


@login_required(login_url='crm_login')
def visit_schedule_create_view(request):
    """Criação de Visita a partir do Modal do Calendário"""
    if request.method == 'POST':
        lead_id = request.POST.get('lead_id')
        property_id = request.POST.get('property_id')
        scheduled_date_str = request.POST.get('scheduled_date')
        agent_id = request.POST.get('agent_id')
        meeting_point = request.POST.get('meeting_point', 'No próprio imóvel')
        key_status = request.POST.get('key_status', 'concierge')

        lead = get_object_or_404(PropertyLead, pk=lead_id)
        if not can_access_lead(request.user, lead):
            raise Http404("Lead não encontrado na sua carteira.")

        prop = get_object_or_404(Property, pk=property_id)
        if is_user_manager(request.user) and agent_id:
            agent = User.objects.filter(pk=agent_id).first() or request.user
        else:
            agent = request.user

        scheduled_date = timezone.datetime.fromisoformat(scheduled_date_str) if scheduled_date_str else timezone.now()
        if timezone.is_naive(scheduled_date):
            scheduled_date = timezone.make_aware(scheduled_date)

        visit = PropertyVisit.objects.create(
            lead=lead,
            visit_property=prop,
            agent=agent,
            scheduled_date=scheduled_date,
            status='confirmed',
            meeting_point=meeting_point,
            key_status=key_status
        )

        if key_status == 'with_agent':
            visit.key_withdrawn_at = timezone.now()
            visit.save()

        if prop not in lead.interested_properties.all():
            lead.interested_properties.add(prop)

        visit_stage = Stage.objects.filter(pipeline=lead.pipeline, stage_type='open', name__icontains='Visita').first()
        if visit_stage and lead.stage != visit_stage:
            lead.stage = visit_stage
            lead.save()

        lead.last_contact_at = timezone.now()
        lead.save()

        InteractionLog.objects.create(
            lead=lead,
            user=request.user,
            action_type='visit_scheduled',
            content=f"Visita agendada para {scheduled_date.strftime('%d/%m/%Y às %H:%M')} no imóvel [{prop.code}] ({prop.title})."
        )

        messages.success(request, f'Visita ao imóvel {prop.code} agendada com sucesso!')
        return redirect('visit_schedule')

    return redirect('visit_schedule')


@login_required(login_url='crm_login')
def visit_create_from_lead_view(request, lead_id):
    """Criação de Visita a partir do Drawer do Lead com Redirecionamento Direto ao WhatsApp"""
    lead = get_object_or_404(PropertyLead, pk=lead_id)
    if not can_access_lead(request.user, lead):
        raise Http404("Lead não encontrado na sua carteira.")

    if request.method == 'POST':
        property_id = request.POST.get('property_id')
        scheduled_date_str = request.POST.get('scheduled_date')
        meeting_point = request.POST.get('meeting_point', 'No próprio imóvel')

        prop = get_object_or_404(Property, pk=property_id)

        scheduled_date = timezone.datetime.fromisoformat(scheduled_date_str) if scheduled_date_str else timezone.now()
        if timezone.is_naive(scheduled_date):
            scheduled_date = timezone.make_aware(scheduled_date)

        visit = PropertyVisit.objects.create(
            lead=lead,
            visit_property=prop,
            agent=lead.agent or request.user,
            scheduled_date=scheduled_date,
            status='confirmed',
            meeting_point=meeting_point,
            key_status='concierge'
        )

        if prop not in lead.interested_properties.all():
            lead.interested_properties.add(prop)

        visit_stage = Stage.objects.filter(pipeline=lead.pipeline, stage_type='open', name__icontains='Visita').first()
        if visit_stage and lead.stage != visit_stage:
            lead.stage = visit_stage
            lead.save()

        lead.last_contact_at = timezone.now()
        lead.save()

        InteractionLog.objects.create(
            lead=lead,
            user=request.user,
            action_type='visit_scheduled',
            content=f"Visita marcada para {scheduled_date.strftime('%d/%m/%Y às %H:%M')} no imóvel [{prop.code}] {prop.neighborhood}."
        )

        messages.success(request, f'Visita ao imóvel {prop.code} agendada com sucesso!')

        if visit.whatsapp_confirmation_url and visit.whatsapp_confirmation_url != '#':
            return redirect(visit.whatsapp_confirmation_url)

        return redirect('kanban')

    return redirect('kanban')


@login_required(login_url='crm_login')
def visit_feedback_form_view(request, visit_id):
    """Carrega o formulário HTMX de feedback da visita"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'lead__client', 'visit_property'), pk=visit_id)
    if not can_access_visit(request.user, visit):
        raise Http404("Visita não encontrada na sua carteira.")
    return render(request, 'visits/partials/visit_feedback_form.html', {'visit': visit})


@login_required(login_url='crm_login')
def visit_feedback_save_view(request, visit_id):
    """Salva o feedback pós-visita e executa a automação de mover o lead para 'Proposta / Negociação'"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'lead__pipeline', 'visit_property'), pk=visit_id)
    if not can_access_visit(request.user, visit):
        raise Http404("Visita não encontrada na sua carteira.")

    if request.method == 'POST':
        status_val = request.POST.get('status', 'completed')
        client_rating = request.POST.get('client_rating') or None
        feedback_notes = request.POST.get('feedback_notes', '').strip()
        will_make_proposal = 'will_make_proposal' in request.POST or request.POST.get('will_make_proposal') == 'true'
        proposal_details = request.POST.get('proposal_details', '').strip()
        rejection_reason = request.POST.get('rejection_reason', '').strip()

        visit.status = status_val
        if client_rating:
            visit.client_rating = int(client_rating)
        visit.feedback_notes = feedback_notes
        visit.will_make_proposal = will_make_proposal
        visit.proposal_details = proposal_details
        visit.rejection_reason = rejection_reason
        visit.save()

        lead = visit.lead
        lead.last_contact_at = timezone.now()

        if will_make_proposal:
            proposal_stage = Stage.objects.filter(
                pipeline=lead.pipeline,
                stage_type='open',
                name__icontains='Proposta'
            ).first() or Stage.objects.filter(pipeline=lead.pipeline, order=4).first()

            if proposal_stage:
                old_stage = lead.stage.name
                lead.stage = proposal_stage
                lead.save()

                InteractionLog.objects.create(
                    lead=lead,
                    user=request.user,
                    action_type='proposal_sent',
                    content=(
                        f"🤝 [AUTOMAÇÃO DE ESTÁGIO] Movido de '{old_stage}' para '{proposal_stage.name}' "
                        f"após feedback positivo na visita do imóvel [{visit.visit_property.code}]. "
                        f"Detalhes da proposta: {proposal_details or 'Aguardando formalização'}."
                    )
                )
        else:
            InteractionLog.objects.create(
                lead=lead,
                user=request.user,
                action_type='visit_feedback',
                content=f"Feedback registrado para visita ao imóvel [{visit.visit_property.code}]: Nota {client_rating or '-'}/5. Parecer: {feedback_notes or 'Sem observações'}."
            )

        lead.save()
        messages.success(request, f'Feedback da visita ao imóvel {visit.visit_property.code} salvo com sucesso!')

        referrer = request.META.get('HTTP_REFERER', '')
        if 'visits' in referrer:
            return redirect('visit_schedule')
        return redirect('kanban')

    return redirect('visit_schedule')


@login_required(login_url='crm_login')
def visit_key_action_view(request, visit_id):
    """Registra a retirada ou devolução de chaves com timestamp auditável"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'visit_property', 'agent'), pk=visit_id)
    if not can_access_visit(request.user, visit):
        raise Http404("Visita não encontrada na sua carteira.")

    if request.method == 'POST':
        action_type = request.POST.get('action_type')
        agent_display = (visit.agent.get_full_name() or visit.agent.username) if visit.agent else 'responsável'

        if action_type == 'withdraw':
            visit.key_status = 'with_agent'
            visit.key_withdrawn_at = timezone.now()
            visit.key_returned_at = None
            visit.save()

            InteractionLog.objects.create(
                lead=visit.lead,
                user=request.user,
                action_type='key_action',
                content=f"🔑 Chave do imóvel [{visit.visit_property.code}] RETIRADA pelo corretor {agent_display} às {visit.key_withdrawn_at.strftime('%H:%M')}."
            )
            messages.warning(request, f'Chave do imóvel {visit.visit_property.code} retirada! Lembre-se do prazo de devolução de até 4h.')

        elif action_type == 'return':
            visit.key_status = 'returned'
            visit.key_returned_at = timezone.now()
            visit.save()

            InteractionLog.objects.create(
                lead=visit.lead,
                user=request.user,
                action_type='key_action',
                content=f"✅ Chave do imóvel [{visit.visit_property.code}] DEVOLVIDA na imobiliária às {visit.key_returned_at.strftime('%H:%M')}."
            )
            messages.success(request, f'Chave do imóvel {visit.visit_property.code} devolvida com sucesso!')

    safe_ref = get_safe_redirect_url(request, request.META.get('HTTP_REFERER'), reverse('visit_schedule'))
    return redirect(safe_ref)


@login_required(login_url='crm_login')
def visit_whatsapp_confirm_view(request, visit_id):
    """Redireciona para o link gerado do WhatsApp com mensagem de confirmação"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'visit_property'), pk=visit_id)
    if not can_access_visit(request.user, visit):
        raise Http404("Visita não encontrada na sua carteira.")

    if visit.whatsapp_confirmation_url and visit.whatsapp_confirmation_url != '#':
        return redirect(visit.whatsapp_confirmation_url)
    messages.error(request, 'Telefone do cliente inválido para WhatsApp.')
    return redirect('visit_schedule')


# ==========================================
# FASE 5: TAREFAS & FOLLOW-UPS DO DIA
# ==========================================

@login_required(login_url='crm_login')
def task_list_view(request):
    """Tela 'Minhas Tarefas & Follow-ups do Dia' com separação por Atrasadas, Hoje e Próximos 7 Dias"""
    user_is_mgr = is_user_manager(request.user)
    selected_category = request.GET.get('category', '')
    selected_agent = request.GET.get('agent', '')

    now = timezone.now()
    today = now.date()
    end_of_today = timezone.make_aware(datetime.combine(today, datetime.max.time()))
    seven_days_later = end_of_today + timedelta(days=7)

    base_qs = Activity.objects.select_related('lead', 'lead__client', 'related_property', 'assigned_to')

    if not user_is_mgr:
        base_qs = base_qs.filter(assigned_to=request.user)
    elif selected_agent:
        base_qs = base_qs.filter(assigned_to_id=selected_agent)

    if selected_category:
        base_qs = base_qs.filter(task_type=selected_category)

    overdue_tasks = list(base_qs.filter(is_completed=False, due_date__lt=now).order_by('due_date'))
    today_tasks = list(base_qs.filter(is_completed=False, due_date__gte=now, due_date__lte=end_of_today).order_by('due_date'))
    upcoming_tasks = list(base_qs.filter(is_completed=False, due_date__gt=end_of_today, due_date__lte=seven_days_later).order_by('due_date'))
    completed_tasks = list(base_qs.filter(is_completed=True).order_by('-completed_at')[:20])

    today_tasks_count = len(today_tasks)
    overdue_tasks_count = len(overdue_tasks)
    upcoming_tasks_count = len(upcoming_tasks)
    completed_today_count = base_qs.filter(is_completed=True, completed_at__date=today).count()

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')
    leads_qs = PropertyLead.objects.filter(status='open').select_related('client')
    if not user_is_mgr:
        leads_qs = leads_qs.filter(agent=request.user)
    active_leads = leads_qs.order_by('-created_at')[:30]
    available_properties = Property.objects.filter(status__in=['available', 'reserved']).order_by('code')[:30]

    context = {
        'overdue_tasks': overdue_tasks,
        'today_tasks': today_tasks,
        'upcoming_tasks': upcoming_tasks,
        'completed_tasks': completed_tasks,
        'today': today,
        'today_tasks_count': today_tasks_count,
        'overdue_tasks_count': overdue_tasks_count,
        'upcoming_tasks_count': upcoming_tasks_count,
        'completed_today_count': completed_today_count,
        'agents': agents,
        'selected_agent': selected_agent,
        'selected_category': selected_category,
        'active_leads': active_leads,
        'available_properties': available_properties,
    }

    return render(request, 'tasks/list.html', context)


@login_required(login_url='crm_login')
def task_create_view(request):
    """Criação de Tarefa / Follow-up (do modal ou do drawer do lead)"""
    if request.method == 'POST':
        title = request.POST.get('title')
        task_type = request.POST.get('task_type', 'general')
        priority = request.POST.get('priority', 'medium')
        due_date_str = request.POST.get('due_date')
        assigned_to_id = request.POST.get('assigned_to')
        lead_id = request.POST.get('lead_id')
        property_id = request.POST.get('property_id')
        description = request.POST.get('description', '')

        due_date = timezone.datetime.fromisoformat(due_date_str) if due_date_str else timezone.now()
        if timezone.is_naive(due_date):
            due_date = timezone.make_aware(due_date)

        if is_user_manager(request.user) and assigned_to_id:
            assigned_to = User.objects.filter(pk=assigned_to_id).first() or request.user
        else:
            assigned_to = request.user

        lead = None
        if lead_id:
            lead = get_object_or_404(PropertyLead, pk=lead_id)
            if not can_access_lead(request.user, lead):
                raise Http404("Lead não encontrado na sua carteira.")

        related_property = Property.objects.filter(pk=property_id).first() if property_id else None

        task = Activity.objects.create(
            title=title,
            task_type=task_type,
            priority=priority,
            due_date=due_date,
            assigned_to=assigned_to,
            lead=lead,
            related_property=related_property,
            description=description
        )

        if lead:
            InteractionLog.objects.create(
                lead=lead,
                user=request.user,
                action_type='task_created',
                content=f"⏰ Tarefa criada: '{task.title}' para {due_date.strftime('%d/%m/%Y às %H:%M')} (Prioridade: {task.get_priority_display()})."
            )

        messages.success(request, f'Tarefa "{task.title}" criada com sucesso!')

        redirect_to = request.POST.get('redirect_to')
        if redirect_to == 'drawer' and lead:
            return redirect('kanban')

        safe_ref = get_safe_redirect_url(request, request.META.get('HTTP_REFERER'), reverse('task_list'))
        return redirect(safe_ref)

    return redirect('task_list')


@login_required(login_url='crm_login')
def task_toggle_complete_view(request, task_id):
    """Alterna o status de conclusão de uma tarefa via HTMX"""
    task = get_object_or_404(Activity.objects.select_related('lead', 'lead__client', 'related_property', 'assigned_to'), pk=task_id)
    if not can_access_task(request.user, task):
        raise Http404("Tarefa não encontrada na sua carteira.")

    task.is_completed = not task.is_completed
    if task.is_completed:
        task.completed_at = timezone.now()
        if task.lead:
            InteractionLog.objects.create(
                lead=task.lead,
                user=request.user,
                action_type='note',
                content=f"✅ Tarefa concluída: '{task.title}'."
            )
    else:
        task.completed_at = None
    task.save()

    return render(request, 'tasks/partials/task_item.html', {'task': task})


# ==========================================
# FASE 6: PAINEL DE INDICADORES & DASHBOARD BI
# ==========================================

@login_required(login_url='crm_login')
@rate_limit('heavy_dashboard', 30, 60, scope='user_or_ip')
def dashboard_view(request):
    """
    Painel de Inteligência e Indicadores Executivos (BI Imobiliário)
    Visão Condicional:
    - Gestor/Admin: Métricas consolidadas de toda a imobiliária + Ranking de corretores + Filtro por corretor.
    - Corretor: Filtro automático no backend (vê apenas sua própria produção e meta individual).
    """
    user = request.user
    is_manager = is_user_manager(user)

    selected_agent_id = request.GET.get('agent', '').strip()
    trans_type_filter = request.GET.get('trans_type', 'all').strip()

    if is_manager:
        if selected_agent_id:
            leads_base_qs = PropertyLead.objects.filter(agent_id=selected_agent_id)
            target_agent = User.objects.filter(id=selected_agent_id).first()
        else:
            leads_base_qs = PropertyLead.objects.all()
            target_agent = None
    else:
        leads_base_qs = PropertyLead.objects.filter(agent=user)
        target_agent = user

    if trans_type_filter in ['buy', 'rent']:
        leads_filtered_qs = leads_base_qs.filter(transaction_type=trans_type_filter)
    else:
        leads_filtered_qs = leads_base_qs

    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    won_leads_month = leads_filtered_qs.filter(
        status='won'
    ).filter(
        Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
    )

    vgv_pipeline = leads_filtered_qs.filter(status='open').aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')
    vgv_fechado_mes = won_leads_month.aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

    vgv_vendas_mes = leads_base_qs.filter(
        status='won', transaction_type='buy'
    ).filter(
        Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
    ).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

    vgv_locacao_mes = leads_base_qs.filter(
        status='won', transaction_type='rent'
    ).filter(
        Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
    ).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

    comissao_prevista = vgv_pipeline * Decimal('0.06')
    comissao_realizada = vgv_fechado_mes * Decimal('0.06')

    if target_agent and hasattr(target_agent, 'profile') and target_agent.profile.monthly_goal is not None:
        meta_mensal = target_agent.profile.monthly_goal
    elif is_manager and not selected_agent_id:
        total_meta_agents = UserProfile.objects.filter(user__is_active=True).aggregate(Sum('monthly_goal'))['monthly_goal__sum']
        meta_mensal = total_meta_agents or Decimal('0.00')
    else:
        meta_mensal = Decimal('0.00')

    meta_progress_percent = min(100.0, float(round((vgv_fechado_mes / meta_mensal) * 100, 1))) if meta_mensal > 0 else 0.0

    all_won_leads = leads_filtered_qs.filter(status='won')
    ticket_medio = all_won_leads.aggregate(Avg('budget'))['budget__avg'] or Decimal('0.00')

    cycle_days_list = [lead.cycle_time_days for lead in all_won_leads]
    tempo_medio_ciclo = round(sum(cycle_days_list) / len(cycle_days_list), 1) if cycle_days_list else 18.5

    pipeline_default = Pipeline.objects.filter(is_default=True).first() or Pipeline.objects.first()
    stages = Stage.objects.filter(pipeline=pipeline_default).order_by('order') if pipeline_default else []

    total_leads_count = leads_filtered_qs.count()
    funnel_data = []
    for stg in stages:
        count_in_stage = leads_filtered_qs.filter(stage=stg).count()
        amount_in_stage = leads_filtered_qs.filter(stage=stg).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')
        percent = round((count_in_stage / total_leads_count) * 100, 1) if total_leads_count > 0 else 0
        funnel_data.append({
            'stage': stg,
            'count': count_in_stage,
            'amount': amount_in_stage,
            'percent': percent,
        })

    total_won = leads_filtered_qs.filter(status='won').count()
    total_lost = leads_filtered_qs.filter(status='lost').count()
    total_concluded = total_won + total_lost
    taxa_conversao_geral = round((total_won / total_concluded) * 100, 1) if total_concluded > 0 else (
        round((total_won / total_leads_count) * 100, 1) if total_leads_count > 0 else 0.0
    )

    origin_choices = dict(PropertyLead.ORIGIN_CHOICES)
    origin_counts = leads_filtered_qs.values('origin').annotate(total=Count('id')).order_by('-total')
    origins_chart_labels = []
    origins_chart_data = []
    for item in origin_counts:
        origins_chart_labels.append(origin_choices.get(item['origin'], item['origin']))
        origins_chart_data.append(item['total'])

    if not origins_chart_data:
        origins_chart_labels = ['Instagram Ads', 'Portal ZAP', 'VivaReal', 'Indicação', 'Site']
        origins_chart_data = [12, 8, 6, 4, 3]

    month_names_pt = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    vgv_monthly_labels = []
    vgv_monthly_data = []

    for i in range(5, -1, -1):
        calc_month = (now.month - i - 1) % 12 + 1
        calc_year = now.year if (now.month - i) > 0 else now.year - 1
        label = f"{month_names_pt[calc_month]}/{str(calc_year)[2:]}"
        vgv_monthly_labels.append(label)

        m_start = timezone.make_aware(datetime(calc_year, calc_month, 1, 0, 0, 0))
        if calc_month == 12:
            m_end = timezone.make_aware(datetime(calc_year + 1, 1, 1, 0, 0, 0))
        else:
            m_end = timezone.make_aware(datetime(calc_year, calc_month + 1, 1, 0, 0, 0))

        month_sum = leads_filtered_qs.filter(
            status='won',
            closed_at__gte=m_start,
            closed_at__lt=m_end
        ).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

        vgv_monthly_data.append(float(month_sum))

    if sum(vgv_monthly_data) == 0 and float(vgv_fechado_mes) > 0:
        vgv_monthly_data[-1] = float(vgv_fechado_mes)

    lost_reasons_qs = leads_filtered_qs.filter(status='lost').exclude(lost_reason__isnull=True).exclude(lost_reason='').values('lost_reason').annotate(total=Count('id')).order_by('-total')[:6]
    lost_reasons_labels = []
    lost_reasons_data = []
    for lr in lost_reasons_qs:
        lost_reasons_labels.append(lr['lost_reason'])
        lost_reasons_data.append(lr['total'])

    if not lost_reasons_data:
        lost_reasons_labels = ['Preço acima do orçamento', 'Financiamento reprovado', 'Localização desfavorável', 'Preferiu outro corretor', 'Desistiu da compra']
        lost_reasons_data = [5, 4, 3, 2, 1]

    agents_ranking = []
    if is_manager:
        all_agents = User.objects.filter(is_active=True).select_related('profile').order_by('first_name')
        for ag in all_agents:
            ag_leads = PropertyLead.objects.filter(agent=ag)
            ag_active = ag_leads.filter(status='open').count()

            ag_visits_month = PropertyVisit.objects.filter(
                agent=ag,
                status='completed',
                scheduled_date__gte=start_of_month
            ).count()

            ag_vgv_won = ag_leads.filter(
                status='won'
            ).filter(
                Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
            ).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

            ag_won_count = ag_leads.filter(status='won').count()
            ag_total_concluded = ag_won_count + ag_leads.filter(status='lost').count()
            ag_conv_rate = round((ag_won_count / ag_total_concluded) * 100, 1) if ag_total_concluded > 0 else 0.0

            ag_goal = (
                ag.profile.monthly_goal
                if hasattr(ag, 'profile') and ag.profile.monthly_goal is not None
                else Decimal('0.00')
            )
            ag_has_goal = ag_goal > 0
            ag_goal_percent = min(100.0, float(round((ag_vgv_won / ag_goal) * 100, 1))) if ag_has_goal else 0.0

            agents_ranking.append({
                'agent': ag,
                'active_leads': ag_active,
                'visits_month': ag_visits_month,
                'vgv_won': ag_vgv_won,
                'vgv_won_formatted': f"R$ {format_brl(ag_vgv_won)}",
                'conversion_rate': ag_conv_rate,
                'goal': ag_goal,
                'has_goal': ag_has_goal,
                'goal_formatted': f"R$ {format_brl(ag_goal)}" if ag_has_goal else 'Meta não definida',
                'goal_input': format_brl(ag_goal) if ag_has_goal else '',
                'goal_percent': ag_goal_percent,
            })

        agents_ranking.sort(key=lambda x: x['vgv_won'], reverse=True)

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')

    context = {
        'is_manager': is_manager,
        'selected_agent_id': selected_agent_id,
        'trans_type_filter': trans_type_filter,
        'agents': agents,

        'vgv_pipeline': vgv_pipeline,
        'vgv_fechado_mes': vgv_fechado_mes,
        'vgv_vendas_mes': vgv_vendas_mes,
        'vgv_locacao_mes': vgv_locacao_mes,
        'comissao_prevista': comissao_prevista,
        'comissao_realizada': comissao_realizada,
        'meta_mensal': meta_mensal,
        'has_meta_mensal': meta_mensal > 0,
        'meta_mensal_input': format_brl(meta_mensal) if meta_mensal > 0 else '',
        'meta_progress_percent': meta_progress_percent,
        'target_agent_id': target_agent.id if target_agent else user.id,
        'target_agent_name': (target_agent.get_full_name() or target_agent.username) if target_agent else (user.get_full_name() or user.username),
        'can_edit_meta': bool(target_agent) or not is_manager,
        'ticket_medio': ticket_medio,
        'tempo_medio_ciclo': tempo_medio_ciclo,
        'taxa_conversao_geral': taxa_conversao_geral,

        'funnel_data': funnel_data,

        # Listas nativas (para json_script seguro sem |safe) + JSON escapado para retrocompatibilidade
        'origins_chart_labels': origins_chart_labels,
        'origins_chart_data': origins_chart_data,
        'vgv_monthly_labels': vgv_monthly_labels,
        'vgv_monthly_data': vgv_monthly_data,
        'lost_reasons_labels': lost_reasons_labels,
        'lost_reasons_data': lost_reasons_data,

        'origins_chart_labels_json': safe_json_dumps(origins_chart_labels),
        'origins_chart_data_json': safe_json_dumps(origins_chart_data),
        'vgv_monthly_labels_json': safe_json_dumps(vgv_monthly_labels),
        'vgv_monthly_data_json': safe_json_dumps(vgv_monthly_data),
        'lost_reasons_labels_json': safe_json_dumps(lost_reasons_labels),
        'lost_reasons_data_json': safe_json_dumps(lost_reasons_data),

        'agents_ranking': agents_ranking,
    }

    return render(request, 'dashboard/dashboard.html', context)


@login_required(login_url='crm_login')
def update_monthly_goal_view(request):
    """Permite ao corretor definir/alterar a própria meta mensal (ou ao gestor ajustar a de um membro)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    goal_value = parse_brl_decimal(request.POST.get('monthly_goal'), default=Decimal('0.00'))
    if goal_value < Decimal('0.00'):
        goal_value = Decimal('0.00')

    target_user = request.user
    target_user_id = request.POST.get('user_id')
    if target_user_id and is_user_manager(request.user):
        target_user = get_object_or_404(User, pk=target_user_id)

    profile, _ = UserProfile.objects.get_or_create(user=target_user, defaults={'role': 'agent'})
    profile.monthly_goal = goal_value
    profile.save(update_fields=['monthly_goal'])

    if goal_value > 0:
        messages.success(request, f'Meta mensal atualizada para R$ {format_brl(goal_value)}!')
    else:
        messages.info(request, 'Meta mensal removida (definida como não informada).')

    safe_next = get_safe_redirect_url(request, request.POST.get('next'), reverse('dashboard'))
    return redirect(safe_next)


# ==========================================
# FASE 7: WEBHOOKS, INTEGRAÇÕES & FEED XML
# ==========================================

@csrf_exempt
@rate_limit('webhook_leads', 60, 60, scope='ip')
def lead_webhook_ingest_view(request):
    """
    Endpoint de Ingestão de Leads via Webhook Seguro (Meta Ads, ZAP, VivaReal, Site, RD Station)
    - Autenticação em tempo constante via hmac.compare_digest (X-API-KEY ou query param api_key)
    - Rate Limiting de 60 req/min por IP
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido. Utilize POST.'}, status=405)

    provided_key = str(request.headers.get('X-API-KEY') or request.GET.get('api_key') or '')
    expected_key = str(settings.WEBHOOK_API_KEY or '')

    if not provided_key or not expected_key or not hmac.compare_digest(
        provided_key.encode('utf-8'),
        expected_key.encode('utf-8'),
    ):
        log_security_event(
            action='webhook_rejected',
            target='webhook_lead_ingest',
            details='Tentativa de ingestão via webhook com X-API-KEY ausente ou inválida.',
            request=request,
        )
        return JsonResponse({
            'error': 'Acesso não autorizado. Envie o cabeçalho X-API-KEY com uma chave válida.'
        }, status=401)

    try:
        if request.content_type == 'application/json' and request.body:
            data = json.loads(request.body)
        else:
            data = request.POST.dict() or json.loads(request.body.decode('utf-8'))
    except Exception:
        data = request.POST.dict()

    name = str(data.get('name') or data.get('nome') or 'Lead da Internet').strip()[:150]
    phone = str(data.get('phone') or data.get('telefone') or data.get('whatsapp') or '').strip()[:30]
    email = str(data.get('email') or '').strip()[:100]
    property_code = str(data.get('property_code') or data.get('codigo_imovel') or '').strip().upper()[:30]
    origin = str(data.get('origin') or data.get('origem') or 'webhook').strip().lower()[:30]
    notes = str(data.get('notes') or data.get('mensagem') or data.get('message') or '').strip()
    preferred_location = str(data.get('location') or data.get('bairro') or '').strip()[:100]

    budget_raw = data.get('budget') or data.get('valor') or 0.00
    try:
        budget = Decimal(str(budget_raw).replace(',', '.'))
    except Exception:
        budget = Decimal('0.00')

    transaction_type = str(data.get('transaction_type') or data.get('finalidade') or 'buy').strip().lower()
    if transaction_type not in ['buy', 'rent']:
        transaction_type = 'buy'

    if not phone and not email:
        return JsonResponse({'error': 'É necessário informar ao menos um telefone ou e-mail de contato.'}, status=400)

    client_phone = phone or f"sem-fone-{random.randint(10000, 99999)}"
    client, _ = Person.objects.get_or_create(
        phone=client_phone,
        defaults={
            'name': name,
            'email': email,
            'client_type': 'buyer' if transaction_type == 'buy' else 'renter',
            'lgpd_consent': bool(data.get('lgpd_consent', True)),
            'lgpd_consent_at': timezone.now(),
            'lgpd_origin': f'webhook:{origin}',
        }
    )

    if email and not client.email:
        client.email = email
        client.save()

    matched_property = None
    if property_code:
        matched_property = Property.objects.filter(code__iexact=property_code).first()

    assigned_agent = assign_lead_smart(property_obj=matched_property)
    if assigned_agent and not client.assigned_agent_id:
        client.assigned_agent = assigned_agent
        client.save(update_fields=['assigned_agent', 'updated_at'])

    pipeline = Pipeline.objects.filter(is_default=True).first() or Pipeline.objects.first()
    first_stage = Stage.objects.filter(pipeline=pipeline).order_by('order').first()

    lead_title = f"Interesse em {matched_property.code if matched_property else 'Imóvel'} - {name}"
    lead = PropertyLead.objects.create(
        title=lead_title,
        client=client,
        pipeline=pipeline,
        stage=first_stage,
        agent=assigned_agent,
        transaction_type=transaction_type,
        property_type=matched_property.property_type if matched_property else 'apartment',
        budget=budget if budget > 0 else (matched_property.sale_price if matched_property else Decimal('0.00')),
        preferred_location=preferred_location or (matched_property.neighborhood if matched_property else ''),
        origin=origin if origin in dict(PropertyLead.ORIGIN_CHOICES) else 'webhook',
        notes=notes,
        status='open',
        last_contact_at=timezone.now()
    )

    if matched_property:
        lead.interested_properties.add(matched_property)

    agent_info = f"ao corretor {assigned_agent.get_full_name() or assigned_agent.username}" if assigned_agent else "sem corretor atribuído"
    captor_info = f" (Captador do Imóvel [{matched_property.code}])" if matched_property and matched_property.captured_by == assigned_agent else " (Roleta de Corretores)"

    InteractionLog.objects.create(
        lead=lead,
        user=assigned_agent,
        action_type='note',
        content=f"📥 Lead captado via Webhook [{lead.get_origin_display()}]. Atribuído {agent_info}{captor_info}. Mensagem recebida: '{notes or 'Sem mensagem inicial'}'."
    )

    return JsonResponse({
        'success': True,
        'lead_id': lead.id,
        'title': lead.title,
        'assigned_agent': assigned_agent.username if assigned_agent else None,
        'assigned_agent_name': assigned_agent.get_full_name() if assigned_agent else None,
        'property_linked': matched_property.code if matched_property else None,
        'stage': first_stage.name if first_stage else None,
    }, status=201)


def _safe_cdata(val) -> str:
    """Previne quebra/injeção de bloco CDATA no XML."""
    return str(val or '').replace(']]>', ']]]]><![CDATA[>')


@rate_limit('export_xml_feed', 30, 60, scope='user_or_ip')
def portal_xml_feed_view(request):
    """
    Gera o Feed XML Padrão para Portais Imobiliários (ZAP, VivaReal, OLX, Imovelweb)
    - Inclui apenas imóveis disponíveis ('available')
    - BLINDAGEM ATIVA: Omite 100% dos dados de proprietário e taxas de comissão.
    """
    properties = Property.objects.filter(
        status='available'
    ).prefetch_related('images').select_related('captured_by').order_by('code')

    force_raw_xml = (
        request.GET.get('format', '').lower() == 'xml'
        or request.GET.get('raw', '') == '1'
    )
    accept_header = request.headers.get('Accept', '')
    if not force_raw_xml and 'text/html' in accept_header and not properties.exists():
        return render(request, 'properties/feed_empty.html')

    log_security_event(
        action='data_export',
        target='portal_xml_feed',
        details='Exportação pública de imóveis disponíveis via Feed XML de Portais.',
        request=request,
    )

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Carga xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        '  <Imoveis>'
    ]

    for prop in properties:
        xml_lines.append('    <Imovel>')
        xml_lines.append(f'      <CodigoImovel>{xml_escape(str(prop.code or ""))}</CodigoImovel>')
        xml_lines.append(f'      <TipoImovel>{xml_escape(str(prop.get_property_type_display()))}</TipoImovel>')
        xml_lines.append(f'      <Finalidade>{xml_escape(str(prop.get_transaction_type_display()))}</Finalidade>')
        xml_lines.append(f'      <Titulo><![CDATA[{_safe_cdata(prop.title)}]]></Titulo>')
        xml_lines.append(f'      <Descricao><![CDATA[{_safe_cdata(prop.description or "")}]]></Descricao>')
        xml_lines.append(f'      <PrecoVenda>{prop.sale_price:.2f}</PrecoVenda>')
        xml_lines.append(f'      <PrecoLocacao>{prop.rental_price:.2f}</PrecoLocacao>')
        xml_lines.append(f'      <TaxaCondominio>{prop.condo_fee:.2f}</TaxaCondominio>')
        xml_lines.append(f'      <ValorIPTU>{prop.iptu:.2f}</ValorIPTU>')
        xml_lines.append(f'      <Bairro><![CDATA[{_safe_cdata(prop.neighborhood)}]]></Bairro>')
        xml_lines.append(f'      <Cidade><![CDATA[{_safe_cdata(prop.city)}]]></Cidade>')
        xml_lines.append(f'      <Estado>{xml_escape(str(prop.state or ""))}</Estado>')
        xml_lines.append(f'      <AreaUtil>{prop.usable_area:.2f}</AreaUtil>')
        xml_lines.append(f'      <AreaTotal>{prop.total_area:.2f}</AreaTotal>')
        xml_lines.append(f'      <QtdQuartos>{prop.bedrooms}</QtdQuartos>')
        xml_lines.append(f'      <QtdSuites>{prop.suites}</QtdSuites>')
        xml_lines.append(f'      <QtdBanheiros>{prop.bathrooms}</QtdBanheiros>')
        xml_lines.append(f'      <QtdVagas>{prop.parking_spaces}</QtdVagas>')

        xml_lines.append('      <Diferenciais>')
        if prop.pets_allowed:
            xml_lines.append('        <Diferencial>Aceita Pets</Diferencial>')
        if prop.gourmet_balcony:
            xml_lines.append('        <Diferencial>Varanda Gourmet</Diferencial>')
        if prop.pool:
            xml_lines.append('        <Diferencial>Piscina</Diferencial>')
        if prop.gym:
            xml_lines.append('        <Diferencial>Academia</Diferencial>')
        if prop.elevator:
            xml_lines.append('        <Diferencial>Elevador</Diferencial>')
        if prop.morning_sun:
            xml_lines.append('        <Diferencial>Sol da Manhã</Diferencial>')
        xml_lines.append('      </Diferenciais>')

        xml_lines.append('      <Fotos>')
        for img in prop.images.all():
            if img.image:
                img_url = request.build_absolute_uri(img.image.url)
                principal = '1' if img.is_featured else '0'
                xml_lines.append(f'        <Foto Principal="{principal}"><![CDATA[{_safe_cdata(img_url)}]]></Foto>')
        xml_lines.append('      </Fotos>')

        xml_lines.append('    </Imovel>')

    xml_lines.append('  </Imoveis>')
    xml_lines.append('</Carga>')

    xml_content = '\n'.join(xml_lines)
    return HttpResponse(xml_content, content_type='application/xml; charset=utf-8')


@login_required(login_url='crm_login')
def whatsapp_template_list_view(request):
    """Tela de Gestão de Modelos de Mensagens do WhatsApp"""
    user_is_mgr = is_user_manager(request.user)
    templates_qs = WhatsAppTemplate.objects.all()
    if not user_is_mgr:
        templates_qs = templates_qs.filter(Q(created_by__isnull=True) | Q(created_by=request.user))

    contacts_qs = Person.objects.exclude(phone='')
    if not user_is_mgr:
        contacts_qs = contacts_qs.filter(assigned_agent=request.user)

    return render(request, 'templates_whatsapp/list.html', {
        'templates': templates_qs.order_by('category', 'title'),
        'categories': WhatsAppTemplate.CATEGORY_CHOICES,
        'contacts': contacts_qs.order_by('name'),
        'properties': Property.objects.all().order_by('-created_at'),
    })


@login_required(login_url='crm_login')
def whatsapp_template_save_view(request):
    """Criação ou Edição de Modelo de Mensagem do WhatsApp"""
    if request.method == 'POST':
        template_id = request.POST.get('template_id')
        title = request.POST.get('title')
        category = request.POST.get('category', 'welcome')
        content = request.POST.get('content', '')
        is_active = 'is_active' in request.POST or request.POST.get('is_active') == 'true'

        if template_id:
            tpl = get_object_or_404(WhatsAppTemplate, pk=template_id)
            if not can_edit_whatsapp_template(request.user, tpl):
                log_security_event(
                    action='unauthorized_access',
                    target=f'WhatsAppTemplate #{template_id}',
                    details='Tentativa bloqueada de edição de template global ou de outro corretor.',
                    request=request,
                )
                return HttpResponseForbidden('Você não tem permissão para editar este modelo.')
            tpl.title = title
            tpl.category = category
            tpl.content = content
            tpl.is_active = is_active
            tpl.save()
            messages.success(request, f'Modelo "{tpl.title}" atualizado com sucesso!')
        else:
            tpl = WhatsAppTemplate.objects.create(
                title=title,
                category=category,
                content=content,
                created_by=request.user,
                is_active=is_active,
            )
            messages.success(request, f'Modelo "{tpl.title}" cadastrado com sucesso!')

    return redirect('whatsapp_template_list')


@login_required(login_url='crm_login')
def whatsapp_template_delete_view(request, template_id):
    """Exclusão de Modelo de Mensagem do WhatsApp"""
    tpl = get_object_or_404(WhatsAppTemplate, pk=template_id)
    if not can_edit_whatsapp_template(request.user, tpl):
        log_security_event(
            action='unauthorized_access',
            target=f'WhatsAppTemplate #{template_id}',
            details='Tentativa bloqueada de exclusão de template global ou de outro corretor.',
            request=request,
        )
        return HttpResponseForbidden('Você não tem permissão para excluir este modelo.')

    if request.method == 'POST':
        title = tpl.title
        tpl.delete()
        messages.success(request, f'Modelo "{title}" removido com sucesso.')
    return redirect('whatsapp_template_list')


@login_required(login_url='crm_login')
def lead_template_message_view(request, lead_id, template_id):
    """Renderiza a mensagem de um template para o lead e redireciona direto ao WhatsApp"""
    lead = get_object_or_404(PropertyLead.objects.select_related('client', 'agent'), pk=lead_id)
    if not can_access_lead(request.user, lead):
        raise Http404("Lead não encontrado na sua carteira.")

    template = get_object_or_404(WhatsAppTemplate, pk=template_id)
    if not is_user_manager(request.user) and template.created_by_id not in (None, request.user.id):
        raise Http404("Modelo não encontrado.")

    wa_url = template.render_url(lead, agent=request.user)

    InteractionLog.objects.create(
        lead=lead,
        user=request.user,
        action_type='whatsapp_sent',
        content=f"💬 Disparo de WhatsApp utilizando o modelo '{template.title}'."
    )
    lead.last_contact_at = timezone.now()
    lead.save()

    if wa_url and wa_url != '#':
        return redirect(wa_url)

    messages.error(request, 'Telefone do cliente inválido para WhatsApp.')
    return redirect('kanban')


# ==========================================
# MÓDULO DE CONTATOS & CONFORMIDADE LGPD
# ==========================================

@login_required(login_url='crm_login')
@rate_limit('search_contacts', 30, 60, scope='user_or_ip')
def contact_list_view(request):
    """Listagem de contatos / clientes com busca e filtros — vinculados à conta"""
    user_is_mgr = is_user_manager(request.user)
    base_qs = Person.objects.select_related('assigned_agent').all()

    if not user_is_mgr:
        base_qs = base_qs.filter(assigned_agent=request.user)

    client_type = request.GET.get('type', '')
    if client_type:
        base_qs = base_qs.filter(client_type=client_type)

    agent_id = request.GET.get('agent', '')
    if user_is_mgr and agent_id:
        base_qs = base_qs.filter(assigned_agent_id=agent_id)

    qs = base_qs
    q = request.GET.get('q', '').strip()
    if q:
        q_digits = re.sub(r'\D', '', q)
        matched_doc_ids = []
        if len(q_digits) >= 3:
            for person_obj in base_qs.exclude(document__isnull=True).exclude(document=''):
                doc_plain = person_obj.document or ''
                doc_digits = re.sub(r'\D', '', doc_plain)
                if q.lower() in doc_plain.lower() or q_digits in doc_digits:
                    matched_doc_ids.append(person_obj.id)

        qs = base_qs.filter(
            Q(name__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q) |
            Q(id__in=matched_doc_ids)
        )

    context = {
        'contacts': qs.order_by('-created_at'),
        'q': q,
        'current_type': client_type,
        'current_agent': agent_id,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'total': qs.count(),
        'is_manager': user_is_mgr,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'contacts/partials/contact_rows.html', context)
    return render(request, 'contacts/list.html', context)


@login_required(login_url='crm_login')
def contact_create_view(request):
    """Cadastro de novo contato no frontend"""
    user_is_mgr = is_user_manager(request.user)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip() or None
        secondary_phone = request.POST.get('secondary_phone', '').strip() or None
        document = request.POST.get('document', '').strip() or None
        client_type = request.POST.get('client_type', 'buyer')
        notes = request.POST.get('notes', '').strip() or None
        agent_id = request.POST.get('assigned_agent') or None
        lgpd_origin = request.POST.get('lgpd_origin', '').strip() or 'cadastro_manual'

        if 'lgpd_form_present' in request.POST:
            lgpd_consent = 'lgpd_consent' in request.POST
        else:
            lgpd_consent = True

        if not name or not phone:
            messages.error(request, 'Nome e telefone são obrigatórios.')
            return redirect('contact_create')

        if user_is_mgr and agent_id:
            agent = User.objects.filter(pk=agent_id).first() or request.user
        else:
            agent = request.user

        person = Person.objects.create(
            name=name,
            phone=phone,
            email=email,
            secondary_phone=secondary_phone,
            document=document,
            client_type=client_type,
            notes=notes,
            assigned_agent=agent,
            lgpd_consent=lgpd_consent,
            lgpd_consent_at=timezone.now() if lgpd_consent else None,
            lgpd_origin=lgpd_origin,
        )
        messages.success(request, f'Contato "{person.name}" cadastrado com sucesso.')
        return redirect('contact_list')

    return render(request, 'contacts/form.html', {
        'contact': None,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'form_title': 'Novo Contato',
        'form_action': 'contact_create',
        'is_manager': user_is_mgr,
    })


@login_required(login_url='crm_login')
def contact_edit_view(request, pk):
    """Edição de contato no frontend (isolada por carteira)"""
    person = get_object_or_404(Person, pk=pk)
    user_is_mgr = is_user_manager(request.user)

    if not can_access_person(request.user, person):
        log_security_event(
            action='unauthorized_access',
            target=f'Person #{pk}',
            details='Tentativa bloqueada de edição de contato de outra carteira.',
            request=request,
        )
        raise Http404("Contato não encontrado na sua carteira.")

    if request.method == 'POST':
        person.name = request.POST.get('name', '').strip()
        person.phone = request.POST.get('phone', '').strip()
        person.email = request.POST.get('email', '').strip() or None
        person.secondary_phone = request.POST.get('secondary_phone', '').strip() or None
        person.document = request.POST.get('document', '').strip() or None
        person.client_type = request.POST.get('client_type', person.client_type)
        person.notes = request.POST.get('notes', '').strip() or None

        if user_is_mgr:
            agent_id = request.POST.get('assigned_agent') or None
            person.assigned_agent = User.objects.filter(pk=agent_id).first() if agent_id else None

        if 'lgpd_form_present' in request.POST:
            new_consent = 'lgpd_consent' in request.POST
            if new_consent and not person.lgpd_consent_at:
                person.lgpd_consent_at = timezone.now()
            person.lgpd_consent = new_consent
            origin_val = request.POST.get('lgpd_origin', '').strip()
            if origin_val:
                person.lgpd_origin = origin_val

        person.save()
        messages.success(request, f'Contato "{person.name}" atualizado.')
        return redirect('contact_list')

    return render(request, 'contacts/form.html', {
        'contact': person,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'form_title': f'Editar — {person.name}',
        'form_action': 'contact_edit',
        'is_manager': user_is_mgr,
    })


@login_required(login_url='crm_login')
def contact_detail_view(request, pk):
    """Ficha do contato (isolada por carteira)"""
    person = get_object_or_404(
        Person.objects.select_related('assigned_agent').prefetch_related('leads', 'owned_properties'),
        pk=pk
    )
    if not can_access_person(request.user, person):
        log_security_event(
            action='unauthorized_access',
            target=f'Person #{pk}',
            details='Tentativa bloqueada de visualização de contato de outra carteira.',
            request=request,
        )
        raise Http404("Contato não encontrado na sua carteira.")

    prefs = getattr(person, 'preferences', None)
    return render(request, 'contacts/detail.html', {
        'contact': person,
        'person': person,
        'prefs': prefs,
        'leads': person.leads.select_related('stage', 'pipeline').all()[:20],
        'owned': person.owned_properties.all()[:20],
        'is_manager': is_user_manager(request.user),
    })


@login_required(login_url='crm_login')
def client_preferences_save_view(request, person_id):
    """Salva/atualiza as preferências de busca do cliente (ClientPreference)"""
    if request.method != 'POST':
        safe_ref = get_safe_redirect_url(request, request.META.get('HTTP_REFERER'), reverse('contact_list'))
        return redirect(safe_ref)

    person = get_object_or_404(Person, pk=person_id)
    if not can_access_person(request.user, person):
        raise Http404("Contato não encontrado na sua carteira.")

    raw_locations = request.POST.get('preferred_locations', '')
    preferred_locations = [x.strip() for x in raw_locations.split(',') if x.strip()]
    property_types = request.POST.getlist('property_types')

    defaults = {
        'transaction_type': request.POST.get('transaction_type', 'buy'),
        'property_types': property_types,
        'preferred_locations': preferred_locations,
        'min_price': parse_brl_decimal(request.POST.get('min_price')),
        'max_price': parse_brl_decimal(request.POST.get('max_price')),
        'payment_method': request.POST.get('payment_method', 'cash'),
        'min_bedrooms': int(request.POST.get('min_bedrooms') or 0),
        'min_suites': int(request.POST.get('min_suites') or 0),
        'min_parking_spaces': int(request.POST.get('min_parking_spaces') or 0),
        'min_area_m2': request.POST.get('min_area_m2') or 0.00,
        'pets_allowed': 'pets_allowed' in request.POST,
        'gourmet_balcony': 'gourmet_balcony' in request.POST,
        'pool_or_leisure': 'pool_or_leisure' in request.POST,
        'morning_sun': 'morning_sun' in request.POST,
        'elevator': 'elevator' in request.POST,
        'notes': request.POST.get('notes', '').strip(),
    }

    ClientPreference.objects.update_or_create(person=person, defaults=defaults)
    messages.success(request, f'Preferências de {person.name} atualizadas com sucesso!')
    safe_ref = get_safe_redirect_url(request, request.META.get('HTTP_REFERER'), reverse('contact_list'))
    return redirect(safe_ref)


@login_required(login_url='crm_login')
def contact_anonymize_lgpd_view(request, pk):
    """
    Ação restrita a administradores/gestores para Anonimizar Titular (LGPD).
    Substitui nome por 'Titular Anonimizado #<id>' e limpa telefone, e-mail, documento
    e observações pessoais sem apagar o registro histórico de métricas do CRM.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if not is_user_manager(request.user):
        log_security_event(
            action='unauthorized_access',
            target=f'LGPD Anonymize Person #{pk}',
            details='Usuário não-administrador tentou executar anonimização LGPD.',
            request=request,
        )
        return HttpResponseForbidden('Apenas administradores/gestores podem anonimizar titulares (LGPD).')

    person = get_object_or_404(Person, pk=pk)
    anonymize_person_lgpd(person, actor=request.user, request=request)
    messages.success(request, f'Titular #{person.id} anonimizado com sucesso em conformidade com a LGPD.')
    return redirect('contact_detail', pk=person.id)


@login_required(login_url='crm_login')
@rate_limit('export_lgpd_data', 30, 60, scope='user_or_ip')
def contact_export_lgpd_view(request, pk):
    """Exporta relatório estruturado de portabilidade LGPD do titular e registra auditoria."""
    if not is_user_manager(request.user):
        log_security_event(
            action='unauthorized_access',
            target=f'LGPD Export Person #{pk}',
            details='Usuário não-administrador tentou exportar dados pessoais de titular.',
            request=request,
        )
        return HttpResponseForbidden('Apenas administradores/gestores podem exportar dados de titulares.')

    person = get_object_or_404(Person.objects.prefetch_related('leads'), pk=pk)
    log_security_event(
        action='data_export',
        target=f'Person #{person.id}',
        details='Exportação de portabilidade LGPD solicitada para o titular.',
        request=request,
    )

    payload = {
        'id': person.id,
        'name': person.name,
        'email': person.email,
        'phone': person.phone,
        'secondary_phone': person.secondary_phone,
        'masked_document': person.masked_document,
        'client_type': person.get_client_type_display(),
        'lgpd_consent': person.lgpd_consent,
        'lgpd_consent_at': person.lgpd_consent_at.isoformat() if person.lgpd_consent_at else None,
        'lgpd_origin': person.lgpd_origin,
        'created_at': person.created_at.isoformat(),
        'leads_count': person.leads.count(),
    }
    response = JsonResponse(payload, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    response['Content-Disposition'] = f'attachment; filename="titular-{person.id}-lgpd.json"'
    return response


def privacy_policy_view(request):
    """Rota pública /privacidade/ com a Política de Privacidade e Proteção de Dados (LGPD) do ImobiCRM."""
    return render(request, 'auth/privacy_policy.html')


# ==========================================
# AUTENTICAÇÃO (Frontend)
# ==========================================

@rate_limit('login_post', 5, 60, methods=('POST',), scope='ip')
def crm_login_view(request):
    """Login por e-mail/usuário + senha (frontend CRM) protegido por Rate Limit (5 POST/min por IP)"""
    if request.user.is_authenticated:
        safe_next = get_safe_redirect_url(request, request.GET.get('next'), reverse('kanban'))
        return redirect(safe_next)

    error = None
    if request.method == 'POST':
        login_id = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = None
        if login_id and password:
            user = authenticate(request, username=login_id, password=password)
            if user is None and '@' in login_id:
                u = User.objects.filter(email__iexact=login_id).first()
                if u and u.username != login_id:
                    user = authenticate(request, username=u.username, password=password)
            if user is not None:
                login(request, user)
                if not hasattr(user, 'profile') or not UserProfile.objects.filter(user=user).exists():
                    UserProfile.objects.get_or_create(user=user, defaults={'role': 'agent'})
                log_security_event(
                    action='login_success',
                    target=user.username,
                    details='Login realizado com sucesso no portal ImobiCRM.',
                    user=user,
                    request=request,
                )
                raw_next = request.POST.get('next') or request.GET.get('next') or '/'
                return redirect(get_safe_redirect_url(request, raw_next, '/'))
            error = 'E-mail/usuário ou senha incorretos.'
        else:
            log_security_event(
                action='login_failed',
                target=login_id or 'credencial_vazia',
                details='Tentativa de login com campos obrigatórios em branco.',
                request=request,
            )
            error = 'Preencha e-mail e senha.'

    return render(request, 'auth/login.html', {
        'error': error,
        'next': request.GET.get('next', ''),
        'google_enabled': bool(getattr(settings, 'GOOGLE_CLIENT_ID', '')),
    })


def crm_logout_view(request):
    logout(request)
    messages.success(request, 'Você saiu da conta.')
    return redirect('crm_login')


def google_login_start(request):
    """Inicia OAuth Google"""
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    if not client_id:
        messages.error(request, 'Login com Google não configurado. Defina GOOGLE_CLIENT_ID nas variáveis de ambiente.')
        return redirect('crm_login')

    state = secrets.token_urlsafe(24)
    request.session['google_oauth_state'] = state
    request.session['google_oauth_next'] = get_safe_redirect_url(request, request.GET.get('next'), '/')

    redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', '') or 'http://127.0.0.1:8000/login/google/callback/'
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return redirect(url)


def google_login_callback(request):
    """Callback OAuth Google — cria/associa usuário e vincula sessão"""
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        messages.error(request, 'Google OAuth incompleto (CLIENT_ID/SECRET).')
        return redirect('crm_login')

    err = request.GET.get('error')
    if err:
        messages.error(request, f'Google recusou o login: {err}')
        return redirect('crm_login')

    state = request.GET.get('state', '')
    expected_state = request.session.get('google_oauth_state') or ''
    if not state or not expected_state or not hmac.compare_digest(state, expected_state):
        messages.error(request, 'Estado OAuth inválido. Tente novamente.')
        return redirect('crm_login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Código Google ausente.')
        return redirect('crm_login')

    redirect_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', '') or 'http://127.0.0.1:8000/login/google/callback/'
    token_data = urllib.parse.urlencode({
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode()

    try:
        req = urllib.request.Request(
            'https://oauth2.googleapis.com/token',
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read().decode())
        access_token = tokens.get('access_token')
        if not access_token:
            raise ValueError('Sem access_token')

        req2 = urllib.request.Request(
            'https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
        )
        with urllib.request.urlopen(req2, timeout=15) as resp2:
            info = json.loads(resp2.read().decode())
    except Exception as e:
        messages.error(request, f'Falha ao autenticar com Google: {e}')
        return redirect('crm_login')

    email = (info.get('email') or '').lower().strip()
    if not email:
        messages.error(request, 'Google não retornou e-mail.')
        return redirect('crm_login')

    given = info.get('given_name') or ''
    family = info.get('family_name') or ''

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        base_username = email.split('@')[0][:30]
        username = base_username
        i = 1
        while User.objects.filter(username=username).exists():
            username = f'{base_username}{i}'
            i += 1
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=given,
            last_name=family,
        )
        user.set_unusable_password()
        user.save()
        from django.contrib.auth.models import Group
        group, _ = Group.objects.get_or_create(name='Corretores')
        user.groups.add(group)
    else:
        if given and not user.first_name:
            user.first_name = given
            user.last_name = family or user.last_name
            user.save(update_fields=['first_name', 'last_name'])

    UserProfile.objects.get_or_create(user=user, defaults={'role': 'agent'})
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    log_security_event(
        action='login_success',
        target=user.username,
        details='Login realizado via Google OAuth2.',
        user=user,
        request=request,
    )

    next_url = get_safe_redirect_url(request, request.session.pop('google_oauth_next', '/'), '/')
    request.session.pop('google_oauth_state', None)
    messages.success(request, f'Bem-vindo, {user.get_full_name() or user.email}!')
    return redirect(next_url)


# ==========================================
# INTEGRAÇÃO DE CALENDÁRIOS (Google & Apple)
# ==========================================

@login_required(login_url='crm_login')
def google_calendar_connect_view(request):
    """Inicia o fluxo OAuth2 para autorizar o Google Calendar"""
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '')
    if not client_id:
        messages.error(request, 'Google Client ID não configurado no servidor.')
        return redirect('visit_schedule')

    redirect_uri = request.build_absolute_uri(reverse('google_calendar_callback'))
    state = secrets.token_urlsafe(32)
    request.session['google_cal_oauth_state'] = state

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'https://www.googleapis.com/auth/calendar.events',
        'access_type': 'offline',
        'prompt': 'consent',
        'state': state,
    }
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return redirect(auth_url)


@login_required(login_url='crm_login')
def google_calendar_callback_view(request):
    """Callback do OAuth2 do Google Calendar: armazena tokens cifrados em repouso e roda sync inicial"""
    expected_state = request.session.pop('google_cal_oauth_state', None) or ''
    state = request.GET.get('state', '')
    if not expected_state or not state or not hmac.compare_digest(state, expected_state):
        messages.error(request, 'Sessão de autorização inválida ou expirada.')
        return redirect('visit_schedule')

    code = request.GET.get('code')
    if not code:
        err = request.GET.get('error', 'desconhecido')
        messages.error(request, f'Permissão negada pelo Google: {err}')
        return redirect('visit_schedule')

    redirect_uri = request.build_absolute_uri(reverse('google_calendar_callback'))
    token_url = 'https://oauth2.googleapis.com/token'
    payload = urllib.parse.urlencode({
        'code': code,
        'client_id': getattr(settings, 'GOOGLE_CLIENT_ID', ''),
        'client_secret': getattr(settings, 'GOOGLE_CLIENT_SECRET', ''),
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }).encode('utf-8')

    try:
        req = urllib.request.Request(token_url, data=payload, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        messages.error(request, f'Erro ao obter tokens do Google: {e}')
        return redirect('visit_schedule')

    access_token = data.get('access_token')
    refresh_token = data.get('refresh_token')
    expires_in = data.get('expires_in', 3600)
    expiry = timezone.now() + timedelta(seconds=expires_in)

    integration, _ = CalendarIntegration.objects.get_or_create(user=request.user)
    integration.google_access_token = access_token
    if refresh_token:
        integration.google_refresh_token = refresh_token
    integration.google_token_expiry = expiry
    integration.google_connected = True
    integration.save()

    from core.calendar_sync import initial_sync_future_visits
    synced = initial_sync_future_visits(request.user)

    messages.success(request, f'Google Agenda conectado com sucesso! {synced} visita(s) futura(s) sincronizada(s).')
    return redirect('visit_schedule')


@login_required(login_url='crm_login')
def google_calendar_sync_now_view(request):
    """Dispara sincronização manual imediata das visitas futuras para o Google Agenda"""
    from core.calendar_sync import initial_sync_future_visits
    synced = initial_sync_future_visits(request.user)
    messages.success(request, f'Sincronização concluída! {synced} visita(s) atualizada(s) no seu Google Agenda.')
    return redirect('visit_schedule')


@rate_limit('export_ical_feed', 30, 60, scope='user_or_ip')
def visit_ical_feed_view(request, token):
    """
    Entrega o feed iCalendar (.ics) de visitas exclusivo para o corretor (ou gestor).
    Permite assinatura única via webcal:// no Apple Calendário, Outlook ou Google Calendar.
    """
    integration = get_object_or_404(CalendarIntegration.objects.select_related('user'), ical_token=token)
    user = integration.user

    log_security_event(
        action='data_export',
        target=f'visit_ical_feed ({user.username})',
        details='Sincronização/exportação do feed iCal (.ics) de visitas.',
        user=user,
        request=request,
    )

    is_mgr = is_user_manager(user)
    if is_mgr:
        visits = PropertyVisit.objects.all()
    else:
        visits = PropertyVisit.objects.filter(agent=user)

    visits = visits.exclude(status='canceled').select_related('visit_property', 'lead', 'lead__client', 'agent')

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ImobiCRM//Agenda de Visitas//PT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Visitas ImobiCRM ({user.first_name or user.username})",
        "X-WR-TIMEZONE:America/Sao_Paulo",
    ]

    def format_dt(dt):
        return dt.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    def clean_ics_text(val):
        return str(val or '').replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')

    for v in visits:
        prop = v.visit_property
        client = v.lead.client.name if v.lead and v.lead.client else "Cliente"
        phone = v.lead.client.phone if v.lead and v.lead.client else ""

        start_str = format_dt(v.scheduled_date)
        end_str = format_dt(v.scheduled_date + timedelta(hours=1))
        created_str = format_dt(v.created_at)

        location = f"{prop.street or ''} {prop.number or ''}, {prop.neighborhood or ''} - {prop.city or ''}".strip(', -')
        summary = f"Visita: [{prop.code}] {prop.title} - {client}"
        description = (
            f"Visita agendada pelo ImobiCRM\\n"
            f"Imóvel: {clean_ics_text(prop.code)} - {clean_ics_text(prop.title)}\\n"
            f"Cliente: {clean_ics_text(client)} ({clean_ics_text(phone)})\\n"
            f"Ponto de Encontro: {clean_ics_text(v.meeting_point or 'No imóvel')}\\n"
            f"Status: {clean_ics_text(v.get_status_display())}\\n"
            f"Chave: {clean_ics_text(v.get_key_status_display())}"
        )

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:imobicrm-visit-{v.id}@imobicrm",
            f"DTSTAMP:{created_str}",
            f"DTSTART:{start_str}",
            f"DTEND:{end_str}",
            f"SUMMARY:{clean_ics_text(summary)}",
            f"LOCATION:{clean_ics_text(location)}",
            f"DESCRIPTION:{description}",
            "STATUS:CONFIRMED",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(lines) + "\r\n"

    response = HttpResponse(ics_content, content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'inline; filename="visitas-{user.username}.ics"'
    return response

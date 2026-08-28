import json
import random
from datetime import timedelta, datetime
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Prefetch, Sum, Count, Avg
from django.utils import timezone
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    Person, Stage, PropertyLead, Pipeline,
    Property, PropertyImage, Activity, InteractionLog,
    PropertyVisit, UserProfile, WhatsAppTemplate
)
from .serializers import (
    PersonSerializer, StageSerializer, PropertyLeadSerializer,
    PropertySerializer, ActivitySerializer, PropertyVisitSerializer
)
from .services.match_engine import (
    get_matching_properties_for_lead,
    get_matching_leads_for_property
)
from .services.lead_router import assign_lead_smart
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout

# ==========================================
# REST API VIEWSETS (DRF)
# ==========================================

class PersonViewSet(viewsets.ModelViewSet):
    queryset = Person.objects.all()
    serializer_class = PersonSerializer

class StageViewSet(viewsets.ModelViewSet):
    queryset = Stage.objects.all()
    serializer_class = StageSerializer

class PropertyLeadViewSet(viewsets.ModelViewSet):
    queryset = PropertyLead.objects.all()
    serializer_class = PropertyLeadSerializer

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


# ==========================================
# VIEWS DO FUNIL KANBAN & VISÃO 360° DO LEAD
# ==========================================

def kanban_view(request):
    """View do Funil Kanban com Filtros Reativos HTMX"""
    pipeline = Pipeline.objects.filter(is_default=True).first() or Pipeline.objects.first()
    
    q = request.GET.get('q', '').strip()
    agent_id = request.GET.get('agent_id', '').strip()
    transaction_type = request.GET.get('transaction_type', '').strip()

    lead_filter = Q()
    if q:
        lead_filter &= (Q(title__icontains=q) | Q(client__name__icontains=q) | Q(client__phone__icontains=q))
    if agent_id:
        lead_filter &= Q(agent_id=agent_id)
    if transaction_type:
        lead_filter &= Q(transaction_type=transaction_type)

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
    }

    if request.headers.get('HX-Request'):
        return render(request, 'kanban/partials/board.html', context)

    return render(request, 'kanban/kanban.html', context)


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
    stages = Stage.objects.filter(pipeline=lead.pipeline).order_by('order')
    matching_properties = get_matching_properties_for_lead(lead)
    all_properties = Property.objects.filter(status__in=['available', 'draft', 'reserved']).order_by('code')
    active_templates = WhatsAppTemplate.objects.filter(is_active=True).order_by('category', 'title')

    return render(request, 'kanban/partials/lead_drawer.html', {
        'lead': lead,
        'stages': stages,
        'matching_properties': matching_properties,
        'all_properties': all_properties,
        'active_templates': active_templates,
    })


def lead_toggle_property_link(request, lead_id, property_id):
    """Vincula ou desvincula um imóvel do lead a partir da seção de Match"""
    lead = get_object_or_404(PropertyLead, pk=lead_id)
    prop = get_object_or_404(Property, pk=property_id)

    if prop in lead.interested_properties.all():
        lead.interested_properties.remove(prop)
        action_msg = f"Imóvel [{prop.code}] desvinculado das opções de interesse."
    else:
        lead.interested_properties.add(prop)
        action_msg = f"Imóvel [{prop.code}] ({prop.title}) vinculado com sucesso ao perfil de interesse do cliente."

    InteractionLog.objects.create(
        lead=lead,
        user=request.user if request.user.is_authenticated else None,
        action_type='note',
        content=action_msg
    )

    matching_properties = get_matching_properties_for_lead(lead)
    return render(request, 'kanban/partials/lead_matches.html', {
        'lead': lead,
        'matching_properties': matching_properties
    })


def lead_add_note_view(request, pk):
    """Adiciona uma nova anotação/interação na timeline do lead e atualiza a última interação"""
    lead = get_object_or_404(PropertyLead, pk=pk)

    if request.method == 'POST':
        action_type = request.POST.get('action_type', 'note')
        content = request.POST.get('content', '').strip()

        if content:
            InteractionLog.objects.create(
                lead=lead,
                user=request.user if request.user.is_authenticated else None,
                action_type=action_type,
                content=content
            )
            lead.last_contact_at = timezone.now()
            lead.save()

    return render(request, 'kanban/partials/timeline.html', {'lead': lead})


def lead_move_stage_view(request, pk):
    """Move o lead entre os estágios do funil, salvando motivo de perda ou sucesso"""
    lead = get_object_or_404(PropertyLead, pk=pk)

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
                    lead.lost_reason = lost_reason
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
                user=request.user if request.user.is_authenticated else None,
                action_type='stage_change',
                content=log_text
            )

            return JsonResponse({'success': True, 'stage_name': new_stage.name})
        except Stage.DoesNotExist:
            return JsonResponse({'error': 'Estágio não encontrado'}, status=404)

    return JsonResponse({'error': 'Método inválido'}, status=405)


def lead_quick_create_view(request):
    """Cadastro Rápido de Novo Lead no Funil (Etapa Inicial)"""
    if request.method == 'POST':
        title = request.POST.get('title')
        client_name = request.POST.get('client_name')
        client_phone = request.POST.get('client_phone')
        transaction_type = request.POST.get('transaction_type', 'buy')
        property_type = request.POST.get('property_type', 'apartment')
        budget = request.POST.get('budget') or 0.00
        origin = request.POST.get('origin', 'manual')

        client, _ = Person.objects.get_or_create(
            phone=client_phone,
            defaults={
                'name': client_name,
                'client_type': 'buyer' if transaction_type == 'buy' else 'renter',
                'assigned_agent': request.user if request.user.is_authenticated else None
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
            agent=request.user if request.user.is_authenticated else None,
            last_contact_at=timezone.now()
        )

        InteractionLog.objects.create(
            lead=lead,
            user=request.user if request.user.is_authenticated else None,
            action_type='note',
            content=f"Lead cadastrado no sistema via {lead.get_origin_display()}."
        )

        messages.success(request, f'Lead "{lead.title}" adicionado com sucesso ao funil!')
        return redirect('kanban')

    return redirect('kanban')


# ==========================================
# VIEWS DO ESTOQUE DE IMÓVEIS
# ==========================================

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


def property_detail_view(request, pk):
    """Ficha Completa do Imóvel com Match de Leads Interessados e Proteção de Privacidade"""
    property_obj = get_object_or_404(Property.objects.prefetch_related('images'), pk=pk)
    can_view_owner = property_obj.can_view_owner(request.user)
    matching_leads = get_matching_leads_for_property(property_obj)

    return render(request, 'properties/detail.html', {
        'property': property_obj,
        'can_view_owner': can_view_owner,
        'matching_leads': matching_leads,
    })


def property_create_view(request):
    """Cadastro Completo de Imóvel"""
    if request.method == 'POST':
        code = request.POST.get('code')
        title = request.POST.get('title')
        property_type = request.POST.get('property_type', 'apartment')
        transaction_type = request.POST.get('transaction_type', 'sale')
        sale_price = request.POST.get('sale_price') or 0.00
        rental_price = request.POST.get('rental_price') or 0.00
        condo_fee = request.POST.get('condo_fee') or 0.00
        iptu = request.POST.get('iptu') or 0.00
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
            captured_by=request.user if request.user.is_authenticated else None
        )

        messages.success(request, f'Imóvel {prop.code} cadastrado com sucesso!')
        return redirect('property_detail', pk=prop.id)

    return render(request, 'properties/form.html', {'property': None})


def property_edit_view(request, pk):
    """Edição Completa de Imóvel"""
    prop = get_object_or_404(Property, pk=pk)

    if request.method == 'POST':
        prop.code = request.POST.get('code')
        prop.title = request.POST.get('title')
        prop.property_type = request.POST.get('property_type', 'apartment')
        prop.transaction_type = request.POST.get('transaction_type', 'sale')
        prop.sale_price = request.POST.get('sale_price') or 0.00
        prop.rental_price = request.POST.get('rental_price') or 0.00
        prop.condo_fee = request.POST.get('condo_fee') or 0.00
        prop.iptu = request.POST.get('iptu') or 0.00
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

        prop.save()
        messages.success(request, f'Imóvel {prop.code} atualizado com sucesso!')
        return redirect('property_detail', pk=prop.id)

    return render(request, 'properties/form.html', {'property': prop})


def property_quick_create_view(request):
    """Captação Rápida de Imóvel (30 segundos) - Salva como Rascunho"""
    if request.method == 'POST':
        title = request.POST.get('title')
        transaction_type = request.POST.get('transaction_type', 'sale')
        property_type = request.POST.get('property_type', 'apartment')
        neighborhood = request.POST.get('neighborhood', '')
        price = request.POST.get('price') or 0.00
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
                    'assigned_agent': request.user if request.user.is_authenticated else None
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
            captured_by=request.user if request.user.is_authenticated else None
        )

        messages.success(request, f'⚡ Captação rápida salva como Rascunho ({prop.code})! Complete a ficha quando desejar.')
        return redirect('property_detail', pk=prop.id)

    return redirect('property_list')


# ==========================================
# FASE 5: GESTÃO DE VISITAS & CONTROLE DE CHAVES
# ==========================================

def visit_schedule_view(request):
    """Tela de Calendário Visual Semanal de Visitas com KPIs e Controle de Chaves"""
    week_offset = int(request.GET.get('week_offset', 0))
    selected_agent = request.GET.get('agent', '')

    today = timezone.now().date()
    start_of_current_week = today - timedelta(days=today.weekday()) # Segunda-feira
    week_start = start_of_current_week + timedelta(weeks=week_offset)
    week_end = week_start + timedelta(days=6) # Domingo

    # Filtro base
    visits_qs = PropertyVisit.objects.filter(
        scheduled_date__date__range=[week_start, week_end]
    ).select_related('lead', 'lead__client', 'visit_property', 'agent').order_by('scheduled_date')

    if selected_agent:
        visits_qs = visits_qs.filter(agent_id=selected_agent)

    # Montagem dos 7 dias da semana
    weekday_names = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira', 'Sábado', 'Domingo']
    days_data = []
    for i in range(7):
        current_day = week_start + timedelta(days=i)
        day_visits = [v for v in visits_qs if v.scheduled_date.date() == current_day]
        days_data.append({
            'date': current_day,
            'weekday_name': weekday_names[i],
            'is_today': current_day == today,
            'visits': day_visits
        })

    # KPIs da semana
    total_visits_week = visits_qs.count()
    confirmed_visits_count = visits_qs.filter(status='confirmed').count()
    keys_with_agent_count = PropertyVisit.objects.filter(key_status='with_agent').count()

    # Chaves em atraso (>4h ou após visita)
    all_active_visits = PropertyVisit.objects.filter(key_status='with_agent').select_related('visit_property', 'agent', 'lead__client')
    overdue_keys = [v for v in all_active_visits if v.is_key_overdue]
    overdue_keys_count = len(overdue_keys)

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')
    active_leads = PropertyLead.objects.filter(status='open').select_related('client').order_by('-created_at')[:30]
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

    return render(request, 'visits/calendar.html', context)


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
        prop = get_object_or_404(Property, pk=property_id)
        agent = User.objects.filter(pk=agent_id).first() or request.user if request.user.is_authenticated else None

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

        # Se a chave já saiu com o corretor
        if key_status == 'with_agent':
            visit.key_withdrawn_at = timezone.now()
            visit.save()

        # Vincula o imóvel ao lead caso não esteja
        if prop not in lead.interested_properties.all():
            lead.interested_properties.add(prop)

        # Automação de Estágio: Avança para 'Visita Agendada' se o lead estiver em 'Novo Lead' ou 'Em Atendimento'
        visit_stage = Stage.objects.filter(pipeline=lead.pipeline, stage_type='open', name__icontains='Visita').first()
        if visit_stage and lead.stage != visit_stage:
            lead.stage = visit_stage
            lead.save()

        lead.last_contact_at = timezone.now()
        lead.save()

        InteractionLog.objects.create(
            lead=lead,
            user=request.user if request.user.is_authenticated else agent,
            action_type='visit_scheduled',
            content=f"Visita agendada para {scheduled_date.strftime('%d/%m/%Y às %H:%M')} no imóvel [{prop.code}] ({prop.title})."
        )

        messages.success(request, f'Visita ao imóvel {prop.code} agendada com sucesso!')
        return redirect('visit_schedule')

    return redirect('visit_schedule')


def visit_create_from_lead_view(request, lead_id):
    """Criação de Visita a partir do Drawer do Lead com Redirecionamento Direto ao WhatsApp"""
    lead = get_object_or_404(PropertyLead, pk=lead_id)

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
            agent=lead.agent or (request.user if request.user.is_authenticated else None),
            scheduled_date=scheduled_date,
            status='confirmed',
            meeting_point=meeting_point,
            key_status='concierge'
        )

        if prop not in lead.interested_properties.all():
            lead.interested_properties.add(prop)

        # Automação de Estágio: Avança para 'Visita Agendada'
        visit_stage = Stage.objects.filter(pipeline=lead.pipeline, stage_type='open', name__icontains='Visita').first()
        if visit_stage and lead.stage != visit_stage:
            lead.stage = visit_stage
            lead.save()

        lead.last_contact_at = timezone.now()
        lead.save()

        InteractionLog.objects.create(
            lead=lead,
            user=request.user if request.user.is_authenticated else lead.agent,
            action_type='visit_scheduled',
            content=f"Visita marcada para {scheduled_date.strftime('%d/%m/%Y às %H:%M')} no imóvel [{prop.code}] {prop.neighborhood}."
        )

        messages.success(request, f'Visita ao imóvel {prop.code} agendada com sucesso!')
        
        # Redireciona para o WhatsApp de confirmação caso disponível
        if visit.whatsapp_confirmation_url and visit.whatsapp_confirmation_url != '#':
            return redirect(visit.whatsapp_confirmation_url)

        return redirect('kanban')

    return redirect('kanban')


def visit_feedback_form_view(request, visit_id):
    """Carrega o formulário HTMX de feedback da visita"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'lead__client', 'visit_property'), pk=visit_id)
    return render(request, 'visits/partials/visit_feedback_form.html', {'visit': visit})


def visit_feedback_save_view(request, visit_id):
    """Salva o feedback pós-visita e executa a automação de mover o lead para 'Proposta / Negociação'"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'lead__pipeline', 'visit_property'), pk=visit_id)

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

        # REGRA DE OURO: Se o cliente tem intenção de fazer proposta, move o lead automaticamente
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
                    user=request.user if request.user.is_authenticated else None,
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
                user=request.user if request.user.is_authenticated else None,
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


def visit_key_action_view(request, visit_id):
    """Registra a retirada ou devolução de chaves com timestamp auditável"""
    visit = get_object_or_404(PropertyVisit.objects.select_related('lead', 'visit_property', 'agent'), pk=visit_id)

    if request.method == 'POST':
        action_type = request.POST.get('action_type')

        if action_type == 'withdraw':
            visit.key_status = 'with_agent'
            visit.key_withdrawn_at = timezone.now()
            visit.key_returned_at = None
            visit.save()

            InteractionLog.objects.create(
                lead=visit.lead,
                user=request.user if request.user.is_authenticated else visit.agent,
                action_type='key_action',
                content=f"🔑 Chave do imóvel [{visit.visit_property.code}] RETIRADA pelo corretor {visit.agent.get_full_name if visit.agent else 'responsável'} às {visit.key_withdrawn_at.strftime('%H:%M')}."
            )
            messages.warning(request, f'Chave do imóvel {visit.visit_property.code} retirada! Lembre-se do prazo de devolução de até 4h.')

        elif action_type == 'return':
            visit.key_status = 'returned'
            visit.key_returned_at = timezone.now()
            visit.save()

            InteractionLog.objects.create(
                lead=visit.lead,
                user=request.user if request.user.is_authenticated else visit.agent,
                action_type='key_action',
                content=f"✅ Chave do imóvel [{visit.visit_property.code}] DEVOLVIDA na imobiliária às {visit.key_returned_at.strftime('%H:%M')}."
            )
            messages.success(request, f'Chave do imóvel {visit.visit_property.code} devolvida com sucesso!')

    return redirect(request.META.get('HTTP_REFERER', 'visit_schedule'))


def visit_whatsapp_confirm_view(request, visit_id):
    """Redireciona para o link gerado do WhatsApp com mensagem de confirmação"""
    visit = get_object_or_404(PropertyVisit, pk=visit_id)
    if visit.whatsapp_confirmation_url and visit.whatsapp_confirmation_url != '#':
        return redirect(visit.whatsapp_confirmation_url)
    messages.error(request, 'Telefone do cliente inválido para WhatsApp.')
    return redirect('visit_schedule')


# ==========================================
# FASE 5: TAREFAS & FOLLOW-UPS DO DIA
# ==========================================

def task_list_view(request):
    """Tela 'Minhas Tarefas & Follow-ups do Dia' com separação por Atrasadas, Hoje e Próximos 7 Dias"""
    selected_category = request.GET.get('category', '')
    selected_agent = request.GET.get('agent', '')

    now = timezone.now()
    today = now.date()
    end_of_today = timezone.make_aware(datetime.combine(today, datetime.max.time()))
    seven_days_later = end_of_today + timedelta(days=7)

    base_qs = Activity.objects.select_related('lead', 'lead__client', 'related_property', 'assigned_to')

    if selected_category:
        base_qs = base_qs.filter(task_type=selected_category)
    if selected_agent:
        base_qs = base_qs.filter(assigned_to_id=selected_agent)

    # Blocos de Tarefas
    overdue_tasks = list(base_qs.filter(is_completed=False, due_date__lt=now).order_by('due_date'))
    today_tasks = list(base_qs.filter(is_completed=False, due_date__gte=now, due_date__lte=end_of_today).order_by('due_date'))
    upcoming_tasks = list(base_qs.filter(is_completed=False, due_date__gt=end_of_today, due_date__lte=seven_days_later).order_by('due_date'))
    completed_tasks = list(base_qs.filter(is_completed=True).order_by('-completed_at')[:20])

    # KPIs
    today_tasks_count = len(today_tasks)
    overdue_tasks_count = len(overdue_tasks)
    upcoming_tasks_count = len(upcoming_tasks)
    completed_today_count = base_qs.filter(is_completed=True, completed_at__date=today).count()

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')
    active_leads = PropertyLead.objects.filter(status='open').select_related('client').order_by('-created_at')[:30]
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

        assigned_to = User.objects.filter(pk=assigned_to_id).first() or (request.user if request.user.is_authenticated else User.objects.first())
        lead = PropertyLead.objects.filter(pk=lead_id).first() if lead_id else None
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
                user=request.user if request.user.is_authenticated else assigned_to,
                action_type='task_created',
                content=f"⏰ Tarefa criada: '{task.title}' para {due_date.strftime('%d/%m/%Y às %H:%M')} (Prioridade: {task.get_priority_display()})."
            )

        messages.success(request, f'Tarefa "{task.title}" criada com sucesso!')
        
        redirect_to = request.POST.get('redirect_to')
        if redirect_to == 'drawer' and lead:
            return redirect('kanban')

        return redirect(request.META.get('HTTP_REFERER', 'task_list'))

    return redirect('task_list')


def task_toggle_complete_view(request, task_id):
    """Alterna o status de conclusão de uma tarefa via HTMX"""
    task = get_object_or_404(Activity.objects.select_related('lead', 'lead__client', 'related_property', 'assigned_to'), pk=task_id)

    task.is_completed = not task.is_completed
    if task.is_completed:
        task.completed_at = timezone.now()
        if task.lead:
            InteractionLog.objects.create(
                lead=task.lead,
                user=request.user if request.user.is_authenticated else task.assigned_to,
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

def dashboard_view(request):
    """
    Painel de Inteligência e Indicadores Executivos (BI Imobiliário)
    Visão Condicional:
    - Gestor/Admin: Métricas consolidadas de toda a imobiliária + Ranking de corretores + Filtro por corretor.
    - Corretor: Filtro automático no backend (vê apenas sua própria produção e meta individual).
    """
    user = request.user
    is_manager = user.is_authenticated and (
        (hasattr(user, 'profile') and user.profile.is_manager) or user.is_superuser or user.is_staff
    )

    selected_agent_id = request.GET.get('agent', '').strip()
    trans_type_filter = request.GET.get('trans_type', 'all').strip() # 'all', 'buy', 'rent'

    # 1. Definir Escopo Base dos Leads de acordo com as permissões
    if is_manager:
        if selected_agent_id:
            leads_base_qs = PropertyLead.objects.filter(agent_id=selected_agent_id)
            target_agent = User.objects.filter(id=selected_agent_id).first()
        else:
            leads_base_qs = PropertyLead.objects.all()
            target_agent = None
    else:
        leads_base_qs = PropertyLead.objects.filter(agent=user) if user.is_authenticated else PropertyLead.objects.none()
        target_agent = user

    # Filtro opcional de finalidade (Venda vs Locação)
    if trans_type_filter in ['buy', 'rent']:
        leads_filtered_qs = leads_base_qs.filter(transaction_type=trans_type_filter)
    else:
        leads_filtered_qs = leads_base_qs

    # 2. Definição do Período Atual (Mês Corrente)
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Leads Ganhos no Mês Corrente
    won_leads_month = leads_filtered_qs.filter(
        status='won'
    ).filter(
        Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
    )

    # 3. KPIs Financeiros Principais
    vgv_pipeline = leads_filtered_qs.filter(status='open').aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')
    vgv_fechado_mes = won_leads_month.aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

    # Separação Venda vs Locação
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

    # Comissão Estimada / Realizada (Média de 6%)
    comissao_prevista = vgv_pipeline * Decimal('0.06')
    comissao_realizada = vgv_fechado_mes * Decimal('0.06')

    # Meta Mensal & Barra de Progresso
    if target_agent and hasattr(target_agent, 'profile'):
        meta_mensal = target_agent.profile.monthly_goal or Decimal('500000.00')
    elif is_manager and not selected_agent_id:
        total_meta_agents = UserProfile.objects.filter(user__is_active=True).aggregate(Sum('monthly_goal'))['monthly_goal__sum']
        meta_mensal = total_meta_agents or Decimal('2000000.00')
    else:
        meta_mensal = Decimal('500000.00')

    meta_progress_percent = min(100.0, float(round((vgv_fechado_mes / meta_mensal) * 100, 1))) if meta_mensal > 0 else 0.0

    # Ticket Médio
    all_won_leads = leads_filtered_qs.filter(status='won')
    ticket_medio = all_won_leads.aggregate(Avg('budget'))['budget__avg'] or Decimal('0.00')

    # Tempo Médio de Fechamento (Ciclo em Dias)
    cycle_days_list = [lead.cycle_time_days for lead in all_won_leads]
    tempo_medio_ciclo = round(sum(cycle_days_list) / len(cycle_days_list), 1) if cycle_days_list else 18.5

    # 4. Funil de Conversão Step-by-Step
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

    # Taxa Geral de Conversão (Ganhos / Total Finalizados)
    total_won = leads_filtered_qs.filter(status='won').count()
    total_lost = leads_filtered_qs.filter(status='lost').count()
    total_concluded = total_won + total_lost
    taxa_conversao_geral = round((total_won / total_concluded) * 100, 1) if total_concluded > 0 else (
        round((total_won / total_leads_count) * 100, 1) if total_leads_count > 0 else 0.0
    )

    # 5. Gráfico 1: Origem dos Leads (Donut Chart)
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

    # 6. Gráfico 2: Evolução do VGV nos Últimos 12 Meses (Bar Chart)
    month_names_pt = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    vgv_monthly_labels = []
    vgv_monthly_data = []

    # Monta os últimos 6 a 12 meses
    for i in range(5, -1, -1):
        # Mês retroativo
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

    # Se for mês atual e soma for 0 nos meses anteriores, adicionar o mês atual para ter visualização rica
    if sum(vgv_monthly_data) == 0 and float(vgv_fechado_mes) > 0:
        vgv_monthly_data[-1] = float(vgv_fechado_mes)

    # 7. Gráfico 3: Motivos de Perda (Horizontal Bar Chart)
    lost_reasons_qs = leads_filtered_qs.filter(status='lost').exclude(lost_reason__isnull=True).exclude(lost_reason='').values('lost_reason').annotate(total=Count('id')).order_by('-total')[:6]
    lost_reasons_labels = []
    lost_reasons_data = []
    for lr in lost_reasons_qs:
        lost_reasons_labels.append(lr['lost_reason'])
        lost_reasons_data.append(lr['total'])

    if not lost_reasons_data:
        lost_reasons_labels = ['Preço acima do orçamento', 'Financiamento reprovado', 'Localização desfavorável', 'Preferiu outro corretor', 'Desistiu da compra']
        lost_reasons_data = [5, 4, 3, 2, 1]

    # 8. Ranking de Produtividade dos Corretores (Apenas para Gestor/Admin)
    agents_ranking = []
    if is_manager:
        all_agents = User.objects.filter(is_active=True).select_related('profile').order_by('first_name')
        for ag in all_agents:
            ag_leads = PropertyLead.objects.filter(agent=ag)
            ag_active = ag_leads.filter(status='open').count()
            
            # Visitas no mês
            ag_visits_month = PropertyVisit.objects.filter(
                agent=ag,
                status='completed',
                scheduled_date__gte=start_of_month
            ).count()

            # VGV fechado no mês
            ag_vgv_won = ag_leads.filter(
                status='won'
            ).filter(
                Q(closed_at__gte=start_of_month) | Q(closed_at__isnull=True, updated_at__gte=start_of_month)
            ).aggregate(Sum('budget'))['budget__sum'] or Decimal('0.00')

            # Conversão do corretor
            ag_won_count = ag_leads.filter(status='won').count()
            ag_total_concluded = ag_won_count + ag_leads.filter(status='lost').count()
            ag_conv_rate = round((ag_won_count / ag_total_concluded) * 100, 1) if ag_total_concluded > 0 else 0.0

            ag_goal = ag.profile.monthly_goal if hasattr(ag, 'profile') else Decimal('500000.00')
            ag_goal_percent = min(100.0, float(round((ag_vgv_won / ag_goal) * 100, 1))) if ag_goal > 0 else 0.0

            agents_ranking.append({
                'agent': ag,
                'active_leads': ag_active,
                'visits_month': ag_visits_month,
                'vgv_won': ag_vgv_won,
                'conversion_rate': ag_conv_rate,
                'goal': ag_goal,
                'goal_percent': ag_goal_percent,
            })

        # Ordena pelo maior VGV fechado
        agents_ranking.sort(key=lambda x: x['vgv_won'], reverse=True)

    agents = User.objects.filter(is_active=True).order_by('first_name', 'username')

    context = {
        'is_manager': is_manager,
        'selected_agent_id': selected_agent_id,
        'trans_type_filter': trans_type_filter,
        'agents': agents,
        
        # KPIs Topo
        'vgv_pipeline': vgv_pipeline,
        'vgv_fechado_mes': vgv_fechado_mes,
        'vgv_vendas_mes': vgv_vendas_mes,
        'vgv_locacao_mes': vgv_locacao_mes,
        'comissao_prevista': comissao_prevista,
        'comissao_realizada': comissao_realizada,
        'meta_mensal': meta_mensal,
        'meta_progress_percent': meta_progress_percent,
        'ticket_medio': ticket_medio,
        'tempo_medio_ciclo': tempo_medio_ciclo,
        'taxa_conversao_geral': taxa_conversao_geral,

        # Funil
        'funnel_data': funnel_data,

        # Datasets Chart.js (JSON strings)
        'origins_chart_labels_json': json.dumps(origins_chart_labels),
        'origins_chart_data_json': json.dumps(origins_chart_data),
        'vgv_monthly_labels_json': json.dumps(vgv_monthly_labels),
        'vgv_monthly_data_json': json.dumps(vgv_monthly_data),
        'lost_reasons_labels_json': json.dumps(lost_reasons_labels),
        'lost_reasons_data_json': json.dumps(lost_reasons_data),

        # Ranking
        'agents_ranking': agents_ranking,
    }

    return render(request, 'dashboard/dashboard.html', context)


# ==========================================
# FASE 7: WEBHOOKS, INTEGRAÇÕES & FEED XML
# ==========================================

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def lead_webhook_ingest_view(request):
    """
    Endpoint de Ingestão de Leads via Webhook Seguro (Meta Ads, ZAP, VivaReal, Site, RD Station)
    - Autenticação via cabeçalho X-API-KEY ou query param api_key
    - Sanitização de dados de entrada
    - Criação de lead na etapa '1. Novo Lead'
    - Vinculação de imóvel por código (se informado)
    - Roteamento Inteligente (Prioridade do Captador + Roleta Circular Round-Robin)
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido. Utilize POST.'}, status=405)

    # 1. Autenticação por API Key
    provided_key = request.headers.get('X-API-KEY') or request.GET.get('api_key')
    expected_key = getattr(settings, 'WEBHOOK_API_KEY', 'imobicrm_secret_key_2026')

    if not provided_key or provided_key != expected_key:
        return JsonResponse({
            'error': 'Acesso não autorizado. Envie o cabeçalho X-API-KEY com uma chave válida.'
        }, status=401)

    # 2. Parsing e Sanitização do Payload (JSON ou Form POST)
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

    # 3. Localizar ou Criar Cliente (Person)
    client_phone = phone or f"sem-fone-{random.randint(10000, 99999)}"
    client, _ = Person.objects.get_or_create(
        phone=client_phone,
        defaults={
            'name': name,
            'email': email,
            'client_type': 'buyer' if transaction_type == 'buy' else 'renter'
        }
    )

    # Atualiza dados caso estivessem vazios
    if email and not client.email:
        client.email = email
        client.save()

    # 4. Localizar Imóvel de Interesse (se houver código)
    matched_property = None
    if property_code:
        matched_property = Property.objects.filter(code__iexact=property_code).first()

    # 5. Roteamento Inteligente (Prioridade do Captador + Roleta Circular)
    assigned_agent = assign_lead_smart(property_obj=matched_property)

    # 6. Criação do Lead no Funil
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

    # 7. Registro de Histórico (Timeline)
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


def portal_xml_feed_view(request):
    """
    Gera o Feed XML Padrão para Portais Imobiliários (ZAP, VivaReal, OLX, Imovelweb)
    - Inclui apenas imóveis disponíveis ('available')
    - BLINDAGEM ATIVA: Omite 100% dos dados de proprietário e taxas de comissão.
    """
    properties = Property.objects.filter(
        status='available'
    ).prefetch_related('images').select_related('captured_by').order_by('code')

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Carga xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        '  <Imoveis>'
    ]

    for prop in properties:
        xml_lines.append('    <Imovel>')
        xml_lines.append(f'      <CodigoImovel>{prop.code}</CodigoImovel>')
        xml_lines.append(f'      <TipoImovel>{prop.get_property_type_display()}</TipoImovel>')
        xml_lines.append(f'      <Finalidade>{prop.get_transaction_type_display()}</Finalidade>')
        xml_lines.append(f'      <Titulo><![CDATA[{prop.title}]]></Titulo>')
        xml_lines.append(f'      <Descricao><![CDATA[{prop.description or ""}]]></Descricao>')
        xml_lines.append(f'      <PrecoVenda>{prop.sale_price:.2f}</PrecoVenda>')
        xml_lines.append(f'      <PrecoLocacao>{prop.rental_price:.2f}</PrecoLocacao>')
        xml_lines.append(f'      <TaxaCondominio>{prop.condo_fee:.2f}</TaxaCondominio>')
        xml_lines.append(f'      <ValorIPTU>{prop.iptu:.2f}</ValorIPTU>')
        xml_lines.append(f'      <Bairro><![CDATA[{prop.neighborhood}]]></Bairro>')
        xml_lines.append(f'      <Cidade><![CDATA[{prop.city}]]></Cidade>')
        xml_lines.append(f'      <Estado>{prop.state}</Estado>')
        xml_lines.append(f'      <AreaUtil>{prop.usable_area:.2f}</AreaUtil>')
        xml_lines.append(f'      <AreaTotal>{prop.total_area:.2f}</AreaTotal>')
        xml_lines.append(f'      <QtdQuartos>{prop.bedrooms}</QtdQuartos>')
        xml_lines.append(f'      <QtdSuites>{prop.suites}</QtdSuites>')
        xml_lines.append(f'      <QtdBanheiros>{prop.bathrooms}</QtdBanheiros>')
        xml_lines.append(f'      <QtdVagas>{prop.parking_spaces}</QtdVagas>')
        
        # Diferenciais
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

        # Fotos
        xml_lines.append('      <Fotos>')
        for img in prop.images.all():
            if img.image:
                img_url = request.build_absolute_uri(img.image.url)
                principal = '1' if img.is_featured else '0'
                xml_lines.append(f'        <Foto Principal="{principal}"><![CDATA[{img_url}]]></Foto>')
        xml_lines.append('      </Fotos>')

        xml_lines.append('    </Imovel>')

    xml_lines.append('  </Imoveis>')
    xml_lines.append('</Carga>')

    xml_content = '\n'.join(xml_lines)
    return HttpResponse(xml_content, content_type='application/xml; charset=utf-8')


def whatsapp_template_list_view(request):
    """Tela de Gestão de Modelos de Mensagens do WhatsApp"""
    templates = WhatsAppTemplate.objects.all().order_by('category', 'title')
    categories = WhatsAppTemplate.CATEGORY_CHOICES

    return render(request, 'templates_whatsapp/list.html', {
        'templates': templates,
        'categories': categories,
    })


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
                is_active=is_active
            )
            messages.success(request, f'Modelo "{tpl.title}" cadastrado com sucesso!')

    return redirect('whatsapp_template_list')


def whatsapp_template_delete_view(request, template_id):
    """Exclusão de Modelo de Mensagem do WhatsApp"""
    tpl = get_object_or_404(WhatsAppTemplate, pk=template_id)
    if request.method == 'POST':
        title = tpl.title
        tpl.delete()
        messages.success(request, f'Modelo "{title}" removido com sucesso.')
    return redirect('whatsapp_template_list')


def lead_template_message_view(request, lead_id, template_id):
    """Renderiza a mensagem de um template para o lead e redireciona direto ao WhatsApp"""
    lead = get_object_or_404(PropertyLead.objects.select_related('client', 'agent'), pk=lead_id)
    template = get_object_or_404(WhatsAppTemplate, pk=template_id)

    wa_url = template.render_url(lead, agent=request.user if request.user.is_authenticated else lead.agent)

    # Registra no log de interações
    InteractionLog.objects.create(
        lead=lead,
        user=request.user if request.user.is_authenticated else lead.agent,
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
# MÓDULO DE CONTATOS (Frontend)
# ==========================================

def contact_list_view(request):
    """Listagem de contatos / clientes com busca e filtros — vinculados à conta"""
    qs = Person.objects.select_related('assigned_agent').all()

    # Contatos vinculados à conta logada (corretor vê só os seus; gestor/admin vê todos)
    if request.user.is_authenticated:
        is_mgr = (
            request.user.is_superuser
            or (hasattr(request.user, 'profile') and UserProfile.objects.filter(user=request.user, role='manager').exists())
        )
        if not is_mgr:
            qs = qs.filter(assigned_agent=request.user)
    else:
        return redirect('crm_login')

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(email__icontains=q) |
            Q(phone__icontains=q) |
            Q(document__icontains=q)
        )

    client_type = request.GET.get('type', '')
    if client_type:
        qs = qs.filter(client_type=client_type)

    agent_id = request.GET.get('agent', '')
    if agent_id:
        qs = qs.filter(assigned_agent_id=agent_id)

    context = {
        'contacts': qs.order_by('-created_at'),
        'q': q,
        'current_type': client_type,
        'current_agent': agent_id,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'total': qs.count(),
    }
    if request.headers.get('HX-Request'):
        return render(request, 'contacts/partials/contact_rows.html', context)
    return render(request, 'contacts/list.html', context)


def contact_create_view(request):
    """Cadastro de novo contato no frontend"""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip() or None
        secondary_phone = request.POST.get('secondary_phone', '').strip() or None
        document = request.POST.get('document', '').strip() or None
        client_type = request.POST.get('client_type', 'buyer')
        notes = request.POST.get('notes', '').strip() or None
        agent_id = request.POST.get('assigned_agent') or None

        if not name or not phone:
            messages.error(request, 'Nome e telefone são obrigatórios.')
            return redirect('contact_create')

        agent = None
        if request.user.is_authenticated:
            is_mgr = (
                request.user.is_superuser
                or (hasattr(request.user, 'profile') and UserProfile.objects.filter(user=request.user, role='manager').exists())
            )
            if is_mgr and agent_id:
                agent = User.objects.filter(pk=agent_id).first()
            else:
                agent = request.user  # contatos sempre vinculados à conta logada
        elif agent_id:
            agent = User.objects.filter(pk=agent_id).first()

        person = Person.objects.create(
            name=name,
            phone=phone,
            email=email,
            secondary_phone=secondary_phone,
            document=document,
            client_type=client_type,
            notes=notes,
            assigned_agent=agent,
        )
        messages.success(request, f'Contato "{person.name}" cadastrado com sucesso.')
        return redirect('contact_list')

    return render(request, 'contacts/form.html', {
        'contact': None,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'form_title': 'Novo Contato',
        'form_action': 'contact_create',
    })


def contact_edit_view(request, pk):
    """Edição de contato no frontend"""
    person = get_object_or_404(Person, pk=pk)

    if request.method == 'POST':
        person.name = request.POST.get('name', '').strip()
        person.phone = request.POST.get('phone', '').strip()
        person.email = request.POST.get('email', '').strip() or None
        person.secondary_phone = request.POST.get('secondary_phone', '').strip() or None
        person.document = request.POST.get('document', '').strip() or None
        person.client_type = request.POST.get('client_type', person.client_type)
        person.notes = request.POST.get('notes', '').strip() or None
        agent_id = request.POST.get('assigned_agent') or None
        person.assigned_agent = User.objects.filter(pk=agent_id).first() if agent_id else None
        person.save()
        messages.success(request, f'Contato "{person.name}" atualizado.')
        return redirect('contact_list')

    return render(request, 'contacts/form.html', {
        'contact': person,
        'client_types': Person.CLIENT_TYPES,
        'agents': User.objects.filter(is_active=True).order_by('first_name', 'username'),
        'form_title': f'Editar — {person.name}',
        'form_action': 'contact_edit',
    })


def contact_detail_view(request, pk):
    """Ficha do contato"""
    person = get_object_or_404(
        Person.objects.select_related('assigned_agent').prefetch_related('leads', 'owned_properties'),
        pk=pk
    )
    prefs = getattr(person, 'preferences', None)
    return render(request, 'contacts/detail.html', {
        'contact': person,
        'prefs': prefs,
        'leads': person.leads.select_related('stage', 'pipeline').all()[:20],
        'owned': person.owned_properties.all()[:20],
    })



# ==========================================
# AUTENTICAÇÃO (Frontend)
# ==========================================

def crm_login_view(request):
    """Login por e-mail/usuário + senha (frontend CRM)"""
    if request.user.is_authenticated:
        return redirect(request.GET.get('next') or 'kanban')

    error = None
    if request.method == 'POST':
        login_id = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = None
        if login_id and password:
            # tenta username direto
            user = authenticate(request, username=login_id, password=password)
            if user is None and '@' in login_id:
                # tenta por e-mail
                u = User.objects.filter(email__iexact=login_id).first()
                if u:
                    user = authenticate(request, username=u.username, password=password)
            if user is not None:
                login(request, user)
                # garante perfil
                if not hasattr(user, 'profile') or not UserProfile.objects.filter(user=user).exists():
                    UserProfile.objects.get_or_create(user=user, defaults={'role': 'agent'})
                next_url = request.POST.get('next') or request.GET.get('next') or '/'
                return redirect(next_url)
            error = 'E-mail/usuário ou senha incorretos.'
        else:
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
        messages.error(request, 'Login com Google não configurado. Defina GOOGLE_CLIENT_ID no settings.')
        return redirect('crm_login')

    import urllib.parse, secrets
    state = secrets.token_urlsafe(16)
    request.session['google_oauth_state'] = state
    request.session['google_oauth_next'] = request.GET.get('next', '/')

    # Sempre usar URI fixa (Google bloqueia IP privado tipo 192.168.x.x)
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
    import urllib.parse, urllib.request, json

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
    if state != request.session.get('google_oauth_state'):
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
    picture = info.get('picture') or ''

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
        user.is_staff = True  # acesso ao CRM frontend; permissões via grupo
        user.save()
        # grupo Corretores
        from django.contrib.auth.models import Group, Permission
        group, _ = Group.objects.get_or_create(name='Corretores')
        if group.permissions.count() == 0:
            for name in ['person', 'company', 'clientpreference', 'property', 'propertyimage',
                         'propertylead', 'pipeline', 'stage', 'activity', 'interactionlog',
                         'propertyvisit', 'whatsapptemplate']:
                for action in ('add', 'change', 'view', 'delete'):
                    try:
                        group.permissions.add(Permission.objects.get(codename=f'{action}_{name}'))
                    except Permission.DoesNotExist:
                        pass
        user.groups.add(group)
    else:
        # atualiza nome se vazio
        if given and not user.first_name:
            user.first_name = given
            user.last_name = family or user.last_name
            user.save(update_fields=['first_name', 'last_name'])

    UserProfile.objects.get_or_create(user=user, defaults={'role': 'agent'})
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    next_url = request.session.pop('google_oauth_next', '/')
    request.session.pop('google_oauth_state', None)
    messages.success(request, f'Bem-vindo, {user.get_full_name() or user.email}!')
    return redirect(next_url or '/')

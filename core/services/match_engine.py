import urllib.parse
from decimal import Decimal
from core.models import Property, PropertyLead

def calculate_match_score(lead, property_obj):
    """
    Calcula a pontuação de compatibilidade (0 a 100%) entre a demanda do Lead e o Imóvel.
    Retorna uma tupla (score_total: int, badges_motivos: list[str]).
    """
    # Se o imóvel estiver vendido, alugado ou inativo, não faz match
    if property_obj.status in ['sold', 'rented', 'inactive']:
        return 0, []

    # Finalidade deve bater (ex: se o lead quer compra, o imóvel deve ser venda ou ambos)
    if lead.transaction_type == 'buy' and property_obj.transaction_type not in ['sale', 'both']:
        return 0, []
    if lead.transaction_type == 'rent' and property_obj.transaction_type not in ['rent', 'both']:
        return 0, []

    score = 0
    reasons = []

    # Obter preferências do cliente ou dados básicos do lead
    pref = getattr(lead.client, 'preferences', None)
    
    # 1. PONTUAÇÃO DE PREÇO (Peso Máximo: 40 pontos com margem de 10% a 15%)
    prop_price = property_obj.sale_price if lead.transaction_type in ['buy', 'invest'] else property_obj.rental_price
    
    max_budget = Decimal('0.00')
    min_budget = Decimal('0.00')
    if pref and pref.max_price > 0:
        max_budget = pref.max_price
        min_budget = pref.min_price
    elif lead.budget > 0:
        max_budget = lead.budget

    if max_budget > 0 and prop_price > 0:
        if prop_price <= max_budget:
            score += 40
            reasons.append("Dentro do orçamento")
        elif prop_price <= max_budget * Decimal('1.10'): # Até 10% acima
            score += 30
            reasons.append("Até 10% acima do orçamento (negociável)")
        elif prop_price <= max_budget * Decimal('1.15'): # Entre 10% e 15% acima
            score += 15
            reasons.append("Até 15% acima do orçamento")
        else:
            score += 0
    else:
        score += 35 # Orçamento não informado, pontuação neutra alta

    # 2. PONTUAÇÃO DE LOCALIZAÇÃO / BAIRRO (Peso Máximo: 25 pontos)
    desired_locations = []
    if pref and pref.preferred_locations:
        desired_locations = [loc.lower().strip() for loc in pref.preferred_locations]
    elif lead.preferred_location:
        desired_locations = [lead.preferred_location.lower().strip()]

    if desired_locations:
        prop_neighborhood = (property_obj.neighborhood or '').lower().strip()
        if any(loc in prop_neighborhood or prop_neighborhood in loc for loc in desired_locations):
            score += 25
            reasons.append(f"No bairro desejado ({property_obj.neighborhood})")
        else:
            score += 5 # Bairro diferente, mas na mesma cidade
    else:
        score += 20 # Bairro aberto

    # 3. PONTUAÇÃO DE TIPOLOGIA (Peso Máximo: 15 pontos)
    desired_types = []
    if pref and pref.property_types:
        desired_types = pref.property_types
    elif lead.property_type:
        desired_types = [lead.property_type]

    if desired_types:
        if property_obj.property_type in desired_types:
            score += 15
            reasons.append(f"Tipo compatível ({property_obj.get_property_type_display()})")
        else:
            score += 0
    else:
        score += 15

    # 4. PONTUAÇÃO DE QUARTOS, VAGAS E METRAGEM (Peso Máximo: 10 pontos)
    phys_score = 0
    min_beds = pref.min_bedrooms if pref else 0
    min_parking = pref.min_parking_spaces if pref else 0
    min_area = pref.min_area_m2 if pref else 0

    if min_beds > 0:
        if property_obj.bedrooms >= min_beds:
            phys_score += 4
    else:
        phys_score += 4

    if min_parking > 0:
        if property_obj.parking_spaces >= min_parking:
            phys_score += 3
    else:
        phys_score += 3

    if min_area > 0:
        if property_obj.usable_area >= min_area:
            phys_score += 3
    else:
        phys_score += 3

    score += phys_score
    if phys_score >= 8:
        reasons.append("Atende quartos/vagas/área")

    # 5. PONTUAÇÃO DE DIFERENCIAIS E EXIGÊNCIAS (Peso Máximo: 10 pontos)
    amenities_score = 0
    total_checks = 0
    if pref:
        if pref.pets_allowed:
            total_checks += 1
            if property_obj.pets_allowed:
                amenities_score += 1
        if pref.gourmet_balcony:
            total_checks += 1
            if property_obj.gourmet_balcony:
                amenities_score += 1
        if pref.morning_sun:
            total_checks += 1
            if property_obj.morning_sun:
                amenities_score += 1
        if pref.pool_or_leisure:
            total_checks += 1
            if property_obj.pool or property_obj.gym:
                amenities_score += 1

    if total_checks > 0:
        score += int((amenities_score / total_checks) * 10)
        if amenities_score == total_checks:
            reasons.append("Atende todas as exigências especiais")
    else:
        score += 10 # Sem exigências específicas

    # Garantir limite de 0 a 100%
    score = max(0, min(100, score))
    return score, reasons


def generate_match_whatsapp_url(lead, property_obj, score):
    """
    Gera o link para disparar no WhatsApp do cliente com o template aprovado.
    """
    phone = lead.clean_phone
    if not phone:
        return "#"

    bairro = property_obj.neighborhood or "ótima localização"
    code = property_obj.code
    title = property_obj.title
    valor = f"R$ {property_obj.sale_price if lead.transaction_type in ['buy', 'invest'] else property_obj.rental_price}"

    # Template do WhatsApp
    msg = (
        f"Oi, {lead.client.name}, beleza? "
        f"Acabamos de captar um imóvel incrível no {bairro} ({code} - {title} por {valor}) "
        f"e priorizei te mandar antes de divulgar forte. Acho que tem tudo a ver com o que você procura! "
        f"Se gostar, me avisa que já agendo nossa visita!"
    )

    encoded_msg = urllib.parse.quote(msg)
    return f"https://wa.me/{phone}?text={encoded_msg}"


def get_matching_properties_for_lead(lead, limit=6, min_score=40):
    """
    Retorna os melhores imóveis para um lead específico, ordenados por pontuação de match decrescente.
    """
    available_properties = Property.objects.exclude(status__in=['sold', 'rented', 'inactive']).prefetch_related('images')
    
    matches = []
    interested_ids = set(lead.interested_properties.values_list('id', flat=True))

    for prop in available_properties:
        score, reasons = calculate_match_score(lead, prop)
        if score >= min_score:
            matches.append({
                'property': prop,
                'score': score,
                'reasons': reasons,
                'is_linked': prop.id in interested_ids,
                'whatsapp_url': generate_match_whatsapp_url(lead, prop, score)
            })

    # Ordenar por score decrescente
    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches[:limit]


def get_matching_leads_for_property(property_obj, limit=6, min_score=40):
    """
    Retorna os leads mais compatíveis para uma propriedade específica (usado na ficha do imóvel).
    """
    open_leads = PropertyLead.objects.filter(status='open').select_related('client', 'client__preferences', 'agent')
    
    matches = []
    for lead in open_leads:
        score, reasons = calculate_match_score(lead, property_obj)
        if score >= min_score:
            matches.append({
                'lead': lead,
                'score': score,
                'reasons': reasons,
                'whatsapp_url': generate_match_whatsapp_url(lead, property_obj, score)
            })

    matches.sort(key=lambda x: x['score'], reverse=True)
    return matches[:limit]

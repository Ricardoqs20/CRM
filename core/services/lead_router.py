from django.contrib.auth.models import User
from django.db.models import Q
from core.models import PropertyLead

def assign_lead_smart(property_obj=None):
    """
    Roteamento e Distribuição Inteligente de Leads (Fase 7):
    - Regra 1 (Prioridade do Captador): Se o lead veio interessado em um imóvel específico que possui
      corretor captador ativo, o lead é atribuído diretamente ao captador.
    - Regra 2 (Roleta Circular / Round-Robin): Se for busca genérica ou o captador estiver inativo,
      distribui de forma circular e equitativa entre todos os corretores ativos do sistema.
    """
    # 1. Prioridade do Captador
    if property_obj and property_obj.captured_by and property_obj.captured_by.is_active:
        return property_obj.captured_by

    # 2. Roleta Circular entre Corretores Ativos
    active_agents = list(
        User.objects.filter(is_active=True)
                    .filter(Q(profile__role__in=['agent', 'manager']) | Q(is_staff=True) | Q(is_superuser=True))
                    .distinct()
                    .order_by('id')
    )

    if not active_agents:
        return User.objects.filter(is_active=True).first()

    if len(active_agents) == 1:
        return active_agents[0]

    # Busca o último lead atribuído para avançar para o próximo na fila circular
    last_lead = PropertyLead.objects.filter(agent__in=active_agents).order_by('-created_at').first()
    if last_lead and last_lead.agent in active_agents:
        last_index = active_agents.index(last_lead.agent)
        next_agent = active_agents[(last_index + 1) % len(active_agents)]
    else:
        next_agent = active_agents[0]

    return next_agent

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from core.models import (
    UserProfile, Person, ClientPreference,
    Property, Pipeline, Stage, PropertyLead,
    Activity, InteractionLog, PropertyVisit,
    WhatsAppTemplate
)

class Command(BaseCommand):
    help = 'Popula o banco com os pipelines padrão, etapas do funil imobiliário e dados de demonstração.'

    def handle(self, *args, **options):
        self.stdout.write('Iniciando carga de dados padrão do CRM Imobiliário...')

        # 1. Criar Pipeline Padrão de Vendas
        pipeline_sales, _ = Pipeline.objects.get_or_create(
            name='Vendas de Imóveis',
            defaults={'is_default': True, 'is_active': True}
        )

        pipeline_rent, _ = Pipeline.objects.get_or_create(
            name='Locação de Imóveis',
            defaults={'is_default': False, 'is_active': True}
        )

        # 2. Etapas do Funil Imobiliário (padrão operacional)
        # order, name, type, color, descrição (só documentação no seed)
        stages_data = [
            (1, 'Novo Lead', 'open', '#2563eb'),           # Qualifica contato e orçamento
            (2, 'Em Atendimento', 'open', '#d97706'),      # Apresenta imóveis e tira dúvidas
            (3, 'Visita Agendada', 'open', '#7c3aed'),    # Agenda visita e controla chaves
            (4, 'Proposta / Negociação', 'open', '#db2777'),  # Proposta, valores e docs
            (5, 'Fechado (Ganho)', 'won', '#059669'),      # Contrato e comissão
            (6, 'Perdido (Arquivado)', 'lost', '#64748b'), # Histórico para reabordagem
        ]

        def sync_stages(pipeline):
            # Remove etapas antigas com nomes numerados legados
            legacy_names = [
                '1. Novo Lead', '2. Em Atendimento', '3. Visita Agendada',
                '4. Proposta / Negociação', '5. Fechado (Ganho)', '6. Perdido (Arquivado)',
            ]
            Stage.objects.filter(pipeline=pipeline, name__in=legacy_names).delete()

            objs = {}
            for order, name, s_type, color in stages_data:
                stage, _ = Stage.objects.get_or_create(
                    pipeline=pipeline,
                    order=order,
                    defaults={'name': name, 'stage_type': s_type, 'color': color}
                )
                stage.name = name
                stage.stage_type = s_type
                stage.color = color
                stage.order = order
                stage.save()
                objs[order] = stage
            # limpa etapas extras fora das 6
            Stage.objects.filter(pipeline=pipeline).exclude(order__in=[1, 2, 3, 4, 5, 6]).delete()
            return objs

        stage_objects = sync_stages(pipeline_sales)
        sync_stages(pipeline_rent)

        self.stdout.write(self.style.SUCCESS(
            f'Funis configurados com 6 etapas: Novo Lead → Em Atendimento → Visita → Proposta → Fechado → Perdido'
        ))

        # 3. Criar Usuários de Demonstração (Corretores e Gestor)
        manager_user, _ = User.objects.get_or_create(
            username='carlos_gestor',
            defaults={'first_name': 'Carlos', 'last_name': 'Mendes', 'email': 'carlos@imobiliaria.com'}
        )
        if not hasattr(manager_user, 'profile'):
            UserProfile.objects.create(user=manager_user, role='manager', creci='1234-MA', phone='(98) 99111-2222')

        agent1, _ = User.objects.get_or_create(
            username='joao_corretor',
            defaults={'first_name': 'João', 'last_name': 'Silva', 'email': 'joao@imobiliaria.com'}
        )
        if not hasattr(agent1, 'profile'):
            UserProfile.objects.create(user=agent1, role='agent', creci='5678-MA', phone='(98) 98888-1111')

        agent2, _ = User.objects.get_or_create(
            username='maria_corretora',
            defaults={'first_name': 'Maria', 'last_name': 'Oliveira', 'email': 'maria@imobiliaria.com'}
        )
        if not hasattr(agent2, 'profile'):
            UserProfile.objects.create(user=agent2, role='agent', creci='9101-MA', phone='(98) 98777-2222')

        # 4. Criar Clientes com Preferências Ricas
        client1, _ = Person.objects.get_or_create(
            name='Lucas Andrade',
            defaults={
                'phone': '(98) 98123-4567',
                'email': 'lucas.andrade@email.com',
                'client_type': 'buyer',
                'assigned_agent': agent1,
                'notes': 'Prefere contato via WhatsApp à noite. Tem 2 gatos e precisa de tela de proteção.'
            }
        )
        ClientPreference.objects.update_or_create(
            person=client1,
            defaults={
                'transaction_type': 'buy',
                'property_types': ['apartment'],
                'preferred_locations': ['Renascença', 'Calhau', 'Ponta D\'Areia'],
                'min_price': 500000.00,
                'max_price': 800000.00,
                'payment_method': 'financing',
                'min_bedrooms': 3,
                'min_suites': 1,
                'min_parking_spaces': 2,
                'min_area_m2': 85.00,
                'pets_allowed': True,
                'gourmet_balcony': True,
                'pool_or_leisure': True,
                'morning_sun': True,
                'notes': 'Exige varanda gourmet e lazer completo.'
            }
        )

        client2, _ = Person.objects.get_or_create(
            name='Dra. Camila Torres',
            defaults={
                'phone': '(98) 98234-5678',
                'email': 'camila.torres@med.com',
                'client_type': 'buyer',
                'assigned_agent': agent2,
                'notes': 'Médica, quer casa ou cobertura até R$ 1.5M. Pagamento à vista.'
            }
        )
        ClientPreference.objects.update_or_create(
            person=client2,
            defaults={
                'transaction_type': 'buy',
                'property_types': ['house', 'penthouse'],
                'preferred_locations': ['Calhau', 'Olho D\'Água'],
                'min_price': 1000000.00,
                'max_price': 1600000.00,
                'payment_method': 'cash',
                'min_bedrooms': 4,
                'min_suites': 2,
                'min_parking_spaces': 3,
                'min_area_m2': 180.00,
                'pool_or_leisure': True,
                'notes': 'Pagamento à vista com recursos próprios.'
            }
        )

        # Proprietário
        owner1, _ = Person.objects.get_or_create(
            name='Roberto Castro (Proprietário)',
            defaults={
                'phone': '(98) 99999-3333',
                'email': 'roberto.castro@invest.com',
                'client_type': 'owner',
                'notes': 'Proprietário de 3 apartamentos na região do Renascença.'
            }
        )

        # 5. Criar Imóveis no Catálogo (Estoque)
        prop1, _ = Property.objects.get_or_create(
            code='AP0101',
            defaults={
                'title': 'Apartamento 3 Quartos com Varanda Gourmet no Renascença II',
                'description': 'Lindo apartamento nascente, 3 quartos sendo 1 suíte, varanda gourmet integrada, 2 vagas cobertas. Condomínio com piscina, academia e salão de festas.',
                'property_type': 'apartment',
                'transaction_type': 'sale',
                'status': 'available',
                'sale_price': 680000.00,
                'condo_fee': 750.00,
                'iptu': 1200.00,
                'building_name': 'Edifício Grand Park Renascença',
                'neighborhood': 'Renascença',
                'city': 'São Luís',
                'usable_area': 96.00,
                'total_area': 125.00,
                'bedrooms': 3,
                'suites': 1,
                'bathrooms': 3,
                'parking_spaces': 2,
                'floor': '7º Andar',
                'pets_allowed': True,
                'gourmet_balcony': True,
                'pool': True,
                'gym': True,
                'elevator': True,
                'morning_sun': True,
                'owner': owner1,
                'captured_by': agent1,
                'is_exclusive': True,
                'agreed_commission_rate': 6.00,
                'key_location': 'Portaria do Edifício'
            }
        )

        prop2, _ = Property.objects.get_or_create(
            code='CS0202',
            defaults={
                'title': 'Casa Duplex com Piscina e 4 Suítes no Calhau',
                'description': 'Excelente residência em rua fechada no Calhau. 4 suítes plenas, área gourmet privativa com churrasqueira e piscina, garagem para 4 carros.',
                'property_type': 'house',
                'transaction_type': 'sale',
                'status': 'available',
                'sale_price': 1450000.00,
                'condo_fee': 0.00,
                'iptu': 2800.00,
                'neighborhood': 'Calhau',
                'city': 'São Luís',
                'usable_area': 260.00,
                'total_area': 450.00,
                'bedrooms': 4,
                'suites': 4,
                'bathrooms': 5,
                'parking_spaces': 4,
                'pets_allowed': True,
                'gourmet_balcony': True,
                'pool': True,
                'owner': owner1,
                'captured_by': agent2,
                'is_exclusive': True,
                'agreed_commission_rate': 5.00,
                'key_location': 'Na imobiliária - Gaveta 02'
            }
        )

        # 6. Criar Leads e Associar às Etapas do Funil
        lead1, _ = PropertyLead.objects.get_or_create(
            title='Busca Apt 3 quartos no Renascença até 700k',
            client=client1,
            defaults={
                'pipeline': pipeline_sales,
                'stage': stage_objects[3], # Visita Agendada
                'agent': agent1,
                'transaction_type': 'buy',
                'property_type': 'apartment',
                'budget': 680000.00,
                'preferred_location': 'Renascença',
                'origin': 'instagram_ads',
                'status': 'open',
                'notes': 'Lead muito quente vindo de campanha do Instagram.',
                'last_contact_at': timezone.now()
            }
        )
        lead1.interested_properties.add(prop1)

        lead2, _ = PropertyLead.objects.get_or_create(
            title='Interesse em Casa de Alto Padrão no Calhau',
            client=client2,
            defaults={
                'pipeline': pipeline_sales,
                'stage': stage_objects[2], # Em Atendimento
                'agent': agent2,
                'transaction_type': 'buy',
                'property_type': 'house',
                'budget': 1450000.00,
                'preferred_location': 'Calhau',
                'origin': 'portal_zap',
                'status': 'open',
                'notes': 'Veio do portal ZAP pelo anúncio da casa CS0202.',
                'last_contact_at': timezone.now()
            }
        )
        lead2.interested_properties.add(prop2)

        # 6.1 Lead Fechado (Ganho) para dados do Dashboard
        client3, _ = Person.objects.get_or_create(
            name='Dr. Fernando Silveira',
            defaults={'phone': '(98) 99188-7766', 'client_type': 'buyer', 'assigned_agent': agent1}
        )
        lead_won, _ = PropertyLead.objects.get_or_create(
            title='Compra Apartamento Grand Park AP0101',
            client=client3,
            defaults={
                'pipeline': pipeline_sales,
                'stage': stage_objects[5], # Fechado (Ganho)
                'agent': agent1,
                'transaction_type': 'buy',
                'property_type': 'apartment',
                'budget': 680000.00,
                'origin': 'referral',
                'status': 'won',
                'closed_at': timezone.now() - timedelta(days=5),
                'last_contact_at': timezone.now()
            }
        )

        # 6.2 Lead Perdido com Motivo
        client4, _ = Person.objects.get_or_create(
            name='Juliana Martins',
            defaults={'phone': '(98) 99155-4433', 'client_type': 'buyer', 'assigned_agent': agent2}
        )
        PropertyLead.objects.get_or_create(
            title='Busca Cobertura na Ponta D\'Areia',
            client=client4,
            defaults={
                'pipeline': pipeline_sales,
                'stage': stage_objects[6], # Perdido (Arquivado)
                'agent': agent2,
                'transaction_type': 'buy',
                'property_type': 'penthouse',
                'budget': 1200000.00,
                'origin': 'portal_vivareal',
                'status': 'lost',
                'lost_reason': 'Financiamento reprovado pela Caixa',
                'closed_at': timezone.now() - timedelta(days=8),
                'last_contact_at': timezone.now()
            }
        )

        # 7. Criar Visita Agendada para o Lead 1
        visit_date = timezone.now() + timedelta(days=1, hours=4)
        visit1, _ = PropertyVisit.objects.get_or_create(
            lead=lead1,
            visit_property=prop1,
            defaults={
                'agent': agent1,
                'scheduled_date': visit_date,
                'status': 'confirmed',
                'meeting_point': 'Na portaria do Edifício Grand Park Renascença',
                'key_status': 'concierge',
                'key_notes': 'Avisar na portaria que o corretor João irá acompanhar.'
            }
        )

        # 8. Criar Tarefa de Follow-up
        Activity.objects.get_or_create(
            title='Confirmar visita com Lucas às 14h',
            lead=lead1,
            defaults={
                'activity_type': 'whatsapp',
                'priority': 'high',
                'description': 'Enviar mensagem no WhatsApp confirmando a visita agendada para amanhã.',
                'related_property': prop1,
                'assigned_to': agent1,
                'due_date': timezone.now() + timedelta(hours=2),
                'is_completed': False
            }
        )

        # 10. Criar Modelos Padrão de WhatsApp (Fase 7)
        templates_data = [
            (
                'Boas-vindas - Interesse em Imóvel de Portal',
                'welcome',
                'Olá, {nome_cliente}! Tudo bem? Sou o {nome_corretor} da Imobiliária. Vi que você demonstrou interesse no imóvel [{codigo_imovel}] no {bairro} ({valor}). Gostaria de receber mais fotos e a ficha técnica completa?'
            ),
            (
                'Oferta de Imóvel Compatível (Match)',
                'match',
                'Oi, {primeiro_nome}, tudo bem? Acabamos de captar uma oportunidade excelente no {bairro} que tem exatamente o perfil que você procura ({valor}). Quer dar uma olhada antes de anunciarmos?'
            ),
            (
                'Confirmação de Visita',
                'visit',
                'Olá, {nome_cliente}! Confirmada a nossa visita para o imóvel no {bairro} [{codigo_imovel}]. Estarei no local no horário combinado. Qualquer imprevisto, só me avisar aqui. Te encontro lá! 🏠'
            ),
            (
                'Solicitação de Documentos para Análise de Crédito',
                'credit_doc',
                'Olá, {primeiro_nome}! Para adiantarmos a simulação e aprovação do seu financiamento junto aos bancos (Caixa/Itaú/Bradesco), você poderia me enviar fotos do RG/CNH, comprovante de renda e de residência?'
            ),
            (
                'Follow-up / Manutenção de Contato',
                'follow_up',
                'Oi, {primeiro_nome}! Como estão as buscas pelo seu imóvel? Surgiram novas opções no {bairro} esta semana. Podemos conversar rapidinho?'
            ),
        ]

        for title, cat, content in templates_data:
            WhatsAppTemplate.objects.get_or_create(
                title=title,
                defaults={'category': cat, 'content': content, 'is_active': True}
            )

        self.stdout.write(self.style.SUCCESS('Carga de dados de demonstração e modelos de WhatsApp concluída com sucesso!'))


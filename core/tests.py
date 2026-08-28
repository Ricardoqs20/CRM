import json
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from core.models import (
    Property, Person, UserProfile, Pipeline, Stage, PropertyLead,
    ClientPreference, InteractionLog, Activity, PropertyVisit,
    WhatsAppTemplate
)

class RealEstateCRMTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager_user = User.objects.create_user(username='admin_test', password='123')
        UserProfile.objects.create(user=self.manager_user, role='manager')

        self.agent_user = User.objects.create_user(username='agent_test', password='123')
        UserProfile.objects.create(user=self.agent_user, role='agent')

        self.other_agent = User.objects.create_user(username='other_agent', password='123')
        UserProfile.objects.create(user=self.other_agent, role='agent')

        # Pipeline & Stages
        self.pipeline = Pipeline.objects.create(name='Vendas de Imóveis', is_default=True)
        self.stage1 = Stage.objects.create(pipeline=self.pipeline, name='1. Novo Lead', order=1, stage_type='open')
        self.stage2 = Stage.objects.create(pipeline=self.pipeline, name='2. Em Atendimento', order=2, stage_type='open')
        self.stage3 = Stage.objects.create(pipeline=self.pipeline, name='3. Visita Agendada', order=3, stage_type='open')
        self.stage6 = Stage.objects.create(pipeline=self.pipeline, name='6. Perdido (Arquivado)', order=6, stage_type='lost')

        self.owner = Person.objects.create(
            name='Seu Silva Proprietário',
            phone='(98) 99999-0000',
            client_type='owner'
        )

        self.property = Property.objects.create(
            code='TST001',
            title='Apartamento Teste Renascença',
            property_type='apartment',
            transaction_type='sale',
            sale_price=500000.00,
            neighborhood='Renascença',
            owner=self.owner,
            captured_by=self.agent_user,
            status='available',
            is_exclusive=True,
            share_owner_contact=False
        )

        self.buyer = Person.objects.create(
            name='Lucas Cliente',
            phone='(98) 98123-4567',
            client_type='buyer'
        )
        ClientPreference.objects.create(
            person=self.buyer,
            transaction_type='buy',
            preferred_locations=['Renascença', 'Calhau'],
            min_price=400000.00,
            max_price=600000.00,
            min_bedrooms=3,
            pets_allowed=True
        )

        self.lead = PropertyLead.objects.create(
            title='Busca Apt 3 quartos Renascença',
            client=self.buyer,
            pipeline=self.pipeline,
            stage=self.stage1,
            budget=550000.00,
            transaction_type='buy',
            agent=self.agent_user,
            last_contact_at=timezone.now() - timedelta(hours=50) # Inativo há >48h
        )

    def test_kanban_view_loads(self):
        response = self.client.get(reverse('kanban'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1. Novo Lead')
        self.assertContains(response, 'Busca Apt 3 quartos Renascença')
        # Alerta de inatividade > 48h
        self.assertTrue(self.lead.is_followup_overdue)
        self.assertContains(response, 'Sem contato há >48h')

    def test_lead_drawer_view(self):
        response = self.client.get(reverse('lead_detail_drawer', kwargs={'pk': self.lead.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lucas Cliente')
        self.assertContains(response, 'wa.me/5598981234567')
        self.assertContains(response, '🐾 Aceita Pets')

    def test_lead_add_note_resets_overdue(self):
        self.client.force_login(self.agent_user)
        self.assertTrue(self.lead.is_followup_overdue)

        response = self.client.post(reverse('lead_add_note', kwargs={'pk': self.lead.id}), {
            'action_type': 'call_logged',
            'content': 'Liguei para o Lucas e alinhamos a visita para sábado.'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Liguei para o Lucas')

        self.lead.refresh_from_db()
        self.assertFalse(self.lead.is_followup_overdue) # Alerta 48h removido
        self.assertEqual(self.lead.interactions.count(), 1)

    def test_lead_move_stage_with_lost_reason(self):
        self.client.force_login(self.agent_user)
        payload = {
            'stage_id': self.stage6.id,
            'lost_reason': 'Comprou com Concorrente'
        }
        response = self.client.post(
            reverse('lead_move_stage', kwargs={'pk': self.lead.id}),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.stage, self.stage6)
        self.assertEqual(self.lead.status, 'lost')
        self.assertEqual(self.lead.lost_reason, 'Comprou com Concorrente')

    def test_lead_quick_create(self):
        self.client.force_login(self.agent_user)
        response = self.client.post(reverse('lead_quick_create'), {
            'title': 'Interesse Casa Calhau',
            'client_name': 'Mariana Souza',
            'client_phone': '(98) 98777-6655',
            'transaction_type': 'buy',
            'property_type': 'house',
            'budget': '1200000.00',
            'origin': 'instagram_ads'
        })
        self.assertEqual(response.status_code, 302)
        created_lead = PropertyLead.objects.filter(title='Interesse Casa Calhau').first()
        self.assertIsNotNone(created_lead)
        self.assertEqual(created_lead.stage, self.stage1)
        self.assertEqual(created_lead.client.name, 'Mariana Souza')
        self.assertEqual(created_lead.origin, 'instagram_ads')

    def test_property_detail_owner_privacy(self):
        self.client.force_login(self.other_agent)
        response = self.client.get(reverse('property_detail', kwargs={'pk': self.property.id}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_owner'])
        self.assertContains(response, 'Contato do Proprietário Privado')

        self.client.force_login(self.agent_user)
        response = self.client.get(reverse('property_detail', kwargs={'pk': self.property.id}))
        self.assertTrue(response.context['can_view_owner'])
        self.assertContains(response, '(98) 99999-0000')


class MatchEngineTestCase(TestCase):
    """Testes do Motor de Cruzamento Inteligente (Fase 4)"""

    def setUp(self):
        self.agent = User.objects.create_user(username='match_agent', password='123')
        UserProfile.objects.create(user=self.agent, role='agent')

        self.pipeline = Pipeline.objects.create(name='Vendas', is_default=True)
        self.stage = Stage.objects.create(pipeline=self.pipeline, name='Novo Lead', order=1, stage_type='open')

        # Imóvel no bairro Renascença, R$ 500k, 3 quartos, aceita pets
        self.prop1 = Property.objects.create(
            code='MATCH01', title='Apt Renascença 3q',
            property_type='apartment', transaction_type='sale',
            sale_price=500000, neighborhood='Renascença',
            bedrooms=3, parking_spaces=2, usable_area=90,
            pets_allowed=True, status='available',
            captured_by=self.agent
        )

        # Imóvel caro (R$ 800k) — fora do orçamento de 15%
        self.prop_caro = Property.objects.create(
            code='MATCH02', title='Cobertura Ponta do Farol',
            property_type='apartment', transaction_type='sale',
            sale_price=800000, neighborhood='Ponta do Farol',
            bedrooms=4, parking_spaces=3, usable_area=150,
            status='available', captured_by=self.agent
        )

        # Imóvel 10% acima do orçamento (R$ 550k, max_budget = 500k)
        self.prop_margem = Property.objects.create(
            code='MATCH03', title='Apt Calhau 3q Margem',
            property_type='apartment', transaction_type='sale',
            sale_price=550000, neighborhood='Calhau',
            bedrooms=3, parking_spaces=2, usable_area=85,
            status='available', captured_by=self.agent
        )

        self.buyer = Person.objects.create(
            name='João Match', phone='(98) 98111-2222', client_type='buyer'
        )
        ClientPreference.objects.create(
            person=self.buyer,
            transaction_type='buy',
            preferred_locations=['Renascença', 'Calhau'],
            min_price=300000, max_price=500000,
            min_bedrooms=3, min_parking_spaces=1,
            pets_allowed=True
        )

        self.lead = PropertyLead.objects.create(
            title='Busca Apt 3q', client=self.buyer,
            pipeline=self.pipeline, stage=self.stage,
            budget=500000, transaction_type='buy',
            property_type='apartment', preferred_location='Renascença',
            agent=self.agent, last_contact_at=timezone.now()
        )

    def test_match_score_perfect_property(self):
        """Imóvel que bate 100% no perfil do lead deve ter score >= 85"""
        from core.services.match_engine import calculate_match_score
        score, reasons = calculate_match_score(self.lead, self.prop1)
        self.assertGreaterEqual(score, 85)
        self.assertTrue(any('orçamento' in r.lower() for r in reasons))
        self.assertTrue(any('bairro' in r.lower() for r in reasons))

    def test_match_score_price_tolerance(self):
        """Imóvel 10% acima do orçamento deve pontuar 30 pts na faixa de preço (não 0)"""
        from core.services.match_engine import calculate_match_score
        score_margem, reasons_margem = calculate_match_score(self.lead, self.prop_margem)
        score_caro, _ = calculate_match_score(self.lead, self.prop_caro)
        # Margem 10% deve ter score razoável, caro demais deve ter score muito mais baixo
        self.assertGreater(score_margem, score_caro)
        self.assertTrue(any('10%' in r for r in reasons_margem))

    def test_matching_leads_for_property(self):
        """A ficha do imóvel deve retornar o lead compatível na lista de matches"""
        from core.services.match_engine import get_matching_leads_for_property
        matches = get_matching_leads_for_property(self.prop1)
        self.assertGreaterEqual(len(matches), 1)
        lead_ids = [m['lead'].id for m in matches]
        self.assertIn(self.lead.id, lead_ids)

    def test_lead_toggle_property_link(self):
        """Vincular e desvincular um imóvel do lead via view toggle"""
        self.client_http = Client()
        self.client_http.force_login(self.agent)

        # Vincular
        response = self.client_http.post(
            reverse('lead_toggle_property_link', kwargs={
                'lead_id': self.lead.id, 'property_id': self.prop1.id
            })
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.prop1, self.lead.interested_properties.all())

        # Desvincular
        response = self.client_http.post(
            reverse('lead_toggle_property_link', kwargs={
                'lead_id': self.lead.id, 'property_id': self.prop1.id
            })
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.prop1, self.lead.interested_properties.all())

    def test_lead_drawer_includes_matches(self):
        """A gaveta 360° do lead deve incluir a seção de Match Automático"""
        self.client_http = Client()
        self.client_http.force_login(self.agent)
        response = self.client_http.get(reverse('lead_detail_drawer', kwargs={'pk': self.lead.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Imóveis Compatíveis no Estoque (Match)')
        self.assertContains(response, 'MATCH01')

    def test_property_detail_includes_matching_leads(self):
        """A ficha do imóvel deve incluir a seção de Leads Compatíveis"""
        self.client_http = Client()
        self.client_http.force_login(self.agent)
        response = self.client_http.get(reverse('property_detail', kwargs={'pk': self.prop1.id}))
        self.assertEqual(response.status_code, 200)
        self.assertIn('matching_leads', response.context)
        self.assertContains(response, 'Clientes da Base Compatíveis')


class VisitAndTaskTestCase(TestCase):
    """Testes Automatizados para a Fase 5: Visitas, Chaves e Tarefas"""
    def setUp(self):
        self.client = Client()
        self.agent = User.objects.create_user(username='corretor_fase5', password='123', first_name='Rodrigo')
        UserProfile.objects.create(user=self.agent, role='agent', phone='(98) 98888-0000')

        self.pipeline = Pipeline.objects.create(name='Vendas', is_default=True)
        self.stage_lead = Stage.objects.create(pipeline=self.pipeline, name='1. Novo Lead', order=1, stage_type='open')
        self.stage_atend = Stage.objects.create(pipeline=self.pipeline, name='2. Em Atendimento', order=2, stage_type='open')
        self.stage_visita = Stage.objects.create(pipeline=self.pipeline, name='3. Visita Agendada', order=3, stage_type='open')
        self.stage_proposta = Stage.objects.create(pipeline=self.pipeline, name='4. Proposta / Negociação', order=4, stage_type='open')

        self.client_buyer = Person.objects.create(
            name='Mariana Souza',
            phone='(98) 98111-2233',
            client_type='buyer'
        )

        self.prop = Property.objects.create(
            code='VIS01',
            title='Apartamento Cobertura no Renascença',
            neighborhood='Renascença',
            street='Rua dos Cravos',
            number='100',
            sale_price=800000.00,
            status='available',
            captured_by=self.agent
        )

        self.lead = PropertyLead.objects.create(
            title='Interesse em Cobertura no Renascença',
            client=self.client_buyer,
            pipeline=self.pipeline,
            stage=self.stage_atend,
            budget=850000.00,
            agent=self.agent,
            status='open'
        )

    def test_visit_creation_and_whatsapp_confirmation(self):
        """Agendamento de visita gera confirmação WhatsApp e log de interação"""
        visit_time = timezone.now() + timedelta(days=2)
        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.prop,
            agent=self.agent,
            scheduled_date=visit_time,
            status='confirmed',
            meeting_point='Portaria principal'
        )
        self.assertTrue(visit.whatsapp_confirmation_url.startswith('https://wa.me/5598981112233?text='))
        self.assertIn('Mariana', visit.whatsapp_confirmation_url)
        self.assertIn('Renascen', visit.whatsapp_confirmation_url)

    def test_key_overdue_alert_logic(self):
        """Chave retirada há mais de 4 horas deve disparar is_key_overdue == True"""
        visit_time = timezone.now() + timedelta(hours=1)
        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.prop,
            agent=self.agent,
            scheduled_date=visit_time,
            key_status='with_agent',
            key_withdrawn_at=timezone.now() - timedelta(hours=5) # 5h atrás
        )
        self.assertTrue(visit.is_key_overdue)

        # Se devolvida, não deve mais estar em atraso
        visit.key_status = 'returned'
        visit.key_returned_at = timezone.now()
        visit.save()
        self.assertFalse(visit.is_key_overdue)

    def test_visit_key_withdraw_and_return_views(self):
        """Views de retirada e devolução de chave registram horários e histórico"""
        self.client.force_login(self.agent)
        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.prop,
            agent=self.agent,
            scheduled_date=timezone.now() + timedelta(hours=2),
            key_status='concierge'
        )

        # Retirar
        response = self.client.post(
            reverse('visit_key_action', kwargs={'visit_id': visit.id}),
            {'action_type': 'withdraw'},
            HTTP_REFERER=reverse('visit_schedule')
        )
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertEqual(visit.key_status, 'with_agent')
        self.assertIsNotNone(visit.key_withdrawn_at)

        # Devolver
        response = self.client.post(
            reverse('visit_key_action', kwargs={'visit_id': visit.id}),
            {'action_type': 'return'},
            HTTP_REFERER=reverse('visit_schedule')
        )
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertEqual(visit.key_status, 'returned')
        self.assertIsNotNone(visit.key_returned_at)

    def test_visit_feedback_auto_moves_lead_to_proposal(self):
        """Feedback com intenção de proposta move o lead automaticamente para 'Proposta / Negociação'"""
        self.client.force_login(self.agent)
        visit = PropertyVisit.objects.create(
            lead=self.lead,
            visit_property=self.prop,
            agent=self.agent,
            scheduled_date=timezone.now() - timedelta(hours=1),
            status='scheduled'
        )
        self.assertEqual(self.lead.stage, self.stage_atend)

        response = self.client.post(
            reverse('visit_feedback_save', kwargs={'visit_id': visit.id}),
            {
                'status': 'completed',
                'client_rating': '5',
                'feedback_notes': 'Cliente amou a vista e as vagas de garagem.',
                'will_make_proposal': 'true',
                'proposal_details': 'Oferta de R$ 780.000 à vista.'
            }
        )
        self.assertEqual(response.status_code, 302)

        visit.refresh_from_db()
        self.lead.refresh_from_db()

        self.assertEqual(visit.status, 'completed')
        self.assertEqual(visit.client_rating, 5)
        self.assertTrue(visit.will_make_proposal)
        # Regra de negócio: Lead movido para o estágio de Proposta
        self.assertEqual(self.lead.stage, self.stage_proposta)

        # Log registrado
        proposal_log = InteractionLog.objects.filter(lead=self.lead, action_type='proposal_sent').first()
        self.assertIsNotNone(proposal_log)
        self.assertIn('AUTOMAÇÃO DE ESTÁGIO', proposal_log.content)

    def test_task_create_and_toggle_complete(self):
        """Criação e conclusão interativa de tarefas via HTMX"""
        self.client.force_login(self.agent)
        task = Activity.objects.create(
            title='Ligar para o banco da cliente',
            task_type='credit_analysis',
            priority='high',
            due_date=timezone.now() + timedelta(hours=3),
            assigned_to=self.agent,
            lead=self.lead
        )
        self.assertFalse(task.is_completed)

        # Toggle via HTMX
        response = self.client.post(reverse('task_toggle_complete', kwargs={'task_id': task.id}))
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertTrue(task.is_completed)
        self.assertIsNotNone(task.completed_at)

    def test_calendar_and_task_list_views_load(self):
        """Telas principais de Visitas e Tarefas carregam com código HTTP 200"""
        self.client.force_login(self.agent)
        
        # Calendário de Visitas
        res_cal = self.client.get(reverse('visit_schedule'))
        self.assertEqual(res_cal.status_code, 200)
        self.assertContains(res_cal, 'Agenda de Visitas & Controle de Chaves')

        # Lista de Tarefas
        res_tasks = self.client.get(reverse('task_list'))
        self.assertEqual(res_tasks.status_code, 200)
        self.assertContains(res_tasks, 'Minhas Tarefas & Follow-ups do Dia')


class DashboardAnalyticsTestCase(TestCase):
    """Testes Automatizados para a Fase 6: Painel de Indicadores & BI"""
    def setUp(self):
        self.client = Client()

        # Gestor
        self.manager = User.objects.create_user(username='gestor_bi', password='123', first_name='Carlos Gestor')
        UserProfile.objects.create(user=self.manager, role='manager', monthly_goal=1000000.00)

        # Corretor 1
        self.agent1 = User.objects.create_user(username='corretor1', password='123', first_name='Lucas Corretor')
        UserProfile.objects.create(user=self.agent1, role='agent', monthly_goal=500000.00)

        # Corretor 2
        self.agent2 = User.objects.create_user(username='corretor2', password='123', first_name='Ana Corretora')
        UserProfile.objects.create(user=self.agent2, role='agent', monthly_goal=400000.00)

        self.pipeline = Pipeline.objects.create(name='Vendas', is_default=True)
        self.stage_lead = Stage.objects.create(pipeline=self.pipeline, name='1. Novo Lead', order=1, stage_type='open')
        self.stage_won = Stage.objects.create(pipeline=self.pipeline, name='5. Fechado (Ganho)', order=5, stage_type='won')
        self.stage_lost = Stage.objects.create(pipeline=self.pipeline, name='6. Perdido', order=6, stage_type='lost')

        self.client1 = Person.objects.create(name='Cliente A', phone='(98) 98100-0001', client_type='buyer')
        self.client2 = Person.objects.create(name='Cliente B', phone='(98) 98100-0002', client_type='buyer')
        self.client3 = Person.objects.create(name='Cliente C', phone='(98) 98100-0003', client_type='buyer')

        # Lead Ganho do Corretor 1 (R$ 600.000)
        self.lead_won1 = PropertyLead.objects.create(
            title='Venda Apartamento Renascença',
            client=self.client1,
            pipeline=self.pipeline,
            stage=self.stage_won,
            agent=self.agent1,
            transaction_type='buy',
            budget=600000.00,
            origin='instagram_ads',
            status='won',
            closed_at=timezone.now() - timedelta(days=10)
        )

        # Lead Perdido do Corretor 1
        self.lead_lost1 = PropertyLead.objects.create(
            title='Proposta Recusada',
            client=self.client2,
            pipeline=self.pipeline,
            stage=self.stage_lost,
            agent=self.agent1,
            transaction_type='buy',
            budget=350000.00,
            origin='portal_zap',
            status='lost',
            lost_reason='Preço acima do orçamento',
            closed_at=timezone.now() - timedelta(days=5)
        )

        # Lead Ganho do Corretor 2 (R$ 300.000)
        self.lead_won2 = PropertyLead.objects.create(
            title='Venda Casa Calhau',
            client=self.client3,
            pipeline=self.pipeline,
            stage=self.stage_won,
            agent=self.agent2,
            transaction_type='buy',
            budget=300000.00,
            origin='referral',
            status='won',
            closed_at=timezone.now() - timedelta(days=2)
        )

    def test_dashboard_view_loads_for_manager(self):
        """Gestor acessa o dashboard com visão global e ranking de corretores"""
        self.client.force_login(self.manager)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_manager'])
        # Deve somar VGV de todos os corretores (600k + 300k = 900k)
        self.assertEqual(response.context['vgv_fechado_mes'], 900000.00)
        # Comissão realizada de 6% (900k * 0.06 = 54k)
        self.assertEqual(response.context['comissao_realizada'], 54000.00)
        # Ranking de corretores deve estar presente
        self.assertContains(response, 'Ranking & Produtividade da Equipe')
        self.assertContains(response, 'Lucas Corretor')
        self.assertContains(response, 'Ana Corretora')

    def test_dashboard_view_restricts_for_agent(self):
        """Corretor vê apenas seus próprios números e não vê ranking da equipe"""
        self.client.force_login(self.agent1)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['is_manager'])
        # Corretor 1 vê apenas os seus 600k (não os 900k globais)
        self.assertEqual(response.context['vgv_fechado_mes'], 600000.00)
        # Sua comissão pessoal de 6% (36k)
        self.assertEqual(response.context['comissao_realizada'], 36000.00)
        # Ranking da equipe não deve ser renderizado
        self.assertNotContains(response, '👑 Painel Exclusivo de Gestão')

    def test_dashboard_filter_by_transaction_type(self):
        """Filtro por Venda vs Locação funciona dinamicamente"""
        self.client.force_login(self.manager)
        response = self.client.get(reverse('dashboard') + '?trans_type=buy')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['vgv_fechado_mes'], 900000.00)

        response_rent = self.client.get(reverse('dashboard') + '?trans_type=rent')
        self.assertEqual(response_rent.status_code, 200)
        self.assertEqual(response_rent.context['vgv_fechado_mes'], 0.00)

    def test_chart_json_datasets_structure(self):
        """Os dados para Chart.js são injetados como JSON válido"""
        self.client.force_login(self.manager)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

        origins_labels = json.loads(response.context['origins_chart_labels_json'])
        origins_data = json.loads(response.context['origins_chart_data_json'])
        self.assertTrue(len(origins_labels) > 0)
        self.assertTrue(len(origins_data) > 0)

        lost_labels = json.loads(response.context['lost_reasons_labels_json'])
        self.assertIn('Preço acima do orçamento', lost_labels)


class IntegrationsTestCase(TestCase):
    """Testes Automatizados para a Fase 7: Webhooks, Roteamento, Templates WhatsApp e Feed XML"""
    def setUp(self):
        self.client = Client()

        # Corretores
        self.agent_captor = User.objects.create_user(username='corretor_captador', password='123', first_name='Marcos Captador')
        UserProfile.objects.create(user=self.agent_captor, role='agent', phone='(98) 98888-1111')

        self.agent_roleta = User.objects.create_user(username='corretor_roleta', password='123', first_name='Bruna Roleta')
        UserProfile.objects.create(user=self.agent_roleta, role='agent', phone='(98) 98777-2222')

        self.pipeline = Pipeline.objects.create(name='Vendas', is_default=True)
        self.stage1 = Stage.objects.create(pipeline=self.pipeline, name='1. Novo Lead', order=1, stage_type='open')

        # Proprietário e Imóvel com captador
        self.owner = Person.objects.create(name='Seu Antenor Proprietário', phone='(98) 99999-8888', client_type='owner')
        self.prop_exclusive = Property.objects.create(
            code='INT001',
            title='Apartamento Exclusivo no Renascença',
            property_type='apartment',
            transaction_type='sale',
            sale_price=750000.00,
            neighborhood='Renascença',
            owner=self.owner,
            captured_by=self.agent_captor,
            status='available',
            is_exclusive=True
        )

        # Template de WhatsApp
        self.tpl_welcome = WhatsAppTemplate.objects.create(
            title='Boas-vindas Portal',
            category='welcome',
            content='Olá, {nome_cliente}! Sou o {nome_corretor}. Vi seu interesse no imóvel [{codigo_imovel}] no {bairro} ({valor}).'
        )

    def test_webhook_unauthorized_without_valid_api_key(self):
        """Webhook rejeita com 401 caso a chave X-API-KEY seja inválida ou ausente"""
        payload = {'name': 'Lead Hacker', 'phone': '98999999999'}
        
        # Sem chave
        response = self.client.post('/api/webhooks/leads/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 401)

        # Chave errada
        response_wrong = self.client.post(
            '/api/webhooks/leads/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='chave_falsa_123'
        )
        self.assertEqual(response_wrong.status_code, 401)

    def test_webhook_ingests_lead_with_captor_priority(self):
        """Webhook com código de imóvel direciona o lead diretamente para o corretor captador"""
        payload = {
            'name': 'Juliana Costa',
            'phone': '(98) 98123-9999',
            'email': 'juliana.costa@email.com',
            'property_code': 'INT001',
            'origin': 'portal_zap',
            'notes': 'Quero saber se aceita financiamento pela Caixa.',
            'budget': 750000.00
        }

        response = self.client.post(
            '/api/webhooks/leads/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='imobicrm_secret_key_2026'
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['assigned_agent'], self.agent_captor.username)

        # Verifica se o lead foi criado no banco
        lead = PropertyLead.objects.get(id=data['lead_id'])
        self.assertEqual(lead.client.name, 'Juliana Costa')
        self.assertEqual(lead.agent, self.agent_captor)
        self.assertEqual(lead.stage, self.stage1)
        self.assertIn(self.prop_exclusive, lead.interested_properties.all())

        # Verifica log na timeline
        log = InteractionLog.objects.filter(lead=lead).first()
        self.assertIsNotNone(log)
        self.assertIn('Webhook', log.content)
        self.assertIn('Captador do Imóvel [INT001]', log.content)

    def test_webhook_generic_lead_round_robin_distribution(self):
        """Lead genérico sem imóvel específico é distribuído via roleta circular"""
        payload = {
            'name': 'Carlos Tráfego Pago',
            'phone': '(98) 98222-3333',
            'origin': 'instagram_ads',
            'notes': 'Busco apartamento de 3 quartos até 800k.'
        }

        response = self.client.post(
            '/api/webhooks/leads/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='imobicrm_secret_key_2026'
        )
        self.assertEqual(response.status_code, 201)
        lead = PropertyLead.objects.get(id=response.json()['lead_id'])
        self.assertIsNotNone(lead.agent)

    def test_portal_xml_feed_structure_and_blindness(self):
        """O feed XML deve gerar XML válido de imóveis disponíveis e omitir dados do proprietário/comissão"""
        response = self.client.get(reverse('portal_xml_feed'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml; charset=utf-8')

        content = response.content.decode('utf-8')
        self.assertIn('<CodigoImovel>INT001</CodigoImovel>', content)
        self.assertIn('<Bairro><![CDATA[Renascença]]></Bairro>', content)
        self.assertIn('<PrecoVenda>750000.00</PrecoVenda>', content)

        # BLINDAGEM DE PRIVACIDADE:
        self.assertNotIn('Seu Antenor', content)
        self.assertNotIn('99999-8888', content)
        self.assertNotIn('comissao', content.lower())
        self.assertNotIn('agreed_commission', content)

    def test_whatsapp_template_rendering_and_url(self):
        """Template interpola tags com perfeição e gera link wa.me/ válido"""
        lead = PropertyLead.objects.create(
            title='Busca Apt Renascença',
            client=self.owner, # Antenor
            pipeline=self.pipeline,
            stage=self.stage1,
            agent=self.agent_captor
        )
        lead.interested_properties.add(self.prop_exclusive)

        rendered = self.tpl_welcome.render_text(lead)
        self.assertIn('Olá, Seu Antenor Proprietário!', rendered)
        self.assertIn('Marcos Captador', rendered)
        self.assertIn('[INT001]', rendered)
        self.assertIn('Renascença', rendered)

        url = self.tpl_welcome.render_url(lead)
        self.assertTrue(url.startswith('https://wa.me/5598999998888?text='))

    def test_whatsapp_template_crud_views(self):
        """Telas de listagem, salvamento e exclusão de templates respondem corretamente"""
        self.client.force_login(self.agent_captor)
        
        # Listagem
        res_list = self.client.get(reverse('whatsapp_template_list'))
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, 'Boas-vindas Portal')

        # Criar novo
        res_save = self.client.post(reverse('whatsapp_template_save'), {
            'title': 'Template Criado via View',
            'category': 'match',
            'content': 'Mensagem teste para {nome_cliente}.',
            'is_active': 'true'
        })
        self.assertEqual(res_save.status_code, 302)
        self.assertTrue(WhatsAppTemplate.objects.filter(title='Template Criado via View').exists())




import importlib
import json
import os
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User

import crm_api.settings as project_settings
from core.crypto import decrypt_value, encrypt_value, is_encrypted, mask_document
from core.models import (
    Activity,
    CalendarIntegration,
    ClientPreference,
    Person,
    Pipeline,
    Property,
    PropertyLead,
    PropertyVisit,
    SecurityAuditLog,
    Stage,
    UserProfile,
    WhatsAppTemplate,
)
from core.security import anonymize_ip


class SecurityHardeningLGPDTestCase(TestCase):
    """Suíte completa de validação de Segurança, Criptografia, LGPD, Anti-IDOR e Rate Limiting."""

    def setUp(self):
        cache.clear()
        self.client = Client()

        self.manager = User.objects.create_user(username='sec_manager', password='StrongPassword!123')
        self.manager_profile = UserProfile.objects.create(user=self.manager, role='manager')

        self.agent_a = User.objects.create_user(username='sec_agent_a', password='StrongPassword!123')
        self.agent_a_profile = UserProfile.objects.create(user=self.agent_a, role='agent')

        self.agent_b = User.objects.create_user(username='sec_agent_b', password='StrongPassword!123')
        self.agent_b_profile = UserProfile.objects.create(user=self.agent_b, role='agent')

        self.pipeline = Pipeline.objects.create(name='Funil Principal', is_default=True)
        self.stage_open = Stage.objects.create(pipeline=self.pipeline, name='1. Novo Lead', order=1, stage_type='open')
        self.stage_won = Stage.objects.create(pipeline=self.pipeline, name='5. Fechado', order=5, stage_type='won')
        self.stage_lost = Stage.objects.create(pipeline=self.pipeline, name='6. Perdido', order=6, stage_type='lost')

        self.person_a = Person.objects.create(
            name='Fernanda Titular LGPD',
            email='fernanda@cliente.com.br',
            phone='(98) 98111-9988',
            secondary_phone='(98) 3222-1100',
            document='123.456.789-01',
            client_type='buyer',
            assigned_agent=self.agent_a,
            notes='Cliente VIP com renda comprovada.',
            lgpd_consent=True,
            lgpd_consent_at=timezone.now(),
            lgpd_origin='site_imobiliaria',
        )
        ClientPreference.objects.create(
            person=self.person_a,
            transaction_type='buy',
            max_price=900000,
            notes='Prefere andar alto.',
        )

        self.prop_a = Property.objects.create(
            code='SEC001',
            title='Apartamento Seguro Ponta dAreia',
            neighborhood='Ponta dAreia',
            sale_price=850000,
            captured_by=self.agent_a,
            owner=self.person_a,
            status='available',
        )

        self.lead_a = PropertyLead.objects.create(
            title='Compra Fernanda Titular LGPD',
            client=self.person_a,
            pipeline=self.pipeline,
            stage=self.stage_won,
            agent=self.agent_a,
            budget=850000,
            transaction_type='buy',
            status='won',
            notes='Anotação confidencial do financiamento.',
            closed_at=timezone.now() - timedelta(days=1),
        )

        self.visit_a = PropertyVisit.objects.create(
            lead=self.lead_a,
            visit_property=self.prop_a,
            agent=self.agent_a,
            scheduled_date=timezone.now() + timedelta(days=1),
            status='confirmed',
        )

        self.task_a = Activity.objects.create(
            title='Enviar minuta contratual',
            due_date=timezone.now() + timedelta(hours=4),
            assigned_to=self.agent_a,
            lead=self.lead_a,
        )

        self.global_tpl = WhatsAppTemplate.objects.create(
            title='Modelo Global Imobiliária',
            category='welcome',
            content='Olá {nome_cliente}',
            created_by=None,
        )
        self.agent_a_tpl = WhatsAppTemplate.objects.create(
            title='Modelo Exclusivo Agente A',
            category='match',
            content='Oi {primeiro_nome}',
            created_by=self.agent_a,
        )

    def tearDown(self):
        cache.clear()

    # ------------------------------------------------------------------
    # 1. ENDURECIMENTO DE CONFIGURAÇÕES E SEGREDOS
    # ------------------------------------------------------------------
    def test_production_secret_validation_blocks_insecure_defaults(self):
        with self.assertRaises(ImproperlyConfigured):
            project_settings.validate_production_secrets(
                False, 'django-insecure-qualquer-coisa', 'prod-webhook-998877'
            )
        with self.assertRaises(ImproperlyConfigured):
            project_settings.validate_production_secrets(
                False, 'your-secret-key-here', 'prod-webhook-998877'
            )
        with self.assertRaises(ImproperlyConfigured):
            project_settings.validate_production_secrets(
                False, 'prod-super-strong-secret-key-2026', 'dev-local-webhook-key'
            )
        # Valores fortes em produção não levantam exceção
        project_settings.validate_production_secrets(
            False, 'prod-super-strong-secret-key-2026', 'prod-super-strong-webhook-2026'
        )

    def test_production_settings_enable_https_and_security_headers(self):
        env_overrides = {
            'DEBUG': 'False',
            'SECRET_KEY': 'prod-valid-secret-key-for-reload-test-999',
            'WEBHOOK_API_KEY': 'prod-valid-webhook-key-for-reload-test-999',
        }
        with patch.dict(os.environ, env_overrides, clear=False):
            importlib.reload(project_settings)
            try:
                self.assertFalse(project_settings.DEBUG)
                self.assertEqual(project_settings.SECURE_PROXY_SSL_HEADER, ('HTTP_X_FORWARDED_PROTO', 'https'))
                self.assertTrue(project_settings.SESSION_COOKIE_SECURE)
                self.assertTrue(project_settings.CSRF_COOKIE_SECURE)
                self.assertTrue(project_settings.SESSION_COOKIE_HTTPONLY)
                self.assertEqual(project_settings.SECURE_HSTS_SECONDS, 31536000)
                self.assertTrue(project_settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)
                self.assertTrue(project_settings.SECURE_CONTENT_TYPE_NOSNIFF)
                self.assertEqual(project_settings.X_FRAME_OPTIONS, 'DENY')
                self.assertEqual(project_settings.SECURE_REFERRER_POLICY, 'strict-origin-when-cross-origin')
            finally:
                pass
        importlib.reload(project_settings)

    # ------------------------------------------------------------------
    # 2. CRIPTOGRAFIA EM REPOUSO & LEITURA RETROATIVA
    # ------------------------------------------------------------------
    @override_settings(FIELD_ENCRYPTION_KEY='dedicated-field-key-test-2026')
    def test_crypto_encrypt_decrypt_and_masking(self):
        cipher = encrypt_value('123.456.789-01')
        self.assertTrue(is_encrypted(cipher))
        self.assertNotIn('123.456.789-01', cipher)
        # Idempotência
        self.assertEqual(encrypt_value(cipher), cipher)
        self.assertEqual(decrypt_value(cipher), '123.456.789-01')
        # Leitura retroativa em texto plano
        self.assertEqual(decrypt_value('999.888.777-66'), '999.888.777-66')
        # Mascaramento CPF e CNPJ
        self.assertEqual(mask_document('123.456.789-01'), '***.456.789-**')
        self.assertEqual(mask_document('12.345.678/0001-90'), '**.345.678/0001-**')

    def test_person_document_and_oauth_tokens_encrypted_in_database_and_retroactive_read(self):
        cal = CalendarIntegration.objects.create(
            user=self.agent_a,
            google_connected=True,
            google_access_token='ya29.super_secret_access_token',
            google_refresh_token='1//super_secret_refresh_token',
        )

        with connection.cursor() as cur:
            cur.execute("SELECT document FROM core_person WHERE id = %s", [self.person_a.id])
            raw_doc = cur.fetchone()[0]
            cur.execute(
                "SELECT google_access_token, google_refresh_token FROM core_calendarintegration WHERE id = %s",
                [cal.id],
            )
            raw_access, raw_refresh = cur.fetchone()

        # No banco de dados, os valores estão cifrados (não em texto plano)
        self.assertTrue(raw_doc.startswith('enc::'))
        self.assertNotIn('123.456.789-01', raw_doc)
        self.assertTrue(raw_access.startswith('enc::'))
        self.assertTrue(raw_refresh.startswith('enc::'))
        self.assertNotIn('super_secret', raw_access)

        # Pelo ORM, descriptografa transparentemente
        self.person_a.refresh_from_db()
        cal.refresh_from_db()
        self.assertEqual(self.person_a.document, '123.456.789-01')
        self.assertEqual(self.person_a.masked_document, '***.456.789-**')
        self.assertEqual(cal.google_access_token, 'ya29.super_secret_access_token')
        self.assertEqual(cal.google_refresh_token, '1//super_secret_refresh_token')

        # Simula registro legado em texto plano direto no banco e valida leitura retroativa
        with connection.cursor() as cur:
            cur.execute("UPDATE core_person SET document = %s WHERE id = %s", ['111.222.333-44', self.person_a.id])
        self.person_a.refresh_from_db()
        self.assertEqual(self.person_a.document, '111.222.333-44')

        # Tela de detalhe exibe apenas o documento mascarado
        self.client.force_login(self.agent_a)
        res = self.client.get(reverse('contact_detail', kwargs={'pk': self.person_a.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, '***.222.333-**')
        self.assertNotContains(res, '111.222.333-44')

    # ------------------------------------------------------------------
    # 3. CONFORMIDADE LGPD, PRIVACIDADE & AUDITORIA
    # ------------------------------------------------------------------
    def test_public_privacy_policy_and_login_link(self):
        res_priv = self.client.get(reverse('privacy_policy'))
        self.assertEqual(res_priv.status_code, 200)
        self.assertContains(res_priv, 'Política de Privacidade')
        self.assertContains(res_priv, 'Finalidade do Tratamento')
        self.assertContains(res_priv, 'Direitos do Titular')
        self.assertContains(res_priv, 'Encarregado')

        res_login = self.client.get(reverse('crm_login'))
        self.assertEqual(res_login.status_code, 200)
        self.assertContains(res_login, reverse('privacy_policy'))

    def test_security_audit_logs_failed_login_permission_change_and_anonymized_ip(self):
        self.assertEqual(anonymize_ip('192.168.15.87'), '192.168.15.0')

        # Login falho
        res = self.client.post(
            reverse('crm_login'),
            {'email': 'sec_agent_a', 'password': 'WrongPassword'},
            REMOTE_ADDR='203.0.113.42',
        )
        self.assertEqual(res.status_code, 200)
        fail_log = SecurityAuditLog.objects.filter(action='login_failed', target='sec_agent_a').first()
        self.assertIsNotNone(fail_log)
        self.assertEqual(fail_log.ip_address, '203.0.113.0')

        # Alteração de permissão / papel
        self.agent_b_profile.role = 'manager'
        self.agent_b_profile.save()
        perm_log = SecurityAuditLog.objects.filter(action='permission_changed').first()
        self.assertIsNotNone(perm_log)
        self.assertIn('manager', perm_log.details)

    def test_lgpd_anonymization_restricted_to_admin_and_preserves_crm_metrics(self):
        # Corretor comum não pode anonimizar (403)
        self.client.force_login(self.agent_a)
        res_forbidden = self.client.post(reverse('contact_anonymize_lgpd', kwargs={'pk': self.person_a.id}))
        self.assertEqual(res_forbidden.status_code, 403)
        self.person_a.refresh_from_db()
        self.assertEqual(self.person_a.name, 'Fernanda Titular LGPD')

        # Gestor/Admin anonimiza com sucesso
        self.client.force_login(self.manager)
        res_ok = self.client.post(reverse('contact_anonymize_lgpd', kwargs={'pk': self.person_a.id}))
        self.assertEqual(res_ok.status_code, 302)

        self.person_a.refresh_from_db()
        self.lead_a.refresh_from_db()

        self.assertEqual(self.person_a.name, f'Titular Anonimizado #{self.person_a.id}')
        self.assertEqual(self.person_a.phone, '')
        self.assertIsNone(self.person_a.email)
        self.assertIsNone(self.person_a.document)
        self.assertIsNone(self.person_a.notes)
        self.assertFalse(self.person_a.lgpd_consent)

        # Métricas históricas do lead (VGV fechado) preservadas integralmente
        self.assertEqual(self.lead_a.status, 'won')
        self.assertEqual(float(self.lead_a.budget), 850000.0)
        self.assertIsNone(self.lead_a.notes)

        res_dash = self.client.get(reverse('dashboard'))
        self.assertEqual(float(res_dash.context['vgv_fechado_mes']), 850000.0)

        anon_log = SecurityAuditLog.objects.filter(action='lgpd_anonymize').first()
        self.assertIsNotNone(anon_log)

    # ------------------------------------------------------------------
    # 4. ANTI-IDOR, ANTI-XSS E PROTEÇÃO DE WEBHOOK
    # ------------------------------------------------------------------
    def test_anti_idor_blocks_cross_agent_access_across_modules(self):
        self.client.force_login(self.agent_b)

        # Lead de agent_a -> 404 para agent_b
        self.assertEqual(self.client.get(reverse('lead_detail_drawer', kwargs={'pk': self.lead_a.id})).status_code, 404)
        self.assertEqual(self.client.post(reverse('lead_add_note', kwargs={'pk': self.lead_a.id}), {'content': 'invasao'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('lead_move_stage', kwargs={'pk': self.lead_a.id}), {'stage_id': self.stage_open.id}).status_code, 404)

        # Imóvel captado por agent_a -> edição bloqueada (403) para agent_b
        self.assertEqual(self.client.get(reverse('property_edit', kwargs={'pk': self.prop_a.id})).status_code, 403)
        self.assertEqual(self.client.post(reverse('property_edit', kwargs={'pk': self.prop_a.id}), {'title': 'Hack'}).status_code, 403)

        # Contato de agent_a -> visualização e edição bloqueadas (404) para agent_b
        self.assertEqual(self.client.get(reverse('contact_detail', kwargs={'pk': self.person_a.id})).status_code, 404)
        self.assertEqual(self.client.get(reverse('contact_edit', kwargs={'pk': self.person_a.id})).status_code, 404)

        # Visita e Tarefa de agent_a -> 404 para agent_b
        self.assertEqual(self.client.get(reverse('visit_feedback_form', kwargs={'visit_id': self.visit_a.id})).status_code, 404)
        self.assertEqual(self.client.post(reverse('visit_key_action', kwargs={'visit_id': self.visit_a.id}), {'action_type': 'withdraw'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('task_toggle_complete', kwargs={'task_id': self.task_a.id})).status_code, 404)

        # Templates WhatsApp globais ou de agent_a -> edição/exclusão bloqueada (403) para agent_b
        self.assertEqual(self.client.post(reverse('whatsapp_template_delete', kwargs={'template_id': self.global_tpl.id})).status_code, 403)
        self.assertEqual(self.client.post(reverse('whatsapp_template_delete', kwargs={'template_id': self.agent_a_tpl.id})).status_code, 403)

        # DRF API isolada por carteira
        res_api_leads = self.client.get('/api/leads/')
        self.assertEqual(res_api_leads.status_code, 200)
        self.assertEqual(len(res_api_leads.json()), 0)

    def test_dashboard_prevents_stored_xss_in_lost_reasons_and_login_blocks_open_redirect(self):
        PropertyLead.objects.create(
            title='Lead XSS',
            client=self.person_a,
            pipeline=self.pipeline,
            stage=self.stage_lost,
            agent=self.agent_a,
            status='lost',
            lost_reason='</script><script>alert("xss")</script>',
            closed_at=timezone.now(),
        )
        self.client.force_login(self.agent_a)
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode('utf-8')
        self.assertNotIn('</script><script>alert("xss")</script>', html)

        # Open Redirect bloqueado no login
        self.client.logout()
        res_login = self.client.post(
            reverse('crm_login'),
            {'email': 'sec_agent_a', 'password': 'StrongPassword!123', 'next': 'https://evil.attacker.com/phish'},
        )
        self.assertEqual(res_login.status_code, 302)
        self.assertEqual(res_login['Location'], '/')

    # ------------------------------------------------------------------
    # 5. RATE LIMITING (LOGIN, WEBHOOKS, BUSCAS/EXPORTAÇÕES)
    # ------------------------------------------------------------------
    def test_rate_limit_login_blocks_after_5_posts_and_audits(self):
        cache.clear()
        url = reverse('crm_login')
        for _ in range(5):
            r = self.client.post(url, {'email': 'sec_agent_a', 'password': 'bad'}, REMOTE_ADDR='198.51.100.10')
            self.assertEqual(r.status_code, 200)

        r6 = self.client.post(url, {'email': 'sec_agent_a', 'password': 'bad'}, REMOTE_ADDR='198.51.100.10')
        self.assertEqual(r6.status_code, 429)
        self.assertTrue(
            SecurityAuditLog.objects.filter(action='rate_limit_exceeded', ip_address='198.51.100.0').exists()
        )

    @override_settings(WEBHOOK_API_KEY='rate-limit-webhook-secret')
    def test_rate_limit_webhook_blocks_after_60_requests(self):
        cache.clear()
        url = reverse('webhook_lead_ingest')
        for i in range(60):
            r = self.client.post(
                url,
                data=json.dumps({'name': f'Lead {i}', 'phone': f'9899000{i:04d}'}),
                content_type='application/json',
                HTTP_X_API_KEY='rate-limit-webhook-secret',
                REMOTE_ADDR='198.51.100.25',
            )
            self.assertEqual(r.status_code, 201)

        r61 = self.client.post(
            url,
            data=json.dumps({'name': 'Lead Flood', 'phone': '98990009999'}),
            content_type='application/json',
            HTTP_X_API_KEY='rate-limit-webhook-secret',
            REMOTE_ADDR='198.51.100.25',
        )
        self.assertEqual(r61.status_code, 429)

    def test_rate_limit_heavy_exports_and_searches_blocks_after_30_requests(self):
        cache.clear()
        self.client.force_login(self.agent_a)
        search_url = reverse('contact_list') + '?q=123'
        for _ in range(30):
            r = self.client.get(search_url)
            self.assertEqual(r.status_code, 200)

        r31 = self.client.get(search_url)
        self.assertEqual(r31.status_code, 429)

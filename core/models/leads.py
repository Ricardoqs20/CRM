import re
import urllib.parse
from django.db import models
from django.db.models import Sum
from django.contrib.auth.models import User
from django.utils import timezone
from .clients import Person
from .properties import Property

class Pipeline(models.Model):
    """Funil de Vendas / Locação / Captação"""
    name = models.CharField(max_length=100, verbose_name='Nome do Funil')
    is_default = models.BooleanField(default=False, verbose_name='Funil Padrão')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')

    class Meta:
        verbose_name = 'Funil de Negócios (Pipeline)'
        verbose_name_plural = 'Funis de Negócios (Pipelines)'

    def __str__(self):
        return self.name


class Stage(models.Model):
    """Etapas do Funil (ex: Novo Lead, Visita Agendada, etc.)"""
    STAGE_TYPES = [
        ('open', 'Em Aberto / Em Andamento'),
        ('won', 'Ganho / Fechado com Sucesso'),
        ('lost', 'Perdido / Arquivado'),
    ]

    pipeline = models.ForeignKey(Pipeline, related_name='stages', on_delete=models.CASCADE, verbose_name='Funil')
    name = models.CharField(max_length=100, verbose_name='Nome da Etapa')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem de Exibição')
    stage_type = models.CharField(max_length=10, choices=STAGE_TYPES, default='open', verbose_name='Tipo de Estágio')
    color = models.CharField(max_length=30, default='#6366f1', verbose_name='Cor de Destaque (Hex)')

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'Etapa do Funil (Stage)'
        verbose_name_plural = 'Etapas do Funil (Stages)'

    def __str__(self):
        return f"{self.pipeline.name} - {self.name}"

    @property
    def total_budget(self):
        """Soma financeira do orçamento acumulado nesta etapa"""
        val = self.leads.aggregate(total=Sum('budget'))['total']
        return val or 0.00


class PropertyLead(models.Model):
    """Lead Imobiliário / Oportunidade de Negócio"""
    TRANSACTION_TYPES = [
        ('buy', 'Compra'),
        ('rent', 'Aluguel'),
        ('invest', 'Investimento'),
    ]
    PROPERTY_TYPES = [
        ('apartment', 'Apartamento'),
        ('house', 'Casa / Sobrado'),
        ('penthouse', 'Cobertura'),
        ('land', 'Terreno'),
        ('commercial', 'Comercial'),
        ('other', 'Outro'),
    ]
    STATUS_CHOICES = [
        ('open', 'Em Negociação'),
        ('won', 'Fechado (Ganho)'),
        ('lost', 'Perdido (Arquivado)'),
    ]
    ORIGIN_CHOICES = [
        ('instagram_ads', 'Instagram / Meta Ads'),
        ('portal_zap', 'Portal ZAP'),
        ('portal_vivareal', 'VivaReal'),
        ('portal_olx', 'OLX'),
        ('portal_chaves', 'Chaves na Mão'),
        ('whatsapp', 'WhatsApp Direto'),
        ('site', 'Site da Imobiliária'),
        ('referral', 'Indicação'),
        ('walk_in', 'Balcão / Telefone'),
        ('manual', 'Cadastro Manual'),
    ]

    title = models.CharField(max_length=255, verbose_name='Título do Negócio')
    client = models.ForeignKey(Person, related_name='leads', on_delete=models.CASCADE, verbose_name='Cliente')
    pipeline = models.ForeignKey(Pipeline, related_name='leads', on_delete=models.CASCADE, verbose_name='Funil')
    stage = models.ForeignKey(Stage, related_name='leads', on_delete=models.CASCADE, verbose_name='Etapa Atual')
    agent = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='leads', verbose_name='Corretor Responsável'
    )
    
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, default='buy', verbose_name='Interesse')
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPES, default='apartment', verbose_name='Tipo de Imóvel')
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Orçamento / Valor Previsto (R$)')
    preferred_location = models.CharField(max_length=255, blank=True, null=True, verbose_name='Bairro / Região de Busca')
    
    # Imóveis do catálogo que o lead demonstrou interesse
    interested_properties = models.ManyToManyField(
        Property, blank=True, related_name='interested_leads', verbose_name='Imóveis Vinculados / Ofertados'
    )
    
    origin = models.CharField(max_length=30, choices=ORIGIN_CHOICES, default='manual', verbose_name='Origem do Lead')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', verbose_name='Status do Negócio')
    lost_reason = models.CharField(max_length=255, blank=True, null=True, verbose_name='Motivo de Perda / Arquivamento')
    notes = models.TextField(blank=True, null=True, verbose_name='Anotações Rápidas')

    last_contact_at = models.DateTimeField(default=timezone.now, verbose_name='Último Contato Realizado')
    closed_at = models.DateTimeField(blank=True, null=True, verbose_name='Data de Fechamento / Perda')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Lead Imobiliário'
        verbose_name_plural = 'Leads Imobiliários'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} - {self.client.name} (R$ {self.budget})"

    @property
    def cycle_time_days(self):
        """Calcula o tempo de ciclo em dias (da criação até o fechamento)"""
        end_time = self.closed_at or timezone.now()
        return max(1, (end_time - self.created_at).days)

    @property
    def is_followup_overdue(self):
        """Retorna True se faz mais de 48 horas desde o último contato e o lead continua em aberto"""
        if self.status != 'open':
            return False
        delta = timezone.now() - self.last_contact_at
        return delta.total_seconds() > (48 * 3600)

    @property
    def clean_phone(self):
        """Retorna apenas os dígitos do telefone para links"""
        if not self.client.phone:
            return ""
        digits = re.sub(r'\D', '', self.client.phone)
        if len(digits) in [10, 11] and not digits.startswith('55'):
            digits = '55' + digits
        return digits

    @property
    def whatsapp_url(self):
        """Gera link direto para iniciar conversa no WhatsApp com mensagem de boas-vindas"""
        phone = self.clean_phone
        if not phone:
            return "#"
        corretor_nome = self.agent.get_full_name() if self.agent else "nossa equipe"
        msg = f"Olá {self.client.name}, tudo bem? Sou o {corretor_nome} da imobiliária. Vi seu interesse no imóvel '{self.title}' e gostaria de tirar suas dúvidas."
        encoded_msg = urllib.parse.quote(msg)
        return f"https://wa.me/{phone}?text={encoded_msg}"

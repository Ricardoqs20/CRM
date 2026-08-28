import re
import urllib.parse
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from .leads import PropertyLead
from .properties import Property

class PropertyVisit(models.Model):
    """Gestão de Visitas a Imóveis, Controle de Chaves e Feedback"""
    STATUS_CHOICES = [
        ('scheduled', 'Agendada'),
        ('confirmed', 'Confirmada com Cliente'),
        ('completed', 'Visita Realizada'),
        ('canceled', 'Cancelada'),
        ('no_show', 'Cliente Não Compareceu'),
    ]
    KEY_STATUS_CHOICES = [
        ('concierge', 'Chave na Portaria do Condomínio'),
        ('with_owner', 'Proprietário Recebe no Local'),
        ('at_agency', 'Chave Disponível na Imobiliária'),
        ('with_agent', 'Chave Retirada pelo Corretor'),
        ('returned', 'Chave Devolvida na Imobiliária'),
    ]
    RATING_CHOICES = [
        (1, '⭐ 1 - Muito Fraco / Desistiu'),
        (2, '⭐⭐ 2 - Não Gostou'),
        (3, '⭐⭐⭐ 3 - Razoável / Considera'),
        (4, '⭐⭐⭐⭐ 4 - Gostou Muito'),
        (5, '⭐⭐⭐⭐⭐ 5 - Excelente / Alto Interesse'),
    ]

    lead = models.ForeignKey(PropertyLead, on_delete=models.CASCADE, related_name='visits', verbose_name='Lead / Cliente')
    visit_property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='visits', verbose_name='Imóvel a Visitar')
    agent = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='visits_conducted', verbose_name='Corretor Acompanhante')
    
    scheduled_date = models.DateTimeField(verbose_name='Data e Hora Agendada')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled', verbose_name='Status da Visita')
    meeting_point = models.CharField(max_length=255, blank=True, null=True, default='No próprio imóvel', verbose_name='Ponto de Encontro')

    # Controle de Chaves
    key_status = models.CharField(max_length=20, choices=KEY_STATUS_CHOICES, default='concierge', verbose_name='Status da Chave')
    key_withdrawn_at = models.DateTimeField(blank=True, null=True, verbose_name='Chave Retirada em')
    key_returned_at = models.DateTimeField(blank=True, null=True, verbose_name='Chave Devolvida em')
    key_notes = models.CharField(max_length=255, blank=True, null=True, verbose_name='Observações da Chave')

    # Registro de Feedback Pós-Visita
    client_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, blank=True, null=True, verbose_name='Avaliação do Cliente')
    feedback_notes = models.TextField(blank=True, null=True, verbose_name='Parecer / O que o cliente achou?')
    will_make_proposal = models.BooleanField(default=False, verbose_name='Cliente fará proposta?')
    proposal_details = models.TextField(blank=True, null=True, verbose_name='Condições ou Valores da Proposta')
    rejection_reason = models.CharField(max_length=255, blank=True, null=True, verbose_name='Motivo de Descarte (Preço, Planta, Localização, etc.)')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Visita a Imóvel'
        verbose_name_plural = 'Visitas a Imóveis'
        ordering = ['-scheduled_date']

    def __str__(self):
        return f"Visita: {self.visit_property.code} por {self.lead.client.name} em {self.scheduled_date.strftime('%d/%m/%Y %H:%M')}"

    @property
    def is_key_overdue(self):
        """
        Retorna True se a chave está com o corretor e:
        - Passou mais de 4 horas desde a retirada, OU
        - Já passou 2 horas após o horário agendado da visita
        """
        if self.key_status != 'with_agent' or not self.key_withdrawn_at:
            return False
        now = timezone.now()
        hours_since_withdrawal = (now - self.key_withdrawn_at).total_seconds() / 3600
        hours_after_visit = (now - self.scheduled_date).total_seconds() / 3600
        return hours_since_withdrawal > 4 or hours_after_visit > 2

    @property
    def client_clean_phone(self):
        """Retorna apenas os dígitos do telefone do cliente para links WhatsApp"""
        phone = self.lead.client.phone
        if not phone:
            return ""
        digits = re.sub(r'\D', '', phone)
        if len(digits) in [10, 11] and not digits.startswith('55'):
            digits = '55' + digits
        return digits

    @property
    def whatsapp_confirmation_url(self):
        """
        Gera link para enviar confirmação de visita no WhatsApp do cliente.
        Template: "Olá, [Nome]! Confirmada a nossa visita para o imóvel no [Bairro] ([Endereço]) 
                   no dia [Data] às [Horário]. Te encontro lá!"
        """
        phone = self.client_clean_phone
        if not phone:
            return "#"

        bairro = self.visit_property.neighborhood or "ótima localização"
        endereco = self.visit_property.street or ""
        numero = self.visit_property.number or ""
        local = f"{endereco}, {numero}".strip(', ') if endereco else bairro
        data = self.scheduled_date.strftime('%d/%m/%Y')
        horario = self.scheduled_date.strftime('%H:%M')
        ponto = self.meeting_point or "no próprio imóvel"

        msg = (
            f"Olá, {self.lead.client.name}! "
            f"Confirmada a nossa visita para o imóvel no {bairro} ({local}) "
            f"no dia {data} às {horario}. "
            f"Ponto de encontro: {ponto}. Te encontro lá! 🏠"
        )

        encoded_msg = urllib.parse.quote(msg)
        return f"https://wa.me/{phone}?text={encoded_msg}"

    @property
    def is_feedback_pending(self):
        """Retorna True se a visita foi realizada mas o feedback ainda não foi preenchido"""
        return self.status == 'completed' and self.client_rating is None

    @property
    def is_past(self):
        """Retorna True se o horário agendado já passou"""
        return self.scheduled_date < timezone.now()

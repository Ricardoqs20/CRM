from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from .leads import PropertyLead
from .properties import Property

class Activity(models.Model):
    """Tarefas, Lembretes e Ações de Follow-up"""
    ACTIVITY_TYPES = [
        ('follow_up', 'Follow-up / Acompanhamento'),
        ('whatsapp', 'Mensagem no WhatsApp'),
        ('call', 'Ligação Telefônica'),
        ('meeting', 'Reunião Presencial / Online'),
        ('email', 'Envio de E-mail'),
        ('task', 'Outra Tarefa'),
    ]
    TASK_TYPES = [
        ('call', '📞 Ligar para Cliente'),
        ('visit_followup', '🏠 Follow-up de Visita'),
        ('document', '📄 Documentação / Certidões'),
        ('credit_analysis', '🏦 Análise de Crédito'),
        ('proposal', '📝 Elaborar Proposta'),
        ('general', '📋 Tarefa Geral'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Baixa'),
        ('medium', 'Média'),
        ('high', 'Alta / Urgente'),
    ]

    title = models.CharField(max_length=255, verbose_name='Título da Atividade')
    activity_type = models.CharField(max_length=20, choices=ACTIVITY_TYPES, default='follow_up', verbose_name='Tipo de Atividade')
    task_type = models.CharField(max_length=20, choices=TASK_TYPES, default='general', verbose_name='Categoria da Tarefa')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium', verbose_name='Prioridade')
    description = models.TextField(blank=True, null=True, verbose_name='Detalhes / Instruções')
    
    lead = models.ForeignKey(PropertyLead, on_delete=models.CASCADE, related_name='activities', null=True, blank=True, verbose_name='Lead Relacionado')
    related_property = models.ForeignKey(Property, on_delete=models.SET_NULL, null=True, blank=True, related_name='activities', verbose_name='Imóvel Relacionado')
    assigned_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities', verbose_name='Responsável')
    
    due_date = models.DateTimeField(verbose_name='Data e Hora Limite')
    is_completed = models.BooleanField(default=False, verbose_name='Concluída')
    completed_at = models.DateTimeField(blank=True, null=True, verbose_name='Concluída em')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Atividade / Tarefa'
        verbose_name_plural = 'Atividades e Tarefas'
        ordering = ['is_completed', 'due_date']

    def __str__(self):
        return f"[{self.get_task_type_display()}] {self.title} - {self.assigned_to.username}"

    @property
    def is_overdue(self):
        """Retorna True se a tarefa está atrasada (não concluída e prazo ultrapassado)"""
        return not self.is_completed and self.due_date < timezone.now()

    @property
    def is_today(self):
        """Retorna True se o prazo é hoje"""
        return self.due_date.date() == timezone.now().date()

    @property
    def is_upcoming(self):
        """Retorna True se o prazo está nas próximas 24h (mas não atrasado)"""
        now = timezone.now()
        delta = self.due_date - now
        return not self.is_completed and 0 < delta.total_seconds() <= 86400

    @property
    def task_icon(self):
        """Retorna o emoji do tipo de tarefa para exibição"""
        icons = {
            'call': '📞',
            'visit_followup': '🏠',
            'document': '📄',
            'credit_analysis': '🏦',
            'proposal': '📝',
            'general': '📋',
        }
        return icons.get(self.task_type, '📋')


class InteractionLog(models.Model):
    """Histórico Centralizado / Timeline de Interações do Lead"""
    ACTION_TYPES = [
        ('note', 'Anotação Interna'),
        ('stage_change', 'Mudança de Estágio no Funil'),
        ('whatsapp_sent', 'WhatsApp Registrado'),
        ('call_logged', 'Ligação Registrada'),
        ('visit_scheduled', 'Visita Agendada'),
        ('visit_completed', 'Visita Realizada'),
        ('visit_feedback', 'Feedback de Visita'),
        ('key_action', 'Movimentação de Chave'),
        ('proposal_sent', 'Proposta Enviada'),
        ('status_change', 'Status Alterado'),
        ('task_created', 'Tarefa/Lembrete Criado'),
    ]

    lead = models.ForeignKey(PropertyLead, on_delete=models.CASCADE, related_name='interactions', verbose_name='Lead')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Usuário Responsável')
    action_type = models.CharField(max_length=30, choices=ACTION_TYPES, default='note', verbose_name='Tipo de Registro')
    content = models.TextField(verbose_name='Conteúdo / Descrição do Histórico')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data e Hora')

    class Meta:
        verbose_name = 'Histórico / Interação'
        verbose_name_plural = 'Histórico de Interações'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.lead.title} - {self.get_action_type_display()} ({self.created_at.strftime('%d/%m/%Y %H:%M')})"

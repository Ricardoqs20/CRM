from django.db import models
from django.contrib.auth.models import User


class SecurityAuditLog(models.Model):
    """Trilha imutável de auditoria de eventos críticos de segurança e LGPD."""

    ACTION_CHOICES = [
        ('login_failed', 'Login Falho'),
        ('login_success', 'Login Realizado'),
        ('rate_limit_exceeded', 'Limite de Requisições Excedido (Rate Limit)'),
        ('permission_changed', 'Alteração de Permissões / Papel'),
        ('data_export', 'Exportação de Dados'),
        ('lgpd_anonymize', 'Anonimização de Titular (LGPD)'),
        ('unauthorized_access', 'Tentativa de Acesso Não Autorizado'),
        ('webhook_rejected', 'Webhook Rejeitado (Chave Inválida)'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='security_audit_logs',
        verbose_name='Usuário',
    )
    action = models.CharField(
        max_length=50,
        choices=ACTION_CHOICES,
        db_index=True,
        verbose_name='Ação',
    )
    target = models.CharField(
        max_length=255,
        blank=True,
        default='',
        verbose_name='Alvo / Registro',
    )
    ip_address = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='IP Anonimizado',
    )
    details = models.TextField(
        blank=True,
        default='',
        verbose_name='Detalhes',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Timestamp',
    )

    class Meta:
        verbose_name = 'Log de Auditoria de Segurança'
        verbose_name_plural = 'Logs de Auditoria de Segurança (LGPD)'
        ordering = ['-created_at']

    def __str__(self):
        actor = self.user.username if self.user else 'anônimo/sistema'
        return f"[{self.get_action_display()}] {actor} -> {self.target} ({self.created_at:%d/%m/%Y %H:%M})"

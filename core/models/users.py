from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('manager', 'Gestor / Administrador'),
        ('agent', 'Corretor de Imóveis'),
        ('assistant', 'Assistente / Atendimento'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='agent', verbose_name='Papel')
    creci = models.CharField(max_length=30, blank=True, null=True, verbose_name='CRECI')
    phone = models.CharField(max_length=30, blank=True, null=True, verbose_name='Telefone / WhatsApp')
    monthly_goal = models.DecimalField(max_digits=12, decimal_places=2, default=500000.00, verbose_name='Meta Mensal de VGV (R$)')
    bio = models.TextField(blank=True, null=True, verbose_name='Biografia / Especialidade')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Perfil de Usuário'
        verbose_name_plural = 'Perfis de Usuários'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"

    @property
    def is_manager(self):
        return self.role == 'manager' or self.user.is_superuser

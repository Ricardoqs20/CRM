from django.db import models
from django.contrib.auth.models import User

class Person(models.Model):
    """Cadastro de Contatos / Clientes / Proprietários"""
    CLIENT_TYPES = [
        ('buyer', 'Comprador'),
        ('renter', 'Inquilino'),
        ('owner', 'Proprietário'),
        ('investor', 'Investidor'),
        ('partner', 'Parceiro / Outro'),
    ]

    name = models.CharField(max_length=255, verbose_name='Nome Completo')
    email = models.EmailField(blank=True, null=True, verbose_name='E-mail')
    phone = models.CharField(max_length=50, verbose_name='Telefone / WhatsApp')
    secondary_phone = models.CharField(max_length=50, blank=True, null=True, verbose_name='Telefone Secundário')
    document = models.CharField(max_length=30, blank=True, null=True, verbose_name='CPF / CNPJ')
    client_type = models.CharField(max_length=20, choices=CLIENT_TYPES, default='buyer', verbose_name='Tipo de Contato')
    
    assigned_agent = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_clients', verbose_name='Corretor Responsável'
    )
    notes = models.TextField(blank=True, null=True, verbose_name='Observações Gerais')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Contato / Cliente'
        verbose_name_plural = 'Contatos e Clientes'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.get_client_type_display()} - {self.phone})"


class Company(models.Model):
    """Cadastro de Empresas, Imobiliárias Parceiras e Construtoras"""
    name = models.CharField(max_length=255, verbose_name='Razão Social / Nome')
    trade_name = models.CharField(max_length=255, blank=True, null=True, verbose_name='Nome Fantasia')
    cnpj = models.CharField(max_length=30, blank=True, null=True, verbose_name='CNPJ')
    contact_name = models.CharField(max_length=255, blank=True, null=True, verbose_name='Pessoa de Contato')
    email = models.EmailField(blank=True, null=True, verbose_name='E-mail')
    phone = models.CharField(max_length=50, blank=True, null=True, verbose_name='Telefone')
    notes = models.TextField(blank=True, null=True, verbose_name='Observações')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Empresa / Parceiro'
        verbose_name_plural = 'Empresas e Parceiros'

    def __str__(self):
        return self.trade_name or self.name


class ClientPreference(models.Model):
    """Bloco de Preferências Detalhado do Cliente para Cruzamento (Match)"""
    TRANSACTION_CHOICES = [
        ('buy', 'Comprar'),
        ('rent', 'Alugar'),
        ('invest', 'Investir'),
    ]
    PAYMENT_METHODS = [
        ('any', 'Qualquer / Indiferente'),
        ('cash', 'À Vista (Recursos Próprios)'),
        ('financing', 'Financiamento Bancário'),
        ('exchange', 'Aceita Permuta'),
        ('installments', 'Direto com a Construtora'),
    ]

    person = models.OneToOneField(Person, on_delete=models.CASCADE, related_name='preferences', verbose_name='Cliente')
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_CHOICES, default='buy', verbose_name='Tipo de Negócio')
    
    # Preferência de tipologia (ex: ['apartment', 'house', 'penthouse'])
    property_types = models.JSONField(default=list, blank=True, verbose_name='Tipos de Imóveis de Interesse')
    
    # Bairros/regiões aceitos (ex: ["Ponta D'Areia", "Renascença", "Calhau"])
    preferred_locations = models.JSONField(default=list, blank=True, verbose_name='Bairros / Regiões de Interesse')
    
    # Faixa de Preço
    min_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Preço Mínimo (R$)')
    max_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Preço Máximo (R$)')
    
    # Condição de Pagamento
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='any', verbose_name='Forma de Pagamento Preferida')
    
    # Características Físicas Mínimas
    min_bedrooms = models.PositiveIntegerField(default=0, verbose_name='Mínimo de Quartos')
    min_suites = models.PositiveIntegerField(default=0, verbose_name='Mínimo de Suítes')
    min_bathrooms = models.PositiveIntegerField(default=0, verbose_name='Mínimo de Banheiros')
    min_parking_spaces = models.PositiveIntegerField(default=0, verbose_name='Mínimo de Vagas')
    min_area_m2 = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, verbose_name='Metragem Mínima (m²)')
    
    # Diferenciais / Exigências
    pets_allowed = models.BooleanField(default=False, verbose_name='Aceita Pet (Obrigatório)')
    gourmet_balcony = models.BooleanField(default=False, verbose_name='Varanda Gourmet (Obrigatório)')
    pool_or_leisure = models.BooleanField(default=False, verbose_name='Lazer Completo no Condomínio')
    morning_sun = models.BooleanField(default=False, verbose_name='Sol da Manhã (Nascente)')
    elevator = models.BooleanField(default=False, verbose_name='Precisa de Elevador')
    
    # Filtros customizados pelo próprio corretor (armazenados dinamicamente)
    custom_filters = models.JSONField(default=dict, blank=True, verbose_name='Filtros Customizados do Corretor')
    
    # Notas comportamentais
    notes = models.TextField(blank=True, null=True, verbose_name='Observações Comportamentais / Específicas')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Preferência do Cliente'
        verbose_name_plural = 'Preferências dos Clientes'

    def __str__(self):
        return f"Preferências de {self.person.name} ({self.get_transaction_type_display()})"

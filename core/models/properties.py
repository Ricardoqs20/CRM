from django.db import models
from django.contrib.auth.models import User
from .clients import Person

class Property(models.Model):
    """Cadastro Detalhado de Imóveis (Estoque & Captação)"""
    PROPERTY_TYPES = [
        ('apartment', 'Apartamento'),
        ('house', 'Casa / Sobrado'),
        ('penthouse', 'Cobertura'),
        ('land', 'Terreno / Lote'),
        ('commercial', 'Sala / Ponto Comercial'),
        ('studio', 'Studio / Flat'),
        ('rural', 'Chácara / Fazenda'),
    ]
    TRANSACTION_TYPES = [
        ('sale', 'Venda'),
        ('rent', 'Locação'),
        ('both', 'Venda e Locação'),
    ]
    STATUS_CHOICES = [
        ('available', 'Disponível'),
        ('draft', 'Rascunho / Cadastro Rápido'),
        ('under_negotiation', 'Em Negociação'),
        ('reserved', 'Reservado'),
        ('sold', 'Vendido'),
        ('rented', 'Alugado'),
        ('inactive', 'Inativo / Arquivado'),
    ]

    # Identificação
    code = models.CharField(max_length=50, unique=True, verbose_name='Código de Referência (ex: AP0102)')
    title = models.CharField(max_length=255, verbose_name='Título do Anúncio')
    description = models.TextField(blank=True, null=True, verbose_name='Descrição Completa')
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPES, default='apartment', verbose_name='Tipo de Imóvel')
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES, default='sale', verbose_name='Finalidade')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='available', verbose_name='Status')

    # Valores Financeiros
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Valor de Venda (R$)')
    rental_price = models.DecimalField(max_digits=12, decimal_places=2, default=0.00, verbose_name='Valor de Locação (R$)')
    condo_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name='Condomínio (R$)')
    iptu = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name='IPTU Anual / Mensal (R$)')

    # Localização
    building_name = models.CharField(max_length=150, blank=True, null=True, verbose_name='Nome do Edifício / Condomínio')
    street = models.CharField(max_length=255, blank=True, null=True, verbose_name='Endereço / Rua')
    number = models.CharField(max_length=20, blank=True, null=True, verbose_name='Número')
    complement = models.CharField(max_length=100, blank=True, null=True, verbose_name='Complemento / Bloco / Apt')
    neighborhood = models.CharField(max_length=100, verbose_name='Bairro')
    city = models.CharField(max_length=100, default='São Luís', verbose_name='Cidade')
    state = models.CharField(max_length=2, default='MA', verbose_name='UF')
    zip_code = models.CharField(max_length=20, blank=True, null=True, verbose_name='CEP')

    # Dimensões e Distribuição
    usable_area = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, verbose_name='Área Privativa (m²)')
    total_area = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, verbose_name='Área Total (m²)')
    bedrooms = models.PositiveIntegerField(default=0, verbose_name='Quartos')
    suites = models.PositiveIntegerField(default=0, verbose_name='Suítes')
    bathrooms = models.PositiveIntegerField(default=1, verbose_name='Banheiros')
    parking_spaces = models.PositiveIntegerField(default=0, verbose_name='Vagas de Garagem')
    floor = models.CharField(max_length=30, blank=True, null=True, verbose_name='Andar / Posição')

    # Características e Diferenciais
    pets_allowed = models.BooleanField(default=False, verbose_name='Aceita Pets')
    gourmet_balcony = models.BooleanField(default=False, verbose_name='Varanda Gourmet')
    pool = models.BooleanField(default=False, verbose_name='Piscina')
    gym = models.BooleanField(default=False, verbose_name='Academia')
    elevator = models.BooleanField(default=False, verbose_name='Elevador')
    morning_sun = models.BooleanField(default=False, verbose_name='Sol da Manhã (Nascente)')
    furnished = models.BooleanField(default=False, verbose_name='Mobiliado / Semi-mobiliado')
    extra_features = models.JSONField(default=list, blank=True, verbose_name='Diferenciais Extras')

    # Controle de Captação & Proprietário
    owner = models.ForeignKey(
        Person, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='owned_properties', verbose_name='Proprietário'
    )
    captured_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='captured_properties', verbose_name='Corretor Captador'
    )
    is_exclusive = models.BooleanField(default=False, verbose_name='Contrato de Exclusividade')
    exclusivity_start = models.DateField(blank=True, null=True, verbose_name='Início da Exclusividade')
    exclusivity_end = models.DateField(blank=True, null=True, verbose_name='Fim da Exclusividade')
    agreed_commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=5.00, verbose_name='Comissão Combinada (%)')
    share_owner_contact = models.BooleanField(default=False, verbose_name='Compartilhar Contato do Proprietário com Outros Corretores')
    key_location = models.CharField(max_length=200, default='Portaria', verbose_name='Localização das Chaves')

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Cadastrado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Imóvel'
        verbose_name_plural = 'Imóveis (Estoque)'
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.code}] {self.title} - {self.neighborhood} (R$ {self.sale_price if self.sale_price > 0 else self.rental_price})"

    def can_view_owner(self, user):
        """Verifica se o usuário pode visualizar os dados de contato do proprietário"""
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if hasattr(user, 'profile') and user.profile.is_manager:
            return True
        if self.captured_by == user:
            return True
        return self.share_owner_contact

    @property
    def cover_image(self):
        """Retorna a imagem de capa ou a primeira imagem cadastrada"""
        featured = self.images.filter(is_featured=True).first()
        if featured:
            return featured.image.url
        first = self.images.first()
        return first.image.url if first else None


class PropertyImage(models.Model):
    """Fotos do Imóvel"""
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='images', verbose_name='Imóvel')
    image = models.ImageField(upload_to='properties/%Y/%m/', verbose_name='Arquivo de Imagem')
    caption = models.CharField(max_length=255, blank=True, null=True, verbose_name='Legenda / Ambiente')
    is_featured = models.BooleanField(default=False, verbose_name='Foto Principal de Capa')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem de Exibição')

    class Meta:
        verbose_name = 'Foto do Imóvel'
        verbose_name_plural = 'Fotos dos Imóveis'
        ordering = ['order', 'id']

    def __str__(self):
        return f"Foto de {self.property.code} ({self.caption or 'Sem legenda'})"

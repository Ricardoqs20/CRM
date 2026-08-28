import urllib.parse
import re
from django.db import models

class WhatsAppTemplate(models.Model):
    """Modelos de Mensagens Rápidas do WhatsApp com Interpolação de Variáveis"""
    CATEGORY_CHOICES = [
        ('welcome', '👋 Primeiro Contato / Boas-vindas'),
        ('match', '🏠 Apresentação de Imóvel (Match)'),
        ('visit', '📅 Confirmação / Agendamento de Visita'),
        ('credit_doc', '📄 Documentação & Análise de Crédito'),
        ('proposal', '🤝 Proposta & Negociação'),
        ('follow_up', '⏰ Reativação / Follow-up'),
        ('general', '💬 Mensagem Geral'),
    ]

    title = models.CharField(max_length=255, verbose_name='Título do Modelo')
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='welcome', verbose_name='Categoria')
    content = models.TextField(
        verbose_name='Conteúdo da Mensagem',
        help_text='Tags disponíveis: {nome_cliente}, {primeiro_nome}, {codigo_imovel}, {titulo_imovel}, {bairro}, {valor}, {nome_corretor}, {telefone_corretor}'
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Modelo de Mensagem WhatsApp'
        verbose_name_plural = 'Modelos de Mensagens WhatsApp'
        ordering = ['category', 'title']

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"

    def render_text(self, lead, property_obj=None, agent=None):
        """Interpola as variáveis dinâmicas com os dados reais do lead e do imóvel"""
        text = self.content

        # Dados do Cliente
        client_name = lead.client.name if lead and lead.client else "Cliente"
        first_name = client_name.split()[0] if client_name else "Cliente"

        # Imóvel
        prop = property_obj
        if not prop and lead and lead.interested_properties.exists():
            prop = lead.interested_properties.first()

        property_code = prop.code if prop else "imóvel selecionado"
        property_title = prop.title if prop else (lead.title if lead else "")
        bairro = prop.neighborhood if prop and prop.neighborhood else (lead.preferred_location if lead and lead.preferred_location else "ótima localização")
        
        # Valor
        if prop and prop.sale_price and prop.sale_price > 0:
            valor = f"R$ {prop.sale_price:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        elif lead and lead.budget and lead.budget > 0:
            valor = f"R$ {lead.budget:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        else:
            valor = "sob consulta"

        # Corretor
        ag = agent or (lead.agent if lead else None)
        agent_name = ag.get_full_name() or ag.username if ag else "Nosso consultor"
        agent_phone = ag.profile.phone if ag and hasattr(ag, 'profile') and ag.profile.phone else ""

        # Substituição de Tags
        text = text.replace('{nome_cliente}', client_name)
        text = text.replace('{primeiro_nome}', first_name)
        text = text.replace('{codigo_imovel}', property_code)
        text = text.replace('{titulo_imovel}', property_title)
        text = text.replace('{bairro}', bairro)
        text = text.replace('{valor}', valor)
        text = text.replace('{nome_corretor}', agent_name)
        text = text.replace('{telefone_corretor}', agent_phone)

        return text

    def render_url(self, lead, property_obj=None, agent=None):
        """Retorna o link https://wa.me/55... pronto para envio com o texto interpolado"""
        if not lead or not lead.client or not lead.client.phone:
            return "#"

        digits = re.sub(r'\D', '', lead.client.phone)
        if len(digits) in [10, 11] and not digits.startswith('55'):
            digits = '55' + digits

        if not digits:
            return "#"

        rendered_text = self.render_text(lead, property_obj, agent)
        encoded_msg = urllib.parse.quote(rendered_text)
        return f"https://wa.me/{digits}?text={encoded_msg}"

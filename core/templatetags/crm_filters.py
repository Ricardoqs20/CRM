import re
from decimal import Decimal
from django import template
from core.utils import format_brl, parse_brl_decimal

register = template.Library()


@register.filter(name='clean_demo_text')
def clean_demo_text_filter(value):
    """Remove marcadores internos de seed ([DEMO-RICARDO], [Demo]) e frases artificiais de exibição."""
    if not value:
        return ''
    text = str(value).strip()
    text = re.sub(r'\[DEMO-RICARDO\]\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[Demo(?:\s+#\d+)?\]\s*', '', text, flags=re.IGNORECASE)
    if re.match(r'^(Tarefa simulada vinculada a|Imóvel simulado em|Preferência simulada)\b', text, flags=re.IGNORECASE):
        return ''
    return text.strip()


@register.filter(name='brl')
def brl_filter(value):
    """Formata valor monetário em padrão brasileiro (ex: 500000 -> 500.000; 500000.50 -> 500.000,50)."""
    return format_brl(value, force_cents=False)


@register.filter(name='brl_int')
def brl_int_filter(value):
    """Formata valor monetário inteiro com pontos de milhar (ex: 500000.00 -> 500.000)."""
    dec = parse_brl_decimal(value, default=Decimal('0.00'))
    int_val = int(dec.quantize(Decimal('1')))
    return f"{int_val:,}".replace(',', '.')


@register.filter(name='brl_input')
def brl_input_filter(value):
    """Formata valor para inputs com máscara data-money-input (retorna vazio se 0/None)."""
    dec = parse_brl_decimal(value, default=Decimal('0.00'))
    if dec <= 0:
        return ''
    return format_brl(dec, force_cents=False)

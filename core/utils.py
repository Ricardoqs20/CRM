from decimal import Decimal, InvalidOperation


def parse_brl_decimal(value, default: Decimal = Decimal('0.00')) -> Decimal:
    """
    Converte strings monetárias brasileiras ou numéricas em Decimal válido.
    Exemplos:
      - "500.000" -> Decimal('500000.00')
      - "1.250.000" -> Decimal('1250000.00')
      - "500.000,00" -> Decimal('500000.00')
      - "750000.00" -> Decimal('750000.00')
      - "" / None -> default
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return default

    raw = str(value).strip()
    if not raw:
        return default

    cleaned = raw.replace('R$', '').replace(' ', '').strip()
    if not cleaned:
        return default

    if ',' in cleaned and '.' in cleaned:
        # Formato pt-BR completo: 1.250.000,50
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        # Formato com vírgula decimal: 500000,50
        cleaned = cleaned.replace(',', '.')
    elif '.' in cleaned:
        parts = cleaned.split('.')
        # Se houver múltiplos pontos (1.250.000) ou exatamente 3 dígitos após o ponto (500.000),
        # trata como separador de milhar brasileiro.
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit()):
            cleaned = ''.join(parts)

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return default


def format_brl(value, force_cents: bool = False) -> str:
    """
    Formata número no padrão brasileiro com pontos de milhar:
      - 500000 -> "500.000" (ou "500.000,00" se force_cents=True)
      - 500000.50 -> "500.000,50"
    """
    dec = parse_brl_decimal(value, default=Decimal('0.00'))
    is_negative = dec < 0
    dec = abs(dec)

    quantized = dec.quantize(Decimal('0.01'))
    has_cents = (quantized % 1) != 0

    if force_cents or has_cents:
        formatted = f"{quantized:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    else:
        int_val = int(quantized)
        formatted = f"{int_val:,}".replace(',', '.')

    return f"-{formatted}" if is_negative else formatted

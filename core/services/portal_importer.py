import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from html.parser import HTMLParser

from core.utils import format_brl, parse_brl_decimal

MAX_DOWNLOAD_BYTES = 1024 * 1024  # 1 MB limite de segurança
REQUEST_TIMEOUT_SECONDS = 6
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.0.0 Safari/537.36'
)

BLOCKED_HOSTNAMES = {
    'localhost',
    'localhost.localdomain',
    'ip6-localhost',
    'ip6-loopback',
    'metadata.google.internal',
}


class SSRFBlockedError(ValueError):
    """Erro levantado quando uma URL falha na validação anti-SSRF."""


def _is_forbidden_ip(ip_obj) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_reserved
        or ip_obj.is_multicast
        or ip_obj.is_unspecified
        or not ip_obj.is_global
    )


def validate_safe_public_url(url: str) -> urllib.parse.ParseResult:
    """
    Valida que a URL usa http/https e aponta exclusivamente para endereços públicos,
    bloqueando localhost, domínios locais e IPs privados/reservados/link-local (Anti-SSRF).
    """
    raw = (url or '').strip()
    if not raw:
        raise SSRFBlockedError('Informe uma URL válida para importar.')

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() not in ('http', 'https'):
        raise SSRFBlockedError('Esquema inválido. Utilize apenas links http:// ou https://.')

    hostname = (parsed.hostname or '').strip().lower()
    if not hostname:
        raise SSRFBlockedError('URL inválida: domínio não identificado.')

    if (
        hostname in BLOCKED_HOSTNAMES
        or hostname.endswith('.local')
        or hostname.endswith('.localhost')
        or hostname.endswith('.internal')
        or hostname.endswith('.lan')
    ):
        raise SSRFBlockedError('Acesso bloqueado: domínios locais ou internos não são permitidos.')

    # Verifica se o hostname já é um IP literal
    try:
        ip_literal = ipaddress.ip_address(hostname)
        if _is_forbidden_ip(ip_literal):
            raise SSRFBlockedError('Acesso bloqueado: endereços IP privados ou reservados não são permitidos.')
        return parsed
    except ValueError:
        pass

    port = parsed.port or (443 if parsed.scheme.lower() == 'https' else 80)
    try:
        addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f'Não foi possível resolver o domínio "{hostname}".') from exc

    if not addr_info:
        raise SSRFBlockedError(f'Nenhum endereço encontrado para "{hostname}".')

    for entry in addr_info:
        ip_str = entry[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_forbidden_ip(ip_obj):
            raise SSRFBlockedError('Acesso bloqueado: o domínio resolve para um endereço IP privado ou reservado.')

    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Valida cada destino de redirecionamento HTTP contra SSRF."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute_url = urllib.parse.urljoin(req.full_url, newurl)
        validate_safe_public_url(absolute_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ListingHTMLParser(HTMLParser):
    """Parser leve da stdlib para extrair meta tags, Open Graph, título e blocos JSON-LD."""

    def __init__(self):
        super().__init__()
        self.meta_tags = {}
        self.json_ld_blocks = []
        self._in_title = False
        self._title_parts = []
        self._in_json_ld = False
        self._json_ld_parts = []

    def handle_starttag(self, tag, attrs):
        tag_lower = tag.lower()
        attrs_dict = {str(k).lower(): str(v or '') for k, v in attrs}

        if tag_lower == 'meta':
            key = (attrs_dict.get('property') or attrs_dict.get('name') or '').strip().lower()
            content = attrs_dict.get('content', '').strip()
            if key and content and key not in self.meta_tags:
                self.meta_tags[key] = content
        elif tag_lower == 'title':
            self._in_title = True
            self._title_parts = []
        elif tag_lower == 'script':
            script_type = attrs_dict.get('type', '').strip().lower()
            if 'ld+json' in script_type:
                self._in_json_ld = True
                self._json_ld_parts = []

    def handle_endtag(self, tag):
        tag_lower = tag.lower()
        if tag_lower == 'title':
            self._in_title = False
        elif tag_lower == 'script' and self._in_json_ld:
            self._in_json_ld = False
            raw_json = ''.join(self._json_ld_parts).strip()
            if raw_json:
                self.json_ld_blocks.append(raw_json)

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_json_ld:
            self._json_ld_parts.append(data)

    @property
    def page_title(self) -> str:
        return ' '.join(''.join(self._title_parts).split()).strip()


PROPERTY_LD_TYPES = {
    'apartment',
    'house',
    'singlefamilyresidence',
    'residence',
    'realestatelisting',
    'product',
    'offer',
    'place',
    'accommodation',
}


def _collect_ld_nodes(obj):
    """Achata objetos/listas/@graph de JSON-LD em uma lista de dicts."""
    nodes = []
    if isinstance(obj, dict):
        nodes.append(obj)
        if isinstance(obj.get('@graph'), list):
            for item in obj['@graph']:
                nodes.extend(_collect_ld_nodes(item))
    elif isinstance(obj, list):
        for item in obj:
            nodes.extend(_collect_ld_nodes(item))
    return nodes


def _extract_from_json_ld(blocks: list[str]) -> dict:
    extracted = {}
    for raw in blocks:
        try:
            parsed = json.loads(raw)
        except Exception:
            continue

        for node in _collect_ld_nodes(parsed):
            node_type = node.get('@type')
            if isinstance(node_type, list):
                types_lower = {str(t).lower() for t in node_type}
            else:
                types_lower = {str(node_type or '').lower()}

            if not (types_lower & PROPERTY_LD_TYPES):
                continue

            if not extracted.get('title') and (node.get('name') or node.get('headline')):
                extracted['title'] = str(node.get('name') or node.get('headline')).strip()

            if not extracted.get('description') and node.get('description'):
                extracted['description'] = str(node.get('description')).strip()

            if not extracted.get('image_url') and node.get('image'):
                img = node['image']
                if isinstance(img, list) and img:
                    img = img[0]
                if isinstance(img, dict):
                    img = img.get('url') or img.get('contentUrl') or ''
                if isinstance(img, str) and img.startswith(('http://', 'https://')):
                    extracted['image_url'] = img.strip()

            offers = node.get('offers')
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict) and not extracted.get('price'):
                price_val = offers.get('price') or offers.get('lowPrice')
                if price_val is not None:
                    extracted['price'] = parse_brl_decimal(price_val)
            elif node.get('price') is not None and not extracted.get('price'):
                extracted['price'] = parse_brl_decimal(node.get('price'))

            if not extracted.get('bedrooms'):
                rooms = node.get('numberOfBedrooms') or node.get('numberOfRooms')
                if rooms is not None:
                    try:
                        extracted['bedrooms'] = int(float(str(rooms)))
                    except (TypeError, ValueError):
                        pass

            if not extracted.get('bathrooms'):
                baths = node.get('numberOfBathroomsTotal')
                if baths is not None:
                    try:
                        extracted['bathrooms'] = int(float(str(baths)))
                    except (TypeError, ValueError):
                        pass

            if not extracted.get('area_m2'):
                floor_size = node.get('floorSize')
                if isinstance(floor_size, dict):
                    floor_size = floor_size.get('value')
                if floor_size is not None:
                    parsed_area = parse_brl_decimal(floor_size)
                    if parsed_area > 0:
                        extracted['area_m2'] = (
                            str(int(parsed_area)) if parsed_area % 1 == 0 else str(parsed_area)
                        )

            address = node.get('address')
            if isinstance(address, dict) and not extracted.get('neighborhood'):
                neighborhood = (
                    address.get('neighborhood')
                    or address.get('addressLocality')
                    or address.get('streetAddress')
                    or ''
                )
                if neighborhood:
                    extracted['neighborhood'] = str(neighborhood).strip()[:100]

    return extracted


def _regex_extract_first_int(pattern: str, text: str):
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def extract_property_from_html(html_text: str, source_url: str) -> dict:
    """Extrai dados imobiliários estruturados (JSON-LD, Open Graph e heurísticas de texto)."""
    parser = _ListingHTMLParser()
    parser.feed(html_text or '')

    meta = parser.meta_tags
    ld_data = _extract_from_json_ld(parser.json_ld_blocks)

    title = (
        ld_data.get('title')
        or meta.get('og:title')
        or meta.get('twitter:title')
        or parser.page_title
        or ''
    ).strip()[:255]

    description = (
        ld_data.get('description')
        or meta.get('og:description')
        or meta.get('description')
        or ''
    ).strip()[:2000]

    image_url = (
        ld_data.get('image_url')
        or meta.get('og:image')
        or meta.get('twitter:image')
        or ''
    ).strip()

    combined_text = "\n".join(
        p for p in (
            title,
            meta.get('og:title', ''),
            parser.page_title,
            description,
            meta.get('og:description', ''),
        ) if p
    )

    # Preço: JSON-LD -> meta tags -> regex "R$ ..."
    price_dec = ld_data.get('price') or Decimal('0.00')
    if price_dec <= 0:
        for meta_key in ('product:price:amount', 'og:price:amount', 'price'):
            if meta.get(meta_key):
                candidate = parse_brl_decimal(meta[meta_key])
                if candidate > 0:
                    price_dec = candidate
                    break
    if price_dec <= 0:
        price_match = re.search(r'R\$\s*([\d\.]+(?:,\d{2})?)', combined_text)
        if price_match:
            price_dec = parse_brl_decimal(price_match.group(1))

    bedrooms = ld_data.get('bedrooms') or _regex_extract_first_int(
        r'(\d+)\s*(?:quartos?|dormit[oó]rios?|qts?)\b',
        combined_text,
    )
    suites = _regex_extract_first_int(r'(\d+)\s*su[ií]tes?\b', combined_text)
    bathrooms = ld_data.get('bathrooms') or _regex_extract_first_int(
        r'(\d+)\s*(?:banheiros?|bwc|wc)\b',
        combined_text,
    )
    parking_spaces = _regex_extract_first_int(
        r'(\d+)\s*(?:vagas?(?:\s+de\s+garagem)?|garagens?)\b',
        combined_text,
    )

    area_m2 = ld_data.get('area_m2')
    if not area_m2:
        area_match = re.search(
            r'(\d+(?:[.,]\d+)?)\s*(?:m²|m2|metros?\s+quadrados?)\b',
            combined_text,
            re.IGNORECASE,
        )
        if area_match:
            area_dec = parse_brl_decimal(area_match.group(1))
            if area_dec > 0:
                area_m2 = str(int(area_dec)) if area_dec % 1 == 0 else str(area_dec)

    neighborhood = ld_data.get('neighborhood') or ''
    if not neighborhood:
        neigh_match = re.search(
            r'(?:bairro|no|na|em)\s+([A-ZÀ-Ú][A-Za-zÀ-ú\'\s]{2,35})(?:\s*[-–,|.]|$)',
            combined_text,
        )
        if neigh_match:
            neighborhood = neigh_match.group(1).strip()[:100]

    transaction_type = 'rent' if re.search(r'\b(alug(?:uel|a-se)|loca[çc][ãa]o)\b', combined_text, re.IGNORECASE) else 'sale'

    parsed_url = urllib.parse.urlparse(source_url)
    source_domain = (parsed_url.hostname or '').lower().removeprefix('www.')

    data = {
        'title': title,
        'price': str(price_dec) if price_dec > 0 else '',
        'price_formatted': format_brl(price_dec) if price_dec > 0 else '',
        'description': description,
        'neighborhood': neighborhood,
        'bedrooms': bedrooms,
        'suites': suites,
        'bathrooms': bathrooms,
        'parking_spaces': parking_spaces,
        'area_m2': area_m2 or '',
        'image_url': image_url,
        'transaction_type': transaction_type,
        'source_domain': source_domain,
    }

    field_labels = {
        'title': 'Título',
        'price_formatted': 'Valor',
        'description': 'Descrição',
        'neighborhood': 'Bairro/Localização',
        'bedrooms': 'Quartos',
        'suites': 'Suítes',
        'bathrooms': 'Banheiros',
        'parking_spaces': 'Vagas',
        'area_m2': 'Área (m²)',
        'image_url': 'Foto principal',
    }
    found_fields = [label for key, label in field_labels.items() if data.get(key) not in (None, '')]
    missing_fields = [label for key, label in field_labels.items() if data.get(key) in (None, '')]

    return {
        'data': data,
        'found_fields': found_fields,
        'missing_fields': missing_fields,
        'source_domain': source_domain,
    }


def fetch_and_extract_property(url: str) -> dict:
    """Executa requisição HTTP segura (Anti-SSRF) e extrai dados do anúncio imobiliário."""
    validate_safe_public_url(url)

    opener = urllib.request.build_opener(_SafeRedirectHandler())
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        },
    )

    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            raw_bytes = resp.read(MAX_DOWNLOAD_BYTES + 1)
            if len(raw_bytes) > MAX_DOWNLOAD_BYTES:
                raw_bytes = raw_bytes[:MAX_DOWNLOAD_BYTES]
            charset = resp.headers.get_content_charset() or 'utf-8'
            html_text = raw_bytes.decode(charset, errors='replace')
            final_url = resp.geturl() or url
    except SSRFBlockedError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise RuntimeError(
                f'O portal bloqueou a leitura automatizada (HTTP {exc.code}). '
                'Preencha os campos do anúncio manualmente abaixo.'
            ) from exc
        raise RuntimeError(f'Não foi possível acessar o anúncio (HTTP {exc.code}).') from exc
    except Exception as exc:
        raise RuntimeError(
            'Não foi possível conectar ao link informado dentro do tempo limite. '
            'Verifique a URL ou preencha os campos manualmente.'
        ) from exc

    return extract_property_from_html(html_text, final_url)

import io
import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw

from core.models import (
    Activity,
    ClientPreference,
    InteractionLog,
    Person,
    Pipeline,
    Property,
    PropertyImage,
    PropertyLead,
    PropertyVisit,
    Stage,
    UserProfile,
    WhatsAppTemplate,
)

TARGET_EMAIL = 'ricardoqsantiago20@gmail.com'
DEMO_ORIGIN = 'demo_ricardo'
DEMO_TAG = '[DEMO-RICARDO]'
DEMO_PROP_PREFIX = 'DEMO-'
DEMO_TPL_PREFIX = '[Demo] '

NEIGHBORHOODS = [
    'Renascença',
    'Calhau',
    "Ponta d'Areia",
    "Olho d'Água",
    'Cohama',
    'Turu',
]

STAGES_SPEC = [
    (1, 'Novo Lead', 'open', '#2563eb'),
    (2, 'Em Atendimento', 'open', '#d97706'),
    (3, 'Visita Agendada', 'open', '#7c3aed'),
    (4, 'Proposta / Negociação', 'open', '#db2777'),
    (5, 'Fechado (Ganho)', 'won', '#059669'),
    (6, 'Perdido (Arquivado)', 'lost', '#64748b'),
]

DEMO_CONTACTS = [
    ('Ana Clara Ferreira', 'buyer', '(98) 98101-1101', '111.222.333-01', 'Busca apartamento próximo à Lagoa da Jansen.'),
    ('Bruno Henrique Costa', 'buyer', '(98) 98102-1102', '111.222.333-02', 'Interesse em financiamento Caixa, entrada de 30%.'),
    ('Camila Ribeiro Santos', 'renter', '(98) 98103-1103', '111.222.333-03', 'Mudança imediata para São Luís a trabalho.'),
    ('Daniel Oliveira Lima', 'owner', '(98) 98104-1104', '111.222.333-04', 'Proprietário de 2 apartamentos no Renascença.'),
    ('Eduarda Martins Silva', 'buyer', '(98) 98105-1105', '111.222.333-05', 'Procura casa em condomínio fechado com área de lazer.'),
    ('Felipe Augusto Rocha', 'investor', '(98) 98106-1106', '111.222.333-06', 'Investidor buscando imóveis para renda de locação.'),
    ('Gabriela Almeida Souza', 'buyer', '(98) 98107-1107', '111.222.333-07', 'Precisa de imóvel que aceite pets de médio porte.'),
    ('Henrique Barros Mendes', 'renter', '(98) 98108-1108', '111.222.333-08', 'Busca sala comercial ou studio mobiliado.'),
    ('Isabela Cardoso Nunes', 'buyer', '(98) 98109-1109', '111.222.333-09', 'Preferência por andar alto e sol da manhã.'),
    ('João Pedro Teixeira', 'owner', '(98) 98110-1110', '111.222.333-10', 'Proprietário de cobertura na Ponta d\'Areia.'),
    ('Karina Duarte Moreira', 'buyer', '(98) 98111-1111', '111.222.333-11', 'Acompanha imóveis no Calhau e Olho d\'Água.'),
    ('Leonardo Pires Gomes', 'renter', '(98) 98112-1112', '111.222.333-12', 'Médico buscando locação próxima ao Renascença.'),
    ('Mariana Castro Araujo', 'buyer', '(98) 98113-1113', '111.222.333-13', 'Família com 2 filhos, mínimo 3 quartos e 2 vagas.'),
    ('Natália Vieira Campos', 'investor', '(98) 98114-1114', '111.222.333-14', 'Interesse em terrenos e lotes comerciais no Turu.'),
    ('Otávio Nogueira Dias', 'owner', '(98) 98115-1115', '111.222.333-15', 'Proprietário de casa no Calhau disponível para venda.'),
    ('Patrícia Lopes Batista', 'buyer', '(98) 98116-1116', '111.222.333-16', 'Pagamento à vista mediante venda de imóvel atual.'),
    ('Rafael Azevedo Cunha', 'renter', '(98) 98117-1117', '111.222.333-17', 'Procura apartamento 2 quartos na Cohama.'),
    ('Sabrina Moura Freitas', 'buyer', '(98) 98118-1118', '111.222.333-18', 'Quer varanda gourmet e elevador.'),
    ('Thiago Santana Borges', 'owner', '(98) 98119-1119', '111.222.333-19', 'Proprietário de salas comerciais no Renascença.'),
    ('Ursula Macedo Tavares', 'buyer', '(98) 98120-1120', '111.222.333-20', 'Busca primeiro imóvel pelo programa de financiamento.'),
    ('Vinícius Correia Pinto', 'renter', '(98) 98121-1121', '111.222.333-21', 'Contrato corporativo de 24 meses.'),
    ('Wanessa Monteiro Reis', 'buyer', '(98) 98122-1122', '111.222.333-22', 'Interesse em cobertura duplex vista mar.'),
    ('Xavier Fonseca Ramos', 'investor', '(98) 98123-1123', '111.222.333-23', 'Compra salas comerciais e studios para Airbnb.'),
    ('Yasmin Coelho Andrade', 'buyer', '(98) 98124-1124', '111.222.333-24', 'Decisão conjunta com cônjuge aos sábados.'),
    ('Zeca Fernandes Barros', 'owner', '(98) 98125-1125', '111.222.333-25', 'Proprietário de terreno amplo na Cohama.'),
    ('Aline Vasconcelos Melo', 'buyer', '(98) 98126-1126', '111.222.333-26', 'Prioriza segurança 24h e área de lazer completa.'),
    ('Bernardo Guimarães Luz', 'renter', '(98) 98127-1127', '111.222.333-27', 'Aceita permuta ou locação com opção de compra.'),
    ('Cláudia Peixoto Braga', 'buyer', '(98) 98128-1128', '111.222.333-28', 'Contato vindo de indicação de cliente antigo.'),
]

DEMO_PROPERTIES = [
    ('AP01', 'Apartamento Vista Lagoa 3 Quartos', 'apartment', 'sale', 'available', Decimal('680000'), Decimal('0'), 'Renascença', 98, 3, 1, 3, 2, True, True),
    ('AP02', 'Apartamento Nascente Varanda Gourmet', 'apartment', 'sale', 'available', Decimal('540000'), Decimal('0'), 'Cohama', 84, 3, 1, 2, 2, True, False),
    ('AP03', 'Apartamento Mobiliado Ponta d\'Areia', 'apartment', 'rent', 'available', Decimal('0'), Decimal('3800'), "Ponta d'Areia", 76, 2, 1, 2, 1, False, True),
    ('AP04', 'Apartamento Compacto 2 Quartos Turu', 'apartment', 'both', 'available', Decimal('340000'), Decimal('2100'), 'Turu', 62, 2, 0, 1, 1, False, True),
    ('AP05', 'Apartamento Alto Padrão Beira Mar', 'apartment', 'sale', 'reserved', Decimal('1150000'), Decimal('0'), "Ponta d'Areia", 145, 3, 3, 4, 3, True, True),
    ('AP06', 'Apartamento 3 Suítes Jardins do Calhau', 'apartment', 'sale', 'under_negotiation', Decimal('890000'), Decimal('0'), 'Calhau', 128, 3, 3, 4, 2, True, True),
    ('AP07', 'Apartamento 2 Quartos Olho d\'Água', 'apartment', 'rent', 'rented', Decimal('0'), Decimal('2600'), "Olho d'Água", 68, 2, 1, 2, 1, False, False),
    ('AP08', 'Apartamento Reformado Parque Renascença', 'apartment', 'sale', 'sold', Decimal('610000'), Decimal('0'), 'Renascença', 92, 3, 1, 2, 2, True, False),
    ('CS09', 'Casa em Condomínio Fechado Calhau', 'house', 'sale', 'available', Decimal('1450000'), Decimal('0'), 'Calhau', 240, 4, 3, 5, 4, True, True),
    ('CS10', 'Casa Duplex Piscina Privativa', 'house', 'sale', 'available', Decimal('980000'), Decimal('0'), "Olho d'Água", 195, 4, 2, 4, 3, False, True),
    ('CS11', 'Sobrado 3 Suítes Cohama', 'house', 'both', 'available', Decimal('760000'), Decimal('4500'), 'Cohama', 170, 3, 3, 4, 2, False, True),
    ('CS12', 'Casa Térrea Condomínio Turu', 'house', 'sale', 'sold', Decimal('590000'), Decimal('0'), 'Turu', 135, 3, 1, 3, 2, False, True),
    ('PH13', 'Cobertura Duplex Frente Mar', 'penthouse', 'sale', 'available', Decimal('2250000'), Decimal('0'), "Ponta d'Areia", 290, 4, 4, 6, 4, True, True),
    ('PH14', 'Cobertura Linear Terraço Gourmet', 'penthouse', 'sale', 'reserved', Decimal('1680000'), Decimal('0'), 'Renascença', 230, 3, 3, 4, 3, True, True),
    ('LD15', 'Terreno Plano Esquina 450m²', 'land', 'sale', 'available', Decimal('490000'), Decimal('0'), 'Calhau', 450, 0, 0, 0, 0, True, False),
    ('LD16', 'Lote Comercial Avenida Principal', 'land', 'sale', 'available', Decimal('820000'), Decimal('0'), 'Turu', 600, 0, 0, 0, 0, False, False),
    ('CM17', 'Sala Comercial Executive Center', 'commercial', 'rent', 'available', Decimal('0'), Decimal('3200'), 'Renascença', 55, 0, 0, 1, 1, False, False),
    ('CM18', 'Conjunto Comercial 2 Salas Integradas', 'commercial', 'both', 'available', Decimal('520000'), Decimal('4100'), 'Renascença', 95, 0, 0, 2, 2, True, False),
    ('ST19', 'Studio Mobiliado Vista Mar', 'studio', 'rent', 'available', Decimal('0'), Decimal('2400'), "Ponta d'Areia", 38, 1, 0, 1, 1, False, False),
    ('ST20', 'Flat Executivo Próximo Shopping', 'studio', 'sale', 'draft', Decimal('310000'), Decimal('0'), 'Renascença', 42, 1, 0, 1, 1, False, False),
    ('AP21', 'Apartamento 4 Quartos Exclusivo', 'apartment', 'sale', 'inactive', Decimal('1320000'), Decimal('0'), 'Calhau', 180, 4, 4, 5, 3, True, True),
    ('CS22', 'Casa Jardim Cohama Ampla', 'house', 'rent', 'available', Decimal('0'), Decimal('3900'), 'Cohama', 160, 3, 1, 3, 2, False, True),
]

DEMO_TEMPLATES = [
    (
        '[Demo] Boas-vindas Primeiro Contato',
        'welcome',
        'Olá, {primeiro_nome}! Aqui é o {nome_corretor} da equipe ImobiCRM. Recebi seu interesse em {titulo_imovel} no {bairro} ({valor}). Como posso te ajudar hoje?',
    ),
    (
        '[Demo] Envio de Opções Compatíveis',
        'match',
        'Oi, {nome_cliente}! Separei o imóvel [{codigo_imovel}] {titulo_imovel} no {bairro} ({valor}), que combina exatamente com o que você procura. Posso te enviar as fotos?',
    ),
    (
        '[Demo] Confirmação de Visita',
        'visit',
        'Olá, {primeiro_nome}! Confirmando nossa visita ao imóvel [{codigo_imovel}] no {bairro}. Qualquer imprevisto me avise por aqui. Até já!',
    ),
    (
        '[Demo] Retorno Pós-Visita',
        'follow_up',
        'Oi, {primeiro_nome}, tudo bem? O que achou da visita ao imóvel [{codigo_imovel}] no {bairro}? Gostaria de avançar com uma análise ou conhecer outra opção?',
    ),
    (
        '[Demo] Alinhamento de Proposta',
        'proposal',
        'Olá, {nome_cliente}! Estou formalizando sua proposta para o imóvel [{codigo_imovel}] ({valor}). Me confirma se podemos incluir as condições combinadas?',
    ),
    (
        '[Demo] Atualização de Preço / Oportunidade',
        'general',
        'Oi, {primeiro_nome}! Novidade sobre o imóvel [{codigo_imovel}] no {bairro}: o proprietário abriu margem para negociação ({valor}). Vamos conversar?',
    ),
]


def _make_demo_image(code: str, variant: int, palette: tuple[int, int, int]) -> ContentFile:
    img = Image.new('RGB', (800, 600), color=palette)
    draw = ImageDraw.Draw(img)
    accent = (min(255, palette[0] + 35), min(255, palette[1] + 35), min(255, palette[2] + 35))
    draw.rectangle([40, 40, 760, 560], outline=(255, 255, 255), width=4)
    draw.rectangle([70, 420, 730, 530], fill=accent)
    draw.text((95, 455), f"ImobiCRM {code} - Foto {variant}", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=82)
    buf.seek(0)
    return ContentFile(buf.read(), name=f"{code.lower()}_{variant}.jpg")


class Command(BaseCommand):
    help = (
        'Gera massa de dados simulados realista (idempotente) vinculada à conta '
        f'{TARGET_EMAIL}, com suporte a --clear.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help=f'Remove apenas os registros demo criados por este comando para {TARGET_EMAIL}.',
        )

    def _get_or_create_target_user(self) -> User:
        user = User.objects.filter(email__iexact=TARGET_EMAIL).first()
        if not user:
            base_username = 'ricardo' if not User.objects.filter(username='ricardo').exists() else 'ricardoqsantiago20'
            user = User.objects.create_user(
                username=base_username,
                email=TARGET_EMAIL,
                password='RicardoDemo@2026',
                first_name='Ricardo',
                last_name='Santiago',
            )
            self.stdout.write(self.style.WARNING(f'Usuário {TARGET_EMAIL} criado (username={user.username}).'))
        else:
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active'])

        UserProfile.objects.get_or_create(user=user, defaults={'role': 'agent'})
        return user

    def _ensure_pipeline_and_stages(self):
        pipeline = Pipeline.objects.filter(is_default=True).first()
        if not pipeline:
            pipeline, _ = Pipeline.objects.get_or_create(
                name='Vendas de Imóveis',
                defaults={'is_default': True, 'is_active': True},
            )
        stages_by_order = {}
        for order, name, s_type, color in STAGES_SPEC:
            stage = Stage.objects.filter(pipeline=pipeline, order=order).first()
            if not stage:
                stage = Stage.objects.create(
                    pipeline=pipeline,
                    order=order,
                    name=name,
                    stage_type=s_type,
                    color=color,
                )
            stages_by_order[order] = stage
        return pipeline, stages_by_order

    def _clear_demo_data(self, user: User):
        demo_leads = PropertyLead.objects.filter(client__lgpd_origin=DEMO_ORIGIN)
        del_logs, _ = InteractionLog.objects.filter(lead__in=demo_leads).delete()
        del_acts, _ = Activity.objects.filter(assigned_to=user, description__contains=DEMO_TAG).delete()
        del_visits, _ = PropertyVisit.objects.filter(lead__in=demo_leads).delete()
        del_leads, _ = demo_leads.delete()
        del_imgs, _ = PropertyImage.objects.filter(property__code__startswith=DEMO_PROP_PREFIX, property__captured_by=user).delete()
        del_props, _ = Property.objects.filter(code__startswith=DEMO_PROP_PREFIX, captured_by=user).delete()
        del_prefs, _ = ClientPreference.objects.filter(person__lgpd_origin=DEMO_ORIGIN).delete()
        del_persons, _ = Person.objects.filter(lgpd_origin=DEMO_ORIGIN, assigned_agent=user).delete()
        del_tpls, _ = WhatsAppTemplate.objects.filter(created_by=user, title__startswith=DEMO_TPL_PREFIX).delete()

        self.stdout.write(
            self.style.WARNING(
                f'Limpeza concluída para {TARGET_EMAIL}: '
                f'Persons={del_persons}, Prefs={del_prefs}, Properties={del_props} (Imgs={del_imgs}), '
                f'Leads={del_leads}, Visits={del_visits}, Activities={del_acts}, '
                f'Logs={del_logs}, Templates={del_tpls}.'
            )
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user = self._get_or_create_target_user()

        if options.get('clear'):
            self._clear_demo_data(user)
            return

        now = timezone.now()
        pipeline, stages_by_order = self._ensure_pipeline_and_stages()

        # 1. Contatos (28) + Preferências
        persons = []
        owners = []
        buyers_renters = []
        for idx, (name, c_type, phone, doc, note) in enumerate(DEMO_CONTACTS, start=1):
            email = f'demo.ricardo.{idx:02d}@imobicrm-demo.local'
            person, _ = Person.objects.get_or_create(
                email=email,
                defaults={
                    'name': name,
                    'phone': phone,
                    'document': doc,
                    'client_type': c_type,
                    'assigned_agent': user,
                    'notes': f'{DEMO_TAG} {note}',
                    'lgpd_consent': True,
                    'lgpd_consent_at': now - timedelta(days=idx),
                    'lgpd_origin': DEMO_ORIGIN,
                },
            )
            if c_type in ('buyer', 'renter', 'investor'):
                trans_type = 'rent' if c_type == 'renter' else ('invest' if c_type == 'investor' else 'buy')
                locs = [NEIGHBORHOODS[idx % len(NEIGHBORHOODS)], NEIGHBORHOODS[(idx + 1) % len(NEIGHBORHOODS)]]
                ClientPreference.objects.get_or_create(
                    person=person,
                    defaults={
                        'transaction_type': trans_type,
                        'property_types': ['apartment', 'house'] if c_type != 'investor' else ['commercial', 'land'],
                        'preferred_locations': locs,
                        'min_price': Decimal('300000.00') if trans_type != 'rent' else Decimal('2000.00'),
                        'max_price': Decimal('950000.00') if trans_type != 'rent' else Decimal('5000.00'),
                        'payment_method': 'financing' if idx % 2 == 0 else 'cash',
                        'min_bedrooms': 2 + (idx % 2),
                        'min_suites': 1,
                        'min_parking_spaces': 1 + (idx % 2),
                        'min_area_m2': Decimal('70.00'),
                        'pets_allowed': (idx % 3 == 0),
                        'gourmet_balcony': (idx % 2 == 0),
                        'pool_or_leisure': (idx % 4 == 0),
                        'notes': f'{DEMO_TAG} Preferência simulada de atendimento.',
                    },
                )
                buyers_renters.append(person)
            else:
                owners.append(person)
            persons.append(person)

        # 2. Imóveis (22) + Imagens Pillow RGB (44)
        palettes = [
            (1, 18, 91),
            (15, 76, 129),
            (30, 64, 175),
            (51, 65, 85),
            (13, 148, 136),
        ]
        properties = []
        for idx, spec in enumerate(DEMO_PROPERTIES, start=1):
            (
                short_code, title, prop_type, trans_type, status_val,
                sale_p, rent_p, neigh, area, beds, suites, baths,
                parking, is_excl, pets,
            ) = spec
            code = f'{DEMO_PROP_PREFIX}{short_code}'
            owner = owners[idx % len(owners)]
            prop, created = Property.objects.get_or_create(
                code=code,
                defaults={
                    'title': title,
                    'description': f'{DEMO_TAG} Imóvel simulado em {neigh}, São Luís - MA, excelente padrão.',
                    'property_type': prop_type,
                    'transaction_type': trans_type,
                    'status': status_val,
                    'sale_price': sale_p,
                    'rental_price': rent_p,
                    'condo_fee': Decimal('650.00') if prop_type in ('apartment', 'penthouse', 'studio') else Decimal('0.00'),
                    'iptu': Decimal('1800.00'),
                    'neighborhood': neigh,
                    'city': 'São Luís',
                    'state': 'MA',
                    'usable_area': Decimal(str(area)),
                    'total_area': Decimal(str(area + 18)),
                    'bedrooms': beds,
                    'suites': suites,
                    'bathrooms': baths,
                    'parking_spaces': parking,
                    'pets_allowed': pets,
                    'gourmet_balcony': (beds >= 3),
                    'pool': (idx % 2 == 0),
                    'gym': (prop_type in ('apartment', 'penthouse')),
                    'elevator': (prop_type in ('apartment', 'penthouse', 'commercial')),
                    'morning_sun': (idx % 2 == 1),
                    'owner': owner,
                    'captured_by': user,
                    'is_exclusive': is_excl,
                    'share_owner_contact': True,
                    'key_location': 'Portaria' if idx % 2 == 0 else 'Imobiliária',
                },
            )
            if created or not prop.images.exists():
                pal = palettes[idx % len(palettes)]
                PropertyImage.objects.create(
                    property=prop,
                    image=_make_demo_image(code, 1, pal),
                    caption='Fachada / Sala Principal',
                    is_featured=True,
                    order=0,
                )
                PropertyImage.objects.create(
                    property=prop,
                    image=_make_demo_image(code, 2, (pal[0] + 15, pal[1] + 20, pal[2] + 25)),
                    caption='Área Social / Detalhes',
                    is_featured=False,
                    order=1,
                )
            properties.append(prop)

        # 3. Leads no Funil Kanban (28) distribuídos nas 6 etapas
        origins = [c[0] for c in PropertyLead.ORIGIN_CHOICES]
        lost_reasons = [
            'Preço acima do orçamento',
            'Financiamento reprovado',
            'Localização desfavorável',
            'Preferiu outro corretor',
        ]
        leads = []
        stage_orders_cycle = [1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 5, 6, 6, 6, 6]
        for idx, stage_order in enumerate(stage_orders_cycle, start=1):
            client = buyers_renters[(idx - 1) % len(buyers_renters)]
            prop = properties[(idx - 1) % len(properties)]
            stage = stages_by_order[stage_order]
            trans = 'rent' if prop.transaction_type == 'rent' else 'buy'
            budget = prop.rental_price if trans == 'rent' and prop.rental_price > 0 else (
                prop.sale_price if prop.sale_price > 0 else Decimal('550000.00')
            )
            lead_title = f"[Demo #{idx:02d}] {prop.get_property_type_display()} em {prop.neighborhood} - {client.name.split()[0]}"
            is_overdue = (stage_order in (1, 2, 3) and idx % 3 == 0)
            last_contact = now - timedelta(hours=72 if is_overdue else (idx * 3))

            lead_status = stage.stage_type
            closed_at = None
            lost_reason = None
            if lead_status == 'won':
                days_ago = [2, 6, 14, 35, 65][idx % 5]
                closed_at = now - timedelta(days=days_ago)
            elif lead_status == 'lost':
                closed_at = now - timedelta(days=(idx % 12) + 1)
                lost_reason = lost_reasons[idx % len(lost_reasons)]

            lead, lead_created = PropertyLead.objects.get_or_create(
                title=lead_title,
                agent=user,
                defaults={
                    'client': client,
                    'pipeline': pipeline,
                    'stage': stage,
                    'transaction_type': trans,
                    'property_type': 'commercial' if prop.property_type == 'commercial' else (
                        'land' if prop.property_type == 'land' else (
                            'penthouse' if prop.property_type == 'penthouse' else (
                                'house' if prop.property_type == 'house' else 'apartment'
                            )
                        )
                    ),
                    'budget': budget,
                    'preferred_location': prop.neighborhood,
                    'origin': origins[idx % len(origins)],
                    'status': lead_status,
                    'lost_reason': lost_reason,
                    'notes': f'{DEMO_TAG} Atendimento simulado para {client.name}.',
                    'last_contact_at': last_contact,
                    'closed_at': closed_at,
                },
            )
            if lead_created:
                lead.interested_properties.add(prop)
                InteractionLog.objects.create(
                    lead=lead,
                    user=user,
                    action_type='note',
                    content=f'{DEMO_TAG} Lead criado na etapa {stage.name} com orçamento R$ {budget}.',
                )
            leads.append(lead)

        # 4. Visitas (18) — hoje, próximos dias, realizadas, confirmadas, canceladas e chave atrasada
        visit_offsets_hours = [
            -48, -24, -6, 2, 4, 24, 28, 48, 52, 72, 96, 120, -72, 16, 30, 60, -12, 8,
        ]
        for idx, offset_h in enumerate(visit_offsets_hours, start=1):
            lead = leads[(idx - 1) % len(leads)]
            prop = properties[(idx + 2) % len(properties)]
            sched = now + timedelta(hours=offset_h)
            meeting_point = f'{DEMO_TAG} Portaria - {prop.neighborhood}'

            if idx in (1, 2, 13):
                v_status = 'completed'
                k_status = 'returned'
                rating = 4 + (idx % 2)
                will_prop = (idx == 1)
                withdrawn_at = sched - timedelta(hours=1)
                returned_at = sched + timedelta(hours=2)
            elif idx in (3, 17):
                v_status = 'canceled'
                k_status = 'concierge'
                rating = None
                will_prop = False
                withdrawn_at = None
                returned_at = None
            elif idx == 12:
                # Chave retirada em atraso (>4h)
                v_status = 'confirmed'
                k_status = 'with_agent'
                rating = None
                will_prop = False
                withdrawn_at = now - timedelta(hours=6)
                returned_at = None
            elif idx % 2 == 0:
                v_status = 'confirmed'
                k_status = 'concierge'
                rating = None
                will_prop = False
                withdrawn_at = None
                returned_at = None
            else:
                v_status = 'scheduled'
                k_status = 'at_agency'
                rating = None
                will_prop = False
                withdrawn_at = None
                returned_at = None

            PropertyVisit.objects.get_or_create(
                lead=lead,
                visit_property=prop,
                meeting_point=meeting_point,
                defaults={
                    'agent': user,
                    'scheduled_date': sched,
                    'status': v_status,
                    'key_status': k_status,
                    'key_withdrawn_at': withdrawn_at,
                    'key_returned_at': returned_at,
                    'client_rating': rating,
                    'feedback_notes': f'{DEMO_TAG} Cliente gostou da planta e localização.' if rating else None,
                    'will_make_proposal': will_prop,
                },
            )

        # 5. Atividades / Tarefas (20) — atrasadas, para hoje, próximas e concluídas
        task_specs = [
            ('Retornar ligação sobre financiamento', 'call', 'high', -5, False),
            ('Enviar opções no Renascença via WhatsApp', 'visit_followup', 'high', -2, False),
            ('Conferir documentação de matrícula', 'document', 'medium', 2, False),
            ('Confirmar horário de visita no Calhau', 'visit_followup', 'high', 4, False),
            ('Elaborar proposta formal de compra', 'proposal', 'high', 6, False),
            ('Solicitar simulação bancária', 'credit_analysis', 'medium', 24, False),
            ('Ligar para proprietário sobre exclusividade', 'call', 'medium', 36, False),
            ('Agendar segunda visita com cônjuge', 'visit_followup', 'high', 48, False),
            ('Enviar certidões negativas do imóvel', 'document', 'low', 72, False),
            ('Follow-up de lead sem contato há >48h', 'call', 'high', -12, False),
            ('Revisar minuta do contrato de locação', 'document', 'medium', 96, False),
            ('Apresentar cobertura na Ponta d\'Areia', 'visit_followup', 'medium', 120, False),
            ('Validar aprovação de crédito Caixa', 'credit_analysis', 'high', 3, False),
            ('Atualizar fotos do anúncio', 'general', 'low', 144, False),
            ('Confirmar devolução de chave na portaria', 'general', 'high', -1, False),
            ('Contato inicial com lead do Portal ZAP', 'call', 'high', -24, True),
            ('Envio de ficha técnica por WhatsApp', 'general', 'medium', -48, True),
            ('Visita realizada no Olho d\'Água', 'visit_followup', 'medium', -72, True),
            ('Proposta entregue ao proprietário', 'proposal', 'high', -36, True),
            ('Checagem de IPTU e condomínio', 'document', 'low', -96, True),
        ]
        for idx, (t_title, t_type, prio, due_h, done) in enumerate(task_specs, start=1):
            lead = leads[(idx - 1) % len(leads)]
            prop = properties[(idx - 1) % len(properties)]
            Activity.objects.get_or_create(
                title=f'[Demo] {t_title}',
                assigned_to=user,
                defaults={
                    'activity_type': 'call' if t_type == 'call' else 'follow_up',
                    'task_type': t_type,
                    'priority': prio,
                    'description': f'{DEMO_TAG} Tarefa simulada vinculada a {lead.client.name}.',
                    'lead': lead,
                    'related_property': prop,
                    'due_date': now + timedelta(hours=due_h),
                    'is_completed': done,
                    'completed_at': (now - timedelta(hours=2)) if done else None,
                },
            )

        # 6. Templates de WhatsApp (6)
        for tpl_title, tpl_cat, tpl_content in DEMO_TEMPLATES:
            WhatsAppTemplate.objects.get_or_create(
                title=tpl_title,
                created_by=user,
                defaults={
                    'category': tpl_cat,
                    'content': tpl_content,
                    'is_active': True,
                },
            )

        # Resumo final das entidades vinculadas ao usuário
        summary = {
            'Contatos (Person)': Person.objects.filter(assigned_agent=user).count(),
            'Preferências (ClientPreference)': ClientPreference.objects.filter(person__assigned_agent=user).count(),
            'Imóveis (Property)': Property.objects.filter(captured_by=user).count(),
            'Fotos (PropertyImage)': PropertyImage.objects.filter(property__captured_by=user).count(),
            'Leads (PropertyLead)': PropertyLead.objects.filter(agent=user).count(),
            'Visitas (PropertyVisit)': PropertyVisit.objects.filter(agent=user).count(),
            'Atividades (Activity)': Activity.objects.filter(assigned_to=user).count(),
            'Templates WhatsApp': WhatsAppTemplate.objects.filter(created_by=user).count(),
        }

        self.stdout.write(self.style.SUCCESS(f'Seed demo concluído com sucesso para {TARGET_EMAIL} (user={user.username}):'))
        for label, count in summary.items():
            self.stdout.write(f'  • {label}: {count}')

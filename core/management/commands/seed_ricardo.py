import random
import io
import datetime
from django.core.management.base import BaseCommand
from django.db import models
from django.core.files.base import ContentFile
from PIL import Image
from django.utils import timezone
from django.contrib.auth.models import User
from core.models.users import UserProfile
from core.models.clients import Person, ClientPreference
from core.models.properties import Property, PropertyImage
from core.models.leads import Pipeline, Stage, PropertyLead
from core.models.activities import Activity
from core.models.visits import PropertyVisit

# Lista de bairros de São Luís - MA
NEIGHBORHOODS = ['Renascença', 'Calhau', 'Ponta d\'Areia', 'Olho d\'Água', 'Cohama', 'Turu']

# Tipos de telefone (98) 9xxxx-xxxx
def random_phone():
    return f"(98) 9{random.randint(1000,9999)}-{random.randint(1000,9999)}"

# Gera código do imóvel (ex: AP01, CA02, etc.)
def generate_property_code(property_type, idx):
    prefix = {
        'apartment': 'AP',
        'house': 'HA',
        'penthouse': 'PH',
        'land': 'LD',
        'commercial': 'CM',
        'studio': 'ST',
        'rural': 'RL',
    }.get(property_type, 'IM')
    return f"{prefix}{idx:04d}"

# Cria (ou obtém) o usuário Ricardo
def get_or_create_ricardo():
    qs = User.objects.filter(
        models.Q(username__icontains='ricardo') |
        models.Q(first_name__icontains='ricardo') |
        models.Q(email__icontains='ricardo')
    )
    if qs.exists():
        user = qs.first()
    else:
        user = User.objects.create_user(
            username='ricardo',
            email='ricardo@example.com',
            password='senha_segura_123',
            first_name='Ricardo',
            last_name='Silva',
        )
        # perfil de corretor (agent)
        UserProfile.objects.create(user=user, role='agent')
    return user

# Cria pessoas / contatos
def create_contacts(user, count=15):
    created = []
    for i in range(count):
        name = f"Cliente {i+1}"
        email = f"cliente{i+1}@example.com"
        phone = random_phone()
        client_type = random.choice(['buyer', 'renter', 'owner'])
        person, _ = Person.objects.get_or_create(
            email=email,
            defaults={
                'name': name,
                'phone': phone,
                'client_type': client_type,
                'assigned_agent': user,
            }
        )
        # Preferências de busca (se for buyer ou renter)
        if client_type in ['buyer', 'renter']:
            pref, _ = ClientPreference.objects.get_or_create(
                person=person,
                defaults={
                    'transaction_type': 'buy' if client_type == 'buyer' else 'rent',
                    'preferred_locations': random.sample(NEIGHBORHOODS, k=3),
                    'min_price': random.choice([100000, 200000, 300000]),
                    'max_price': random.choice([400000, 500000, 600000]),
                    'min_bedrooms': random.randint(1, 3),
                    'pets_allowed': random.choice([True, False]),
                }
            )
        created.append(person)
    return created

# Cria imóveis
def create_properties(user, owners, count=12):
    created = []
    for i in range(count):
        prop_type = random.choice(['apartment', 'house', 'penthouse', 'commercial', 'land'])
        title = f"{prop_type.title()} {i+1} em {random.choice(NEIGHBORHOODS)}"
        code = generate_property_code(prop_type, i+1)
        neighborhood = random.choice(NEIGHBORHOODS)
        sale_price = random.choice([250000, 350000, 500000, 750000]) if random.choice([True, False]) else 0
        rental_price = random.choice([1500, 2000, 2500, 3000]) if sale_price == 0 else 0
        status = random.choice(['available', 'reserved', 'sold', 'rented'])
        owner = random.choice(owners)
        prop, prop_created = Property.objects.get_or_create(
            code=code,
            defaults={
                'title': title,
                'description': 'Imóvel fictício gerado para testes.',
                'property_type': prop_type,
                'transaction_type': 'sale' if sale_price > 0 else 'rent',
                'status': status,
                'sale_price': sale_price,
                'rental_price': rental_price,
                'neighborhood': neighborhood,
                'city': 'São Luís',
                'state': 'MA',
                'bedrooms': random.randint(1, 4),
                'bathrooms': random.randint(1, 3),
                'parking_spaces': random.randint(0, 2),
                'usable_area': random.randint(50, 200),
                'total_area': random.randint(60, 250),
                'owner': owner,
                'captured_by': user,
            },
        )
        # Cria 1‑2 imagens em memória (JPEG RGB)
        for j in range(random.randint(1, 2)):
            img = Image.new('RGB', (800, 600), color=(random.randint(0,255), random.randint(0,255), random.randint(0,255)))
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            image_file = ContentFile(buffer.read(), name=f"property_{code}_{j}.jpg")
            PropertyImage.objects.create(property=prop, image=image_file, is_featured=(j==0))
        created.append(prop)
    return created

# Cria pipeline e etapas padrão
def get_or_create_default_pipeline():
    pipeline, created = Pipeline.objects.get_or_create(name='Padrão', defaults={'is_default': True, 'is_active': True})
    if created:
        # etapas padrão (6)
        stage_names = ['Novo Lead', 'Contato Inicial', 'Qualificação', 'Visita Agendada', 'Negociação', 'Fechado']
        for order, name in enumerate(stage_names, start=1):
            Stage.objects.create(pipeline=pipeline, name=name, order=order)
    return pipeline

# Cria leads
def create_leads(user, contacts, properties, pipeline, count=15):
    stages = list(Stage.objects.filter(pipeline=pipeline).order_by('order'))
    created = []
    for i in range(count):
        client = random.choice(contacts)
        prop = random.choice(properties)
        title = f"Lead {i+1} - {prop.title}"
        lead = PropertyLead.objects.create(
            title=title,
            client=client,
            pipeline=pipeline,
            stage=random.choice(stages),
            agent=user,
            transaction_type='buy' if prop.transaction_type == 'sale' else 'rent',
            property_type=prop.property_type,
            budget=prop.sale_price if prop.sale_price > 0 else prop.rental_price,
            preferred_location=prop.neighborhood,
            origin='manual',
            status='open',
            last_contact_at=timezone.now() - datetime.timedelta(hours=random.choice([24, 72, 120])),
        )
        lead.interested_properties.add(prop)
        created.append(lead)
    return created

# Cria visitas
def create_visits(user, leads, properties, count=8):
    created = []
    for i in range(count):
        lead = random.choice(leads)
        prop = random.choice(properties)
        scheduled = timezone.now() + datetime.timedelta(days=random.choice([-2, -1, 0, 1, 2]))
        status = random.choice(['scheduled', 'confirmed', 'completed', 'canceled'])
        visit = PropertyVisit.objects.create(
            lead=lead,
            visit_property=prop,
            agent=user,
            scheduled_date=scheduled,
            status=status,
            key_status=random.choice(['concierge', 'with_owner', 'at_agency', 'with_agent', 'returned']),
        )
        created.append(visit)
    return created

# Cria atividades / tarefas
def create_activities(user, leads, properties, count=10):
    created = []
    for i in range(count):
        lead = random.choice(leads)
        prop = random.choice(properties)
        due = timezone.now() + datetime.timedelta(days=random.choice([-1, 0, 1, 3]))
        activity = Activity.objects.create(
            title=f"Atividade {i+1}",
            activity_type='follow_up',
            task_type=random.choice([t[0] for t in Activity.TASK_TYPES]),
            priority=random.choice(['low', 'medium', 'high']),
            assigned_to=user,
            due_date=due,
            lead=lead,
            related_property=prop,
            description='Tarefa gerada automaticamente para testes.',
        )
        created.append(activity)
    return created

# Cria templates WhatsApp (caso exista modelo)
def create_whatsapp_templates(user):
    # Verifica se há um modelo chamado WhatsAppTemplate
    from django.apps import apps
    Model = apps.get_model('core', 'WhatsAppTemplate') if apps.is_installed('core') else None
    if not Model:
        return []
    templates = []
    msgs = [
        ('Abordagem Inicial', 'Olá {nome}, tudo bem? Sou {corretor} da ImobiCRM. Vi seu interesse em {imovel} e gostaria de ajudar.'),
        ('Agendamento Visita', 'Confirmando visita ao imóvel {imovel} em {data} às {horario}. Aguardo sua confirmação.'),
        ('Pós‑visita', 'Oi {nome}, como foi a visita ao {imovel}? Caso tenha dúvidas, estou à disposição.'),
        ('Envio Proposta', 'Segue proposta para {imovel} conforme conversamos. Qualquer ajuste, me avise.'),
    ]
    for title, body in msgs:
        tmpl, _ = Model.objects.get_or_create(
            title=title,
            defaults={
                'content': body,
                'is_active': True,
            },
        )
        templates.append(tmpl)
    return templates

class Command(BaseCommand):
    help = 'Seed fictitious data for Ricardo (São Luís – MA)'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Remove only the seeded data before re‑seeding')

    def handle(self, *args, **options):
        from django.db import models
        from PIL import Image
        user = get_or_create_ricardo()
        self.stdout.write(self.style.SUCCESS(f'Usuário Ricardo: {user.username}'))

        if options['clear']:
            # Apaga dados criados pelo seed (identificados por prefixos ou tags)
            PropertyImage.objects.filter(image__icontains='property_').delete()
            PropertyLead.objects.all().delete()
            PropertyVisit.objects.all().delete()
            Activity.objects.all().delete()
            Property.objects.all().delete()
            Person.objects.filter(email__icontains='@example.com').delete()
            ClientPreference.objects.all().delete()
            Stage.objects.filter(pipeline__name='Padrão').delete()
            Pipeline.objects.filter(name='Padrão').delete()
            self.stdout.write(self.style.WARNING('Dados seed limpos.'))
            return

        # Cria contatos e preferências
        contacts = create_contacts(user)
        self.stdout.write(self.style.SUCCESS(f'Criados {len(contacts)} contatos.'))

        # Cria imóveis (associando proprietários existentes)
        owners = [c for c in contacts if c.client_type == 'owner'] or contacts
        properties = create_properties(user, owners)
        self.stdout.write(self.style.SUCCESS(f'Criados {len(properties)} imóveis com imagens.'))

        # Pipeline padrão e etapas
        pipeline = get_or_create_default_pipeline()
        self.stdout.write(self.style.SUCCESS(f'Pipeline "{pipeline.name}" pronto.'))

        # Leads
        leads = create_leads(user, contacts, properties, pipeline)
        self.stdout.write(self.style.SUCCESS(f'Criados {len(leads)} leads.'))

        # Visitas
        visits = create_visits(user, leads, properties)
        self.stdout.write(self.style.SUCCESS(f'Criadas {len(visits)} visitas.'))

        # Atividades
        activities = create_activities(user, leads, properties)
        self.stdout.write(self.style.SUCCESS(f'Criadas {len(activities)} atividades.'))

        # Templates WhatsApp (se modelo existir)
        templates = create_whatsapp_templates(user)
        if templates:
            self.stdout.write(self.style.SUCCESS(f'Criados {len(templates)} templates WhatsApp.'))
        else:
            self.stdout.write(self.style.WARNING('Modelo WhatsAppTemplate não encontrado; nenhum template criado.'))

        self.stdout.write(self.style.SUCCESS('Seed concluído.'))

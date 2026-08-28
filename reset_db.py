#!/usr/bin/env python
"""Recria o banco SQLite e popula usuário de teste (corretor)."""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
sys.path.insert(0, str(BASE))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm_api.settings')

def main():
    db = BASE / 'db.sqlite3'
    if db.exists():
        backup = BASE / 'db.sqlite3.bak'
        try:
            if backup.exists():
                backup.unlink()
            db.rename(backup)
            print(f'Backup: {backup.name}')
        except Exception as e:
            print(f'Não foi possível renomear db.sqlite3: {e}')
            print('Feche o runserver / VS Code e tente de novo.')
            sys.exit(1)

    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth.models import User
    from core.models import UserProfile, Person

    print('Aplicando migrations...')
    call_command('migrate', interactive=False, verbosity=1)

    # Superuser opcional (admin)
    if not User.objects.filter(username='Ricardo').exists():
        u = User.objects.create_superuser('Ricardo', 'ricardo@local', 'admin123')
        UserProfile.objects.get_or_create(user=u, defaults={'role': 'manager'})
        print('Admin: Ricardo / admin123')

    # Usuário corretor (NÃO admin)
    user, created = User.objects.get_or_create(
        username='corretor',
        defaults={
            'first_name': 'Ricardo',
            'last_name': 'Corretor',
            'email': 'corretor@imobicrm.local',
            'is_staff': False,
            'is_superuser': False,
        },
    )
    user.set_password('corretor123')
    user.is_staff = False
    user.is_superuser = False
    user.save()
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'role': 'agent',
            'creci': '12345-F',
            'phone': '98999990001',
            'monthly_goal': 800000,
            'bio': 'Corretor de teste',
        },
    )
    print('Corretor: corretor / corretor123  (role=agent)')

    contacts = [
        ('Ana Paula Souza', 'ana.souza@email.com', '98991112233', 'buyer'),
        ('Bruno Mendes', 'bruno.mendes@email.com', '98992223344', 'owner'),
        ('Carla Ferreira', 'carla.ferreira@email.com', '98993334455', 'renter'),
        ('Diego Almeida', 'diego.almeida@email.com', '98994445566', 'buyer'),
        ('Elena Costa', 'elena.costa@email.com', '98995556677', 'investor'),
    ]
    for name, email, phone, ctype in contacts:
        Person.objects.get_or_create(
            email=email,
            defaults={
                'name': name,
                'phone': phone,
                'client_type': ctype,
                'assigned_agent': user,
            },
        )
    print(f'Contatos: {Person.objects.count()}')

    # Seed opcional se o comando existir
    try:
        call_command('seed_real_estate', verbosity=0)
        print('Seed de imóveis aplicado.')
    except Exception as e:
        print(f'Seed opcional não rodou: {e}')

    print('\\nOK. Rode: python manage.py runserver')
    print('Login teste: corretor / corretor123')

if __name__ == '__main__':
    main()

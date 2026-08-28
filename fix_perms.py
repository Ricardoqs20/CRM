#!/usr/bin/env python
"""Libera permissões do usuário corretor no Admin (corrige 403)."""
import os, sys
from pathlib import Path
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'crm_api.settings')
import django
django.setup()

from django.contrib.auth.models import User, Group, Permission
from core.models import UserProfile, Person

user, _ = User.objects.get_or_create(
    username='corretor',
    defaults={'first_name': 'Ricardo', 'last_name': 'Corretor', 'email': 'corretor@imobicrm.local'},
)
user.set_password('corretor123')
user.is_staff = True
user.is_superuser = False
user.is_active = True
user.save()
UserProfile.objects.update_or_create(
    user=user,
    defaults={'role': 'agent', 'creci': '12345-F', 'phone': '98999990001', 'monthly_goal': 800000},
)
group, _ = Group.objects.get_or_create(name='Corretores')
model_names = [
    'person', 'company', 'clientpreference', 'property', 'propertyimage',
    'propertylead', 'pipeline', 'stage', 'activity', 'interactionlog',
    'propertyvisit', 'whatsapptemplate',
]
perms = []
for name in model_names:
    for action in ('add', 'change', 'view', 'delete'):
        try:
            perms.append(Permission.objects.get(codename=f'{action}_{name}'))
        except Permission.DoesNotExist:
            print('sem', action, name)
group.permissions.set(perms)
user.groups.add(group)
print('OK: corretor / corretor123')
print('  is_staff=', user.is_staff, 'is_superuser=', user.is_superuser)
print('  permissões no grupo Corretores:', group.permissions.count())
print('Abra: http://127.0.0.1:8000/admin/core/person/')

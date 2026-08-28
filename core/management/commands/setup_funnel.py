from django.core.management.base import BaseCommand
from core.models import Pipeline, Stage


class Command(BaseCommand):
    help = 'Configura as 6 etapas padrão do funil imobiliário (Vendas e Locação).'

    STAGES = [
        # order, name, type, color
        (1, 'Novo Lead', 'open', '#2563eb'),
        (2, 'Em Atendimento', 'open', '#d97706'),
        (3, 'Visita Agendada', 'open', '#7c3aed'),
        (4, 'Proposta / Negociação', 'open', '#db2777'),
        (5, 'Fechado (Ganho)', 'won', '#059669'),
        (6, 'Perdido (Arquivado)', 'lost', '#64748b'),
    ]

    DESCRIPTIONS = {
        'Novo Lead': 'Acabou de chegar. Qualifique o contato (o que quer e se tem orçamento).',
        'Em Atendimento': 'Contato estabelecido. Apresente imóveis e tire dúvidas.',
        'Visita Agendada': 'Interesse real. Agende visita, confirme horário e controle chaves.',
        'Proposta / Negociação': 'Quer fechar. Envie proposta, negocie valores e documentação.',
        'Fechado (Ganho)': 'Negócio concluído. Contrato assinado e comissão.',
        'Perdido (Arquivado)': 'Não compra agora. Guarde para reabordagem futura.',
    }

    def handle(self, *args, **options):
        sales, _ = Pipeline.objects.get_or_create(
            name='Vendas de Imóveis',
            defaults={'is_default': True, 'is_active': True},
        )
        sales.is_default = True
        sales.is_active = True
        sales.save()

        rent, _ = Pipeline.objects.get_or_create(
            name='Locação de Imóveis',
            defaults={'is_default': False, 'is_active': True},
        )

        legacy = [
            '1. Novo Lead', '2. Em Atendimento', '3. Visita Agendada',
            '4. Proposta / Negociação', '5. Fechado (Ganho)', '6. Perdido (Arquivado)',
        ]

        for pipeline in (sales, rent):
            Stage.objects.filter(pipeline=pipeline, name__in=legacy).delete()
            for order, name, s_type, color in self.STAGES:
                stage, _ = Stage.objects.get_or_create(
                    pipeline=pipeline,
                    order=order,
                    defaults={'name': name, 'stage_type': s_type, 'color': color},
                )
                stage.name = name
                stage.stage_type = s_type
                stage.color = color
                stage.save()
                self.stdout.write(f'  [{pipeline.name}] {order}. {name}')
            Stage.objects.filter(pipeline=pipeline).exclude(order__in=[1, 2, 3, 4, 5, 6]).delete()

        self.stdout.write(self.style.SUCCESS('Funil configurado com sucesso.'))
        self.stdout.write('')
        for name, desc in self.DESCRIPTIONS.items():
            self.stdout.write(f'  • {name}: {desc}')

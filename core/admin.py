import re
from django.contrib import admin, messages
from django.utils.html import format_html
from .models import (
    UserProfile, Person, Company, ClientPreference,
    Property, PropertyImage,
    Pipeline, Stage, PropertyLead,
    Activity, InteractionLog,
    PropertyVisit, WhatsAppTemplate, CalendarIntegration,
    SecurityAuditLog,
)
from .security import anonymize_person_lgpd, is_user_manager


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'creci', 'phone', 'created_at']
    list_filter = ['role']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'creci', 'phone']


class ClientPreferenceInline(admin.StackedInline):
    model = ClientPreference
    extra = 0
    can_delete = False


@admin.action(description='Anonimizar Titular (LGPD)')
def admin_anonymize_persons(modeladmin, request, queryset):
    if not is_user_manager(request.user):
        messages.error(request, 'Apenas administradores/gestores podem anonimizar titulares.')
        return
    count = 0
    for person in queryset:
        anonymize_person_lgpd(person, actor=request.user, request=request)
        count += 1
    messages.success(request, f'{count} titular(es) anonimizado(s) em conformidade com a LGPD.')


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'masked_doc_col', 'client_type', 'lgpd_consent', 'assigned_agent', 'created_at']
    list_filter = ['client_type', 'lgpd_consent', 'assigned_agent', 'created_at']
    search_fields = ['name', 'phone', 'email']
    inlines = [ClientPreferenceInline]
    actions = [admin_anonymize_persons]

    def masked_doc_col(self, obj):
        return obj.masked_document or '—'
    masked_doc_col.short_description = 'CPF / CNPJ'


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['trade_name', 'name', 'masked_cnpj_col', 'contact_name', 'phone', 'email']
    search_fields = ['name', 'trade_name', 'contact_name']

    def masked_cnpj_col(self, obj):
        return obj.masked_cnpj or '—'
    masked_cnpj_col.short_description = 'CNPJ'


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1
    fields = ['image', 'caption', 'is_featured', 'order']


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'title', 'property_type', 'transaction_type', 'neighborhood',
        'sale_price', 'rental_price', 'status', 'is_exclusive', 'captured_by'
    ]
    list_filter = ['property_type', 'transaction_type', 'status', 'is_exclusive', 'neighborhood', 'city']
    search_fields = ['code', 'title', 'neighborhood', 'street', 'building_name', 'description']
    inlines = [PropertyImageInline]
    fieldsets = (
        ('Identificação e Tipo', {
            'fields': ('code', 'title', 'description', 'property_type', 'transaction_type', 'status')
        }),
        ('Valores Financeiros', {
            'fields': ('sale_price', 'rental_price', 'condo_fee', 'iptu')
        }),
        ('Endereço e Localização', {
            'fields': ('building_name', 'street', 'number', 'complement', 'neighborhood', 'city', 'state', 'zip_code')
        }),
        ('Características e Dimensões', {
            'fields': ('usable_area', 'total_area', 'bedrooms', 'suites', 'bathrooms', 'parking_spaces', 'floor')
        }),
        ('Diferenciais', {
            'fields': ('pets_allowed', 'gourmet_balcony', 'pool', 'gym', 'elevator', 'morning_sun', 'furnished', 'extra_features')
        }),
        ('Controle de Captação e Chaves', {
            'fields': ('owner', 'captured_by', 'is_exclusive', 'exclusivity_start', 'exclusivity_end', 'agreed_commission_rate', 'share_owner_contact', 'key_location')
        }),
    )


class StageInline(admin.TabularInline):
    model = Stage
    extra = 1
    fields = ['name', 'order', 'stage_type', 'color']


@admin.register(Pipeline)
class PipelineAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_default', 'is_active']
    inlines = [StageInline]


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ['name', 'pipeline', 'order', 'stage_type', 'color_badge']
    list_filter = ['pipeline', 'stage_type']
    list_editable = ['order']

    def color_badge(self, obj):
        safe_color = obj.color if re.match(r'^#[0-9a-fA-F]{3,8}$', str(obj.color or '')) else '#6366f1'
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{}</span>',
            safe_color, obj.color
        )
    color_badge.short_description = 'Cor'


class InteractionLogInline(admin.TabularInline):
    model = InteractionLog
    extra = 0
    readonly_fields = ['created_at']
    fields = ['created_at', 'user', 'action_type', 'content']


@admin.action(description='Anonimizar Titular do Lead (LGPD)')
def admin_anonymize_lead_clients(modeladmin, request, queryset):
    if not is_user_manager(request.user):
        messages.error(request, 'Apenas administradores/gestores podem anonimizar titulares.')
        return
    seen_ids = set()
    for lead in queryset.select_related('client'):
        if lead.client_id and lead.client_id not in seen_ids:
            anonymize_person_lgpd(lead.client, actor=request.user, request=request)
            seen_ids.add(lead.client_id)
    messages.success(request, f'{len(seen_ids)} titular(es) vinculado(s) anonimizado(s) com sucesso.')


@admin.register(PropertyLead)
class PropertyLeadAdmin(admin.ModelAdmin):
    list_display = ['title', 'client', 'pipeline', 'stage', 'agent', 'budget', 'origin', 'status', 'created_at']
    list_filter = ['status', 'pipeline', 'stage', 'origin', 'transaction_type', 'property_type', 'agent']
    search_fields = ['title', 'client__name', 'client__phone', 'preferred_location']
    filter_horizontal = ['interested_properties']
    inlines = [InteractionLogInline]
    actions = [admin_anonymize_lead_clients]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['title', 'activity_type', 'priority', 'assigned_to', 'lead', 'due_date', 'is_completed']
    list_filter = ['activity_type', 'priority', 'is_completed', 'assigned_to', 'due_date']
    search_fields = ['title', 'description', 'lead__title', 'lead__client__name']


@admin.register(PropertyVisit)
class PropertyVisitAdmin(admin.ModelAdmin):
    list_display = ['visit_property', 'lead', 'agent', 'scheduled_date', 'status', 'key_status', 'key_overdue_badge', 'will_make_proposal']
    list_filter = ['status', 'key_status', 'will_make_proposal', 'scheduled_date', 'agent']
    search_fields = ['visit_property__code', 'visit_property__title', 'lead__client__name', 'lead__title']

    def key_overdue_badge(self, obj):
        if obj.is_key_overdue:
            return format_html(
                '<span style="background-color: #ef4444; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold;">🚨 Atraso &gt;4h</span>'
            )
        return format_html('<span style="color: #10b981; font-weight: bold;">✓ OK</span>')
    key_overdue_badge.short_description = 'Status Chave'


@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'created_by', 'is_active', 'created_at']
    list_filter = ['category', 'is_active']
    search_fields = ['title', 'content']


@admin.register(CalendarIntegration)
class CalendarIntegrationAdmin(admin.ModelAdmin):
    list_display = ['user', 'google_connected', 'google_calendar_id', 'google_token_expiry', 'updated_at']
    list_filter = ['google_connected']
    exclude = ['google_access_token', 'google_refresh_token']
    readonly_fields = ['ical_token', 'created_at', 'updated_at']


@admin.register(SecurityAuditLog)
class SecurityAuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'action', 'user', 'target', 'ip_address']
    list_filter = ['action', 'created_at']
    search_fields = ['target', 'details', 'user__username', 'ip_address']
    readonly_fields = ['user', 'action', 'target', 'ip_address', 'details', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

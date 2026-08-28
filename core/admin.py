from django.contrib import admin
from django.utils.html import format_html
from .models import (
    UserProfile, Person, Company, ClientPreference,
    Property, PropertyImage,
    Pipeline, Stage, PropertyLead,
    Activity, InteractionLog,
    PropertyVisit, WhatsAppTemplate
)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'creci', 'phone', 'created_at']
    list_filter = ['role']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'creci', 'phone']


class ClientPreferenceInline(admin.StackedInline):
    model = ClientPreference
    extra = 0
    can_delete = False


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'client_type', 'assigned_agent', 'created_at']
    list_filter = ['client_type', 'assigned_agent', 'created_at']
    search_fields = ['name', 'phone', 'email', 'document']
    inlines = [ClientPreferenceInline]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['trade_name', 'name', 'cnpj', 'contact_name', 'phone', 'email']
    search_fields = ['name', 'trade_name', 'cnpj', 'contact_name']


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
            'fields': ('owner', 'captured_by', 'is_exclusive', 'exclusivity_start', 'exclusivity_end', 'agreed_commission_rate', 'key_location')
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
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{}</span>',
            obj.color, obj.color
        )
    color_badge.short_description = 'Cor'


class InteractionLogInline(admin.TabularInline):
    model = InteractionLog
    extra = 0
    readonly_fields = ['created_at']
    fields = ['created_at', 'user', 'action_type', 'content']


@admin.register(PropertyLead)
class PropertyLeadAdmin(admin.ModelAdmin):
    list_display = ['title', 'client', 'pipeline', 'stage', 'agent', 'budget', 'origin', 'status', 'created_at']
    list_filter = ['status', 'pipeline', 'stage', 'origin', 'transaction_type', 'property_type', 'agent']
    search_fields = ['title', 'client__name', 'client__phone', 'preferred_location']
    filter_horizontal = ['interested_properties']
    inlines = [InteractionLogInline]


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
                '<span style="background-color: #ef4444; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold;">🚨 Atraso >4h</span>'
            )
        return format_html('<span style="color: #10b981; font-weight: bold;">✓ OK</span>')
    key_overdue_badge.short_description = 'Status Chave'


@admin.register(WhatsAppTemplate)
class WhatsAppTemplateAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'is_active', 'created_at']
    list_filter = ['category', 'is_active']
    search_fields = ['title', 'content']
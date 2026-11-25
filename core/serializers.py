from rest_framework import serializers
from .models import (
    Person, Company, ClientPreference,
    Property, PropertyImage,
    Pipeline, Stage, PropertyLead,
    Activity, InteractionLog,
    PropertyVisit, UserProfile
)


class ClientPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientPreference
        fields = '__all__'


class PersonSerializer(serializers.ModelSerializer):
    preferences = ClientPreferenceSerializer(read_only=True)
    masked_document = serializers.CharField(read_only=True)

    class Meta:
        model = Person
        fields = [
            'id', 'name', 'email', 'phone', 'secondary_phone',
            'document', 'masked_document', 'client_type', 'assigned_agent',
            'notes', 'lgpd_consent', 'lgpd_consent_at', 'lgpd_origin',
            'preferences', 'created_at', 'updated_at',
        ]
        extra_kwargs = {
            'document': {'write_only': True},
        }


class CompanySerializer(serializers.ModelSerializer):
    masked_cnpj = serializers.CharField(read_only=True)

    class Meta:
        model = Company
        fields = [
            'id', 'name', 'trade_name', 'cnpj', 'masked_cnpj',
            'contact_name', 'email', 'phone', 'notes', 'created_at',
        ]
        extra_kwargs = {
            'cnpj': {'write_only': True},
        }


class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = '__all__'


class PropertySerializer(serializers.ModelSerializer):
    images = PropertyImageSerializer(many=True, read_only=True)
    property_type_display = serializers.CharField(source='get_property_type_display', read_only=True)
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    captured_by_name = serializers.CharField(source='captured_by.get_full_name', read_only=True)
    owner_name = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = '__all__'

    def get_owner_name(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if obj.owner and obj.can_view_owner(user):
            return obj.owner.name
        return None


class StageSerializer(serializers.ModelSerializer):
    leads_count = serializers.IntegerField(source='leads.count', read_only=True)

    class Meta:
        model = Stage
        fields = '__all__'


class PipelineSerializer(serializers.ModelSerializer):
    stages = StageSerializer(many=True, read_only=True)

    class Meta:
        model = Pipeline
        fields = '__all__'


class PropertyLeadSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='client.name', read_only=True)
    client_phone = serializers.CharField(source='client.phone', read_only=True)
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    property_type_display = serializers.CharField(source='get_property_type_display', read_only=True)
    origin_display = serializers.CharField(source='get_origin_display', read_only=True)
    agent_name = serializers.CharField(source='agent.get_full_name', read_only=True)
    is_followup_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = PropertyLead
        fields = '__all__'


class ActivitySerializer(serializers.ModelSerializer):
    activity_type_display = serializers.CharField(source='get_activity_type_display', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)

    class Meta:
        model = Activity
        fields = '__all__'


class InteractionLogSerializer(serializers.ModelSerializer):
    action_type_display = serializers.CharField(source='get_action_type_display', read_only=True)
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)

    class Meta:
        model = InteractionLog
        fields = '__all__'


class PropertyVisitSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source='lead.client.name', read_only=True)
    property_code = serializers.CharField(source='visit_property.code', read_only=True)
    property_title = serializers.CharField(source='visit_property.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = PropertyVisit
        fields = '__all__'

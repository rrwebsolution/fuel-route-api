from decimal import Decimal

from rest_framework import serializers

from .models import FuelStation
from .services.city_coordinates import US_STATES, default_city_index


class FuelStationSerializer(serializers.ModelSerializer):
    retail_price = serializers.DecimalField(
        max_digits=12, decimal_places=8, min_value=Decimal("0.01"), coerce_to_string=False
    )
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False, allow_null=True)

    class Meta:
        model = FuelStation
        fields = [
            "opis_id", "name", "address", "city", "state", "rack_id",
            "retail_price", "latitude", "longitude", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_state(self, value):
        state = value.strip().upper()
        if state not in US_STATES:
            raise serializers.ValidationError("Must be a two-letter US state code, e.g. TX.")
        return state

    def validate(self, attrs):
        has_lat = attrs.get("latitude") is not None
        has_lon = attrs.get("longitude") is not None
        if has_lat != has_lon:
            raise serializers.ValidationError("Provide both latitude and longitude, or neither.")

        # Without explicit coordinates, place the station at its city centroid (same as the
        # CSV import) so it can be found along routes. Re-resolve when city/state change.
        location_changed = "city" in attrs or "state" in attrs
        if not has_lat and (self.instance is None or location_changed or "latitude" in attrs):
            city = attrs.get("city", getattr(self.instance, "city", ""))
            state = attrs.get("state", getattr(self.instance, "state", ""))
            coordinates = default_city_index().lookup(city, state)
            attrs["latitude"], attrs["longitude"] = coordinates if coordinates else (None, None)
        return attrs


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200, trim_whitespace=True)
    finish = serializers.CharField(max_length=200, trim_whitespace=True)

    def validate(self, attrs):
        if " ".join(attrs["start"].lower().split()) == " ".join(attrs["finish"].lower().split()):
            raise serializers.ValidationError({"finish": "Finish must be different from start."})
        return attrs

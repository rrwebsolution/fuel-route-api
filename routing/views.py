from decimal import Decimal, InvalidOperation

from django.db import DatabaseError, connection
from django.db.models import Q
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStation
from .serializers import FuelStationSerializer, RouteRequestSerializer
from .services.exceptions import TripPlanningError
from .services.trip_planner import plan_trip


class RouteView(APIView):
    """POST a start and finish in the USA; get the route plus cost-optimised fuel stops."""

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": {"code": "invalid_request", "message": "Invalid request.", "fields": serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = plan_trip(serializer.validated_data["start"], serializer.validated_data["finish"])
        except TripPlanningError as exc:
            return Response(
                {"error": {"code": exc.code, "message": exc.message, **exc.details}},
                status=exc.status_code,
            )
        return Response(result)


class StationPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class FuelStationListView(generics.ListCreateAPIView):
    """
    GET: list fuel stations.  POST: add a fuel station.

    Filters: ?state=TX  ?city=Dallas  ?search=pilot (name/address/city)
             ?max_price=3.50  ?ordering=price|-price|name
    """

    serializer_class = FuelStationSerializer
    pagination_class = StationPagination
    ORDERINGS = {"price": "retail_price", "-price": "-retail_price", "name": "name", "-name": "-name"}

    def get_queryset(self):
        params = self.request.query_params
        queryset = FuelStation.objects.all()
        if state := params.get("state", "").strip():
            queryset = queryset.filter(state__iexact=state)
        if city := params.get("city", "").strip():
            queryset = queryset.filter(city__iexact=city)
        if search := params.get("search", "").strip():
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(address__icontains=search) | Q(city__icontains=search)
            )
        if max_price := params.get("max_price", "").strip():
            try:
                limit = Decimal(max_price)
            except InvalidOperation:
                raise ValidationError({"max_price": "Must be a number, e.g. 3.50."}) from None
            queryset = queryset.filter(retail_price__lte=limit)
        return queryset.order_by(self.ORDERINGS.get(params.get("ordering", ""), "opis_id"))


class FuelStationDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET, PUT, PATCH or DELETE one fuel station by its OPIS Truckstop ID."""

    queryset = FuelStation.objects.all()
    serializer_class = FuelStationSerializer
    lookup_field = "opis_id"


class HealthView(APIView):
    """GET service status: database connectivity and how much station data is loaded."""

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            stations = FuelStation.objects.count()
            located = FuelStation.objects.exclude(latitude=None).count()
        except DatabaseError:
            return Response(
                {"status": "error", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({
            "status": "ok",
            "database": "ok",
            "fuel_stations": stations,
            "fuel_stations_with_coordinates": located,
        })

from django.db import models


class FuelStation(models.Model):
    """A truck stop and its retail diesel price, imported from the OPIS CSV."""

    opis_id = models.PositiveIntegerField(unique=True, help_text="OPIS Truckstop ID")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=12, decimal_places=8)
    # The CSV has no coordinates; they are resolved from the city/state during import.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["opis_id"]
        indexes = [
            # Bounding-box prefilter when searching for stations along a route.
            models.Index(fields=["latitude", "longitude"], name="fuelstation_lat_lon_idx"),
            models.Index(fields=["state", "city"], name="fuelstation_state_city_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"

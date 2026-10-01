"""Domain errors raised by the service layer, each mapped to an HTTP status by the view."""


class TripPlanningError(Exception):
    status_code = 422
    code = "trip_planning_error"

    def __init__(self, message, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class LocationNotFoundError(TripPlanningError):
    status_code = 422
    code = "location_not_found"


class RouteNotFoundError(TripPlanningError):
    status_code = 422
    code = "route_not_found"


class NoFuelStationsError(TripPlanningError):
    status_code = 422
    code = "no_fuel_stations"


class FuelRangeExceededError(TripPlanningError):
    """A stretch of the route is longer than the vehicle range with no station in between."""

    status_code = 422
    code = "fuel_range_exceeded"


class ExternalServiceError(TripPlanningError):
    status_code = 502
    code = "external_service_error"


class ExternalServiceTimeoutError(ExternalServiceError):
    status_code = 504
    code = "external_service_timeout"

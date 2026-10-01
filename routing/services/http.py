"""Small shared helper for calling the free external APIs (Nominatim, OSRM)."""

import requests
from django.conf import settings

from .exceptions import ExternalServiceError, ExternalServiceTimeoutError

_session = requests.Session()


def get_json(url, service_name, params=None, accepted_statuses=(200,)):
    """GET a JSON document, translating network problems into domain errors."""
    try:
        response = _session.get(
            url,
            params=params,
            headers={"User-Agent": settings.GEOCODING_USER_AGENT},
            timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise ExternalServiceTimeoutError(f"The {service_name} service timed out. Please try again.") from exc
    except requests.RequestException as exc:
        raise ExternalServiceError(f"Could not reach the {service_name} service.") from exc

    if response.status_code not in accepted_statuses:
        raise ExternalServiceError(
            f"The {service_name} service returned an error.", upstream_status=response.status_code
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ExternalServiceError(f"The {service_name} service returned an invalid response.") from exc

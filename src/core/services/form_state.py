"""Signed form baselines; callers must authorize and lock before comparison."""

import hashlib
import json

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.translation import gettext_lazy as _


class FormStateConflictError(ValidationError):
    """The submitted baseline is missing, invalid or no longer current."""

    def __init__(self, current):
        self.current = current
        super().__init__(
            _(
                "Der Formularstand ist veraltet oder ungültig. Es wurde nichts gespeichert. "
                "Ihre Eingaben bleiben erhalten. Prüfen Sie den aktuellen Stand und senden Sie bewusst erneut ab."
            )
        )


def _payload(instance, *, fields, facility_id):
    state = json.dumps(
        {name: getattr(instance, name) for name in fields},
        cls=DjangoJSONEncoder,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "object": str(instance.pk),
        "facility": str(facility_id),
        "state": hashlib.sha256(state.encode()).hexdigest(),
    }


def sign_form_state(instance, *, fields, facility_id, salt):
    """Sign an identity-bound digest, never the editable plaintext itself."""
    return signing.dumps(_payload(instance, fields=fields, facility_id=facility_id), salt=salt)


def require_form_state(token, instance, *, fields, facility_id, salt):
    """Compare to the caller's fresh locked instance without querying the DB."""
    try:
        payload = signing.loads(token or "", salt=salt)
    except (signing.BadSignature, TypeError, ValueError):
        raise FormStateConflictError(instance) from None
    if payload != _payload(instance, fields=fields, facility_id=facility_id):
        raise FormStateConflictError(instance)

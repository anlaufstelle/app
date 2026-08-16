"""Gemeinsamer Request-Helper für serverabgesicherte Tally-POSTs."""

from django.urls import reverse

from core.services.events import new_tally_idempotency_key


def post_tally(client, payload, *, idempotency_key=None, **extra):
    """Sende den vom UI garantierten identischen Header-/Body-Schlüssel."""
    key = idempotency_key or new_tally_idempotency_key()
    return client.post(
        reverse("core:tally_increment"),
        {**payload, "idempotency_key": key},
        HTTP_X_IDEMPOTENCY_KEY=key,
        **extra,
    )

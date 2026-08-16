"""Kurzlebige, signierte Rücknahme-Capabilities der Strichliste (Refs #1913)."""

from __future__ import annotations

import json
from datetime import timedelta
from hashlib import sha256

from django.core.signing import BadSignature, TimestampSigner
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Event
from core.services.compliance import user_can_see_document_type
from core.services.events.crud import soft_delete_event
from core.services.events.tally import evaluate_tally_eligibility

TALLY_UNDO_SECONDS = 60
_TALLY_UNDO_VERSION = "v1"
_TALLY_UNDO_SALT = "core.tally_undo.v1"


class TallyUndoDenied(Exception):  # noqa: N818 - stable domain rejection name
    """Undifferenzierte Ablehnung ohne ein Event-Existenzorakel."""


def _timestamp(value):
    return value.isoformat()


def _idempotency_key_hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _capability_payload(event):
    return {
        "v": _TALLY_UNDO_VERSION,
        "event": str(event.pk),
        "facility": str(event.facility_id),
        "creator": str(event.created_by_id),
        "document_type": str(event.document_type_id),
        # Der Schlüssel bleibt ein interner Idempotenz-Mechanismus. Sein
        # Digest bindet die Capability genauso, ohne ihn im HTML-Token zu
        # transportieren oder in Exporte/Offline-Payloads zu verlängern.
        "idempotency_key_hash": _idempotency_key_hash(event.idempotency_key),
        "created_at": _timestamp(event.created_at),
        "updated_at": _timestamp(event.updated_at),
    }


def _signer():
    return TimestampSigner(salt=_TALLY_UNDO_SALT)


def mint_tally_undo_token(event):
    """Mint nur im tatsächlichen Tally-Erfolgs-/Replay-Kontext einen Token."""
    if (
        event.created_via != Event.CreatedVia.TALLY
        or not event.is_anonymous
        or event.client_id is not None
        or event.case_id is not None
        or event.created_by_id is None
        or not event.idempotency_key
        or event.is_deleted
    ):
        raise TallyUndoDenied
    if timezone.now() - event.created_at > timedelta(seconds=TALLY_UNDO_SECONDS):
        raise TallyUndoDenied
    serialized = json.dumps(_capability_payload(event), sort_keys=True, separators=(",", ":"))
    return _signer().sign(serialized)


def _load_payload(token):
    try:
        raw = _signer().unsign(token, max_age=TALLY_UNDO_SECONDS)
        payload = json.loads(raw)
    except (BadSignature, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TallyUndoDenied from exc
    if not isinstance(payload, dict) or payload.get("v") != _TALLY_UNDO_VERSION:
        raise TallyUndoDenied
    return payload


def _matches_identity(event, facility, user, payload):
    return (
        str(event.pk) == payload.get("event")
        and str(facility.pk) == payload.get("facility")
        and event.facility_id == facility.pk
        and str(user.pk) == payload.get("creator")
        and event.created_by_id == user.pk
        and str(event.document_type_id) == payload.get("document_type")
        and _idempotency_key_hash(event.idempotency_key) == payload.get("idempotency_key_hash")
        and _timestamp(event.created_at) == payload.get("created_at")
    )


@transaction.atomic
def undo_tally_event(facility, user, token):
    """Atomisch ein eigenes, unverändertes Tally-Event innerhalb 60 s löschen."""
    payload = _load_payload(token)
    try:
        event = (
            Event.objects.for_facility(facility)
            .select_for_update()
            .select_related("document_type")
            .get(pk=payload.get("event"))
        )
    except (Event.DoesNotExist, ValueError, TypeError) as exc:
        raise TallyUndoDenied from exc

    if not _matches_identity(event, facility, user, payload):
        raise TallyUndoDenied
    if event.is_deleted:
        if (
            AuditLog.objects.for_facility(facility)
            .filter(
                action=AuditLog.Action.DELETE,
                target_type="Event",
                target_id=str(event.pk),
                user=user,
                detail__source="tally_undo",
            )
            .exists()
        ):
            return event
        raise TallyUndoDenied
    if (
        event.created_via != Event.CreatedVia.TALLY
        or not event.is_anonymous
        or event.client_id is not None
        or event.case_id is not None
        or _timestamp(event.updated_at) != payload.get("updated_at")
        or timezone.now() - event.created_at > timedelta(seconds=TALLY_UNDO_SECONDS)
        or not user.is_assistant_or_above
        or not user_can_see_document_type(user, event.document_type)
        or not evaluate_tally_eligibility(event.document_type, user).eligible
    ):
        raise TallyUndoDenied
    soft_delete_event(event, user, source="tally_undo")
    return event

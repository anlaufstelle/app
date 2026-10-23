"""Persist an MFA successor session before committing the factor change."""

import logging
from contextlib import contextmanager
from copy import copy

from django.contrib.sessions.backends.base import SessionBase
from django.db import transaction

from core.models.user import SessionRevocationError

logger = logging.getLogger(__name__)


class _PreparedSession(SessionBase):
    """Reuse the confirmed write when response middleware saves identical data.

    The concrete backend follows this mixin in the generated class's MRO.
    Django still sets its normal session cookie, including expiry and flags.
    A second write would introduce a failure window after the factor commit.
    """

    _prepared_data = None

    def save(self, must_create=False):
        payload = self.serializer().dumps(dict(self))
        if not must_create and self._prepared_data == (self.session_key, payload):
            return
        super().save(must_create=must_create)

    def prepare(self):
        self.save()
        payload = self.serializer().dumps(dict(self))
        # cached_db may swallow a failed cache write. Verify the session as the
        # next request will see it, including cache-only backends' silent loss.
        stored = type(self)(self.session_key)
        if self.serializer().dumps(dict(stored)) != payload:
            raise SessionRevocationError("The successor session was not stored completely.")
        self._prepared_data = (self.session_key, payload)


def _message_snapshot(request):
    """Keep queued messages reversible along with the session ceremony."""
    storage = request._messages
    snapshots = []
    for item in (storage, *getattr(storage, "storages", ())):
        snapshots.append(
            (
                item,
                {
                    key: copy(value) if isinstance(value, (list, dict, set)) else value
                    for key, value in vars(item).items()
                },
            )
        )
    return snapshots


@contextmanager
def mfa_completion_session(request, response):
    """Stage a new key without deleting the retry session before commit.

    Factor changes, audit, final session payload and its read-back all precede
    the database commit. Cache writes cannot join that transaction: an
    unpublished successor is deleted on rollback and its new auth revision
    cannot authenticate against the rolled-back account in any case.
    """
    original = request.session
    original_password = request.user.password
    original_device = getattr(request.user, "otp_device", None)
    messages_before = _message_snapshot(request)
    # The serializer's signing salt must remain compatible with the backend
    # loaded by ordinary requests, rather than this temporary subclass's name.
    successor_class = type("MFASuccessorSession", (_PreparedSession, type(original)), {"key_salt": original.key_salt})
    successor = successor_class()
    successor.update(dict(original))
    request.session = successor
    try:
        with transaction.atomic():
            yield
            # Include session-backed flash messages before sealing the payload.
            request._messages.update(response)
            successor.prepare()
    except Exception:
        request.session = original
        request.user.password = original_password
        request.user.otp_device = original_device
        for storage, state in messages_before:
            storage.__dict__.clear()
            storage.__dict__.update(state)
        if successor.session_key:
            try:
                successor.delete()
            except Exception:  # noqa: BLE001 -- unpublished keys are never sent to the client
                logger.error("An unpublished MFA successor session could not be deleted.")
        raise
    # Keep the old session until both persistence steps have succeeded. Its
    # old auth revision is already invalid after a factor replacement/removal.
    try:
        original.delete()
    except Exception:  # noqa: BLE001 -- the committed successor remains usable
        logger.error("The previous MFA session key could not be deleted.")

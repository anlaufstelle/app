"""Follow-Up-Tests für Mutation-Survivors in
``core.services.file_vault.audit`` (Refs #1388).

``log_attachment_violation`` ist die AuditLog-Senke für File-Vault-Rejects
(Virus, Extension-Allowlist, MIME-Mismatch — Refs #610/#901). Der Voll-Lauf
2026-07-16 liess hier echte Verhaltens-Survivors stehen: der ``target_type``
und die aus dem Event abgeleitete ``target_id`` des forensischen
SECURITY_VIOLATION-Eintrags wurden von keinem Test festgenagelt.

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

import pytest
from django.db import connection

from core.services.file_vault.audit import log_attachment_violation


def _requires_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("AuditLog-Trigger (Hash-Chain/Immutable) erfordert PostgreSQL")


class _EventStub:
    """Minimaler Event-Stand-in — ``log_attachment_violation`` liest nur ``pk``."""

    def __init__(self, pk):
        self.pk = pk


@pytest.mark.django_db
class TestLogAttachmentViolationTarget:
    def setup_method(self):
        _requires_postgres()

    def test_persists_target_type_and_event_pk(self, facility, staff_user):
        """Killt fünf ``log_attachment_violation``-Survivors auf einmal:

        - ``mutmut_1``: ``target_id = event.pk if … else None`` → ``target_id = None``.
        - ``mutmut_2``: ``getattr(event, "pk", None)`` → ``getattr(None, "pk", None)``
          (immer ``None`` → ``target_id`` fällt auf ``None``).
        - ``mutmut_18``: ``target_id=target_id`` am Aufruf entfernt (Default ``None``).
        - ``mutmut_11``: ``target_type="Attachment"`` → ``target_type=None``.
        - ``mutmut_22``: ``"Attachment"`` → ``"XXAttachmentXX"``.

        Der SECURITY_VIOLATION-Eintrag ist die forensische Spur eines
        abgewiesenen Uploads — Ziel-Typ und die Event-Zuordnung müssen exakt
        stimmen, sonst ist der Verstoß nicht dem verursachenden Vorgang
        zuzuordnen.
        """
        event = _EventStub(pk=4242)
        entry = log_attachment_violation(
            facility,
            staff_user,
            event,
            reason="extension_not_allowed",
            filename="schaden.exe",
        )
        assert entry is not None
        assert entry.target_type == "Attachment"  # mutmut_11 (None) + mutmut_22 (XX…XX)
        assert entry.target_id == str(4242)  # mutmut_1 + mutmut_2 + mutmut_18

    def test_target_id_none_when_event_has_no_pk(self, facility, staff_user):
        """Regression-Anker für den Pre-Save-Reject-Pfad (Event ohne PK):
        ``target_id`` muss dann leer bleiben — nagelt zusätzlich fest, dass
        ``mutmut_1``/``mutmut_2`` nicht durch einen konstanten Nicht-None-Wert
        „gekillt" wirken, sondern die echte Bedingung greift.
        """
        entry = log_attachment_violation(
            facility,
            staff_user,
            _EventStub(pk=None),
            reason="virus_detected",
            filename="virus.pdf",
        )
        assert entry is not None
        assert entry.target_type == "Attachment"
        assert entry.target_id in ("", None)

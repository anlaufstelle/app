"""Gemeinsame Approval-Status-Choices (Refs #1834).

``DeletionRequest.Status`` und ``RectificationRequest.Status`` waren
zeichengleich (PENDING/APPROVED/REJECTED, identische Labels) — zwei Kopien
derselben Vier-Augen-Semantik. Sie teilen sich jetzt
:class:`core.constants.ApprovalStatus`; die Modelle behalten ihr
``Status``-Klassenattribut als Alias, damit die Aufrufstellen
(``DeletionRequest.Status.PENDING`` & Co.) unangetastet bleiben.

Nicht betroffen: ``RetentionProposal.Status`` (fünf Werte, eigene Semantik mit
``held``/``deferred`` und dem Label „Freigegeben") — die Ähnlichkeit endet beim
Namen.

Refs #2040 ergänzt ``WITHDRAWN`` als vierten Wert. Er trifft das geteilte Enum
und damit beide Modelle — genutzt wird er heute aber nur vom ``DeletionRequest``
(Selbst-Rückzug). Dass der Art.-16-Pfad davon unberührt bleibt, pinnt
``src/tests/test_deletion_request_withdraw.py``.
"""

import pytest

from core.constants import ApprovalStatus
from core.models import DeletionRequest, RectificationRequest, RetentionProposal


class TestSharedChoices:
    def test_both_models_point_at_the_shared_class(self):
        assert DeletionRequest.Status is ApprovalStatus
        assert RectificationRequest.Status is ApprovalStatus

    def test_values_are_unchanged(self):
        assert [value for value, _label in ApprovalStatus.choices] == [
            "pending",
            "approved",
            "rejected",
            "withdrawn",
        ]
        assert ApprovalStatus.PENDING == "pending"
        assert ApprovalStatus.APPROVED == "approved"
        assert ApprovalStatus.REJECTED == "rejected"
        assert ApprovalStatus.WITHDRAWN == "withdrawn"

    def test_labels_are_unchanged(self):
        assert [str(label) for _value, label in ApprovalStatus.choices] == [
            "Ausstehend",
            "Genehmigt",
            "Abgelehnt",
            "Zurückgezogen",
        ]

    @pytest.mark.parametrize("model", [DeletionRequest, RectificationRequest])
    def test_db_field_keeps_its_choices(self, model):
        field = model._meta.get_field("status")
        assert [value for value, _label in field.choices] == [
            "pending",
            "approved",
            "rejected",
            "withdrawn",
        ]
        assert field.default == ApprovalStatus.PENDING

    def test_retention_proposal_keeps_its_own_status(self):
        """Fünf Werte, eigene Labels — bewusst nicht zusammengelegt."""
        assert RetentionProposal.Status is not ApprovalStatus
        assert [value for value, _label in RetentionProposal.Status.choices] == [
            "pending",
            "approved",
            "held",
            "deferred",
            "rejected",
        ]

"""Pin-Tests der Welle L (release-0.99, Refs #2174 / #2193) — Batch **security-1n**.

Quelle: Vollmutation #2174, Batch ``security-1n``
(``welle-l-0.99/reports/m-scratch/batch-security-1n.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-security-1n/triage-099-security-1n.json``).
7 Survivor-Mutanten in ``core.services.security.locking.check_version_conflict``
(``_27``/``_32``/``_39``/``_43``/``_44``/``_50``/``_55``) — allesamt
Meldungs-Verlust (``ValidationError(<Text>)`` -> ``ValidationError(None)``
bzw. Marker-/``_(None)``-Varianten) in den drei ``ValidationError``-Zweigen
der Funktion (``missing_token``, ``invalid_token`` und ``version_conflict``).

Der urspruengliche Batch ``security-1`` hatte alle 19 Meldungs-Varianten
dieser Funktion faelschlich als ``skip`` eingestuft, mit der Begruendung,
Aufrufer prueften ausschliesslich ``exc_info.value.code`` und die
Meldungs-Zeichenkette habe keinen weiteren Leser. Die Familienreview hat
das widerlegt: ``src/core/views/_dynamic_errors.py:55`` liest
``error.messages`` und gibt sie in ``src/core/views/clients.py:805`` an die
Oberflaeche aus — ein Meldungs-Verlust ist dort direkt beobachtbar. Alle 7
Mutanten dieses Batches sind daher ``test``, Prio 3 (Meldungs-Verlust-Regel).

``check_version_conflict`` fuehrt in den ``invalid_token``-/
``version_conflict``-Zweigen IMMER ``select_for_update()`` aus —
Direktaufrufe mit nicht-leerem ``expected_updated_at`` brauchen deshalb
``transaction.atomic()`` (Refs #1338, siehe auch
``src/tests/test_optimistic_locking.py``, dessen bestehende Tests nur den
``code`` pruefen, nie den Wortlaut).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction

from core.services.security import check_version_conflict

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_missing_token_message_exact_wording(sample_workitem):
    """Vollstaendiger Zweig-Nachweis fuer ``missing_token`` (Testauftrag
    security-1n nennt alle vier ausloesenden Eingaben mit Exakt-Assertion
    auf Text UND ``code``). Kein Survivor dieses Batches liegt in diesem
    Zweig — die missing_token-Mutanten ``_4``/``_8``/``_9``/``_10``/``_11``
    gehoeren zum Ursprungsbatch ``security-1``, nicht zu ``security-1n``.
    Dieser Test rundet den Fehlermeldungs-Vertrag der Funktion vollstaendig
    ab, ohne dass er fuer den Kill-Nachweis dieses Batches gebraucht wird.
    """
    with pytest.raises(ValidationError) as exc_info:
        check_version_conflict(sample_workitem, None, require_token=True)
    assert exc_info.value.messages == ["Für diese Änderung ist ein Versions-Token erforderlich."]
    assert exc_info.value.code == "missing_token"


def test_invalid_token_value_error_branch_message_exact(sample_workitem):
    """Pinnt ``x_check_version_conflict__mutmut_27`` (Meldung -> ``None``)
    und ``x_check_version_conflict__mutmut_32`` (Meldung -> XX-Marker-
    Variante desselben Wortlauts, von derselben Exakt-Assertion getroffen):
    ``ValueError``-Zweig, wenn ``datetime.fromisoformat()`` einen nicht-ISO-
    parsebaren Token nicht parsen kann.
    """
    with transaction.atomic():
        with pytest.raises(ValidationError) as exc_info:
            check_version_conflict(sample_workitem, "kaputt")
    assert exc_info.value.messages == ["Der übermittelte Versions-Token ist ungültig."]
    assert exc_info.value.code == "invalid_token"


def test_invalid_token_naive_datetime_branch_message_exact(sample_workitem):
    """Pinnt ``x_check_version_conflict__mutmut_39`` (Meldung -> ``None``),
    ``x_check_version_conflict__mutmut_43`` (Meldung -> ``_(None)`` — das
    Erzwingen des lazy-Uebersetzungs-Proxys zu ``str`` schlaegt fehl,
    sobald ``exc_info.value.messages`` gelesen wird) und
    ``x_check_version_conflict__mutmut_44`` (Meldung -> XX-Marker-Variante
    desselben Wortlauts): Zweig fuer einen erfolgreich geparsten, aber
    naiven (zeitzonenlosen) ISO-Zeitstempel.
    """
    sample_workitem.refresh_from_db()
    naive_token = sample_workitem.updated_at.replace(tzinfo=None).isoformat()

    with transaction.atomic():
        with pytest.raises(ValidationError) as exc_info:
            check_version_conflict(sample_workitem, naive_token)
    assert exc_info.value.messages == ["Der übermittelte Versions-Token ist ungültig."]
    assert exc_info.value.code == "invalid_token"


def test_version_conflict_branch_message_exact(sample_workitem):
    """Pinnt ``x_check_version_conflict__mutmut_50`` (Meldung -> ``None``)
    und ``x_check_version_conflict__mutmut_55`` (Meldung -> XX-Marker-
    Variante desselben Wortlauts): ein veralteter, aber zeitzonenbehafteter
    Zeitstempel weicht vom aktuellen DB-Wert ab (echter Versionskonflikt).
    """
    stale = "2000-01-01T00:00:00+00:00"
    with transaction.atomic():
        with pytest.raises(ValidationError) as exc_info:
            check_version_conflict(sample_workitem, stale)
    assert exc_info.value.messages == [
        "Der Datensatz wurde zwischenzeitlich bearbeitet. Bitte laden Sie die Seite neu."
    ]
    assert exc_info.value.code == "version_conflict"

"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.deletion`` (Batch ``deletion-1``).

Refs #2174.
Refs #2193.

Quelle: Vollmutations-Lauf auf main@049bc799 (frischer Mutantenbaum), Batch
``deletion-1`` (10 Survivor-Mutanten in zwei Funktionen: ``x_withdraw_deletion_request``
8 — ``_9``, ``_10``, ``_11``, ``_12``, ``_13``, ``_15``, ``_16``, ``_19`` —,
``x_annotate_deletion_targets`` 2 — ``_110``, ``_112``). Andere Funktionen
desselben Moduls (``eligible_deletion_confirmers``, ``withdraw_blocked_message``)
sind nicht Teil dieses Batches.

Zwei Mutationsachsen bei ``x_withdraw_deletion_request``:

* **Meldungs-Verlust/-Varianten auf der „fremde Antragstellerin"-Guard-Zeile**
  (``_9`` ``ValidationError(None)``, ``_10`` ``_(None)``, ``_11``
  ``"XX...XX"``-Wrap, ``_12``/``_13`` klein-/grossgeschrieben): ein
  exakter Abgleich von ``exc.value.messages`` gegen den deutschen Text
  toetet alle fuenf Varianten gemeinsam — ``_11``/``_12``/``_13`` waeren nach
  ``docs/testing/mutation-testing.md`` reine i18n-``skip``-Kandidaten (nur
  Gross-/Kleinschreibung bzw. ``XX``-Marker desselben Wortlauts), sterben
  hier aber tatsaechlich am selben Pin (empirisch mit ``MUTANT_UNDER_TEST``
  gegen die fertige Datei verifiziert) und sind deshalb laut bindender
  Klassifikationsregel ``test`` mit Verweis auf denselben Test, nicht
  ``skip``.
* **Meldungs-Verlust/-Argumentverlust auf der „nicht mehr PENDING"-Guard-Zeile**
  (``_15`` ``ValidationError(None)``, ``_16``
  ``withdraw_blocked_message(None)`` statt ``withdraw_blocked_message(dr.status)``):
  ein zweiter Rueckzugsversuch auf einen BEREITS zurueckgezogenen Antrag
  (``status == WITHDRAWN``) unterscheidet beide von einem korrekten Lauf —
  ``_15`` liefert ``["None"]`` statt des deutschen Textes, ``_16`` waehlt
  wegen ``None != WITHDRAWN`` faelschlich die generische „bereits
  entschieden"-Meldung statt der spezifischeren „bereits zurueckgezogen"-
  Meldung (``withdraw_blocked_message``, Modulkopf).

``x_withdraw_deletion_request__mutmut_19`` (``dr.save(update_fields=["status",
"reviewed_at"])`` -> ``dr.save(update_fields=None)``, voller Save) ist
``equivalent``: ``dr`` wird direkt zuvor per ``select_for_update().get(...)``
frisch geladen und bis zum Commit dieser Transaktion gesperrt (Docstring
„Sperr-Ordnung"), zwischen Laden und Speichern aendert ausschliesslich dieser
Code ``status``/``reviewed_at`` im Speicher, und ``DeletionRequest`` hat KEIN
``auto_now``-Feld (nur ``created_at`` mit ``auto_now_add`` — das bleibt beim
Neuladen und beim vollen Save gleich, ``core/models/workitem.py``). Alle drei
Bedingungen der bindenden Schreib-Scope-Regel (gesperrt+frisch geladen, kein
fehlendes ``auto_now``-Feld, kein abweichender In-Memory-Zustand) sind damit
erfuellt — ein voller Save schreibt exakt denselben Zeileninhalt wie die
Feldliste zurueck.

``x_annotate_deletion_targets__mutmut_110``/``_112`` (``date_format(...,
"DATE_FORMAT")`` -> ``date_format(..., None)`` bzw. Argument ganz entfernt,
faellt auf den Parameter-Default ``None`` zurueck) sind beide ``equivalent``:
``django.utils.formats.date_format(value, format=None, ...)`` wertet intern
``get_format(format or "DATE_FORMAT", ...)`` aus (Django-Quelltext,
installierte Version 6.0.8) — ``None`` ist falsy, der Ausdruck faellt also
IMMER auf denselben String ``"DATE_FORMAT"`` zurueck wie das Original. Kein
Aufrufer kann einen Unterschied beobachten, weil keiner existiert.

Konventionen wie ``test_mutation_pins_099_deletion_a.py``: eine Testklasse je
Guard-Zeile, jeder Test nennt seine Mutanten in der Docstring,
Zusicherungen vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Substring-Match uebersehen wuerde);
Fixtures kommen aus ``src/tests/conftest.py``; ``event_qualified``/
``own_request`` sind eine eigene, modul-lokale Kopie (bewusst nicht aus
``test_deletion_request_withdraw.py`` importiert, um Pin-Dateien voneinander
unabhaengig zu halten). ``_encryption_key`` liegt modulweit an (Lehre 112) — der
Mutantenbaum hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import DeletionRequest, Event
from core.services.deletion import withdraw_deletion_request
from core.services.events import request_deletion

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def event_qualified(facility, client_qualified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_qualified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 20, "notiz": "Qualifiziert"},
        created_by=staff_user,
    )


@pytest.fixture
def own_request(event_qualified, staff_user):
    """PENDING-Antrag, gestellt von ``staff_user``."""
    return request_deletion(event_qualified, staff_user, "Versehentlich gestellt")


class TestWithdrawForeignRequesterGuardMessage:
    """Guard-Zeile 1: ``dr.requested_by_id != user.pk``."""

    def test_fremde_antragstellerin_meldet_den_richtigen_text(self, own_request, lead_user):
        """Pins ``x_withdraw_deletion_request__mutmut_9`` (``ValidationError(None)``),
        ``_10`` (``_(None)``), ``_11`` (``"XX...XX"``-Wrap), ``_12``
        (kleingeschrieben) und ``_13`` (grossgeschrieben): alle fuenf weichen
        vom exakten deutschen Text ab und werden von derselben Zusicherung
        getoetet."""
        assert own_request.requested_by_id != lead_user.pk

        with pytest.raises(ValidationError) as exc:
            withdraw_deletion_request(own_request, lead_user)

        assert exc.value.messages == ["Nur die antragstellende Person kann einen Löschantrag zurückziehen."]
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.PENDING


class TestWithdrawNotPendingGuardMessage:
    """Guard-Zeile 2: ``dr.status != DeletionRequest.Status.PENDING``."""

    def test_bereits_zurueckgezogener_antrag_meldet_den_richtigen_text(self, own_request, staff_user):
        """Pins ``x_withdraw_deletion_request__mutmut_15`` (``ValidationError(None)``
        statt ``withdraw_blocked_message(dr.status)``) und ``_16``
        (``withdraw_blocked_message(None)`` statt ``withdraw_blocked_message(dr.status)``
        — mit ``status == WITHDRAWN`` waehlt die Mutante wegen ``None != WITHDRAWN``
        faelschlich die generische „bereits entschieden"-Meldung statt der
        spezifischeren „bereits zurueckgezogen"-Meldung, ``withdraw_blocked_message``,
        Modulkopf)."""
        withdraw_deletion_request(own_request, staff_user)
        own_request.refresh_from_db()
        assert own_request.status == DeletionRequest.Status.WITHDRAWN

        with pytest.raises(ValidationError) as exc:
            withdraw_deletion_request(own_request, staff_user)

        assert exc.value.messages == ["Dieser Löschantrag wurde bereits zurückgezogen."]

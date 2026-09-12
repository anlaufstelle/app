"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.events.tally_undo``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-sec.json``). Acht Survivors, KEINE Überschneidung mit den in
``test_mutation_pins_097_tally_undo.py`` bereits gepinnten Mutanten (dort:
``_matches_identity__mutmut_4``, ``undo_tally_event__mutmut_28/29/30/39/40/66/68``
— hier: der Signier-Salt, die Versionsprüfung und die komplette
Ablehn-Kette von ``mint_tally_undo_token``).

* **``x__signer__mutmut_1``**: der modulweite Salt (``"core.tally_undo.v1"``)
  fällt auf ``None`` (Djangos Default-Salt) — die Domänentrennung der
  Capability entfiele, obwohl Mint und Verify weiterhin zueinander passen
  (der Mutant überlebt jeden Funktionstest, der nur sich selbst signiert und
  prüft). Präventiv gegen Cross-Protocol-Replay mit einem anderen
  ``TimestampSigner`` ohne eigenen Salt.
* **``x__load_payload__mutmut_8``**: ``not isinstance(payload, dict) or
  payload.get("v") != _TALLY_UNDO_VERSION`` wird zu ``and`` — bei einem
  dict-Payload schließt ``not isinstance(...)`` (False) den Ausdruck kurz,
  die Versionsprüfung entfällt vollständig. Eine gültig signierte
  Fremdversion würde akzeptiert.
* **``x_mint_tally_undo_token__mutmut_1`` bis ``…_6``**: je ein ``or`` der
  siebengliedrigen Ablehn-Kette wird zu ``and``. Wegen der stärkeren Bindung
  von ``and`` entsteht ``… or (Ci and Ci+1) or …`` — verletzt ein Ereignis
  GENAU EINE der beiden benachbarten Klauseln, lehnt WEDER die eine NOCH die
  andere allein ab. Ein Testfall pro Klausel (sieben insgesamt) tötet alle
  sechs Kettenmutanten, weil jede Klausel an höchstens zwei Kettenpositionen
  beteiligt ist.

``_encryption_key`` liegt modulweit an: ``mint_tally_undo_token`` reicht bis in
den Anhangs-/File-Vault-Pfad, der Mutantenbaum hat keine ``.env``.
"""

from __future__ import annotations

import json

import pytest
from django.core.signing import TimestampSigner
from django.utils import timezone

from core.models import Event
from core.services.events import create_event
from core.services.events.tally_undo import (
    TallyUndoDenied,
    _capability_payload,
    _signer,
    mint_tally_undo_token,
    undo_tally_event,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_MODUL_SALT = "core.tally_undo.v1"


def _tally_event(facility, user, document_type, *, key="pin-098-tally"):
    return create_event(
        facility=facility,
        user=user,
        document_type=document_type,
        occurred_at=timezone.now(),
        data_json={},
        is_anonymous=True,
        idempotency_key=key,
        created_via=Event.CreatedVia.TALLY,
    )


def test_faehigkeit_ist_an_den_modul_salt_gebunden(facility, staff_user, doc_type_contact):
    """Pins ``x__signer__mutmut_1``: der Salt fällt auf ``None`` (Djangos
    Default). Eine Nutzlast, die unter einem FREMDEN ``TimestampSigner`` ohne
    eigenen Salt signiert wurde, dürfte der Modul-Signierer NICHT akzeptieren
    — genau das prüft dieser Test unabhängig von ``mint``/``undo``."""
    event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-signer")
    payload = json.dumps(_capability_payload(event), sort_keys=True, separators=(",", ":"))

    # Regulärer Modul-Signierer akzeptiert seine eigene Signatur.
    _signer().unsign(_signer().sign(payload), max_age=60)

    # Fremd-Signierer ohne den Modul-Salt darf hier NICHT durchgehen.
    fremd_token = TimestampSigner().sign(payload)
    with pytest.raises(TallyUndoDenied):
        undo_tally_event(facility, staff_user, fremd_token)


def test_gueltig_signierte_fremdversion_wird_abgewiesen(facility, staff_user, doc_type_contact):
    """Pins ``x__load_payload__mutmut_8``: ``not isinstance(...) or
    payload.get("v") != _TALLY_UNDO_VERSION`` wird zu ``and`` — bei einem
    dict-Payload schließt ``not isinstance`` (False) den Ausdruck kurz, die
    Versionsprüfung entfällt vollständig."""
    event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-version")
    payload = _capability_payload(event)
    payload["v"] = "v2"
    token = _signer().sign(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    with pytest.raises(TallyUndoDenied):
        undo_tally_event(facility, staff_user, token)

    event.refresh_from_db()
    assert event.is_deleted is False


class TestMintVerweigertJedeNichtTallyEigenschaft:
    """Pins ``x_mint_tally_undo_token__mutmut_1`` bis ``…_6``: je EIN Merkmal
    eines gültigen Tally-Ereignisses verletzen, alle anderen unverändert
    lassen. Ein Fall pro Klausel tötet alle sechs ``or``->``and``-Mutanten
    der Ablehn-Kette."""

    def test_created_via_ist_nicht_tally(self, facility, staff_user, doc_type_contact):
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-createdvia")
        Event.objects.filter(pk=event.pk).update(created_via=Event.CreatedVia.STANDARD)
        event.refresh_from_db()

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_ereignis_ist_nicht_anonym(self, facility, staff_user, doc_type_contact):
        """``create_event`` erzwingt ``is_anonymous=True``, sobald kein Client
        gesetzt ist und der Dokumentationstyp kein ``min_contact_stage``
        verlangt (Zeile ``client is None and not is_anonymous and not
        document_type.min_contact_stage``) — die Verletzung wird deshalb per
        Queryset-Update NACH dem Anlegen gesetzt, am Service vorbei."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-anonym")
        Event.objects.filter(pk=event.pk).update(is_anonymous=False)
        event.refresh_from_db()

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_ereignis_traegt_eine_person(self, facility, staff_user, client_identified, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            client=client_identified,
            is_anonymous=True,
            idempotency_key="pin-098-mint-client",
            created_via=Event.CreatedVia.TALLY,
        )

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_ereignis_traegt_einen_fall(self, facility, staff_user, case_open, doc_type_contact):
        """``create_event`` lehnt ``case`` mit personengebundenem ``case_open``
        bei ``is_anonymous=True`` direkt ab (``_validate_case_assignment``) —
        die Verletzung wird deshalb per Queryset-Update NACH dem Anlegen
        gesetzt, am Service vorbei."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-case")
        Event.objects.filter(pk=event.pk).update(case=case_open)
        event.refresh_from_db()

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_ersteller_fehlt(self, facility, staff_user, doc_type_contact):
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-creator")
        Event.objects.filter(pk=event.pk).update(created_by=None)
        event.refresh_from_db()

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_idempotenzschluessel_fehlt(self, facility, staff_user, doc_type_contact):
        """Der None-Fall MUSS ``TallyUndoDenied`` liefern, nicht irgendeine
        Exception — sonst bliebe der ``AttributeError`` aus
        ``_idempotency_key_hash`` (``None.encode(...)``) unbemerkt, sobald die
        Ablehn-Kette diese Klausel nicht mehr fängt."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-key")
        Event.objects.filter(pk=event.pk).update(idempotency_key=None)
        event.refresh_from_db()
        assert event.idempotency_key is None

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

    def test_ereignis_ist_bereits_geloescht(self, facility, staff_user, doc_type_contact):
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-098-mint-deleted")
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        event.refresh_from_db()

        with pytest.raises(TallyUndoDenied):
            mint_tally_undo_token(event)

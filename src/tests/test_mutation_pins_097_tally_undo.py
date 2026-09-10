"""Mutation-Pins der Welle J (0.97, Refs #2149) — Bindungen der
Strichlisten-Ruecknahme (``core.services.events.tally_undo``).

Quelle: Prio-1-Survivors des atomaren Messlaufs
(``welle-j-0.97/reports/triage-097-sec.json``, Batch ``sec``). Acht
bestaetigte Testluecken in drei Familien:

* **Die Capability-Identitaet** (:func:`_matches_identity`) — aus der
  UND-Kette wird ``(Ereignis, Einrichtung, Creator) or (Creator-Feld, Typ,
  Idempotenzschluessel, Erstellzeitpunkt)``. Damit faellt die Bindung an
  ``document_type``, ``idempotency_key_hash`` und ``created_at`` weg, sobald
  die erste Haelfte stimmt — und keiner dieser drei Werte wird spaeter noch
  einmal geprueft. Der Bestandsfall ``wrong_user`` toetet den Mutanten nicht,
  weil dort BEIDE Haelften falsch sind.
* **Der Replay-Nachweis** (die ``AuditLog``-Abfrage im ``is_deleted``-Zweig
  von :func:`undo_tally_event`) — faellt ``target_id``, ``user`` oder
  ``detail__source`` aus dem Filter, wird eine fremde bzw. eine ganz normale
  Loeschung als „schon zurueckgenommen" quittiert. Aus der Ablehnung wird eine
  Erfolgsmeldung — und ein Existenz-/Zustandsorakel ueber fremde Loeschungen,
  das der Docstring von ``TallyUndoDenied`` ausdruecklich vermeiden will.
* **Die Rechte-/Fristkette** — die ``or``-Kette wird an zwei Stellen zu einem
  ``and``-Paar, und die Eignungspruefung verliert die handelnde Person. Beides
  oeffnet den Loeschpfad fuer Akteure, die ihn nicht haben duerfen.

Gepinnte Mutanten (Mutantenbaum ``/work/anlaufstelle/mutants``, Stand
f5272d3e — ``tally_undo.py`` ist seitdem unveraendert):

* ``x__matches_identity__mutmut_4``    — ``and`` -> ``or`` vor ``event.created_by_id == user.pk``
* ``x_undo_tally_event__mutmut_28``    — ``target_id=str(event.pk)`` faellt aus dem Replay-Filter
* ``x_undo_tally_event__mutmut_29``    — ``user=user`` faellt aus dem Replay-Filter
* ``x_undo_tally_event__mutmut_30``    — ``detail__source="tally_undo"`` faellt aus dem Replay-Filter
* ``x_undo_tally_event__mutmut_39``    — ``or not user.is_assistant_or_above`` -> ``and`` mit der Sichtbarkeitsklausel
* ``x_undo_tally_event__mutmut_40``    — ``or <Frist>`` -> ``and`` mit der Rollenschwelle
* ``x_undo_tally_event__mutmut_66``    — ``evaluate_tally_eligibility(dt, user)`` -> ``(dt, None)``
* ``x_undo_tally_event__mutmut_68``    — ``evaluate_tally_eligibility(dt, user)`` -> ``(dt, )`` (Default ``None``)

Konventionen wie in den 096-Pin-Dateien: eine Testklasse je Familie, jeder
Test nennt seine Mutanten in der Docstring samt der Abweichung, Zusicherungen
vergleichen exakt (``==``/``is``), keine Fakes — es laufen echte Model-Objekte
durch die echten Services.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, User
from core.services.events import create_event
from core.services.events.crud import soft_delete_event
from core.services.events.tally_undo import TallyUndoDenied, mint_tally_undo_token, undo_tally_event

# ``_encryption_key`` ist Pflicht: ``soft_delete_event`` laeuft in den
# Anhang-/File-Vault-Pfad, und der Mutantenbaum hat keine ``.env`` — ohne die
# Fixture waeren die Pins dort aus dem falschen Grund rot (Lehre der parallelen
# Pin-Lanen der Welle J).
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _tally_event(facility, user, document_type, *, key):
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


def _delete_audits(event):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETE,
        target_type="Event",
        target_id=str(event.pk),
    )


class TestCapabilityIdentitaet:
    def test_faehigkeit_bleibt_an_den_idempotenzschluessel_gebunden(self, facility, staff_user, doc_type_contact):
        """Pins ``x__matches_identity__mutmut_4``: mit ``or`` statt ``and``
        genuegt die erste Haelfte der Kette (Ereignis-PK, Einrichtung,
        Creator-Feld im Payload) — der Digest des Idempotenzschluessels bindet
        die Capability dann nicht mehr. ``.update()`` statt ``save()``, damit
        ``updated_at`` (auto_now) unveraendert bleibt und wirklich DIESE
        Klausel den Unterschied macht."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-identity")
        token = mint_tally_undo_token(event)
        Event.objects.filter(pk=event.pk).update(idempotency_key="pin-097-identity-gedreht")

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

        event.refresh_from_db()
        assert event.is_deleted is False
        assert _delete_audits(event).count() == 0


class TestReplayNachweis:
    def test_replay_nachweis_ist_an_dieses_ereignis_gebunden(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_28``: ohne ``target_id`` im
        Replay-Filter genuegt IRGENDEINE fruehere Ruecknahme derselben Person
        in derselben Einrichtung, damit ein anderweitig geloeschtes Ereignis
        als „schon zurueckgenommen" quittiert wird."""
        event_a = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-replay-a")
        event_b = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-replay-b")
        token_b = mint_tally_undo_token(event_b)
        # A regulaer zuruecknehmen: erzeugt die einzige tally_undo-Audit-Zeile.
        undo_tally_event(facility, staff_user, mint_tally_undo_token(event_a))
        # B geht ueber den normalen Loeschpfad (ohne ``source``) weg.
        soft_delete_event(event_b, staff_user)

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token_b)

        assert _delete_audits(event_a).get().detail["source"] == "tally_undo"
        assert "source" not in _delete_audits(event_b).get().detail

    def test_replay_nachweis_ist_an_die_handelnde_person_gebunden(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_29``: ohne ``user`` im
        Replay-Filter zaehlt die Ruecknahme einer ANDEREN Person als eigener
        Replay — der Aufrufer bekommt statt ``TallyUndoDenied`` das Ereignis
        als Erfolg zurueck, und die Audit-Attribution traegt die Zusage
        „eigenes Ereignis, eigene Ruecknahme" nicht mehr."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-replay-actor")
        token = mint_tally_undo_token(event)
        other = User.objects.create_user(
            username="pin097-anderer-staff",
            role=User.Role.STAFF,
            facility=facility,
            is_staff=True,
        )
        soft_delete_event(event, other, source="tally_undo")

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

        audit = _delete_audits(event).get()
        assert audit.user_id == other.pk
        assert audit.detail["source"] == "tally_undo"

    def test_replay_nachweis_verlangt_die_ruecknahme_provenienz(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_30``: ohne
        ``detail__source="tally_undo"`` gilt jede DELETE-Audit-Zeile dieser
        Person zu diesem Ereignis als Ruecknahme-Replay — eine regulaere
        Loeschung ueber den normalen Pfad wird als erfolgreiches Undo
        quittiert. Der Bestandstest gleichen Namens setzt ``is_deleted`` direkt
        und erzeugt gar keine Audit-Zeile; genau diese Haelfte der Zusage fehlt
        dort."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-replay-source")
        token = mint_tally_undo_token(event)
        soft_delete_event(event, staff_user)

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

        audit = _delete_audits(event).get()
        assert audit.user_id == staff_user.pk
        assert "source" not in audit.detail


class TestRechteUndFristkette:
    def test_super_admin_darf_keinen_strich_zuruecknehmen(self, facility, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_39`` UND
        ``x_undo_tally_event__mutmut_40``: beide Mutanten paaren die
        Rollenschwelle ``not user.is_assistant_or_above`` per ``and`` mit einer
        Nachbarklausel (Sichtbarkeit bzw. Frist), die hier jeweils NICHT
        zutrifft — die Schwelle allein lehnt dann nicht mehr ab.
        ``is_assistant_or_above`` schliesst genau SUPER_ADMIN aus; eine
        Systemadministration, die Erstellerin eines Strichlisten-Ereignisses
        ist, koennte damit ueber die Ruecknahme in Einrichtungsdaten loeschen.
        Der Bestands-Rollentest ist reine Positivkontrolle fuer die
        Assistenz."""
        super_admin = User.objects.create_user(
            username="pin097-superadmin",
            role=User.Role.SUPER_ADMIN,
            facility=facility,
            is_staff=True,
        )
        event = _tally_event(facility, super_admin, doc_type_contact, key="pin-097-superadmin")
        token = mint_tally_undo_token(event)
        assert super_admin.is_assistant_or_above is False

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, super_admin, token)

        event.refresh_from_db()
        assert event.is_deleted is False
        assert _delete_audits(event).count() == 0

    def test_ruecknahme_endet_60_s_nach_erstellung_auch_bei_gueltiger_signatur(
        self, facility, staff_user, doc_type_contact
    ):
        """Pins ``x_undo_tally_event__mutmut_40``: die zweite Fristlinie. Die
        Signaturfrist (``max_age`` in ``_load_payload``) laeuft ab dem MINT,
        die Fachfrist ab der ERSTELLUNG — ein am Ende des Mint-Fensters
        ausgestellter Token bliebe sonst bis rund 120 s nach Erstellung
        verwendbar. Gepatcht wird nur die Modul-Uhr von ``tally_undo``,
        ``django.core.signing.time`` bleibt unangetastet, damit die Signatur
        frisch ist und wirklich diese Klausel entscheidet. Der
        Bestandsfall ``expired`` scheitert schon an ``_matches_identity``."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-097-frist")
        token = mint_tally_undo_token(event)
        spaeter = event.created_at + timedelta(seconds=90)

        with patch("core.services.events.tally_undo.timezone.now", return_value=spaeter):
            with pytest.raises(TallyUndoDenied):
                undo_tally_event(facility, staff_user, token)

        event.refresh_from_db()
        assert event.is_deleted is False
        assert _delete_audits(event).count() == 0

    def test_ruecknahme_prueft_die_eignung_mit_der_handelnden_person(self, facility, assistant_user):
        """Pins ``x_undo_tally_event__mutmut_66`` UND
        ``x_undo_tally_event__mutmut_68``: mit ``user=None`` liefert
        ``evaluate_tally_eligibility`` bewusst die rollenUNabhaengige
        Strukturentscheidung — die Gruende RESTRICTED_DOCUMENT_TYPE und
        RESTRICTED_REQUIRED_FIELD entfallen. Ersteren deckt die Zeile davor
        (``user_can_see_document_type``) noch ab, die Feld-Sensitivitaet aber
        nicht: eine Person, die den Typ sehen darf, ein sensibles Pflichtfeld
        darin aber nicht, koennte das Ereignis zuruecknehmen.

        Das Pflichtfeld kommt bewusst NACH dem Mint dazu — so bleibt der
        Schreibpfad des Ereignisses unberuehrt und der Unterschied liegt allein
        in der Eignungspruefung zum Ruecknahme-Zeitpunkt."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Strichliste (Pin 097)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
            tally_enabled=True,
        )
        event = _tally_event(facility, assistant_user, doc_type, key="pin-097-eignung")
        token = mint_tally_undo_token(event)

        vertraulich = FieldTemplate.objects.create(
            facility=facility,
            name="Vertrauliche Anzahl (Pin 097)",
            field_type=FieldTemplate.FieldType.NUMBER,
            is_required=True,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=vertraulich, sort_order=0)

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, assistant_user, token)

        event.refresh_from_db()
        assert event.is_deleted is False
        assert _delete_audits(event).count() == 0

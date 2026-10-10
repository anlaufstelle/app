"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — Batch ``events-5``.

Quelle: Vollmutation #2174, Batch ``events-5``
(``welle-l-0.99/reports/m-scratch/batch-events-5.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-events-5/triage-099-events-5.json``).
28 Survivor-/No-tests-Mutanten in drei Modulen: ``core.services.events.tally_undo``
(11), ``core.services.events.feed`` (9), ``core.services.events.tally`` (8).

Nach Triage sind 16 der 28 Mutanten ``equivalent`` (siehe Begruendungen in der
Triage-Datei; alle NEUN ``feed.py``-Mutanten fallen in diese Klasse — sie
betreffen ausschliesslich das ``flat``-Argument von ``values_list()``, das nur
die Python-Iterationsverpackung steuert und die als Subquery eingebettete SQL
NICHT aendert, bzw. einen beweisbar unerreichbaren ``.get()``-Default; sechs
weitere in ``tally.py`` sind DB-Constraint- bzw. Default-Wert-Aequivalente —
darunter ``x_build_tally_summary__mutmut_17`` (fehlender ``facility=facility``-
Filter in der Zaehl-Abfrage): NICHT ueber „pk ist global eindeutig" (laut
bindender Aequivalenzregel dieser Welle explizit KEIN Beweis), sondern ueber
die zusammengesetzte Fremdschluessel-Bindung ``core_event_document_type_facility_fk``
(Migration ``0166_event_composite_fk.py``, Refs #2066): sie erzwingt fuer JEDE
committete Event-Zeile ``event.facility_id == event.document_type.facility_id``
— unabhaengig von RLS-Bypass, Bulk-Update oder Migrationspfad. Damit kann kein
Ereignis einer anderen Einrichtung je den ``document_type.pk`` DIESER
Einrichtung tragen; die Zeilen von ``build_tally_summary`` sind beweisbar
unveraendert. Zwoelf Mutanten sind echte Testluecken und werden hier gepinnt:

* **Grenzwert bei 60 Sekunden** (``mint_tally_undo_token``/``undo_tally_event``,
  je ein unabhaengiger Fristvergleich): ``>`` -> ``>=`` verschiebt die Grenze
  um einen Zeitpunkt.
* **Determinismus der signierten Nutzlast**: fuenf Mutanten verlassen die
  sortierte/kompakte JSON-Form, unter der die Rücknahme-Capability signiert
  wird — ein gemeinsamer Pin unsigniert den Token und prueft Byte-Form statt
  Text der Gesamtsignatur (die ohnehin je Aufruf neu datiert ist).
* **Abfragezahl beim Laden des gesperrten Events**: ``select_related`` faellt
  weg, das Ergebnis bleibt gleich, die Abfragezahl steigt.
* **Replay-Nachweis** (``undo_tally_event``, ``is_deleted``-Zweig): zwei
  weitere Filterfelder (``action``, ``target_type``) faellen aus der bereits
  in Welle J teilweise gepinnten Audit-Existenzpruefung.
* **``current_tally_window``**: exportiert, aber im gesamten Baum nirgends
  aufgerufen (echte Testluecke) — ein Mutant stuerzt ab, der andere ignoriert
  die uebergebene Facility bei der Schichterkennung.

``_encryption_key`` liegt modulweit an: der Mutantenbaum hat keine ``.env``,
und ``create_event``/``soft_delete_event`` laufen bis in den
Anhangs-/File-Vault-Pfad.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, Event, TimeFilter
from core.services.audit import audit_event
from core.services.events import create_event
from core.services.events.tally import current_tally_window
from core.services.events.tally_undo import (
    TALLY_UNDO_SECONDS,
    TallyUndoDenied,
    _signer,
    mint_tally_undo_token,
    undo_tally_event,
)

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


class TestMintTokenGrenzwertAn60Sekunden:
    def test_mint_erlaubt_genau_an_der_60_sekunden_grenze(self, facility, staff_user, doc_type_contact):
        """Pins ``x_mint_tally_undo_token__mutmut_14``: ``>`` -> ``>=`` am
        Fristvergleich ``timezone.now() - event.created_at > timedelta(seconds=
        TALLY_UNDO_SECONDS)``. Exakt auf der Grenze (Differenz == 60 s) lehnt
        das Original NICHT ab (``60 > 60`` ist falsch), die Mutante SEHR WOHL
        (``60 >= 60``)."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-mint-grenze")
        grenze = event.created_at + timedelta(seconds=TALLY_UNDO_SECONDS)

        with patch("core.services.events.tally_undo.timezone.now", return_value=grenze):
            token = mint_tally_undo_token(event)

        assert token


class TestUndoGrenzwertAn60Sekunden:
    def test_ruecknahme_ist_genau_an_der_60_sekunden_grenze_noch_erlaubt(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_56``: dieselbe ``>``->``>=``-
        Verschaerfung, aber in der zweiten, unabhaengigen Fristpruefung der
        Rechte-/Fristkette von ``undo_tally_event`` selbst (ggue. dem
        Erstellzeitpunkt, nicht dem Mint-Zeitpunkt aus der Signatur)."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-undo-grenze")
        token = mint_tally_undo_token(event)
        grenze = event.created_at + timedelta(seconds=TALLY_UNDO_SECONDS)

        with patch("core.services.events.tally_undo.timezone.now", return_value=grenze):
            result = undo_tally_event(facility, staff_user, token)

        assert result.pk == event.pk
        event.refresh_from_db()
        assert event.is_deleted is True


class TestMintTokenSerialisierungIstDeterministisch:
    def test_serialisierte_nutzlast_ist_sortiert_und_kompakt(self, facility, staff_user, doc_type_contact):
        """Pins ``x_mint_tally_undo_token__mutmut_18`` (``sort_keys=None``),
        ``…__mutmut_19`` (``separators=None``), ``…__mutmut_21`` (``sort_keys``
        entfaellt komplett), ``…__mutmut_22`` (``separators=(",", ":")``
        entfaellt komplett -> Default mit Leerzeichen) und ``…__mutmut_24``
        (``sort_keys=False``): alle fuenf verlassen die deterministische,
        sortierte/kompakte JSON-Form, unter der die Ruecknahme-Capability
        signiert wird. Der Verify-Pfad (``_load_payload``) liest per
        Schluessel und bliebe unveraendert funktionsfaehig — die Abweichung
        ist ausschliesslich an der tatsaechlich signierten Nutzlast selbst
        beobachtbar (Byte-Form statt Textvergleich der GESAMTEN, je Aufruf neu
        datierten Signatur)."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-format")
        token = mint_tally_undo_token(event)

        raw = _signer().unsign(token, max_age=TALLY_UNDO_SECONDS)

        # Sortierte Schluessel: "created_at" ist alphabetisch der erste
        # der acht Payload-Schluessel (v/event/facility/creator/document_type/
        # idempotency_key_hash/created_at/updated_at).
        assert raw.startswith('{"created_at":'), raw
        # Kompakte Separatoren: keine Leerzeichen nach Komma oder Doppelpunkt.
        assert ", " not in raw, raw
        assert ": " not in raw, raw


class TestUndoLaedtDenDokumentationstypInDerGleichenAbfrage:
    def test_document_type_is_joined_without_an_extra_query(self, facility, staff_user, doc_type_contact, monkeypatch):
        """Pin eager loading while isolating the independently tested monthly carry."""
        monkeypatch.setattr("core.services.dashboard.snapshot.carry_deleted_event", lambda facility, event: None)
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-queries")
        token = mint_tally_undo_token(event)

        with CaptureQueriesContext(connection) as captured:
            result = undo_tally_event(facility, staff_user, token)

        assert result.pk == event.pk
        # Business-activity tracking adds two required queries (Refs #2524).
        # Pin the eager join directly as well as the total query budget.
        queries = [row["sql"] for row in captured.captured_queries]
        event_joins = [sql for sql in queries if 'FROM "core_event"' in sql and 'JOIN "core_documenttype"' in sql]
        assert len(event_joins) == 1, queries
        assert not any('FROM "core_documenttype"' in sql for sql in queries), queries
        assert len(queries) <= 27, queries


class TestReplayNachweisPrueftAktionUndZieltyp:
    def test_replay_nachweis_verlangt_die_loesch_aktion(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_26``: ``action=AuditLog.Action.DELETE``
        faellt aus dem Replay-Nachweis-Filter des ``is_deleted``-Zweigs. Eine
        irrelevante Audit-Zeile (hier: ``CLIENT_UPDATE``) mit passendem
        Zieltyp/-id, Akteurin und ``detail__source="tally_undo"`` darf NICHT
        als Nachweis einer bereits erfolgten Ruecknahme durchgehen."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-replay-action")
        token = mint_tally_undo_token(event)
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        audit_event(
            AuditLog.Action.CLIENT_UPDATE,
            user=staff_user,
            facility=facility,
            target_obj=event,
            detail={"source": "tally_undo"},
        )

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)

    def test_replay_nachweis_verlangt_den_zieltyp_event(self, facility, staff_user, doc_type_contact):
        """Pins ``x_undo_tally_event__mutmut_27``: ``target_type="Event"``
        faellt aus dem Replay-Nachweis-Filter. Eine DELETE-Audit-Zeile eines
        FREMDEN Zieltyps mit zufaellig passender ``target_id`` (hier: derselbe
        String wie die Event-PK, nur unter ``target_type="Attachment"``
        gefuehrt), gleicher Akteurin und ``detail__source="tally_undo"`` darf
        NICHT als Nachweis fuer DIESES Event durchgehen."""
        event = _tally_event(facility, staff_user, doc_type_contact, key="pin-099-events5-replay-target-type")
        token = mint_tally_undo_token(event)
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        audit_event(
            AuditLog.Action.DELETE,
            user=staff_user,
            facility=facility,
            target_type="Attachment",
            target_id=str(event.pk),
            detail={"source": "tally_undo"},
        )

        with pytest.raises(TallyUndoDenied):
            undo_tally_event(facility, staff_user, token)


class TestCurrentTallyWindow:
    def test_liefert_ein_fenster_statt_abzustuerzen(self, facility):
        """Pins ``x_current_tally_window__mutmut_1``: ``context = None`` statt
        des Aufrufs von ``current_tally_context(facility)`` — jeder Aufruf
        stuerzte mit ``AttributeError`` ab. ``current_tally_window`` ist
        exportiert (``core/services/events/__init__.py``), aber sonst nirgends
        im Baum aufgerufen — kein Bestandstest haette den Absturz gefangen."""
        window_start, window_end = current_tally_window(facility)

        assert window_start < window_end

    def test_verwendet_die_uebergebene_einrichtung_fuer_die_schichterkennung(self, facility):
        """Pins ``x_current_tally_window__mutmut_2``:
        ``current_tally_context(facility)`` -> ``current_tally_context(None)``.
        ``TimeFilter.objects.for_facility(None)`` filtert auf
        ``facility_id IS NULL`` — bei einer nicht-nullbaren FK immer leer. Die
        Mutante faellt deshalb IMMER auf den Ganztags-Fallback zurueck, selbst
        waehrend eine echte, enger begrenzte Schicht dieser Einrichtung
        laeuft."""
        TimeFilter.objects.create(
            facility=facility,
            label="Pin099-events5-Fruehschicht",
            start_time=time(0, 0),
            end_time=time(12, 0),
            sort_order=0,
        )
        mitten_in_der_schicht = timezone.make_aware(datetime.combine(timezone.localdate(), time(6, 0)))

        with patch("core.services.events.tally.timezone.localtime", return_value=mitten_in_der_schicht):
            window_start, window_end = current_tally_window(facility)

        assert timezone.localtime(window_end).time() == time(12, 0)


def test_tally_undo_locks_only_the_event_row(facility, staff_user, doc_type_contact):
    event = _tally_event(facility, staff_user, doc_type_contact, key="undo-row-lock-target")
    token = mint_tally_undo_token(event)
    with CaptureQueriesContext(connection) as captured:
        undo_tally_event(facility, staff_user, token)
    joined_locks = [
        query["sql"]
        for query in captured.captured_queries
        if "FOR UPDATE" in query["sql"] and 'JOIN "core_documenttype"' in query["sql"]
    ]
    assert len(joined_locks) == 1, joined_locks
    assert 'FOR UPDATE OF "core_event"' in joined_locks[0], joined_locks

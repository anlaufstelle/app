"""EventHistory-Retention nach ADR-050 (Refs #2065).

Deckt Schema (D1.4/D6), Anzeige/Auskunft (D6), den Redaktions-Sweep (D1/D5),
die Legal-Hold-Kopplung (D2/M-9) und die Guards aus der Migrationsskizze
Schritt 9 ab.
"""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from core.models import EventHistory, Settings
from core.services.events import create_event, update_event
from core.templatetags.history_tags import compute_diff


@pytest.mark.django_db
class TestRetentionSchema:
    """Paket 2 (ADR-050 D1.4/D3/D6/M-5/M-7): neue Felder + Teilindex."""

    def test_settings_hat_detail_und_chronicle_frist_mit_default_24(self, facility):
        settings_obj = Settings.objects.create(facility=facility)
        assert settings_obj.eventhistory_detail_months == 24
        assert settings_obj.eventhistory_chronicle_months == 24

    def test_beide_fristen_lassen_sich_auf_null_stellen(self, facility):
        """``0`` = aus (M-5/M-7) — kein MinValueValidator(1) wie bei den Tages-Fristen."""
        settings_obj = Settings.objects.create(
            facility=facility,
            eventhistory_detail_months=0,
            eventhistory_chronicle_months=0,
        )
        settings_obj.full_clean()
        assert settings_obj.eventhistory_detail_months == 0

    def test_eventhistory_hat_nullbares_redacted_at(self):
        field = EventHistory._meta.get_field("redacted_at")
        assert field.null is True

    def test_beide_fristen_sind_im_einstellungs_formular_editierbar(self):
        """D3: „ein weiterer Regler in den Einrichtungs-Einstellungen" — sonst ist die
        Frist nur per Shell aenderbar und die Doku aus Paket 10 verspricht zu viel."""
        from core.forms.facility_settings import FacilitySettingsForm

        assert "eventhistory_detail_months" in FacilitySettingsForm.Meta.fields
        assert "eventhistory_chronicle_months" in FacilitySettingsForm.Meta.fields

    def test_fristaenderung_wird_mit_alt_und_neu_wert_auditiert(self, facility, admin_user):
        """Eine Verkuerzung/Abschaltung entscheidet ueber die Tilgung von Inhalten —
        dieselbe Nachweispflicht wie bei ``auditlog_retention_months``."""
        from core.models import AuditLog
        from core.services.system.settings import update_settings

        settings_obj = Settings.objects.create(facility=facility)
        update_settings(
            settings_obj,
            admin_user,
            expected_updated_at=settings_obj.updated_at.isoformat(),
            eventhistory_detail_months=6,
        )

        entry = AuditLog.objects.get(action=AuditLog.Action.SETTINGS_CHANGE)
        assert entry.detail["changes"]["eventhistory_detail_months"] == {"old": 24, "new": 6}

    def test_teilindex_auf_changed_at_nur_fuer_unredigierte_zeilen(self):
        """D6: ``(changed_at) WHERE redacted_at IS NULL`` — der Index schrumpft mit jedem Lauf."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'core_eventhistory' AND indexdef LIKE '%%changed_at%%'"
            )
            definitions = [row[0] for row in cur.fetchall()]
        assert any("redacted_at IS NULL" in d for d in definitions), definitions


def _mark_redacted(entry, *, before=None, after=None):
    """Setzt die Redaktions-Marker per SQL — der Sweep aus Paket 4 existiert hier
    noch nicht, und ``EventHistory.save()`` ist append-only."""
    from core.services.system import bypass_replication_triggers

    with bypass_replication_triggers():
        EventHistory.objects.filter(pk=entry.pk).update(
            data_before=before,
            data_after=after,
            redacted_at=timezone.now(),
        )
    return EventHistory.objects.get(pk=entry.pk)


@pytest.mark.django_db
class TestRedactedDisplay:
    """Paket 3 (ADR-050 D6): Anzeige und Auskunft VOR jeder Datenaenderung.

    ``_diff_delete`` kennt den ``_redacted``-Zweig seit Refs #714;
    ``_diff_create``/``_diff_update`` nicht — sie wuerden die Marker-Schluessel
    ``_redacted``/``fields`` als Feld-Labels rendern. Das faellt heute nicht auf,
    weil Art.-17-redigierte Ereignisse geloescht und ihre Detailseiten
    unerreichbar sind; mit dem Sweep betrifft es LEBENDE, sichtbare Ereignisse.
    """

    def test_redigierte_create_zeile_rendert_feldnamen_statt_marker_schluessel(
        self, facility, staff_user, doc_type_contact
    ):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )
        entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        entry = _mark_redacted(entry, after={"_redacted": True, "fields": ["dauer", "notiz"]})

        result = compute_diff(entry)

        labels = {f["label"] for f in result["fields"]}
        assert labels == {"Dauer", "Notiz"}
        assert "_redacted" not in labels and "fields" not in labels
        assert all(f["value"] != "True" for f in result["fields"])

    def test_redigierte_update_zeile_zeigt_beide_spalten_ohne_werte(self, facility, staff_user, doc_type_contact):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Alt"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Neu"})
        entry = EventHistory.objects.filter(event=event, action=EventHistory.Action.UPDATE).first()
        entry = _mark_redacted(
            entry,
            before={"_redacted": True, "fields": ["dauer", "notiz"]},
            after={"_redacted": True, "fields": ["dauer", "notiz"]},
        )

        result = compute_diff(entry)

        labels = {f["label"] for f in result["fields"]}
        assert labels == {"Dauer", "Notiz"}
        for field in result["fields"]:
            assert "Alt" not in str(field["old_value"])
            assert "Neu" not in str(field["new_value"])
            assert field["changed"] is True

    def test_auskunft_meldet_redigierte_create_zeile_als_redigiert(self, facility, staff_user, doc_type_contact):
        """D1.3: Das Top-Level-Merkmal wurde bisher NUR aus ``data_before`` abgeleitet.
        Eine redigierte CREATE-Zeile hat dort ``NULL`` (und behaelt es, D1.1) und
        meldete deshalb faelschlich ``redacted: false``."""
        from core.services.client.export import _gather_event_history

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        entry = EventHistory.objects.get(event=event, action=EventHistory.Action.CREATE)
        _mark_redacted(entry, after={"_redacted": True, "fields": ["dauer"]})

        payload = _gather_event_history([event.pk])

        assert len(payload) == 1
        assert payload[0]["redacted"] is True
        assert payload[0]["data_before"] == []
        assert payload[0]["data_after"] == [{"slug": "dauer", "label": "Dauer", "value": None}]


@pytest.fixture
def event_with_history(facility, staff_user, doc_type_contact):
    """Ein Ereignis mit CREATE- und UPDATE-Fassung — zwei belegte Historienzeilen."""
    event = create_event(
        facility=facility,
        user=staff_user,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 15, "notiz": "Erstgespräch"},
    )
    update_event(event, staff_user, {"dauer": 30, "notiz": "Zweitgespräch"})
    return event


@pytest.mark.django_db
class TestRedactEventHistoryRows:
    """Paket 4 (ADR-050 D1/D5): der einzige Redaktions-Schreiber."""

    def test_spaltengenau_null_bleibt_null_und_je_spalte_eigene_slug_liste(self, facility, event_with_history):
        from core.retention.eventhistory import redact_event_history_rows

        create_row = EventHistory.objects.get(event=event_with_history, action=EventHistory.Action.CREATE)
        update_row = EventHistory.objects.get(event=event_with_history, action=EventHistory.Action.UPDATE)

        result = redact_event_history_rows(facility, [create_row.pk, update_row.pk], category="test")

        assert result.redacted == 2
        create_row.refresh_from_db()
        update_row.refresh_from_db()
        # D1.1: die CREATE-Zeile hatte nie ein „Vorher" — sie bekommt auch keins.
        assert create_row.data_before is None
        assert create_row.data_after == {"_redacted": True, "fields": ["dauer", "notiz"]}
        # D1.2: je Spalte die eigene Liste.
        assert update_row.data_before == {"_redacted": True, "fields": ["dauer", "notiz"]}
        assert update_row.data_after == {"_redacted": True, "fields": ["dauer", "notiz"]}
        assert create_row.redacted_at is not None

    def test_metadaten_snapshot_bleibt_stehen(self, facility, event_with_history):
        """M-3: ``field_metadata`` wird NICHT eingedampft — die revisionsfeste
        Darstellung der uebrigen Feld-Labels (Refs #824) haengt daran."""
        from core.retention.eventhistory import redact_event_history_rows

        row = EventHistory.objects.get(event=event_with_history, action=EventHistory.Action.CREATE)
        before = dict(row.field_metadata)

        redact_event_history_rows(facility, [row.pk], category="test")

        row.refresh_from_db()
        assert row.field_metadata == before
        assert before

    def test_zweiter_lauf_ist_idempotent(self, facility, event_with_history):
        from core.retention.eventhistory import redact_event_history_rows

        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))
        first = redact_event_history_rows(facility, pks, category="test")
        stamps = dict(EventHistory.objects.filter(pk__in=pks).values_list("pk", "redacted_at"))

        second = redact_event_history_rows(facility, pks, category="test")

        assert first.redacted == 2
        assert second.redacted == 0
        assert dict(EventHistory.objects.filter(pk__in=pks).values_list("pk", "redacted_at")) == stamps

    def test_nachweis_je_charge_mit_anzahl_und_chargengrenze(self, facility, event_with_history):
        from core.models import AuditLog
        from core.retention.eventhistory import redact_event_history_rows

        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))
        redact_event_history_rows(facility, pks, category="test")

        entry = AuditLog.objects.get(action=AuditLog.Action.EVENT_HISTORY_REDACTED)
        assert entry.detail["count"] == 2
        assert entry.detail["category"] == "test"
        assert entry.detail["batch_first"] and entry.detail["batch_last"]
        assert entry.target_type == "EventHistory"

    def test_ohne_bypass_kontext_bricht_der_sweep_fail_loud_ab(self, facility, event_with_history, monkeypatch):
        """D5.1 (Muster Refs #1016): ohne RLS-Bypass liefe der Sweep ueber 0 Zeilen
        und meldete Erfolg — genau das darf er nicht."""
        from core.retention import eventhistory as module

        monkeypatch.setattr(module, "has_rls_bypass_context", lambda: False)
        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))

        with pytest.raises(RuntimeError, match="Bypass"):
            module.redact_event_history_rows(facility, pks, category="test")

        assert not EventHistory.objects.filter(redacted_at__isnull=False).exists()

    def test_ereignis_unter_legal_hold_wird_zurueckgestellt_nicht_uebersprungen(
        self, facility, event_with_history, admin_user
    ):
        """D2: gehaltene Ereignisse bleiben in der Menge, ihr Zurueckstellen wird
        gezaehlt — still zu ueberspringen erzeugte den Zustand „geloescht, aber
        unredigiert", den niemand mehr sieht."""
        from core.models import LegalHold
        from core.retention.eventhistory import redact_event_history_rows

        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event_with_history.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )
        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))

        result = redact_event_history_rows(facility, pks, category="test")

        assert result.redacted == 0
        assert result.deferred == 1
        assert not EventHistory.objects.filter(pk__in=pks, redacted_at__isnull=False).exists()


@pytest.mark.django_db
class TestBypassTriggerSelection:
    """Paket 5 (ADR-050 D5.5/M-6): Trigger-Auswahl und kanonische Sperr-Reihenfolge."""

    def test_append_only_trigger_reihenfolge_ist_eventhistory_vor_auditlog(self):
        """D5.5: Die Reihenfolge war ``core_auditlog`` zuerst — der Anwendung
        (Historie, dann Audit) genau entgegengesetzt und damit ein ABBA-Zyklus.
        Sie ist sonst genau die Art von Ordnung, die ein spaeteres Refactoring
        stillschweigend dreht."""
        from core.services.system._db_admin import _APPEND_ONLY_TRIGGERS

        tables = [table for table, _trigger in _APPEND_ONLY_TRIGGERS]
        assert tables.index("core_eventhistory") < tables.index("core_auditlog")

    def test_ohne_auswahl_bleibt_das_bestandsverhalten(self):
        from core.services.system._db_admin import _APPEND_ONLY_TRIGGERS, _selected_triggers

        assert _selected_triggers(None) == list(_APPEND_ONLY_TRIGGERS)

    def test_auswahl_oeffnet_nur_die_genannte_tabelle(self):
        from core.services.system._db_admin import _selected_triggers

        selected = _selected_triggers(("core_eventhistory",))

        assert {table for table, _trigger in selected} == {"core_eventhistory"}
        assert len(selected) == 2

    def test_sweep_laesst_das_auditlog_geschlossen(self, facility, event_with_history, monkeypatch):
        """M-6: Der Sweep nimmt auf ``core_auditlog`` gar keine DDL-Sperre mehr,
        sondern nur das ROW EXCLUSIVE seines eigenen Nachweis-Inserts — dieselbe
        Ordnung wie jeder Request, kein Zyklus mehr moeglich."""
        from core.retention.eventhistory import redact_event_history_rows
        from core.services.system import _db_admin

        opened: list[tuple[str, str]] = []
        original = _db_admin.bypass_replication_triggers

        def _recording(tables=None):
            opened.extend(_db_admin._selected_triggers(tables))
            return original(tables=tables)

        monkeypatch.setattr("core.retention.eventhistory.bypass_replication_triggers", _recording)
        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))

        redact_event_history_rows(facility, pks, category="test")

        assert opened
        assert all(table == "core_eventhistory" for table, _trigger in opened)


def _age_rows(event, *, days):
    """Setzt ``changed_at`` aller Fassungen zurueck — ``auto_now_add`` laesst sich
    beim Anlegen nicht steuern, und der Trigger blockt gewoehnliche Updates.

    Die relative Ordnung bleibt erhalten (Sekundenabstand): ein pauschaler
    identischer Zeitstempel machte „die juengste Fassung" mehrdeutig und pruefte
    dann nur noch die zufaellige UUID-Ordnung.
    """
    from core.services.system import bypass_replication_triggers

    rows = list(EventHistory.objects.filter(event=event).order_by("changed_at", "pk"))
    base = timezone.now() - timedelta(days=days)
    with bypass_replication_triggers(tables=("core_eventhistory",)):
        for index, row in enumerate(rows):
            EventHistory.objects.filter(pk=row.pk).update(changed_at=base + timedelta(seconds=index))


@pytest.mark.django_db
class TestRetentionRunWiring:
    """Paket 6 (ADR-050 D2/D3/D4/D8): die drei Mengen im ``enforce_retention``-Lauf."""

    def test_geloeschtes_ereignis_hinterlaesst_keine_unredigierte_zeile(
        self, facility, settings_obj, staff_user, event_with_history
    ):
        """Der §5-Befund als Regressionsnetz: der Fristablauf leerte bisher das
        Ereignis, nicht seine Historie."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)

        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert not EventHistory.objects.filter(event=event_with_history, redacted_at__isnull=True).exists()
        for row in EventHistory.objects.filter(event=event_with_history):
            for payload in (row.data_before, row.data_after):
                assert payload is None or payload.get("_redacted") is True

    def test_lebendes_ereignis_behaelt_die_juengste_fassung(self, facility, settings_obj, event_with_history):
        """D4/M-2 (N=1): auf der Detailseite bleibt immer mindestens eine lesbare
        letzte Aenderung stehen, auch bei einer jahrelang unangetasteten Akte."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        _age_rows(event_with_history, days=1000)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        newest = EventHistory.objects.filter(event=event_with_history).order_by("-changed_at", "-pk").first()
        assert newest.redacted_at is None
        assert newest.data_after == {"dauer": 30, "notiz": "Zweitgespräch"}
        assert result["redacted"] == 1

    def test_detailfrist_null_schaltet_nur_die_altersmenge_ab(self, facility, settings_obj, event_with_history):
        """``0`` = aus (M-5). Der D2-Zweig ist davon unberuehrt — er ist eine
        DSGVO-Korrektur, keine konfigurierbare Frist."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        settings_obj.eventhistory_detail_months = 0
        settings_obj.save(update_fields=["eventhistory_detail_months"])
        _age_rows(event_with_history, days=1000)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert not EventHistory.objects.filter(redacted_at__isnull=False).exists()

    def test_dry_run_zaehlt_ohne_zu_aendern(self, facility, settings_obj, event_with_history):
        from core.retention.eventhistory import enforce_eventhistory_retention

        _age_rows(event_with_history, days=1000)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)

        assert result["redacted"] == 1
        assert not EventHistory.objects.filter(redacted_at__isnull=False).exists()

    def test_stufe_zwei_loescht_die_redigierte_zeile_nach_ihrer_eigenen_frist(
        self, facility, settings_obj, event_with_history
    ):
        """D8/M-7: gemessen ab ``redacted_at``, nicht ab ``changed_at`` — die Frage
        lautet „wie lange steht die Zeile schon leer"."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.system import bypass_replication_triggers

        row = EventHistory.objects.filter(event=event_with_history).order_by("changed_at").first()
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(pk=row.pk).update(
                data_after={"_redacted": True, "fields": ["dauer"]},
                redacted_at=timezone.now() - timedelta(days=1000),
            )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["deleted"] == 1
        assert not EventHistory.objects.filter(pk=row.pk).exists()

    def test_chronikfrist_null_haelt_die_chronologie_unbefristet(self, facility, settings_obj, event_with_history):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.system import bypass_replication_triggers

        settings_obj.eventhistory_chronicle_months = 0
        settings_obj.save(update_fields=["eventhistory_chronicle_months"])
        row = EventHistory.objects.filter(event=event_with_history).order_by("changed_at").first()
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(pk=row.pk).update(redacted_at=timezone.now() - timedelta(days=1000))

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["deleted"] == 0
        assert EventHistory.objects.filter(pk=row.pk).exists()


@pytest.mark.django_db
class TestLegalHoldCoupling:
    """Paket 7 (ADR-050 D2/M-9): der Hold dreifach abgesichert."""

    def test_hold_anlage_sperrt_die_ereigniszeile_vor_dem_schreiben(self, facility, admin_user, event_with_history):
        """D2: ``create_hold_for_target`` lief bisher OHNE ``transaction.atomic``,
        legte nur die LegalHold-Zeile an und fasste das Ziel-Ereignis nie an —
        ``LegalHold.target_id`` ist ein freies UUID-Feld ohne Fremdschluessel, die
        Datenbank koordinierte hier also von sich aus nichts. Der gemeinsame
        Koordinationspunkt mit dem Sweep ist die Ereigniszeile; sie muss VOR dem
        Schreiben der Hold-Zeile gesperrt werden."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from core.retention.legal_holds import create_hold_for_target

        with CaptureQueriesContext(connection) as captured:
            create_hold_for_target(
                facility,
                target_type="Event",
                target_id=event_with_history.pk,
                user=admin_user,
                reason="Ermittlungsverfahren",
            )

        statements = [q["sql"] for q in captured.captured_queries]
        lock_at = next(i for i, sql in enumerate(statements) if "core_event" in sql and "FOR UPDATE" in sql.upper())
        insert_at = next(i for i, sql in enumerate(statements) if "INSERT INTO" in sql.upper() and "legalhold" in sql)
        assert lock_at < insert_at

    def test_hold_auf_bereits_redigiertes_ereignis_gilt_als_verspaetet(
        self, facility, settings_obj, admin_user, staff_user, event_with_history
    ):
        """D2, Rennrichtung „Redaktion zuerst": der Hold wird NICHT abgelehnt — er
        bindet weiterhin Ereigniszeile, Anhaenge und die Anonymisierung —, aber er
        traegt das Verspaetungsmerkmal. Ein stiller Hold, der Vollstaendigkeit
        suggeriert, waere die schlechtere Antwort: die Spoliationsfrage muss
        spaeter beantwortbar sein."""
        from core.models import AuditLog
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        hold = create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event_with_history.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        assert hold.history_already_redacted is True
        entry = AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).latest("timestamp")
        assert entry.detail["history_already_redacted"] is True

    def test_hold_zuerst_stellt_das_ereignis_im_lauf_zurueck(
        self, facility, settings_obj, admin_user, staff_user, event_with_history
    ):
        """D2, Rennrichtung „Hold zuerst": die Historie bleibt vollstaendig, genau
        wie der Hold es anordnet — und das Zurueckstellen erscheint in der
        Lauf-Zusammenfassung, statt still zu passieren."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event_with_history.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert result["deferred"] == 1
        assert EventHistory.objects.filter(event=event_with_history, redacted_at__isnull=True).count() == 3

    def test_manueller_loeschpfad_blockt_unter_aktivem_hold(self, facility, admin_user, staff_user, event_with_history):
        """M-9: ``soft_delete_event`` prueft heute UEBERHAUPT keinen Hold — es leert
        Payload und Anhaenge unmittelbar; nur die Retention-Query schliesst
        gehaltene Ereignisse aus."""
        from django.core.exceptions import ValidationError

        from core.models import LegalHold
        from core.services.events import soft_delete_event

        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event_with_history.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        with pytest.raises(ValidationError):
            soft_delete_event(event_with_history, staff_user)

        event_with_history.refresh_from_db()
        assert event_with_history.is_deleted is False
        assert event_with_history.data_json == {"dauer": 30, "notiz": "Zweitgespräch"}


@pytest.mark.django_db
class TestProofIsPruneExempt:
    """Paket 8 (M-4): der Nachweis ueberlebt die Routine-Frist."""

    def test_nachweis_ist_von_der_auditlog_frist_ausgenommen(self, facility, settings_obj, event_with_history):
        """Muster ``AUDIT_PRUNE_CHECKPOINT``: ein Nachweis, der vor den von ihm
        belegten Zeilen verschwindet, belegt nichts mehr — die redigierte
        Historienzeile bleibt dagegen (bis Stufe 2 greift) stehen."""
        from datetime import timedelta

        from core.models import AuditLog
        from core.retention.audit_pruning import prune_auditlog
        from core.retention.eventhistory import redact_event_history_rows

        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))
        redact_event_history_rows(facility, pks, category="test")
        proof = AuditLog.objects.get(action=AuditLog.Action.EVENT_HISTORY_REDACTED)

        # Alle Audit-Zeilen weit hinter den Cutoff altern.
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers(tables=("core_auditlog",)):
            AuditLog.objects.all().update(timestamp=timezone.now() - timedelta(days=3650))
        prune_auditlog(facility, settings_obj, timezone.now(), dry_run=False)

        assert AuditLog.objects.filter(pk=proof.pk).exists()


def _run_backfill():
    """Die echte Backfill-Funktion der Migration 0174 gegen die echten Modelle.

    Per ``importlib``, weil der Modulname mit einer Ziffer beginnt; der Stub
    bildet nur die historischen Modelle auf die echten ab. Geprueft wird die
    Funktion selbst, nicht eine nachgebaute Kopie, die auseinanderlaufen
    koennte (Muster ``test_tombstone_backfill_migration``).
    """
    import importlib

    from django.db import connection as db_connection

    from core.models import EventHistory as RealEventHistory

    module = importlib.import_module("core.migrations.0174_redact_deleted_event_history_backlog")

    class _Apps:
        @staticmethod
        def get_model(app_label, name):
            return {"EventHistory": RealEventHistory}[name]

    class _SchemaEditor:
        connection = db_connection

    module.redact_deleted_event_history_backlog(_Apps(), _SchemaEditor())


class _BacklogMigrationClassState:
    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def _backlog_database(cls, django_db_setup, django_db_blocker):
        from django.core.management import call_command

        with django_db_blocker.unblock():
            call_command("flush", interactive=False, verbosity=0)
            yield
            call_command("flush", interactive=False, verbosity=0)

    @pytest.fixture(scope="class")
    @classmethod
    def organization(cls, _backlog_database):
        from core.models import Organization

        return Organization.objects.create(name="Testorg")

    @pytest.fixture(scope="class")
    @classmethod
    def facility(cls, organization):
        from core.models import Facility

        return Facility.objects.create(organization=organization, name="Teststelle")

    @pytest.fixture(scope="class")
    @classmethod
    def staff_user(cls, facility):
        from core.models import User

        return User.objects.create_user(username="teststaff", role=User.Role.STAFF, facility=facility, is_staff=True)

    @pytest.fixture(scope="class")
    @classmethod
    def admin_user(cls, facility):
        from core.models import User

        return User.objects.create_user(
            username="testadmin",
            role=User.Role.FACILITY_ADMIN,
            facility=facility,
            is_staff=True,
            can_confirm_deletion=True,
        )

    @pytest.fixture(scope="class")
    @classmethod
    def client_identified(cls, facility, staff_user):
        from core.models import Client

        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Test-ID-01",
            created_by=staff_user,
        )

    @pytest.fixture(scope="class")
    @classmethod
    def doc_type_contact(cls, facility):
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt",
            report_key="contact",
            tally_enabled=True,
        )
        duration = FieldTemplate.objects.create(
            facility=facility,
            name="Dauer",
            field_type=FieldTemplate.FieldType.NUMBER,
            report_key="dauer_minuten",
        )
        note = FieldTemplate.objects.create(
            facility=facility,
            name="Notiz",
            field_type=FieldTemplate.FieldType.TEXTAREA,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=duration, sort_order=0)
        DocumentTypeField.objects.create(document_type=doc_type, field_template=note, sort_order=1)
        return doc_type


@pytest.mark.django_db
class TestBacklogMigration(_BacklogMigrationClassState):
    """Paket 9 (Migrationsskizze Schritt 7): der D2-Bestand.

    Diese Tests rufen die Arbeitsfunktion direkt auf und teilen ihren
    unveraenderten Basisstand je Klasse. Den echten, ungekapstelten
    Migrationsuebergang prueft separat ``TestBacklogMigrationExecutorRun``.
    """

    def test_bestand_geloeschter_ereignisse_wird_nachredigiert(self, facility, staff_user, doc_type_contact):
        from core.models import Event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Zweitgespräch"})
        # Der Altzustand: Ereignis geloescht, Historie unangetastet.
        Event.objects.filter(pk=event.pk).update(is_deleted=True, data_json={})

        _run_backfill()

        rows = EventHistory.objects.filter(event=event)
        assert rows.count() == 2
        for row in rows:
            assert row.redacted_at is not None
            for payload in (row.data_before, row.data_after):
                assert payload is None or payload.get("_redacted") is True

    def test_lebende_ereignisse_bleiben_unberuehrt(self, facility, staff_user, doc_type_contact):
        """Der D3-Bestand gehoert NICHT in die Migration — unbestimmte Laufzeit im
        Deploy-Fenster; ihn erledigt der erste Cron-Lauf."""
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )

        _run_backfill()

        row = EventHistory.objects.get(event=event)
        assert row.redacted_at is None
        assert row.data_after == {"dauer": 15}

    def test_zweiter_lauf_aendert_nichts(self, facility, staff_user, doc_type_contact):
        from core.models import Event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        _run_backfill()
        stamps = dict(EventHistory.objects.values_list("pk", "redacted_at"))

        _run_backfill()

        assert dict(EventHistory.objects.values_list("pk", "redacted_at")) == stamps

    def test_trigger_bleiben_danach_enable_always(self, facility, staff_user, doc_type_contact):
        """Das pauschale ``ENABLE TRIGGER`` aus 0074 wuerde den seit 0107
        gehaerteten Zustand still auf Origin zurueckstufen und den
        ``replica``-Vektor aus Refs #1026 wieder oeffnen."""
        from core.models import Event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
        )
        Event.objects.filter(pk=event.pk).update(is_deleted=True)

        _run_backfill()

        with connection.cursor() as cur:
            cur.execute(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'core_eventhistory'::regclass AND NOT tgisinternal"
            )
            states = dict(cur.fetchall())
        assert states["eventhistory_no_update"] == "A"
        assert states["eventhistory_no_delete"] == "A"


@pytest.mark.django_db
class TestGuards:
    """Paket 12 (ADR-050 Migrationsskizze Schritt 9): die Regressionsnetze."""

    def test_genau_ein_redaktions_schreiber(self):
        """Beide Ausloeser rufen denselben Service. Eine zweite Implementierung
        waere genau die Drift, die ADR-044 §3 benennt — der Guard sucht deshalb
        jede Stelle, die ``redacted_at`` schreibt.

        Refs #2085: Die Suche ist **whitespace-tolerant**. Vorher fand sie nur
        den exakten String ``redacted_at=``; ein Schreiber mit Leerzeichen
        (``redacted_at = ...``) rutschte lautlos durch — im Cross-Review an
        einer real gebauten Migration nachgewiesen. Ein Kommentar an der
        Fundstelle schuetzt davor nicht: schon ein Formatlauf oder eine
        Handaenderung kippt die Schreibweise in beide Richtungen. Der Guard
        darf sein eigenes Versprechen nicht von einer Leerzeichenkonvention
        abhaengig machen.
        """
        import re
        from pathlib import Path

        core = Path("src/core")
        allowed = {
            "src/core/retention/eventhistory.py",
            "src/core/migrations/0174_redact_deleted_event_history_backlog.py",
            # Die Felddeklaration selbst — kein Schreiber, aber sie traegt den
            # Namen. Ausdruecklich gelistet statt aus dem Muster ausgenommen:
            # eine Ausnahme im Regex verstecken hiesse, die naechste
            # Deklarations-aehnliche Fundstelle ebenfalls zu uebersehen.
            "src/core/models/event_history.py",
        }
        muster = re.compile(r"redacted_at\s*=")
        writers = sorted(
            path.as_posix() for path in core.rglob("*.py") if muster.search(path.read_text(encoding="utf-8"))
        )
        assert set(writers) <= allowed, writers

    def test_redigierte_zeile_loest_in_der_key_rotation_kein_update_aus(self, facility, staff_user, doc_type_crisis):
        """D-Nebeneffekt (§6 der ADR): ``_reencrypt_data_json`` liefert ohne
        verschluesselte Werte ``changed = False`` — die Zeile faellt aus der
        Schleife und loest weder UPDATE noch Trigger-Bypass aus. Genau daran
        haengt, dass die Rotationskosten proportional zum unredigierten Fenster
        statt zur Tabellengroesse werden."""
        from core.retention.eventhistory import redact_event_history_rows
        from core.services.file_vault.rotation import _reencrypt_event_history

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_crisis,
            occurred_at=timezone.now(),
            data_json={"notiz-krise": "vertraulicher Wortlaut"},
        )
        before_rotation = _reencrypt_event_history(dry_run=True)
        pks = list(EventHistory.objects.filter(event=event).values_list("pk", flat=True))
        redact_event_history_rows(facility, pks, category="test")

        after_rotation = _reencrypt_event_history(dry_run=True)

        assert before_rotation > 0, "Vorbedingung: die Fassungen tragen Ciphertext"
        assert after_rotation == 0

    def test_nachweis_liegt_in_derselben_transaktion_wie_seine_charge(self, facility, event_with_history, monkeypatch):
        """D5.3: Ein Abbruch beim Nachweis darf keine bereits veraenderten
        Append-only-Zeilen ohne Beleg zuruecklassen."""
        from core.retention import eventhistory as module

        def _boom(*args, **kwargs):
            raise RuntimeError("Nachweis scheitert")

        monkeypatch.setattr(module, "audit_retention_decision", _boom)
        pks = list(EventHistory.objects.filter(event=event_with_history).values_list("pk", flat=True))

        with pytest.raises(RuntimeError, match="Nachweis"):
            module.redact_event_history_rows(facility, pks, category="test")

        assert EventHistory.objects.filter(pk__in=pks, redacted_at__isnull=False).count() == 0
        for row in EventHistory.objects.filter(pk__in=pks):
            assert row.data_after is None or not row.data_after.get("_redacted")

    def test_trigger_bleiben_nach_dem_lauf_enable_always(self, facility, settings_obj, staff_user, event_with_history):
        """Der Sweep laeuft naechtlich — ein einziger Pfad, der statt des exakten
        Vorzustands ein pauschales ``ENABLE TRIGGER`` schriebe, degradierte den
        gehaerteten Zustand still auf Origin (Refs #1026)."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        with connection.cursor() as cur:
            cur.execute(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid IN ('core_eventhistory'::regclass, 'core_auditlog'::regclass) "
                "AND NOT tgisinternal"
            )
            states = dict(cur.fetchall())
        assert states["eventhistory_no_update"] == "A"
        assert states["eventhistory_no_delete"] == "A"
        assert states["auditlog_immutable"] == "A"


class _FakeDeadlockCause(Exception):
    """Treiber-Ausnahme, wie psycopg sie als ``__cause__`` unter den Django-Wrapper legt."""

    sqlstate = "40P01"


class _FakeSerializationCause(Exception):
    sqlstate = "40001"


def _deadlock_error():
    from django.db import OperationalError

    error = OperationalError("deadlock detected")
    error.__cause__ = _FakeDeadlockCause()
    return error


@pytest.mark.django_db
class TestDeadlockRetry:
    """Fix-Runde 1, Finding 7 (ADR-050 D5.5): Erkennung ueber SQLSTATE, nicht ueber Text."""

    def test_deadlock_wird_erkannt_und_mit_backoff_wiederholt(self, monkeypatch, facility):
        from core.retention import eventhistory as module

        attempts: list[int] = []
        sleeps: list[float] = []

        def _always_deadlock(*_args, **_kwargs):
            attempts.append(1)
            raise _deadlock_error()

        monkeypatch.setattr(module, "_run_batch", _always_deadlock)
        monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

        with pytest.raises(Exception, match="deadlock"):
            module._run_batch_with_retry(facility, [], category="test", cutoff=None, delete=False)

        assert len(attempts) == module.MAX_ATTEMPTS
        assert len(sleeps) == module.MAX_ATTEMPTS - 1
        assert sleeps == sorted(sleeps) and sleeps[0] > 0, sleeps

    def test_zweiter_versuch_gewinnt(self, monkeypatch, facility):
        """Die Charge ist idempotent (D1.4) — ein Wiederholen kann nichts doppelt redigieren."""
        from core.retention import eventhistory as module

        calls: list[int] = []
        sleeps: list[float] = []

        def _deadlock_once(*_args, **_kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _deadlock_error()
            return module.SweepResult(processed=7)

        monkeypatch.setattr(module, "_run_batch", _deadlock_once)
        monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

        result = module._run_batch_with_retry(facility, [], category="test", cutoff=None, delete=False)

        assert result.processed == 7
        assert len(calls) == 2
        assert len(sleeps) == 1

    def test_andere_operational_errors_werden_durchgereicht(self, monkeypatch, facility):
        """Kein Deadlock, sondern ein Befund — er darf nicht in einer Retry-Schleife verschwinden."""
        from django.db import OperationalError

        from core.retention import eventhistory as module

        sleeps: list[float] = []

        def _serialization_failure(*_args, **_kwargs):
            error = OperationalError("could not serialize access")
            error.__cause__ = _FakeSerializationCause()
            raise error

        monkeypatch.setattr(module, "_run_batch", _serialization_failure)
        monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))

        with pytest.raises(OperationalError, match="serialize"):
            module._run_batch_with_retry(facility, [], category="test", cutoff=None, delete=False)

        assert sleeps == []

    def test_erkennung_haengt_nicht_am_englischen_meldungstext(self, monkeypatch, facility):
        """Der Meldungstext ist lokalisierbar; SQLSTATE 40P01 ist es nicht."""
        from django.db import OperationalError

        from core.retention import eventhistory as module

        calls: list[int] = []

        def _localized_deadlock(*_args, **_kwargs):
            calls.append(1)
            error = OperationalError("Verklemmung erkannt")
            error.__cause__ = _FakeDeadlockCause()
            raise error

        monkeypatch.setattr(module, "_run_batch", _localized_deadlock)
        monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

        with pytest.raises(OperationalError):
            module._run_batch_with_retry(facility, [], category="test", cutoff=None, delete=False)

        assert len(calls) == module.MAX_ATTEMPTS


@pytest.mark.django_db
class TestDryRunHonoursHolds:
    """Fix-Runde 1, Finding 6: der Dry-run muss dasselbe sagen wie der echte Lauf."""

    def test_gehaltenes_ereignis_erscheint_als_zurueckgestellt_nicht_als_zu_redigieren(
        self, facility, settings_obj, admin_user, staff_user, event_with_history
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event_with_history.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        dry = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)
        real = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert dry["redacted"] == 0, "gehaltene Zeilen sind nicht 'would redact'"
        assert dry["deferred"] == 1
        assert (dry["redacted"], dry["deferred"]) == (real["redacted"], real["deferred"])

    def test_dry_run_stimmt_ohne_hold_mit_dem_echten_lauf_ueberein(
        self, facility, settings_obj, staff_user, event_with_history
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)

        dry = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)
        real = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert dry["redacted"] == real["redacted"] == 3
        assert dry["deferred"] == 0

    def test_kommando_weist_zurueckstellungen_auch_im_dry_run_aus(
        self, facility, settings_obj, admin_user, staff_user, event_with_history
    ):
        from io import StringIO

        from django.core.management import call_command

        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event_with_history.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )
        out = StringIO()

        call_command("enforce_retention", "--dry-run", stdout=out)

        output = out.getvalue()
        assert "Would redact 0 event-history row(s)" in output
        assert "Would defer 1 event(s)" in output


def _age_redaction(event, *, days):
    """Datiert ``redacted_at`` zurueck, damit Stufe 2 greift."""
    from core.services.system import bypass_replication_triggers

    with bypass_replication_triggers(tables=("core_eventhistory",)):
        EventHistory.objects.filter(event=event, redacted_at__isnull=False).update(
            redacted_at=timezone.now() - timedelta(days=days)
        )


@pytest.mark.django_db
class TestLatenessSurvivesStageTwo:
    """Fix-Runde 1, Finding 4: „frühere Fassungen nicht mehr vollständig verfügbar".

    Das Merkmal hing an einer noch VORHANDENEN Zeile mit ``redacted_at``. Genau
    die loescht Stufe 2 — danach meldete ein neuer Hold faelschlich
    ``history_already_redacted=false``, obwohl die Inhalte unwiederbringlich fort
    sind. Das ist der von D2 ausdruecklich verbotene stille Hold.
    """

    def _hold(self, facility, event, admin_user):
        from core.retention.legal_holds import create_hold_for_target

        return create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

    def test_unberuehrtes_ereignis_ist_nicht_verspaetet(self, facility, admin_user, event_with_history):
        """Gegenprobe gegen Falsch-Positive: ohne jede Tilgung ist der Hold puenktlich."""
        hold = self._hold(facility, event_with_history, admin_user)

        assert hold.history_already_redacted is False

    def test_nach_stufe_eins_verspaetet(self, facility, settings_obj, staff_user, admin_user, event_with_history):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True

    def test_nach_stufe_zwei_weiterhin_verspaetet(
        self, facility, settings_obj, staff_user, admin_user, event_with_history
    ):
        """Der Kern des Befunds: Stufe 2 loescht genau die Zeilen, an denen das
        Merkmal bisher hing."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)
        _age_redaction(event_with_history, days=1000)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert not EventHistory.objects.filter(event=event_with_history).exists(), "Vorbedingung: Stufe 2 hat geraeumt"
        hold = self._hold(facility, event_with_history, admin_user)

        assert hold.history_already_redacted is True

    def test_lebendes_ereignis_nach_stufe_zwei_ist_verspaetet(
        self, facility, settings_obj, admin_user, event_with_history
    ):
        """Auch ohne Loeschung des Ereignisses: die aelteren Fassungen sind fort,
        nur der Schutzrand (D4) steht noch. Die CREATE-Fassung fehlt — daran ist
        die Luecke erkennbar."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        _age_rows(event_with_history, days=1000)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)
        _age_redaction(event_with_history, days=1000)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        remaining = EventHistory.objects.filter(event=event_with_history)
        assert remaining.count() == 1, "Vorbedingung: nur die juengste Fassung steht noch"
        assert not remaining.filter(action=EventHistory.Action.CREATE).exists()

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True


@pytest.mark.django_db
class TestManualDeleteLockOrder:
    """Fix-Runde 1, Finding 3: M-9 war gegenueber einer konkurrierenden Hold-Anlage TOCTOU-anfaellig."""

    def test_event_zeile_wird_vor_der_hold_lesung_gesperrt(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """``assert_processing_allowed(lock=True)`` sperrt nur die Client-Zeile; die
        Event-Sperre entstand erst beim spaeteren ``event.save()``. Zwischen
        Hold-Lesung und Sperre konnte eine Hold-Transaktion dieselbe Event-Zeile
        sperren, den Hold committen und vom Loeschvorgang ueberholt werden.

        Die Event-Sperre ist genau der D2-Koordinationspunkt, den die Hold-Anlage
        ebenfalls nimmt — damit ist das Rennen definiert entschieden.
        """
        from django.db import connection as db_connection
        from django.test.utils import CaptureQueriesContext

        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        with CaptureQueriesContext(db_connection) as captured:
            soft_delete_event(event, staff_user)

        statements = [q["sql"] for q in captured.captured_queries]
        client_lock = next(
            i for i, sql in enumerate(statements) if "core_client" in sql and "FOR UPDATE" in sql.upper()
        )
        event_lock = next(i for i, sql in enumerate(statements) if "core_event" in sql and "FOR UPDATE" in sql.upper())
        hold_read = next(i for i, sql in enumerate(statements) if "core_legalhold" in sql)

        # Kanonische Ordnung: Client -> Event -> Hold-Lesung.
        assert client_lock < event_lock < hold_read


@pytest.mark.django_db
class TestClientHoldsAreHard:
    """Fix-Runde 1, Finding 5: der Personen-Hold bekommt denselben Koordinationspunkt.

    Bisher prueft der Sweep Client-Holds zwar, aber ohne gemeinsame Sperre: eine
    Client-Hold-Transaktion konnte nach ``_held_event_ids`` committen, waehrend
    der Sweep anschliessend redigierte. Zusage („ein Hold auf Ereignis ODER
    Person haelt beide Stufen auf") und best-effort-Mechanik fielen auseinander.
    """

    def test_hold_anlage_sperrt_die_client_zeile_vor_dem_schreiben(self, facility, admin_user, client_identified):
        from django.db import connection as db_connection
        from django.test.utils import CaptureQueriesContext

        from core.retention.legal_holds import create_hold_for_target

        with CaptureQueriesContext(db_connection) as captured:
            create_hold_for_target(
                facility,
                target_type="Client",
                target_id=client_identified.pk,
                user=admin_user,
                reason="Ermittlungsverfahren",
            )

        statements = [q["sql"] for q in captured.captured_queries]
        lock_at = next(i for i, sql in enumerate(statements) if "core_client" in sql and "FOR UPDATE" in sql.upper())
        insert_at = next(i for i, sql in enumerate(statements) if "INSERT INTO" in sql.upper() and "legalhold" in sql)
        assert lock_at < insert_at

    def test_verspaeteter_client_hold_traegt_dasselbe_merkmal(
        self, facility, settings_obj, admin_user, staff_user, client_identified, doc_type_contact
    ):
        from core.models import AuditLog
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        soft_delete_event(event, staff_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        hold = create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        assert hold.history_already_redacted is True
        entry = AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).latest("timestamp")
        assert entry.detail["history_already_redacted"] is True

    def test_puenktlicher_client_hold_traegt_das_merkmal_nicht(
        self, facility, admin_user, staff_user, client_identified, doc_type_contact
    ):
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import create_event

        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        hold = create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        assert hold.history_already_redacted is False

    def test_sweep_sperrt_client_vor_event(
        self, facility, settings_obj, staff_user, client_identified, doc_type_contact
    ):
        """Kanonische Ordnung Client -> Event, konsistent zur L1-Regel und zum
        manuellen Loeschpfad."""
        from django.db import connection as db_connection
        from django.test.utils import CaptureQueriesContext

        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        soft_delete_event(event, staff_user)

        with CaptureQueriesContext(db_connection) as captured:
            enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        statements = [q["sql"] for q in captured.captured_queries]
        client_lock = next(
            i for i, sql in enumerate(statements) if "core_client" in sql and "FOR UPDATE" in sql.upper()
        )
        event_lock = next(i for i, sql in enumerate(statements) if "core_event" in sql and "FOR UPDATE" in sql.upper())
        hold_read = next(i for i, sql in enumerate(statements) if "core_legalhold" in sql)
        assert client_lock < event_lock < hold_read

    def test_client_hold_stellt_die_historie_zurueck(
        self, facility, settings_obj, admin_user, staff_user, client_identified, doc_type_contact
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        soft_delete_event(event, staff_user)
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert result["deferred"] == 1
        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()


@pytest.mark.django_db
class TestCaseHoldsAreHardToo:
    """Refs #2197 (b): der Sweep bildet die Hold-Menge auch aus FALL-Holds.

    ``lock_and_partition_events`` las bis hierher nur Ereignis- und
    Personen-Holds (``held_events``/``held_clients``). Der Fall-Hold gilt seit
    Refs #2122 im Aufbewahrungslauf — dort stellt ``_lock_event_and_recheck_hold``
    das Ereignis eines gehaltenen Falls zurueck. Der Historien-Sweep redigierte
    dieselben Ereignisse trotzdem: der Rahmen blieb stehen, sein Inhalt nicht
    (Spoliation, Muster Refs #1066).

    Der Koordinationspunkt ist derselbe wie beim Personen-Hold: ``core_client``.
    ``_lock_case_and_check_history`` sperrt beim Anlegen eines Fall-Holds zuerst
    die Personenzeile — genau die, die dieser Sweep als erste Stufe nimmt.
    """

    def _deleted_event(self, facility, staff_user, doc_type_contact, *, client, case=None):
        from core.services.events import soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client,
            case=case,
        )
        soft_delete_event(event, staff_user)
        return event

    def test_ereignis_eines_gehaltenen_falls_wird_zurueckgestellt(
        self, facility, settings_obj, staff_user, admin_user, client_identified, doc_type_contact, case_open
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.retention.legal_holds import create_hold_for_target

        event = self._deleted_event(facility, staff_user, doc_type_contact, client=client_identified, case=case_open)
        create_hold_for_target(
            facility,
            target_type="Case",
            target_id=case_open.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert result["deferred"] == 1
        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()

    def test_ereignis_eines_ungehaltenen_falls_wird_weiterhin_redigiert(
        self, facility, settings_obj, staff_user, client_identified, doc_type_contact, case_open
    ):
        """Gegenprobe: der neue Zweig darf nur das Gehaltene zurueckstellen."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        event = self._deleted_event(facility, staff_user, doc_type_contact, client=client_identified, case=case_open)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["deferred"] == 0
        assert result["redacted"] == 2
        assert EventHistory.objects.filter(event=event, redacted_at__isnull=False).count() == 2

    def test_zwischenzeitlich_in_einen_gehaltenen_fall_verschobenes_ereignis_wird_gehalten(
        self, facility, settings_obj, staff_user, admin_user, client_identified, doc_type_contact, case_open
    ):
        """Die Fall-Zugehoerigkeit wird NACH der Sperre gelesen, nicht aus dem Snapshot.

        Ein Umhaengen des Ereignisses in einen anderen Fall DERSELBEN Person
        laesst den Personenbezug unveraendert — die Revalidierung aus Finding C
        greift also nicht, das Ereignis bleibt in der Charge. Wuerde der Sweep
        den Fall aus dem Snapshot von vorhin nehmen, saehe er den Hold des
        neuen Falls nie. Die Personenzeile ist waehrenddessen gesperrt; genau
        sie nimmt ``_lock_case_and_check_history`` als erste Stufe, der Hold
        kann also nicht danebenher committen.
        """
        from core.models import Case, Event
        from core.retention import eventhistory as module
        from core.retention.legal_holds import create_hold_for_target

        event = self._deleted_event(facility, staff_user, doc_type_contact, client=client_identified, case=case_open)
        held_case = Case.objects.create(
            facility=facility, client=client_identified, title="Beweismittel-Fall", created_by=staff_user
        )
        create_hold_for_target(
            facility,
            target_type="Case",
            target_id=held_case.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )
        original_lock = module._lock_rows

        def _move_event_into_the_held_case(model, ids):
            locked = original_lock(model, ids)
            if model is Event and locked:
                Event.objects.filter(pk__in=locked).update(case=held_case)
            return locked

        module._lock_rows = _move_event_into_the_held_case
        try:
            result = module.enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)
        finally:
            module._lock_rows = original_lock

        assert result["redacted"] == 0
        assert result["deferred"] == 1
        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()


@pytest.mark.django_db
class TestBacklogMigrationHonoursHolds(_BacklogMigrationClassState):
    """Fix-Runde 1, Finding 1 (Critical): die Bestandsmigration war hold-blind.

    Ein vor dem Upgrade geloeschtes, gehaltenes Ereignis haette beim Deploy
    irreversibel seine Historieninhalte verloren — und zwar ausgerechnet beim
    Altbestand, fuer den D2 „Hold zuerst -> vollstaendig erhalten und
    zurueckgestellt" genauso gilt wie im laufenden Betrieb.
    """

    def _deleted_event(self, facility, staff_user, doc_type_contact, client=None):
        from core.models import Event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
            client=client,
        )
        update_event(event, staff_user, {"dauer": 30, "notiz": "Zweitgespräch"})
        # Altzustand: Ereignis geloescht, Historie unangetastet.
        Event.objects.filter(pk=event.pk).update(is_deleted=True, data_json={})
        return event

    def test_ereignis_unter_event_hold_wird_zurueckgestellt(self, facility, staff_user, admin_user, doc_type_contact):
        from core.models import LegalHold

        event = self._deleted_event(facility, staff_user, doc_type_contact)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        _run_backfill()

        rows = EventHistory.objects.filter(event=event)
        assert rows.filter(redacted_at__isnull=False).count() == 0
        assert rows.filter(data_after={"dauer": 30, "notiz": "Zweitgespräch"}).exists(), "Inhalte erhalten"

    def test_ereignis_unter_client_hold_wird_zurueckgestellt(
        self, facility, staff_user, admin_user, doc_type_contact, client_identified
    ):
        from core.models import LegalHold

        event = self._deleted_event(facility, staff_user, doc_type_contact, client=client_identified)
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=client_identified.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        _run_backfill()

        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()

    def test_ereignis_unter_case_hold_wird_zurueckgestellt(
        self, facility, staff_user, admin_user, doc_type_contact, client_identified
    ):
        """Refs #2197 (b): derselbe Baustein, deshalb derselbe Schutz im Altbestand.

        Die Migration importiert ``lock_and_partition_events`` (ADR-044 §3:
        eine Fassung, keine Drift) — der Fall-Zweig kommt hier ohne eine Zeile
        Migrationscode an. Genau das haelt dieser Test fest.
        """
        from core.models import Case, Event, LegalHold

        event = self._deleted_event(facility, staff_user, doc_type_contact, client=client_identified)
        case = Case.objects.create(
            facility=facility, client=client_identified, title="Beweismittel-Fall", created_by=staff_user
        )
        Event.objects.filter(pk=event.pk).update(case=case)
        LegalHold.objects.create(
            facility=facility,
            target_type="Case",
            target_id=case.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        _run_backfill()

        rows = EventHistory.objects.filter(event=event)
        assert rows.filter(redacted_at__isnull=False).count() == 0
        assert rows.filter(data_after={"dauer": 30, "notiz": "Zweitgespräch"}).exists(), "Inhalte erhalten"

    def test_ungehaltene_ereignisse_werden_weiterhin_redigiert(
        self, facility, staff_user, admin_user, doc_type_contact
    ):
        """Der Hold darf nur das Gehaltene zurueckstellen, nicht den ganzen Lauf."""
        from core.models import LegalHold

        held = self._deleted_event(facility, staff_user, doc_type_contact)
        free = self._deleted_event(facility, staff_user, doc_type_contact)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=held.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        _run_backfill()

        assert not EventHistory.objects.filter(event=held, redacted_at__isnull=False).exists()
        assert EventHistory.objects.filter(event=free, redacted_at__isnull=True).count() == 0

    def test_zurueckstellung_wird_ausgewiesen(self, facility, staff_user, admin_user, doc_type_contact, capsys):
        from core.models import LegalHold

        event = self._deleted_event(facility, staff_user, doc_type_contact)
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        _run_backfill()

        output = capsys.readouterr().out
        assert "0174_redact_deleted_event_history_backlog" in output
        # Eindeutig: die Zahl steht unmittelbar vor „Ereignis(se)". Ein blosses
        # ``"1" in output`` waere schon durch „0174" gruen geworden.
        assert re.search(r"0 Historien-Fassung\(en\) redigiert, 1 Ereignis\(se\)", output), output

    def test_rls_guard_steht_am_einstieg(self, monkeypatch):
        """Finding 2: ohne den Guard laeuft die Migration unter der App-Rolle still
        leer durch — die PK-Abfrage liefert null Zeilen und die Migration meldet
        Erfolg, ohne den Trigger-Bypass je zu erreichen."""
        import importlib

        module = importlib.import_module("core.migrations.0174_redact_deleted_event_history_backlog")
        calls: list[str] = []

        monkeypatch.setattr(module, "assert_migration_role_bypasses_rls", lambda editor: calls.append("guard"))
        monkeypatch.setattr(module, "redact_deleted_event_history_backlog", lambda *a, **kw: calls.append("work"))

        module.run_backfill(object(), object())

        assert calls == ["guard", "work"], "Der Guard MUSS die erste Zeile sein"


@pytest.mark.django_db
class TestLatenessDetectsLegacyMarkers:
    """Fix-Runde 2, Finding B: Alt-Redaktionen tragen den Marker, aber kein ``redacted_at``.

    ``_redact_event_history`` (Art. 17 / K-Anon) ersetzt beide Payloads durch
    ``{"_redacted": True, "anonymized": True}``, stempelt aber nichts — der
    Zeitstempel kam erst mit ADR-050. Bleibt die CREATE-Zeile dabei stehen,
    liefen alle drei bisherigen Pruefungen ins Leere und ein danach angelegter
    Hold meldete faelschlich „puenktlich".
    """

    def _hold(self, facility, event, admin_user):
        from core.retention.legal_holds import create_hold_for_target

        return create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

    def test_art17_marker_ohne_zeitstempel_gilt_als_redigiert(self, facility, admin_user, event_with_history):
        """Der Marker der Art.-17-Kaskade — ``anonymized``, ohne ``fields``."""
        from core.services.system import bypass_replication_triggers

        marker = {"_redacted": True, "anonymized": True}
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(event=event_with_history).update(data_before=marker, data_after=marker)
        assert not EventHistory.objects.filter(event=event_with_history, redacted_at__isnull=False).exists()

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True

    def test_slug_marker_ohne_zeitstempel_gilt_als_redigiert(self, facility, admin_user, event_with_history):
        """Die andere Markerform — ``fields``-Slugliste, etwa aus einer Alt-Redaktion."""
        from core.services.system import bypass_replication_triggers

        row = EventHistory.objects.get(event=event_with_history, action=EventHistory.Action.CREATE)
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(pk=row.pk).update(data_after={"_redacted": True, "fields": ["dauer", "notiz"]})

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True

    def test_normale_loeschzeile_ist_keine_redaktion(self, facility, admin_user, staff_user, event_with_history):
        """Abgrenzung — die Gegenprobe, die diesen Fix nicht ueberschiessen laesst:
        ``soft_delete_event`` schreibt seit Refs #714 IMMER eine DELETE-Zeile mit
        ``{"_redacted": True, "fields": [...]}`` in ``data_before``. Das ist der
        normale Loeschmarker und bedeutet gerade NICHT, dass fruehere Fassungen
        getilgt waeren — die CREATE- und UPDATE-Zeilen sind noch vollstaendig.
        Ein sofort danach gesetzter Hold ist puenktlich."""
        from core.services.events import soft_delete_event

        soft_delete_event(event_with_history, staff_user)

        assert EventHistory.objects.filter(event=event_with_history, action=EventHistory.Action.DELETE).exists()
        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is False

    def test_art17_marker_auf_der_loeschzeile_gilt_als_redigiert(
        self, facility, admin_user, staff_user, event_with_history
    ):
        """Gegenstueck zur Abgrenzung: traegt die DELETE-Zeile den
        ``anonymized``-Marker, war es die Art.-17-Kaskade und nicht der
        Loeschpfad — dann sind Inhalte getilgt."""
        from core.services.events import soft_delete_event
        from core.services.system import bypass_replication_triggers

        soft_delete_event(event_with_history, staff_user)
        marker = {"_redacted": True, "anonymized": True}
        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(event=event_with_history).update(data_before=marker, data_after=None)

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True


@pytest.mark.django_db
class TestApprovedDeletionTakesClientLock:
    """Fix-Runde 2, Finding D: der genehmigte Vier-Augen-Weg umging die Client-Sperre.

    ``_deletion_workflow_write()`` laesst ``assert_processing_allowed`` sofort
    zurueckkehren (Art.-17 gilt waehrend einer Art.-18-Einschraenkung weiter) —
    und mit ihr entfiel die Client-Zeilensperre, die im manuellen Pfad als
    Nebenwirkung des Gates entstand. Der Koordinationspunkt gegen einen
    parallelen Personen-Hold fehlte damit ausgerechnet im geprueften Loeschweg.
    """

    def test_genehmigter_loeschweg_sperrt_client_vor_event_und_hold(
        self, facility, staff_user, admin_user, client_identified, doc_type_contact
    ):
        from django.db import connection as db_connection
        from django.test.utils import CaptureQueriesContext

        from core.services.events import create_event
        from core.services.events.deletion import approve_deletion, create_or_get_event_deletion_request

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        request, _created = create_or_get_event_deletion_request(event, staff_user, "Antrag der Person")
        admin_user.can_confirm_deletion = True
        admin_user.save(update_fields=["can_confirm_deletion"])

        with CaptureQueriesContext(db_connection) as captured:
            approve_deletion(request, admin_user)

        statements = [q["sql"] for q in captured.captured_queries]
        client_lock = next(
            i for i, sql in enumerate(statements) if "core_client" in sql and "FOR UPDATE" in sql.upper()
        )
        event_lock = next(i for i, sql in enumerate(statements) if "core_event" in sql and "FOR UPDATE" in sql.upper())
        hold_read = next(i for i, sql in enumerate(statements) if "core_legalhold" in sql)
        assert client_lock < event_lock < hold_read

    def test_genehmigter_loeschweg_respektiert_den_personen_hold(
        self, facility, staff_user, admin_user, client_identified, doc_type_contact
    ):
        """M-9 gilt auch hier: ein Personen-Hold blockt den gepruefen Weg genauso."""
        from django.core.exceptions import ValidationError

        from core.retention.legal_holds import create_hold_for_target
        from core.services.events import create_event
        from core.services.events.deletion import approve_deletion, create_or_get_event_deletion_request

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        request, _created = create_or_get_event_deletion_request(event, staff_user, "Antrag der Person")
        admin_user.can_confirm_deletion = True
        admin_user.save(update_fields=["can_confirm_deletion"])
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

        with pytest.raises(ValidationError):
            approve_deletion(request, admin_user)

        event.refresh_from_db()
        assert event.is_deleted is False


@pytest.mark.django_db
class TestSweepRevalidatesClientBinding:
    """Fix-Runde 2, Finding C: der Event->Client-Bezug wird nach der Sperre revalidiert.

    Die Zuordnung wurde vor saemtlichen Sperren gelesen. Ein Merge kann das
    Ereignis waehrend der Sperrwartezeit auf einen anderen, ungesperrten
    Klienten verschieben — dessen Hold der Sweep dann nie sieht. Dieselbe
    Revalidierungs-Disziplin wie im L1-Workitem-Bulk.
    """

    def test_zwischenzeitlich_verschobenes_ereignis_wird_zurueckgestellt(
        self, facility, settings_obj, staff_user, admin_user, client_identified, doc_type_contact, organization
    ):
        from core.models import Client, Event
        from core.retention import eventhistory as module
        from core.services.events import create_event, soft_delete_event

        other = Client.objects.create(facility=facility, pseudonym="Ziel-der-Zusammenfuehrung")
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        soft_delete_event(event, staff_user)

        original_lock = module._lock_rows

        def _move_event_while_waiting(model, ids):
            locked = original_lock(model, ids)
            if model is Event and locked:
                # Der Merge committet genau hier: das Ereignis haengt jetzt an
                # einem Klienten, dessen Zeile diese Charge nie gesperrt hat.
                Event.objects.filter(pk__in=locked).update(client=other)
            return locked

        module._lock_rows = _move_event_while_waiting
        try:
            result = module.enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)
        finally:
            module._lock_rows = original_lock

        assert result["redacted"] == 0, "ein verschobenes Ereignis darf nicht mit der alten Sperrmenge fallen"
        assert result["skipped"] == 1
        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()

    def test_unveraenderter_bezug_wird_normal_verarbeitet(
        self, facility, settings_obj, staff_user, client_identified, doc_type_contact
    ):
        """Gegenprobe: die Revalidierung darf den Normalfall nicht ausbremsen."""
        from core.retention.eventhistory import enforce_eventhistory_retention
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        soft_delete_event(event, staff_user)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["skipped"] == 0
        assert result["redacted"] == 2


@pytest.mark.django_db
class TestBacklogMigrationChecksHoldsPerBatch(_BacklogMigrationClassState):
    """Fix-Runde 2, Finding A (Critical): das Live-Deploy-Rennen der Migration.

    Migrationen laufen dokumentiert VOR dem Rolling-Restart, waehrend der
    Webprozess noch bedient (ops-runbook § 3). Ein einmaliger Hold-Snapshot mit
    anschliessender Chargen-Redaktion laesst deshalb genau das Fenster offen,
    das D2 im Betrieb schliesst: ein nach dem Snapshot angelegter Hold wird von
    einer spaeteren Charge ueberholt.
    """

    def _deleted_event(self, facility, staff_user, doc_type_contact):
        from core.models import Event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Erstgespräch"},
        )
        Event.objects.filter(pk=event.pk).update(is_deleted=True, data_json={})
        return event

    def test_hold_nach_abgeschlossener_erster_charge_wird_von_der_zweiten_gesehen(
        self, facility, staff_user, admin_user, doc_type_contact
    ):
        import importlib

        from core.models import LegalHold

        module = importlib.import_module("core.migrations.0174_redact_deleted_event_history_backlog")

        first = self._deleted_event(facility, staff_user, doc_type_contact)
        second = self._deleted_event(facility, staff_user, doc_type_contact)
        by_row = {
            pk: event_id
            for pk, event_id in EventHistory.objects.filter(event__in=[first, second]).values_list("pk", "event_id")
        }

        original_batch = module._redact_batch
        batches: list[list] = []
        held_event_id: list = []

        def _hold_arrives_after_first_batch(history_model, pks, now):  # noqa: N803 — Signatur der Migration
            batches.append(list(pks))
            result = original_batch(history_model, pks, now)
            if len(batches) == 1:
                # Die erste Charge ist COMMITTET. Erst jetzt ordnet jemand die
                # Sperre an — und zwar fuer das Ereignis, das noch aussteht.
                done = {by_row[pk] for pk in batches[0]}
                target = next(e.pk for e in (first, second) if e.pk not in done)
                held_event_id.append(target)
                LegalHold.objects.create(
                    facility=facility,
                    target_type="Event",
                    target_id=target,
                    reason="Ermittlungsverfahren",
                    created_by=admin_user,
                )
            return result

        module._redact_batch = _hold_arrives_after_first_batch
        module.BATCH_SIZE = 1
        try:
            _run_backfill()
        finally:
            module._redact_batch = original_batch
            module.BATCH_SIZE = 500

        assert len(batches) >= 2, "Vorbedingung: mehr als eine Charge"
        held = held_event_id[0]
        # Das gehaltene Ereignis liegt wirklich in einer SPAETEREN Charge — der
        # Beweis, dass die zweite Charge den Hold selbst gelesen hat und nicht
        # ein Vorab-Snapshot ihn zufaellig schon kannte.
        assert held not in {by_row[pk] for pk in batches[0]}
        assert any(held in {by_row[pk] for pk in batch} for batch in batches[1:])

        assert EventHistory.objects.filter(event_id=held, redacted_at__isnull=False).count() == 0, (
            "der nach der ersten Charge angelegte Hold muss von seiner Charge gesehen werden"
        )
        redacted_event = next(e.pk for e in (first, second) if e.pk != held)
        assert EventHistory.objects.filter(event_id=redacted_event, redacted_at__isnull=False).exists()


@pytest.mark.django_db(transaction=True)
class TestBacklogMigrationExecutorRun:
    """Fix-Runde 2, Finding E: ein Hold-Fall durch den ECHTEN Migrations-Uebergang.

    Die uebrigen Migrationstests rufen die Arbeitsfunktion direkt. Das prueft
    die Logik, aber nicht den Weg, den ``migrate`` tatsaechlich nimmt — inklusive
    ``atomic = False``, dem ``RunPython``-Einstieg mit RLS-Guard und den
    historischen Modellen aus dem Migrationszustand.
    """

    def test_migrationsuebergang_0173_nach_0174_respektiert_den_hold(
        self, facility, staff_user, admin_user, doc_type_contact
    ):
        from django.db import connection as db_connection
        from django.db.migrations.executor import MigrationExecutor

        from core.models import Event, LegalHold

        held_event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Gehalten"},
        )
        free_event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 20, "notiz": "Frei"},
        )
        Event.objects.filter(pk__in=[held_event.pk, free_event.pk]).update(is_deleted=True, data_json={})
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=held_event.pk,
            reason="Ermittlungsverfahren",
            created_by=admin_user,
        )

        target = ("core", "0174_redact_deleted_event_history_backlog")
        previous = ("core", "0173_auditlog_event_history_redacted")

        executor = MigrationExecutor(db_connection)
        try:
            executor.migrate([previous])  # 0174 zuruecknehmen (reverse_code = noop)
            executor.loader.build_graph()
            executor.migrate([target])  # und wirklich neu anwenden

            assert not EventHistory.objects.filter(event=held_event, redacted_at__isnull=False).exists(), (
                "der echte Migrationslauf muss den Hold genauso respektieren"
            )
            assert EventHistory.objects.filter(event=free_event, redacted_at__isnull=True).count() == 0
        finally:
            # ``transaction=True``: der Rueckweg auf 0173 nimmt auch alle spaeteren
            # Migrationen (0175 ff.) zurueck, und der Vorlauf endet bei 0174. Ohne
            # Rueckkehr zum Graph-Kopf fehlen den folgenden Tests desselben Workers
            # die neueren Spalten (Refs #2129 max_storage_mb, #2136 quick_exit_url).
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db
class TestMarkerRecognitionNeedsTheFlag:
    """Fix-Runde 3, Punkt 1: ``anonymized`` allein ist kein Redaktionsbeleg.

    ``anonymized`` ist **kein reservierter Feld-Slug** — ein Dokumentationstyp
    darf ein gewoehnliches Ja/Nein-Feld dieses Namens fuehren. Wurde es mit
    „ja" befuellt, sah die Erkennung darin faelschlich eine Art.-17-Redaktion
    und stempelte jeden spaeteren Hold als verspaetet.
    """

    def _hold(self, facility, event, admin_user):
        from core.retention.legal_holds import create_hold_for_target

        return create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=admin_user,
            reason="Ermittlungsverfahren",
        )

    def test_nutzdatenfeld_namens_anonymized_ist_keine_redaktion(self, facility, admin_user, event_with_history):
        """Die fehlende Gegenprobe: unveraenderte Nutzdaten mit diesem Feldnamen."""
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(event=event_with_history).update(
                data_before=None, data_after={"anonymized": True, "dauer": 30}
            )

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is False

    def test_anonymized_mit_redaktionsflag_bleibt_eine_redaktion(self, facility, admin_user, event_with_history):
        """Gegenrichtung — der echte Art.-17-Marker traegt BEIDE Schluessel."""
        from core.services.system import bypass_replication_triggers

        with bypass_replication_triggers(tables=("core_eventhistory",)):
            EventHistory.objects.filter(event=event_with_history).update(
                data_before=None, data_after={"_redacted": True, "anonymized": True}
            )

        assert self._hold(facility, event_with_history, admin_user).history_already_redacted is True


@pytest.mark.django_db
class TestDeleteRevalidatesClientBinding:
    """Fix-Runde 3, Punkt 2: der Loeschservice revalidiert den Client-Bezug.

    ``approve_deletion`` laedt das Ereignis ungesperrt; ``soft_delete_event``
    sperrte danach den Client aus dieser womoeglich veralteten Instanz. Gewinnt
    parallel ein Merge, haengt das Ereignis an einem anderen Klienten — dessen
    Hold niemand geprueft hat —, und das anschliessende ``event.save()`` schrieb
    den alten Bezug aus dem Speicher zurueck.
    """

    def test_paralleler_merge_bricht_die_loeschung_ab(self, facility, staff_user, client_identified, doc_type_contact):
        from django.core.exceptions import ValidationError

        from core.models import Client, Event
        from core.services.events import create_event, crud

        other = Client.objects.create(facility=facility, pseudonym="Ziel-der-Zusammenfuehrung")
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        original_lock = crud._lock_event_row

        def _merge_wins_the_lock(target):
            original_lock(target)
            # Der Merge committet genau hier: das Ereignis haengt jetzt an einem
            # Klienten, dessen Zeile dieser Loeschvorgang nie gesperrt hat.
            Event.objects.filter(pk=target.pk).update(client=other)

        crud._lock_event_row = _merge_wins_the_lock
        try:
            with pytest.raises(ValidationError):
                crud.soft_delete_event(event, staff_user)
        finally:
            crud._lock_event_row = original_lock

        event.refresh_from_db()
        assert event.is_deleted is False, "der Abbruch darf nichts halb geloescht zuruecklassen"
        # Der injizierte Merge liegt in DERSELBEN Transaktion wie der
        # Loeschvorgang und rollt mit dem Abbruch zurueck — ein echter
        # Nebenlaeufer waere eine eigene, committete Transaktion. Dass der
        # Loeschpfad den Bezug nicht zurueckschreiben KANN, pinnt deshalb der
        # ``update_fields``-Test unten statt dieser Zeile.
        assert other.pk is not None

    def test_save_kann_den_client_bezug_nicht_zurueckschreiben(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """``update_fields`` statt Voll-Save: eine Instanz aus einem aelteren
        Lesevorgang darf beim Loeschen nicht jedes ihrer Felder zurueckschreiben —
        allen voran nicht ``client_id``."""
        from core.models import Event
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )
        recorded: list = []
        original_save = Event.save

        def _record(self, *args, **kwargs):
            recorded.append(kwargs.get("update_fields"))
            return original_save(self, *args, **kwargs)

        Event.save = _record
        try:
            soft_delete_event(event, staff_user)
        finally:
            Event.save = original_save

        assert recorded, "Vorbedingung: der Loeschpfad speichert das Ereignis"
        fields = recorded[-1]
        assert fields is not None, "Voll-Save wuerde jedes Feld der Instanz zurueckschreiben"
        assert "client" not in fields and "client_id" not in fields
        # Refs #1092: ohne ``search_text`` bliebe der Klartext im Suchindex.
        assert {"is_deleted", "data_json", "search_text"} <= set(fields)

    def test_unveraenderter_bezug_loescht_normal(self, facility, staff_user, client_identified, doc_type_contact):
        """Gegenprobe: die Revalidierung darf den Normalfall nicht blockieren."""
        from core.services.events import create_event, soft_delete_event

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15},
            client=client_identified,
        )

        soft_delete_event(event, staff_user)

        event.refresh_from_db()
        assert event.is_deleted is True
        assert event.client_id == client_identified.pk


class TestRedactionMarkerInventory:
    """Fix-Runde 3, Punkt 4: Architektur-Guard fuer die Markerformen.

    Der „ein Redaktionsschreiber"-Guard sucht nach ``redacted_at=`` — einen
    dritten, UNGESTEMPELTEN Redaktionsschreiber (Marker im Payload, kein
    Zeitstempel) haette er nicht erkannt. Genau diese Form gibt es aber schon:
    die Art.-17-Kaskade. Fuer ein compliance-relevantes Signal ist das zu wenig.

    Gepinnt wird deshalb der **Produzenten-Kreis**: das Redaktionsflag darf
    ausserhalb der beiden Fabriken in ``history_diff`` in keinem Dict-Literal
    mehr auftauchen. Geprueft per AST, nicht per Textsuche — sonst schluegen
    Docstrings und Lese-Zugriffe (``payload.get("_redacted")``) mit an.
    """

    #: Wer das Flag in einem Dict-Literal fuehren darf, und warum.
    _MARKER_PRODUCERS = {
        "core/services/events/history_diff.py": (
            "die beiden Fabriken build_field_redaction_marker/build_anonymization_marker — "
            "bewusst neben dem Erkenner is_redaction_marker, damit Produzent und Leser nicht driften"
        ),
    }

    def test_marker_forms_have_exactly_one_producer(self):
        import ast
        from pathlib import Path

        from core.services.events.history_diff import REDACTION_FLAG

        offenders: dict[str, int] = {}
        for path in sorted(Path("src/core").rglob("*.py")):
            relative = path.relative_to("src").as_posix()
            # Migrationen sind eingefrorene Historie — 0074 traegt die Altform
            # und darf nicht nachtraeglich umgeschrieben werden.
            if relative.startswith("core/migrations/") or relative in self._MARKER_PRODUCERS:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literals = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.Dict)
                and any(isinstance(key, ast.Constant) and key.value == REDACTION_FLAG for key in node.keys)
            )
            if literals:
                offenders[relative] = literals

        assert not offenders, (
            f"Redaktionsmarker werden ausserhalb der Fabriken gebaut: {offenders}. "
            "Neue Produzenten muessen build_field_redaction_marker() oder "
            "build_anonymization_marker() nutzen — sonst entsteht eine Markerform, "
            "die die Verspaetungspruefung in core.retention.legal_holds nicht kennt."
        )

    def test_the_two_marker_forms_are_pinned(self):
        """Das Inventar selbst: genau zwei Formen, beide mit dem Flag."""
        from core.services.events.history_diff import (
            REDACTION_FLAG,
            build_anonymization_marker,
            build_field_redaction_marker,
            is_redaction_marker,
        )

        fields_form = build_field_redaction_marker(["notiz", "dauer"])
        anon_form = build_anonymization_marker()

        assert fields_form == {REDACTION_FLAG: True, "fields": ["dauer", "notiz"]}, "sortiert und stabil"
        assert anon_form == {REDACTION_FLAG: True, "anonymized": True}
        assert is_redaction_marker(fields_form) and is_redaction_marker(anon_form)
        # Und die Gegenprobe: Nutzdaten mit gleichnamigen Schluesseln sind kein Marker.
        assert not is_redaction_marker({"anonymized": True})
        assert not is_redaction_marker({"fields": ["dauer"]})


@pytest.mark.django_db
class TestAnonymisierteFallenInDenSweep:
    """Refs #2085: die vierte Kandidatenmenge — Historie anonymisierter Personen.

    Der Art.-17-Sofortpfad laeuft im Webprozess unter der App-Rolle und hat
    dort keinen Owner-DDL-Weg. Statt der App-Rolle ein neues Recht zu geben
    (verworfen, s. ``test_rls.py::TestSecurityDefinerInventory``), uebernimmt
    der owner-seitige Sweep die Historie — mit allen vier Eigenschaften, die
    einer Definer-Funktion strukturell fehlten: hold-aware, idempotent,
    gebatcht, mit Nachweis je Charge.

    Warum es eine EIGENE Menge braucht und die drei bestehenden nicht reichen:
    ``anonymize_client`` soft-loescht die Ereignisse **nicht** (sie bleiben als
    Statistik-Aggregat stehen, ``_redact_live_events``), also greift D2 nicht;
    und D3 greift erst nach Ablauf der Detailfrist (Vorgabe 24 Monate). Ohne
    diese Menge staende die Historie einer getilgten Person bis zu zwei Jahre
    im Klartext.
    """

    def _anonymisierte_person(self, facility, staff_user, doc_type_contact, admin_user, pseudonym):
        """Der Zustand, den der Request unter der App-Rolle hinterlaesst.

        Bewusst **ohne** ``anonymize_client_now``: der Testrunner verbindet als
        Owner, dort nimmt die Weiche in ``_redact_event_history`` den Direktweg
        und redigiert die Historie sofort — der Zustand „anonymisiert, Historie
        steht noch aus" entstuende gar nicht. Diese Klasse prueft die
        Sweep-Mechanik; die Kopplung Request -> Nachlauf unter einer echten
        Nicht-Owner-Rolle prueft
        ``test_client_anonymize_now.py::TestAnonymizeClientNowUnterDerAppRolle``.

        Der Stempel wird genau so gesetzt, wie ``_redact_client_identity`` ihn
        setzt (Refs #2013).
        """
        from core.models import Client

        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff_user,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Klartext-Notiz"},
            client=person,
        )
        assert EventHistory.objects.filter(event=event).exists()
        person.soft_delete(user=admin_user)
        person.anonymized_at = timezone.now()
        person.save(update_fields=["anonymized_at"])
        return person, event

    def test_historie_einer_anonymisierten_person_wird_erfasst(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention

        _person, event = self._anonymisierte_person(facility, staff_user, doc_type_contact, admin_user, "Art17-Sweep")
        # Vorbedingung: das Ereignis lebt weiter (Statistik-Aggregat), die
        # Detailfrist ist nicht abgelaufen — keine der drei Bestandsmengen
        # wuerde greifen.
        event.refresh_from_db()
        assert event.is_deleted is False

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] >= 1
        rows = list(EventHistory.objects.filter(event=event))
        assert rows
        for row in rows:
            assert row.redacted_at is not None
            for payload in (row.data_before, row.data_after):
                assert payload is None or payload.get("_redacted") is True

    def test_zweiter_lauf_faellt_auf_null_und_verschiebt_den_fristanker_nicht(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        """Idempotenz (ADR-050 D1.4) — genau die Eigenschaft, die dem
        Definer-Rumpf fehlte: er schrieb bei jedem Aufruf ``redacted_at``
        erneut und schob damit die M-7-Frist beliebig weit hinaus."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        _person, event = self._anonymisierte_person(facility, staff_user, doc_type_contact, admin_user, "Art17-Idem")
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)
        anker = sorted(EventHistory.objects.filter(event=event).values_list("redacted_at", flat=True))

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert sorted(EventHistory.objects.filter(event=event).values_list("redacted_at", flat=True)) == anker

    def test_legal_hold_stellt_die_historie_zurueck(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user, lead_user
    ):
        """Der Schutz, den die Definer-Funktion umging: ein Hold, der NACH der
        Anonymisierung eintrifft, haelt die Historie auf. ``anonymize_client_now``
        selbst weist eine Anonymisierung unter Hold weiterhin ab — dieser Test
        deckt das Fenster danach."""
        from core.models import LegalHold
        from core.retention.eventhistory import enforce_eventhistory_retention

        person, event = self._anonymisierte_person(facility, staff_user, doc_type_contact, admin_user, "Art17-Hold")
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=person.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        assert result["deferred"] == 1
        for row in EventHistory.objects.filter(event=event):
            assert row.redacted_at is None
            assert row.data_after == {"dauer": 15, "notiz": "Klartext-Notiz"}

    def test_nicht_anonymisierte_person_bleibt_unberuehrt(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        """Die Menge haengt am Stempel ``Client.anonymized_at``, nicht am
        Papierkorb: eine soft-geloeschte, aber noch nicht anonymisierte Person
        ist wiederherstellbar und behaelt ihre Historie."""
        from core.models import Client
        from core.retention.eventhistory import enforce_eventhistory_retention

        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Nur-Papierkorb",
            created_by=staff_user,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "bleibt"},
            client=person,
        )
        person.soft_delete(user=admin_user)

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert result["redacted"] == 0
        for row in EventHistory.objects.filter(event=event):
            assert row.redacted_at is None
            assert row.data_after == {"dauer": 15, "notiz": "bleibt"}

    def test_dry_run_weist_die_menge_aus(self, facility, settings_obj, staff_user, doc_type_contact, admin_user):
        """Der Dry-run darf nicht weniger versprechen als der echte Lauf —
        sonst plant die Betreiberin eine Tilgung nicht ein, die dann doch
        stattfindet."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        _person, event = self._anonymisierte_person(facility, staff_user, doc_type_contact, admin_user, "Art17-Dry")

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)

        assert result["redacted"] >= 1
        assert not EventHistory.objects.filter(event=event, redacted_at__isnull=False).exists()

    def test_nachweiszeile_nennt_die_eigene_kategorie(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        """Die Charge braucht einen unterscheidbaren Nachweis: eine
        Art.-17-Nachtilgung ist betrieblich etwas anderes als der Ablauf einer
        Detailfrist."""
        from core.models import AuditLog
        from core.retention.eventhistory import CATEGORY_ANONYMIZED_CLIENT, enforce_eventhistory_retention

        self._anonymisierte_person(facility, staff_user, doc_type_contact, admin_user, "Art17-Nachweis")

        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        eintraege = [
            entry
            for entry in AuditLog.objects.filter(action=AuditLog.Action.EVENT_HISTORY_REDACTED)
            if entry.detail.get("category") == CATEGORY_ANONYMIZED_CLIENT
        ]
        assert len(eintraege) == 1
        assert eintraege[0].detail["count"] >= 1


@pytest.mark.django_db
class TestDryRunZaehltJedeZeileEinmal:
    """Refs #2085 (Cross-Review Runde 2, MEDIUM 1): der Dry-run darf nicht
    ueberzeichnen.

    Die vier Kandidatenmengen sind **absichtlich nicht disjunkt** — eine
    Historienzeile kann zugleich zu einem soft-geloeschten Ereignis und zu einer
    anonymisierten Person gehoeren, und eine alte lebende Zeile kann zugleich
    Art.-17- und Alterskandidat sein. Im echten Lauf verhindert die Reihenfolge
    zusammen mit ``redacted_at IS NULL`` die Doppelverarbeitung; der Dry-run
    aendert dagegen nichts und muss deshalb selbst zaehlen, wie viele
    **verschiedene** Zeilen fallen wuerden.

    Warum das mehr als Kosmetik ist: der Dry-run ist genau das, was eine
    Betreiberin ansieht, **bevor** sie den Lauf freigibt. Eine zu hohe Zahl
    kostet Vertrauen oder fuehrt zu falschen Schluessen ueber den Bestand — und
    sie widerspricht der Zusage im Docstring von ``_count_without_held``, der
    Dry-run duerfe „nicht mehr versprechen als der echte Lauf".
    """

    def _ueberlappende_zeile(self, facility, staff_user, doc_type_contact, admin_user):
        """Eine Zeile, die in ZWEI Mengen faellt: geloeschtes Ereignis (D2) und
        anonymisierte Person (Refs #2085)."""
        from core.models import Client
        from core.services.events import soft_delete_event

        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Overlap-01",
            created_by=staff_user,
        )
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "Klartext"},
            client=person,
        )
        person.soft_delete(user=admin_user)
        person.anonymized_at = timezone.now()
        person.save(update_fields=["anonymized_at"])
        soft_delete_event(event, staff_user)
        return person, event

    def test_ueberlappende_zeile_wird_einmal_gezaehlt(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        from core.retention.eventhistory import enforce_eventhistory_retention

        _person, event = self._ueberlappende_zeile(facility, staff_user, doc_type_contact, admin_user)
        eindeutige_zeilen = EventHistory.objects.filter(event=event, redacted_at__isnull=True).count()

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)

        assert result["redacted"] == eindeutige_zeilen, (
            f"Dry-run meldet {result['redacted']} statt {eindeutige_zeilen} — "
            "ueberlappende Kandidaten werden doppelt gezaehlt."
        )

    def test_dry_run_stimmt_mit_dem_echten_lauf_ueberein(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user
    ):
        """Die schaerfste Form der Zusage: Vorschau und Lauf nennen dieselbe Zahl."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        self._ueberlappende_zeile(facility, staff_user, doc_type_contact, admin_user)

        vorschau = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)
        echt = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        assert vorschau["redacted"] == echt["redacted"]

    def test_zurueckgestellte_ereignisse_werden_ebenfalls_nur_einmal_gezaehlt(
        self, facility, settings_obj, staff_user, doc_type_contact, admin_user, lead_user
    ):
        """``deferred`` zaehlt Ereignisse, nicht Zeilen, und lief schon vorher
        ueber eine Menge — der Test nagelt das fuer die Ueberlappung fest, damit
        die Deduplizierung der Zeilen sie nicht versehentlich aufweicht."""
        from core.models import LegalHold
        from core.retention.eventhistory import enforce_eventhistory_retention

        person, _event = self._ueberlappende_zeile(facility, staff_user, doc_type_contact, admin_user)
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=person.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )

        result = enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=True)

        assert result["deferred"] == 1
        assert result["redacted"] == 0

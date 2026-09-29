"""Heilung unvollständig redigierter Historienzeilen (Refs #2188, Bezug #2177).

Vor dem Vorwärts-Fix aus #2177 konnte die Schlüsselrotation eine gerade
redigierte ``EventHistory``-Zeile mit einer frisch verschlüsselten Kopie der
alten Nutzlast überschreiben. ``redacted_at`` blieb dabei gesetzt — und weil
jeder Aufbewahrungslauf ausschließlich Zeilen mit ``redacted_at IS NULL``
anfasst, fiel die Zeile dauerhaft aus jeder weiteren Redaktion heraus, obwohl
sie wieder entschlüsselbare Werte trägt.

``repair_eventhistory_redaction`` findet solche Zeilen (Trockenlauf als
Standard) und heilt sie mit ``--apply``: unter Zeilensperre, mit einem
Nachweis je Zeile, hold-aware und ohne eine korrekt redigierte Zeile zu
verändern.
"""

import io
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import CommandError, call_command
from django.db import OperationalError, connection
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import AuditLog, Client, EventHistory, Facility
from core.services.events import create_event
from core.services.events.history_diff import build_anonymization_marker, build_field_redaction_marker
from core.services.file_vault.encryption import encrypt_field
from tests._row_lock_helpers import mitschnitt
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

COMMAND = "repair_eventhistory_redaction"
REPAIR_CATEGORY = "eventhistory_repaired"
LOCK_TABLES = ("core_client", "core_event", "core_eventhistory")


def _set_row(pk, *, before, after, redacted_at):
    """Schreibt eine Historienzeile am Append-only-Trigger vorbei (Testaufbau)."""
    from core.services.system import bypass_replication_triggers

    with bypass_replication_triggers(tables=("core_eventhistory",)):
        EventHistory.objects.filter(pk=pk).update(data_before=before, data_after=after, redacted_at=redacted_at)


def _row(facility, user, doc_type, *, before, after, redacted_at, client=None, case=None):
    """Legt ein Ereignis an und setzt dessen Historienzeile auf den gewünschten Zustand."""
    event = create_event(
        facility=facility,
        user=user,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 15},
        client=client,
        case=case,
    )
    row = EventHistory.objects.get(event=event)
    _set_row(row.pk, before=before, after=after, redacted_at=redacted_at)
    return row.pk


def _hit_payloads():
    """Nutzlast wie nach einer Umschlüsselung: wieder verschlüsselte Werte statt Marker."""
    before = {"notiz": encrypt_field("Erstgespräch vertraulich"), "dauer": 15}
    after = {"notiz": encrypt_field("Zweitgespräch vertraulich"), "dauer": 30, "ort": encrypt_field("Bahnhof")}
    return before, after


def _snapshot():
    """Byte-genauer Abzug aller Historienzeilen (jsonb als Text)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT id::text, action, data_before::text, data_after::text, field_metadata::text, "
            "changed_at, redacted_at FROM core_eventhistory ORDER BY id"
        )
        return {row[0]: row[1:] for row in cur.fetchall()}


def _repair_audits():
    return AuditLog.objects.filter(action=AuditLog.Action.EVENT_HISTORY_REDACTED, detail__category=REPAIR_CATEGORY)


def _run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command(COMMAND, *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def _facility_line(output, facility):
    lines = [line for line in output.splitlines() if f"'{facility.name}'" in line]
    assert len(lines) == 1, output
    return lines[0]


def _count(line, label):
    match = re.search(rf"{re.escape(label)} (\d+)", line)
    assert match, (label, line)
    return int(match.group(1))


@pytest.fixture
def bestand(facility, staff_user, doc_type_contact):
    """Fünf Zeilen: eine getroffene und vier, die niemand anfassen darf."""
    redacted_at = timezone.now() - timedelta(days=40)
    hit_before, hit_after = _hit_payloads()

    def row(**kwargs):
        return _row(facility, staff_user, doc_type_contact, **kwargs)

    rows = {
        "getroffen": row(before=hit_before, after=hit_after, redacted_at=redacted_at),
        "feldmarker": row(
            before=build_field_redaction_marker(["dauer"]),
            after=build_field_redaction_marker(["dauer", "notiz"]),
            redacted_at=redacted_at,
        ),
        "anonymisiert": row(
            before=build_anonymization_marker(), after=build_anonymization_marker(), redacted_at=redacted_at
        ),
        "unredigiert": row(
            before={"notiz": encrypt_field("offen")}, after={"notiz": encrypt_field("offen 2")}, redacted_at=None
        ),
        "anlage": row(before=None, after=build_field_redaction_marker(["dauer"]), redacted_at=redacted_at),
    }
    return SimpleNamespace(rows=rows, redacted_at=redacted_at, hit_before=hit_before, hit_after=hit_after)


@pytest.mark.django_db
class TestTrockenlauf:
    """Der Umfang im Bestand wird sichtbar, bevor irgendetwas geschrieben wird."""

    @pytest.mark.parametrize("args", [(), ("--dry-run",)], ids=["ohne-schalter", "dry-run"])
    def test_meldet_fund_je_einrichtung_und_schreibt_nichts(self, bestand, facility, args):
        vorher = _snapshot()
        audits = AuditLog.objects.count()

        output = _run(*args)

        line = _facility_line(output, facility)
        assert _count(line, "gefunden") == 1
        assert _count(line, "zurückgestellt (Legal Hold)") == 0
        assert "Trockenlauf — nichts geändert" in output
        assert "--apply" in output
        assert _snapshot() == vorher
        assert AuditLog.objects.count() == audits

    def test_dry_run_und_apply_zugleich_ist_ein_bedienfehler(self, bestand):
        vorher = _snapshot()
        with pytest.raises(CommandError, match="--dry-run.*--apply"):
            _run("--dry-run", "--apply")
        assert _snapshot() == vorher


@pytest.mark.django_db
class TestHeilung:
    def test_getroffene_zeile_traegt_danach_den_feld_marker_mit_nachweis(self, bestand, facility):
        pk = bestand.rows["getroffen"]
        vorher = _snapshot()
        audits = AuditLog.objects.count()

        output = _run("--apply")

        row = EventHistory.objects.get(pk=pk)
        assert row.data_before == build_field_redaction_marker(bestand.hit_before.keys())
        assert row.data_after == build_field_redaction_marker(bestand.hit_after.keys())
        # Die Chronologie-Frist der zweiten Stufe laeuft ab der urspruenglichen
        # Redaktion — die Heilung darf sie nicht verlaengern.
        assert row.redacted_at == bestand.redacted_at

        nachher = _snapshot()
        for name, other in bestand.rows.items():
            if name != "getroffen":
                assert nachher[str(other)] == vorher[str(other)], name

        assert AuditLog.objects.count() == audits + 1
        (proof,) = _repair_audits()
        assert proof.facility_id == facility.pk
        assert proof.target_type == "EventHistory"
        assert proof.target_id == str(pk)
        assert proof.detail["count"] == 1
        assert proof.detail["batch_first"] == proof.detail["batch_last"] == str(pk)

        line = _facility_line(output, facility)
        assert _count(line, "gefunden") == 1
        assert _count(line, "geheilt") == 1
        assert _count(line, "zurückgestellt (Legal Hold)") == 0
        assert "Trockenlauf" not in output

    @pytest.mark.parametrize(
        ("before", "after", "expected_before", "expected_after"),
        [
            (None, "hit", None, "field"),
            ("alt", "hit", "alt", "field"),
            ("hit", {}, "field", {}),
        ],
        ids=["anlage-null-bleibt-null", "marker-spalte-bleibt", "leere-spalte-bleibt-leer"],
    )
    def test_nur_die_belegten_spalten_ohne_marker_werden_ersetzt(
        self, facility, staff_user, doc_type_contact, before, after, expected_before, expected_after
    ):
        _hit_before, hit_after = _hit_payloads()
        alt = build_field_redaction_marker(["alt"])
        choose = {"hit": hit_after, "alt": alt, "field": build_field_redaction_marker(hit_after.keys())}

        def resolve(value):
            return choose.get(value, value) if isinstance(value, str) else value

        pk = _row(
            facility,
            staff_user,
            doc_type_contact,
            before=resolve(before),
            after=resolve(after),
            redacted_at=timezone.now(),
        )

        _run("--apply")

        row = EventHistory.objects.get(pk=pk)
        assert row.data_before == resolve(expected_before)
        assert row.data_after == resolve(expected_after)
        assert _repair_audits().count() == 1

    def test_zeile_einer_anonymisierten_person_bekommt_den_anonymisierungs_marker(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        hit_before, hit_after = _hit_payloads()
        pk = _row(
            facility,
            staff_user,
            doc_type_contact,
            client=client_identified,
            before=hit_before,
            after=hit_after,
            redacted_at=timezone.now(),
        )
        Client.objects.filter(pk=client_identified.pk).update(anonymized_at=timezone.now())

        _run("--apply")

        row = EventHistory.objects.get(pk=pk)
        assert row.data_before == build_anonymization_marker()
        assert row.data_after == build_anonymization_marker()
        assert _repair_audits().count() == 1

    def test_zweiter_lauf_findet_nichts_und_schreibt_nichts(self, bestand, facility):
        _run("--apply")
        vorher = _snapshot()
        audits = AuditLog.objects.count()

        output = _run("--apply")

        line = _facility_line(output, facility)
        assert _count(line, "gefunden") == 0
        assert _count(line, "geheilt") == 0
        assert _snapshot() == vorher
        assert AuditLog.objects.count() == audits

    def test_einrichtung_einschraenken_und_unbekannte_einrichtung(self, bestand, facility):
        Facility.objects.create(organization=facility.organization, name="Zweitstelle")

        alle = _run()
        assert "'Zweitstelle'" in alle

        nur_eine = _run("--facility", facility.name)
        assert "'Zweitstelle'" not in nur_eine
        assert _count(_facility_line(nur_eine, facility), "gefunden") == 1

        with pytest.raises(CommandError, match="Gibt es nicht"):
            _run("--facility", "Gibt es nicht")


@pytest.mark.django_db
class TestLegalHold:
    """Ein aktiver Hold ordnet an, den Zustand zu erhalten — auch diesen."""

    @pytest.mark.parametrize("target", ["Event", "Client", "Case"])
    def test_gehaltene_zeile_wird_gezaehlt_aber_nicht_geheilt(
        self, facility, staff_user, admin_user, doc_type_contact, client_identified, case_open, target
    ):
        from core.retention.legal_holds import create_hold_for_target

        hit_before, hit_after = _hit_payloads()
        pk = _row(
            facility,
            staff_user,
            doc_type_contact,
            client=client_identified,
            case=case_open,
            before=hit_before,
            after=hit_after,
            redacted_at=timezone.now(),
        )
        target_id = {
            "Event": EventHistory.objects.get(pk=pk).event_id,
            "Client": client_identified.pk,
            "Case": case_open.pk,
        }[target]
        create_hold_for_target(facility, target_type=target, target_id=target_id, user=admin_user, reason="Verfahren")
        vorher = _snapshot()
        audits = AuditLog.objects.count()

        trocken = _facility_line(_run(), facility)
        assert _count(trocken, "gefunden") == 1
        assert _count(trocken, "zurückgestellt (Legal Hold)") == 1

        echt = _facility_line(_run("--apply"), facility)
        assert _count(echt, "gefunden") == 1
        assert _count(echt, "geheilt") == 0
        assert _count(echt, "zurückgestellt (Legal Hold)") == 1

        assert _snapshot() == vorher
        assert AuditLog.objects.count() == audits


@pytest.mark.django_db
class TestSperrenUndRennen:
    def test_zwischenzeitlich_regulaer_redigierte_zeile_bleibt_unveraendert(self, bestand, facility):
        """Compare-and-Swap: erkannt wird ohne Sperre, geheilt nur, was UNTER der
        Sperre noch getroffen ist. Redigiert ein regulaerer Lauf die Zeile
        dazwischen, gewinnt seine Fassung, und es entsteht kein Nachweis fuer
        eine Heilung, die nicht stattfand."""
        pk = bestand.rows["getroffen"]

        def regulaere_redaktion():
            _set_row(
                pk,
                before=build_anonymization_marker(),
                after=build_anonymization_marker(),
                redacted_at=bestand.redacted_at,
            )

        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre("core_event", regulaere_redaktion)
            output = _run("--apply")

        assert aufzeichnung.offene_schranken() == []
        row = EventHistory.objects.get(pk=pk)
        assert row.data_before == build_anonymization_marker()
        assert row.data_after == build_anonymization_marker()
        assert _repair_audits().count() == 0
        line = _facility_line(output, facility)
        assert _count(line, "geheilt") == 0
        assert _count(line, "übersprungen") == 1

    def test_sperrfolge_person_ereignis_historie_vor_dem_ersten_nachweis(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        hit_before, hit_after = _hit_payloads()
        _row(
            facility,
            staff_user,
            doc_type_contact,
            client=client_identified,
            before=hit_before,
            after=hit_after,
            redacted_at=timezone.now(),
        )
        audit_positions = []

        with mitschnitt() as aufzeichnung:

            def on_audit(sender, instance, created, **kwargs):
                if created:
                    audit_positions.append(len(aufzeichnung))

            post_save.connect(on_audit, sender=AuditLog, weak=False)
            try:
                _run("--apply")
            finally:
                post_save.disconnect(on_audit, sender=AuditLog)

        assert [t for t in aufzeichnung.sperrfolge() if t in LOCK_TABLES] == list(LOCK_TABLES)
        lock_positions = [
            index for index, abfrage in enumerate(aufzeichnung) if any(abfrage.sperrt(t) for t in LOCK_TABLES)
        ]
        assert audit_positions, "kein Nachweis geschrieben"
        assert max(lock_positions) < min(audit_positions)
        # Die Historienzeile wird unter Sperre NEU gelesen, gebunden an genau sie.
        assert aufzeichnung.sperren("core_eventhistory")

    def test_verklemmung_trifft_die_charge_nicht_den_lauf(self, bestand, facility):
        class _DeadlockCause(Exception):
            sqlstate = "40P01"

        def verklemmung():
            raise OperationalError("deadlock detected") from _DeadlockCause()

        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre("core_event", verklemmung)
            output = _run("--apply")

        row = EventHistory.objects.get(pk=bestand.rows["getroffen"])
        assert row.data_after == build_field_redaction_marker(bestand.hit_after.keys())
        assert _repair_audits().count() == 1
        assert _count(_facility_line(output, facility), "geheilt") == 1

    def test_anderer_datenbankfehler_bricht_ab_ohne_teilzustand(self, bestand):
        def kaputt():
            raise OperationalError("server closed the connection unexpectedly")

        vorher = _snapshot()
        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre("core_event", kaputt)
            with pytest.raises(OperationalError):
                _run("--apply")

        assert _snapshot() == vorher
        assert _repair_audits().count() == 0


@pytest.mark.django_db
class TestAusgabe:
    def test_nur_zaehlwerte_und_einrichtungsnamen(self, bestand, facility):
        """Das Protokoll eines Wartungslaufs landet in Journalen und Tickets —
        es darf weder Feldwerte noch verschluesselte Tokens noch Zeilen-IDs
        tragen."""
        geheim = [
            value["value"]
            for payload in (bestand.hit_before, bestand.hit_after)
            for value in payload.values()
            if isinstance(value, dict)
        ]
        for args in ((), ("--apply",)):
            output = _run(*args)
            assert "gAAAA" not in output
            assert "__encrypted__" not in output
            assert "vertraulich" not in output
            assert "Bahnhof" not in output
            for token in geheim:
                assert token not in output
            for pk in bestand.rows.values():
                assert str(pk) not in output
            assert _facility_line(output, facility)


@pytest.mark.django_db
class TestOhneOwnerKontext:
    """Unter Zeilensicherheit saehe die Erkennung keine einzige Historienzeile
    und meldete faelschlich einen sauberen Bestand — ein Betreiber, der den
    Trockenlauf mit der App-Rolle faehrt, hielte seine Installation dann fuer
    geheilt. Deshalb bricht das Command ohne Owner-/Bypass-Kontext ab, bevor es
    eine Zeile liest oder schreibt; im Trockenlauf genauso wie bei der Heilung.
    """

    @pytest.mark.parametrize("args", [(), ("--dry-run",), ("--apply",)], ids=["standard", "dry-run", "apply"])
    @pytest.mark.parametrize("scope", ["einrichtung", "leer"])
    def test_command_bricht_ab_ohne_zu_lesen_oder_zu_schreiben(
        self,
        bestand,
        facility,
        rls_test_role,  # noqa: F811
        args,
        scope,
    ):
        vorher = _snapshot()
        audits = AuditLog.objects.count()
        facility_id = facility.pk if scope == "einrichtung" else ""

        with mitschnitt() as aufzeichnung:
            with as_rls_role(rls_test_role, facility_id=facility_id):
                with pytest.raises(CommandError, match="Bypass-Kontext"):
                    _run(*args)

        assert not [a for a in aufzeichnung if a.liest("core_eventhistory") or a.liest("core_event")]
        assert _snapshot() == vorher
        assert AuditLog.objects.count() == audits

    @pytest.mark.parametrize("dry_run", [True, False], ids=["trockenlauf", "heilung"])
    def test_dienst_wirft_ohne_owner_kontext(
        self,
        bestand,
        facility,
        rls_test_role,  # noqa: F811
        dry_run,
    ):
        from core.retention import eventhistory, eventhistory_repair

        vorher = _snapshot()
        with mitschnitt() as aufzeichnung:
            with as_rls_role(rls_test_role, facility_id=facility.pk):
                with pytest.raises(eventhistory.OwnerContextMissingError):
                    eventhistory_repair.repair_eventhistory_redaction(facility, dry_run=dry_run)

        assert not [a for a in aufzeichnung if a.liest("core_eventhistory")]
        assert _snapshot() == vorher

"""Regressionstests gegen CSV-Formula-Injection (Refs #719, #1064).

Werte, die mit ``=``, ``+``, ``-``, ``@``, Tab oder CR/LF beginnen,
werden in Excel/LibreOffice als Formel ausgewertet — `=cmd|'/c calc'!A1`
in einem Klientel-Pseudonym fuehrt zu Code-Execution beim Oeffnen der CSV.
``_sanitize_csv_cell`` praefixt solche Werte mit ``'`` (OWASP-Pattern).

Abgedeckt: Events-/Statistik-Export (#719) und der AuditLog-CSV-Export
des super_admin (#1064).
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, FieldTemplate, User
from core.services.system import _sanitize_csv_cell, export_events_csv


class TestSanitizeCsvCellUnit:
    """Unit-Test des Sanitizers — alle 6 OWASP-Praefixe + Negativfaelle."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("=cmd|'/c calc'!A1", "'=cmd|'/c calc'!A1"),
            ("+1+2", "'+1+2"),
            ("-1234", "'-1234"),
            ("@SUM(A1:A10)", "'@SUM(A1:A10)"),
            ("\tTab-Smuggling", "'\tTab-Smuggling"),
            ("\rCR-Smuggling", "'\rCR-Smuggling"),
            ("\nLF-Smuggling", "'\nLF-Smuggling"),
            # Negative Faelle — kein Prefix-Match, unveraendert
            ("Maria", "Maria"),
            ("123", "123"),
            ("# Kommentar", "# Kommentar"),
            (".verstecktes-Feld", ".verstecktes-Feld"),
            ("", ""),
            # None → leerer String, kein Crash
            (None, ""),
        ],
    )
    def test_sanitize_csv_cell(self, raw, expected):
        assert _sanitize_csv_cell(raw) == expected


@pytest.fixture
def normal_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt",
        category=DocumentType.Category.CONTACT,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


@pytest.fixture
def attacker_client(facility):
    """Klientel mit boesgemeintem Pseudonym — klassischer Injection-Vektor."""
    return Client.objects.create(
        facility=facility,
        pseudonym="=cmd|'/c calc'!A1",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )


@pytest.fixture
def freitext_field(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Freitext",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def attacker_event(facility, normal_doc_type, attacker_client, admin_user, freitext_field):
    return Event.objects.create(
        facility=facility,
        client=attacker_client,
        document_type=normal_doc_type,
        occurred_at=timezone.now(),
        data_json={freitext_field.slug: "+SUM(A:A)"},
        created_by=admin_user,
    )


@pytest.mark.django_db
class TestExportEventsCSVNeutralizesInjection:
    """Integrations-Test: ein boesgemeintes Pseudonym landet im CSV mit ``'``-Praefix."""

    def test_pseudonym_with_formula_prefix_is_neutralized(self, facility, attacker_event, admin_user):
        import csv
        import io

        chunks = list(
            export_events_csv(
                facility, date.today() - timedelta(days=1), date.today() + timedelta(days=1), user=admin_user
            )
        )
        full_csv = "".join(chunks)

        # Property: KEINE Zelle darf mit einem OWASP-Praefix-Zeichen beginnen.
        # Das ist die zentrale Garantie — egal in welcher Spalte das
        # boesgemeinte Pseudonym landet.
        reader = csv.reader(io.StringIO(full_csv))
        for row in reader:
            for cell in row:
                if cell:
                    assert cell[0] not in ("=", "+", "-", "@", "\t", "\r", "\n"), (
                        f"Zelle beginnt mit Formula-Injection-Praefix: {cell!r} (Zeile: {row})"
                    )

        # Positiv-Check: der sanitized Wert mit ``'``-Praefix ist da.
        assert "'=cmd|'/c calc'!A1" in full_csv, (
            f"Erwartete neutralisierte Variante ``'=cmd|'/c calc'!A1`` nicht im CSV gefunden.\n"
            f"CSV-Auszug: {full_csv[:500]}"
        )

    def test_field_value_with_plus_prefix_neutralized(self, facility, attacker_event, admin_user):
        chunks = list(
            export_events_csv(
                facility, date.today() - timedelta(days=1), date.today() + timedelta(days=1), user=admin_user
            )
        )
        full_csv = "".join(chunks)

        # ``+SUM(A:A)`` aus data_json muss neutralisiert sein.
        assert "'+SUM(A:A)" in full_csv, (
            f"Field-Value ``+SUM(A:A)`` nicht neutralisiert im CSV.\nCSV-Auszug: {full_csv[:500]}"
        )

    def test_normal_pseudonym_not_modified(self, facility, normal_doc_type, admin_user, freitext_field):
        """Negativtest: harmlose Pseudonyme bleiben unveraendert."""
        cli = Client.objects.create(
            facility=facility,
            pseudonym="Maria-Mueller-1",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        Event.objects.create(
            facility=facility,
            client=cli,
            document_type=normal_doc_type,
            occurred_at=timezone.now(),
            data_json={freitext_field.slug: "Hallo Welt"},
            created_by=admin_user,
        )
        chunks = list(
            export_events_csv(
                facility, date.today() - timedelta(days=1), date.today() + timedelta(days=1), user=admin_user
            )
        )
        full_csv = "".join(chunks)

        # Pseudonym + Freitext erscheinen ohne ``'``-Praefix.
        assert "Maria-Mueller-1" in full_csv
        assert "'Maria-Mueller-1" not in full_csv
        assert "Hallo Welt" in full_csv
        assert "'Hallo Welt" not in full_csv


@pytest.mark.django_db
class TestAuditLogExportNeutralizesInjection:
    """Integrations-Test AuditLog-CSV-Export (Refs #1064).

    Der Events-/Statistik-Export sanitized seit #719 jede dynamische
    Zelle — ``SystemAuditLogExportView._iter_csv`` wurde dabei uebersehen.
    Angriffspfad: ein facility_admin benennt die eigene Facility in einen
    Formel-Payload um, jede Audit-Zeile der Facility traegt den Namen in
    der ``facility``-Spalte; ein super_admin exportiert das Audit-Log und
    oeffnet die CSV in Excel/LibreOffice → Formel/DDE laeuft im Kontext
    des hoechstprivilegierten Operators.
    """

    def _export_csv(self, client, super_admin_user):
        client.force_login(super_admin_user)
        response = client.get(reverse("core:system_audit_export"))
        assert response.status_code == 200
        return b"".join(response.streaming_content).decode("utf-8")

    def test_facility_name_with_formula_prefix_is_neutralized(self, client, super_admin_user, facility):
        facility.name = "=cmd|'/c calc'!A1"
        facility.save()
        AuditLog.objects.create(action=AuditLog.Action.LOGIN, facility=facility)

        full_csv = self._export_csv(client, super_admin_user)

        # Property: KEINE Zelle darf mit einem OWASP-Praefix-Zeichen
        # beginnen — gleiche Garantie wie beim Events-Export (#719).
        import csv
        import io

        reader = csv.reader(io.StringIO(full_csv))
        for row in reader:
            for cell in row:
                if cell:
                    assert cell[0] not in ("=", "+", "-", "@", "\t", "\r", "\n"), (
                        f"Zelle beginnt mit Formula-Injection-Praefix: {cell!r} (Zeile: {row})"
                    )

        # Positiv-Check: der neutralisierte Facility-Name ist da.
        assert "'=cmd|'/c calc'!A1" in full_csv, (
            f"Erwartete neutralisierte Variante nicht im CSV gefunden.\nCSV-Auszug: {full_csv[:500]}"
        )

    def test_username_and_target_cells_neutralized(self, client, super_admin_user, facility):
        """``UnicodeUsernameValidator`` erlaubt fuehrende ``@``/``+``/``-``
        (``-2+3`` ist ein gueltiger Username und eine valide Excel-Formel);
        ``target_type``/``target_id`` sind freie CharFields."""
        attacker = User.objects.create_user(
            username="-2+3",
            role=User.Role.FACILITY_ADMIN,
            facility=facility,
            is_staff=True,
        )
        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN,
            user=attacker,
            facility=facility,
            target_type="@SUM(A1:A10)",
            target_id="+2+5",
        )

        full_csv = self._export_csv(client, super_admin_user)

        assert "'-2+3" in full_csv
        assert "'@SUM(A1:A10)" in full_csv
        assert "'+2+5" in full_csv

    def test_normal_values_not_modified(self, client, super_admin_user, facility):
        """Negativtest: harmlose Werte bleiben unveraendert."""
        AuditLog.objects.create(
            action=AuditLog.Action.LOGIN,
            facility=facility,
            target_type="Client",
            target_id="42",
        )

        full_csv = self._export_csv(client, super_admin_user)

        assert "Teststelle" in full_csv
        assert "'Teststelle" not in full_csv
        assert "Client" in full_csv
        assert "'Client" not in full_csv


@pytest.mark.django_db
class TestCsvHeaderNeutralizesInjection:
    """Security R13: FieldTemplate.name (freies CharField, durch
    facility_admin setzbar) landet als CSV-Header-Zelle — muss wie
    Werte-Zellen durch _sanitize_csv_cell laufen."""

    def test_formula_field_name_in_header_is_neutralized(self, facility, admin_user, normal_doc_type):
        import csv
        import io

        FieldTemplate.objects.create(
            facility=facility,
            name="=SUM(A1:A10)",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        chunks = list(
            export_events_csv(
                facility, date.today() - timedelta(days=1), date.today() + timedelta(days=1), user=admin_user
            )
        )
        full_csv = "".join(chunks)
        reader = csv.reader(io.StringIO(full_csv))
        for row in reader:
            for cell in row:
                if cell:
                    assert cell[0] not in ("=", "+", "-", "@", "\t", "\r", "\n"), (
                        f"Header-/Zellen-Injection: {cell!r} (Zeile: {row})"
                    )
        assert "'=SUM(A1:A10)" in full_csv

"""Tests for DSGVO documentation package."""

import re
from datetime import date
from unittest import mock

import pytest
from django.test import Client as DjangoClient

from core.models import AuditLog, Settings
from core.services.client import _document_type_retention_overrides, get_document_list, render_document

# Refs #1775: Zeilen der Fristen-Tabelle in dsfa.md § 2.3
# ("| # | Frist | Wert | Einstellung | Wirkung bei Ablauf |"). Nur diese eine
# Tabelle im Dokument hat eine laufende Nummer in der ersten Spalte.
_RETENTION_ROW = re.compile(r"^\|\s*(?P<nr>\d+)\s*\|\s*(?P<frist>[^|]*?)\s*\|\s*(?P<wert>[^|]*?)\s*\|")


def _retention_rows(content: str) -> dict[int, tuple[str, str]]:
    """``{Zeilennummer: (Frist, Wert)}`` aus dsfa.md § 2.3.

    Refs #1775: ``"365 Tage" in content`` beweist **nichts** ueber eine
    bestimmte Frist — die Default-Werte von Frist 2 (``retention_identified_days``)
    und Frist 5 (``retention_activities_days``) sind beide 365, und das Wort
    "keine" steht ohnehin in der Prosa unter der Tabelle ("greift **keine** der
    sieben Fristen"). Erst die Zuordnung Zeile → Zellinhalt ist trennscharf.
    """
    rows: dict[int, tuple[str, str]] = {}
    for line in content.splitlines():
        match = _RETENTION_ROW.match(line)
        if match:
            rows[int(match.group("nr"))] = (match.group("frist"), match.group("wert"))
    return rows


@pytest.fixture
def facility(facility):
    """Root-conftest-Einrichtung plus die hier assertierten Settings (Refs #1839).

    ``facility_full_name`` und die drei Aufbewahrungsfristen werden in dieser
    Datei erwartet (Z. 101/475 bzw. 106-108/215/223/298) und bleiben deshalb
    hier stehen; Organisation und Einrichtung kommen aus ``tests/conftest.py``.
    """
    Settings.objects.create(
        facility=facility,
        facility_full_name="Anlaufstelle Musterstadt",
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )
    return facility


@pytest.mark.django_db
class TestDSGVOPackageService:
    """Service-level tests."""

    def test_get_document_list_returns_7(self):
        docs = get_document_list()
        assert len(docs) == 7
        slugs = [d["slug"] for d in docs]
        assert "verarbeitungsverzeichnis" in slugs
        assert "dsfa" in slugs
        assert "av-vertrag" in slugs
        assert "toms" in slugs
        assert "informationspflichten" in slugs
        assert "loeschkonzept" in slugs
        assert "mitarbeiter-informationspflichten" in slugs

    def test_render_replaces_facility_name(self, facility):
        content, filename = render_document("verarbeitungsverzeichnis", facility)
        assert "Anlaufstelle Musterstadt" in content
        assert "{{ facility_name }}" not in content

    def test_render_replaces_retention_days(self, facility):
        content, _ = render_document("informationspflichten", facility)
        assert "90 Tage" in content
        assert "365 Tage" in content
        assert "3650 Tage" in content
        assert "{{ retention_" not in content

    def test_render_replaces_date(self, facility):
        content, _ = render_document("dsfa", facility)
        assert "{{ date }}" not in content

    def test_render_uses_berlin_local_date(self, facility):
        with (
            mock.patch("core.services.client.dsgvo_package.date", create=True) as mock_date,
            mock.patch("core.services.client.dsgvo_package.timezone.localdate", return_value=date(2026, 6, 22)),
        ):
            mock_date.today.return_value = date(2026, 6, 21)
            content, _ = render_document("dsfa", facility)

        assert "22.06.2026" in content
        assert "21.06.2026" not in content

    def test_render_unknown_slug_raises(self, facility):
        with pytest.raises(ValueError, match="Unknown document"):
            render_document("nonexistent", facility)

    def test_all_templates_render(self, facility):
        for doc in get_document_list():
            content, filename = render_document(doc["slug"], facility)
            assert len(content) > 100
            assert filename.endswith(".md")


@pytest.mark.django_db
class TestTemplateCurrentState:
    """Refs #2075: Altvorlagen bilden Softwarestand und TOM-Ist-Stand ab."""

    @pytest.mark.parametrize(
        ("slug", "version"),
        [
            ("av-vertrag", "1.2"),
            ("dsfa", "1.5"),
            ("informationspflichten", "1.2"),
            ("loeschkonzept", "1.1"),
            ("mitarbeiter-informationspflichten", "1.1"),
            ("toms", "1.3"),
            ("verarbeitungsverzeichnis", "1.2"),
        ],
    )
    def test_stale_template_stamps_are_current(self, facility, slug, version):
        content, _ = render_document(slug, facility)

        assert f"**Version:** {version} (Softwarestand v0.99.6, 2026-10-07)" in content

    def test_toms_describe_current_security_controls_and_limits(self, facility):
        content, _ = render_document("toms", facility)

        for control in (
            "Row Level Security",
            "File-Vault",
            "MultiFernet",
            "HMAC-SHA256",
            "Passkeys",
            "kein passwordless Login",
        ):
            assert control in content
        assert "18 Facility-gescopte Tabellen" not in content

    def test_toms_disclose_plaintext_free_text_residual_risk(self, facility):
        content, _ = render_document("toms", facility)

        assert "folgen **nicht** dem Sensitivity/Encryption/Retention-Modell" in content
        for field_name in (
            "Client.notes",
            "Case.description",
            "Episode.description",
            "WorkItem.description",
            "AuditLog.detail",
        ):
            assert field_name in content
        assert "feldweise Verschlüsselung ist bis post-v1.0 vertagt" in content

    @pytest.mark.parametrize("slug", ["informationspflichten", "verarbeitungsverzeichnis"])
    def test_legal_basis_is_facility_neutral(self, facility, slug):
        content, _ = render_document(slug, facility)

        assert "SGB VIII" not in content
        assert "einschlägiges Fach- und Sozialrecht der Einrichtung" in content


@pytest.mark.django_db
class TestLoeschkonzept:
    """Das eigenstaendige Loeschkonzept rendert Ist-Fristen und Backup-Grenze."""

    def test_renders_all_configured_retention_values(self, facility):
        settings_obj = facility.settings
        settings_obj.retention_anonymous_days = 91
        settings_obj.retention_identified_days = 366
        settings_obj.retention_qualified_days = 3651
        settings_obj.retention_activities_days = 201
        settings_obj.client_trash_days = 46
        settings_obj.auditlog_retention_months = 25
        settings_obj.save()

        content, filename = render_document("loeschkonzept", facility)

        assert filename == "loeschkonzept.md"
        for value in ("91 Tage", "366 Tage", "3651 Tage", "201 Tage", "46 Tage", "25 Monate"):
            assert value in content
        assert "{{ retention_" not in content

    def test_renders_backup_retention_default(self, facility):
        content, _ = render_document("loeschkonzept", facility)

        assert "Backup-Sicherungsstände | 90 Tage" in content
        assert "{{ backup_retention_days }}" not in content

    def test_unwired_environment_does_not_override_backup_retention(self, facility, monkeypatch):
        monkeypatch.setenv("BACKUP_RETENTION_DAYS", "120")

        content, _ = render_document("loeschkonzept", facility)

        assert "Backup-Sicherungsstände | 90 Tage" in content
        assert "Backup-Sicherungsstände | 120 Tage" not in content

    def test_explains_backup_deletion_boundary(self, facility):
        content, _ = render_document("loeschkonzept", facility)

        assert "unverzüglich ≠ Zerstörung integritätsgesicherter Sicherungsstände" in content
        assert "HMAC" in content
        assert "Legal Hold" in content
        assert "BACKUP_RETENTION_DAYS" not in content


@pytest.mark.django_db
class TestMitarbeiterInformationspflichten:
    """Beschaeftigte erhalten ein eigenes, kurzes Art.-13/14-Artefakt."""

    def test_renders_employment_context_and_live_audit_retention(self, facility):
        content, filename = render_document("mitarbeiter-informationspflichten", facility)

        assert filename == "mitarbeiter_informationspflichten.md"
        assert "§ 26 BDSG" in content
        assert "Art. 6 Abs. 1 lit. b DSGVO" in content
        assert "Benutzername, Anzeigename, E-Mail-Adresse, Rolle und Berechtigung zur" in content
        assert "24 Monate" in content
        assert "{{" not in content


@pytest.mark.django_db
class TestAvvSubprocessors:
    """AVV § 6 bietet konkrete Kategorien oder eine ausdrueckliche Keine-Wahl."""

    def test_lists_examples_and_none_option(self, facility):
        content, _ = render_document("av-vertrag", facility)

        for category in ("Tracker-Host", "SMTP-Dienst", "Offsite-Backup-Speicher"):
            assert category in content
        assert "Keine Unterauftragnehmer" in content


@pytest.mark.django_db
class TestDsfaExtendedRetentionPlaceholders:
    """Refs #1762: dsfa.md § 2.3 rendert alle sieben Aufbewahrungsfristen als
    tatsächlichen Ist-Wert — vorher waren es nur die ersten drei
    (``retention_{anonymous,identified,qualified}_days``), die restlichen
    vier standen als "Standardwert mit Prüfauftrag"-Prosa im Dokument."""

    def test_scalar_fields_render_configured_values(self, facility):
        settings_obj = facility.settings
        settings_obj.retention_activities_days = 200
        settings_obj.client_trash_days = 45
        settings_obj.auditlog_retention_months = 36
        settings_obj.save()

        content, _ = render_document("dsfa", facility)
        rows = _retention_rows(content)

        # Refs #1775: zellgenau statt ``"200 Tage" in content`` — sonst
        # bestuende der Test auch, wenn der Wert in der falschen Zeile landet.
        assert rows[5] == ("Aktivitäten / Zeitstrom-Einträge", "200 Tage")
        assert rows[6] == ("Papierkorb-Frist für gelöschte Personen und Fälle", "45 Tage")
        assert rows[7] == ("Audit-Log", "36 Monate")
        assert "{{ retention_activities_days }}" not in content
        assert "{{ client_trash_days }}" not in content
        assert "{{ auditlog_retention_months }}" not in content

    def test_default_values_render_when_settings_use_model_defaults(self, facility):
        # `facility`-Fixture legt Settings ohne explizite activities/trash/
        # auditlog-Werte an -> Modell-Defaults 365/30/24 gelten.
        content, _ = render_document("dsfa", facility)
        rows = _retention_rows(content)

        # Refs #1775: "365 Tage" allein war nicht trennscharf — die Fixture
        # setzt ``retention_identified_days=365``, Zeile 2 traegt denselben
        # Text. Ein kaputter ``retention_activities_days``-Platzhalter blieb
        # damit unbemerkt.
        assert rows[5] == ("Aktivitäten / Zeitstrom-Einträge", "365 Tage")
        assert rows[6] == ("Papierkorb-Frist für gelöschte Personen und Fälle", "30 Tage")
        assert rows[7] == ("Audit-Log", "24 Monate")
        assert rows[8] == ("Inhalte früherer Fassungen eines Ereignisses", "24 Monate")
        assert rows[9] == ("Chronologie früherer Fassungen (nach ihrer Redaktion)", "24 Monate")
        assert rows[10] == ("Übergabenotizen (Schichtnotizen)", "90 Tage")

    def test_check_prosa_is_removed(self, facility):
        """Die "Standardwert, vor Freigabe zu prüfen"-Prosa entfällt, weil
        jetzt alle sieben Fristen echte Ist-Werte sind.

        Refs #1775: Die reinen Negativ-Assertionen ("Standardwert"/"Prüfauftrag"
        kommen nicht vor) waren nie rot — "Prüfauftrag" stand ueberhaupt nie in
        der Vorlage. Der Kern der Anforderung ist positiv formulierbar: **jede**
        der sieben Zellen traegt einen konkreten Ist-Wert.
        """
        content, _ = render_document("dsfa", facility)
        rows = _retention_rows(content)

        # Refs #2492: plus the two history periods and the handover note period.
        assert sorted(rows) == list(range(1, 12)), f"§ 2.3 no longer lists eleven periods: {rows}"
        for nr, (frist, wert) in rows.items():
            assert "{{" not in wert, f"Frist {nr} ({frist}) traegt einen ungerenderten Platzhalter: {wert!r}"
            # Zeile 4 ist die einzige nicht-numerische ("keine" bzw.
            # "<Typ>: <n> Tage"); die uebrigen sechs sind "<n> Tage/Monate".
            erwartet = r"^(keine|.+: \d+ Tage)$" if nr == 4 else r"^\d+ (Tage|Monate)$"
            assert re.match(erwartet, wert), f"Frist {nr} ({frist}) ist kein Ist-Wert, sondern Prosa: {wert!r}"

        # Die alte Prosa-Fassung im Einleitungssatz ("die uebrigen vier stehen
        # mit ihrem Standardwert und sind vor Freigabe zu pruefen").
        assert "Standardwert" not in content
        assert "Alle elf sind unten mit dem für diese Einrichtung **tatsächlich konfigurierten** Wert" in content

    def test_document_type_override_placeholder_is_replaced(self, facility):
        content, _ = render_document("dsfa", facility)
        rows = _retention_rows(content)

        assert "{{ retention_document_type_overrides }}" not in content
        # Die alte generische Beschreibung entfaellt zugunsten des Ist-Werts.
        assert "je Typ gesetzt, sonst keine" not in content
        # Refs #1775: Kein Dokumentationstyp mit eigener Frist -> die ZELLE
        # sagt "keine". ``"keine" in content`` war vakuum-wahr: die Prosa unter
        # der Tabelle enthaelt "greift **keine** der sieben Fristen".
        assert rows[4] == ("Höchstfrist (verkürzt nur) einzelner Dokumentationstypen", "keine")


class TestVerarbeitungsverzeichnisAuditLogFrist:
    """Refs #1762: VVT und DSFA liegen im **selben** ausgelieferten ZIP.

    § 3.4 der VVT nannte hart „1 Jahr" Audit-Log-Aufbewahrung, waehrend die
    Schwester-Vorlage im selben Download den echten Wert rendert
    (``auditlog_retention_months``, Default 24, ``0`` = unbegrenzt) und die
    In-App-VVT es ohnehin richtig sagt. Ergebnis waren zwei sich
    widersprechende Art.-30-/Art.-35-Aussagen in einem Paket.
    """

    def test_frist_kommt_aus_den_settings(self, facility):
        settings_obj = facility.settings
        settings_obj.auditlog_retention_months = 36
        settings_obj.save()

        content, _ = render_document("verarbeitungsverzeichnis", facility)

        assert "36 Monate" in content
        assert "1 Jahr" not in content
        assert "{{ auditlog_retention_months }}" not in content

    def test_dauerhafte_ausnahmen_stehen_wortgleich_zur_dsfa(self, facility):
        vvt, _ = render_document("verarbeitungsverzeichnis", facility)
        dsfa, _ = render_document("dsfa", facility)

        for begriff in ("Sicherheitsverstöße", "Berichtszeiträume", "ungeprüfter Freitext"):
            assert begriff in vvt, f"VVT nennt die Ausnahme nicht: {begriff}"
            assert begriff in dsfa

    def test_beide_dokumente_nennen_dieselbe_frist(self, facility):
        """Der eigentliche Befund: ein ZIP, zwei Aussagen."""
        settings_obj = facility.settings
        settings_obj.auditlog_retention_months = 18
        settings_obj.save()

        vvt, _ = render_document("verarbeitungsverzeichnis", facility)
        dsfa, _ = render_document("dsfa", facility)

        assert "18 Monate" in vvt
        assert "18 Monate" in dsfa

    def test_document_type_override_shows_real_configured_type(self, facility):
        from core.models import DocumentType

        DocumentType.objects.create(facility=facility, name="Vermittlung", retention_days=180)

        content, _ = render_document("dsfa", facility)

        assert _retention_rows(content)[4] == (
            "Höchstfrist (verkürzt nur) einzelner Dokumentationstypen",
            "Vermittlung: 180 Tage",
        )
        assert "je Typ gesetzt, sonst keine" not in content

    def test_document_type_override_ignores_non_event_types(self, facility):
        """Nur Ereignis-Typen zaehlen — sonst behauptet die DSFA eine Frist,
        die nirgends durchgesetzt wird (Refs #1738, G4).

        ``retention_days`` ist seit M24 event-only (ADR-039): der
        Durchsetzungslauf (``enforce_document_type_retention``) und die
        Strategie-Quelle (``iter_strategies``) filtern auf
        ``entity_type=EVENT``. Zeile 4 in dsfa.md § 2.3 ist der **Ist-Wert
        dessen, was der Aufbewahrungs-Job tut** — ein Personen- oder Fall-Typ
        mit gesetzter Frist waere dort eine durchgesetzte Frist, die es nicht
        gibt. Fuer Personen- und Fall-Daten gilt der Owner-Lebenszyklus, der in
        derselben DSFA an anderer Stelle beschrieben ist.
        """
        from core.models import DocumentType

        DocumentType.objects.create(
            facility=facility,
            name="Personenprofil-Frist",
            entity_type=DocumentType.EntityType.CLIENT,
            retention_days=42,
        )
        DocumentType.objects.create(
            facility=facility,
            name="Fallformular-Frist",
            entity_type=DocumentType.EntityType.CASE,
            retention_days=43,
        )
        # Positiv-Kontrolle im selben Lauf: der Ereignis-Typ erscheint sehr wohl.
        DocumentType.objects.create(facility=facility, name="Vermittlung", retention_days=180)

        assert _document_type_retention_overrides(facility) == [("Vermittlung", 180)]

        content, _ = render_document("dsfa", facility)

        assert _retention_rows(content)[4] == (
            "Höchstfrist (verkürzt nur) einzelner Dokumentationstypen",
            "Vermittlung: 180 Tage",
        )
        assert "Personenprofil-Frist" not in content
        assert "Fallformular-Frist" not in content


@pytest.mark.django_db
class TestDSGVOPackageView:
    """View-level tests for package overview."""

    def test_admin_can_access(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 200

    def test_lead_gets_403(self, lead_user):
        client = DjangoClient()
        client.force_login(lead_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 403

    def test_staff_gets_403(self, staff_user):
        client = DjangoClient()
        client.force_login(staff_user)
        response = client.get("/dsgvo/")
        assert response.status_code == 403

    def test_unauthenticated_redirects(self):
        client = DjangoClient()
        response = client.get("/dsgvo/")
        assert response.status_code == 302


@pytest.mark.django_db
class TestDSGVODocumentDownload:
    """View-level tests for document download."""

    def test_admin_can_download(self, admin_user, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/verarbeitungsverzeichnis/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/markdown; charset=utf-8"
        assert "attachment" in response["Content-Disposition"]
        assert b"Anlaufstelle Musterstadt" in response.content

    def test_creates_audit_log(self, admin_user, facility):
        client = DjangoClient()
        client.force_login(admin_user)
        client.get("/dsgvo/toms/")
        assert AuditLog.objects.filter(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.EXPORT,
            target_type="DSGVO-Dokument",
            target_id="toms",
        ).exists()

    def test_unknown_document_404(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        response = client.get("/dsgvo/nonexistent/")
        assert response.status_code == 404

    def test_lead_gets_403(self, lead_user):
        client = DjangoClient()
        client.force_login(lead_user)
        response = client.get("/dsgvo/toms/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestDSGVORateLimit:
    """S3 (Refs #1084): RATELIMIT_BULK_ACTION (30/h/User) auf Package-Ansicht
    und Dokument-Download — Dokument-Rendering ist teuer, die Views waren die
    letzte Luecke im sonst dichten Drossel-Netz.

    Tests laufen mit ``RATELIMIT_ENABLE = False``; ``ratelimit_enabled``
    (conftest) aktiviert es und nagelt das Zaehlfenster fest (Refs #1900).
    django-ratelimit mit ``block=True`` antwortet via ``handler403`` mit 429
    (Refs #1354).
    """

    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, ratelimit_enabled):
        return ratelimit_enabled

    def test_package_view_blocked_after_30_requests(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        for _ in range(30):
            assert client.get("/dsgvo/").status_code == 200
        assert client.get("/dsgvo/").status_code == 429

    def test_document_download_blocked_after_30_requests(self, admin_user):
        client = DjangoClient()
        client.force_login(admin_user)
        for _ in range(30):
            assert client.get("/dsgvo/toms/").status_code == 200
        assert client.get("/dsgvo/toms/").status_code == 429


# Refs #840 (C-73): Versionsstempel-Footer in jedem gerenderten Dokument.
class TestPackageVersionStamp:
    def test_footer_contains_settings_hash_and_software_version(self, facility, settings):
        settings.SOURCE_CODE_VERSION = "abc123def456"
        content, _ = render_document("toms", facility)
        assert "Settings-Hash:" in content
        assert "Software-Version: abc123de" in content
        assert "Generiert:" in content

    def test_settings_change_changes_hash(self, facility):
        content_before, _ = render_document("toms", facility)
        # Settings aendern
        s = facility.settings
        s.retention_anonymous_days = 180
        s.save()
        content_after, _ = render_document("toms", facility)

        # Beide enthalten einen Settings-Hash, aber unterschiedlich.
        import re

        h_before = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_before).group(1)
        h_after = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_after).group(1)
        assert h_before != h_after, "Settings-Aenderung muss Hash-Wechsel bewirken (Refs #840)."

    def test_document_type_override_changes_footer_hash(self, facility):
        """Refs #1762: prueft die Durchreichung von document_type_overrides
        durch render_document -> _build_footer -> _settings_hash. Die
        direkten _settings_hash-Tests uebergeben die Liste explizit und
        wuerden einen Ausfall dieser Verdrahtung nicht bemerken — nur der
        gerenderte Fussnoten-Hash zeigt, ob render_document sie tatsaechlich
        weiterreicht."""
        content_before, _ = render_document("dsfa", facility)

        from core.models import DocumentType

        DocumentType.objects.create(facility=facility, name="Vermittlung", retention_days=180)

        content_after, _ = render_document("dsfa", facility)

        import re

        h_before = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_before).group(1)
        h_after = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_after).group(1)
        assert h_before != h_after, "Dokumentationstyp-Override muss Fussnoten-Hash aendern (Refs #1762)."

    def test_backup_retention_changes_footer_hash(self, facility, monkeypatch):
        content_before, _ = render_document("loeschkonzept", facility)

        monkeypatch.setattr("core.services.client.dsgvo_package.DEFAULT_BACKUP_RETENTION_DAYS", 120)
        content_after, _ = render_document("loeschkonzept", facility)

        h_before = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_before).group(1)
        h_after = re.search(r"Settings-Hash: ([0-9a-f]{8})", content_after).group(1)
        assert h_before != h_after, "Backup-Frist muss Fussnoten-Hash aendern (Refs #2050)."


def _effect_cell(content: str, label_part: str) -> str:
    """Last cell ("Wirkung bei Ablauf") of the first table row whose label contains ``label_part``.

    Works for both retention tables: the numbered one in dsfa.md § 2.3 and the
    unnumbered one in loeschkonzept.md § 2.
    """
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        labels = cells[:2]
        if any(label_part in label for label in labels):
            return cells[-1]
    raise AssertionError(f"no table row containing {label_part!r}")


def _flat(content: str) -> str:
    """Rendered document with all whitespace runs collapsed, so prose checks ignore line wrapping."""
    return " ".join(content.split())


@pytest.mark.django_db
class TestDocumentTypePeriodIsAdditional:
    """Refs #2462: a document-type period never extends the contact-stage period.

    ``process_facility_retention`` runs all strategies one after another and an
    event falls as soon as the first one matches; the templates therefore must
    not call the document-type period an override.
    """

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_effect_cell_describes_earliest_expiring_period(self, facility, slug):
        content, _ = render_document(slug, facility)
        effect = _effect_cell(content, "Höchstfrist (verkürzt nur) einzelner Dokumentationstypen")

        assert "Überschreibt" not in effect
        assert "zuerst ablaufende" in effect
        assert "verlängert" in effect

    def test_longer_document_type_period_does_not_protect_identified_event(
        self, facility, client_identified, staff_user
    ):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import DocumentType, Event
        from core.retention.enforcement import process_facility_retention

        long_type = DocumentType.objects.create(facility=facility, name="Beratungsgespräch", retention_days=3650)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=long_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={},
            is_anonymous=False,
            created_by=staff_user,
        )

        process_facility_retention(facility, facility.settings, timezone.now(), dry_run=False)

        event.refresh_from_db()
        assert event.is_deleted is True


def _backup_rotation_days() -> list[int]:
    """Distinct ``-mtime +N`` rotation ages of the shipped ``backup.sh`` (ascending)."""
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backup.sh"
    return sorted({int(days) for days in re.findall(r"-mtime \+(\d+) -delete", script.read_text(encoding="utf-8"))})


@pytest.mark.django_db
class TestBackupDeletionBoundary:
    """Refs #2464: templates promise only the local rotation ``backup.sh`` performs."""

    def test_deletion_concept_separates_local_rotation_from_offsite_copies(self, facility):
        content, _ = render_document("loeschkonzept", facility)

        offsite = _effect_cell(content, "Off-Site-Kopien der Sicherungsstände")
        assert "`backup.sh` löscht am Off-Site-Ziel nichts" in offsite
        assert "ausgesetzt" in _effect_cell(content, "Backup-Sicherungsstände")
        prose = _flat(content)
        assert "setzt `backup.sh` die lokale Rotation aus" in prose
        assert "ausgesetzte lokale Rotation" in prose
        assert "spätestens mit der planmäßigen Rotation des letzten sie enthaltenden Sicherungsstands" not in prose

    def test_toms_name_local_rotation_and_offsite_policy(self, facility):
        content = _flat(render_document("toms", facility)[0])

        assert "Lokale Rotation" in content
        assert "Off-Site-Kopien löscht `backup.sh` nicht" in content

    @pytest.mark.architecture
    def test_documented_rotation_matches_backup_script(self, facility):
        from core.services.compliance import get_activity

        days = _backup_rotation_days()
        assert days == [7, 28, 90], days
        # ``find -mtime +N`` drops the fractional day, so it only selects
        # archives that are at least N + 1 full days old.
        minimum_ages = [day + 1 for day in days]
        expected = "/".join(str(age) for age in minimum_ages)

        toms = _flat(render_document("toms", facility)[0])
        deletion_concept, _ = render_document("loeschkonzept", facility)
        vvt_backup = _flat(str(get_activity("backup")["retention_period"]))

        assert f"mindestens {expected} vollen Tagen" in toms
        local_effect = _effect_cell(deletion_concept, "Backup-Sicherungsstände")
        for age, label in zip(minimum_ages, ("täglich", "wöchentlich", "monatlich"), strict=True):
            assert f"{age} ({label})" in local_effect.replace(" volle Tage", "")
            assert f"{age} ({label})" in vvt_backup.replace(" volle Tage", "")
        for day in days:
            assert f"nach {day} Tagen" not in vvt_backup

    @pytest.mark.architecture
    def test_find_mtime_selects_only_after_full_days(self, tmp_path):
        """The documented minimum age is what ``find -mtime +N`` really selects."""
        import os
        import shutil
        import subprocess
        import time

        find = shutil.which("find")
        if find is None:
            pytest.skip("find not available")
        day = 24 * 3600
        now = time.time()
        for limit in _backup_rotation_days():
            probe = tmp_path / str(limit)
            probe.mkdir()
            kept = probe / "kept"
            removed = probe / "removed"
            kept.touch()
            removed.touch()
            os.utime(kept, (now - limit * day - day / 2,) * 2)
            os.utime(removed, (now - (limit + 1) * day - 3600,) * 2)
            selected = subprocess.run(
                [find, str(probe), "-type", "f", "-mtime", f"+{limit}", "-print"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout.split()
            assert selected == [str(removed)], (limit, selected)

    def test_backup_rotation_promises_no_hard_deadline(self, facility):
        content, _ = render_document("loeschkonzept", facility)
        deletion_concept = _flat(content)
        toms = _flat(render_document("toms", facility)[0])

        assert "spätestens nach" not in deletion_concept
        assert "keine feste Höchstfrist" in _effect_cell(content, "Backup-Sicherungsstände")
        assert "nächsten erfolgreichen Lauf" in deletion_concept
        assert "Altersgrenze" in deletion_concept
        assert "keine feste Höchstfrist" in toms


@pytest.mark.django_db
class TestRetentionRunRequiresQualifiedApproval:
    """The regular run now requires approval for additional details (Refs #2524)."""

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_qualified_row_requires_approval(self, facility, slug):
        content, _ = render_document(slug, facility)
        effect = _effect_cell(content, "Stufe „Mit Zusatzangaben")
        assert "Vorschlag" in effect
        assert "Freigabe" in effect
        assert "Löschbestätigung" in effect
        assert "Fallabschluss" in effect
        assert "ohne Fall ab Ereignisdatum" in effect
        assert "letzter fachlicher Fallaktivität" in effect

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_templates_name_proposal_protection_and_opt_in(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])
        assert "Zurückstellen schützt" in content
        assert "Ablehnen" in content
        assert "365 Tagen" in content
        assert "Auto-Freigabe" in content
        assert "Vier-Augen" in content or "4-Augen" in content

    @pytest.mark.parametrize("slug", ["informationspflichten", "verarbeitungsverzeichnis"])
    def test_period_start_is_named_per_stage(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])

        assert "letztem Kontakt" not in content
        assert "ab dem jeweiligen Kontakt" in content
        assert "ab Fallabschluss" in content
        assert "ohne Fall ab" in content
        assert "letzter fachlicher" in content

    def test_information_notice_names_exceptions_of_automatic_deletion(self, facility):
        content = _flat(render_document("informationspflichten", facility)[0])

        assert "Nach Ablauf der Frist werden die Daten automatisch gelöscht." not in content
        assert "Aufbewahrungssperre" in content
        assert "Sicherungskopien" in content

    def test_deletion_concept_names_monthly_snapshots_as_residue(self, facility):
        content, _ = render_document("loeschkonzept", facility)

        effect = _effect_cell(content, "Monatliche Statistik-Aggregate")
        assert "bleiben bestehen" in effect.lower()
        assert "k-Anonymitäts-Schwelle" in _flat(content)

    def test_vvt_view_names_period_start_per_stage(self):
        from core.services.compliance import get_activity

        retention = str(get_activity("klienten_stammdaten")["retention_period"])

        assert "letztem Kontakt" not in retention
        assert "Falllauf-Ende" not in retention
        assert "ab Ereignisdatum" in retention
        assert "ab Fallabschluss" in retention
        assert "ohne Fall ab Ereignisdatum" in retention
        assert "letzter fachlicher Fallaktivität" in retention
        assert "Löschbestätigung" in retention

    def test_rejected_proposal_does_not_keep_the_event(self, facility, client_identified, staff_user):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import DocumentType, Event, RetentionProposal
        from core.retention.enforcement import process_facility_retention
        from core.retention.proposals import create_proposal

        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={},
            is_anonymous=False,
            created_by=staff_user,
        )
        proposal, _ = create_proposal(facility, "Event", event.pk, timezone.localdate(), {}, "identified")
        RetentionProposal.objects.filter(pk=proposal.pk).update(status=RetentionProposal.Status.REJECTED)

        process_facility_retention(facility, facility.settings, timezone.now(), dry_run=False)

        event.refresh_from_db()
        assert event.is_deleted is True

    def test_newer_contact_does_not_extend_older_identified_event(self, facility, client_identified, staff_user):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import DocumentType, Event
        from core.retention.enforcement import process_facility_retention

        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        old, recent = (
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type,
                occurred_at=timezone.now() - timedelta(days=days),
                data_json={},
                is_anonymous=False,
                created_by=staff_user,
            )
            for days in (400, 1)
        )

        process_facility_retention(facility, facility.settings, timezone.now(), dry_run=False)

        old.refresh_from_db()
        recent.refresh_from_db()
        assert old.is_deleted is True
        assert recent.is_deleted is False


@pytest.mark.django_db
class TestQualifiedPeriodAnchors:
    """Additional details expire through three case anchors (Refs #2524)."""

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_qualified_row_names_all_case_anchors(self, facility, slug):
        content, _ = render_document(slug, facility)

        assert "Fälle der Stufe „Mit Zusatzangaben" not in content
        effect = _effect_cell(content, "Ereignisse der Stufe „Mit Zusatzangaben")
        assert "ohne Fall ab Ereignisdatum" in effect
        assert "offenen Fall ab letzter fachlicher Fallaktivität" in effect
        assert "Freigabe" in effect

    @pytest.mark.parametrize("slug", ["informationspflichten", "verarbeitungsverzeichnis"])
    def test_stage_tables_name_all_anchors(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])

        assert "ohne Fall ab" in content
        assert "offener Fall ab letzter fachlicher" in content

    def test_vvt_view_names_case_anchors(self):
        from core.services.compliance import get_activity

        retention = str(get_activity("klienten_stammdaten")["retention_period"])

        assert "ohne Fall ab Ereignisdatum" in retention
        assert "offenen Fall ab letzter fachlicher Fallaktivität" in retention

    def test_qualified_event_without_case_gets_proposed(self, facility, client_qualified, staff_user):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import DocumentType, Event, RetentionProposal
        from core.retention.enforcement import process_facility_retention

        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        event = Event.objects.create(
            facility=facility,
            client=client_qualified,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=5000),
            data_json={},
            is_anonymous=False,
            created_by=staff_user,
        )

        process_facility_retention(facility, facility.settings, timezone.now(), dry_run=False)

        event.refresh_from_db()
        assert event.is_deleted is False

        assert RetentionProposal.objects.filter(
            facility=facility,
            target_id=event.pk,
            status=RetentionProposal.Status.PENDING,
        ).exists()


@pytest.mark.django_db
class TestRetentionExceptionsAreNamed:
    """Refs #2425: the templates name the exceptions the retention run really has.

    ``eventhistory._lock_rows`` locks with ``skip_locked`` and the run skips
    held or locked events; ``anonymize_clients`` needs at least one event and
    no active hold; ``carry_deleted_event`` adds each deleted event to its
    month's aggregate, the running month included, and later recomputations
    keep them.
    """

    @pytest.mark.parametrize("slug", ["dsfa", "loeschkonzept"])
    def test_history_redaction_names_skipped_events(self, facility, slug):
        content = _flat(render_document(slug, facility)[0])

        assert "spätestens mit dem nächsten Aufbewahrungslauf" not in content
        assert "regulär der nächste Aufbewahrungslauf" in content
        assert "von einer anderen Bearbeitung gesperrt" in content
        assert "ein späterer Lauf holt sie nach" in content

    def test_history_row_points_to_the_exceptions(self, facility):
        content, _ = render_document("loeschkonzept", facility)

        effect = _effect_cell(content, "Inhalte früherer Fassungen")
        assert "regulär mit dem nächsten Aufbewahrungslauf (Ausnahmen § 3.1)" in effect

    def test_vvt_view_names_conditions_of_client_anonymization(self):
        from core.services.compliance import get_activity

        retention = _flat(str(get_activity("klienten_stammdaten")["retention_period"]))

        assert "die Person wird anonymisiert, sobald alle ihre Ereignisse gelöscht sind." not in retention
        assert "sobald alle Ereignisse gelöscht sind" in retention
        assert "ohne Ereignis" in retention
        assert "Hold und Art. 18 stellen zurück" in retention

    def test_processing_register_template_names_eventless_inactivity(self, facility):
        content = _flat(render_document("verarbeitungsverzeichnis", facility)[0])

        assert "Ohne Ereignis anonymisiert der Lauf nach" in content
        assert "Tagen ab letzter Änderung" in content
        assert "Hold und Art. 18" in content

    def test_deletion_concept_does_not_wait_for_trash_period(self, facility):
        content = _flat(render_document("loeschkonzept", facility)[0])

        old_claim = "Für manuell gelöschte Personen und Fälle gilt zuvor die oben ausgewiesene Papierkorb-Frist."
        assert old_claim not in content
        assert "wartet nicht auf die Papierkorb-Frist" in content

    def test_snapshots_include_the_current_month(self, facility):
        content, _ = render_document("loeschkonzept", facility)
        prose = _flat(content)
        effect = _effect_cell(content, "Monatliche Statistik-Aggregate")

        assert "Vor jeder Löschung" not in prose
        assert "der laufende Monat ist ausgenommen" not in effect
        assert "auch im laufenden Monat" in effect
        assert "Den laufenden Monat sichert dieser Schritt nicht" not in prose
        assert "auch dem laufenden Monat" in prose
        assert "keine unveränderliche Archivkopie" in prose

    def test_snapshot_records_current_month_deletions(self, facility, client_identified, staff_user):
        from django.utils import timezone

        from core.models import DocumentType, Event, StatisticsSnapshot
        from core.services.dashboard.snapshot import carry_deleted_event

        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=False,
            created_by=staff_user,
            is_deleted=True,
        )
        today = timezone.localdate()

        carry_deleted_event(facility, event)

        snapshot = StatisticsSnapshot.objects.get(facility=facility, year=today.year, month=today.month)
        assert snapshot.data["total_contacts"] == 1

    def test_templates_promise_counts_of_deleted_events(self, facility):
        deletion_concept, _ = render_document("loeschkonzept", facility)
        prose = _flat(deletion_concept)
        notice = _flat(render_document("informationspflichten", facility)[0])

        assert "damit Auswertungen vergangener Zeiträume erhalten" not in prose
        assert "fehlen sie in der neuen Fassung des Aggregats" not in prose
        assert "ein Löschlauf verkleinert ein Aggregat dadurch nicht" not in prose
        assert "Die Zählwerte eines bestehenden Aggregats ändert ein Löschlauf nicht" in prose
        assert "Löschung und Übertrag werden nur gemeinsam gespeichert" in prose
        assert "löscht der Lauf dieses Ereignis nicht" in prose
        effect = _effect_cell(deletion_concept, "Monatliche Statistik-Aggregate")
        assert "früher gelöschte Ereignisse fehlen" not in effect
        assert "spätere Neuberechnungen behalten sie" in effect
        assert "Statistische Zählwerte ohne Bezug zu Ihrer Person bleiben erhalten." in notice
        assert "können erhalten bleiben" not in notice

    def test_recomputed_month_keeps_events_deleted_earlier(self, facility, client_identified, staff_user):
        from datetime import datetime, timedelta

        from django.utils import timezone

        from core.models import DocumentType, Event, StatisticsSnapshot
        from core.services.dashboard.snapshot import carry_deleted_event

        previous_month = timezone.localdate().replace(day=1) - timedelta(days=10)
        occurred_at = timezone.make_aware(datetime.combine(previous_month, datetime.min.time()))
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        first, second = (
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type,
                occurred_at=occurred_at,
                data_json={},
                is_anonymous=False,
                created_by=staff_user,
            )
            for _ in range(2)
        )

        Event.objects.filter(pk=first.pk).update(is_deleted=True)
        carry_deleted_event(facility, first)
        assert StatisticsSnapshot.objects.get(facility=facility).data["total_contacts"] == 2
        Event.objects.filter(pk=second.pk).update(is_deleted=True)
        carry_deleted_event(facility, second)

        assert StatisticsSnapshot.objects.get(facility=facility).data["total_contacts"] == 2

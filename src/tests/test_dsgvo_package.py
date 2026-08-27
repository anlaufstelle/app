"""Tests for DSGVO documentation package."""

import re
from datetime import date
from unittest import mock

import pytest
from django.test import Client as DjangoClient

from core.models import AuditLog, Facility, Organization, Settings, User
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
def facility(db):
    org = Organization.objects.create(name="DSGVO-Org")
    facility = Facility.objects.create(organization=org, name="DSGVO-Einrichtung")
    Settings.objects.create(
        facility=facility,
        facility_full_name="Anlaufstelle Musterstadt",
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )
    return facility


@pytest.fixture
def admin_user(facility):
    return User.objects.create_user(
        username="dsgvo_admin",
        password="test1234",
        role=User.Role.FACILITY_ADMIN,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def lead_user(facility):
    return User.objects.create_user(
        username="dsgvo_lead",
        password="test1234",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
    )


@pytest.fixture
def staff_user(facility):
    return User.objects.create_user(
        username="dsgvo_staff",
        password="test1234",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )


@pytest.mark.django_db
class TestDSGVOPackageService:
    """Service-level tests."""

    def test_get_document_list_returns_5(self):
        docs = get_document_list()
        assert len(docs) == 5
        slugs = [d["slug"] for d in docs]
        assert "verarbeitungsverzeichnis" in slugs
        assert "dsfa" in slugs
        assert "av-vertrag" in slugs
        assert "toms" in slugs
        assert "informationspflichten" in slugs

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

        assert sorted(rows) == [1, 2, 3, 4, 5, 6, 7], f"§ 2.3 hat nicht mehr sieben Fristen: {rows}"
        for nr, (frist, wert) in rows.items():
            assert "{{" not in wert, f"Frist {nr} ({frist}) traegt einen ungerenderten Platzhalter: {wert!r}"
            # Zeile 4 ist die einzige nicht-numerische ("keine" bzw.
            # "<Typ>: <n> Tage"); die uebrigen sechs sind "<n> Tage/Monate".
            erwartet = r"^(keine|.+: \d+ Tage)$" if nr == 4 else r"^\d+ (Tage|Monate)$"
            assert re.match(erwartet, wert), f"Frist {nr} ({frist}) ist kein Ist-Wert, sondern Prosa: {wert!r}"

        # Die alte Prosa-Fassung im Einleitungssatz ("die uebrigen vier stehen
        # mit ihrem Standardwert und sind vor Freigabe zu pruefen").
        assert "Standardwert" not in content
        assert "Alle sieben sind unten mit dem für diese Einrichtung **tatsächlich konfigurierten** Wert" in content

    def test_document_type_override_placeholder_is_replaced(self, facility):
        content, _ = render_document("dsfa", facility)
        rows = _retention_rows(content)

        assert "{{ retention_document_type_overrides }}" not in content
        # Die alte generische Beschreibung entfaellt zugunsten des Ist-Werts.
        assert "je Typ gesetzt, sonst keine" not in content
        # Refs #1775: Kein Dokumentationstyp mit eigener Frist -> die ZELLE
        # sagt "keine". ``"keine" in content`` war vakuum-wahr: die Prosa unter
        # der Tabelle enthaelt "greift **keine** der sieben Fristen".
        assert rows[4] == ("Abweichende Frist einzelner Dokumentationstypen", "keine")


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
            "Abweichende Frist einzelner Dokumentationstypen",
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
            "Abweichende Frist einzelner Dokumentationstypen",
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

"""Tests für DocumentType.system_type — Immutabilität, Rename-Safety, None-Verhalten."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.reporting.base import EXCLUDED_KEY
from core.services.system import get_active_bans
from tests._reporting_helpers import get_sample_report_1_statistics


@pytest.fixture
def doc_type_ban(facility):
    """DocumentType mit system_type=BAN."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )
    ft_grund = FieldTemplate.objects.create(
        facility=facility, name="Grund_st", field_type=FieldTemplate.FieldType.TEXTAREA, is_required=True
    )
    ft_bis = FieldTemplate.objects.create(facility=facility, name="Bis_st", field_type=FieldTemplate.FieldType.DATE)
    ft_aktiv = FieldTemplate.objects.create(
        facility=facility, name="Aktiv_st", field_type=FieldTemplate.FieldType.BOOLEAN
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_grund, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_bis, sort_order=1)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_aktiv, sort_order=2)
    return doc_type


@pytest.fixture
def doc_type_contact(facility):
    """DocumentType mit dem Berichtsschluessel ``contact`` — ohne Systemtyp.

    Seit E3 (Refs #1849) ist ``report_key`` die Erststufe der
    Berichtszuordnung, seit E4 (Refs #1850) die einzige: der frueher
    zusaetzlich gesetzte ``system_type=contact`` existiert nicht mehr.
    """
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt_st",
        report_key="contact",
    )


@pytest.mark.django_db
class TestSystemTypeImmutability:
    def test_system_type_immutable_after_creation(self, doc_type_ban):
        """system_type kann nach Erstellung nicht geändert werden."""
        doc_type_ban.system_type = "crisis"
        with pytest.raises(ValidationError, match="system_type kann nach Erstellung nicht geändert werden"):
            doc_type_ban.save()

    def test_system_type_none_stays_mutable(self, facility):
        """system_type=None darf gesetzt werden (erstmaliges Zuweisen)."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Benutzerdefiniert",
            system_type=None,
        )
        dt.system_type = "crisis"
        dt.save()  # Kein Fehler
        dt.refresh_from_db()
        assert dt.system_type == "crisis"

    def test_system_type_same_value_no_error(self, doc_type_ban):
        """Gleiches system_type nochmal setzen ist erlaubt (kein false positive)."""
        doc_type_ban.system_type = "ban"
        doc_type_ban.save()  # Kein Fehler


@pytest.mark.django_db
class TestRenameSafety:
    def test_rename_doctype_bans_still_work(self, facility, client_identified, staff_user, doc_type_ban):
        """DocumentType.name ändern → Bans funktionieren weiterhin über system_type."""
        future_date = (date.today() + timedelta(days=30)).isoformat()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_ban,
            occurred_at=timezone.now(),
            data_json={"grund": "Bedrohung", "bis": future_date, "aktiv": True},
            created_by=staff_user,
        )

        # Name ändern
        doc_type_ban.name = "Platzverweis"
        doc_type_ban.save()

        # Bans werden weiterhin über system_type gefunden
        bans = get_active_bans(facility)
        assert len(bans) == 1

    def test_rename_doctype_export_still_works(self, facility, staff_user, doc_type_contact):
        """DocumentType.name ändern → Sachbericht-Export funktioniert weiterhin.

        Der Export haengt am stabilen Schluessel, nicht am Namen — seit E3
        (Refs #1849) am ``report_key`` statt am ``system_type``. Die
        Rename-Sicherheit ist dieselbe.
        """
        # Kontakt-Event erstellen
        Event.objects.create(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        # Name ändern
        doc_type_contact.name = "Erstgespräch"
        doc_type_contact.save()

        # Export mappt weiterhin über den stabilen Schlüssel
        today = date.today()
        stats = get_sample_report_1_statistics(facility, today - timedelta(days=1), today + timedelta(days=1))
        assert stats["total"] == 1
        assert any(name == "Kontakte" for name, _ in stats["by_category"])


@pytest.mark.django_db
class TestSystemTypeNone:
    def test_none_system_type_excluded_from_bans(self, facility):
        """DocumentType ohne system_type wird von Bans ignoriert."""
        bans = get_active_bans(facility)
        assert bans == []

    def test_none_system_type_excluded_from_export(self, facility, staff_user):
        """DocumentType ohne system_type wird vom Sachbericht-Export ausgeschlossen."""
        dt_custom = DocumentType.objects.create(
            facility=facility,
            name="Custom",
            system_type=None,
        )
        Event.objects.create(
            facility=facility,
            document_type=dt_custom,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        today = date.today()
        stats = get_sample_report_1_statistics(facility, today - timedelta(days=1), today + timedelta(days=1))
        assert stats["total"] == 0


class TestSystemTypeWertelisteEingefroren:
    """Freeze-Guard fuer die SystemType-Werteliste.

    RFC #1748 hatte die
    damals neun Werte eingefroren (Option **O1**, Maintainer-Entscheid
    2026-08-03). Mit Konzept
    #1847 ist die
    Richtung entschieden worden und mit E4
    (#1850) vollzogen:
    die sieben reinen Berichtswerte (``contact``, ``medical``,
    ``needle_exchange``, ``accompaniment``, ``counseling``, ``referral``,
    ``note``) sind **entfernt**; die Berichts-Zuordnung traegt seit E3 allein
    ``report_key`` (Migration ``0145`` hat den Bestand uebernommen, ``0147``
    die Altwerte genullt).

    **Redaktionsregel: keine neuen Werte ohne App-Logik — Erweiterung nur mit
    RFC-Entscheid (#1748).**
    Neue Berichts-Zuordnungen laufen ueber ``report_key`` und die
    Backend-Deklarationen, nicht ueber dieses Kern-Datenmodell-Enum.

    Hintergrund: Die neun Werte entstanden auf einen Schlag als Abbild der
    damaligen Seed-Dokumentationstypen ([ADR-024](../../docs/adr/024-slug-based-identifiers.md)),
    weil ``system_type`` vor M2 der einzige rename-sichere Schluessel war und
    deshalb auch die reine Berichts-Zuordnung tragen musste. Genau diese
    Doppelnutzung ist jetzt aufgeloest — uebrig bleibt **eine** Rolle:

    1. **Verhaltens-Schluessel** (``ban``, ``crisis``) — echte App-Logik
       (Hausverbot-Banner/Feed/Uebergabe bzw. Krisen-Highlight/Uebergabe).

    Ein zusaetzlicher Wert muesste sich dieser einen Rolle zuordnen lassen —
    das ist der Moment, in dem der RFC-Entscheid faellig wird.
    """

    EINGEFROREN = (
        "ban",
        "crisis",
    )
    MIT_APP_LOGIK = frozenset({"ban", "crisis"})

    def test_werteliste_ist_exakt_und_in_reihenfolge_gepinnt(self):
        assert tuple(DocumentType.SystemType.values) == self.EINGEFROREN

    def test_jeder_wert_hat_genau_eine_rolle(self, facility, staff_user):
        """Nur noch EINE Rolle — und der Bericht liest den Systemtyp nicht mehr.

        Die Partition ist nach E4 trivial (jeder Wert traegt App-Logik); der
        Gehalt steckt in der zweiten Haelfte: ``crisis`` heisst als
        Berichtsschluessel genauso wie als Systemtyp, ist aber eine ANDERE
        Achse. Ein Typ mit ``system_type=crisis`` und ohne ``report_key``
        zaehlt deshalb in keinem Bericht mit — waere der Systemtyp noch
        Erststufe, stuende hier eine 1.
        """
        from core.reporting.registry import get_report_key_vocabulary

        assert set(self.EINGEFROREN) == self.MIT_APP_LOGIK
        # Namensgleichheit ohne Bindung: derselbe String, zwei Achsen.
        assert "crisis" in get_report_key_vocabulary()

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Krise ohne Berichtsschlüssel",
            system_type=DocumentType.SystemType.CRISIS,
        )
        Event.objects.create(
            facility=facility,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )

        today = date.today()
        stats = get_sample_report_1_statistics(facility, today - timedelta(days=1), today + timedelta(days=1))
        assert stats["total"] == 0

    def test_ausschluss_liegt_am_report_key_nicht_mehr_am_systemtyp(self, facility, staff_user):
        """Der Ausschluss-Marker ist von ``note`` auf ``report_key=excluded`` gewandert.

        Drei Typen, ein Beleg (E4, Refs #1850): der ausdruecklich
        ausgeschlossene, ein Verhaltens-Typ ohne Berichtsschluessel und einer
        ganz ohne alles liefern denselben Beitrag zum Bericht — naemlich
        keinen. Der Unterschied liegt allein im Vollstaendigkeitshinweis: die
        beiden schluessellosen Typen sind die echte Luecke
        (#1819), der
        ausgeschlossene nicht. Ein gesetzter ``system_type`` rettet dabei
        nichts mehr — genau das ist der Vollzug dieser Etappe.
        """
        from core.reporting.registry import get_backend

        typen = (
            ("Ausgeschlossen", None, EXCLUDED_KEY),
            ("Krise ohne Schlüssel", DocumentType.SystemType.CRISIS, None),
            ("Ohne alles", None, None),
        )
        for name, system_type, report_key in typen:
            doc_type = DocumentType.objects.create(
                facility=facility,
                name=name,
                system_type=system_type,
                report_key=report_key,
            )
            Event.objects.create(
                facility=facility,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
                created_by=staff_user,
            )

        today = date.today()
        backend = get_backend("sample-report-1")
        result = backend.build_dataset(facility, today - timedelta(days=1), today + timedelta(days=1))

        assert result.summary["total"] == 0
        assert result.summary["by_category"] == []
        # Nur die beiden schluessellosen Typen sind eine Luecke.
        (issue,) = [i for i in result.completeness if i.code == "unmapped_system_types"]
        assert issue.count == 2

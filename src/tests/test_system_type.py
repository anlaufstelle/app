"""Tests für DocumentType.system_type — Immutabilität, Rename-Safety, None-Verhalten."""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
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
    """DocumentType mit system_type=CONTACT."""
    return DocumentType.objects.create(
        facility=facility,
        name="Kontakt_st",
        system_type=DocumentType.SystemType.CONTACT,
    )


@pytest.mark.django_db
class TestSystemTypeImmutability:
    def test_system_type_immutable_after_creation(self, doc_type_ban):
        """system_type kann nach Erstellung nicht geändert werden."""
        doc_type_ban.system_type = "contact"
        with pytest.raises(ValidationError, match="system_type kann nach Erstellung nicht geändert werden"):
            doc_type_ban.save()

    def test_system_type_none_stays_mutable(self, facility):
        """system_type=None darf gesetzt werden (erstmaliges Zuweisen)."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="Benutzerdefiniert",
            system_type=None,
        )
        dt.system_type = "note"
        dt.save()  # Kein Fehler
        dt.refresh_from_db()
        assert dt.system_type == "note"

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
        """DocumentType.name ändern → Sachbericht-Export funktioniert weiterhin über system_type."""
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

        # Export mappt weiterhin über system_type
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

    RFC #1748,
    Option **O1**, Maintainer-Entscheid 2026-08-03.

    **Redaktionsregel: keine neuen Werte ohne App-Logik — Erweiterung nur mit
    RFC-Entscheid (#1748).**
    Neue Berichts-Zuordnungen laufen ueber das Backend-Mapping, nicht ueber
    dieses Kern-Datenmodell-Enum.

    Hintergrund: Die neun Werte entstanden auf einen Schlag als Abbild der
    damaligen Seed-Dokumentationstypen ([ADR-024](../../docs/adr/024-slug-based-identifiers.md)),
    weil ``system_type`` vor M2 der einzige rename-sichere Schluessel war und
    deshalb auch die reine Berichts-Zuordnung tragen musste. Sie fuellen
    seitdem **drei** Rollen, und genau diese Aufteilung friert der Test ein:

    1. **Verhaltens-Schluessel** (``ban``, ``crisis``) — echte App-Logik
       (Hausverbot-Banner/Feed/Uebergabe bzw. Krisen-Highlight).
    2. **Berichts-Zuordnung** (die sechs Werte in ``CATEGORY_MAP``) — keine
       App-Logik; sie sind das einzige Opt-in, mit dem eine Einrichtung einen
       eigenen Dokumentationstyp in den Beispiel-Sachbericht bekommt.
    3. **Ausschluss-Marker** (``note``) — exportseitig identisch mit ``NULL``.

    Ein zusaetzlicher Wert wuerde die Partition unten sprengen und muesste sich
    hier einer Rolle zuordnen lassen — das ist der Moment, in dem der
    RFC-Entscheid faellig wird. Eine Reduktion (Option O2) haengt am offenen
    Produktentscheid #1741
    und wird bis dahin nicht verfolgt.
    """

    EINGEFROREN = (
        "ban",
        "contact",
        "crisis",
        "medical",
        "needle_exchange",
        "accompaniment",
        "counseling",
        "referral",
        "note",
    )
    MIT_APP_LOGIK = frozenset({"ban", "crisis"})
    AUSSCHLUSS_MARKER = frozenset({"note"})

    def test_werteliste_ist_exakt_und_in_reihenfolge_gepinnt(self):
        assert tuple(DocumentType.SystemType.values) == self.EINGEFROREN

    def test_jeder_wert_hat_genau_eine_rolle(self):
        """Partition ueber die drei Rollen — keine Luecke, keine Ueberlappung."""
        from core.reporting.backends.sample_report_1 import CATEGORY_MAP

        berichts_zuordnung = set(CATEGORY_MAP)
        alle = set(self.EINGEFROREN)

        # ``crisis`` traegt App-Logik UND eine Berichts-Zuordnung — die einzige
        # bewusste Doppelrolle. Ansonsten ist die Aufteilung disjunkt.
        assert berichts_zuordnung <= alle, "CATEGORY_MAP mappt einen unbekannten system_type"
        assert self.MIT_APP_LOGIK & berichts_zuordnung == {"crisis"}
        assert self.AUSSCHLUSS_MARKER & berichts_zuordnung == set()
        assert self.MIT_APP_LOGIK | berichts_zuordnung | self.AUSSCHLUSS_MARKER == alle

    def test_note_ist_exportseitig_identisch_mit_keinem_systemtyp(self, facility, staff_user):
        """Der Hilfetext behauptet „wie ohne Systemtyp" — hier ist der Beleg.

        Beide Typen liefern denselben Beitrag zum Bericht (naemlich keinen) und
        schlagen gleichermassen in der Vollstaendigkeits-Warnung auf. Der reale
        Unterschied liegt ausserhalb des Exports: ein gesetztes ``note`` ist
        unveraenderbar dokumentiert, ``NULL`` darf ein Paketlauf spaeter noch
        belegen.
        """
        from core.reporting.registry import get_backend

        for name, system_type in (("Notiz-Typ", DocumentType.SystemType.NOTE), ("Ohne Systemtyp", None)):
            doc_type = DocumentType.objects.create(
                facility=facility,
                name=name,
                system_type=system_type,
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
        # Beide Ereignisse zaehlen gemeinsam in die eine Warnung — kein
        # Sonderweg fuer den Marker.
        (issue,) = [i for i in result.completeness if i.code == "unmapped_system_types"]
        assert issue.count == 2

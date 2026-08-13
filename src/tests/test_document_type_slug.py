"""Tests für DocumentType.slug + entity_type (M2, Refs #1058).

Muster: ``test_fieldtemplate_slug.py`` (Slug-Generierung/Immutabilität/
Kollision), ``test_system_type.py`` (Immutabilitäts-Guard-Stil). Die
DB-Trigger-Regressionstests (Muster ``test_slug_immutable_trigger.py``, Refs
#1709) liegen in einer eigenen ``TestDocumentTypeSlugImmutableTrigger``-Klasse
in genau dieser Datei — dort.
"""

import importlib

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction

from core.models import DocumentType
from core.models.slug import _SLUG_RE

# Migrationsmodul hat einen ziffernstartigen Namen — nur via importlib ladbar
# (Muster ``test_totp_secret_at_rest.py``).
_migration = importlib.import_module("core.migrations.0117_documenttype_slug_entity_type")


@pytest.mark.django_db
class TestSlugGeneration:
    def test_auto_slug_from_name(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung")
        assert dt.slug == "beratung"

    def test_umlaut_slug(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Krisengespräch")
        assert dt.slug == "krisengespraech"

    def test_empty_slug_raises(self, facility):
        with pytest.raises(ValidationError, match="gültigen Slug"):
            DocumentType.objects.create(facility=facility, name="!!!")

    def test_full_clean_before_save_allows_empty_slug(self, facility):
        # Bestandsmuster (z. B. test_contact_stage): full_clean() auf einer
        # ungespeicherten Instanz OHNE Slug muss durchlaufen — der Slug
        # entsteht erst in save(). blank=True deckt genau diesen Pfad;
        # nach dem Speichern ist der Slug trotzdem gesetzt.
        dt = DocumentType(facility=facility, name="Validierung vor Save")
        dt.full_clean()
        dt.save()
        assert dt.slug == "validierung-vor-save"


@pytest.mark.django_db
class TestSlugCollision:
    def test_different_names_same_slug_gets_suffix(self, facility):
        dt1 = DocumentType.objects.create(facility=facility, name="Test Typ")
        dt2 = DocumentType.objects.create(facility=facility, name="Test-Typ")
        assert dt1.slug == "test-typ"
        assert dt2.slug == "test-typ-2"

    def test_cross_facility_same_slug_allowed(self, facility, other_facility):
        dt1 = DocumentType.objects.create(facility=facility, name="Beratung")
        dt2 = DocumentType.objects.create(facility=other_facility, name="Beratung")
        assert dt1.slug == dt2.slug == "beratung"


@pytest.mark.django_db
class TestSlugImmutability:
    def test_slug_immutable_after_creation(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung")
        dt.slug = "changed"
        with pytest.raises(ValidationError, match="nach Erstellung nicht geändert"):
            dt.save()

    def test_same_slug_no_error(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung")
        dt.slug = "beratung"
        dt.save()  # kein Fehler


@pytest.mark.django_db
class TestExplicitSlug:
    def test_explicit_valid_slug_accepted(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Foo", slug="custom-slug")
        assert dt.slug == "custom-slug"

    def test_explicit_invalid_slug_rejected(self, facility):
        with pytest.raises(ValidationError, match="nicht gültig"):
            DocumentType.objects.create(facility=facility, name="Foo", slug="Invalid Slug!")

    def test_duplicate_explicit_slug_same_facility_raises(self, facility):
        DocumentType.objects.create(facility=facility, name="A", slug="same-slug")
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentType.objects.create(facility=facility, name="B", slug="same-slug")


@pytest.mark.django_db
class TestEntityTypeImmutability:
    def test_event_to_client_raises(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung", entity_type=DocumentType.EntityType.EVENT)
        dt.entity_type = DocumentType.EntityType.CLIENT
        with pytest.raises(ValidationError, match="entity_type kann nach Erstellung nicht geändert werden"):
            dt.save()

    def test_client_to_event_raises(self, facility):
        dt = DocumentType.objects.create(
            facility=facility, name="Person-Dok", entity_type=DocumentType.EntityType.CLIENT
        )
        dt.entity_type = DocumentType.EntityType.EVENT
        with pytest.raises(ValidationError, match="entity_type kann nach Erstellung nicht geändert werden"):
            dt.save()

    def test_same_entity_type_no_error(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung")
        dt.entity_type = DocumentType.EntityType.EVENT
        dt.save()  # kein Fehler

    def test_default_entity_type_is_event(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Beratung")
        assert dt.entity_type == DocumentType.EntityType.EVENT


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestDocumentTypeSlugImmutableTrigger:
    """DB-Trigger (Migration 0117), Muster ``test_slug_immutable_trigger.py``
    (Refs #1709) — schließt ORM-Bypasses, die an ``save()`` vorbeigehen."""

    def _create(self, facility, **kwargs):
        return DocumentType.objects.create(facility=facility, name="Beratung", **kwargs)

    def test_raw_update_of_slug_is_blocked(self, facility):
        dt = self._create(facility)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE core_documenttype SET slug = %s WHERE id = %s", ["anderer-slug", str(dt.pk)])
        assert "immutable" in str(excinfo.value).lower()

    def test_queryset_update_of_slug_is_blocked(self, facility):
        dt = self._create(facility)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            DocumentType.objects.filter(pk=dt.pk).update(slug="anderer-slug")
        assert "immutable" in str(excinfo.value).lower()

    def test_bulk_update_of_slug_is_blocked(self, facility):
        dt = self._create(facility)
        dt.slug = "anderer-slug"
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            DocumentType.objects.bulk_update([dt], ["slug"])
        assert "immutable" in str(excinfo.value).lower()

    def test_update_of_name_is_allowed(self, facility):
        dt = self._create(facility)
        DocumentType.objects.filter(pk=dt.pk).update(name="Neuer Name")
        dt.refresh_from_db()
        assert dt.name == "Neuer Name"

    def test_insert_is_allowed(self, facility):
        before = DocumentType.objects.filter(facility=facility).count()
        self._create(facility)
        after = DocumentType.objects.filter(facility=facility).count()
        assert after == before + 1

    def test_raw_update_with_identical_slug_is_allowed(self, facility):
        dt = self._create(facility)
        original_slug = dt.slug
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("UPDATE core_documenttype SET slug = %s WHERE id = %s", [dt.slug, str(dt.pk)])
        dt.refresh_from_db()
        assert dt.slug == original_slug


class TestBackfillFunction:
    """Ruft ``backfill_document_type_slugs`` direkt gegen konstruierte
    Bestände auf (Django-``apps.get_model``-Muster).

    Die echte Migration 0117 backfüllt, WÄHREND weder die Unique-Constraint
    ``unique_facility_documenttype_slug`` noch der Slug-Immutable-Trigger
    existieren (Reihenfolge in der Migrationsdatei: AddField → RunPython-
    Backfill → AddConstraint → RunSQL-Trigger). Diese Tests laufen aber gegen
    eine bereits voll migrierte Test-DB — Setup/Backfill klammern daher
    Constraint+Trigger testweise aus (DDL ist in Postgres transaktional, der
    Rollback am Testende von ``pytest.mark.django_db`` stellt beides wieder
    her) und bilden damit exakt den historischen Migrations-Zustand nach.
    ``bulk_create`` umgeht zusätzlich ``save()`` (reines INSERT statt der
    Modell-Slug-Generierung), um den Vor-Backfill-Zustand (leerer Slug für
    alle Zeilen) direkt zu setzen.

    Die Constraint wird NICHT innerhalb derselben Transaktion nach dem
    Backfill wieder angelegt (Postgres verweigert ``ALTER TABLE`` auf einer
    Tabelle mit offenen Trigger-Events aus vorangegangenem DML in derselben
    Transaktion — ``ObjectInUse: pending trigger events``). Die Eindeutigkeit
    wird stattdessen direkt über die resultierenden ``slug``-Werte geprüft —
    das deckt exakt das ab, was die Constraint in Produktion durchsetzt, und
    der Backfill selbst pinnt seinen Fail-loud-Assert separat (siehe unten).
    """

    def _drop_constraint_and_disable_trigger(self):
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE core_documenttype DROP CONSTRAINT unique_facility_documenttype_slug")
            cur.execute("ALTER TABLE core_documenttype DISABLE TRIGGER documenttype_slug_immutable")

    def _run_backfill(self):
        from django.apps import apps as real_apps

        _migration.backfill_document_type_slugs(real_apps, None)

    @pytest.mark.django_db
    def test_backfill_deterministic_and_unique(self, facility, other_facility):
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create(
            [
                DocumentType(facility=facility, name="Alpha", slug=""),
                DocumentType(facility=facility, name="Beta", slug=""),
                DocumentType(facility=other_facility, name="Alpha", slug=""),
            ]
        )

        self._run_backfill()

        slugs = {dt.name: dt.slug for dt in DocumentType.objects.filter(facility=facility)}
        assert slugs == {"Alpha": "alpha", "Beta": "beta"}
        assert DocumentType.objects.get(facility=other_facility, name="Alpha").slug == "alpha"
        # B-5 (Fix-Runde 1): entity_type="event" ist der AddField-Default aus
        # Migration 0117 — hier ueber Zeilen belegt, die (wie echter Bestand
        # vor 0117) ohne explizites entity_type angelegt wurden.
        for dt in DocumentType.objects.filter(facility=facility):
            assert dt.entity_type == "event"

    @pytest.mark.django_db
    def test_backfill_collision_gets_counter_per_facility(self, facility):
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create(
            [
                DocumentType(facility=facility, name="Test Typ", slug=""),
                DocumentType(facility=facility, name="Test-Typ", slug=""),
            ]
        )

        self._run_backfill()

        slugs = sorted(DocumentType.objects.filter(facility=facility).values_list("slug", flat=True))
        assert slugs == ["test-typ", "test-typ-2"]

    @pytest.mark.django_db
    def test_backfill_orders_by_name_not_physical_insert_order(self, facility):
        """B-2 (Fix-Runde 1): pinnt, WELCHE Zeile bei einer Kollision den
        Basis-Slug bekommt — waere ``order_by("facility_id", "name", "id")``
        im Backfill entfernt/veraendert, wuerde dieser Test kippen. Beide Namen
        slugifizieren zu "zeta-typ"; bewusst in der zur order_by-Zielreihenfolge
        UMGEKEHRTEN physischen Einfuege-Reihenfolge angelegt ("Zeta-Typ" zuerst)
        — Leerzeichen sortiert vor Bindestrich, "Zeta Typ" muss trotz spaeterer
        Einfuegung den Basis-Slug bekommen.

        Refs #1749: frueher stand ``category`` als Tie-Break zwischen zwei
        Zeilen mit identischem Namen — den Fall gibt es nicht mehr (die
        ``(facility, name)``-Constraint verbietet ihn), die Ordnung ueber
        ``name`` ist derselbe Mechanismus an der Stelle, an der er noch
        beobachtbar ist.
        """
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create(
            [
                DocumentType(facility=facility, name="Zeta-Typ", slug=""),
                DocumentType(facility=facility, name="Zeta Typ", slug=""),
            ]
        )

        self._run_backfill()

        assert DocumentType.objects.get(facility=facility, name="Zeta Typ").slug == "zeta-typ"
        assert DocumentType.objects.get(facility=facility, name="Zeta-Typ").slug == "zeta-typ-2"

    @pytest.mark.django_db
    def test_backfill_empty_name_gets_typ_fallback(self, facility):
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create([DocumentType(facility=facility, name="!!!", slug="")])

        self._run_backfill()

        dt = DocumentType.objects.get(facility=facility, name="!!!")
        assert dt.slug == "typ"

    @pytest.mark.django_db
    def test_backfill_truncates_before_counter_suffix(self, facility):
        self._drop_constraint_and_disable_trigger()
        # Refs #1749: zwei gleiche Namen sind nicht mehr zulaessig — das
        # Ausrufezeichen faellt beim Slugifizieren weg, der Basis-Slug ist also
        # weiterhin identisch und die Kollisionsaufloesung wird geprueft.
        long_name = "A" * 200
        DocumentType.objects.bulk_create(
            [
                DocumentType(facility=facility, name=long_name, slug=""),
                DocumentType(facility=facility, name="A" * 199 + "!", slug=""),
            ]
        )

        self._run_backfill()

        slugs = sorted(DocumentType.objects.filter(facility=facility).values_list("slug", flat=True))
        assert len(slugs) == 2
        assert all(len(slug) <= 120 for slug in slugs)
        assert slugs[0] != slugs[1]
        # Lexikografische Sortierung bringt den "-2"-Suffix ('-' < 'a') vor
        # den unsuffixierten Basis-Slug — Reihenfolge in ``slugs`` daher nicht
        # relevant, nur dass GENAU einer der beiden den Suffix trägt.
        assert sum(1 for slug in slugs if slug.endswith("-2")) == 1

    @pytest.mark.django_db
    def test_backfill_assert_fails_loud_on_leftover_duplicate(self, facility, monkeypatch):
        """Simuliert eine kaputte Kollisions-Aufloesung (praepariert via
        Monkeypatch), um den Fail-loud-Assert-Pfad direkt zu pinnen."""
        self._drop_constraint_and_disable_trigger()
        # Beide Namen slugifizieren identisch zu "alpha" — die (facility,
        # name)-Constraint bleibt unberührt (Namen sind unterschiedliche
        # Strings), aber die (praeparierte) Kollisions-Aufloesung soll hier
        # eigentlich einen Counter-Suffix vergeben.
        DocumentType.objects.bulk_create(
            [
                DocumentType(facility=facility, name="Alpha", slug=""),
                DocumentType(facility=facility, name="Alpha!!!", slug=""),
            ]
        )

        monkeypatch.setattr(_migration, "_next_free_slug", lambda base, used: base)

        with pytest.raises(AssertionError, match="doppelte"):
            self._run_backfill()


@pytest.mark.django_db
class TestE16SeedSlugTable:
    """E16: die 9 Seed-Namen sind alle eindeutig ⇒ kein Counter-Suffix."""

    EXPECTED = {
        "Kontakt": "kontakt",
        "Krisengespräch": "krisengespraech",
        "Medizinische Versorgung": "medizinische-versorgung",
        "Spritzentausch": "spritzentausch",
        "Begleitung": "begleitung",
        "Beratungsgespräch": "beratungsgespraech",
        "Vermittlung": "vermittlung",
        "Notiz": "notiz",
        "Hausverbot": "hausverbot",
    }

    def test_seed_names_produce_expected_slugs(self, facility):
        for name, expected_slug in self.EXPECTED.items():
            dt = DocumentType.objects.create(facility=facility, name=name)
            assert dt.slug == expected_slug, f"{name} → {dt.slug} (erwartet {expected_slug})"
            dt.delete()

    def test_seeded_facility_document_types_match(self, facility):
        from core.seed.doc_types import seed_document_types

        seed_document_types(facility)
        actual = {dt.name: dt.slug for dt in DocumentType.objects.filter(facility=facility)}
        for name, expected_slug in self.EXPECTED.items():
            assert actual[name] == expected_slug


@pytest.mark.django_db
class TestExistingDocumentTypesGetEventEntityType:
    """Bestand bekommt ``entity_type="event"`` (Migrations-Default, Refs #1058)."""

    def test_created_without_explicit_entity_type_is_event(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Bestandstyp")
        assert dt.entity_type == "event"


@pytest.mark.django_db
class TestSlugNormalization:
    """Final-Review D (Refs #1058): ``slugify`` laesst Unterstriche durch und die
    Truncation auf 120 Zeichen kann auf einem Bindestrich enden — beides
    verletzt ``_SLUG_RE`` (``[a-z0-9]+(?:-[a-z0-9]+)*``). Das Paketformat kann
    solche Slugs nie referenzieren: ``parse_package`` weist sie als
    Schema-Fehler ab, der Typ waere damit dauerhaft nicht paketfaehig."""

    def test_underscore_becomes_hyphen(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="Feld_mit_Unterstrich")
        assert dt.slug == "feld-mit-unterstrich"
        assert _SLUG_RE.fullmatch(dt.slug)

    def test_multiple_separators_collapse(self, facility):
        dt = DocumentType.objects.create(facility=facility, name="A__B -- C")
        assert dt.slug == "a-b-c"
        assert _SLUG_RE.fullmatch(dt.slug)

    def test_truncation_never_ends_on_hyphen(self, facility):
        # 119 Zeichen + Trennzeichen + Rest: der Schnitt bei 120 landet exakt
        # auf dem Bindestrich.
        dt = DocumentType.objects.create(facility=facility, name="A" * 119 + " xy")
        assert len(dt.slug) <= 120
        assert not dt.slug.endswith("-")
        assert _SLUG_RE.fullmatch(dt.slug)

    def test_counter_suffix_never_produces_double_hyphen(self, facility):
        # Refs #1749: der zweite Typ braucht einen anderen ``name`` (die
        # ``(facility, name)``-Constraint) — das Ausrufezeichen faellt beim
        # Slugifizieren weg, die Slug-Kollision bleibt also erhalten.
        long_name = "A" * 119 + " xy"
        first = DocumentType.objects.create(facility=facility, name=long_name)
        second = DocumentType.objects.create(facility=facility, name=long_name + "!")
        assert first.slug != second.slug
        for slug in (first.slug, second.slug):
            assert len(slug) <= 120
            assert _SLUG_RE.fullmatch(slug), slug

    def test_generated_slugs_are_package_referenceable(self, facility):
        """Gegenprobe gegen die Schema-Validierung, die das Paketformat nutzt."""
        from core.packages.schema import _SLUG_RE as SCHEMA_SLUG_RE

        dt = DocumentType.objects.create(facility=facility, name="Beratung_Intensiv")
        assert SCHEMA_SLUG_RE.fullmatch(dt.slug)


class TestBackfillSlugNormalization:
    """Der 0117-Backfill muss dieselbe Normalisierung fahren wie das Modell —
    sonst traegt Bestand Slugs, die das Paketformat nie ausdruecken kann."""

    def _drop_constraint_and_disable_trigger(self):
        with connection.cursor() as cur:
            cur.execute("ALTER TABLE core_documenttype DROP CONSTRAINT unique_facility_documenttype_slug")
            cur.execute("ALTER TABLE core_documenttype DISABLE TRIGGER documenttype_slug_immutable")

    def _run_backfill(self):
        from django.apps import apps as real_apps

        _migration.backfill_document_type_slugs(real_apps, None)

    @pytest.mark.django_db
    def test_backfill_normalizes_underscores(self, facility):
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create([DocumentType(facility=facility, name="Feld_mit_Unterstrich", slug="")])

        self._run_backfill()

        dt = DocumentType.objects.get(facility=facility, name="Feld_mit_Unterstrich")
        assert dt.slug == "feld-mit-unterstrich"
        assert _SLUG_RE.fullmatch(dt.slug)

    @pytest.mark.django_db
    def test_backfill_truncation_never_ends_on_hyphen(self, facility):
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create([DocumentType(facility=facility, name="A" * 119 + " xy", slug="")])

        self._run_backfill()

        slug = DocumentType.objects.get(facility=facility).slug
        assert len(slug) <= 120
        assert _SLUG_RE.fullmatch(slug), slug

    @pytest.mark.django_db
    def test_backfill_keeps_e16_seed_slugs_unchanged(self, facility):
        """Beweis, dass die Normalisierung den Bestand NICHT umschreibt: die
        neun E16-Seed-Namen ergeben exakt dieselben Slugs wie zuvor."""
        self._drop_constraint_and_disable_trigger()
        DocumentType.objects.bulk_create(
            [DocumentType(facility=facility, name=name, slug="") for name in TestE16SeedSlugTable.EXPECTED]
        )

        self._run_backfill()

        actual = {dt.name: dt.slug for dt in DocumentType.objects.filter(facility=facility)}
        assert actual == TestE16SeedSlugTable.EXPECTED

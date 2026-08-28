"""Schema-Hygiene-Buendel (Refs #2068).

Fuenf unabhaengige Befunde, die alle am Schema haengen und deshalb in einer
Migration (``0167_schema_hygiene``) zusammenlaufen:

(a) ``Facility.name`` war nicht eindeutig. Vier Management-Kommandos schlagen
    ueber den Namen nach: ``enforce_retention``, ``detect_breaches`` und
    ``create_statistics_snapshots`` per ``.filter(name=…)`` — die verarbeiteten
    bei einem Doppelnamen still **alle** Treffer —, ``generate_dsgvo_package``
    per ``.get(name=…)``, das mit ``MultipleObjectsReturned`` abgestuerzt waere.
    Ein UNIQUE heilt alle vier auf einmal.
(b) ``QuickTemplate.document_type`` war CASCADE: das Loeschen eines
    Dokumentationstyps riss die Schnellvorlagen still mit. Alle anderen
    Konfigurations-Referenzen auf ``DocumentType`` sind PROTECT und werden im
    Loeschdialog als Blocker ausgewiesen.
(c) ``RectificationRequest.target_client`` war CASCADE — im Widerspruch zur
    eigenen Vier-Augen-Doktrin und zum Praezedenzfall ``Client.merged_into``
    (PROTECT). Ein Art.-16-Antrag ist ein Rechenschaftsbeleg; er darf nicht
    dadurch verschwinden, dass jemand die Person hart loescht.
(d) ``RecentClientVisit.visited_at`` war ``auto_now``: jeder Aufruf einer
    Personenakte schrieb die Zeile neu, auch beim Neuladen im Sekundentakt —
    reiner Dead-Tuple-Churn auf einer Tabelle, die nur der Dashboard-Widget
    liest.
(e) ``TimeFilter.is_default`` wurde ausschliesslich vom Seed geschrieben und
    nirgends gelesen; ``current_tally_context`` faellt bei "keine aktive
    Schicht" auf den vollen Tag zurueck, nicht auf die Default-Schicht. Das
    Feld widersprach damit seinem eigenen help_text.

(f) UUIDv4→v7 ist im Issue eine reine Feststellung ohne Aenderung und hat
    deshalb keinen Test.
"""

import importlib
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from core.constants import RECENT_VISIT_TOUCH_THROTTLE_MINUTES
from core.models import Facility, QuickTemplate, RecentClientVisit, RectificationRequest, TimeFilter
from core.services.client.main import anonymize_client, track_client_visit
from core.services.config.delete_blockers import ConfigDeleteBlocked, delete_document_type

MIGRATION = importlib.import_module("core.migrations.0167_schema_hygiene")


class _SchemaEditorStub:
    """Reicht nur ``connection`` durch — mehr nutzt der Guard nicht."""

    def __init__(self, conn):
        self.connection = conn


def _unique_constraint_on(table: str, column: str) -> str:
    """Namen des UNIQUE-Constraints ueber genau dieser einen Spalte holen.

    Django benennt den Constraint aus ``unique=True`` mit einem Hash-Anteil;
    ein Literal im Test waere ein Wartungsstolperstein.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
            WHERE c.conrelid = %s::regclass AND c.contype = 'u'
              AND array_length(c.conkey, 1) = 1 AND a.attname = %s
            """,
            [table, column],
        )
        row = cursor.fetchone()
    assert row, f"kein einspaltiger UNIQUE-Constraint auf {table}.{column}"
    return row[0]


# --- (a) Facility.name UNIQUE ------------------------------------------------


@pytest.mark.django_db
class TestFacilityNameUnique:
    def test_zweite_einrichtung_mit_gleichem_namen_wird_abgelehnt(self, organization, facility):
        with pytest.raises(IntegrityError), transaction.atomic():
            Facility.objects.create(organization=organization, name=facility.name)

    def test_gleicher_name_auch_ueber_organisationsgrenzen_hinweg_abgelehnt(self, organization, facility):
        """Die vier Kommandos schlagen **global** nach, nicht je Organisation —
        ein je-Organisation-UNIQUE heilte sie deshalb nicht."""
        other_org = type(organization).objects.create(name="Zweiter Träger")
        with pytest.raises(IntegrityError), transaction.atomic():
            Facility.objects.create(organization=other_org, name=facility.name)


@pytest.mark.postgres
@pytest.mark.django_db
class TestFacilityNameGuard0167:
    """Der Preflight meldet Doppelnamen im Bestand fail-loud mit Klartext.

    Duplikate lassen sich nach der Migration nicht mehr anlegen — der Test
    nimmt den Constraint deshalb innerhalb eines Savepoints weg. PostgreSQL
    faehrt DDL transaktional, der Rollback stellt ihn vollstaendig wieder her.
    """

    def test_guard_ist_still_ohne_duplikate(self, facility, other_facility):
        MIGRATION.guard_no_duplicate_facility_names(None, _SchemaEditorStub(connection))

    def test_guard_meldet_doppelten_namen_im_klartext(self, organization, facility):
        constraint = _unique_constraint_on("core_facility", "name")
        with transaction.atomic():
            with connection.cursor() as cursor:
                # Die Fixtures haben aufgeschobene FK-Trigger offen; ein ALTER
                # TABLE lehnt Postgres dann mit "pending trigger events" ab.
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                cursor.execute(f"ALTER TABLE core_facility DROP CONSTRAINT {constraint}")
            Facility.objects.create(organization=organization, name=facility.name)
            with pytest.raises(RuntimeError, match=facility.name):
                MIGRATION.guard_no_duplicate_facility_names(None, _SchemaEditorStub(connection))
            transaction.set_rollback(True)


@pytest.mark.django_db
class TestKommandosMeldenUnbekannteEinrichtung:
    """Nach dem UNIQUE ist der Mehrfach-Treffer unmoeglich; bleibt der
    0-Treffer-Fall. Alle vier Kommandos muessen ihn benennen, statt still
    nichts zu tun (``.filter``) oder mit ``DoesNotExist`` abzustuerzen."""

    def test_enforce_retention(self, facility, settings_obj):
        stderr = StringIO()
        call_command("enforce_retention", "--facility", "gibts-nicht", stderr=stderr)
        assert "gibts-nicht" in stderr.getvalue() and "not found" in stderr.getvalue()

    def test_detect_breaches(self, facility):
        stderr = StringIO()
        call_command("detect_breaches", "--facility=gibts-nicht", stdout=StringIO(), stderr=stderr)
        assert "gibts-nicht" in stderr.getvalue() and "not found" in stderr.getvalue()

    def test_create_statistics_snapshots(self, facility, settings_obj):
        stderr = StringIO()
        call_command("create_statistics_snapshots", "--facility", "gibts-nicht", stdout=StringIO(), stderr=stderr)
        assert "gibts-nicht" in stderr.getvalue() and "not found" in stderr.getvalue()

    def test_generate_dsgvo_package(self, facility, tmp_path):
        with pytest.raises(CommandError, match="gibts-nicht"):
            call_command(
                "generate_dsgvo_package", "--facility", "gibts-nicht", "--output-dir", str(tmp_path), stdout=StringIO()
            )


# --- (b) QuickTemplate.document_type PROTECT ---------------------------------


@pytest.mark.django_db
class TestQuickTemplateProtect:
    def test_on_delete_ist_protect(self):
        from django.db import models as dj_models

        field = QuickTemplate._meta.get_field("document_type")
        assert field.remote_field.on_delete is dj_models.PROTECT

    def test_dokumentationstyp_mit_schnellvorlage_ist_blockiert(self, rf, facility, doc_type_contact, admin_user):
        QuickTemplate.objects.create(
            facility=facility, document_type=doc_type_contact, name="Beratung 30 Min", created_by=admin_user
        )
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ConfigDeleteBlocked) as excinfo:
            delete_document_type(request, doc_type_contact)
        assert any("Schnellvorlage" in reason for reason in excinfo.value.reasons), excinfo.value.reasons
        assert QuickTemplate.objects.filter(document_type=doc_type_contact).exists()


# --- (c) RectificationRequest.target_client PROTECT --------------------------


def _rectification(facility, client, user, *, field_path="pseudonym"):
    return RectificationRequest.objects.create(
        facility=facility, target_client=client, field_path=field_path, requested_by=user
    )


@pytest.mark.django_db
class TestRectificationTargetClientProtect:
    def test_on_delete_ist_protect(self):
        from django.db import models as dj_models

        field = RectificationRequest._meta.get_field("target_client")
        assert field.remote_field.on_delete is dj_models.PROTECT

    def test_offener_antrag_blockiert_hard_delete(self, facility, client_identified, staff_user):
        _rectification(facility, client_identified, staff_user)
        with pytest.raises(ProtectedError), transaction.atomic():
            client_identified.delete()
        assert RectificationRequest.objects.filter(target_client=client_identified).exists()

    def test_ohne_antrag_bleibt_hard_delete_moeglich(self, client_identified):
        """Gegenprobe: der Antrag ist der Blocker, nicht irgendeine andere Kante."""
        client_identified.delete()

    def test_anonymisierung_bricht_nicht_und_redigiert_den_antrag(self, facility, client_identified, staff_user):
        """Der DSGVO-Soft-Pfad bleibt gangbar — er laeuft ueber UPDATE, nicht
        ueber DELETE, und war deshalb nie von der Kante abhaengig."""
        req = _rectification(facility, client_identified, staff_user)
        anonymize_client(client_identified, staff_user)
        req.refresh_from_db()
        assert req.old_value is None and req.new_value is None and req.reason is None
        assert req.target_client_id == client_identified.pk


# --- (d) RecentClientVisit.visited_at mit Throttle ---------------------------


def _xmin(pk):
    """Postgres-Tupelversion: aendert sich genau dann, wenn die Zeile neu
    geschrieben wurde. Praeziser als ein Zeitstempel-Vergleich, der einen
    Rewrite auf denselben Wert nicht sehen wuerde."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT xmin::text FROM core_recentclientvisit WHERE id = %s", [str(pk)])
        return cursor.fetchone()[0]


@pytest.mark.postgres
@pytest.mark.django_db
class TestRecentClientVisitThrottle:
    def test_visited_at_ist_nicht_mehr_auto_now(self):
        assert RecentClientVisit._meta.get_field("visited_at").auto_now is False

    def test_erster_besuch_stempelt(self, staff_user, client_identified, facility):
        before = timezone.now()
        track_client_visit(staff_user, client_identified, facility)
        visit = RecentClientVisit.objects.get(user=staff_user, client=client_identified)
        assert visit.visited_at >= before

    def test_zweiter_besuch_innerhalb_der_drosselung_schreibt_die_zeile_nicht_neu(
        self, staff_user, client_identified, facility
    ):
        track_client_visit(staff_user, client_identified, facility)
        visit = RecentClientVisit.objects.get(user=staff_user, client=client_identified)
        stamp_before, xmin_before = visit.visited_at, _xmin(visit.pk)

        track_client_visit(staff_user, client_identified, facility)

        visit.refresh_from_db()
        assert visit.visited_at == stamp_before
        assert _xmin(visit.pk) == xmin_before, "Zeile wurde trotz Drosselung neu geschrieben"

    def test_besuch_nach_ablauf_der_drosselung_stempelt_neu(self, staff_user, client_identified, facility):
        track_client_visit(staff_user, client_identified, facility)
        visit = RecentClientVisit.objects.get(user=staff_user, client=client_identified)
        stale = timezone.now() - timedelta(minutes=RECENT_VISIT_TOUCH_THROTTLE_MINUTES + 1)
        RecentClientVisit.objects.filter(pk=visit.pk).update(visited_at=stale)

        track_client_visit(staff_user, client_identified, facility)

        visit.refresh_from_db()
        assert visit.visited_at > stale

    def test_einrichtungswechsel_schreibt_trotz_drosselung(
        self, staff_user, client_identified, facility, other_facility
    ):
        """Die Drosselung darf nur den Zeitstempel unterdruecken — eine echte
        Feldaenderung muss durchgehen, sonst verliert die Zeile ihren Scope."""
        track_client_visit(staff_user, client_identified, facility)
        visit = RecentClientVisit.objects.get(user=staff_user, client=client_identified)

        track_client_visit(staff_user, client_identified, other_facility)

        visit.refresh_from_db()
        assert visit.facility_id == other_facility.pk


# --- (e) TimeFilter.is_default entfernt --------------------------------------


@pytest.mark.postgres
@pytest.mark.django_db
class TestTimeFilterIsDefaultEntfernt:
    def test_feld_existiert_nicht_mehr(self):
        assert "is_default" not in {f.name for f in TimeFilter._meta.get_fields()}

    def test_spalte_existiert_nicht_mehr(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'core_timefilter' AND column_name = 'is_default'"
            )
            assert cursor.fetchone() is None

    def test_bootstrap_legt_die_drei_standardschichten_an(self, facility):
        from core.services.facility_bootstrap import ensure_default_time_filters

        created = ensure_default_time_filters(facility)
        assert created is not None
        assert [tf.label for tf in created] == ["Frühdienst", "Spätdienst", "Nachtdienst"]

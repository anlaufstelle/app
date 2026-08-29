"""Pin-Tests aus der Survivor-Triage zum v0.89.0-Cut (Refs #1953).

Der ``mutation-delta``-Lauf gegen BASE ``v0.86.0`` liess in vier als
Prioritaet 1 eingestuften Funktionen Mutanten ueberleben (Loeschpfad,
Duplikat-Merge, Retention-Check, Anhang-Typwechsel). Diese Datei pinnt
GENAU die Luecken, die dabei sichtbar wurden — je Test steht in der
Docstring, welchen Mutanten er toetet und warum.

Warum eine eigene Datei statt Streuung in die Bestandsdateien: die
Zuordnung Mutant -> Test ist der Nachweis, den die Cut-Policy verlangt
(``docs/dev/release-checklist.md`` § 0.5(3)). In den Bestandsdateien
waere sie nach dem naechsten Refactoring nicht mehr auffindbar; die
fachlichen Invarianten selbst bleiben dort, wo sie hingehoeren.

**Konvention Meldungstexte:** Die Zusicherungen vergleichen den
Meldungstext EXAKT (``==``), nicht per Teilstring. Das ist Absicht:
mutmut erzeugt zu jedem String-Literal u.a. die Variante
``"XX<text>XX"`` — ein Teilstring-Vergleich (``in``) findet den
Originaltext darin weiterhin und wuerde den Mutanten ueberleben lassen.
Der Preis ist, dass eine bewusste Umformulierung diese Tests mit
anpassen muss; das ist bei einer Ablehnungs-Begruendung im Loeschpfad
gewollt — sie ist der einzige Hinweis, WARUM die Tilgung verweigert
wurde.

Der Sprachkontext wird explizit auf Deutsch festgenagelt
(``translation.override("de")``), weil ``gettext_lazy`` erst beim
``str()`` aufloest und die Suite auch englische Ausgaben kennt.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone, translation

from core.models import AuditLog, Case, Client, LegalHold
from core.services.case.deletion import anonymize_case_now, soft_delete_case
from core.services.client.merge import merge_clients, unmerge_client
from core.services.compliance import _retention_checks
from core.services.compliance._types import ComplianceStatus
from core.services.system import bypass_replication_triggers

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# core.services.case.deletion.anonymize_case_now — Ablehnungs-Begruendungen
# ---------------------------------------------------------------------------


@pytest.fixture
def trashed_case(case_open, staff_user):
    soft_delete_case(case_open, staff_user)
    case_open.refresh_from_db()
    return case_open


class TestAnonymizeCaseNowNamesItsRefusalReason:
    """Welcher der drei Guards abgelehnt hat, muss am Text erkennbar bleiben.

    Die Bestandstests (``test_case_deletion_workflow.py``) pruefen nur
    ``pytest.raises(ValidationError)``. Damit steht fest, DASS abgelehnt
    wird — die Guard-Bedingungen selbst sind dadurch gepinnt (ihre
    Negations-Mutanten sterben daran). Offen blieb, WELCHE Begruendung
    die Ablehnung nennt: alle elf ueberlebenden Mutanten dieser Funktion
    tauschen ausschliesslich den Meldungstext.

    Das ist im Loeschpfad kein Kosmetik-Detail. Die drei Ablehnungen
    verlangen vom Admin drei verschiedene Reaktionen — Fall erst in den
    Papierkorb legen, gar nichts tun (schon getilgt), oder den Legal Hold
    klaeren. Eine vertauschte oder leere Begruendung schickt ihn in die
    falsche Richtung; bei der Aufbewahrungssperre waere die falsche
    Richtung ein Spoliations-Versuch.
    """

    def test_case_outside_the_trash_says_so(self, case_open, admin_user):
        """Toetet ``x_anonymize_case_now__mutmut_2/3/4/5/6``.

        Die fuenf Mutanten ersetzen den Text des ersten Guards durch
        ``None``, ``_(None)``, die ``XX``-markierte Variante sowie die
        klein- und grossgeschriebene Fassung. Der exakte Vergleich
        unterscheidet alle fuenf vom Original.
        """
        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                anonymize_case_now(case_open, admin_user)
            assert exc.value.messages == ["Nur Fälle im Papierkorb können anonymisiert werden."]

    def test_already_anonymized_case_says_so(self, trashed_case, admin_user):
        """Toetet ``x_anonymize_case_now__mutmut_8/9/10/11/12``.

        Dieselben fuenf Varianten, hier am zweiten Guard
        (``anonymized_at is not None``). Der Fall wird zuerst reguler
        getilgt, damit der Guard ueberhaupt greift.
        """
        anonymize_case_now(trashed_case, admin_user)
        trashed_case.refresh_from_db()

        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                anonymize_case_now(trashed_case, admin_user)
            assert exc.value.messages == ["Der Fall wurde bereits endgültig anonymisiert."]

    def test_legal_hold_says_so(self, facility, trashed_case, lead_user, admin_user):
        """Toetet ``x_anonymize_case_now__mutmut_16``.

        Der Mutant markiert den Text der Legal-Hold-Ablehnung als
        ``"XX...XX"``. Gerade hier traegt der Wortlaut die
        Rechtsinformation: die Tilgung ist nicht kaputt, sondern
        gesperrt — ohne diesen Hinweis wirkte die Ablehnung wie ein
        Fehler, den man umgehen darf.
        """
        LegalHold.objects.create(
            facility=facility,
            target_type="Case",
            target_id=trashed_case.pk,
            reason="Gerichtsverfahren",
            created_by=lead_user,
        )

        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                anonymize_case_now(trashed_case, admin_user)
            assert exc.value.messages == [
                "Für diesen Fall besteht eine Aufbewahrungssperre (Legal Hold) — Anonymisierung nicht möglich."
            ]
        trashed_case.refresh_from_db()
        assert trashed_case.anonymized_at is None


# ---------------------------------------------------------------------------
# core.services.client.merge._merge_inventory — Auswahl der Nachweis-Zeile
# ---------------------------------------------------------------------------


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin-Ziel",
        created_by=staff_user,
    )


def _decoy_detail(target):
    """Ein formal gueltiges, aber LEERES Merge-Inventar.

    ``merged_into`` passt, damit die Konsistenzpruefung in
    ``_merge_inventory`` es durchwinkt — greift ein Mutant zu dieser
    Zeile, laeuft das Undo also durch und bewegt nur nichts. Genau
    dieser stille Ausgang ist der gefaehrliche: die Quelle stuende
    danach als leeres Duplikat wiederhergestellt da, waehrend ihre
    Faelle beim Ziel blieben.
    """
    return {
        "merged_into": str(target.pk),
        "moved_cases": [],
        "moved_events": [],
        "moved_workitems": [],
        "moved_attachments": [],
        "adopted_slugs": [],
        "document_type_adopted": False,
        "target_stage_before": None,
    }


class TestMergeInventoryPicksTheRightAuditRow:
    """Das Undo-Inventar muss aus GENAU der eigenen Merge-Zeile kommen.

    ``_merge_inventory`` waehlt die juengste ``CLIENT_MERGED``-Zeile
    ueber vier Filter (``facility``, ``action``, ``target_type``,
    ``target_id``) und nimmt deren ``detail`` als Umkehr-Anweisung. Die
    Bestandstests decken nur die beiden Totalausfaelle ab: gar keine
    Zeile (``test_unmerge_refuses_missing_merge_audit_row``) und eine
    Zeile mit unpassendem ``merged_into``
    (``test_unmerge_refuses_mismatched_merge_inventory``).

    Ungetestet blieb der Fall, auf den es bei einer Auswahl-Query
    ankommt: eine ANDERE Zeile ist vorhanden, neuer, und stimmt in
    allen Filtern bis auf einen ueberein. Genau dort sassen drei
    ueberlebende Mutanten, die je einen der Filter streichen.
    """

    def test_lookalike_audit_rows_do_not_supply_the_inventory(
        self, facility, other_facility, source, target, staff_user, lead_user
    ):
        """Toetet ``x__merge_inventory__mutmut_7/8/9``.

        Nach dem Merge liegen drei Koeder-Zeilen im Log, jede NEUER als
        die echte ``CLIENT_MERGED``-Zeile und jede in genau einem Filter
        abweichend:

        * ``mutmut_8`` streicht ``action=CLIENT_MERGED`` — dann gewinnt
          die ``CLIENT_UPDATE``-Zeile derselben Person (Koeder B). Das
          ist der tragende der drei: ohne den Aktions-Filter liest die
          Funktion das ``detail`` einer BELIEBIGEN Zeile ueber diese
          Person als Umkehr-Anweisung. Die ``target_id`` allein sagt nur
          „geht um diese Person", nicht „ist ein Merge-Nachweis".
        * ``mutmut_7`` streicht ``facility=source.facility`` — dann
          gewinnt die Zeile der fremden Einrichtung (Koeder A).
        * ``mutmut_9`` streicht ``target_type="Client"`` — dann gewinnt
          die ``Case``-Zeile mit derselben ``target_id`` (Koeder C).

        Bei diesen beiden ist der Filter Abwehr in der Tiefe, nicht die
        erste Verteidigungslinie: ``Client``- und ``Case``-PKs sind
        UUIDs, eine Kollision zwischen den Typen oder ueber
        Einrichtungsgrenzen hinweg entsteht im Normalbetrieb nicht. Sie
        traegt aber genau dann, wenn etwas anderes schon schiefging —
        eine reparierte Zeile, ein Import, ein fehlgeleiteter
        Fremd-Schreiber. Der Test stellt diesen Zustand her, statt sich
        darauf zu verlassen, dass er nie eintritt.

        Alle drei Koeder tragen ein leeres Inventar. Wer zu ihnen
        greift, stellt die Quelle wieder her, laesst ihren Fall aber
        beim Ziel — die Zusicherung auf ``case.client_id`` faellt dann.
        Das Original ignoriert alle drei und bewegt den Fall zurueck.
        """
        case = Case.objects.create(facility=facility, client=source, title="Fall Quelle", created_by=staff_user)

        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        case.refresh_from_db()
        assert case.client_id == target.pk, "Vorbedingung: der Merge hat den Fall zum Ziel gezogen"

        newer = timezone.now() + timedelta(hours=1)
        detail = _decoy_detail(target)
        # Koeder A — andere Einrichtung, sonst identisch (mutmut_7).
        AuditLog.objects.create(
            facility=other_facility,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Client",
            target_id=str(source.pk),
            detail=detail,
            timestamp=newer,
        )
        # Koeder B — andere Aktion, sonst identisch (mutmut_8).
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.CLIENT_UPDATE,
            target_type="Client",
            target_id=str(source.pk),
            detail=detail,
            timestamp=newer + timedelta(minutes=1),
        )
        # Koeder C — anderer Zieltyp, sonst identisch (mutmut_9).
        AuditLog.objects.create(
            facility=facility,
            action=AuditLog.Action.CLIENT_MERGED,
            target_type="Case",
            target_id=str(source.pk),
            detail=detail,
            timestamp=newer + timedelta(minutes=2),
        )

        unmerge_client(source, lead_user)

        case.refresh_from_db()
        source.refresh_from_db()
        assert case.client_id == source.pk, "Das Undo muss das ECHTE Inventar benutzt haben"
        assert source.merged_into_id is None
        assert source.is_deleted is False

    def test_missing_audit_row_names_its_reason(self, source, target, lead_user):
        """Toetet ``x__merge_inventory__mutmut_24/25/26/27/28``.

        Die fuenf Mutanten tauschen nur den Meldungstext der Ablehnung
        (``None``, ``_(None)``, ``XX``-Variante, klein, gross). Der
        Bestandstest prueft an dieser Stelle bloss ``raises``. Der
        Wortlaut ist hier die einzige Auskunft darueber, dass nicht der
        Merge kaputt ist, sondern sein Nachweis fehlt — ohne ihn wirkte
        das Undo schlicht defekt.
        """
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        # Append-Only-Bypass per Owner-DDL (Muster aus
        # ``test_client_merge_workflow.py``): der Trigger verhindert das
        # Entfernen sonst — genau dafuer ist er da.
        with bypass_replication_triggers():
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_MERGED,
                target_id=str(source.pk),
            ).delete()

        with translation.override("de"):
            with pytest.raises(ValidationError) as exc:
                unmerge_client(source, lead_user)
            assert exc.value.messages == [
                "Zu dieser Zusammenführung fehlt der Audit-Nachweis — Aufheben nicht möglich."
            ]


# ---------------------------------------------------------------------------
# core.services.compliance.retention._retention_checks — Meldung + Massnahme
# ---------------------------------------------------------------------------


def _retention_run_aged(hours):
    """Setzt einen ``RETENTION_RUN_COMPLETED``-Marker auf ``hours`` Alter."""
    AuditLog.objects.filter(action=AuditLog.Action.RETENTION_RUN_COMPLETED).delete()
    entry = AuditLog.objects.create(
        action=AuditLog.Action.RETENTION_RUN_COMPLETED,
        facility=None,
        target_type="RetentionRun",
    )
    with bypass_replication_triggers():
        AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(hours=hours))
    return entry


class TestRetentionCheckCarriesMessageAndActionHint:
    """Ein gelber/roter Check ohne Text ist kein Betriebssignal.

    Die Bestandstests in ``test_compliance_service.py`` pruefen fuer
    alle vier Schwellen nur ``status``. Damit sind die Schwellwerte
    gepinnt, nicht aber der Inhalt, den das Compliance-Dashboard
    daraus anzeigt — dort sassen zwei ueberlebende Mutanten.

    Am Retention-Check haengt laut Modul-Docstring die DSFA-Zusage zu
    Loeschfristen: er ist die einzige Stelle, an der ein seit Tagen
    toter ``enforce_retention``-Cron auffaellt. Ein Status ohne Meldung
    und ohne Massnahme sagt der Leitung, dass etwas nicht stimmt, aber
    weder was noch was zu tun ist.
    """

    def test_warning_states_the_age_and_the_remedy(self):
        """Toetet ``x__retention_checks__mutmut_40`` und ``__mutmut_64``.

        * ``mutmut_40`` setzt die WARNING-Meldung auf ``None`` — der
          Check meldete Gelb ohne Text.
        * ``mutmut_64`` streicht ``action_hint=hint`` aus dem
          ``ComplianceCheck``; das Feld faellt auf seinen Default
          ``None`` zurueck und die Handlungsanweisung verschwindet aus
          ALLEN Zweigen (der auffaelligste Verlust ist der rote).

        40 h liegt zwischen ``RETENTION_WARN_HOURS`` und
        ``RETENTION_CRITICAL_HOURS`` (Refs #1872: ein ausgefallener
        Tageslauf).
        """
        _retention_run_aged(40)

        check = _retention_checks()[0]

        assert check.status == ComplianceStatus.WARNING
        assert check.message == "Letzter Lauf vor 40h — ein Tageslauf fehlt."
        assert check.action_hint == "Cron muss taeglich laufen (deploy/crontab: 0 3 * * *)."

    def test_critical_states_the_age_and_the_remedy(self):
        """Zweite Haelfte des Nachweises fuer ``__mutmut_64``.

        Der gestrichene ``action_hint`` wirkt in beiden Alarm-Zweigen.
        Der rote Zweig ist der, bei dem die fehlende Massnahme am
        teuersten waere — er bedeutet, dass mindestens zwei Tageslaeufe
        ausgefallen sind und Loeschfristen ueberzogen werden.
        """
        _retention_run_aged(60)

        check = _retention_checks()[0]

        assert check.status == ComplianceStatus.CRITICAL
        assert check.message == "Letzter Lauf vor 60h — mindestens zwei Laeufe ausgefallen."
        assert check.action_hint == "Cron-Status im Container pruefen + manuell ausfuehren."

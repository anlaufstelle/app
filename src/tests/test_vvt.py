"""Tests fuer ``core.services.compliance.vvt`` (Verzeichnis Verarbeitungstaetigkeiten).

Refs #876. Testet die statische Konstante ``PROCESSING_ACTIVITIES``:

* Schema: jeder Eintrag hat alle Pflichtfelder.
* Mindestabdeckung: alle 6 Pflicht-Verarbeitungstaetigkeiten sind
  registriert.
* Helper: ``get_processing_activities`` und ``get_activity`` verhalten
  sich erwartungsgemaess.
"""

import re
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils.functional import Promise

from core.models import Activity, AuditLog, EventHistory
from core.models.settings import (
    DEFAULT_RETENTION_ANONYMOUS_DAYS,
    DEFAULT_RETENTION_IDENTIFIED_DAYS,
    DEFAULT_RETENTION_QUALIFIED_DAYS,
)
from core.services.compliance import (
    PROCESSING_ACTIVITIES,
    get_activity,
    get_processing_activities,
)

#: Wurzel des Anwendungs-Quellbaums (``src/``) — fuer die Quelltext-Guards
#: unten, analog ``PO_PATH`` in ``test_i18n_catalog.py``.
_SRC_ROOT = Path(__file__).resolve().parent.parent

#: Refs #1862: Formulierungen, die eine Protokollierung *jedes* Lesezugriffs
#: behaupten. Eine TOM darf die tatsaechlich protokollierten Lesezugriffe
#: benennen ("von Lesezugriffen nur Export, Download, ...") — sie darf sie nicht
#: pauschalieren.
_BLANKET_READ_LOG_RE = re.compile(
    r"Lese-\s*und\s*Schreib|jede[rns]?\s+Lesezugriff|alle[rn]?\s+Lesezugriffe",
    re.IGNORECASE,
)

#: Refs #1862, seit #1818 umgedreht: Bis zum Fall-Loeschpfad war die Behauptung
#: „ein Fall kann geloescht werden" die unzutreffende Angabe — es gab keinen
#: Loeschworkflow (``DeletionRequest.TargetType`` kannte nur
#: ``EVENT``/``CLIENT``, ADR-039 §6) und folgerichtig keine Loeschaktion.
#: Mit ADR-044 gibt es beides; unzutreffend ist jetzt die **Gegenaussage**.
#: Der Ausdruck faengt die konkrete Formulierung, die dort stand, und ihre
#: naheliegenden Varianten.
_CASE_UNDELETABLE_CLAIM_RE = re.compile(
    r"Fall\w*\s+(?:kann|koennen|können)\s+nicht\s+(?:mehr\s+)?gel[oö]e?scht|"
    r"(?:Faelle|Fälle)\s+sind\s+nicht\s+l[oö]schbar",
    re.IGNORECASE,
)

#: Refs #1862: Wortstamm der beiden Fall-Unterobjekte. Bis #1959 landete ihre
#: Bearbeitung **nirgends** — weder AuditLog noch EventHistory noch
#: Aktivitaets-Feed; seit #1959 schreibt sie eine AuditLog-Zeile.
_LOGGED_SUBOBJECT_RE = re.compile(r"Episode|Wirkungsziel", re.IGNORECASE)

#: Die Aussage, die eine TOM ueber diese Unterobjekte seit #1959 NICHT mehr
#: treffen darf — sie war bis dahin die einzige zutreffende.
_UNLOGGED_SUBOBJECT_PHRASE = "gar nicht protokolliert"

#: Refs #1862: ``VIEW_QUALIFIED`` entsteht ausschliesslich beim Aufruf der
#: Personen-Detailseite (``core/views/clients.py``). Eine pauschale Zusage
#: "jeder Zugriff wird protokolliert" ist damit falsch — das Bearbeiten-
#: Formular zeigt dieselben Zusatzangaben entschluesselt und schreibt nichts.
_BLANKET_CLIENT_ACCESS_CLAIM_RE = re.compile(
    r"jede[rn]?\s+Zugriff\w*(\s+darauf)?\s+wird\s+(automatisch\s+)?protokolliert"
    r"|every\s+access\s+is\s+logged",
    re.IGNORECASE,
)

#: Nutzerdokumentation, die die Kontaktstufe erklaert — dieselbe Zusage wie die
#: beiden Oberflaechen-Bausteine, deshalb derselbe Guard.
_STAGE_DOC_PATHS = (
    "docs/user-guide.md",
    "docs/glossar.md",
    "docs/fachkonzept-anlaufstelle.md",
    "docs/en/user-guide.md",
    "docs/en/glossary.md",
)

# Pflichtfelder pro Eintrag laut Auftrag.
REQUIRED_KEYS = {
    "id",
    "title",
    "purpose",
    "legal_basis",
    "data_categories",
    "recipients",
    "retention_period",
    "toms",
}

# Mindest-IDs, die im Verzeichnis vorhanden sein muessen (Refs #876).
REQUIRED_IDS = {
    "klienten_stammdaten",
    "falldaten",
    "auditlog",
    "auth_login",
    "backup",
    "dsgvo_requests",
}


class TestProcessingActivitiesSchema:
    """Jeder Eintrag erfuellt das Pflicht-Schema."""

    def test_all_entries_have_required_keys(self):
        for entry in PROCESSING_ACTIVITIES:
            missing = REQUIRED_KEYS - set(entry.keys())
            assert missing == set(), f"Eintrag {entry.get('id', '?')} fehlen Felder: {missing}"

    def test_toms_describe_append_only_not_hash_chain(self):
        """A6.2 (Refs #1024 / #1016): no TOM may base append-only on a
        'Hash-Chain'.

        Append-only is enforced by DB trigger + RLS policies. The per-facility
        HMAC chain (``AuditLog.prev_hash``/``entry_hash``,
        ``core.services.audit.chain``, Refs #1070) makes tampering detectable
        afterwards but blocks no write; a TOM presenting it as the append-only
        mechanism would misstate the Art. 30 record.
        """
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                assert "Hash-Chain" not in str(tom), (
                    f"Aktivitaet {entry['id']}: TOM behauptet faelschlich eine "
                    "Hash-Chain — tatsaechlich Append-Only via DB-Trigger + RLS."
                )

    def test_toms_do_not_claim_blanket_read_logging(self):
        """Refs #1862: keine TOM darf eine flaechendeckende Lese-Protokollierung
        behaupten.

        Von Lesezugriffen protokolliert die Anwendung ausschliesslich benannte
        Ausnahmen: den Aufruf einer Person der Stufe „Mit Zusatzangaben"
        (``VIEW_QUALIFIED``, ``views/clients.py``), Auskunft/Export
        (``EXPORT``), Datei-Downloads (``DOWNLOAD``), die Offline-Mitnahme
        (``OFFLINE_*_READ``) und facility-uebergreifende Zugriffe
        (``SYSTEM_VIEW``). Gewoehnliche Listen-, Detail- und Suchaufrufe
        erzeugen keinen Eintrag. Eine TOM, die pauschal „Lese- und
        Schreibzugriffe" protokolliert nennt, waere eine unzutreffende Angabe
        im Verzeichnis nach Art. 30 DSGVO — dieselbe Fehlerklasse wie die
        Hash-Chain-Behauptung oben.
        """
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                assert not _BLANKET_READ_LOG_RE.search(str(tom)), (
                    f"Aktivitaet {entry['id']}: TOM behauptet eine "
                    f"flaechendeckende Lese-Protokollierung ({tom!s}) — "
                    "protokolliert werden nur benannte Lesezugriffe."
                )

    def test_toms_do_not_deny_the_case_deletion_path(self):
        """Refs #1818 (ADR-044): Ein **Fall** kann geloescht werden — umgedrehter Guard.

        Dieser Test pinnte bis #1818 das Gegenteil: ``Case`` erbte zwar
        ``SoftDeletableModel``, aber kein Produktionspfad setzte ``is_deleted``,
        ``DeletionRequest.TargetType`` kannte nur ``EVENT``/``CLIENT``
        (ADR-039 §6), und ``AuditLog.Action`` fuehrte CASE_CREATE/UPDATE/
        CLOSE/REOPEN ohne Loeschaktion. Eine TOM, die damals eine
        Fall-Loeschung nannte, sagte eine Verarbeitung zu, die es nicht gab.

        Genau dafuer war er geschrieben — er sollte reissen, sobald der
        Loeschweg kommt. Er ist deshalb **umgedreht** statt geloescht: dieselbe
        Ableitung aus dem Produktionscode, nur mit der neuen Soll-Menge.

        Zwei Haelften. Erstens: die drei Fall-Loeschaktionen SIND in
        ``AuditLog.Action`` — ohne sie waere die Zusage im Verzeichnis wieder
        unzutreffend. Zweitens: keine TOM behauptet noch das Gegenteil (der
        Klammerzusatz „ein Fall kann nicht geloescht werden" stand bis
        #1818 in der Aktivitaet ``falldaten``).
        """
        for action in ("CASE_SOFT_DELETED", "CASE_RESTORED", "CASE_ANONYMIZED"):
            assert hasattr(AuditLog.Action, action), (
                f"AuditLog.Action.{action} fehlt — ohne die Aktion darf das Verzeichnis "
                "keine Protokollierung der Fall-Loeschung zusagen."
            )
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                assert not _CASE_UNDELETABLE_CLAIM_RE.search(str(tom)), (
                    f"Aktivitaet {entry['id']}: TOM behauptet, ein Fall koenne nicht "
                    f"geloescht werden ({tom!s}) — seit ADR-044 gibt es den Loeschweg."
                )

    def test_toms_do_not_deny_the_episode_and_goal_write_path(self):
        """Refs #1959, umgedrehter Guard: Episoden- und Wirkungsziel-Bearbeitungen
        werden protokolliert.

        Bis #1959 pinnte dieser Slot das Gegenteil — wer die beiden
        Unterobjekte in einer TOM ueberhaupt erwaehnte, musste ausdruecklich
        "gar nicht protokolliert" sagen, weil ``models/case.py`` und
        ``services/case/goals.py`` nur speicherten. Genau dafuer war er
        geschrieben: er sollte reissen, sobald der Schreibpfad kommt. Er ist
        deshalb **umgedreht** statt geloescht — dieselbe Ableitung aus dem
        Produktionscode, nur mit der neuen Soll-Menge.

        Zwei Haelften. Erstens: die vier Aktionen SIND in ``AuditLog.Action`` —
        ohne sie waere die Zusage im Verzeichnis unzutreffend. Zweitens: keine
        TOM behauptet noch das Gegenteil.

        Was **nicht** dazugekommen ist, bleibt ungesagt: ``EventHistory`` gibt
        es weiterhin nur fuer Ereignisse, und ``log_activity`` ruft von den
        beiden Services nach wie vor nur ``delete_milestone`` — ein Verweis auf
        einen "Bearbeitungsverlauf" waere deshalb weiterhin unzutreffend.
        """
        for action in ("EPISODE_UPDATE", "GOAL_UPDATE", "CASE_EVENT_ASSIGNED", "CASE_EVENT_REMOVED"):
            assert hasattr(AuditLog.Action, action), (
                f"AuditLog.Action.{action} fehlt — ohne die Aktion darf das Verzeichnis "
                "keine Protokollierung der Episoden-/Wirkungsziel-Bearbeitung zusagen."
            )
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                text = str(tom)
                if not _LOGGED_SUBOBJECT_RE.search(text):
                    continue
                assert _UNLOGGED_SUBOBJECT_PHRASE not in text, (
                    f"Aktivitaet {entry['id']}: TOM sagt weiter "
                    f'"{_UNLOGGED_SUBOBJECT_PHRASE}" ueber Episoden/Wirkungsziele '
                    f"({text}) — seit #1959 schreiben beide Bearbeitungswege eine "
                    "AuditLog-Zeile."
                )

    def test_id_is_unique(self):
        ids = [entry["id"] for entry in PROCESSING_ACTIVITIES]
        assert len(ids) == len(set(ids)), f"Doppelte IDs gefunden: {ids}"

    def test_id_is_str(self):
        for entry in PROCESSING_ACTIVITIES:
            assert isinstance(entry["id"], str)

    def test_text_fields_are_translatable(self):
        """``title``, ``purpose``, ``legal_basis``, ``retention_period`` sind
        ``gettext_lazy``-Strings (also ``Promise``-Instanzen) — wichtig
        fuer die spaetere Lokalisierung (Refs #878).
        """
        for entry in PROCESSING_ACTIVITIES:
            for field in ("title", "purpose", "legal_basis", "retention_period"):
                value = entry[field]
                assert isinstance(value, (str, Promise)), f"Feld {field!r} in {entry['id']} ist weder str noch lazy."

    def test_professional_legal_bases_are_facility_neutral(self):
        professional_entries = {
            entry["id"]: str(entry["legal_basis"])
            for entry in PROCESSING_ACTIVITIES
            if entry["id"] in {"klienten_stammdaten", "falldaten"}
        }

        assert professional_entries.keys() == {"klienten_stammdaten", "falldaten"}
        for legal_basis in professional_entries.values():
            assert "SGB VIII" not in legal_basis
            assert "einschlaegiges Fach- und Sozialrecht der Einrichtung" in legal_basis

    def test_list_fields_are_lists_of_translatable_strings(self):
        for entry in PROCESSING_ACTIVITIES:
            for field in ("data_categories", "recipients", "toms"):
                value = entry[field]
                assert isinstance(value, list), f"Feld {field!r} in {entry['id']} ist keine Liste."
                assert len(value) > 0, f"Feld {field!r} in {entry['id']} ist leer."
                for item in value:
                    assert isinstance(item, (str, Promise)), (
                        f"Element in {field!r} von {entry['id']} ist weder str noch lazy."
                    )


class TestProcessingActivitiesCoverage:
    """Pflicht-Verarbeitungstaetigkeiten sind registriert."""

    def test_minimum_six_activities(self):
        assert len(PROCESSING_ACTIVITIES) >= 6, (
            f"Erwartet mindestens 6 Eintraege, gefunden: {len(PROCESSING_ACTIVITIES)}."
        )

    def test_required_ids_present(self):
        present_ids = {entry["id"] for entry in PROCESSING_ACTIVITIES}
        missing = REQUIRED_IDS - present_ids
        assert missing == set(), f"Fehlende Pflicht-IDs: {missing}"


class TestProcessingActivitiesContent:
    """Nutzersichtbare VVT-Angaben bleiben an der Implementierung gepinnt."""

    def test_client_retention_period_uses_enforced_defaults(self):
        retention = str(get_activity("klienten_stammdaten")["retention_period"])

        assert f"{DEFAULT_RETENTION_ANONYMOUS_DAYS} Tage" in retention
        assert f"{DEFAULT_RETENTION_IDENTIFIED_DAYS} Tage" in retention
        assert f"{DEFAULT_RETENTION_QUALIFIED_DAYS} Tage" in retention
        assert "12 Monate" not in retention
        assert "36 Monate" not in retention

    def test_backup_retention_names_local_rotation_and_offsite_gap(self):
        """Refs #2464: ``backup.sh`` rotates locally (``-mtime +7/+28/+90``) and never deletes off-site."""
        backup = get_activity("backup")
        retention = str(backup["retention_period"])

        # ``-mtime +N`` selects archives of at least N + 1 full days, and only
        # in a later successful run: a minimum age, not a deadline.
        for days in ("8 (täglich)", "29 (wöchentlich)", "91 volle Tage (monatlich)"):
            assert days in retention
        assert "keine feste Höchstfrist" in retention
        assert "erfolgreichen Lauf" in retention
        for days in ("nach 7 Tagen", "nach 28 Tagen", "nach 90 Tagen"):
            assert days not in retention
        assert "30 Tage" not in retention
        assert "12 Wochen" not in retention
        assert "12 Monate" not in retention
        assert "Off-Site" in retention
        assert "ausgesetzt" in retention

    def test_backup_toms_do_not_promise_deletions_vanish_from_backups(self):
        """Refs #2464: the 02:00 backup holds exactly what the 03:00 retention run deletes."""
        toms = [str(tom) for tom in get_activity("backup")["toms"]]

        assert not any("nicht im Backup ueberleben" in tom for tom in toms)
        assert any("Rotation" in tom and "Aufbewahrungslauf" in tom for tom in toms)

    def test_case_data_retention_names_additional_type_period_and_deletion_effect(self):
        """The type period is additional (first expiry wins); deletion empties content, no k-anonymisation."""
        retention = str(get_activity("falldaten")["retention_period"])

        assert "Pro Dokumentationstyp konfigurierbar" not in retention
        assert "k-anonymisiert" not in retention
        assert "zusätzlich" in retention
        assert "zuerst ablaufende" in retention
        assert "sofort geleert" in retention
        assert "Aufbewahrungssperre" in retention

    def test_audit_log_retention_names_exemptions_and_no_legal_hold(self):
        """``prune_auditlog`` excludes only ``PRUNE_EXEMPT_Q``; a legal hold protects no audit entry."""
        retention = str(get_activity("auditlog")["retention_period"])

        assert "per LegalHold" not in retention
        assert "Aufbewahrungssperren (Legal Hold) wirken nicht auf das Audit-Log" in retention
        for exemption in ("Sicherheitsverstöße", "Ereignis-Historie", "Berichtszeiträume"):
            assert exemption in retention, exemption
        # The run prunes per facility only; entries without one stay for good.
        assert "Einträge ohne Einrichtung" in retention
        assert "unbefristet" in retention

    def test_data_subject_rights_match_implemented_paths(self):
        purpose = str(get_activity("dsgvo_requests")["purpose"])

        assert "Art. 15, 16, 17 und 20" in purpose
        assert "Art. 15-22" not in purpose
        assert "Art. 18" in purpose
        assert "Art. 21" in purpose
        assert "organisatorischen Prozess" in purpose


class TestSearchStaysUnaudited:
    """ADR-043 / Refs #1862: Die Volltextsuche wird bewusst nicht protokolliert.

    Der VVT sagt das seit der Korrektur der beiden TOM-Eintraege ausdruecklich
    zu. Wer eine Such-Protokollierung nachruestet, macht die Art.-30-Angabe
    damit erneut unzutreffend — dieser Guard erzwingt, dass ADR-043 und der VVT
    im selben Schritt nachgezogen werden, statt still auseinanderzulaufen.
    """

    def test_no_audit_action_for_search_exists(self):
        matches = sorted(value for value in AuditLog.Action.values if "search" in value)
        assert matches == [], (
            f"Neue Such-Audit-Aktion(en) {matches} — ADR-043 (keine "
            "Such-Protokollierung) und die TOM-Eintraege im VVT muessen "
            "mitgezogen werden."
        )

    def test_search_paths_write_no_audit_entries(self):
        """Weder View noch Service der Suche rufen ``log_audit_event`` auf."""
        for relative in ("core/views/search.py", "core/services/dashboard/search.py"):
            source = (_SRC_ROOT / relative).read_text(encoding="utf-8")
            assert "log_audit_event" not in source, (
                f"{relative} protokolliert die Suche — siehe ADR-043 und die TOM-Eintraege in vvt.py."
            )


@pytest.mark.django_db
class TestEpisodeAndGoalEditsAreAudited:
    """Refs #1959, umgedreht: die TOM-Aussage weiter am Code gepinnt.

    Bis #1959 hiess diese Klasse ``TestEpisodeAndGoalEditsLeaveNoTrace`` und
    pinnte das (0, 0, 0)-Delta — die TOM sagte damals "gar nicht
    protokolliert", und das war die unangenehmste und deshalb am schaerfsten
    nachzuweisende Aussage des Verzeichnisses. Mit dem Schreibpfad kehrt sich
    die Soll-Menge um: ueber die echten Bearbeiten-Routen entsteht **genau
    eine** ``AuditLog``-Zeile — und weiterhin **keine** ``EventHistory``- und
    keine ``Activity``-Zeile. Das dritte und vierte Delta bleiben also bei
    null; wer sie mitzieht, macht die TOM erneut unzutreffend, nur in die
    andere Richtung.
    """

    def _deltas(self, before):
        return (
            AuditLog.objects.count() - before[0],
            EventHistory.objects.count() - before[1],
            Activity.objects.count() - before[2],
        )

    def _counts(self):
        return (AuditLog.objects.count(), EventHistory.objects.count(), Activity.objects.count())

    def test_episode_edit_writes_exactly_one_audit_row(self, client, staff_user, case_open, episode):
        client.force_login(staff_user)
        before = self._counts()
        response = client.post(
            reverse("core:episode_update", kwargs={"case_pk": case_open.pk, "pk": episode.pk}),
            {
                "title": "Geaenderte Episode",
                "description": episode.description or "",
                "started_at": episode.started_at.isoformat(),
                "ended_at": episode.ended_at.isoformat() if episode.ended_at else "",
            },
        )
        assert response.status_code == 302
        episode.refresh_from_db()
        assert episode.title == "Geaenderte Episode"
        assert self._deltas(before) == (1, 0, 0), (
            "Episoden-Bearbeitung muss genau eine AuditLog-Zeile schreiben und "
            "weder EventHistory noch Aktivitaets-Feed beruehren — sonst ist die "
            "TOM in vvt.py unzutreffend."
        )
        assert AuditLog.objects.latest("timestamp").action == AuditLog.Action.EPISODE_UPDATE

    def test_goal_edit_writes_exactly_one_audit_row(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        before = self._counts()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geaendertes Wirkungsziel", "description": outcome_goal.description or ""},
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.title == "Geaendertes Wirkungsziel"
        assert self._deltas(before) == (1, 0, 0), (
            "Wirkungsziel-Bearbeitung muss genau eine AuditLog-Zeile schreiben "
            "und weder EventHistory noch Aktivitaets-Feed beruehren — sonst ist "
            "die TOM in vvt.py unzutreffend."
        )
        assert AuditLog.objects.latest("timestamp").action == AuditLog.Action.GOAL_UPDATE


class TestQualifiedAccessClaimStaysScoped:
    """Refs #1862: die Zusage "jeder Zugriff wird protokolliert" ist falsch.

    ``VIEW_QUALIFIED`` entsteht projektweit an genau einer Stelle — im GET der
    Personen-Detailseite (``core/views/clients.py``). Das Bearbeiten-Formular
    derselben Person zeigt die Zusatzangaben entschluesselt im Klartext und
    schreibt **nichts**; Liste, Ereignis, Fall und Suchtreffer ebenfalls nicht.
    Der Guard deckt die Nutzerdokumentation ab; die beiden Oberflaechen-
    Bausteine (Badge-``title``, Legendenzeile) und der ``help_text`` haengen in
    ``test_contact_stage_explanations.py``.
    """

    def test_view_qualified_is_written_in_exactly_one_module(self):
        hits = sorted(
            path.relative_to(_SRC_ROOT).as_posix()
            for path in _SRC_ROOT.rglob("*.py")
            if "AuditLog.Action.VIEW_QUALIFIED" in path.read_text(encoding="utf-8")
            and path.name != "audit.py"
            and "tests" not in path.parts
        )
        assert hits == ["core/views/clients.py"], (
            f"VIEW_QUALIFIED wird jetzt in {hits} geschrieben — die auf die "
            "Personen-Detailseite eingegrenzten Zusagen in Oberflaeche, "
            "Handbuch, Glossar und Fachkonzept muessen nachgezogen werden."
        )

    def test_stage_docs_do_not_promise_blanket_access_logging(self):
        for relative in _STAGE_DOC_PATHS:
            doc = Path(relative)
            if not doc.exists():
                # Muster ``test_architecture_guards_views.TestUserGuidePathsResolve``:
                # der Mutations-Baum (und der Release-Snapshot-Kontext) fuehrt
                # ``docs/`` nicht mit — der Guard prueft Doku-Konsistenz und
                # laeuft nur dort, wo die Doku liegt.
                pytest.skip(f"{relative} nicht vorhanden")
            text = doc.read_text(encoding="utf-8")
            match = _BLANKET_CLIENT_ACCESS_CLAIM_RE.search(text)
            assert match is None, (
                f'{relative} sagt "{match.group(0)}" zu — protokolliert wird nur '
                "der Aufruf der Personen-Detailseite (VIEW_QUALIFIED), nicht das "
                "Bearbeiten-Formular, die Liste, ein Ereignis, ein Fall oder ein "
                "Suchtreffer."
            )


class TestVVTHelpers:
    """``get_processing_activities`` und ``get_activity``."""

    def test_get_processing_activities_returns_full_list(self):
        result = get_processing_activities()
        assert isinstance(result, list)
        assert len(result) == len(PROCESSING_ACTIVITIES)

    def test_get_processing_activities_returns_snapshot(self):
        """Aenderungen am Resultat duerfen die Konstante nicht mutieren."""
        before = len(PROCESSING_ACTIVITIES)
        result = get_processing_activities()
        result.pop()
        assert len(PROCESSING_ACTIVITIES) == before

    def test_get_activity_known_id(self):
        entry = get_activity("klienten_stammdaten")
        assert entry is not None
        assert entry["id"] == "klienten_stammdaten"

    def test_get_activity_all_required_ids(self):
        for activity_id in REQUIRED_IDS:
            entry = get_activity(activity_id)
            assert entry is not None, f"get_activity({activity_id!r}) lieferte None."
            assert entry["id"] == activity_id

    def test_get_activity_unknown_returns_none(self):
        assert get_activity("does-not-exist") is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestAuditLogPruningIgnoresLegalHolds:
    """Premise of the VVT and deletion concept wording: holds do not reach the audit log."""

    def test_premise_prune_exemptions_are_action_based(self):
        from core.retention.audit_pruning import PRUNE_EXEMPT_ACTIONS

        assert set(PRUNE_EXEMPT_ACTIONS) == {
            AuditLog.Action.SECURITY_VIOLATION,
            AuditLog.Action.RETENTION_RUN_COMPLETED,
            AuditLog.Action.AUDIT_PRUNE_CHECKPOINT,
            AuditLog.Action.EVENT_HISTORY_REDACTED,
        }

    def test_premise_daily_run_never_prunes_entries_without_facility(self, facility, settings_obj):
        from datetime import timedelta
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command
        from django.utils import timezone

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        facility_entry = AuditLog.objects.create(facility=facility, action=AuditLog.Action.SYSTEM_VIEW)
        system_entry = AuditLog.objects.create(facility=None, action=AuditLog.Action.SYSTEM_VIEW)
        later = timezone.now() + timedelta(days=25 * 31)

        with mock.patch("core.management.commands.enforce_retention.timezone.now", return_value=later):
            call_command("enforce_retention", stdout=StringIO())

        assert not AuditLog.objects.filter(pk=facility_entry.pk).exists()
        assert AuditLog.objects.filter(pk=system_entry.pk).exists()

    def test_prune_for_a_facility_keeps_entries_without_facility(self, facility, settings_obj):
        from datetime import timedelta

        from django.utils import timezone

        from core.services.retention import prune_auditlog

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        facility_entry = AuditLog.objects.create(facility=facility, action=AuditLog.Action.SYSTEM_VIEW)
        system_entry = AuditLog.objects.create(facility=None, action=AuditLog.Action.SYSTEM_VIEW)

        result = prune_auditlog(facility, settings_obj, now=timezone.now() + timedelta(days=800))

        assert result["count"] == 1
        assert not AuditLog.objects.filter(pk=facility_entry.pk).exists()
        assert AuditLog.objects.filter(pk=system_entry.pk).exists()

    def test_audit_entry_about_held_event_is_pruned(self, facility, settings_obj, sample_event, lead_user):
        from datetime import timedelta

        from django.utils import timezone

        from core.models import LegalHold
        from core.services.retention import prune_auditlog

        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        hold = LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=sample_event.pk,
            reason="Open incident",
            created_by=lead_user,
        )
        entry = AuditLog.objects.create(
            facility=facility,
            user=lead_user,
            action=AuditLog.Action.EXPORT,
            target_type="Event",
            target_id=str(sample_event.pk),
        )

        prune_auditlog(facility, settings_obj, now=timezone.now() + timedelta(days=25 * 31))

        assert not AuditLog.objects.filter(pk=entry.pk).exists()
        hold.refresh_from_db()
        assert hold.is_active

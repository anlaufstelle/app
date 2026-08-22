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

#: Refs #1862: Behauptung, ein **Fall** koenne geloescht werden. Es gibt keinen
#: Fall-Loeschworkflow (``services/case/cases.py``, ``DeletionRequest``
#: kennt nur ``EVENT``/``CLIENT``, ADR-039 §6) und folgerichtig keine
#: ``AuditLog.Action.CASE_DELETE``. Der Ausdruck endet bewusst am Satz-/
#: Aufzaehlungstrenner — "Loeschen von Ereignissen; ... von Faellen" ist
#: keine Loeschzusage fuer Faelle.
_CASE_DELETION_CLAIM_RE = re.compile(r"Loesch\w*[^;.]{0,60}Faelle", re.IGNORECASE)

#: Refs #1862: Wortstamm der beiden Fall-Unterobjekte, deren Bearbeitung
#: **nirgends** landet — weder AuditLog noch EventHistory noch
#: Aktivitaets-Feed (Schreibpfad offen in #1959).
_UNLOGGED_SUBOBJECT_RE = re.compile(r"Episode|Wirkungsziel", re.IGNORECASE)

#: Die Aussage, die eine TOM ueber diese Unterobjekte treffen MUSS, sobald sie
#: sie ueberhaupt erwaehnt.
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
        """A6.2 (Refs #1024 / #1016): keine TOM darf 'Hash-Chain'-Integrität
        behaupten.

        Der AuditLog ist Append-Only via DB-Trigger + RLS-Policies — es gibt
        keine kryptographische Hash-Kette. Eine solche Behauptung im VVT wäre
        eine unzutreffende DSGVO-Art.-30-Dokumentation.
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

    def test_toms_do_not_claim_that_a_case_can_be_deleted(self):
        """Refs #1862: Ein **Fall** kann in dieser Anwendung nicht geloescht werden.

        ``Case`` erbt zwar ``SoftDeletableModel``, aber kein Produktionspfad
        setzt ``is_deleted`` je auf einem Fall; ``DeletionRequest.TargetType``
        kennt nur ``EVENT``/``CLIENT`` (``services/case/cases.py``, ADR-039 §6),
        und ``AuditLog.Action`` fuehrt CASE_CREATE/UPDATE/CLOSE/REOPEN — keine
        Loeschaktion. Eine TOM, die eine Fall-Loeschung protokolliert nennt,
        sagt eine Verarbeitung zu, die es nicht gibt.
        """
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                assert not _CASE_DELETION_CLAIM_RE.search(str(tom)), (
                    f"Aktivitaet {entry['id']}: TOM behauptet eine Fall-Loeschung "
                    f"({tom!s}) — es gibt keinen Fall-Loeschworkflow."
                )

    def test_toms_naming_episodes_or_goals_say_they_are_unlogged(self):
        """Refs #1862: Episoden- und Wirkungsziel-Bearbeitungen landen nirgends.

        Wer sie in einer TOM ueberhaupt erwaehnt, muss das ausdruecklich sagen —
        ein Verweis auf einen "Bearbeitungsverlauf" waere eine zweite
        unzutreffende Angabe: ``EventHistory`` gibt es nur fuer Ereignisse,
        ``log_activity`` ruft von den beiden Services nur ``delete_milestone``.
        """
        for entry in PROCESSING_ACTIVITIES:
            for tom in entry["toms"]:
                text = str(tom)
                if not _UNLOGGED_SUBOBJECT_RE.search(text):
                    continue
                assert _UNLOGGED_SUBOBJECT_PHRASE in text, (
                    f"Aktivitaet {entry['id']}: TOM nennt Episoden/Wirkungsziele, "
                    f'sagt aber nicht "{_UNLOGGED_SUBOBJECT_PHRASE}" ({text}) — '
                    "ihre Bearbeitung erzeugt weder AuditLog- noch "
                    "EventHistory- noch Activity-Zeile."
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
class TestEpisodeAndGoalEditsLeaveNoTrace:
    """Refs #1862 (Schreibpfad offen in #1959): die TOM-Aussage am Code gepinnt.

    Der VVT sagt seit der Korrektur, dass Bearbeitungen von Episoden und
    Wirkungszielen **gar nicht** protokolliert werden. Das ist die
    unangenehmste Aussage des Verzeichnisses und deshalb genau die, die
    nachweisbar bleiben muss: Ueber die echten Bearbeiten-Routen entsteht
    weder eine ``AuditLog``- noch eine ``EventHistory``- noch eine
    ``Activity``-Zeile. Sobald #1959 den Schreibpfad nachruestet, fallen
    diese Tests auf — und die TOM ist im selben Schritt nachzuziehen.
    """

    def _deltas(self, before):
        return (
            AuditLog.objects.count() - before[0],
            EventHistory.objects.count() - before[1],
            Activity.objects.count() - before[2],
        )

    def _counts(self):
        return (AuditLog.objects.count(), EventHistory.objects.count(), Activity.objects.count())

    def test_episode_edit_writes_no_audit_no_history_no_activity(self, client, staff_user, case_open, episode):
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
        assert self._deltas(before) == (0, 0, 0), (
            "Episoden-Bearbeitung hinterlaesst jetzt eine Spur — die TOM in "
            'vvt.py ("gar nicht protokolliert") ist damit unzutreffend.'
        )

    def test_goal_edit_writes_no_audit_no_history_no_activity(self, client, staff_user, case_open, outcome_goal):
        client.force_login(staff_user)
        before = self._counts()
        response = client.post(
            reverse("core:goal_update", kwargs={"case_pk": case_open.pk, "pk": outcome_goal.pk}),
            {"title": "Geaendertes Wirkungsziel", "description": outcome_goal.description or ""},
        )
        assert response.status_code == 200
        outcome_goal.refresh_from_db()
        assert outcome_goal.title == "Geaendertes Wirkungsziel"
        assert self._deltas(before) == (0, 0, 0), (
            "Wirkungsziel-Bearbeitung hinterlaesst jetzt eine Spur — die TOM in "
            'vvt.py ("gar nicht protokolliert") ist damit unzutreffend.'
        )


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
            text = Path(relative).read_text(encoding="utf-8")
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

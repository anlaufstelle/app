"""Papierkorb-Invariante der Fall-Domaene (Refs #1777).

Zwei Haelften, die zusammen den Befund aus #1777 aufloesen — ``views/cases.py``
filterte an KEINER Stelle ``is_deleted``, anders als die Personen-Pfade nach
#1737:

**(a) „Faelle kennen kein Soft-Delete" ist eine Invariante, kein Zufall.**
``Case`` erbt ``SoftDeletableModel`` (das Feld existiert also), aber **kein**
Produktionspfad setzt es je: ``DeletionRequest.TargetType`` kennt nur
``EVENT``/``CLIENT``, und der einzige ``soft_delete()``-Aufruf im gesamten
Produktionscode ist ``client.soft_delete`` im Vier-Augen-Loeschworkflow. Ein
``is_deleted``-Filter an den Fall-Views waere heute wirkungslos; die Guards hier
machen ein stilles Einfuehren sichtbar, statt die Wirkungslosigkeit zu
kaschieren.

**(b) Die Grenze verlaeuft ueber die PERSON, nicht ueber den Fall.**
Ein Fall haengt per Pflicht-FK an genau einer Person. Ist die im Papierkorb
(``is_deleted=True``) oder anonymisiert (``is_active=False``), ist der Fall der
letzte offene Lesepfad auf ihren Bestand — und, ueber Bearbeiten/Episoden/Ziele,
sogar ein Schreibpfad. Genau diese Grenze zogen die Fall-Views bis #1777 nicht.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django.urls import reverse

from core.forms.events import EventMetaForm
from core.models import Case, DeletionRequest, Episode, OutcomeGoal
from core.models.mixins import SoftDeletableModel

REPO_SRC = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# (a) Invariante: Faelle kennen kein Soft-Delete
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestCaseSoftDeleteInvariant:
    """Ableitungsbasierter Guard nach dem Muster der uebrigen Architektur-Guards.

    Bewusst **keine** gepflegte „Was darf soft-geloescht werden"-Liste, sondern
    zwei Ableitungen aus dem Produktionscode, deren Ergebnis gepinnt ist:

    1. die Wertemenge von ``DeletionRequest.TargetType`` (der einzige Weg IN
       einen Papierkorb) und
    2. alle ``<x>.soft_delete(...)``-Aufrufe unter ``src/core/`` ausserhalb der
       Migrationen.

    Traegt jemand ``CASE = "Case"`` nach oder ruft ``case.soft_delete()``, reisst
    der Guard — und mit ihm der Anlass, die Fall-Views (Detail/Edit/Partials,
    Episoden, Ziele) um echte ``is_deleted``-Gates zu ergaenzen. Ohne diesen
    Guard entstuende genau die Luecke, die #1777 an den Personen-Pfaden bereits
    beschreibt: ein Papierkorb ohne Papierkorb-Grenze.
    """

    #: ``<receiver>.soft_delete(`` — der Empfaenger ist die Evidenz, um welches
    #: Aggregat es geht.
    _SOFT_DELETE_CALL = re.compile(r"(?<![\w.])([\w.]+)\.soft_delete\(")

    #: Gepinnter Ist-Zustand: genau EIN Aufrufer, und der arbeitet auf Personen.
    _EXPECTED_SOFT_DELETE_CALLS = {("core/services/client/main.py", "client")}

    @classmethod
    def _production_modules(cls) -> list[Path]:
        return [
            path
            for path in sorted((REPO_SRC / "core").rglob("*.py"))
            if "migrations" not in path.parts and "__pycache__" not in path.parts
        ]

    @classmethod
    def _soft_delete_calls(cls) -> set[tuple[str, str]]:
        found = set()
        for path in cls._production_modules():
            rel = path.relative_to(REPO_SRC).as_posix()
            for receiver in cls._SOFT_DELETE_CALL.findall(path.read_text(encoding="utf-8")):
                # ``self.soft_delete``/``super().soft_delete`` gibt es nicht; die
                # Mixin-Definition selbst ist ein ``def``, kein Aufruf.
                found.add((rel, receiver))
        return found

    def test_case_inherits_soft_delete_capability(self):
        """Positiv-Kontrolle: das Feld EXISTIERT — die Invariante ist eine
        Aussage ueber die Nutzung, nicht ueber das Schema."""
        assert issubclass(Case, SoftDeletableModel)
        assert Case._meta.get_field("is_deleted") is not None
        assert callable(Case.soft_delete)

    def test_deletion_request_targets_exclude_case(self):
        """Der Vier-Augen-Loeschworkflow kennt nur Ereignis und Person."""
        assert {choice.value for choice in DeletionRequest.TargetType} == {"Event", "Client"}

    def test_extraction_finds_the_known_client_soft_delete(self):
        """Anker gegen einen still gruenen Guard: findet die Ableitung ueberhaupt etwas?

        Ohne diese Kontrolle waere ``test_no_production_path_soft_deletes_a_case``
        auch dann gruen, wenn ein Refactor die Aufrufform aendert und die Regex
        nichts mehr matcht.
        """
        assert self._soft_delete_calls() == self._EXPECTED_SOFT_DELETE_CALLS, (
            "Die Menge der soft_delete()-Aufrufe im Produktionscode hat sich geaendert. "
            "Neuer Aufrufer? Dann pruefen, auf welchem Aggregat er arbeitet — und ob "
            "die zugehoerigen Views eine Papierkorb-Grenze ziehen (Muster #1737)."
        )

    def test_no_production_path_soft_deletes_a_case(self):
        """Kein ``case.soft_delete()`` und kein ``case.is_deleted = True``."""
        offenders = [
            (module, receiver)
            for module, receiver in self._soft_delete_calls()
            if "case" in receiver.lower() or "episode" in receiver.lower()
        ]
        assert not offenders, (
            f"Ein Produktionspfad soft-loescht jetzt einen Fall: {sorted(offenders)}. "
            "Die Invariante aus #1777 gilt damit nicht mehr — die Fall-Views brauchen "
            "echte ``is_deleted=False``-Gates (Detail, Bearbeiten, Partial, Episoden, Ziele) "
            "und einen Papierkorb-/Wiederherstellen-Weg."
        )

        assignments = []
        for path in self._production_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not (isinstance(node.value, ast.Constant) and node.value.value is True):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "is_deleted"
                        and isinstance(target.value, ast.Name)
                        and "case" in target.value.id.lower()
                    ):
                        assignments.append((path.relative_to(REPO_SRC).as_posix(), node.lineno))
        assert not assignments, f"Direkte ``case.is_deleted = True``-Zuweisung: {assignments}"


# ---------------------------------------------------------------------------
# (b) Papierkorb-/Anonymisierungs-Grenze ueber die Person
# ---------------------------------------------------------------------------


@pytest.fixture
def case_of_trashed_client(case_open, admin_user):
    case_open.client.soft_delete(user=admin_user)
    return case_open


@pytest.fixture
def case_of_anonymized_client(case_open):
    person = case_open.client
    person.is_active = False
    person.pseudonym = f"Gelöscht-{str(person.pk)[:8]}"
    person.save(update_fields=["is_active", "pseudonym"])
    return case_open


@pytest.mark.django_db
class TestCaseViewsRespectClientBoundary:
    """Fall-Pfade einer widerrufenen Person sind 404 — analog #1737.

    Der Fall traegt Titel, Beschreibung und ``data_json`` (seit M2 auch
    feldverschluesselt) sowie seit M24 Datei-Anhaenge. Bleibt er erreichbar,
    waehrend Personen-Detail und -Bearbeiten schon 404 antworten, ist er der
    Umweg um die Loeschgrenze — lesend UND schreibend.
    """

    @pytest.fixture(params=["case_of_trashed_client", "case_of_anonymized_client"])
    def revoked_case(self, request):
        return request.getfixturevalue(request.param)

    def test_detail_is_404(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_detail", args=[revoked_case.pk])).status_code == 404

    def test_update_get_is_404(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_update", args=[revoked_case.pk])).status_code == 404

    def test_update_post_is_404_and_writes_nothing(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[revoked_case.pk]),
            {
                "client": str(revoked_case.client.pk),
                "title": "Frischer Klartext-Titel",
                "description": "Frische Klartext-Beschreibung",
                "lead_user": "",
            },
        )
        assert response.status_code == 404
        revoked_case.refresh_from_db()
        assert revoked_case.title != "Frischer Klartext-Titel"

    def test_fields_partial_is_404(self, client, staff_user, revoked_case, doc_type_case_form):
        client.force_login(staff_user)
        url = f"{reverse('core:case_fields_partial')}?document_type={doc_type_case_form.pk}&case={revoked_case.pk}"
        assert client.get(url).status_code == 404

    def test_close_and_reopen_are_404(self, client, lead_user, revoked_case):
        client.force_login(lead_user)
        assert client.post(reverse("core:case_close", args=[revoked_case.pk])).status_code == 404
        assert client.post(reverse("core:case_reopen", args=[revoked_case.pk])).status_code == 404

    def test_event_assignment_is_404(self, client, staff_user, revoked_case, sample_event):
        client.force_login(staff_user)
        assert (
            client.post(
                reverse("core:case_assign_event", args=[revoked_case.pk]),
                {"event": str(sample_event.pk)},
            ).status_code
            == 404
        )

    def test_episode_create_is_404_and_writes_nothing(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:episode_create", args=[revoked_case.pk]),
            {"title": "Episode mit Klartext", "description": "frei"},
        )
        assert response.status_code == 404
        assert not Episode.objects.filter(title="Episode mit Klartext").exists()

    def test_goal_create_is_404_and_writes_nothing(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", args=[revoked_case.pk]),
            {"title": "Wirkungsziel mit Klartext", "description": ""},
        )
        assert response.status_code == 404
        assert not OutcomeGoal.objects.filter(title="Wirkungsziel mit Klartext").exists()

    def test_case_list_hides_the_case(self, client, staff_user, revoked_case):
        """Sonst zeigt die Liste einen Eintrag, dessen Detailseite 404 liefert."""
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 200
        assert str(revoked_case.pk) not in response.content.decode()

    def test_cases_for_client_json_is_empty(self, client, staff_user, revoked_case):
        client.force_login(staff_user)
        response = client.get(f"{reverse('core:cases_for_client')}?client={revoked_case.client.pk}")
        assert response.status_code == 200
        assert response.json() == []

    def test_event_form_case_choice_excludes_the_case(self, facility, staff_user, revoked_case):
        """Der Fall-Select im Ereignisformular ist zugleich die Validierungs-Whitelist."""
        form = EventMetaForm(facility=facility, user=staff_user)
        assert revoked_case.pk not in {case.pk for case in form.fields["case"].queryset}


@pytest.mark.django_db
class TestLiveCaseStaysReachable:
    """Regressionswaechter: die Grenze darf den Normalbetrieb nicht treffen."""

    def test_detail_and_update_stay_200(self, client, staff_user, case_open):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_detail", args=[case_open.pk])).status_code == 200
        assert client.get(reverse("core:case_update", args=[case_open.pk])).status_code == 200

    def test_case_list_shows_the_case(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))
        assert str(case_open.pk) in response.content.decode()

    def test_cases_for_client_lists_the_case(self, client, staff_user, case_open):
        client.force_login(staff_user)
        response = client.get(f"{reverse('core:cases_for_client')}?client={case_open.client.pk}")
        assert [entry["id"] for entry in response.json()] == [str(case_open.pk)]

    def test_restored_client_makes_the_case_reachable_again(self, client, staff_user, case_of_trashed_client):
        case_of_trashed_client.client.restore()
        client.force_login(staff_user)
        assert client.get(reverse("core:case_detail", args=[case_of_trashed_client.pk])).status_code == 200

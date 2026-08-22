"""Papierkorb-Grenze der Fall-Domaene (Refs #1777, seit #1818 umgekehrt).

Die Datei hiess urspruenglich „Invariante", weil Teil (a) das GEGENTEIL pinnte:
Faelle kannten keinen Soft-Delete, und die Guards machten ein stilles
Einfuehren sichtbar. Mit dem Fall-Loeschpfad (#1818, [ADR-044]) ist genau das
eingetreten — die Guards sind deshalb **umgedreht** statt geloescht: dieselben
Ableitungen aus dem Produktionscode, nur mit der neuen Soll-Menge. Ein Loeschen
haette den Anlass mitgenommen, aus dem sie entstanden sind.

Drei Haelften:

**(a) Der Papierkorb-Fall MUSS existieren — und an genau einer Stelle entstehen.**
``DeletionRequest.TargetType`` kennt jetzt ``CASE``, und es gibt genau zwei
``soft_delete()``-Aufrufer im Produktionscode: den Personen-Workflow und
``services/case/deletion.py``. Ein ``case.soft_delete()`` irgendwo sonst — in
einer View, im Retention-Pfad, im Admin — umginge Vier-Augen-Staffelung (P1c),
Audit-Eintrag und Aktivitaets-Spur.

**(b) Die Grenze verlaeuft AUCH ueber die PERSON.**
Ein Fall haengt per Pflicht-FK an genau einer Person. Ist die im Papierkorb
(``is_deleted=True``) oder anonymisiert (``is_active=False``), ist der Fall der
letzte offene Lesepfad auf ihren Bestand — und, ueber Bearbeiten/Episoden/Ziele,
sogar ein Schreibpfad. Genau diese Grenze zogen die Fall-Views bis #1777 nicht.

**(c) Und seit #1818 ueber den Fall selbst.**
Derselbe Schnitt fuer ``case.is_deleted=True``: Detail, Bearbeiten, Partial,
Episoden, Ziele, Schliessen/Wiedereroeffnen, Ereignis-Zuordnung sind 404, Liste
und Ereignis-Select kennen den Fall nicht mehr. Traeger ist wie in (b) der
gemeinsame Choke-Point ``cases_with_live_client()``.
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
# (a) Der Papierkorb-Fall existiert — und entsteht an genau einer Stelle
# ---------------------------------------------------------------------------


@pytest.mark.architecture
class TestCaseSoftDeleteEntryPoints:
    """Ableitungsbasierter Guard nach dem Muster der uebrigen Architektur-Guards.

    Bewusst **keine** gepflegte „Was darf soft-geloescht werden"-Liste, sondern
    zwei Ableitungen aus dem Produktionscode, deren Ergebnis gepinnt ist:

    1. die Wertemenge von ``DeletionRequest.TargetType`` (der Vier-Augen-Weg IN
       einen Papierkorb) und
    2. alle ``<x>.soft_delete(...)``-Aufrufe unter ``src/core/`` ausserhalb der
       Migrationen.

    Bis #1818 verlangte der Guard, dass **kein** Fall soft-geloescht wird. Seit
    dem Fall-Loeschpfad verlangt er das Gegenteil: der Aufruf muss existieren —
    und zwar genau einmal, im Loesch-Service. Reisst er, ist entweder der
    Loeschpfad verschwunden oder ein zweiter Einstieg entstanden, der an
    Staffelung (P1c), Audit und Aktivitaets-Spur vorbeilaeuft.
    """

    #: ``<receiver>.soft_delete(`` — der Empfaenger ist die Evidenz, um welches
    #: Aggregat es geht.
    _SOFT_DELETE_CALL = re.compile(r"(?<![\w.])([\w.]+)\.soft_delete\(")

    #: Gepinnter Ist-Zustand: genau ZWEI Aufrufer — Personen- und Fall-Workflow.
    _EXPECTED_SOFT_DELETE_CALLS = {
        ("core/services/client/main.py", "client"),
        ("core/services/case/deletion.py", "case"),
    }

    #: Der einzige Modulpfad, der einen Fall in den Papierkorb legen darf.
    _CASE_SOFT_DELETE_MODULE = "core/services/case/deletion.py"

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
        """Positiv-Kontrolle: das Feld existiert am Modell."""
        assert issubclass(Case, SoftDeletableModel)
        assert Case._meta.get_field("is_deleted") is not None
        assert callable(Case.soft_delete)

    def test_deletion_request_targets_include_case(self):
        """Der Vier-Augen-Loeschworkflow kennt Ereignis, Person UND Fall (#1818)."""
        assert {choice.value for choice in DeletionRequest.TargetType} == {"Event", "Client", "Case"}

    def test_extraction_finds_both_known_soft_deletes(self):
        """Anker gegen einen still gruenen Guard: findet die Ableitung ueberhaupt etwas?

        Ohne diese Kontrolle waere ``test_only_the_deletion_service_soft_deletes_a_case``
        auch dann gruen, wenn ein Refactor die Aufrufform aendert und die Regex
        nichts mehr matcht.
        """
        assert self._soft_delete_calls() == self._EXPECTED_SOFT_DELETE_CALLS, (
            "Die Menge der soft_delete()-Aufrufe im Produktionscode hat sich geaendert. "
            "Neuer Aufrufer? Dann pruefen, auf welchem Aggregat er arbeitet — und ob "
            "die zugehoerigen Views eine Papierkorb-Grenze ziehen (Muster #1737/#1818)."
        )

    def test_only_the_deletion_service_soft_deletes_a_case(self):
        """Genau ein Fall-Papierkorb-Einstieg, und der liegt im Loesch-Service."""
        case_calls = {
            (module, receiver)
            for module, receiver in self._soft_delete_calls()
            if "case" in receiver.lower() or "episode" in receiver.lower()
        }
        assert case_calls == {(self._CASE_SOFT_DELETE_MODULE, "case")}, (
            f"Fall-Soft-Delete an unerwarteter Stelle bzw. gar nicht mehr: {sorted(case_calls)}. "
            "Der Papierkorb-Fall entsteht ausschliesslich in "
            f"``{self._CASE_SOFT_DELETE_MODULE}`` — jeder andere Einstieg umgeht "
            "Vier-Augen-Staffelung (P1c), CASE_SOFT_DELETED-Audit und Aktivitaets-Spur."
        )

    def test_no_raw_is_deleted_assignment_on_a_case(self):
        """``case.is_deleted = True`` von Hand bleibt verboten — auch im Loesch-Service.

        Die Mixin-Methode setzt zusaetzlich ``deleted_at``/``deleted_by``; ohne
        sie stuende ein Fall ohne Loeschdatum im Papierkorb und liefe nie in den
        Fristablauf.
        """
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


# ---------------------------------------------------------------------------
# (c) Papierkorb-Grenze am Fall selbst (Refs #1818)
# ---------------------------------------------------------------------------


@pytest.fixture
def trashed_case(case_open, staff_user):
    """Fall im Papierkorb — ueber den Produktionspfad, nicht per ``update()``."""
    from core.services.case.deletion import soft_delete_case

    soft_delete_case(case_open, staff_user)
    case_open.refresh_from_db()
    return case_open


@pytest.mark.django_db
class TestCaseViewsRespectTheCaseTrashBoundary:
    """Ein Fall im Papierkorb ist ueberall abwesend — Umkehrung der #1777-Invariante.

    Derselbe Schnitt wie in (b), nur ist der Traeger jetzt der Fall selbst.
    Beides laeuft ueber ``cases_with_live_client()``; die Tests fassen bewusst
    die Views an, nicht den Helfer — ein Lesepfad, der am Choke-Point
    vorbeigeht, faellt sonst nicht auf.
    """

    def test_detail_is_404(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_detail", args=[trashed_case.pk])).status_code == 404

    def test_update_get_is_404(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        assert client.get(reverse("core:case_update", args=[trashed_case.pk])).status_code == 404

    def test_update_post_is_404_and_writes_nothing(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:case_update", args=[trashed_case.pk]),
            {
                "client": str(trashed_case.client.pk),
                "title": "Frischer Klartext-Titel",
                "description": "Frische Klartext-Beschreibung",
                "lead_user": "",
            },
        )
        assert response.status_code == 404
        trashed_case.refresh_from_db()
        assert trashed_case.title != "Frischer Klartext-Titel"

    def test_fields_partial_is_404(self, client, staff_user, trashed_case, doc_type_case_form):
        client.force_login(staff_user)
        url = f"{reverse('core:case_fields_partial')}?document_type={doc_type_case_form.pk}&case={trashed_case.pk}"
        assert client.get(url).status_code == 404

    def test_close_and_reopen_are_404(self, client, lead_user, trashed_case):
        client.force_login(lead_user)
        assert client.post(reverse("core:case_close", args=[trashed_case.pk])).status_code == 404
        assert client.post(reverse("core:case_reopen", args=[trashed_case.pk])).status_code == 404

    def test_event_assignment_is_404(self, client, staff_user, trashed_case, sample_event):
        client.force_login(staff_user)
        assert (
            client.post(
                reverse("core:case_assign_event", args=[trashed_case.pk]),
                {"event": str(sample_event.pk)},
            ).status_code
            == 404
        )

    def test_episode_create_is_404_and_writes_nothing(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:episode_create", args=[trashed_case.pk]),
            {"title": "Episode mit Klartext", "description": "frei"},
        )
        assert response.status_code == 404
        assert not Episode.objects.filter(title="Episode mit Klartext").exists()

    def test_goal_create_is_404_and_writes_nothing(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        response = client.post(
            reverse("core:goal_create", args=[trashed_case.pk]),
            {"title": "Wirkungsziel mit Klartext", "description": ""},
        )
        assert response.status_code == 404
        assert not OutcomeGoal.objects.filter(title="Wirkungsziel mit Klartext").exists()

    def test_case_list_hides_the_case(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        response = client.get(reverse("core:case_list"))
        assert response.status_code == 200
        assert str(trashed_case.pk) not in response.content.decode()

    def test_cases_for_client_json_is_empty(self, client, staff_user, trashed_case):
        client.force_login(staff_user)
        response = client.get(f"{reverse('core:cases_for_client')}?client={trashed_case.client.pk}")
        assert response.status_code == 200
        assert response.json() == []

    def test_event_form_case_choice_excludes_the_case(self, facility, staff_user, trashed_case):
        form = EventMetaForm(facility=facility, user=staff_user)
        assert trashed_case.pk not in {case.pk for case in form.fields["case"].queryset}

    def test_restored_case_is_reachable_again(self, client, staff_user, admin_user, trashed_case):
        from core.services.case.deletion import restore_case

        restore_case(trashed_case, admin_user)
        client.force_login(staff_user)
        assert client.get(reverse("core:case_detail", args=[trashed_case.pk])).status_code == 200

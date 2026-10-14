"""Fall-Bearbeiten bei parallelem Schließen/Wiedereröffnen (Refs #2359).

Ablauf wie in zwei Browser-Tabs: Tab A rendert das Bearbeiten-Formular
eines Falls, Tab B schließt den Fall (bzw. eröffnet ihn wieder), Tab A
ändert danach Titel oder Beschreibung und speichert. Status und
Schließdatum gehören nicht zu den Feldern des Formulars — ein paralleler
Statuswechsel darf die Eingabe aus Tab A deshalb weder verwerfen noch als
Konflikt ablehnen, und der parallele Status muss erhalten bleiben. Eine
parallele Änderung eines Formularfelds bleibt dagegen ein echter Konflikt
mit derselben Meldung wie bisher und darf nichts still überschreiben.

Die Tests posten die versteckten Felder genau so, wie das gerenderte
Formular sie trägt (HTML-Parser statt fest verdrahteter Feldnamen) — sie
prüfen damit den Weg der Oberfläche, nicht einen einzelnen Mechanismus.
"""

import uuid
from html.parser import HTMLParser

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import Client as HttpClient
from django.urls import reverse

from core.models import AuditLog, Case
from core.services.case import (
    anonymize_case_now,
    approve_case_deletion,
    close_case,
    reopen_case,
    request_case_deletion,
    soft_delete_case,
    update_case,
)

CONFLICT_TEXT = "zwischenzeitlich bearbeitet"


class _HiddenInputs(HTMLParser):
    """Sammelt ``<input type="hidden">`` des Bearbeiten-Formulars (ohne CSRF)."""

    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attr = dict(attrs)
        name = attr.get("name")
        if attr.get("type") == "hidden" and name and name != "csrfmiddlewaretoken":
            self.values[name] = attr.get("value") or ""


def _render_edit_form(http, case):
    """Tab A: Formular laden und die versteckten Felder übernehmen."""
    response = http.get(reverse("core:case_update", kwargs={"pk": case.pk}))
    assert response.status_code == 200
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    assert "expected_updated_at" in parser.values, "Formular muss den Versions-Token tragen"
    return parser.values


def _form_payload(case, hidden, **overrides):
    """Sichtbare Formularfelder mit dem Stand zum Renderzeitpunkt + Änderungen."""
    payload = dict(hidden)
    payload.update(
        {
            "title": case.title,
            "description": case.description or "",
            "lead_user": str(case.lead_user_id or ""),
            "document_type": str(case.document_type_id or ""),
            "client": str(case.client_id),
        }
    )
    payload.update(overrides)
    return payload


def _other_tab(user):
    http = HttpClient()
    http.force_login(user)
    return http


def _close_in_other_tab(lead_user, case):
    """Tab B: „Fall schließen" über denselben Pfad wie die Oberfläche."""
    response = _other_tab(lead_user).post(reverse("core:case_close", kwargs={"pk": case.pk}))
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == Case.Status.CLOSED


def _reopen_in_other_tab(lead_user, case):
    response = _other_tab(lead_user).post(reverse("core:case_reopen", kwargs={"pk": case.pk}))
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.status == Case.Status.OPEN


@pytest.fixture
def described_case(case_open):
    case_open.description = "Langer Beschreibungstext, der nicht verloren gehen darf."
    case_open.save(update_fields=["description", "updated_at"])
    return case_open


@pytest.mark.django_db
class TestParallelStatusChangeKeepsCaseEdit:
    def test_title_edit_survives_parallel_close(self, client, staff_user, lead_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)

        _close_in_other_tab(lead_user, described_case)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title="Titel aus Tab A"),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:case_detail", kwargs={"pk": described_case.pk}), (
            "Speichern muss wie im Normalfall zur Fallansicht weiterleiten"
        )
        described_case.refresh_from_db()
        assert described_case.title == "Titel aus Tab A", "Eingabe aus Tab A darf nicht verloren gehen"
        assert described_case.status == Case.Status.CLOSED, "Parallel gesetzter Status muss erhalten bleiben"
        assert described_case.closed_at is not None
        assert described_case.description == snapshot.description

    def test_description_edit_survives_parallel_reopen(self, client, staff_user, lead_user, case_closed):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, case_closed)
        snapshot = Case.objects.get(pk=case_closed.pk)

        _reopen_in_other_tab(lead_user, case_closed)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_closed.pk}),
            _form_payload(snapshot, hidden, description="Neuer Text aus Tab A."),
        )

        assert response.status_code == 302
        assert response.url == reverse("core:case_detail", kwargs={"pk": case_closed.pk})
        case_closed.refresh_from_db()
        assert case_closed.description == "Neuer Text aus Tab A."
        assert case_closed.status == Case.Status.OPEN
        assert case_closed.closed_at is None

    def test_edit_survives_close_and_reopen(self, client, staff_user, lead_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)

        _close_in_other_tab(lead_user, described_case)
        _reopen_in_other_tab(lead_user, described_case)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title="Nach Hin und Her"),
        )

        assert response.status_code == 302
        described_case.refresh_from_db()
        assert described_case.title == "Nach Hin und Her"
        assert described_case.status == Case.Status.OPEN


@pytest.mark.django_db
class TestParallelFormFieldChangeStaysCaseConflict:
    def test_parallel_title_change_is_reported_as_conflict(self, client, staff_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)

        # Tab B ändert ein Feld, das auch im Formular steht.
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, description="Beschreibung aus Tab A"),
            follow=True,
        )

        assert response.status_code == 200
        assert response.redirect_chain == []
        assert response.context["form"]["description"].value() == "Beschreibung aus Tab A"
        assert response.context["has_edit_conflict"]
        assert CONFLICT_TEXT in response.content.decode()
        described_case.refresh_from_db()
        assert described_case.title == "Titel aus Tab B", "Paralleler Titel darf nicht still überschrieben werden"
        assert described_case.description == snapshot.description, "Konflikt darf nichts teilweise schreiben"

    def test_close_plus_title_change_is_conflict(self, client, staff_user, lead_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)

        _close_in_other_tab(lead_user, described_case)
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title="Titel aus Tab A"),
        )

        assert response.status_code == 200
        assert not response.has_header("Location")
        assert response.context["has_edit_conflict"]
        described_case.refresh_from_db()
        assert described_case.title == "Titel aus Tab B"
        assert described_case.status == Case.Status.CLOSED

    def test_manipulated_form_state_does_not_rescue(self, client, staff_user, lead_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)
        hidden["expected_form_state"] = "0" * 64

        _close_in_other_tab(lead_user, described_case)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title="Titel aus Tab A"),
        )

        assert response.status_code == 200
        assert not response.has_header("Location")
        assert response.context["has_edit_conflict"]
        described_case.refresh_from_db()
        assert described_case.title == snapshot.title


@pytest.mark.django_db
class TestCaseFormStateService:
    """Service-Ebene: der Fingerabdruck löst NUR einen reinen Versions-Mismatch auf."""

    def test_token_ignores_status_but_tracks_form_fields(self, case_open, lead_user):
        from core.services.case.cases import case_form_state_token

        before = case_form_state_token(case_open)
        case_open.status = Case.Status.CLOSED
        assert case_form_state_token(case_open) == before
        for attr, value in (
            ("title", "Anders"),
            ("description", "Anders"),
            ("lead_user_id", lead_user.pk),
            ("data_json", {"x": 1}),
        ):
            probe = Case.objects.get(pk=case_open.pk)
            setattr(probe, attr, value)
            assert case_form_state_token(probe) != before, attr

    def test_token_is_bound_to_the_case(self, case_open, case_closed):
        from core.services.case.cases import case_form_state_token

        case_closed.title = case_open.title
        assert case_form_state_token(case_open) != case_form_state_token(case_closed)

    def test_token_uses_its_own_salt(self, case_open):
        from core.services.case.cases import case_form_state_token
        from core.services.case.workitems import _form_state_token_from_values

        assert case_form_state_token(case_open) != _form_state_token_from_values(case_open.pk, {})

    @pytest.mark.parametrize("change", [close_case, "reopen"])
    def test_service_applies_edit_when_only_status_changed(self, staff_user, case_open, change):
        from core.services.case.cases import case_form_state_token

        if change == "reopen":
            close_case(Case.objects.get(pk=case_open.pk), staff_user)
            case_open.refresh_from_db()
            change = reopen_case
        stale_version = case_open.updated_at.isoformat()
        state = case_form_state_token(case_open)
        change(Case.objects.get(pk=case_open.pk), staff_user)
        expected_status = Case.objects.get(pk=case_open.pk).status

        update_case(
            case_open,
            staff_user,
            expected_updated_at=stale_version,
            expected_form_state=state,
            title="Neu",
        )
        case_open.refresh_from_db()
        assert case_open.title == "Neu"
        assert case_open.status == expected_status

    @pytest.mark.parametrize("state", [None, "", "falscher-fingerabdruck", 42])
    def test_service_without_matching_state_keeps_conflict(self, staff_user, case_open, state):
        stale_version = case_open.updated_at.isoformat()
        close_case(Case.objects.get(pk=case_open.pk), staff_user)

        with pytest.raises(ValidationError) as excinfo:
            update_case(
                case_open,
                staff_user,
                expected_updated_at=stale_version,
                expected_form_state=state,
                title="Neu",
            )
        assert excinfo.value.code == "version_conflict"
        case_open.refresh_from_db()
        assert case_open.title == "Offener Fall"

    def test_default_contract_unchanged_without_form_state(self, staff_user, case_open):
        stale_version = case_open.updated_at.isoformat()
        close_case(Case.objects.get(pk=case_open.pk), staff_user)

        with pytest.raises(ValidationError) as excinfo:
            update_case(case_open, staff_user, expected_updated_at=stale_version, title="Neu")
        assert excinfo.value.code == "version_conflict"

    def test_invalid_token_is_not_rescued_by_form_state(self, staff_user, case_open):
        from core.services.case.cases import case_form_state_token

        with pytest.raises(ValidationError) as excinfo:
            update_case(
                case_open,
                staff_user,
                expected_updated_at="kein-zeitstempel",
                expected_form_state=case_form_state_token(case_open),
                title="Neu",
            )
        assert excinfo.value.code == "invalid_token"

    def test_vanished_row_is_no_match(self, case_open):
        from core.services.case.cases import _case_form_state_unchanged, case_form_state_token

        state = case_form_state_token(case_open)
        ghost = Case(pk=uuid.uuid4(), title=case_open.title)
        assert _case_form_state_unchanged(ghost, state) is False


@pytest.mark.django_db
class TestInvalidCaseRerenderKeepsFormState:
    def test_invalid_post_rerenders_the_posted_form_state(self, client, staff_user, described_case):
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title=""),
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state"), "Formular muss den Stand der Formularfelder tragen"
        assert parser.values.get("expected_form_state") == hidden.get("expected_form_state")

    def test_invalid_post_keeps_the_rendered_form_state_after_parallel_edit(self, client, staff_user, described_case):
        """Tab B ändert den Titel zwischen Laden und ungültigem POST aus Tab A.

        Die Neu-Anzeige muss den Fingerabdruck aus Tab A weitertragen — nicht
        einen aus dem inzwischen geänderten Datenbankstand neu berechneten,
        der die fremde Änderung beim nächsten Speichern als „unverändert“
        ausgäbe.
        """
        from core.services.case.cases import case_form_state_token

        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")
        current_state = case_form_state_token(Case.objects.get(pk=described_case.pk))
        assert current_state != hidden["expected_form_state"]

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, title=""),
        )

        assert response.status_code == 200
        parser = _HiddenInputs()
        parser.feed(response.content.decode())
        assert parser.values.get("expected_form_state") == hidden["expected_form_state"]
        assert parser.values.get("expected_form_state") != current_state


def _trash(case, staff_user, lead_user, route):
    """Legt den Fall über einen der beiden fachlichen Löschwege in den Papierkorb."""
    fresh = Case.objects.get(pk=case.pk)
    if route == "direct":
        soft_delete_case(fresh, staff_user)
    else:
        approve_case_deletion(request_case_deletion(fresh, staff_user, "Doppelt angelegt"), lead_user)
    assert Case._base_manager.get(pk=case.pk).is_deleted


def _case_update_audits(case):
    return AuditLog.objects.filter(action=AuditLog.Action.CASE_UPDATE, target_id=str(case.pk)).count()


@pytest.mark.django_db
class TestFormStateRescueOnlyForLiveCases:
    """Die Rettung per Fingerabdruck gilt nur für eine lebende Akte.

    Schließen hebt ``updated_at`` an, der Papierkorb nur die Löschmerkmale —
    beide lassen die Formularfelder unverändert. Nach „schließen, dann in den
    Papierkorb“ passt der Fingerabdruck deshalb weiterhin; geschrieben werden
    darf trotzdem nicht, denn die Akte gibt es fachlich nicht mehr.
    """

    @pytest.mark.parametrize("route", ["direct", "four_eyes"])
    def test_close_then_trash_is_rejected_despite_matching_form_state(self, staff_user, lead_user, case_open, route):
        from core.services.case.cases import case_form_state_token
        from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE

        loaded = Case.objects.get(pk=case_open.pk)
        stale_version = loaded.updated_at.isoformat()
        state = case_form_state_token(loaded)
        close_case(Case.objects.get(pk=case_open.pk), lead_user)
        _trash(case_open, staff_user, lead_user, route)
        audits_before = _case_update_audits(case_open)

        with pytest.raises(ValidationError) as excinfo:
            update_case(
                loaded,
                staff_user,
                expected_updated_at=stale_version,
                expected_form_state=state,
                title="Neuer Inhalt nach Papierkorb",
            )

        assert excinfo.value.messages == [str(CASE_WRITE_REDACTED_MESSAGE)]
        stored = Case._base_manager.get(pk=case_open.pk)
        assert stored.title == "Offener Fall", "Eine Akte im Papierkorb darf keinen neuen Inhalt bekommen"
        assert stored.is_deleted
        assert _case_update_audits(case_open) == audits_before

    def test_trash_without_version_change_is_rejected(self, staff_user, lead_user, case_open):
        from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE

        loaded = Case.objects.get(pk=case_open.pk)
        _trash(case_open, staff_user, lead_user, "direct")
        current_version = Case._base_manager.get(pk=case_open.pk).updated_at.isoformat()

        with pytest.raises(ValidationError) as excinfo:
            update_case(loaded, staff_user, expected_updated_at=current_version, title="Neu")

        assert excinfo.value.messages == [str(CASE_WRITE_REDACTED_MESSAGE)]
        assert Case._base_manager.get(pk=case_open.pk).title == "Offener Fall"

    def test_anonymized_case_is_rejected(self, staff_user, lead_user, admin_user, case_open):
        from core.services.case.cases import case_form_state_token
        from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE

        loaded = Case.objects.get(pk=case_open.pk)
        stale_version = loaded.updated_at.isoformat()
        state = case_form_state_token(loaded)
        close_case(Case.objects.get(pk=case_open.pk), lead_user)
        _trash(case_open, staff_user, lead_user, "direct")
        anonymize_case_now(Case._base_manager.get(pk=case_open.pk), admin_user)
        redacted_title = Case._base_manager.get(pk=case_open.pk).title

        with pytest.raises(ValidationError) as excinfo:
            update_case(
                loaded,
                staff_user,
                expected_updated_at=stale_version,
                expected_form_state=state,
                title="Neu",
            )

        assert excinfo.value.messages == [str(CASE_WRITE_REDACTED_MESSAGE)]
        assert Case._base_manager.get(pk=case_open.pk).title == redacted_title

    def test_close_only_is_still_rescued(self, staff_user, lead_user, case_open):
        """Gegenprobe: ohne Papierkorb greift die Rettung wie zugesagt."""
        from core.services.case.cases import case_form_state_token

        loaded = Case.objects.get(pk=case_open.pk)
        stale_version = loaded.updated_at.isoformat()
        state = case_form_state_token(loaded)
        close_case(Case.objects.get(pk=case_open.pk), lead_user)

        update_case(
            loaded,
            staff_user,
            expected_updated_at=stale_version,
            expected_form_state=state,
            title="Neuer Inhalt",
        )

        stored = Case.objects.get(pk=case_open.pk)
        assert stored.title == "Neuer Inhalt"
        assert stored.status == Case.Status.CLOSED

    def test_form_post_after_close_then_trash_is_rejected(self, client, monkeypatch, staff_user, lead_user, case_open):
        """Oberflächenweg: Schließen und Papierkorb committen zwischen dem
        Laden des Falls durch die Bearbeiten-Ansicht und dem Speichern."""
        import core.views.cases as case_views
        from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE

        client.force_login(staff_user)
        hidden = _render_edit_form(client, case_open)
        snapshot = Case.objects.get(pk=case_open.pk)
        original = case_views.get_scoped_object
        calls = []

        def _hooked(*args, **kwargs):
            obj = original(*args, **kwargs)
            if not calls:
                calls.append(obj.pk)
                close_case(Case.objects.get(pk=obj.pk), lead_user)
                _trash(obj, staff_user, lead_user, "direct")
            return obj

        monkeypatch.setattr(case_views, "get_scoped_object", _hooked)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            _form_payload(snapshot, hidden, title="Neuer Inhalt nach Papierkorb"),
        )

        assert calls, "Der Papierkorb muss zwischen Laden und Speichern gelegen haben"
        assert response.status_code == 302
        assert response.url != reverse("core:case_detail", kwargs={"pk": case_open.pk})
        assert str(CASE_WRITE_REDACTED_MESSAGE) in [str(m) for m in get_messages(response.wsgi_request)]
        assert Case._base_manager.get(pk=case_open.pk).title == "Offener Fall"

    def test_rejection_for_trashed_case_shows_message_instead_of_404(
        self, client, monkeypatch, staff_user, lead_user, case_open
    ):
        """Die Absage landet nicht auf der Bearbeiten-Seite einer Akte, die es
        nicht mehr gibt (404), sondern auf einer Seite mit der Vertragsmeldung."""
        import core.views.cases as case_views
        from core.services.case.state_contract import CASE_WRITE_REDACTED_MESSAGE

        client.force_login(staff_user)
        hidden = _render_edit_form(client, case_open)
        snapshot = Case.objects.get(pk=case_open.pk)
        original = case_views.get_scoped_object
        calls = []

        def _hooked(*args, **kwargs):
            obj = original(*args, **kwargs)
            if not calls:
                calls.append(obj.pk)
                close_case(Case.objects.get(pk=obj.pk), lead_user)
                _trash(obj, staff_user, lead_user, "direct")
            return obj

        monkeypatch.setattr(case_views, "get_scoped_object", _hooked)

        response = client.post(
            reverse("core:case_update", kwargs={"pk": case_open.pk}),
            _form_payload(snapshot, hidden, title="Neuer Inhalt nach Papierkorb"),
            follow=True,
        )

        assert calls
        assert response.status_code == 200, "Die Absage darf nicht in einer 404-Seite enden"
        assert response.redirect_chain[-1][0] != reverse("core:case_update", kwargs={"pk": case_open.pk})
        assert str(CASE_WRITE_REDACTED_MESSAGE) in response.content.decode()
        assert Case._base_manager.get(pk=case_open.pk).title == "Offener Fall"

    def test_conflict_on_live_case_still_returns_to_the_form(self, client, staff_user, described_case):
        """A conflict on a live case retains the bound form with a comparison."""
        client.force_login(staff_user)
        hidden = _render_edit_form(client, described_case)
        snapshot = Case.objects.get(pk=described_case.pk)
        update_case(Case.objects.get(pk=described_case.pk), staff_user, title="Titel aus Tab B")

        response = client.post(
            reverse("core:case_update", kwargs={"pk": described_case.pk}),
            _form_payload(snapshot, hidden, description="Aus Tab A"),
        )

        assert response.status_code == 200
        assert not response.has_header("Location")
        assert response.context["has_edit_conflict"]

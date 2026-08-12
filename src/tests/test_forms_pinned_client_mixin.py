"""Gemeinsamer Vertrag von Pin-Guard und Widerrufs-Gate (Refs #1834).

Die Personen-Auflösung lag dreimal fast wortgleich in ``CaseForm``,
``WorkItemForm`` und ``EventMetaForm``. Die Driftkosten sind belegt: die
Fix-Kette #1771 → #1786 → #1791 musste jedes Mal an drei Kopien nachgezogen
werden, und der #1791-Befund (leerer POST-Wert rutschte durch das
``if client_id``-Short-Circuit) existierte genau deshalb.

Diese Datei prüft den Vertrag **einmal für alle Formulare**, die ihn tragen —
sie ist damit die Stelle, an der ein künftiger Fix nicht mehr an einer Kopie
vorbeilaufen kann:

* **Pin-Guard** (``CaseForm``/``WorkItemForm``): fehlender ``client``-Key =
  Normalfall (Read-only-Formular rendert kein Feld), abweichender ODER leerer
  Wert = Manipulation → Formularfehler.
* **Widerrufs-Gate** (alle drei): jede Person — auch die gepinnte aus dem
  Bestand — läuft durch ``resolve_writable_client``.

Die formularspezifischen Eigenheiten (Pflichtfeld bei ``CaseForm``, ``None``
bei ``WorkItemForm``/``EventMetaForm``, eigene Fehlermeldung je Formular)
bleiben bewusst in den jeweiligen ``clean_client()``-Methoden und werden hier
mitgeprüft, damit die Zusammenführung sie nicht einebnet.
"""

import pytest

from core.forms.cases import CaseForm
from core.forms.events import EventMetaForm
from core.forms.mixins import PinnedClientMixin
from core.forms.workitems import WorkItemForm
from core.models import Client, WorkItem
from tests._form_helpers import assert_field_error, assert_form_valid

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_anonymized(client_identified):
    """Anonymisiert, aber NICHT im Papierkorb — der K-Anon-Bridge-Zustand (#1771)."""
    client_identified.is_active = False
    client_identified.pseudonym = "anonymisiert"
    client_identified.save(update_fields=["is_active", "pseudonym"])
    return client_identified


def _case_form(facility, user, *, client_value=None, pinned_client=None):
    data = {"title": "Fall", "description": ""}
    if client_value is not None:
        data["client"] = client_value
    return CaseForm(data=data, facility=facility, user=user, pinned_client=pinned_client)


def _workitem_form(facility, user, *, client_value=None, pinned_client=None):
    data = {
        "item_type": WorkItem.ItemType.TASK,
        "title": "Aufgabe",
        "priority": WorkItem.Priority.NORMAL,
    }
    if client_value is not None:
        data["client"] = client_value
    return WorkItemForm(data=data, facility=facility, pinned_client=pinned_client)


def _event_form(facility, user, doc_type, *, client_value=None):
    data = {"document_type": str(doc_type.pk), "occurred_at": "2026-05-16T10:00"}
    if client_value is not None:
        data["client"] = client_value
    return EventMetaForm(data=data, facility=facility, user=user)


PIN_FORMS = [pytest.param(_case_form, id="case"), pytest.param(_workitem_form, id="workitem")]


class TestMixinContract:
    """Die Mechanik existiert genau einmal."""

    @pytest.mark.parametrize("form_class", [CaseForm, WorkItemForm, EventMetaForm])
    def test_forms_share_the_mixin(self, form_class):
        assert issubclass(form_class, PinnedClientMixin)

    def test_pin_defaults_to_none(self):
        """Ohne ``pinned_client``-Kwarg gilt kein Pin — auch für Formulare ohne Pin-Pfad."""
        assert PinnedClientMixin.pinned_client is None


class TestPinGuard:
    """Refs #1786/#1791 — gepinnt gewinnt der Bestand, Abweichungen sind Fehler."""

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_missing_key_keeps_the_pinned_client(self, build_form, facility, staff_user, client_identified):
        """Der legitime Normalfall: das Read-only-Formular sendet kein ``client``-Feld."""
        form = build_form(facility, staff_user, pinned_client=client_identified)
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_diverging_value_is_rejected(self, build_form, facility, staff_user, client_identified, client_qualified):
        form = build_form(facility, staff_user, client_value=str(client_qualified.pk), pinned_client=client_identified)
        assert_field_error(form, "client", "kann nach dem Anlegen nicht geändert werden")

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_empty_value_is_rejected(self, build_form, facility, staff_user, client_identified):
        """#1791: das Entfernen darf nicht durch das ``if client_id``-Short-Circuit rutschen."""
        form = build_form(facility, staff_user, client_value="", pinned_client=client_identified)
        assert_field_error(form, "client", "kann nach dem Anlegen nicht geändert werden")

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_matching_value_passes(self, build_form, facility, staff_user, client_identified):
        form = build_form(facility, staff_user, client_value=str(client_identified.pk), pinned_client=client_identified)
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified


class TestRevocationGateAppliesToEveryForm:
    """Refs #1771 — das Pinning ersetzt das Widerrufs-Gate nicht, es kommt dazu."""

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_pinned_anonymized_client_is_rejected(self, build_form, facility, staff_user, client_anonymized):
        form = build_form(facility, staff_user, pinned_client=client_anonymized)
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_pinned_trashed_client_is_rejected(self, build_form, facility, staff_user, client_trashed):
        form = build_form(facility, staff_user, pinned_client=client_trashed)
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    @pytest.mark.parametrize("build_form", PIN_FORMS)
    def test_posted_anonymized_client_is_rejected_without_pin(
        self, build_form, facility, staff_user, client_anonymized
    ):
        form = build_form(facility, staff_user, client_value=str(client_anonymized.pk))
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_event_form_rejects_anonymized_client(self, facility, staff_user, doc_type_contact, client_anonymized):
        """``EventMetaForm`` kennt keinen Pin (das Gate sitzt view-seitig), teilt aber den Resolver."""
        form = _event_form(facility, staff_user, doc_type_contact, client_value=str(client_anonymized.pk))
        assert_field_error(form, "client", "gelöscht oder anonymisiert")

    def test_event_form_accepts_live_client(self, facility, staff_user, doc_type_contact, client_identified):
        form = _event_form(facility, staff_user, doc_type_contact, client_value=str(client_identified.pk))
        assert_form_valid(form)
        assert form.cleaned_data["client"] == client_identified


class TestPerFormDivergenceStays:
    """Die Unterschiede sind gewollt und dürfen nicht wegabstrahiert werden."""

    def test_case_form_requires_a_client(self, facility, staff_user):
        form = _case_form(facility, staff_user)
        assert_field_error(form, "client", "Bitte eine Person auswählen")

    def test_workitem_form_allows_no_client(self, facility, staff_user):
        form = _workitem_form(facility, staff_user)
        assert_form_valid(form)
        assert form.cleaned_data["client"] is None

    def test_event_form_allows_no_client(self, facility, staff_user, doc_type_contact):
        form = _event_form(facility, staff_user, doc_type_contact)
        assert_form_valid(form)
        assert form.cleaned_data["client"] is None

    def test_workitem_form_keeps_its_own_missing_error(self, facility, staff_user, other_facility):
        """Fremde Facility → ``Ungültige Person-ID`` (nicht der Fall-/Event-Text)."""
        foreign = Client.objects.create(
            facility=other_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Fremd-01",
        )
        form = _workitem_form(facility, staff_user, client_value=str(foreign.pk))
        assert_field_error(form, "client", "Ungültige Person-ID")

    def test_case_form_keeps_its_own_missing_error(self, facility, staff_user, other_facility):
        foreign = Client.objects.create(
            facility=other_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Fremd-02",
        )
        form = _case_form(facility, staff_user, client_value=str(foreign.pk))
        assert_field_error(form, "client", "Person existiert nicht")

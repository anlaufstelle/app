"""Pin-Tests fuer zwei Prio-1-Testluecken in ``core.forms.events`` (Welle F, 0.93).

Quelle: Survivor-Triage des ``mutation-delta``-Laufs zum Meilenstein release-0.93,
Vorgabe ``welle-f-0.93/reports/pin-events.json``. Modul unter Test:
:mod:`core.forms.events` (``src/core/forms/events.py``).

Gepinnt werden genau zwei Mutanten:

* ``xǁEventMetaFormǁ__init____mutmut_43`` — ``case_qs.select_related("client")``
  wird zu ``select_related(None)``. Das Ergebnis-Queryset ist inhaltlich
  identisch (gleiche Faelle, gleiche Validierungs-Whitelist), aber
  ``_case_label`` liest fuer JEDEN Fall ``case.client.pseudonym``: ohne den
  Join wird das Rendern des ``case``-Dropdowns ein N+1. Beobachtbar ist das an
  zwei Stellen — an ``query.select_related`` selbst und an der Query-Zahl.
* ``xǁEventMetaFormǁclean_client__mutmut_7`` — ``missing_error=_("Person
  existiert nicht.")`` wird zu ``missing_error=None``. Das Gate bleibt
  wirksam (das Formular ist weiter ungueltig), aber
  ``resolve_writable_client`` reicht den Parameter direkt in
  ``raise ValidationError(missing_error)`` — die Meldung am Feld ``client``
  lautet dann woertlich ``"None"`` und landet so im Offline-Dead-Letter-/
  Replay-UI. Die Schwesterformulare haben ihre eigene Meldung gepinnt
  (``test_forms_cases.py``, ``test_forms_pinned_client_mixin.py``), nur das
  Event-Pendant fehlte.

Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen
EXAKT (``==``), nie per Teilstring — mutmut erzeugt ``"XX<text>XX"``-Varianten,
die ein Teilstring-Match ueberleben wuerden; der Sprachkontext wird per
``translation.override("de")`` festgenagelt; Fixtures kommen aus
``src/tests/conftest.py``, es gibt keine Fixture-Faken (echte Model-Objekte);
KEIN Import aus ``test_rls_functional`` (dessen modulweiter Skip unter
``MUTANT_UNDER_TEST`` wuerde jeden Pin entwerten).
"""

import uuid

import pytest
from django.utils import translation

from core.forms.events import EventMetaForm, _case_label
from core.models import Case

pytestmark = pytest.mark.django_db


def _event_form(facility, user, doc_type, *, client_value=None):
    """Gebundenes ``EventMetaForm`` wie in ``test_forms_pinned_client_mixin.py``."""
    data = {"document_type": str(doc_type.pk), "occurred_at": "2026-05-16T10:00"}
    if client_value is not None:
        data["client"] = client_value
    return EventMetaForm(data=data, facility=facility, user=user)


class TestEventMetaFormCaseChoicesJoinTheClient:
    """Das ``case``-Dropdown holt seine Personen im selben Zugriff mit.

    ``EventMetaForm.__init__`` setzt auf dem Fall-Queryset ausdruecklich
    ``select_related("client")``, weil ``_case_label`` (``core/forms/events.py``)
    fuer jeden Eintrag ``case.client.pseudonym`` liest. Der Join ist damit kein
    Schmuck, sondern die Bedingung dafuer, dass die Auswahlliste in EINER
    Abfrage entsteht.
    """

    def test_case_queryset_declares_the_client_join(self, facility, staff_user, case_open):
        """Pin: xǁEventMetaFormǁ__init____mutmut_43 — select_related("client") -> select_related(None).

        Django speichert die angeforderten Joins in ``Query.select_related``.
        Im Original ist das exakt ``{"client": {}}``; ``select_related(None)``
        loescht die Liste, der Mutant liefert dort ``False``.
        """
        form = EventMetaForm(facility=facility, user=staff_user)
        assert form.fields["case"].queryset.query.select_related == {"client": {}}

    def test_rendering_all_case_labels_costs_exactly_one_query(
        self, facility, staff_user, case_open, client_qualified, django_assert_num_queries
    ):
        """Pin: xǁEventMetaFormǁ__init____mutmut_43 — select_related("client") -> select_related(None).

        Zwei offene Faelle mit je eigener Person: mit dem Join kostet das
        Auslesen aller Dropdown-Beschriftungen genau 1 Query, der Mutant
        braucht 3 (eine je ``case.client``-Zugriff obendrauf) — der
        ``django_assert_num_queries(1)``-Block schlaegt dort fehl.
        """
        Case.objects.create(
            facility=facility,
            client=client_qualified,
            title="Zweiter offener Fall",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        case_queryset = EventMetaForm(facility=facility, user=staff_user).fields["case"].queryset
        with django_assert_num_queries(1):
            labels = sorted(_case_label(case) for case in case_queryset)
        assert labels == ["Offener Fall – Test-ID-01", "Zweiter offener Fall – Test-QU-01"]


class TestEventMetaFormKeepsItsOwnMissingClientError:
    """Die Meldung fuer eine nicht aufloesbare Person gehoert dem Formular.

    ``resolve_writable_client`` (``core/services/client/main.py``) haelt die
    Meldung bewusst aufruferseitig, damit die drei Formulare ihre gewachsenen
    Texte behalten: ``CaseForm`` und ``EventMetaForm`` sagen "Person existiert
    nicht.", ``WorkItemForm`` "Ungueltige Person-ID". Fremde Facility und
    unbekannte UUID sind von aussen ununterscheidbar derselbe Fall.
    """

    def test_foreign_client_gets_the_forms_own_message(self, facility, staff_user, doc_type_contact, foreign_client):
        """Pin: xǁEventMetaFormǁclean_client__mutmut_7 — missing_error=_("Person existiert nicht.") -> None.

        Eine Person der zweiten Facility ist ausserhalb des Scopes. Das
        Original meldet am Feld ``client`` exakt ``["Person existiert
        nicht."]``; mit ``missing_error=None`` liefert
        ``ValidationError(None).messages`` stattdessen ``["None"]``.
        """
        with translation.override("de"):
            form = _event_form(facility, staff_user, doc_type_contact, client_value=str(foreign_client.pk))
            assert form.is_valid() is False
            assert list(form.errors["client"]) == ["Person existiert nicht."]

    def test_unknown_uuid_gets_the_same_message(self, facility, staff_user, doc_type_contact):
        """Pin: xǁEventMetaFormǁclean_client__mutmut_7 — missing_error=_("Person existiert nicht.") -> None.

        Der zweite Zweig derselben Meldung: eine UUID, die es nirgends gibt,
        darf sich von der fremden Facility nicht unterscheiden lassen. Auch
        hier steht beim Mutanten ``"None"`` statt des Textes.
        """
        with translation.override("de"):
            form = _event_form(facility, staff_user, doc_type_contact, client_value=str(uuid.uuid4()))
            assert form.is_valid() is False
            assert list(form.errors["client"]) == ["Person existiert nicht."]

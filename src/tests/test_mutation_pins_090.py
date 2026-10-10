"""Pin-Tests aus der Survivor-Triage zum Checkpoint release-0.90 (Refs #1953).

Der ``mutation-delta``-Lauf gegen BASE ``v0.89.0`` liess in den vier
Delta-Dateien 260 Mutanten ueberleben. Diese Datei pinnt die als
Prioritaet 1 eingestuften — vier Bloecke in der Reihenfolge des Berichts:

* ``core.services.deletion`` — Vier-Augen-Genehmigerpool und die
  fail-closed Ziel-Aufloesung der Loeschantrags-Liste (Refs #2040/#1736);
* ``core.services.dashboard.activity`` — der Anzeige-Vertrag des
  Aktivitaets-Renderers, dem eine ganze Testebene gefehlt hat (Refs #2044);
* ``core.services.client.main`` — DSGVO-/Audit-Pfade der Anonymisierung;
* ``core.forms.clients`` — die fail-closed-Zweige der Formulare.

Jeder Test nennt in seiner Docstring den Mutanten, den er toetet. Jeder
dieser Mutanten wurde von Hand in der Quelle nachgestellt und der Test
nachweislich rot; als aequivalent erkannte Mutanten sind dort ebenfalls
als solche vermerkt, statt sie stillschweigend wegzulassen.

Warum eine eigene Datei statt Streuung in die Bestandsdateien: die
Zuordnung Mutant -> Test ist der Nachweis, den die Cut-Policy verlangt
(``docs/dev/release-checklist.md`` § 0.5(3)). Muster und Begruendung wie
``src/tests/test_mutation_pins_089.py``.

**Konvention Meldungstexte:** Die Zusicherungen vergleichen Label-Texte
EXAKT (``==``), nicht per Teilstring. mutmut erzeugt zu jedem
String-Literal u.a. die Variante ``"XX<text>XX"`` — ein
Teilstring-Vergleich (``in``) findet den Originaltext darin weiterhin und
liesse den Mutanten ueberleben. Bei den generischen Ersatz-Labels
(``"Fall"``, ``"Ereignis"``, ``"Person (nicht mehr auffindbar)"``) ist der
exakte Vergleich zugleich die fachliche Zusicherung: genau dieser Text und
kein Titel, Pseudonym oder Dokumentationstyp-Name steht dort.

Der Sprachkontext wird explizit auf Deutsch festgenagelt
(``translation.override("de")``), weil ``gettext_lazy`` erst beim ``str()``
aufloest und die Suite auch englische Ausgaben kennt.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone, translation
from django.utils.formats import date_format

from core.forms.clients import ClientForm, ClientInlineCreateForm
from core.models import (
    Activity,
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    Event,
    Milestone,
    WorkItem,
)
from core.services.client import main as client_main
from core.services.client.main import (
    anonymize_eligible_soft_deleted_clients,
    pending_merge_client_ids,
    update_client_stage,
)
from core.services.dashboard.activity import ACTIVITY_CONTEXT_SCHEMA, render_activity_summary
from core.services.deletion import annotate_deletion_targets, eligible_deletion_confirmers
from tests._sensitivity_helpers import make_inherited_fields_explicit

pytestmark = pytest.mark.django_db


def _request(facility, staff_user, target_type, target_id, **kwargs):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type=target_type,
        target_id=target_id,
        reason="Grund",
        requested_by=staff_user,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# core.services.deletion.eligible_deletion_confirmers — Genehmiger-Pool
# ---------------------------------------------------------------------------


class TestEligibleDeletionConfirmersIsScoped:
    """Der Vier-Augen-Pool ist auf die eigene Einrichtung und aktive Konten begrenzt.

    ``core_user`` traegt — anders als die fachlichen Tabellen — KEINE
    RLS-Policy (Rollen/Logins sind organisationsweit). Die
    Mandantentrennung dieser Abfrage haengt damit allein am
    ``facility=``-Filter im Servicecode; faellt er weg, deckt keine
    Datenbankschicht das ab. Die Bestandstests pruefen den Pool nur
    innerhalb einer Einrichtung und liessen beide Filter offen.
    """

    def test_confirmer_of_another_facility_is_not_offered(
        self, facility, admin_user, second_facility, second_facility_user
    ):
        """Toetet ``x_eligible_deletion_confirmers__mutmut_5``.

        Der Mutant streicht ``facility=facility`` aus dem Filter. Ohne
        Pin wuerde die Antragsseite Genehmigerinnen einer FREMDEN
        Einrichtung namentlich anbieten (Klarname + Benutzername) und der
        Setup-Guard „kein moeglicher Genehmiger" bliebe stumm, obwohl die
        eigene Einrichtung keinen hat.
        """
        second_facility_user.can_confirm_deletion = True
        second_facility_user.save(update_fields=["can_confirm_deletion"])

        assert list(eligible_deletion_confirmers(facility)) == [admin_user]
        assert list(eligible_deletion_confirmers(second_facility)) == [second_facility_user]

    def test_deactivated_confirmer_is_not_offered(self, facility, admin_user):
        """Toetet ``x_eligible_deletion_confirmers__mutmut_6``.

        Der Mutant streicht ``is_active=True``. Ein deaktiviertes Konto
        kann sich nicht anmelden und damit nichts genehmigen; wuerde es
        weiter im Pool stehen, meldete der Setup-Guard faelschlich einen
        entscheidungsfaehigen Antragsweg — der Antrag bliebe still liegen.
        """
        admin_user.is_active = False
        admin_user.save(update_fields=["is_active"])

        assert list(eligible_deletion_confirmers(facility)) == []


# ---------------------------------------------------------------------------
# core.services.deletion.annotate_deletion_targets — Ziel-Aufloesung der Liste
# ---------------------------------------------------------------------------


@pytest.fixture
def trashed_client(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Papierkorb-01",
        created_by=staff_user,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def trashed_case(facility, client_identified, staff_user):
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Fall im Papierkorb",
        created_by=staff_user,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def trashed_event(facility, client_identified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        created_by=staff_user,
        is_deleted=True,
    )


class TestEveryRequestInTheListIsAnnotated:
    """Die Schleife annotiert die GANZE Liste, nicht nur bis zum ersten Ziel.

    Alle Bestandstests reichen genau EINEN Antrag herein. Damit ist jedes
    ``continue`` in der Schleife von einem ``break`` ununterscheidbar —
    fuenf Mutanten tauschen genau das. In der echten Liste blieben dann
    alle Antraege ab dem zweiten ohne ``target_label``/``target_url``:
    das Template rendert leere Zellen, die Entscheiderin sieht nicht mehr,
    worueber sie entscheidet.
    """

    def test_all_six_target_shapes_are_annotated(
        self,
        facility,
        staff_user,
        lead_user,
        client_identified,
        case_open,
        sample_event,
    ):
        """Toetet die fuenf ``continue`` -> ``break``-Mutanten
        ``x_annotate_deletion_targets__mutmut_64/74/86/96/107``.

        Die Reihenfolge ist Teil des Pins: hinter JEDEM ``continue``-Zweig
        steht noch mindestens ein weiterer Antrag, sonst waere ``break``
        an der letzten Position wirkungsgleich. Der Ereignis-Treffer steht
        deshalb bewusst am Ende — er ist der einzige Zweig ohne
        ``continue``.
        """
        requests = [
            _request(facility, staff_user, DeletionRequest.TargetType.CLIENT, client_identified.pk),
            _request(facility, staff_user, DeletionRequest.TargetType.CLIENT, uuid.uuid4()),
            _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk),
            _request(facility, staff_user, DeletionRequest.TargetType.CASE, uuid.uuid4()),
            _request(facility, staff_user, DeletionRequest.TargetType.EVENT, uuid.uuid4()),
            _request(facility, staff_user, DeletionRequest.TargetType.EVENT, sample_event.pk),
        ]

        with translation.override("de"):
            annotate_deletion_targets(requests, facility, lead_user)

            for position, dr in enumerate(requests):
                assert getattr(dr, "target_label", None), f"Antrag {position} blieb unannotiert"
                assert hasattr(dr, "target_url"), f"Antrag {position} blieb unannotiert"

            # Toetet zusaetzlich ``x_annotate_deletion_targets__mutmut_59/60/61``:
            # der Bestandstest prueft fuer den verschwundenen Personen-Ziel
            # nur, DASS ein Label steht — nicht WELCHES. Der exakte Text ist
            # hier die Zusicherung, dass kein Pseudonym geraten wird.
            assert str(requests[1].target_label) == "Person (nicht mehr auffindbar)"
            assert requests[1].target_url == ""


class TestFailClosedTargetsCarryNoLink:
    """Ein Ziel im Papierkorb bekommt ein Label, aber KEINEN Link.

    Die Detailansicht antwortet fuer ein soft-geloeschtes Ziel mit 404 —
    ein Link dorthin ist eine Sackgasse. Fuer Personen ist das getestet
    (``test_deletion_request_list_targets.py``), fuer Faelle, Ereignisse
    und Merge-Ziele war es offen. Die ueberlebenden Mutanten ersetzen den
    leeren String durch ``None`` oder durch die ``"XXXX"``-Variante —
    letztere ist WAHR und erzeugt im Template genau den Link, den die
    fail-closed-Regel verbietet.
    """

    def test_trashed_case_target_has_label_but_no_link(self, facility, staff_user, lead_user, trashed_case):
        """Toetet ``x_annotate_deletion_targets__mutmut_88/89``."""
        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, trashed_case.pk)

        annotate_deletion_targets([dr], facility, lead_user)

        assert dr.target_label == trashed_case.title
        assert dr.target_url == ""

    def test_trashed_event_target_has_label_but_no_link(self, facility, staff_user, lead_user, trashed_event):
        """Toetet ``x_annotate_deletion_targets__mutmut_118``."""
        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, trashed_event.pk)

        annotate_deletion_targets([dr], facility, lead_user)

        assert dr.target_url == ""

    def test_trashed_merge_target_has_label_but_no_link(
        self, facility, staff_user, lead_user, client_identified, trashed_client
    ):
        """Toetet ``x_annotate_deletion_targets__mutmut_45/46``."""
        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            client_identified.pk,
            merge_into=trashed_client,
        )

        annotate_deletion_targets([dr], facility, lead_user)

        assert dr.merge_target_label == trashed_client.pseudonym
        assert dr.merge_target_url == ""


class TestMergeAnnotationDefaults:
    """Ein gewoehnlicher Loeschantrag traegt LEERE Merge-Felder.

    ``merge_target_label``/``merge_target_url`` werden fuer jeden Antrag
    vorbelegt, damit das Template den Merge-Chip nur dort zeigt, wo es
    wirklich eine Zusammenfuehrung gibt. Die Mutanten setzen stattdessen
    ``None`` (das Template schriebe „None" in die Zelle) oder ``"XXXX"``
    (wahr — der Chip erschiene an JEDEM Antrag, samt Link-Rahmen).
    """

    def test_plain_request_has_empty_merge_fields(self, facility, staff_user, lead_user, client_identified):
        """Toetet ``x_annotate_deletion_targets__mutmut_31/32/33/34``."""
        dr = _request(facility, staff_user, DeletionRequest.TargetType.CLIENT, client_identified.pk)

        annotate_deletion_targets([dr], facility, lead_user)

        assert dr.merge_target_label == ""
        assert dr.merge_target_url == ""


class TestGenericLabelsStayGeneric:
    """Die fail-closed-Ersatzlabel nennen genau ihren generischen Text.

    Fuer die Assistenz (kein Fall-Zugriff) und fuer ein durch die
    Sensibilitaets-Policy verborgenes Ereignis darf im Label WEDER der
    Falltitel NOCH der Dokumentationstyp-Name stehen. Die Bestandstests
    pruefen das negativ (``title not in body``) — eine Negativ-Zusicherung
    ueberlebt jede Textmutation. Der exakte Vergleich pinnt zugleich, dass
    dort ueberhaupt ein Text steht (die ``None``-Mutanten) und dass es
    genau der generische ist.
    """

    def test_case_invisible_to_assistant_is_labelled_generically(self, facility, staff_user, assistant_user, case_open):
        """Toetet ``x_annotate_deletion_targets__mutmut_79/81/82/83/84/85``."""
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        dr = _request(facility, staff_user, DeletionRequest.TargetType.CASE, case_open.pk)

        with translation.override("de"):
            annotate_deletion_targets([dr], facility, assistant_user)

            assert str(dr.target_label) == "Fall"
        assert dr.target_url == ""

    def test_event_invisible_by_sensitivity_is_labelled_generically(
        self, facility, staff_user, assistant_user, sample_event
    ):
        """Toetet ``x_annotate_deletion_targets__mutmut_102/103/104``."""
        make_inherited_fields_explicit(sample_event.document_type)
        sample_event.document_type.sensitivity = DocumentType.Sensitivity.HIGH
        sample_event.document_type.save(update_fields=["sensitivity"])
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, sample_event.pk)

        with translation.override("de"):
            annotate_deletion_targets([dr], facility, assistant_user)

            assert str(dr.target_label) == "Ereignis"

    def test_unresolvable_merge_target_is_labelled_generically(
        self, facility, staff_user, lead_user, client_identified, foreign_client
    ):
        """Toetet ``x_annotate_deletion_targets__mutmut_39/40/41/42/43``.

        Das Merge-Ziel wird — anders als das Antragsziel — ueber die
        ``merge_into``-FK gefuehrt, aufgeloest wird es aber ueber
        ``Client.objects.for_facility(facility)``. Faellt es aus dieser
        Bulk-Query (hier: Person einer fremden Einrichtung), muss das
        Label generisch bleiben und darf kein Pseudonym raten.
        """
        dr = _request(
            facility,
            staff_user,
            DeletionRequest.TargetType.CLIENT,
            client_identified.pk,
            merge_into=foreign_client,
        )

        with translation.override("de"):
            annotate_deletion_targets([dr], facility, lead_user)

            assert str(dr.merge_target_label) == "Person (nicht mehr auffindbar)"
        assert dr.merge_target_url == ""


class TestEventLabelCarriesTheEventDate:
    """Das Ereignis-Label nennt das Datum DES EREIGNISSES, nicht heute.

    ``timezone.localtime(None)`` liefert die aktuelle Zeit — der Mutant
    beschriftet damit jeden Antrag mit dem heutigen Datum. Auf der
    Antragsliste ist das Datum das einzige Unterscheidungsmerkmal
    zwischen mehreren Ereignissen desselben Dokumentationstyps; ein
    falsches Datum fuehrt zur Freigabe der falschen Tilgung.
    """

    def test_label_names_document_type_and_the_event_date(
        self, facility, staff_user, lead_user, client_identified, doc_type_contact
    ):
        """Toetet ``x_annotate_deletion_targets__mutmut_108/110/112/113/114/115``."""
        occurred = timezone.make_aware(datetime(2024, 3, 7, 9, 30))
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=occurred,
            created_by=staff_user,
        )
        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, event.pk)

        with translation.override("de"):
            annotate_deletion_targets([dr], facility, lead_user)

            expected = date_format(timezone.localtime(occurred), "DATE_FORMAT")
            assert str(dr.target_label) == f"{doc_type_contact.name} — {expected}"
            assert expected != date_format(timezone.localtime(timezone.now()), "DATE_FORMAT")


class TestEventLookupStaysInsideTheFacility:
    """Die Ereignis-Bulk-Query ist auf die eigene Einrichtung gescoped.

    ``Event.objects.visible_to(user)`` filtert NUR nach Sensibilitaet,
    nicht nach Einrichtung (``core/models/managers.py``). Faellt
    ``facility=facility`` weg, haengt die Mandantentrennung dieser Query
    allein an der RLS-Policy — der Servicecode selbst gaebe den
    Dokumentationstyp-Namen eines fremden Ereignisses als Label aus.
    """

    def test_foreign_event_is_not_resolved(self, facility, staff_user, lead_user, foreign_event):
        """Toetet ``x_annotate_deletion_targets__mutmut_26``."""
        dr = _request(facility, staff_user, DeletionRequest.TargetType.EVENT, foreign_event.pk)

        with translation.override("de"):
            annotate_deletion_targets([dr], facility, lead_user)

            assert str(dr.target_label) == "Ereignis"
        assert dr.target_url == ""


# ===========================================================================
# core.services.dashboard.activity — Anzeige-Vertrag des Aktivitaets-Renderers
# ===========================================================================
#
# Strukturbefund der Triage (Refs #2044): 176 der 362 Mutanten dieses Moduls
# ueberlebten, verteilt auf nur drei Funktionen. Ursache ist NICHT eine
# Haeufung von Einzelluecken, sondern eine fehlende Testebene: der gerenderte
# Satz wird nirgends als Vertrag geprueft. Die Actions werden nur dort
# beruehrt, wo ein anderer Test sie beilaeufig erzeugt — dann per Substring
# (``pseudonym in html``) oder negativ (``assert "Stern-9" not in ...``).
# Beides ueberlebt jede Textmutation, und acht der achtzehn Action-Zweige
# werden ueberhaupt nie ausgefuehrt.
#
# Die drei Klassen unten schliessen genau diese Ebene: exakter Satz je Action,
# einmal mit lebendem Ziel und einmal mit verschwundenem Ziel, plus der
# Fallback-Renderer.


def _activity(facility, staff_user, *, action, target_model, target_id, verb, **context):
    ctx = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": action}
    ctx.update(context)
    return Activity.objects.create(
        facility=facility,
        actor=staff_user,
        verb=verb,
        target_type=ContentType.objects.get_for_model(target_model),
        target_id=target_id,
        context=ctx,
    )


#: (Action-Wert, Zusatzkontext, erwarteter Satz ohne lebendes Ziel)
GONE_TARGET_CASES = [
    ("client_created", {}, "Person angelegt"),
    ("client_qualified", {}, "Person qualifiziert"),
    ("client_updated", {"kind": "renamed"}, "Person umbenannt"),
    ("client_updated", {}, "Person aktualisiert"),
    ("client_trashed", {}, "Person in den Papierkorb verschoben"),
    ("client_merged", {}, "Person mit einem Duplikat zusammengeführt"),
    ("client_unmerged", {}, "Zusammenführung mit einem Duplikat aufgehoben"),
    ("event_created", {}, "Dokumentation angelegt"),
    ("event_deleted", {}, "Dokumentation gelöscht"),
    ("workitem_created", {}, "Aufgabe angelegt"),
    ("workitem_updated", {}, "Aufgabe aktualisiert"),
    ("workitem_completed", {}, "Aufgabe erledigt"),
    ("workitem_reopened", {}, "Aufgabe wiedereröffnet"),
    ("workitem_recurrence_created", {}, "Wiederkehrende Folgeaufgabe angelegt"),
    ("case_created", {}, "Fall eröffnet"),
    ("case_closed", {}, "Fall geschlossen"),
    ("case_reopened", {}, "Fall wiedereröffnet"),
    ("case_trashed", {}, "Fall in den Papierkorb verschoben"),
    ("milestone_deleted", {}, "Meilenstein gelöscht"),
]


class TestRendererFallsBackWhenTheTargetIsGone:
    """Jede Action hat einen lesbaren Satz OHNE lebendes Ziel.

    Der Docstring von ``render_activity_summary`` erklaert diesen Pfad als
    bewusste Zusicherung: verschwindet das referenzierte Objekt, faellt der
    Renderer „deliberately" auf einen generischen, aber lesbaren Satz
    zurueck. Elf der fuenfzehn ``if target is None``-Zeilen wurden von
    keinem Test ausgefuehrt; acht ``is None`` -> ``is not None``-Mutanten
    ueberlebten. Unter ihnen wuerde eine Zeitstrom-/Dashboard-Zeile mit
    getilgtem Ziel einen ``AttributeError`` werfen statt zu rendern —
    ausgerechnet nach einer Anonymisierung, also genau dann, wenn die
    Zeile gebraucht wird.
    """

    @pytest.mark.parametrize(("action", "context", "expected"), GONE_TARGET_CASES)
    def test_sentence_without_a_live_target(self, facility, staff_user, action, context, expected):
        """Toetet u. a. ``x_render_activity_summary__mutmut_70/83/117/142/168/181/194/220``
        (die acht invertierten ``target is None``-Guards) sowie alle
        Textmutanten der Fallback-Saetze.
        """
        activity = _activity(
            facility,
            staff_user,
            action=action,
            target_model=Client,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
            **context,
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == expected


class TestRendererNamesTheLiveTarget:
    """Mit lebendem Ziel steht der Bezeichner des Ziels im Satz.

    Die Bestandstests pruefen fuer mehrere Actions nur ``pseudonym in
    html``. Ein Teilstring-Vergleich ueberlebt die ``"XX…XX"``-Variante
    jedes Literals und jede Vertauschung der ``%``-Format-Schluessel; der
    exakte Vergleich pinnt Satzbau UND eingesetzten Wert.
    """

    def test_client_branches(self, facility, staff_user, client_identified):
        """Toetet die Textmutanten der fuenf ``CLIENT_*``-Zweige mit Ziel."""
        pseudonym = client_identified.pseudonym
        expectations = [
            ("client_created", {}, f"Person {pseudonym} angelegt"),
            ("client_qualified", {}, f"{pseudonym} qualifiziert"),
            ("client_updated", {"kind": "renamed"}, f"Person {pseudonym} umbenannt"),
            ("client_updated", {}, f"Person {pseudonym} aktualisiert"),
            ("client_trashed", {}, f"Person {pseudonym} in den Papierkorb verschoben"),
            ("client_merged", {}, f"Person {pseudonym} mit einem Duplikat zusammengeführt"),
        ]
        with translation.override("de"):
            for action, context, expected in expectations:
                activity = _activity(
                    facility,
                    staff_user,
                    action=action,
                    target_model=Client,
                    target_id=client_identified.pk,
                    verb=Activity.Verb.UPDATED,
                    **context,
                )
                assert render_activity_summary(activity) == expected

    def test_event_branches(self, facility, staff_user, client_identified, doc_type_contact):
        """Toetet die Textmutanten von ``EVENT_CREATED``/``EVENT_DELETED``.

        Der Zweig ohne Person (`` return document_type``) ist die einzige
        Stelle, an der der Renderer den Dokumentationstyp nackt ausgibt —
        er wird hier eigens abgedeckt.
        """
        with_client = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            created_by=staff_user,
        )
        without_client = Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            created_by=staff_user,
        )
        name = doc_type_contact.name
        with translation.override("de"):
            created = _activity(
                facility,
                staff_user,
                action="event_created",
                target_model=Event,
                target_id=with_client.pk,
                verb=Activity.Verb.CREATED,
            )
            assert render_activity_summary(created) == f"{name} für {client_identified.pseudonym}"

            anonymous = _activity(
                facility,
                staff_user,
                action="event_created",
                target_model=Event,
                target_id=without_client.pk,
                verb=Activity.Verb.CREATED,
            )
            assert render_activity_summary(anonymous) == name

            deleted = _activity(
                facility,
                staff_user,
                action="event_deleted",
                target_model=Event,
                target_id=with_client.pk,
                verb=Activity.Verb.DELETED,
            )
            assert render_activity_summary(deleted) == f"{name} gelöscht"

    def test_workitem_branches(self, facility, staff_user, sample_workitem):
        """Toetet die Textmutanten der fuenf ``WORKITEM_*``-Zweige mit Ziel."""
        title = sample_workitem.title
        expectations = [
            ("workitem_created", f"Aufgabe: {title}"),
            ("workitem_updated", f"Aufgabe aktualisiert: {title}"),
            ("workitem_completed", f"Aufgabe erledigt: {title}"),
            ("workitem_reopened", f"Aufgabe wiedereröffnet: {title}"),
            ("workitem_recurrence_created", f"Wiederkehrende Folgeaufgabe: {title}"),
        ]
        with translation.override("de"):
            for action, expected in expectations:
                activity = _activity(
                    facility,
                    staff_user,
                    action=action,
                    target_model=WorkItem,
                    target_id=sample_workitem.pk,
                    verb=Activity.Verb.UPDATED,
                )
                assert render_activity_summary(activity) == expected

    def test_case_branches(self, facility, staff_user, case_open):
        """Toetet die Textmutanten der drei ``CASE_*``-Zweige mit Ziel."""
        title = case_open.title
        expectations = [
            ("case_created", f"Fall eröffnet: {title}"),
            ("case_closed", f'Fall "{title}" geschlossen'),
            ("case_reopened", f'Fall "{title}" wiedereröffnet'),
        ]
        with translation.override("de"):
            for action, expected in expectations:
                activity = _activity(
                    facility,
                    staff_user,
                    action=action,
                    target_model=Case,
                    target_id=case_open.pk,
                    verb=Activity.Verb.UPDATED,
                )
                assert render_activity_summary(activity) == expected


class TestGenericSummaryResolvesTheTargetLabel:
    """Der Fallback-Renderer benennt die Zielart — und faellt sonst neutral aus.

    ``_generic_summary``/``_target_model`` tragen zusammen 48 Survivors.
    Kein einziger Mutant dieser beiden Funktionen wurde durch eine
    Zusicherung getoetet — nur durch geworfene Ausnahmen. Der Fallback ist
    kein Randfall: JEDE redigierte Zeile (``REDACTED``/``LEGACY_REDACTED``
    nach einer Anonymisierung) laeuft durch ihn. Ohne Pin rendert er
    ungestraft „None erstellt" oder fuer jede Zielart „Aktivität erstellt".
    """

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            (Client, "Person erstellt"),
            (Event, "Dokumentation erstellt"),
            (WorkItem, "Aufgabe erstellt"),
            (Case, "Fall erstellt"),
            (Milestone, "Meilenstein erstellt"),
        ],
    )
    def test_redacted_row_names_its_target_kind(self, facility, staff_user, model, expected):
        """Toetet ``x__generic_summary__mutmut_32/33/34/35/36/37`` sowie die
        Label-Schluessel-Mutanten und ``x__target_model__mutmut_1/2/7/8/9/10/16/17``.
        """
        activity = _activity(
            facility,
            staff_user,
            action="redacted",
            target_model=model,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == expected

    def test_unknown_target_kind_stays_neutral(self, facility, staff_user):
        """Eine Zielart ausserhalb der Label-Tabelle bekommt den Oberbegriff."""
        activity = _activity(
            facility,
            staff_user,
            action="legacy_redacted",
            target_model=DocumentType,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )

        with translation.override("de"):
            assert render_activity_summary(activity) == "Aktivität erstellt"

    def test_redacted_row_is_not_rendered_as_a_deleted_milestone(self, facility, staff_user):
        """Toetet ``x_render_activity_summary__mutmut_237``.

        Der Mutant dreht den letzten Action-Vergleich um
        (``action != MILESTONE_DELETED``). Damit rendert JEDE Zeile, die
        bis dorthin durchfaellt — also alle redigierten — „Meilenstein
        gelöscht". Er ueberlebte, weil auf diesem Pfad bislang nur negativ
        geprueft wurde (kein Pseudonym im Satz).
        """
        activity = _activity(
            facility,
            staff_user,
            action="redacted",
            target_model=Client,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )

        with translation.override("de"):
            assert render_activity_summary(activity) != "Meilenstein gelöscht"
            assert render_activity_summary(activity) == "Person erstellt"

    def test_broken_context_falls_back_instead_of_raising(self, facility, staff_user):
        """Die beiden Vorspann-Guards (kein ``str``, unbekannte Action).

        Beide Zeilen wurden von keinem Test ausgefuehrt. Sie sind der
        Migrationspfad fuer Altzeilen ohne ``context``-Schema — bricht er,
        ist der Zeitstrom fuer historische Daten nicht mehr renderbar.
        """
        no_string = _activity(
            facility,
            staff_user,
            action="client_created",
            target_model=Client,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )
        no_string.context = {"schema": ACTIVITY_CONTEXT_SCHEMA, "action": 5}
        no_string.save(update_fields=["context"])

        unknown = _activity(
            facility,
            staff_user,
            action="wurde_nie_vergeben",
            target_model=Client,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )

        not_a_dict = _activity(
            facility,
            staff_user,
            action="client_created",
            target_model=Client,
            target_id=uuid.uuid4(),
            verb=Activity.Verb.CREATED,
        )
        not_a_dict.context = ["kein", "dict"]
        not_a_dict.save(update_fields=["context"])

        with translation.override("de"):
            assert render_activity_summary(no_string) == "Person erstellt"
            assert render_activity_summary(unknown) == "Person erstellt"
            assert render_activity_summary(not_a_dict) == "Person erstellt"


# ===========================================================================
# core.services.client.main — DSGVO-/Audit-Pfade
# ===========================================================================


class TestRetentionAnonymizationKeepsItsTrashGate:
    """Die irreversible Anonymisierung passiert NUR im Papierkorb.

    ``anonymize_eligible_soft_deleted_clients`` traegt das
    ``is_deleted=True``-Gate zweimal: einmal in der Auswahl-Query und ein
    zweites Mal auf der GESPERRTEN Zeile (Refs #2013). Beide Mutanten
    ueberlebten. Der Vorgang ist nicht rueckholbar — faellt eines der
    Gates, wird eine LEBENDE Akte getilgt.
    """

    def test_a_restored_client_is_never_anonymized(self, facility, staff_user, settings_obj):
        """Haelt die Zusicherung als Ganzes fest (Auswahl-Gate + Sperr-Gate).

        Der Mutant ``x_anonymize_eligible_soft_deleted_clients__mutmut_13``
        (``is_deleted=True`` aus der AUSWAHL-Query gestrichen) galt hier als
        ÄQUIVALENT (Hand-Nachweis: das zweite Gate auf der gesperrten Zeile
        faenge die Zeile weiterhin ab). Das ist WIDERLEGT: der ``dry_run``-Zweig
        gibt ``qs.count()`` zurueck, BEVOR die Schleife und damit das zweite
        ``is_deleted``-Gate der gesperrten Zeile ueberhaupt erreicht wird — der
        Mutant ist ueber ``dry_run=True`` killbar (Rueckgabewert ``1`` statt
        ``0``). Siehe den Pin
        ``test_mutation_pins_092_nachlauf.py::TestTrashSweepGatesAndDefers::
        test_the_dry_run_preview_also_honours_the_trash_gate``.

        Dieser Test bleibt trotzdem stehen — er ist (fuer den Echt-Lauf-Zweig)
        die einzige Zusicherung, dass eine Zeile mit ``is_deleted=False`` und
        abgelaufenem ``deleted_at`` die irreversible Tilgung NICHT ausloest,
        und er faellt, sobald jemand BEIDE Gates anfasst.
        """
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Wiederhergestellt-01",
            created_by=staff_user,
        )
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Client.objects.filter(pk=client.pk).update(is_deleted=False, deleted_at=expired)

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 0
        client.refresh_from_db()
        assert client.anonymized_at is None
        assert client.pseudonym == "Wiederhergestellt-01"

    def test_the_locked_reread_gate_holds_on_its_own(self, facility, staff_user, settings_obj, monkeypatch):
        """Toetet ``x_anonymize_eligible_soft_deleted_clients__mutmut_35``.

        Das zweite Gate greift per Konstruktion nur, wenn die Zeile
        zwischen Auswahl und Sperre wiederhergestellt wurde — genau das
        Rennen, das der Kommentar beschreibt. Der Test stellt es her,
        indem er der Schleife eine Auswahlmenge uebergibt, die die
        wiederhergestellte Zeile noch enthaelt.
        """
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Rennen-01",
            created_by=staff_user,
        )
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Client.objects.filter(pk=client.pk).update(is_deleted=False, deleted_at=expired)

        monkeypatch.setattr(
            client_main,
            "exclude_clients_with_active_holds",
            lambda qs, facility: Client.objects.for_facility(facility).filter(anonymized_at__isnull=True),
        )

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 0
        client.refresh_from_db()
        assert client.anonymized_at is None
        assert client.pseudonym == "Rennen-01"

    def test_audit_row_names_the_expired_trash_date(self, facility, staff_user, settings_obj):
        """Toetet ``x_anonymize_eligible_soft_deleted_clients__mutmut_46``.

        Der Mutant setzt ``deleted_at=None`` im ``detail`` der
        ``CLIENT_ANONYMIZED``-Zeile. Damit verliert der Nachweis nach
        Art. 5 Abs. 2 DSGVO die Angabe, ab wann die Frist lief — der
        Grund, WARUM getilgt wurde, ist nicht mehr belegt.
        """
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Frist-01",
            created_by=staff_user,
        )
        expired = timezone.now() - timedelta(days=settings_obj.client_trash_days + 5)
        Client.objects.filter(pk=client.pk).update(is_deleted=True, deleted_at=expired)
        client.refresh_from_db()

        assert anonymize_eligible_soft_deleted_clients(facility, settings_obj) == 1

        row = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(client.pk),
        ).latest("timestamp")
        assert row.detail["deleted_at"] == str(expired)
        assert row.detail["trigger"] == "trash_days_expired"


class TestEventTimestampGeneralizationIsComplete:
    """Der Monatsbucket loescht ALLE feineren Anteile — Mikrosekunden inklusive.

    ``occurred_at`` ist ein ``timestamptz``; Postgres speichert
    Mikrosekunden. Bleibt der Anteil stehen, traegt der generalisierte
    Zeitstempel weiterhin einen praktisch eindeutigen Fingerabdruck des
    Originalzeitpunkts — die Generalisierung waere wirkungslos.
    """

    def test_bucket_drops_the_microseconds(self, facility, client_identified, doc_type_contact, staff_user):
        """Toetet ``x__generalize_event_timestamps__mutmut_25``."""
        occurred = timezone.make_aware(datetime(2024, 5, 17, 13, 44, 55, 123456))
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=occurred,
            created_by=staff_user,
        )

        client_main._generalize_event_timestamps([event.pk])

        event.refresh_from_db()
        local = timezone.localtime(event.occurred_at)
        assert local == timezone.localtime(occurred).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        assert local.microsecond == 0

    def test_the_chunked_write_path_also_writes_the_bucket(
        self, facility, client_identified, doc_type_contact, staff_user, monkeypatch
    ):
        """Toetet ``x__generalize_event_timestamps__mutmut_41``.

        Der Mutant verdirbt den Feldnamen im ``bulk_update`` DES
        CHUNK-ZWEIGS (``len(pending) >= _TIMESTAMP_BUCKET_CHUNK``). Er
        ueberlebte, weil kein Test je mehr als eine Handvoll Ereignisse
        generalisiert — der DSGVO-Lauf einer Akte mit ueber 500
        Ereignissen war damit unverifiziert. Statt 501 Zeilen anzulegen
        setzt der Test die Chunk-Groesse herunter; gepinnt wird derselbe
        Codepfad.
        """
        monkeypatch.setattr(client_main, "_TIMESTAMP_BUCKET_CHUNK", 2)
        occurred = timezone.make_aware(datetime(2024, 5, 17, 13, 44, 55))
        events = [
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=occurred + timedelta(days=index),
                created_by=staff_user,
            )
            for index in range(5)
        ]

        client_main._generalize_event_timestamps([e.pk for e in events])

        for event in events:
            event.refresh_from_db()
            local = timezone.localtime(event.occurred_at)
            assert (local.day, local.hour, local.minute, local.second) == (1, 0, 0, 0)


class TestStageChangeAuditNamesItsActor:
    """Die Stufenaenderung bleibt zurechenbar.

    ``user=None`` ist im Audit-Schreiber den Cron-/Anonymisierungspfaden
    vorbehalten. Eine IDENTIFIED->QUALIFIED-Aenderung ist eine
    datenschutzrelevante Entscheidung einer Person — ohne Akteur ist sie
    nach Art. 5 Abs. 2 DSGVO nicht mehr belegt.
    """

    def test_stage_change_row_carries_the_acting_user(self, facility, client_identified, staff_user):
        """Toetet ``x_update_client_stage__mutmut_3``."""
        update_client_stage(
            client_identified,
            Client.ContactStage.IDENTIFIED,
            Client.ContactStage.QUALIFIED,
            staff_user,
        )

        row = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.STAGE_CHANGE,
            target_id=str(client_identified.pk),
        ).latest("timestamp")
        assert row.user == staff_user


class TestMergeBadgeOnlyForPendingRequests:
    """Das „Zusammenführung beantragt"-Kennzeichen haengt am Status.

    Faellt der ``status=PENDING``-Filter, traegt eine Person das
    Kennzeichen dauerhaft — auch nach Ablehnung oder Durchfuehrung des
    Antrags. Die Personenliste behauptete dann einen laufenden Vorgang,
    den es nicht gibt.
    """

    def test_a_decided_merge_request_no_longer_marks_the_client(
        self, facility, staff_user, lead_user, client_identified, client_qualified
    ):
        """Toetet ``x_pending_merge_client_ids__mutmut_14``."""
        pending = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            merge_into=client_qualified,
            reason="Doppelt",
            requested_by=staff_user,
        )
        assert pending_merge_client_ids(facility, [client_identified]) == {client_identified.pk}

        pending.status = DeletionRequest.Status.REJECTED
        pending.reviewed_by = lead_user
        pending.reviewed_at = timezone.now()
        pending.save(update_fields=["status", "reviewed_by", "reviewed_at"])

        assert pending_merge_client_ids(facility, [client_identified]) == set()


class TestMergeInventoryDegradesInsteadOfBreaking:
    """Eine fehlende Inventar-Zeile darf den Retention-Lauf nicht reissen.

    Der Guard ist ausdruecklich „degradierend statt abbrechend"
    formuliert. Der ueberlebende Mutant tauscht ``or`` gegen ``and``:
    bei fehlender Zeile faellt ``row.detail`` dann in einen
    ``AttributeError`` — der gesamte naechtliche Loeschlauf bricht an
    einem Alt-/Reparaturzustand ab.
    """

    def test_missing_client_merged_row_is_survived(self, facility, staff_user, client_identified, client_qualified):
        """Toetet ``x__redact_merged_source_references__mutmut_18``."""
        Client.objects.filter(pk=client_identified.pk).update(merged_into=client_qualified)
        client_identified.refresh_from_db()
        assert not AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.CLIENT_MERGED,
            target_id=str(client_identified.pk),
        ).exists()

        client_main._redact_merged_source_references(client_identified)


# ===========================================================================
# core.forms.clients — fail-closed-Zweige der Formulare
# ===========================================================================


class TestClientFormWithoutFacilityOffersNoDocumentTypes:
    """Ohne Einrichtung bleibt die Typ-Auswahl leer (fail-closed).

    Der Kommentar an der Stelle nennt den Grund: ein ungescoptes Queryset
    waere ein Cross-Tenant-Leck der Konfigurationsnamen. Der ``else``-Zweig
    hatte keinen einzigen Test — der Mutant, der ihn durch ``None``
    ersetzt, ueberlebte.
    """

    def test_document_type_queryset_is_empty(self, facility, doc_type_contact):
        """Toetet ``x ǁClientFormǁ__init____mutmut_37``."""
        DocumentType.objects.create(
            facility=facility,
            name="Personentyp",
            entity_type=DocumentType.EntityType.CLIENT,
        )

        form = ClientForm(facility=None)

        queryset = form.fields["document_type"].queryset
        assert queryset is not None
        assert list(queryset) == []


class TestInlineCreateFormAcceptsTheLowestStage:
    """Die unterste Kontaktstufe ist ein GUELTIGER Mindestwert.

    ``stage_index`` gibt fuer die unterste Stufe den Index 0 zurueck; nur
    ein unbekannter Wert liefert ``-1``. Drei Mutanten verschieben genau
    diese Grenze (``stage_index(None)``, ``<= 0``, ``< 1``) und
    behandeln damit die unterste Stufe als ungueltig — die Auswahl faellt
    fail-closed auf die HOECHSTE Stufe zurueck. Die Anlegemaske bietet
    dann nur noch „qualifiziert" an, obwohl der Dokumentationstyp
    „identifiziert" verlangt.
    """

    def test_identified_stays_selectable(self, facility):
        """Toetet ``xǁClientInlineCreateFormǁ__init____mutmut_10/11/12``."""
        form = ClientInlineCreateForm(facility=facility, min_stage=Client.ContactStage.IDENTIFIED)

        offered = [value for value, _label in form.fields["contact_stage"].choices]
        assert Client.ContactStage.IDENTIFIED in offered
        assert form.initial["min_stage"] == str(Client.ContactStage.IDENTIFIED)


class TestInlineCreateFormLiftsTheHiddenFieldError:
    """Der Fehler des versteckten ``min_stage``-Feldes wird sichtbar.

    ``min_stage`` ist ein ``HiddenInput``; sein Feldfehler waere im Modal
    unsichtbar, deshalb hebt ``clean()`` ihn in den Non-Field-Block mit
    ``role=alert``. Der Mutant reicht statt des Fehlers ``None`` weiter —
    der Block zeigte dann die Zeichenkette „None". Der ganze Pfad war
    ungetestet.
    """

    def test_manipulated_min_stage_becomes_a_readable_non_field_error(self, facility):
        """Toetet ``xǁClientInlineCreateFormǁclean__mutmut_8``."""
        with translation.override("de"):
            form = ClientInlineCreateForm(
                {"pseudonym": "Neu-01", "contact_stage": Client.ContactStage.QUALIFIED, "min_stage": "kaputt"},
                facility=facility,
            )
            assert not form.is_valid()

            non_field = [str(message) for message in form.errors.get("__all__", [])]
            assert non_field, "Der min_stage-Fehler erreicht den Non-Field-Block nicht"
            assert "None" not in non_field
            assert non_field == [str(message) for message in form.errors["min_stage"]]


class TestRedactedPseudonymsStayDistinct:
    """Der Ersatz-Pseudonym-Marker traegt den Anfang der Ziel-UUID.

    Der Mutant setzt ``str(None)[:8]`` ein: jede anonymisierte Person
    einer Einrichtung hiesse dann „Gelöscht-None". Die zweite
    Anonymisierung derselben Einrichtung liefe damit in den partiellen
    Unique-Index ``unique_facility_pseudonym`` — der DSGVO-Lauf braeche
    ab. Der Survivor kam erst im sauberen Wiederholungslauf zum Vorschein
    (im Parallel-Lauf war er faelschlich als „getoetet" gezaehlt).
    """

    def test_two_anonymized_clients_keep_distinct_markers(self, facility, staff_user):
        """Toetet ``x__redact_client_identity__mutmut_2``."""
        first = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Marker-01",
            created_by=staff_user,
        )
        second = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Marker-02",
            created_by=staff_user,
        )

        client_main._redact_client_identity(first)
        client_main._redact_client_identity(second)

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.pseudonym == f"Gelöscht-{str(first.pk)[:8]}"
        assert second.pseudonym == f"Gelöscht-{str(second.pk)[:8]}"
        assert first.pseudonym != second.pseudonym

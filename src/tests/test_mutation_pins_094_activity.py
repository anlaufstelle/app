"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.dashboard.activity``.

Quelle: Survivor-Triage des Moduls ``src/core/services/dashboard/activity.py``
(Mutantenbaum ``mutants/src/core/services/dashboard/activity.py``). Vorgabe ist
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (14 triagierte
Mutanten); gepinnt sind alle 14. Drei Bloecke:

* **``_target_model`` ohne gesetzten Zieltyp (4 Mutanten).** Beide ``getattr``
  in :func:`~core.services.dashboard.activity._target_model` sind defensiv —
  Djangos ``RelatedObjectDoesNotExist`` ist ausdruecklich eine Unterklasse von
  ``AttributeError``, genau damit dieses Muster greift. Bislang setzt jeder
  Test seinen ``target_type`` ueber ``ContentType.objects.get_for_model(...)``;
  der Zustand „Activity ohne Zieltyp" war komplett unbeobachtet. Der Pin
  konstruiert ihn ueber die oeffentliche API (ungespeicherte ``Activity``, kein
  Duck-Typing) und nagelt BEIDE Verteidigungen fest: das Nicht-Werfen (Zeilen
  33/34 mit Default) und den exakten Rueckgabewert ``""`` — den die
  Label-Tabelle in ``_generic_summary`` nicht von einem beliebigen anderen
  unbekannten Modellnamen unterscheidet, weshalb er direkt geprueft wird.
* **``activity_target_prefetch`` (2 Mutanten).** Die Funktion existiert laut
  Docstring ausschliesslich als N+1-Schutz („Batch GenericFK targets and the
  Event relations used by the renderer"); ihre beiden ``select_related``-Teile
  sind wertneutral und daher nur ueber die Query-Zahl beobachtbar. Kein
  bestehender Test zaehlt Queries auf DIESEM Pfad — ``test_mutation_pins_094_feed.py`` pinnt
  nur den Aufrufer (``_feed_activities``), nicht die vorgeladenen
  Event-Relationen. Gemessen wird nach dem Muster jener Datei: 1 vs. 5
  Aktivitaeten, im Messfenster wird jede vorgeladene Relation ueber den echten
  Renderer materialisiert.
* **Die beiden Guards von ``log_activity`` (8 Mutanten).** ``grep`` nach
  ``client_update_kind`` in ``src/tests/`` findet keinen Aufruf mit einem
  ungueltigen Wert bzw. mit ``"renamed"`` ausserhalb von ``CLIENT_UPDATED``:
  beide Guards sind vollstaendig unbeobachtet, nicht nur ihr Wortlaut. Sie
  sind erreichbar (kein ``pragma``-Marker) und schuetzen den einzigen realen
  Aufrufer (``core/services/client/main.py``) vor Fehlnutzung. Jeder Guard
  bekommt einen Test, der seine Meldung EXAKT vergleicht und zusaetzlich
  festhaelt, dass keine Zeile geschrieben wurde.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override("de")`` festgenagelt; ``django_db``
nur dort, wo wirklich Datenbank noetig ist; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py``, alle
uebrigen Objekte sind echte Model-Instanzen (keine Fakes). Jeder Test nennt
seine Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

import uuid

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import Activity, Event
from core.services.dashboard.activity import (
    ACTIVITY_CONTEXT_SCHEMA,
    _target_model,
    activity_target_prefetch,
    log_activity,
    render_activity_summary,
)


def _event_activity(facility, user, client, doc_type):
    """Echte Dokumentation plus die zugehoerige Aktivitaets-Zeile."""
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json={"dauer": 15, "notiz": "Pin-Notiz"},
        created_by=user,
    )
    return log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.CREATED,
        target=event,
        action=Activity.Action.EVENT_CREATED,
    )


def _count_queries(func):
    """Query-Zahl EINES Aufrufs — inklusive aller darin beruehrten Relationen."""
    with CaptureQueriesContext(connection) as ctx:
        func()
    return len(ctx.captured_queries)


# ============================================================================
# _target_model — die beiden Defaults der Zieltyp-Aufloesung
# ============================================================================


class TestTargetModelToleratesAMissingTargetType:
    """Eine Aktivitaet ohne Zieltyp darf die Anzeige nicht sprengen.

    ``target_type`` ist in der Datenbank NOT NULL; in Python ist der Zustand
    aber ueber die oeffentliche API erreichbar (neu konstruierte, noch nicht
    gespeicherte ``Activity``). Djangos FK-Deskriptor wirft dann
    ``RelatedObjectDoesNotExist`` — eine ``AttributeError``-Unterklasse, die
    die beiden ``getattr``-Defaults auffangen sollen.
    """

    def test_activity_without_target_type_yields_the_empty_model_name(self):
        """Pin: ``x__target_model__mutmut_6`` — Default am aeusseren ``getattr``
        faellt weg (``getattr(activity, "target_type", )``); ``mutmut_15`` —
        Default am inneren ``getattr`` faellt weg
        (``getattr(target_type, "model", )``); ``mutmut_18`` — innerer Default
        ``""`` -> ``"XXXX"``; ``mutmut_19`` — aeusseres ``or ""`` -> ``or
        "XXXX"``.

        Abweichung beim Mutanten: ``mutmut_6``/``mutmut_15`` werfen den
        ``AttributeError`` durch (``_target_model`` und damit
        ``render_activity_summary`` brechen ab, statt ``""`` bzw. „Aktivität
        erstellt" zu liefern); ``mutmut_18``/``mutmut_19`` liefern statt ``""``
        den Marker-String ``"XXXX"``. Letzterer bleibt im gerenderten Satz
        unsichtbar (``labels.get`` faellt fuer jeden unbekannten Schluessel auf
        „Aktivität" zurueck), deshalb prueft der Pin den Rueckgabewert der
        Aufloesung SELBST — und den Satz zusaetzlich fuer die beiden
        Crash-Mutanten.

        Kein Datenbankzugriff noetig: ``target_type_id`` ist ``None``, der
        Deskriptor wirft ohne Nachladen.
        """
        activity = Activity(
            verb=Activity.Verb.CREATED,
            target_id=uuid.uuid4(),
            context={"schema": ACTIVITY_CONTEXT_SCHEMA, "action": "wurde_nie_vergeben"},
        )

        assert _target_model(activity) == ""

        with translation.override("de"):
            assert render_activity_summary(activity) == "Aktivität erstellt"


# ============================================================================
# activity_target_prefetch — N+1-Schutz der beiden Event-Relationen
# ============================================================================


@pytest.mark.django_db
class TestActivityTargetPrefetchPreloadsBothRenderedEventRelations:
    """``activity_target_prefetch``: ``select_related("document_type", "client")``."""

    def test_query_count_stays_constant_when_rendering_many_event_activities(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Pin: ``x_activity_target_prefetch__mutmut_9`` — ``document_type``
        faellt aus ``select_related``; ``x_activity_target_prefetch__mutmut_10``
        — ``client`` faellt raus.

        ``render_activity_summary`` liest im ``EVENT_CREATED``-Zweig BEIDE
        Relationen (``target.document_type.name`` und
        ``target.client.pseudonym``), beide werden hier im Messfenster
        materialisiert. Abweichung beim Mutanten: die Query-Zahl waechst
        linear mit der Zahl der Aktivitaeten — bei 5 statt 1 Zeile laedt jeder
        Mutant seine Relation viermal zusaetzlich nach (``large == small + 4``
        statt ``large == small``). Die gerenderten Saetze bleiben identisch;
        die Optimierung ist ausschliesslich ueber die Query-Zahl beobachtbar.
        """
        event_ct = ContentType.objects.get_for_model(Event)

        def touch():
            activities = (
                Activity.objects.filter(facility=facility, target_type=event_ct)
                .order_by("occurred_at")
                .prefetch_related(activity_target_prefetch())
            )
            with translation.override("de"):
                return [render_activity_summary(activity) for activity in activities]

        _event_activity(facility, staff_user, client_identified, doc_type_contact)
        # Erster Aufruf ausserhalb der Messung: fuellt den ContentType-Cache.
        assert touch() == ["Kontakt für Test-ID-01"]
        small = _count_queries(touch)

        for _ in range(4):
            _event_activity(facility, staff_user, client_identified, doc_type_contact)
        assert touch() == ["Kontakt für Test-ID-01"] * 5
        large = _count_queries(touch)

        assert large == small


# ============================================================================
# log_activity — die beiden Guards um ``client_update_kind``
# ============================================================================


@pytest.mark.django_db
class TestLogActivityGuardsTheClientUpdateKind:
    """``client_update_kind`` ist auf ``"renamed"`` UND auf ``CLIENT_UPDATED`` beschraenkt."""

    def test_unknown_kind_is_rejected_with_its_exact_message(self, facility, staff_user, client_identified):
        """Pin: ``x_log_activity__mutmut_6`` (``raise ValueError(None)``),
        ``mutmut_7`` (``"XXclient_update_kind must be None or 'renamed'XX"``),
        ``mutmut_8`` (``"... must be none or 'renamed'"``), ``mutmut_9``
        (Meldung komplett gross).

        Der Aufruf nutzt bewusst ``CLIENT_UPDATED``, damit ausschliesslich der
        ERSTE Guard feuern kann. Abweichung beim Mutanten: ``str(exc.value)``
        ist ``"None"`` (``mutmut_6``) bzw. die jeweilige Textvariante — der
        exakte Vergleich schlaegt in allen vier Faellen fehl.
        """
        with pytest.raises(ValueError) as excinfo:
            log_activity(
                facility=facility,
                actor=staff_user,
                verb=Activity.Verb.UPDATED,
                target=client_identified,
                action=Activity.Action.CLIENT_UPDATED,
                client_update_kind="umbenannt",
            )

        assert str(excinfo.value) == "client_update_kind must be None or 'renamed'"
        assert not Activity.objects.filter(facility=facility).exists()

    def test_renamed_outside_client_updated_is_rejected_with_its_exact_message(
        self, facility, staff_user, client_identified
    ):
        """Pin: ``x_log_activity__mutmut_13`` (``raise ValueError(None)``),
        ``mutmut_14``
        (``"XXclient_update_kind is only valid for CLIENT_UPDATEDXX"``),
        ``mutmut_15`` (``"... only valid for client_updated"``), ``mutmut_16``
        (Meldung komplett gross).

        ``"renamed"`` passiert den ersten Guard und faellt am zweiten aus, weil
        die Action nicht ``CLIENT_UPDATED`` ist. Abweichung beim Mutanten:
        ``str(exc.value)`` ist ``"None"`` (``mutmut_13``) bzw. die jeweilige
        Textvariante.
        """
        with pytest.raises(ValueError) as excinfo:
            log_activity(
                facility=facility,
                actor=staff_user,
                verb=Activity.Verb.CREATED,
                target=client_identified,
                action=Activity.Action.CLIENT_CREATED,
                client_update_kind="renamed",
            )

        assert str(excinfo.value) == "client_update_kind is only valid for CLIENT_UPDATED"
        assert not Activity.objects.filter(facility=facility).exists()

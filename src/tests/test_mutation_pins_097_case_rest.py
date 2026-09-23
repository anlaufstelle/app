"""Pin-Tests der Welle J (0.97, Refs #2149) — Prio-1-Survivors der Fall-Services.

Quelle: Survivor-Triage des atomaren Mutations-Messlaufs
(``welle-j-0.97/reports/triage-097-case-rest.json``, Batch ``case-rest``).
Gepinnt werden 74 bestaetigte Testluecken aus ``core.services.case.cases``,
``core.services.case.episodes`` und ``core.services.case.goals`` in fuenf
Familien:

* **Die Zeilensperre des Art.-18-Gates** — ``lock=True`` ->
  ``lock=False``/``lock=None``/Kwarg weggelassen an 13 Call-Sites. Die
  Abweisung einer BEREITS eingeschraenkten Person bleibt erhalten (die
  Bestandstests in ``test_processing_restriction_write_paths.py`` bleiben
  gruen), nur das ``FOR UPDATE`` faellt weg. Genau darin liegt aber der
  Unterschied zwischen „war beim Pruefen nicht eingeschraenkt" und „kann bis
  zum Commit nicht eingeschraenkt werden" (Docstring
  ``assert_processing_allowed``, Red-Team B-5/B-9). Beobachtbar nur am
  ausgefuehrten SQL.
* **Der geprueften Person das Argument nehmen** — ``assert_processing_allowed(
  <client>, lock=True)`` -> ``(None, lock=True)`` an vier Call-Sites.
  ``_restricted_flag(None)`` liefert sofort ``False``; das Gate ist fuer
  diesen Aufruf abgeschaltet, OHNE dass ueberhaupt eine ``core_client``-
  Abfrage laeuft.
* **Audit-Attribution** — ``user=user``/``facility=<…>``/``actor=user`` ->
  ``None`` in ``close_case``, ``reopen_case``, ``update_case`` und
  ``delete_milestone``. Alle drei Spalten sind nullable; der Schreibvorgang
  scheitert nicht, die Zeile verliert nur still ihren Akteur bzw. ihre
  Einrichtung (Threat-Model TB6, Repudiation).
* **``update_case``-``data_json``-Familie** — die Trigger-Bedingung des
  Dynamikdaten-Guards und der Default des bestehenden ``data_json``. Beide
  Mutanten-Gruppen schalten ``clean_dynamic_data`` fuer je einen realen
  Aufrufweg ab (reines ``data_json``-Update / reiner Typwechsel) und fuehren
  zu ungefilterten Slugs bzw. zu Datenverlust.
* **Konstruktor-Kwargs und Lookup-Zieltreue** — ``description`` im
  ``Case(...)``-Konstruktor, ``user``/``description`` beim Durchreichen an
  ``Case.create_episode`` und die PK-Bindung in ``get_case_or_none``.

Konventionen wie ``test_mutation_pins_096_events_crud.py``: eine Testklasse je
Familie; jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der
beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==``/``is``), nie
per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); keine Fakes — es
laufen echte Model-Objekte durch die echten Services.
"""

import re
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity, AuditLog, Client, Event
from core.services.case.cases import (
    assign_event_to_case,
    close_case,
    create_case,
    get_case_or_none,
    remove_event_from_case,
    reopen_case,
    update_case,
)
from core.services.case.episodes import close_episode, create_episode, update_episode
from core.services.case.goals import (
    achieve_goal,
    create_goal,
    create_milestone,
    delete_milestone,
    toggle_milestone,
    unachieve_goal,
    update_goal,
)

#: ``FOR UPDATE``-Statements verraten die Sperre; der Tabellenname steht im
#: ``FROM``. Gleiche Grob-Erkennung wie ``test_mutation_pins_096_events_crud.py``,
#: damit sie nicht an Django-Versionsdetails der SQL-Formatierung haengt.
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _locking_statements(captured, table):
    """Alle ``FOR UPDATE``-Anweisungen eines Mitschnitts auf *table*."""
    hits = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match and match.group("table") == table:
            hits.append(query["sql"])
    return hits


def _pk_literal(obj_or_pk):
    """Die PK so, wie Postgres sie im ausgefuehrten SQL zeigt: Hex ohne Bindestriche."""
    pk = getattr(obj_or_pk, "pk", obj_or_pk)
    return str(pk).replace("-", "")


# ---------------------------------------------------------------------------
# cases.py — Art.-18-Gate: die Zeilensperre und die geprueften Personen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseServicesTakeTheClientRowLock:
    """``lock=True`` ist der Unterschied zwischen Momentaufnahme und Zusage.

    Keiner dieser Services nimmt sonst eine ``core_client``-Sperre; faellt sie
    weg, kann ein paralleles ``restrict_processing`` zwischen Pruefung und
    Write committen. Die Bestandstests bleiben gruen, weil eine bereits
    gesetzte Einschraenkung auch ungesperrt gelesen wird.
    """

    def test_create_case_sperrt_die_personenzeile(self, facility, staff_user, client_identified):
        """Pin: ``x_create_case__mutmut_15`` (``lock=None``), ``…__mutmut_17``
        (Kwarg weggelassen, Default ``False``) und ``…__mutmut_18``
        (``lock=False``).

        Abweichung beim Mutanten: der ``core_client``-SELECT des Gates traegt
        kein ``FOR UPDATE`` mehr — die Liste der Sperr-Anweisungen ist leer.
        """
        with CaptureQueriesContext(connection) as captured:
            create_case(facility, staff_user, client_identified, title="Sperr-Pin")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_update_case_sperrt_die_personenzeile(self, staff_user, client_identified, case_open):
        """Pin: ``x_update_case__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            update_case(case_open, staff_user, title="Neuer Titel")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_close_case_sperrt_die_personenzeile(self, staff_user, client_identified, case_open):
        """Pin: ``x_close_case__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            close_case(case_open, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_reopen_case_sperrt_die_personenzeile(self, staff_user, client_identified, case_closed):
        """Pin: ``x_reopen_case__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            reopen_case(case_closed, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_assign_event_to_case_sperrt_beide_personenzeilen(
        self, facility, staff_user, client_identified, doc_type_contact, case_open
    ):
        """Pin: ``x_assign_event_to_case__mutmut_*`` — ein Personenbezug faellt
        aus der Sperrmenge (``case.client_id``/``event.client_id`` -> ``None``)
        oder die Sperre wird entwertet (``lock=None``/``lock=False``/Kwarg
        weggelassen).

        Der Docstring zieht die Linie ausdruecklich: geprueft werden BEIDE
        Personenbezuege. Damit beide Bezuege unterscheidbar bleiben, laeuft der
        Pin ueber das Personen-Mismatch (Fall-Person != Ereignis-Person) — das
        Gate laeuft VOR dem Mismatch-Guard.

        Seit der PK-Sperrfolge (Refs #2268) fallen beide Sperren in EINER
        Anweisung: die Fall- und die Ereignisperson nacheinander zu sperren
        oeffnete ein Fenster fuer einen Sperrzyklus. Abweichung beim Mutanten:
        im Mitschnitt fehlt eine der beiden PKs — oder die Sperr-Anweisung
        ganz.
        """
        fremde_person = Client.objects.create(
            facility=facility,
            pseudonym="PIN-097-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        event = Event.objects.create(
            facility=facility,
            client=fremde_person,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with CaptureQueriesContext(connection) as captured:
            with pytest.raises(ValidationError):
                assign_event_to_case(case_open, event, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine gemeinsame Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]
        assert _pk_literal(fremde_person) in locks[0], locks[0]

    def test_remove_event_from_case_sperrt_die_personenzeile(self, staff_user, client_identified, case_event):
        """Pin: ``x_remove_event_from_case__mutmut_2`` (``lock=None``),
        ``…__mutmut_4`` (Kwarg weggelassen) und ``…__mutmut_5``
        (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            remove_event_from_case(case_event, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


# ---------------------------------------------------------------------------
# cases.py — Anonymitaetsgrenze der Fall-Zuordnung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAssignEventAnonymityBoundary:
    """„Anonyme Ereignisse duerfen nur an Faelle OHNE Person haengen."

    Die Bedingung ist ein ``or``: sie faengt sowohl das als anonym markierte
    Ereignis als auch das Ereignis ohne Personenbezug. Beide Haelften sind
    noetig — am Modell gibt es keinen CheckConstraint, der ein
    nicht-anonymes Ereignis ohne ``client`` verhinderte.
    """

    def test_ereignis_ohne_person_nennt_die_anonymitaetsgrenze(self, facility, staff_user, doc_type_contact, case_open):
        """Pin: ``x_assign_event_to_case__mutmut_30`` — ``if event.is_anonymous
        or event.client_id is None:`` -> ``… and …``.

        Abweichung beim Mutanten: der Aufruf faellt in den naechsten Zweig und
        meldet die FALSCHE Ursache („Person des Ereignisses passt nicht zur
        Person des Falls.") statt der Anonymitaetsgrenze.
        """
        event = Event.objects.create(
            facility=facility,
            client=None,
            is_anonymous=False,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        with pytest.raises(ValidationError) as excinfo:
            assign_event_to_case(case_open, event, staff_user)

        assert excinfo.value.messages == [
            "Anonyme Ereignisse können nicht an einen Fall mit zugeordneter Person gehängt werden."
        ]


# ---------------------------------------------------------------------------
# cases.py — Konstruktor-Kwargs und Lookup-Zieltreue
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateCaseConstructorKwargs:
    """Was der Aufrufer uebergibt, muss auch in der Zeile landen."""

    def test_create_case_speichert_die_uebergebene_description(self, facility, staff_user, client_identified):
        """Pin: ``x_create_case__mutmut_50`` — ``description=description,`` im
        ``Case(...)``-Konstruktor entfaellt.

        ``Case.description`` hat kein ``default=`` (nur ``blank=True``); ohne
        das Kwarg greift Djangos Feld-Fallback ``""``.

        Abweichung beim Mutanten: ``case.description`` ist leer, die
        uebergebene Beschreibung geht schon beim Anlegen verloren.
        """
        case = create_case(
            facility,
            staff_user,
            client_identified,
            title="Pin-Fall",
            description="Wichtiger Kontext",
        )

        assert case.description == "Wichtiger Kontext"
        case.refresh_from_db()
        assert case.description == "Wichtiger Kontext"


@pytest.mark.django_db
class TestGetCaseOrNoneTargetsThePk:
    """Der Lookup ist an die PK gebunden, nicht nur an die Einrichtung."""

    def test_unbekannte_case_id_liefert_none(self, facility, case_open):
        """Pin: ``x_get_case_or_none__mutmut_4`` — ``.get(pk=case_id,
        facility=facility)`` -> ``.get(facility=facility)``.

        Abweichung beim Mutanten: die PK-Bindung fliegt aus dem Lookup; bei
        genau einem Fall in der Einrichtung liefert der Mutant IHN zurueck,
        egal welche ID uebergeben wurde — genau das Existenz-Orakel, vor dem
        der Docstring warnt.
        """
        assert get_case_or_none(facility, uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# cases.py — update_case: die data_json-Familie
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateCaseDataJsonGuard:
    """``clean_dynamic_data`` gilt fuer BEIDE Aufrufwege des Guards."""

    def test_reines_data_json_update_laeuft_durch_die_whitelist(
        self, facility, staff_user, client_identified, doc_type_case_form
    ):
        """Pin: ``x_update_case__mutmut_29`` (``"XXdata_jsonXX"``),
        ``…__mutmut_30`` (``"DATA_JSON"``) und ``…__mutmut_31``
        (``"data_json" not in fields``).

        Alle drei nehmen der Trigger-Bedingung den ``data_json``-Zweig: wird
        NUR ``data_json`` aktualisiert (der haeufige Fall — der Bestandstest
        ``test_update_drops_unknown_keys`` reicht zusaetzlich
        ``document_type`` mit und deckt den Ausfall zu), laeuft
        ``clean_dynamic_data`` nicht mehr.

        Abweichung beim Mutanten: der unbekannte Slug bleibt ungefiltert im
        gespeicherten ``data_json`` stehen.
        """
        case = create_case(
            facility,
            staff_user,
            client_identified,
            title="Pin-Fall",
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )

        update_case(case, staff_user, data_json={"anlass": "Wohnungsverlust", "nicht-am-typ": "Schmuggelware"})

        case.refresh_from_db()
        assert case.data_json == {"anlass": "Wohnungsverlust"}

    def test_reiner_typwechsel_behaelt_das_bestehende_data_json(
        self, facility, staff_user, client_identified, doc_type_case_form
    ):
        """Pin: ``x_update_case__mutmut_41`` (``fields.get("data_json", None)``)
        und ``…__mutmut_43`` (``fields.get("data_json")``).

        Wird nur der Typ gewechselt, soll ``raw_data`` der BESTEHENDE
        ``data_json``-Wert sein, damit er erneut durch die Whitelist des neuen
        Typs laeuft.

        Abweichung beim Mutanten: ``raw_data`` ist ``None``,
        ``validate_data_json`` liefert ``{}`` — jede reine
        ``document_type``-Aenderung loescht den kompletten Inhalt.
        """
        case = create_case(
            facility,
            staff_user,
            client_identified,
            title="Pin-Fall",
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )

        update_case(case, staff_user, document_type=doc_type_case_form)

        case.refresh_from_db()
        assert case.data_json == {"anlass": "Mietschulden"}


# ---------------------------------------------------------------------------
# cases.py — Audit-Attribution
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCaseAuditAttribution:
    """Akteur und Einrichtung sind nullable — ihr Verlust bleibt still.

    Ohne beide faellt die Zeile aus jeder facility-gescopten Audit-Flaeche
    heraus bzw. ist der Vorgang niemandem mehr zuzuordnen (Threat-Model TB6,
    Repudiation).
    """

    def test_create_case_auditiert_die_einrichtung(self, facility, staff_user, client_identified):
        """Pin: ``x_create_case__mutmut_58`` — ``facility=facility`` ->
        ``facility=None`` im ``audit_event(CASE_CREATE)``.

        Korrektur zur Triage: der Eintrag ordnet den Mutanten dem
        ``Case(...)``-Konstruktor zu (``quell_zeile`` 114). Im Mutantenbaum
        (Stand f5272d3e) trifft ``…__mutmut_58`` jedoch das ``facility``-Kwarg
        des nachfolgenden ``audit_event``-Aufrufs; der Konstruktor bleibt
        unveraendert. Gepinnt wird deshalb die Audit-Attribution.

        Abweichung beim Mutanten: ``facility_id`` der ``CASE_CREATE``-Zeile
        ist ``None`` — der Vorgang ist fuer die Einrichtung unsichtbar
        protokolliert.
        """
        case = create_case(facility, staff_user, client_identified, title="Pin-Fall")

        row = AuditLog.objects.get(
            action=AuditLog.Action.CASE_CREATE,
            target_type="Case",
            target_id=str(case.pk),
        )
        assert row.facility_id == facility.pk

    def test_update_case_auditiert_akteur_und_einrichtung(self, facility, staff_user, case_open):
        """Pin: ``x_update_case__mutmut_67`` (``user=user`` -> ``user=None``)
        und ``…__mutmut_68`` (``facility=case.facility`` -> ``facility=None``).

        Abweichung beim Mutanten: ``user_id`` bzw. ``facility_id`` der
        ``CASE_UPDATE``-Zeile ist ``None``.
        """
        update_case(case_open, staff_user, title="Neuer Titel")

        row = AuditLog.objects.get(
            action=AuditLog.Action.CASE_UPDATE,
            target_type="Case",
            target_id=str(case_open.pk),
        )
        assert row.user_id == staff_user.pk
        assert row.facility_id == facility.pk

    def test_close_case_auditiert_einrichtung_und_akteur(self, facility, staff_user, case_open):
        """Pin: ``x_close_case__mutmut_10`` (``facility=case.facility`` ->
        ``facility=None`` im ``audit_event``) und ``…__mutmut_17``
        (``actor=user`` -> ``actor=None`` im ``log_activity``).

        Abweichung beim Mutanten: ``facility_id`` der ``CASE_CLOSE``-Zeile
        bzw. ``actor_id`` des ``CASE_CLOSED``-Feed-Eintrags ist ``None``.
        """
        close_case(case_open, staff_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.CASE_CLOSE,
            target_type="Case",
            target_id=str(case_open.pk),
        )
        assert row.facility_id == facility.pk

        activity = Activity.objects.get(
            target_id=case_open.pk,
            context__action=Activity.Action.CASE_CLOSED.value,
        )
        assert activity.actor_id == staff_user.pk

    def test_reopen_case_auditiert_einrichtung_und_akteur(self, facility, staff_user, case_closed):
        """Pin: ``x_reopen_case__mutmut_10`` (``facility=case.facility`` ->
        ``facility=None`` im ``audit_event``) und ``…__mutmut_17``
        (``actor=user`` -> ``actor=None`` im ``log_activity``).

        Abweichung beim Mutanten: ``facility_id`` der ``CASE_REOPEN``-Zeile
        bzw. ``actor_id`` des ``CASE_REOPENED``-Feed-Eintrags ist ``None``.
        """
        reopen_case(case_closed, staff_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.CASE_REOPEN,
            target_type="Case",
            target_id=str(case_closed.pk),
        )
        assert row.facility_id == facility.pk

        activity = Activity.objects.get(
            target_id=case_closed.pk,
            context__action=Activity.Action.CASE_REOPENED.value,
        )
        assert activity.actor_id == staff_user.pk


# ---------------------------------------------------------------------------
# episodes.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEpisodeServicesTakeTheClientRowLock:
    """Die Episoden-Naht traegt dieselbe Zeilensperre wie der Fall-Service."""

    def test_create_episode_sperrt_die_personenzeile(self, staff_user, client_identified, case_open):
        """Pin: ``x_create_episode__mutmut_3`` (``lock=None``), ``…__mutmut_5``
        (Kwarg weggelassen) und ``…__mutmut_6`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            create_episode(case_open, staff_user, title="Etappe")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_update_episode_sperrt_die_personenzeile(self, staff_user, client_identified, episode):
        """Pin: ``x_update_episode__mutmut_4`` (``lock=None``), ``…__mutmut_6``
        (Kwarg weggelassen) und ``…__mutmut_7`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            update_episode(episode, staff_user, title="Neue Etappe")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_close_episode_sperrt_die_personenzeile(self, staff_user, client_identified, episode):
        """Pin: ``x_close_episode__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            close_episode(episode, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


@pytest.mark.django_db
class TestCreateEpisodePassesItsArguments:
    """Die duenne Naht reicht durch, was sie bekommt."""

    def test_create_episode_setzt_den_erstellenden_user(self, staff_user, case_open):
        """Pin: ``x_create_episode__mutmut_7`` — ``case.create_episode(
        user=user, …)`` -> ``user=None``.

        ``Episode.created_by`` ist nullable; die Episode entstuende ohne
        erfassten Ersteller, obwohl ein echter User den Service rief.

        Abweichung beim Mutanten: ``episode.created_by`` ist ``None``.
        """
        created = create_episode(case_open, staff_user, title="Etappe")

        assert created.created_by_id == staff_user.pk

    def test_create_episode_speichert_die_uebergebene_description(self, staff_user, case_open):
        """Pin: ``x_create_episode__mutmut_13`` — das ``description``-Kwarg
        entfaellt beim Durchreichen an ``Case.create_episode``.

        Abweichung beim Mutanten: es greift der Modell-Default ``""``, die
        uebergebene Beschreibung geht verloren.
        """
        created = create_episode(case_open, staff_user, title="Etappe", description="Wichtiger Kontext")

        assert created.description == "Wichtiger Kontext"


# ---------------------------------------------------------------------------
# goals.py
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGoalServicesTakeTheClientRowLock:
    """Wirkungsziele und Meilensteine gaten unter derselben Zeilensperre."""

    def test_create_goal_sperrt_die_personenzeile(self, staff_user, client_identified, case_open):
        """Pin: ``x_create_goal__mutmut_3`` (``lock=None``), ``…__mutmut_5``
        (Kwarg weggelassen) und ``…__mutmut_6`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            create_goal(case_open, staff_user, "Stabile Wohnsituation")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_update_goal_sperrt_die_personenzeile(self, staff_user, client_identified, outcome_goal):
        """Pin: ``x_update_goal__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            update_goal(outcome_goal, staff_user, title="Neues Ziel")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_achieve_goal_sperrt_die_personenzeile(self, client_identified, outcome_goal):
        """Pin: ``x_achieve_goal__mutmut_2`` (``lock=None``), ``…__mutmut_4``
        (Kwarg weggelassen) und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            achieve_goal(outcome_goal)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_unachieve_goal_sperrt_die_personenzeile(self, client_identified, outcome_goal):
        """Pin: ``x_unachieve_goal__mutmut_1`` (Person -> ``None``),
        ``…__mutmut_2`` (``lock=None``), ``…__mutmut_4`` (Kwarg weggelassen)
        und ``…__mutmut_5`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt — beim ``None``-Mutanten entfaellt die Abfrage ganz.
        """
        with CaptureQueriesContext(connection) as captured:
            unachieve_goal(outcome_goal)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_create_milestone_sperrt_die_personenzeile(self, client_identified, outcome_goal, staff_user):
        """Pin: ``x_create_milestone__mutmut_2`` (Person -> ``None``),
        ``…__mutmut_3`` (``lock=None``), ``…__mutmut_5`` (Kwarg weggelassen)
        und ``…__mutmut_6`` (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt — beim ``None``-Mutanten entfaellt die Abfrage ganz.
        """
        with CaptureQueriesContext(connection) as captured:
            create_milestone(outcome_goal, staff_user, "Erstgespräch geführt")

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_toggle_milestone_sperrt_die_personenzeile(self, client_identified, milestone):
        """Pin: ``x_toggle_milestone__mutmut_2`` (``lock=None``),
        ``…__mutmut_4`` (Kwarg weggelassen) und ``…__mutmut_5``
        (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            toggle_milestone(milestone)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]

    def test_delete_milestone_sperrt_die_personenzeile(self, staff_user, client_identified, milestone):
        """Pin: ``x_delete_milestone__mutmut_3`` (``lock=None``),
        ``…__mutmut_5`` (Kwarg weggelassen) und ``…__mutmut_6``
        (``lock=False``).

        Abweichung beim Mutanten: keine ``core_client``-Sperr-Anweisung im
        Mitschnitt.
        """
        with CaptureQueriesContext(connection) as captured:
            delete_milestone(milestone, staff_user)

        locks = _locking_statements(captured, "core_client")
        assert len(locks) == 1, f"erwartet GENAU eine Client-Sperre, war: {locks}"
        assert _pk_literal(client_identified) in locks[0], locks[0]


@pytest.mark.django_db
class TestDeleteMilestoneAuditAttribution:
    """Die Loeschzeile traegt Einrichtung und handelnde Person."""

    def test_delete_milestone_auditiert_einrichtung_und_akteur(self, facility, staff_user, milestone):
        """Pin: ``x_delete_milestone__mutmut_12`` (``facility=facility`` ->
        ``facility=None`` im ``audit_event``) und ``…__mutmut_29``
        (``actor=user`` -> ``actor=None`` im ``log_activity``).

        Abweichung beim Mutanten: ``facility_id`` der
        ``MILESTONE_DELETE``-Zeile bzw. ``actor_id`` des
        ``MILESTONE_DELETED``-Feed-Eintrags ist ``None``.
        """
        milestone_pk = milestone.pk

        delete_milestone(milestone, staff_user)

        row = AuditLog.objects.get(
            action=AuditLog.Action.MILESTONE_DELETE,
            target_type="Milestone",
            target_id=str(milestone_pk),
        )
        assert row.facility_id == facility.pk

        activity = Activity.objects.get(
            target_id=milestone_pk,
            context__action=Activity.Action.MILESTONE_DELETED.value,
        )
        assert activity.actor_id == staff_user.pk

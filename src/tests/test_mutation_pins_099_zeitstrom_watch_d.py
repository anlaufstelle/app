"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.zeitstrom_watch``.

Quelle: Vollmutation #2174, Batch ``zeitstrom_watch-d``
(``welle-l-0.99/reports/m-scratch/batch-zeitstrom_watch-d.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-zeitstrom_watch-d/triage-099-zeitstrom_watch-d.json``).
60 Survivor-Mutanten in vier Funktionen: ``build_zeitstrom_page_scope``,
``_opaque_validator``, ``_page_etag`` (alle drei opake HMAC-Validatoren) und
``_tally_topology_projection`` (Datenprojektion der Strichlisten-Topologie).

**Die drei HMAC-Validatoren** (44 Mutanten insgesamt) sind bewusst intransparent
— kein Aufrufer darf sich auf einzelne Bytes des Rückgabewerts verlassen, nur
auf Gleichheit/Ungleichheit. Trotzdem ändert JEDE Abweichung von der
dokumentierten Quelle (die exakt benannten Dict-Schlüssel + der feste
HMAC-Salt + ``algorithm="sha256"``) den vollständigen Rückgabewert — kein Fall
ist „nirgendwo gelesen" (die Docs-Definition von ``equivalent``). Gepinnt wird
daher über eine **unabhängige Rekonstruktion**: Jeder Test baut dieselbe
Quelle mit den EXAKTEN Feldnamen/Salt/Algorithmus aus dem freigegebenen
Quelltext nach und vergleicht sie auf Bytegleichheit mit dem tatsächlichen
Rückgabewert. Das killt sowohl reine Schlüssel-Umbenennungen (Prio 2, internes
Repräsentationsdetail) als auch die Mutanten, die ``str(user.pk)``/
``str(facility.pk)`` durch ``str(None)`` ersetzen (Prio 1: zwei User bzw. zwei
Einrichtungen mit sonst gleichem State/Rolle/Sensitivitäten bekämen
DENSELBEN Validator — die Bindung an die Identität, die diese Validatoren
laut Docstring herstellen sollen, wäre aufgehoben), die Salt-Mutationen
(Prio 1: die Namensraum-Trennung der drei Validator-Familien geht verloren)
sowie das entfernte ``algorithm="sha256"``-Keyword in
``xǁ_page_etagǁ__mutmut_18`` (Prio 1: ``salted_hmac`` fällt dann auf seinen
Funktions-Default ``algorithm="sha1"`` zurück — ein schwächerer,
40-stelliger statt 64-stelliger Digest; siehe Klassen-Docstring unten für die
Korrektur des ursprünglichen Fehlurteils).

**``_tally_topology_projection``** (17 Mutanten) ist reine Datenprojektion,
keine HMAC-Funktion — hier wird echtes Verhalten (Sentinel-Wert, Spy auf die
``build_tally_summary``-Aufrufargumente, exakte Tupel-Form mit einem
Pflicht- und einem optionalen Feld in kontrollierter Sortier-Reihenfolge,
sowie ein ``document_type`` ohne das prefetchte Attribut
``_tally_policy_assignments``) geprüft, keine Rekonstruktion.

**Zwei nachträgliche Korrekturen (Zweitgutachten, übernommen):**
``xǁ_tally_topology_projectionǁ__mutmut_21``/``__mutmut_24``
(die beiden ``getattr``-Default-Mutanten) waren zunächst als Äquivalenz
dokumentiert, weil ``document_type`` in JEDEM Aufrufpfad über
``tally_eligibilities()``'s Prefetch ankommt, der das Attribut immer setzt.
Beobachtbar wird das Default-Verhalten trotzdem, sobald ein Aufrufer
``_tally_topology_projection`` mit einer selbst gebauten ``summary`` und
einem ``document_type`` OHNE dieses Attribut aufruft (z. B. ein frischer
``DocumentType.objects.get(...)``-Fetch ohne ``prefetch_related``) — ein
legitimer Aufrufpfad über den öffentlichen ``summary=``-Parameter der
Funktion. Beide Mutanten sind daher umklassifiziert auf ``test``/Prio 2,
siehe ``test_document_type_ohne_prefetch_faellt_auf_getattr_default_zurueck``.
``xǁ_page_etagǁ__mutmut_18`` war zunächst als reine Trailing-Komma-Äquivalenz
dokumentiert — ein Lesefehler: der Diff gegen ``__mutmut_orig`` entfernt
tatsächlich das komplette Keyword-Argument ``algorithm="sha256"``, nicht nur
ein Komma (siehe oben). Alle 60 Mutanten der Batch sind damit als ``test``
klassifiziert und gepinnt, keine ``equivalent``-Fälle mehr.

Konventionen wie ``test_mutation_pins_099_tally.py``: lokaler ``_add_field``-
Helfer (Bestandsdatei bleibt unangetastet), Fixtures aus ``conftest.py``,
Zusicherungen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Prüfung übersehen würde).
"""

from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.crypto import salted_hmac

from core.models import DocumentType, DocumentTypeField, FieldTemplate, TimeFilter
from core.services.events.tally import TallyLiveContext
from core.services.zeitstrom_filters import ZeitstromState, resolve_zeitstrom_state
from core.services.zeitstrom_watch import (
    _json,
    _opaque_validator,
    _page_etag,
    _tally_topology_projection,
    build_zeitstrom_page_scope,
)
from tests._factories import make_document_type

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _add_field(document_type, *, name, field_type, required, sort_order):
    """Legt Feldvorlage + Zuordnung an (Muster ``test_mutation_pins_099_tally.py``)."""
    field_template = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        is_required=required,
    )
    DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=field_template,
        sort_order=sort_order,
    )
    return field_template


class TestBuildZeitstromPageScope:
    """``build_zeitstrom_page_scope`` — 14 Mutanten, alle als ``test`` gepinnt."""

    def test_scope_entspricht_der_unabhaengig_nachgebauten_hmac(self, staff_user):
        """Pins ``xǁbuild_zeitstrom_page_scopeǁ__mutmut_2/3`` (Schlüssel
        ``"user"`` → ``"XXuserXX"``/``"USER"``), ``__mutmut_5/6`` (``"facility"``),
        ``__mutmut_8/9`` (``"role"``), ``__mutmut_10/11`` (``"active"``),
        ``__mutmut_12/13`` (``"sensitivities"``) — reine Schlüssel-Umbenennungen,
        die den Rückgabewert bytweise ändern, aber die Unterscheidungskraft
        zwischen Usern/Einrichtungen NICHT aufheben.

        Zusätzlich Prio 1: ``__mutmut_4`` (``str(user.pk)`` → ``str(None)`` —
        zwei User derselben Rolle/Einrichtung/Sensitivitäten bekämen exakt
        denselben Scope, die vom Docstring versprochene Bindung an die
        Nutzeridentität entfiele) sowie ``__mutmut_16`` (Salt entfernt),
        ``__mutmut_22/23`` (Salt umbenannt/großgeschrieben) — die
        Namensraum-Trennung des Scope-Validators ginge verloren.
        """
        from core.services.compliance import allowed_sensitivities_for_user

        facility = staff_user.facility
        source = {
            "user": str(staff_user.pk),
            "facility": str(facility.pk),
            "role": staff_user.role,
            "active": staff_user.is_active,
            "sensitivities": sorted(allowed_sensitivities_for_user(staff_user)),
        }
        expected = salted_hmac("core.zeitstrom.watch.scope.v1", _json(source), algorithm="sha256").hexdigest()

        assert build_zeitstrom_page_scope(staff_user, facility) == expected


class TestOpaqueValidator:
    """``_opaque_validator`` — 15 Mutanten, alle als ``test`` gepinnt."""

    def test_validator_entspricht_der_unabhaengig_nachgebauten_hmac(self, facility, staff_user):
        """Pins ``xǁ_opaque_validatorǁ__mutmut_3/4`` (Schlüssel ``"user"``),
        ``__mutmut_6/7`` (``"facility"``), ``__mutmut_9/10`` (``"state"``),
        ``__mutmut_11/12`` (``"component"``), ``__mutmut_13/14``
        (``"projection"``) — reine Schlüssel-Umbenennungen.

        Prio 1: ``__mutmut_5`` (``str(user.pk)`` → ``str(None)``) und
        ``__mutmut_8`` (``str(facility.pk)`` → ``str(None)``) — zwei User bzw.
        zwei Einrichtungen mit sonst gleichem State/Komponente/Projektion
        bekämen denselben Komponenten-Validator. ``__mutmut_16`` (Salt
        entfernt), ``__mutmut_22/23`` (Salt umbenannt/großgeschrieben) —
        Namensraum-Trennung zu den anderen beiden Validator-Familien entfiele.
        """
        state = resolve_zeitstrom_state({}, facility)
        component = "focus_box"  # _COMPONENT_STATE_KEYS["focus_box"] == () — kein State-Rauschen
        projection = ("beliebiger-projektionswert",)
        component_state = {}
        source = {
            "user": str(staff_user.pk),
            "facility": str(facility.pk),
            "state": component_state,
            "component": component,
            "projection": projection,
        }
        expected_digest = salted_hmac(
            "core.zeitstrom.watch.component.v1", _json(source), algorithm="sha256"
        ).hexdigest()
        expected = f'"{expected_digest}"'

        actual = _opaque_validator(
            user=staff_user,
            facility=facility,
            state=state,
            component=component,
            projection=projection,
        )
        assert actual == expected


class TestPageEtag:
    """``_page_etag`` — 14 Mutanten, alle als ``test`` gepinnt.

    (``__mutmut_18`` ursprünglich fälschlich als ``equivalent``
    dokumentiert — Korrektur nach Zweitgutachten, siehe Test-Docstring
    unten und Moduldocstring.)
    """

    def test_etag_entspricht_der_unabhaengig_nachgebauten_hmac(self, facility, staff_user):
        """Pins ``xǁ_page_etagǁ__mutmut_2/3`` (Schlüssel ``"user"``),
        ``__mutmut_5/6`` (``"facility"``), ``__mutmut_8/9`` (``"state"``),
        ``__mutmut_10/11`` (``"components"``) — reine Schlüssel-Umbenennungen.

        Prio 1: ``__mutmut_4`` (``str(user.pk)`` → ``str(None)``) und
        ``__mutmut_7`` (``str(facility.pk)`` → ``str(None)``) — zwei User bzw.
        Einrichtungen mit sonst gleichem State/Komponenten-Validatoren
        bekämen dasselbe Seiten-ETag. ``__mutmut_13`` (Salt entfernt),
        ``__mutmut_19/20`` (Salt umbenannt/großgeschrieben).

        Ebenfalls Prio 1 — Korrektur nach Zweitgutachten: ``__mutmut_18``
        (``salted_hmac("core.zeitstrom.watch.page.v1", _json(source),
        ).hexdigest()``) wurde zunächst als reine Trailing-Komma-Äquivalenz
        gelesen. Der Diff gegen ``__mutmut_orig`` zeigt aber: das komplette
        Keyword-Argument ``algorithm="sha256"`` fehlt, nicht nur ein Komma.
        ``salted_hmac`` fällt dadurch auf seinen Funktions-Default
        ``algorithm="sha1"`` zurück (Django-Signatur:
        ``salted_hmac(key_salt, value, secret=None, *, algorithm="sha1")``)
        — ein anderer, schwächerer Hash mit 40- statt 64-stelligem Digest.
        Diese Rekonstruktion hier verwendet explizit ``algorithm="sha256"``
        und erkennt den SHA1-Digest der Mutante daher sofort als Mismatch
        (Kill-Nachweis: 40 vs. 64 Hex-Zeichen im Assertion-Diff).
        """
        state = resolve_zeitstrom_state({}, facility)
        validators = {"feed": '"feed-validator"', "tally_topology": '"tally-validator"'}
        source = {
            "user": str(staff_user.pk),
            "facility": str(facility.pk),
            "state": state.params,
            "components": validators,
        }
        expected_digest = salted_hmac("core.zeitstrom.watch.page.v1", _json(source), algorithm="sha256").hexdigest()
        expected = f'"{expected_digest}"'

        actual = _page_etag(user=staff_user, facility=facility, state=state, validators=validators)
        assert actual == expected


def _flat_live_context(*, time_filter, shift_date):
    """Fest verdrahtetes Zeitfenster — die Uhrzeiten selbst sind für die
    Topologie-Projektion irrelevant, nur ``time_filter``/``shift_date`` fließen
    in den Rückgabewert ein."""
    return TallyLiveContext(
        time_filter=time_filter,
        shift_date=shift_date,
        window_start=timezone.make_aware(datetime.combine(shift_date, time(8, 0))),
        window_end=timezone.make_aware(datetime.combine(shift_date, time(16, 0))),
    )


def _state_for(*, live_context, target_date, is_live):
    """``ZeitstromState`` mit ``time_filter=None`` — ``is_live_tally_context``
    hängt in diesem Zweig NUR von ``target_date == local_date`` ab (siehe
    ``zeitstrom_filters.ZeitstromState.is_live_tally_context``)."""
    local_date = target_date if is_live else date(2099, 1, 1)
    return ZeitstromState(
        target_date=target_date,
        time_filter=None,
        time_filters=(),
        live_context=live_context,
        feed_type="",
        doc_type_id="",
        view="",
        local_date=local_date,
    )


class TestTallyTopologyProjection:
    """``_tally_topology_projection`` — 17 Mutanten: alle 17 ``test`` gepinnt.

    (``__mutmut_21``/``__mutmut_24`` ursprünglich als ``equivalent``
    dokumentiert, nach Zweitgutachten umklassifiziert — siehe Moduldocstring
    und ``test_document_type_ohne_prefetch_faellt_auf_getattr_default_zurueck``.)
    """

    def test_kein_tally_kontext_liefert_exakt_das_sentinel_tupel(self, facility, staff_user):
        """Pins ``xǁ_tally_topology_projectionǁ__mutmut_2``
        (``"hidden"`` → ``"XXhiddenXX"``) und ``__mutmut_3`` (``"HIDDEN"``):
        außerhalb der laufenden Schicht liefert die Projektion wortwörtlich
        ``("hidden",)`` — dieser String fließt über ``_opaque_validator`` in
        den gehashten Komponenten-Validator ein; jede Schreibweisen-Abweichung
        würde ihn für JEDEN Nutzer unbeobachtbar verändern."""
        live_context = _flat_live_context(time_filter=None, shift_date=date(2020, 1, 1))
        state = _state_for(live_context=live_context, target_date=date(2020, 1, 1), is_live=False)

        assert _tally_topology_projection(facility, staff_user, state) == ("hidden",)

    def test_ruft_build_tally_summary_mit_exakt_facility_und_live_context_auf(self, facility, staff_user):
        """Pins ``xǁ_tally_topology_projectionǁ__mutmut_6`` (``facility`` im
        Fallback-Aufruf → ``None``: die Strichliste würde für KEINE oder die
        falsche Einrichtung ausgewertet — ``Event.objects.filter(facility=None,
        ...)`` trifft auf keine reale Einrichtung), ``__mutmut_8`` (``context``
        → ``None`` statt ``state.live_context``: ``build_tally_summary``
        berechnet das Zeitfenster dann UNABHÄNGIG neu über
        ``current_tally_context(facility)`` — bei einem Schichtwechsel
        zwischen Auflösung von ``state`` und diesem Aufruf ein anderes
        Fenster als der Rest der Seite) und ``__mutmut_11`` (das
        ``context``-Argument fehlt komplett, derselbe Effekt über den
        Funktions-Default). Ohne vorberechnete ``summary`` MUSS die
        Projektion ``build_tally_summary`` mit GENAU der übergebenen
        ``facility`` und dem bereits aufgelösten ``state.live_context``
        aufrufen — per Spy auf den Modul-Import nachgewiesen (Regeln aus
        früheren Batches: Spy statt Ergebnisreihenfolge)."""
        live_context = _flat_live_context(time_filter=None, shift_date=date(2020, 1, 1))
        state = _state_for(live_context=live_context, target_date=date(2020, 1, 1), is_live=True)

        with patch("core.services.zeitstrom_watch.build_tally_summary") as mock_summary:
            mock_summary.return_value = {
                "time_filter": None,
                "shift_date": live_context.shift_date,
                "window_start": live_context.window_start,
                "window_end": live_context.window_end,
                "rows": [],
            }
            _tally_topology_projection(facility, staff_user, state)

        mock_summary.assert_called_once_with(facility, staff_user, context=live_context)

    def test_pflicht_und_optionales_feld_liefern_die_exakte_topologie(self, facility, staff_user):
        """Pins ``xǁ_tally_topology_projectionǁ__mutmut_19`` (``document_type``
        im ``getattr`` → ``None``) und ``__mutmut_25/26`` (Attributname
        ``"_tally_policy_assignments"`` → ``"XX..."``/GROSS): der Lookup
        trifft nie, ``required_fields`` bliebe für JEDEN Dokumentationstyp
        dauerhaft leer — die Strichliste verlöre die Information, welches
        Feld beim Ausfüllen Pflicht ist (Prio 1, Baseline-Prüfung geschwächt).

        ``__mutmut_28`` (``if field.is_required: continue`` — Bedingung
        invertiert: nimmt genau die NICHT-Pflichtfelder statt der
        Pflichtfelder auf) und ``__mutmut_29`` (``continue`` → ``break``: das
        optionale Feld an Sortierposition 0 bricht die Schleife ab, bevor das
        Pflichtfeld an Position 1 überhaupt erreicht wird — Reihenfolge ist
        hier bewusst so gewählt, dass ein ``break`` sichtbar Daten verliert).
        ``__mutmut_30`` (angehängt wird ``None`` statt des Feld-Tupels).
        ``__mutmut_31``/``__mutmut_32`` (``str(assignment.pk)``/
        ``str(field.pk)`` → ``str(None)`` — Zuordnungs- bzw. Feld-Identität
        im Pflichtfeld-Tupel verloren). ``__mutmut_34`` (``str(document_type.pk)``
        im äußeren Zeilen-Tupel → ``str(None)``). ``__mutmut_38``
        (``str(summary["time_filter"].pk)`` → ``str(None)``: bei laufendem
        Schichtwechsel — gleiche Zeilen, andere Schicht — bliebe der
        Validator unbeobachtbar stehen).

        ``__mutmut_21``/``__mutmut_24`` (der ``getattr``-Default für ein
        ``document_type`` OHNE das prefetchte Attribut
        ``_tally_policy_assignments``) sind hier NICHT abgedeckt — dieser
        Test läuft ausschließlich über den Fallback ``build_tally_summary``,
        der das Attribut per Prefetch immer setzt. Siehe
        ``test_document_type_ohne_prefetch_faellt_auf_getattr_default_zurueck``
        für den Aufrufpfad, der den fehlenden Attribut-Zustand tatsächlich
        beobachtbar macht.
        """
        doc_type = make_document_type(
            facility=facility,
            name="Kurzkontakt",
            entity_type=DocumentType.EntityType.EVENT,
            tally_enabled=True,
            is_active=True,
        )
        # Sortierung bewusst: optional VOR Pflicht — ein `break` statt
        # `continue` (mutmut_29) verliert dadurch sichtbar das Pflichtfeld.
        _add_field(doc_type, name="Anmerkung", field_type=FieldTemplate.FieldType.TEXT, required=False, sort_order=0)
        required_field = _add_field(
            doc_type, name="Anzahl", field_type=FieldTemplate.FieldType.NUMBER, required=True, sort_order=1
        )
        required_assignment = DocumentTypeField.objects.get(field_template=required_field)

        time_filter = TimeFilter.objects.create(
            facility=facility,
            label="Frühdienst",
            start_time=time(8, 0),
            end_time=time(16, 0),
            sort_order=0,
        )
        shift_date = date(2020, 1, 1)
        live_context = _flat_live_context(time_filter=time_filter, shift_date=shift_date)
        state = _state_for(live_context=live_context, target_date=shift_date, is_live=True)

        result = _tally_topology_projection(facility, staff_user, state)

        expected = (
            str(time_filter.pk),
            time_filter.label,
            shift_date,
            live_context.window_start,
            live_context.window_end,
            (
                (
                    str(doc_type.pk),
                    doc_type.name,
                    True,  # requires_number_input: einziges Pflichtfeld ist NUMBER-typisiert
                    (
                        (
                            str(required_assignment.pk),
                            required_assignment.sort_order,
                            str(required_field.pk),
                            required_field.name,
                            required_field.slug,
                            required_field.field_type,
                            required_field.min_value,
                            required_field.max_value,
                            required_field.sensitivity,
                            required_field.is_active,
                        ),
                    ),
                ),
            ),
        )
        assert result == expected

    def test_document_type_ohne_prefetch_faellt_auf_getattr_default_zurueck(self, facility, staff_user):
        """Pins ``xǁ_tally_topology_projectionǁ__mutmut_21`` (Default von
        ``getattr(document_type, "_tally_policy_assignments", ())`` auf
        ``None`` geändert) und ``__mutmut_24`` (das dritte Argument entfällt
        komplett, ``getattr(document_type, "_tally_policy_assignments")``).

        Nachbesserung nach Zweitgutachten: der andere Test in
        dieser Klasse lässt ``document_type`` immer über den internen
        ``build_tally_summary``-Fallback laufen, dessen
        ``tally_eligibilities()``-Prefetch ``_tally_policy_assignments``
        IMMER setzt — der Default-Zweig des ``getattr`` bliebe dort
        tatsächlich unerreichbar. Der öffentliche ``summary=``-Parameter
        erlaubt aber einen legitimen zweiten Aufrufpfad: ein Aufrufer kann
        eine eigene ``summary`` mit einem ``document_type`` übergeben, das
        NICHT über diesen Prefetch geladen wurde (hier: ein frischer
        ``DocumentType.objects.get(...)``-Fetch, per ``hasattr`` verifiziert
        ohne das Attribut). Auf diesem Pfad ist der Default-Wert sehr wohl
        beobachtbar: das Original fängt den fehlenden Attribut-Zustand über
        den ``()``-Default ab (leere Pflichtfeldliste, kein Fehler);
        ``__mutmut_21`` liefert stattdessen ``None`` als Default, worüber die
        Schleife (``for assignment in None:``) mit ``TypeError`` abstürzt;
        ``__mutmut_24`` hat gar keinen Default mehr und wirft
        ``AttributeError`` direkt aus dem ``getattr``. Beide Abstürze zählen
        als ``rot`` im Kill-Nachweis — der Test selbst erwartet exakt das
        stille, fehlerfreie Original-Verhalten (Prio 2: kein
        Sicherheits-/Datenschutzproblem, aber eine echte Testlücke — ein
        Aufrufer ohne Prefetch bekäme in Produktion einen unbehandelten
        Absturz statt der dokumentierten leeren Pflichtfeldliste)."""
        doc_type = make_document_type(
            facility=facility,
            name="Ohne Prefetch",
            entity_type=DocumentType.EntityType.EVENT,
            tally_enabled=True,
            is_active=True,
        )
        _add_field(doc_type, name="Anzahl", field_type=FieldTemplate.FieldType.NUMBER, required=True, sort_order=0)
        # Frischer Fetch OHNE prefetch_related -- traegt das Attribut nicht,
        # anders als ein document_type aus tally_eligibilities().
        unprefetched = DocumentType.objects.get(pk=doc_type.pk)
        assert not hasattr(unprefetched, "_tally_policy_assignments")

        live_context = _flat_live_context(time_filter=None, shift_date=date(2020, 1, 1))
        state = _state_for(live_context=live_context, target_date=date(2020, 1, 1), is_live=True)
        summary = {
            "time_filter": None,
            "shift_date": live_context.shift_date,
            "window_start": live_context.window_start,
            "window_end": live_context.window_end,
            "rows": [{"document_type": unprefetched, "requires_number_input": False}],
        }

        result = _tally_topology_projection(facility, staff_user, state, summary=summary)

        assert result == (
            None,
            None,
            live_context.shift_date,
            live_context.window_start,
            live_context.window_end,
            ((str(unprefetched.pk), unprefetched.name, False, ()),),
        )

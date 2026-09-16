"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.zeitstrom_watch``.

Quelle: Vollmutation #2174, Batch ``zeitstrom_watch-d``
(``welle-l-0.99/reports/m-scratch/batch-zeitstrom_watch-d.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-zeitstrom_watch-d/triage-099-zeitstrom_watch-d.json``).
60 Survivor-Mutanten in vier Funktionen: ``build_zeitstrom_page_scope``,
``_opaque_validator``, ``_page_etag`` (alle drei opake HMAC-Validatoren) und
``_tally_topology_projection`` (Datenprojektion der Strichlisten-Topologie).

**Die drei HMAC-Validatoren** (43 Mutanten insgesamt) sind bewusst intransparent
— kein Aufrufer darf sich auf einzelne Bytes des Rückgabewerts verlassen, nur
auf Gleichheit/Ungleichheit. Trotzdem ändert JEDE Abweichung von der
dokumentierten Quelle (die exakt benannten Dict-Schlüssel + der feste
HMAC-Salt + ``sha256``) den vollständigen Rückgabewert — kein Fall ist
„nirgendwo gelesen" (die Docs-Definition von ``equivalent``). Gepinnt wird
daher über eine **unabhängige Rekonstruktion**: Jeder Test baut dieselbe
Quelle mit den EXAKTEN Feldnamen/Salt aus dem freigegebenen Quelltext nach und
vergleicht sie auf Bytegleichheit mit dem tatsächlichen Rückgabewert. Das
killt sowohl reine Schlüssel-Umbenennungen (Prio 2, internes
Repräsentationsdetail) als auch die Mutanten, die ``str(user.pk)``/
``str(facility.pk)`` durch ``str(None)`` ersetzen (Prio 1: zwei User bzw. zwei
Einrichtungen mit sonst gleichem State/Rolle/Sensitivitäten bekämen
DENSELBEN Validator — die Bindung an die Identität, die diese Validatoren
laut Docstring herstellen sollen, wäre aufgehoben) sowie die Salt-Mutationen
(Prio 1: die Namensraum-Trennung der drei Validator-Familien geht verloren).

Eine einzige Ausnahme ist ein echtes Äquivalenz-Urteil:
``xǁ_page_etagǁ__mutmut_18`` hängt nur ein überflüssiges Komma an den
``salted_hmac``-Aufruf — bytegleicher AST/Bytecode zum Original, siehe
Klassen-Docstring unten.

**``_tally_topology_projection``** (17 Mutanten) ist reine Datenprojektion,
keine HMAC-Funktion — hier wird echtes Verhalten (Sentinel-Wert, Spy auf die
``build_tally_summary``-Aufrufargumente, exakte Tupel-Form mit einem
Pflicht- und einem optionalen Feld in kontrollierter Sortier-Reihenfolge)
geprüft, keine Rekonstruktion. Zwei ``getattr``-Default-Mutanten
(``__mutmut_21``/``__mutmut_24``) sind hier als echte Äquivalenz belegt,
siehe Klassen-Docstring.

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
    """``_page_etag`` — 14 Mutanten: 13 ``test`` + 1 ``equivalent``."""

    def test_etag_entspricht_der_unabhaengig_nachgebauten_hmac(self, facility, staff_user):
        """Pins ``xǁ_page_etagǁ__mutmut_2/3`` (Schlüssel ``"user"``),
        ``__mutmut_5/6`` (``"facility"``), ``__mutmut_8/9`` (``"state"``),
        ``__mutmut_10/11`` (``"components"``) — reine Schlüssel-Umbenennungen.

        Prio 1: ``__mutmut_4`` (``str(user.pk)`` → ``str(None)``) und
        ``__mutmut_7`` (``str(facility.pk)`` → ``str(None)``) — zwei User bzw.
        Einrichtungen mit sonst gleichem State/Komponenten-Validatoren
        bekämen dasselbe Seiten-ETag. ``__mutmut_13`` (Salt entfernt),
        ``__mutmut_19/20`` (Salt umbenannt/großgeschrieben).

        ``__mutmut_18`` (``salted_hmac("core.zeitstrom.watch.page.v1",
        _json(source), ).hexdigest()`` — ein überflüssiges Komma nach dem
        zweiten Positionsargument) ist NICHT gepinnt: das Komma erzeugt
        keinen AST-Unterschied zum Original (dieselbe Zwei-Argument-Liste,
        derselbe Bytecode) — kein Aufrufer, auch kein noch so genauer Test,
        kann diese Mutante vom Original unterscheiden. Echte Äquivalenz.
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
    """``_tally_topology_projection`` — 17 Mutanten: 15 ``test`` + 2 ``equivalent``."""

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

        NICHT gepinnt (echte Äquivalenz): ``__mutmut_21`` (Default-Wert des
        ``getattr`` von ``()`` auf ``None`` geändert) und ``__mutmut_24``
        (Default-Argument komplett entfernt, ``getattr`` mit nur zwei
        Argumenten). ``document_type`` stammt in JEDEM realen Aufrufpfad aus
        ``tally_eligibilities()`` (via ``build_tally_summary`` /
        ``_tally_static_candidate_queryset``), deren Prefetch
        (``Prefetch("fields", ..., to_attr="_tally_policy_assignments")``)
        das Attribut IMMER setzt — auch als leere Liste, nie ungesetzt. Der
        ``getattr``-Lookup trifft daher in jedem erreichbaren Fall direkt,
        der Default-Zweig ist toter Code; kein Aufrufer (auch kein Test ohne
        künstlich präparierte ``document_type``-Objekte außerhalb dieses
        Prefetch-Pfads) kann die beiden Mutanten vom Original unterscheiden.
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

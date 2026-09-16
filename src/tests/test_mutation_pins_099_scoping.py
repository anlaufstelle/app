"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.scoping``,
Batch ``scoping-1`` (Vollmutation-Survivor-Triage, 19 Survivors in
``get_scoped_object``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-scoping-1.json`` (Triage
``reports/m-scratch/pins-scoping-1/triage-099-scoping-1.json``).

Alle 19 Survivors sitzen in den beiden ``ValueError``-Zweigen des Helfers
(Event-Ablehnung, fehlendes ``request.current_facility``) und veraendern
NICHT den sichtbaren Datenraum — ``get_object_or_404(..., facility=facility,
...)`` wird in jedem Mutanten weiterhin nur bei erfolgreichem Scoping
erreicht. Der bestehende Test
``test_scoping_helper.py::TestGetScopedObject::test_event_is_rejected_with_reference_to_dedicated_loader``
prueft die Event-Fehlermeldung nur per ``pytest.raises(match="...")`` (Teil-
string-Suche) — mutmuts ``XX``-Marker-Mutation (ganze Konstante durch
``"XX" + Text + "XX"`` ersetzt) UND reine Gross-/Kleinschreibungs-Mutationen
lassen den gesuchten Teilstring an anderer Stelle im String unveraendert
stehen und ueberleben deshalb den ``match=``-Check. Drei Pins mit EXAKTEM
Textvergleich (``str(excinfo.value) ==``) schliessen die Luecke:

* ``test_event_rejection_message_is_exact_not_just_a_substring_match``
  killt ``_4``/``_5``/``_6`` (1. Teilstring XX-gewrappt/lower/upper),
  ``_7``/``_8``/``_9`` (2. Teilstring) und ``_10`` (3. Teilstring
  XX-gewrappt — der von ``match="get_visible_event_or_404"`` gesuchte
  Teilstring bleibt in der Mitte des XX-Wraps erhalten).
* ``test_missing_facility_message_is_exact_when_attribute_is_explicitly_none``
  killt ``_21`` (``raise ValueError(None)`` statt der Meldung — Text wird zu
  ``"None"``), ``_22``/``_23`` (1. Teilstring), ``_24``/``_25``/``_26``
  (2. Teilstring), ``_27``/``_28``/``_29`` (3. Teilstring) und
  ``_30``/``_31`` (4. Teilstring).
* ``test_missing_current_facility_attribute_raises_valueerror_not_attributeerror``
  killt ``_17`` (``getattr(request, "current_facility", )`` — der Default
  ``None`` faellt weg. Solange ein Aufrufer ``request.current_facility``
  vorher explizit auf ``None`` gesetzt hat, ist das Verhalten identisch
  (Attribut existiert); fehlt das Attribut komplett — z.B. weil die
  ``FacilityScopeMiddleware`` nicht gelaufen ist, genau das Szenario, vor
  dem dieser Helfer schuetzen soll (siehe Modul-Docstring) — wirft der
  Mutant ``AttributeError`` statt der beabsichtigten, sprechenden
  ``ValueError``). Prio 2 (fachliches Verhalten: Exception-Vertrag), nicht
  Prio 1 — der sichtbare Datenraum wird in keinem Fall erweitert, der
  Lookup schlaegt so oder so fehl, nur mit einer anderen Exception-Klasse.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest

from core.models import Event
from core.services.scoping import get_scoped_object

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_EVENT_REJECTION_MESSAGE = (
    "get_scoped_object() darf nicht fuer Event verwendet werden — Events tragen "
    "eine Sensitivitaets-Policy zusaetzlich zum Facility-Scope. Bitte "
    "core.services.compliance.sensitivity.get_visible_event_or_404 nutzen."
)

_MISSING_FACILITY_MESSAGE = (
    "get_scoped_object() ohne request.current_facility aufgerufen — ein ungescopter "
    "Lookup waere ein Cross-Facility-IDOR. Sicherstellen, dass die "
    "FacilityScopeMiddleware gelaufen ist und der aktuelle User einer Facility "
    "zugeordnet ist."
)


def test_event_rejection_message_is_exact_not_just_a_substring_match(rf, facility):
    """Pinnt ``xǁget_scoped_objectǁ__mutmut_4``/``_5``/``_6``/``_7``/``_8``/
    ``_9``/``_10``: die Ablehnungsmeldung fuer ``Event`` muss GENAU dem
    deutschen Originaltext entsprechen, nicht nur den von
    ``test_scoping_helper.py`` per ``match=`` gesuchten Teilstring
    enthalten."""
    request = rf.get("/")
    request.current_facility = facility

    with pytest.raises(ValueError) as excinfo:
        get_scoped_object(Event, request, pk="00000000-0000-0000-0000-000000000000")

    assert str(excinfo.value) == _EVENT_REJECTION_MESSAGE


def test_missing_facility_message_is_exact_when_attribute_is_explicitly_none(rf, client_identified):
    """Pinnt ``xǁget_scoped_objectǁ__mutmut_21``/``_22``/``_23``/``_24``/
    ``_25``/``_26``/``_27``/``_28``/``_29``/``_30``/``_31``: mit explizit auf
    ``None`` gesetztem ``request.current_facility`` muss die Meldung GENAU
    dem deutschen Originaltext entsprechen (``_21`` ersetzt sie komplett
    durch ``str(None)``, die uebrigen veraendern je einen Teilstring)."""
    request = rf.get("/")
    request.current_facility = None

    with pytest.raises(ValueError) as excinfo:
        get_scoped_object(type(client_identified), request, pk=client_identified.pk)

    assert str(excinfo.value) == _MISSING_FACILITY_MESSAGE


def test_missing_current_facility_attribute_raises_valueerror_not_attributeerror(rf, client_identified):
    """Pinnt ``xǁget_scoped_objectǁ__mutmut_17``: fehlt
    ``request.current_facility`` komplett (Attribut nie gesetzt — z.B. weil
    die ``FacilityScopeMiddleware`` nicht gelaufen ist, siehe Modul-
    Docstring), muss weiterhin die sprechende ``ValueError`` fliegen. Der
    Mutant entfernt den ``getattr``-Default und laesst stattdessen eine
    unklare ``AttributeError`` durch."""
    request = rf.get("/")
    assert not hasattr(request, "current_facility")

    with pytest.raises(ValueError) as excinfo:
        get_scoped_object(type(client_identified), request, pk=client_identified.pk)

    assert str(excinfo.value) == _MISSING_FACILITY_MESSAGE

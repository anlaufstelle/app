"""Pin-Test des Mutations-Delta-Checkpoints der Welle M (release-0.99.1) zu
``core.services.audit.hwm``.

Quelle: Ticket #2322 (https://github.com/anlaufstelle/app/issues/2322),
Survivor-Triage des Checkpoint-Deltas ``6435c18e`` (BASE v0.99.0). Gepinnt
wird der einzige Prio-1-Survivor dieses Moduls: ``_validate_chains`` toleriert
per Vertrag genau EINEN bekannten Fall (leerer/fehlender Eintrag,
``continue``) und weist jede andere strukturelle Abweichung zurueck. Ein
frueher leerer Eintrag darf die Pruefung nachfolgender Ketten nicht
verhindern — als ``break`` beendet die Mutante die gesamte Validierung beim
ersten harmlosen Eintrag und laesst einen spaeteren, strukturell ungueltigen
Eintrag ungeprueft durch (fail-open statt fail-closed).

Konvention wie ``src/tests/test_mutation_pins_094_attachments_crud.py``:
Zusicherungen vergleichen EXAKT, keine Fixture-Fakes.
"""

import pytest

from core.services.audit.hwm import HwmIntegrityError, _validate_chains

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestValidateChains:
    def test_leerer_erster_eintrag_stoppt_die_pruefung_nicht(self):
        """Pin: ``x__validate_chains__mutmut_9`` — bei einem leeren Ketteneintrag
        wird ``continue`` durch ``break`` ersetzt.

        Ein geordnetes ``chains``-Dict mit leerem ERSTEM Eintrag (Erstlauf-Fall,
        toleriert) und malformed ZWEITEM Eintrag (kein Objekt — der Fall, den
        die Funktion laut Docstring gezielt abweist).

        Abweichung beim Mutanten: der zweite, strukturell ungueltige Eintrag
        wird nie erreicht — ``_validate_chains`` kehrt ohne Fehler zurueck,
        statt ``HwmIntegrityError`` auszuloesen.
        """
        chains = {
            "system": {},
            "1": "kein-objekt",
        }

        with pytest.raises(HwmIntegrityError) as exc_info:
            _validate_chains(chains, "/pins-0991/hwm.json")

        assert "'1'" in str(exc_info.value)
        assert "ist kein Objekt" in str(exc_info.value)

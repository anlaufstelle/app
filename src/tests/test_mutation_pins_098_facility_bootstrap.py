"""Pin-Test der Welle K (0.98) — Prio-2-Testluecke in ``core.services.facility_bootstrap``.

Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Batch ``export-settings`` der Survivor-Triage (``triage-096-merge-2-
misc.json``). Gepinnt wird ein Mutant in ``ensure_quick_templates``: fehlt zu
EINER deklarierten Schnellvorlage der Dokumentationstyp-Slug im Katalog der
Einrichtung, bricht der Mutant (``continue`` -> ``break``) die gesamte
Schleife ab statt nur diese eine Vorlage zu ueberspringen — alle
NACHFOLGENDEN Vorlagen des Pakets entstehen dann nicht mehr. Kein
Sicherheitsbezug (reiner Startbestand), aber falsche fachliche Wirkung.

``src/tests/test_facility_bootstrap.py::test_package_quick_templates_skip_
unknown_type_slugs`` (Bestandstest, NICHT angefasst) arbeitet mit einer
EINELEMENTIGEN Definitionsliste — bei nur einem Element sind ``continue`` und
``break`` identisch, der Mutant bleibt unentdeckt. Dieser Pin verwendet ZWEI
Definitionen, den unbekannten Slug VORAN.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat keine
``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest

from core.services.facility_bootstrap import QuickTemplateDef, ensure_default_package, ensure_quick_templates

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestEnsureQuickTemplatesSkipsOnlyTheUnknownSlug:
    def test_an_unknown_slug_is_skipped_the_rest_of_the_batch_is_not(self, facility):
        """Mutante ``x_ensure_quick_templates__mutmut_9`` (``continue`` ->
        ``break``, wenn der Dokumentationstyp-Slug einer Definition nicht im
        Katalog der Einrichtung existiert).

        Original: die unbekannte erste Definition wird uebersprungen, die
        zweite (bekannte) entsteht -> ``["Doch anlegen"]``. Mutant: die
        Schleife bricht an der ersten (unbekannten) Definition ab, die Liste
        bleibt leer (``[]``) — die komplette Batch nach dem fehlenden Slug
        fehlt im Startbestand der Einrichtung.
        """
        ensure_default_package(facility)

        created = ensure_quick_templates(
            facility,
            (
                QuickTemplateDef("nicht-im-katalog", "Nicht anlegen", {}),
                QuickTemplateDef("kontakt", "Doch anlegen", {}),
            ),
        )

        assert [t.name for t in created] == ["Doch anlegen"]
        assert created[0].sort_order == 0

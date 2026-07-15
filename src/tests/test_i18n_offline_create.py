"""Refs #1499, #1524: EN-Uebersetzungs-Lock fuer die pk-losen
Offline-Create-Shells (core/events/offline_create.html,
core/workitems/offline_create.html, core/clients/_offline_event_fields.html).

DE ist Quellsprache (ADR-027); die neuen ``{% trans %}``-Strings dieser
Templates mussten im EN-Katalog nachgezogen werden. ``test_i18n_catalog``
sichert nur, dass KEIN EN-Eintrag leer/fuzzy ist — dieser Test friert
zusaetzlich die konkreten EN-Renderings ein (Regression-Lock analog
test_i18n_offline_banner.py), damit ein spaeterer makemessages-/msgmerge-
Lauf sie nicht unbemerkt auf einen Fuzzy-Fallback zurueckdreht.
"""

from __future__ import annotations

import pytest
from django.utils.translation import gettext, override

# (deutscher Quell-String, erwartetes EN-Rendering) — 1:1 aus dem
# kompilierten en-Katalog, byte-genau.
OFFLINE_CREATE_EN: list[tuple[str, str]] = [
    ("Datei offline nicht erfassbar — später nachreichen.", "File cannot be captured offline — add it later."),
    (
        "Offline gespeichert – wird beim nächsten Online-Kontakt synchronisiert.",
        "Saved offline – will sync the next time you're online.",
    ),
    ("Neuer Kontakt (offline)", "New contact (offline)"),
    ("– bitte wählen –", "– please select –"),
    ("Neue Aufgabe (offline)", "New task (offline)"),
    ("Neue Erfassung (offline)", "New entry (offline)"),
    (
        "Für die Offline-Erfassung sind noch keine Vorlagen vorbereitet oder der "
        "lokale Cache ist leer. Bitte einmal online öffnen.",
        "No templates have been prepared for offline entry yet, or the local cache is empty. Please open online once.",
    ),
    ("Weitere Erfassung", "Another entry"),
    ("Mitgenommene Person", "Person taken offline"),
    ("— ohne Person —", "— no person —"),
    ("Dieser Dokumentationstyp erfordert eine zugeordnete Person.", "This document type requires an assigned person."),
    (
        "Aufgaben dürfen nur von Mitarbeitenden angelegt werden. Für Ihre Rolle ist "
        "die Aufgaben-Erfassung nicht verfügbar.",
        "Tasks may only be created by staff members. Task entry is not available for your role.",
    ),
    ("Zur Offline-Übersicht", "To the offline overview"),
    ("Weitere Aufgabe", "Another task"),
    ("Offline-Erfassung", "Offline entry"),
]


@pytest.mark.parametrize(("source_de", "expected_en"), OFFLINE_CREATE_EN)
def test_offline_create_strings_translated_en(source_de: str, expected_en: str) -> None:
    with override("en"):
        rendered = gettext(source_de)
    assert rendered == expected_en, f"EN-Uebersetzung fuer {source_de!r} driftete: {rendered!r} != {expected_en!r}"


def test_offline_create_lock_covers_all_new_strings() -> None:
    # Schutz gegen versehentliches Ausduennen der Lock-Liste.
    assert len(OFFLINE_CREATE_EN) == 15
    # Kein Eintrag faellt auf den deutschen Quelltext zurueck (echte Uebersetzung).
    with override("en"):
        for source_de, _expected in OFFLINE_CREATE_EN:
            assert gettext(source_de) != source_de

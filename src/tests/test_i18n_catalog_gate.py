"""Regressionstest fuer das verbindliche Katalog-Gate (Refs #1933).

``scripts/i18n_catalog.py check`` (= ``make i18n-check``) erkannte
neue/entfernte/semantisch geaenderte ``msgid``s schon vor diesem Ticket
zuverlaessig — das Problem war ausschliesslich, dass es an ``make ci``
vorbeilief. Dieser Test haengt sich NICHT an eine gemockte Fixture, sondern
beweist das Verhalten gegen den echten Quellbaum: ein ``msgid``, das nur von
Python-Quellcode referenziert wird (keine Template-Referenz — z. B. eine
Formular-Fehlermeldung, die ausschliesslich zur Laufzeit bei einem
fehlgeschlagenen Validierungslauf entsteht), wird aus dem ausgelieferten
Katalog entfernt, bleibt aber im Quellcode referenziert. Kein anderes
Katalog-Gate haette das gesehen: ``test_i18n_catalog.py`` prueft nur
fuzzy/leer bei VORHANDENEN Eintraegen, ``test_i18n_catalog_freshness.py``
nur .po/.mo-Konsistenz — beide setzen voraus, dass der Eintrag noch existiert.
Genau diese Luecke fuehrte laut Problem-Bericht dazu, dass ein Runtime-String
mehrere Commits lang seine englische Uebersetzung verlor, obwohl alle
anderen Gates gruen blieben.

Der Kandidat wird dynamisch ausgewaehlt (nicht als Literal hartkodiert),
damit der Test kuenftige Katalog-Umbauten uebersteht — analog dem Ansatz in
``test_i18n_catalog_freshness.py::test_der_vergleich_erkennt_eine_drift``.
Real gegen den Quellbaum zu extrahieren (``_generated_catalogues()``, ein
echter ``manage.py makemessages``-Subprozess ueber eine Repo-Kopie) macht den
Test langsam (~3 s) — daher zusaetzlich als ``slow`` markiert, analog den
uebrigen Meta-Tests, die Source-Files scannen (``architecture``).
"""

from __future__ import annotations

import shutil

import pytest

from tests.test_i18n_catalog_workflow import _workflow_module

# ``_generated_catalogues()`` startet ein echtes ``manage.py makemessages``,
# das ohne GNU-gettext (``msguniq``/``xgettext``) mit ``CommandError`` abbricht.
# Der Skip haelt eine lokale Umgebung ohne die Tools gruen — in CI greift er
# NICHT: ``.github/workflows/test.yml`` installiert das Paket vor pytest
# (Refs #1933, #1900).
pytestmark = [
    pytest.mark.architecture,
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("msguniq") is None,
        reason="GNU-gettext-Tools fehlen (msguniq) — 'sudo apt-get install gettext' installiert sie",
    ),
]


def _find_runtime_only_entry(workflow, text: str) -> tuple[str, str]:
    """Ein aktiver, einzeiliger Katalogeintrag, der AUSSCHLIESSLICH von
    ``.py``-Quellcode referenziert wird (keine Template-Referenz) — ein
    genuiner Runtime-String (z. B. eine Formular-Fehlermeldung), keine
    statische UI-Beschriftung. Gibt (Blocktext, msgid) zurueck.
    """
    for block in workflow._blocks(text):
        if block.key is None:
            continue
        msgctxt, msgid, msgid_plural = block.key
        if msgctxt or msgid_plural or not msgid:
            continue
        lines = block.text.splitlines()
        refs = [line for line in lines if line.startswith("#:")]
        if not refs:
            continue
        ref_text = " ".join(refs)
        if "templates/" in ref_text or ".py:" not in ref_text:
            continue
        # Mehrzeilige msgid/msgstr ueberspringen — die Blockentfernung per
        # Chunk-Vergleich unten braucht einen eindeutigen, einzeiligen Treffer.
        msgid_lines = [line for line in lines if line.startswith("msgid ")]
        msgstr_lines = [line for line in lines if line.startswith("msgstr ")]
        if len(msgid_lines) != 1 or len(msgstr_lines) != 1:
            continue
        return block.text, msgid
    raise AssertionError(
        "Kein rein Python-referenzierter Katalogeintrag gefunden — Testannahme verletzt "
        "(sollte es im aktuellen Baum mehrfach geben, z. B. Formular-Fehlermeldungen)"
    )


def _remove_block(text: str, target_block_text: str) -> str:
    """Entfernt GENAU den Block mit ``target_block_text`` — Tokenisierung wie
    ``workflow._blocks()`` (Split auf Leerzeile), damit kein Offset-/
    Newline-Mismatch durch ``.strip("\\n")`` entstehen kann.
    """
    raw_blocks = text.split("\n\n")
    kept = [raw for raw in raw_blocks if raw.strip("\n") != target_block_text]
    assert len(kept) == len(raw_blocks) - 1, "Zielblock nicht eindeutig im Katalog gefunden"
    return "\n\n".join(kept)


def test_losing_a_runtime_only_referenced_msgid_is_reported_as_drift():
    workflow = _workflow_module()
    existing_path, _ = workflow._paths(workflow.ROOT, "de")
    existing = existing_path.read_text(encoding="utf-8")
    target_block, target_msgid = _find_runtime_only_entry(workflow, existing)

    modified_existing = _remove_block(existing, target_block)
    assert f'msgid "{target_msgid}"' not in modified_existing

    # Echte Extraktion gegen den tatsaechlichen Quellbaum — kein Mock, kein
    # Fixture-Katalog. Beweist, dass der Kandidat wirklich (noch) referenziert
    # wird, nicht nur ein Testartefakt ist.
    temp, mirror = workflow._generated_catalogues()
    try:
        generated_path, _ = workflow._paths(mirror, "de")
        generated = generated_path.read_text(encoding="utf-8")
    finally:
        temp.cleanup()

    assert f'msgid "{target_msgid}"' in generated, (
        f"{target_msgid!r} wird nicht mehr aus dem echten Quellbaum extrahiert — "
        "Testannahme (Python-only Runtime-String) verletzt, anderen Kandidaten waehlen"
    )

    # Kontrolle: der unveraenderte Katalog hat KEINE Drift gegen die reale
    # Extraktion — sonst waere ein spaeteres "drift erkannt" bedeutungslos
    # (koennte an vorbestehendem Drift liegen statt am injizierten Verlust).
    baseline_drift = workflow._report(existing, generated, "de")
    assert baseline_drift is False, (
        "Der reale Katalog hat bereits unabhaengig vom injizierten Verlust Drift — "
        "'make i18n-check' vorher lokal reparieren, bevor dieser Test aussagekraeftig ist"
    )

    drift_after_loss = workflow._report(modified_existing, generated, "de")
    assert drift_after_loss is True
    assert int(drift_after_loss) == 1  # Exit-Code-Vertrag von ``scripts/i18n_catalog.py check``

    added = workflow.catalogue_keys(generated) - workflow.catalogue_keys(modified_existing)
    assert ("", target_msgid, "") in added

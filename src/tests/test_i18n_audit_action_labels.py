"""Refs #1766: Guard fuer die englischen Audit-Aktions-Labels.

Der EN-Katalog trug einen systemischen Rueckstand aus dem #974-Pass: elf
Audit-Aktions-Labels waren schlicht falsch uebersetzt, weil beim
``msgmerge``-Lauf Fuzzy-Vorschlaege fremder ``msgid``s uebernommen und
danach entfuzzt wurden (``Ereignis angelegt`` -> "Edit Event",
``2FA deaktiviert`` -> "Case has been updated.",
``Passwort-Reset angefordert`` -> "Password changed",
``2FA-Verifikation fehlgeschlagen`` -> "Login failed"). Die EN-Audit-Log-
Ansicht zeigte damit *falsche Aktionsnamen* — im DSGVO-Rechenschaftskontext
(Art. 5 Abs. 2) ein Sicherheits-, kein Kosmetikproblem: ein Eintrag "Login
failed" konnte in Wahrheit eine fehlgeschlagene 2FA-Verifikation sein.

Kein Bestands-Guard sah das. ``test_i18n_catalog`` prueft nur ``fuzzy``/leer
(beides traf nicht zu), und der Duplikat-Guard in
``scripts/check_translations.py`` ist bewusst **de-only** — in ``en.po``
sind msgstr-Kollisionen der Normalfall (``Ereignis``/``Event`` uebersetzen
korrekt beide auf "Event"). Dieser Test zieht die Grenze deshalb dort, wo
Eindeutigkeit fachlich begruendbar ist: innerhalb der geschlossenen
``AuditLog.Action``-Werteliste. Zwei verschiedene Audit-Aktionen duerfen nie
denselben englischen Namen tragen, sonst ist die Aktion im Log nicht mehr
unterscheidbar.

Der Test prueft beide Ebenen:

* die ``.po``-Quelle (was Uebersetzerinnen pflegen) und
* den **kompilierten** Katalog via ``gettext`` — die ``src/locale/**/*.mo``
  sind bewusst versioniert und werden per ``COPY src/`` ausgeliefert (der
  Docker-Build fuehrt kein ``compilemessages`` aus, s. ``.gitignore``).
  Eine ``.po``-Korrektur ohne frisches ``.mo`` waere in Produktion wirkungslos.
"""

from pathlib import Path

import pytest
from django.utils import translation

from core.models.audit import AuditLog

# Der PO-Parser aus dem Bestands-Guard (Refs #974) — bewusst wiederverwendet
# statt kopiert; ``src/tests`` ist ein Package und ``src`` liegt im pythonpath.
from tests.test_i18n_catalog import _parse_active_entries

PO_PATH = Path(__file__).resolve().parent.parent / "locale" / "en" / "LC_MESSAGES" / "django.po"

# Audit-Labels, die ihre englische Uebersetzung legitim mit einer anderen
# msgid teilen: verschiedene deutsche Quell-Strings, die im Englischen
# tatsaechlich dasselbe Wort ergeben. Neue Eintraege nur mit Begruendung —
# ein *nicht* gelisteter Treffer ist der Regelfall des #974-Fuzzy-Bugs
# (die Uebersetzung gehoert zu einer fremden msgid).
ALLOWED_SHARED_TRANSLATIONS: dict[str, str] = {
    # Aktion ``EXPORT`` vs. Button-Label "Exportieren" — beides "Export".
    "Export": "Export",
    # Aktion ``DOWNLOAD`` vs. Button-Label "Herunterladen" — beides "Download".
    "Download": "Download",
}

# Die im Ticket dokumentierten Fehluebersetzungen. Der Test pinnt bewusst die
# ALTEN (falschen) Werte als verboten statt neue Wortlaute festzuschreiben —
# eine spaetere Stil-Ueberarbeitung soll den Guard nicht falsch-rot faerben.
KNOWN_MISTRANSLATIONS: dict[str, str] = {
    "Fall aktualisiert": "Case has been updated.",
    "Fall geschlossen": "closed",
    "Fall wiedereröffnet": "reopened",
    "Meilenstein gelöscht": "Delete Milestone",
    "Ereignis angelegt": "Edit Event",
    "Aufgabe angelegt": "Total tasks",
    "Aufgabe aktualisiert": "Task has been updated.",
    "Passwort-Reset angefordert": "Password changed",
    "2FA aktiviert": "updated",
    "2FA deaktiviert": "Case has been updated.",
    "2FA-Verifikation fehlgeschlagen": "Login failed",
}


def _audit_msgids() -> list[str]:
    """Die deutschen Quell-Strings aller ``AuditLog.Action``-Labels.

    ``translation.override(None)`` deaktiviert die Uebersetzung, damit die
    lazy Labels ihre msgid liefern (und nicht die aktive Sprache).
    """
    with translation.override(None):
        return [str(label) for _value, label in AuditLog.Action.choices]


def _catalog_entries():
    return list(_parse_active_entries(PO_PATH.read_text(encoding="utf-8")))


@pytest.mark.skipif(not PO_PATH.exists(), reason="EN-Katalog nicht vorhanden")
class TestAuditActionLabelsInCatalog:
    """Die Audit-Aktions-Labels muessen im EN-Katalog vollstaendig,
    nicht-fuzzy und untereinander eindeutig uebersetzt sein."""

    def test_every_audit_label_is_present_and_translated(self):
        entries = {mid: (fuzzy, values) for mid, fuzzy, values in _catalog_entries()}
        problems = []
        for msgid in _audit_msgids():
            if msgid not in entries:
                problems.append(f"{msgid!r}: fehlt im EN-Katalog")
                continue
            fuzzy, values = entries[msgid]
            if fuzzy:
                problems.append(f"{msgid!r}: als fuzzy markiert (wird beim Kompilieren verworfen)")
            if not values or not values[0]:
                problems.append(f"{msgid!r}: leere msgstr")
        assert not problems, "Audit-Aktions-Labels ohne nutzbare EN-Uebersetzung (Refs #1766):\n" + "\n".join(problems)

    def test_audit_label_translations_are_unique(self):
        entries = {mid: values for mid, _fuzzy, values in _catalog_entries()}
        by_translation: dict[str, list[str]] = {}
        for msgid in _audit_msgids():
            values = entries.get(msgid) or [""]
            by_translation.setdefault(values[0], []).append(msgid)
        collisions = {tr: ids for tr, ids in by_translation.items() if len(ids) > 1}
        assert not collisions, (
            "Zwei Audit-Aktionen tragen denselben englischen Namen — im Audit-Log "
            f"nicht mehr unterscheidbar (Refs #1766): {collisions}"
        )

    def test_audit_labels_do_not_borrow_a_foreign_translation(self):
        """Klassisches #974-Fuzzy-Muster: die Uebersetzung gehoert zu einer
        anderen msgid. Legitime Synonyme stehen in
        ``ALLOWED_SHARED_TRANSLATIONS`` — mit Begruendung."""
        audit_msgids = set(_audit_msgids())
        entries = _catalog_entries()
        own = {mid: values[0] for mid, _f, values in entries if mid in audit_msgids and values}
        foreign: dict[str, list[str]] = {}
        for mid, _fuzzy, values in entries:
            if mid in audit_msgids:
                continue
            for value in values:
                if value:
                    foreign.setdefault(value, []).append(mid)
        borrowed = {
            msgid: (translated, foreign[translated])
            for msgid, translated in own.items()
            if translated in foreign and ALLOWED_SHARED_TRANSLATIONS.get(msgid) != translated
        }
        assert not borrowed, (
            "Audit-Label traegt die Uebersetzung einer fremden msgid (Refs #1766, "
            f"#974-Fuzzy-Muster) — korrigieren oder bewusst allowlisten: {borrowed}"
        )


class TestAuditActionLabelsAtRuntime:
    """Gegenprobe gegen den KOMPILIERTEN Katalog: das ausgelieferte ``.mo``
    muss die Korrektur ebenfalls tragen (``.po``-Fix allein waere in
    Produktion wirkungslos)."""

    def test_known_mistranslations_are_gone(self):
        with translation.override("en"):
            still_wrong = {
                msgid: wrong for msgid, wrong in KNOWN_MISTRANSLATIONS.items() if translation.gettext(msgid) == wrong
            }
        assert not still_wrong, (
            "Falsche EN-Audit-Labels weiterhin im kompilierten Katalog — "
            f"`msgfmt`/`compilemessages` nach der .po-Korrektur vergessen? (Refs #1766): {still_wrong}"
        )

    def test_every_audit_label_translates_to_english(self):
        """Kein Label darf auf den deutschen Quelltext zurueckfallen —
        Ausnahmen sind Bezeichner, die im Englischen identisch sind."""
        identical_by_design = {"Export", "Download", "Legal Hold"}
        with translation.override("en"):
            untranslated = [
                msgid
                for msgid in _audit_msgids()
                if translation.gettext(msgid) == msgid and msgid not in identical_by_design
            ]
        assert not untranslated, (
            f"Audit-Labels fallen in der EN-Oberflaeche auf Deutsch zurueck (Refs #1766): {untranslated}"
        )

    def test_runtime_labels_stay_unique(self):
        with translation.override("en"):
            labels = [translation.gettext(msgid) for msgid in _audit_msgids()]
        duplicates = {label for label in labels if labels.count(label) > 1}
        assert not duplicates, f"Doppelte EN-Audit-Labels zur Laufzeit (Refs #1766): {duplicates}"

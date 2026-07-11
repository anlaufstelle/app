"""Refs #1348: Regressionstest gegen falsch uebernommene de-msgstr-Werte.

In der deutschen ``django.po`` renderten zwei inhaltlich unterschiedliche
UI-Texte fehlerhaft identisch als "Keine Klienten gefunden" — ein
uebernommener/kopierter ``msgstr``, der weder zum jeweiligen ``msgid``
passte noch der Sprachleitlinie folgte ("Person" statt "Klient",
s. docs/sprachleitlinie.md). Betroffen:

- ``attachments/partials/attachment_table.html:71`` — leere Anhangsliste
- ``workitem_bulk.py`` (Bulk-Aktion auf der Aufgaben-Inbox) — kein Item traf
  den serverseitigen Bulk-Filter
- ``system/dashboard.html:108`` — kein Backup-Health-Datensatz (via
  ``#, fuzzy``, daher ohnehin nicht live kompiliert; s.
  scripts/check_translations.py, derselbe Copy-Paste-Fehler)

Dieser Test aktiviert die deutsche Sprache und prueft ``gettext()`` gegen
den KOMPILIERTEN Katalog (``.mo``) — nicht nur die ``.po``-Quelle wie
``scripts/check_translations.py``. Beide Guards ergaenzen sich: das Skript
faengt die Fehlklasse (`Refs #1348`) im ``.po`` per Duplikat-Erkennung,
dieser Test verifiziert das tatsaechliche Laufzeitverhalten inkl. eines
frisch kompilierten ``.mo``.
"""

from django.utils.translation import gettext, override


class TestDeMsgstrNoLongerMisattributed:
    """Jede msgid muss zu ihrer eigenen, sinnvollen msgstr uebersetzen —
    nicht zur msgstr eines fachlich fremden Strings."""

    def test_no_files_found_does_not_say_no_clients(self):
        with override("de"):
            assert gettext("Keine Dateien gefunden") == "Keine Dateien gefunden"

    def test_no_valid_tasks_found_does_not_say_no_clients(self):
        with override("de"):
            assert gettext("Keine gültigen Aufgaben gefunden.") == "Keine gültigen Aufgaben gefunden."

    def test_no_backup_found_does_not_say_no_clients(self):
        with override("de"):
            assert gettext("Kein Backup gefunden") == "Kein Backup gefunden"

    def test_the_three_strings_no_longer_collide(self):
        """Die drei msgids duerfen nicht mehr auf dieselbe (falsche) msgstr
        zusammenfallen — genau das war der urspruengliche Bug."""
        with override("de"):
            translations = {
                gettext("Keine Dateien gefunden"),
                gettext("Keine gültigen Aufgaben gefunden."),
                gettext("Kein Backup gefunden"),
            }
        assert len(translations) == 3, (
            f"Erwartet 3 unterschiedliche Uebersetzungen, bekam {translations!r} "
            "— Verdacht auf wiederkehrenden msgstr-Copy-Paste (Refs #1348)."
        )
        assert "Keine Klienten gefunden" not in translations

    def test_actual_clients_empty_state_still_says_no_persons(self):
        """Gegenprobe: die ECHTE 'Keine Klienten'-Ansicht (Klientenliste)
        folgt der Sprachleitlinie ("Person" statt "Klient",
        docs/sprachleitlinie.md) und bleibt von diesem Fix unberuehrt."""
        with override("de"):
            assert gettext("Keine Personen gefunden") == "Keine Personen gefunden"

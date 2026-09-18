"""Architecture-Guards — Klarnamen-Interpolation in Log-Vorlagen (Refs #2227).

Der PII-Scrub schuetzt den **eingesetzten Wert**, nicht die Vorlage: Der
Sentry-Pfad ersetzt die fertige Zeile durch ihr Template und verwirft die
Argumente, der Log-Formatter maskiert bekannte Muster. Beides greift nicht,
wenn ein Personen- oder Einrichtungsname als nackte Zeichenfolge in die Zeile
wandert — ``logger.info("%s nicht gefunden", user.get_full_name())`` sieht fuer
jede Mustererkennung aus wie beliebiger Text. ``docs/admin-guide.md`` § 5.6
formuliert daraus die Entwicklungsregel „keine Klarnamen in Log-Vorlagen und
Fehlertexten"; dieser Guard macht sie pruefbar.

Der Scan arbeitet **auf dem AST**, nicht auf dem Quelltext: ein Regex ueber
``logger\\.``-Zeilen kaeme weder durch einen mehrzeiligen Aufruf noch durch
einen f-String-Ausdruck und wuerde jede Erwaehnung in einem Kommentar oder
Docstring melden. Gesucht werden Aufrufe eines Loggers, in deren Argumenten
(einschliesslich ``extra=``, f-String-Teilen und ``.format()``-Argumenten) ein
Attributzugriff auf ein Personen-/Namensfeld steht.

Was der Guard NICHT leistet (bewusst): Er sieht nur den direkten
Attributzugriff im Aufruf. Ein ueber eine Zwischenvariable oder einen
Hilfsaufruf eingesetzter Name bleibt unsichtbar — dagegen steht weiterhin die
Regel im Handbuch und der fail-closed-Scrub des Sentry-Pfades.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

#: Methoden des ``logging``-Moduls, die eine Zeile schreiben.
LOG_METHODS = frozenset(
    {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "log",
    }
)

#: Attribute, deren Wert ein Klarname (oder ein namensnaher Identifikator)
#: ist. Deckungsgleich mit den Personen-Schluesseln, die der Sentry-Scrub
#: exakt maskiert (``_EXACT_SENSITIVE_KEYS`` in
#: ``anlaufstelle/settings/_sentry.py``) — was dort nicht hinausgehen darf,
#: gehoert auch nicht in die Vorlage. ``get_full_name`` steht als Methode in
#: derselben Liste: ``user.get_full_name()`` ist derselbe Wert mit Klammern.
PERSON_ATTRIBUTES = frozenset(
    {
        "name",
        "username",
        "pseudonym",
        "first_name",
        "last_name",
        "email",
        "full_name",
        "display_name",
        "get_full_name",
    }
)

#: Gescannter Baum (App-Code). Relativ zum Repo-Wurzelverzeichnis.
APP_ROOT = Path("src/core")

#: Fundstellen, die bewusst bleiben duerfen — Schluessel ist
#: ``(Datei relativ zur Scan-Wurzel, umschliessende Funktion/Methode,
#: Attribut)``, bewusst OHNE Zeilennummer (die verschiebt sich bei jeder
#: Umformatierung). Der Wert ist die Begruendung; sie steht hier und nicht im
#: Ticket, damit sie beim naechsten Anfassen der Stelle mitgelesen wird.
ALLOWLIST = {
    (
        "services/dashboard/snapshot.py",
        "create_or_update_snapshot",
        "name",
    ): (
        "``facility.name`` ist der Name der EINRICHTUNG (des Betreibers "
        "selbst), kein Personenname: Klient*innen und Konten tauchen in "
        "diesen beiden Zeilen nicht auf. Die Zeilen markieren einen "
        "uebersprungenen Statistik-Backend-Lauf bzw. einen geschriebenen "
        "Snapshot; in einer Installation mit mehreren Einrichtungen ist der "
        "Name genau die Information, die den Ausfall zuordenbar macht. Auf "
        "dem Weg zu einem Error-Tracker geht der Wert ohnehin nicht mit — "
        "der Hook ersetzt die Zeile durch ihr Template (Refs #2198). "
        "Verbesserungsmoeglichkeit (eigenes Ticket, Datei liegt ausserhalb "
        "dieser Aenderung): ``facility_id`` im ``extra`` statt des Namens in "
        "der Vorlage."
    ),
}


def _receiver_name(node):
    """Name des Objekts, auf dem eine Methode aufgerufen wird."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_logger_call(node):
    """Ist ``node`` ein ``logger.<level>(…)``/``logging.<level>(…)``-Aufruf?

    Erkannt wird ueber den Empfaenger, nicht ueber den Modulimport: im
    App-Code heisst er ``logger`` (Modul-Konstante), daneben sind
    ``logging.warning`` (Modulfunktion), ``self.logger`` und ``_logger``
    gaengige Schreibweisen. ``messages.error(...)`` (Django-Messages) faellt
    damit heraus — das ist eine Nutzermeldung, kein Log-Sink.
    """
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in LOG_METHODS:
        return False
    receiver = _receiver_name(node.func.value)
    if receiver is None:
        return False
    lowered = receiver.lower()
    return lowered in {"logging", "log"} or lowered.endswith("logger") or lowered.endswith("_log")


def _person_attributes(call):
    """Personen-/Namensattribute in den Argumenten eines Aufrufs.

    ``ast.walk`` ueber jedes Argument deckt alle im Ticket genannten Formen in
    einem Durchgang ab: ``%s``-Positionsargumente, f-String-Teile
    (``JoinedStr``/``FormattedValue``), die Argumente eines
    ``"…".format(…)``-Aufrufs und die Werte in ``extra={…}`` (Keyword).
    """
    found = set()
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            if isinstance(node, ast.Attribute) and node.attr in PERSON_ATTRIBUTES:
                found.add(node.attr)
    return found


def find_person_attribute_logging(root):
    """Fundstellen als Menge von ``(datei, geltungsbereich, attribut)``.

    ``root`` ist parametrisiert, damit der Selbsttest gegen ``tmp_path``
    laeuft und nie Proben im echten Baum ablegt. Der Geltungsbereich ist der
    gepunktete Name der umschliessenden Klasse/Funktion (``<modul>`` fuer
    Aufrufe auf Modulebene) — bewusst statt einer Zeilennummer, die sich bei
    jeder Umformatierung verschiebt.
    """
    root = Path(root)
    findings = set()

    def walk(node, scope, relative):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        if isinstance(node, ast.Call) and _is_logger_call(node):
            for attribute in _person_attributes(node):
                findings.add((relative, ".".join(scope) or "<modul>", attribute))
        for child in ast.iter_child_nodes(node):
            walk(child, scope, relative)

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        walk(tree, (), path.relative_to(root).as_posix())
    return findings


def _write(tmp_path, name, source):
    target = tmp_path / name
    target.write_text(textwrap.dedent(source), encoding="utf-8")
    return target


class TestScannerSelfCheck:
    """Je eine positive und eine negative Probe pro Interpolationsmuster."""

    def test_percent_arguments(self, tmp_path):
        _write(
            tmp_path,
            "percent.py",
            """
            def bad(user):
                logger.info("Konto %s fehlt", user.username)

            def good(user):
                logger.info("Konto %s fehlt", user.pk)
            """,
        )
        assert find_person_attribute_logging(tmp_path) == {("percent.py", "bad", "username")}

    def test_fstring_parts(self, tmp_path):
        _write(
            tmp_path,
            "fstring.py",
            """
            def bad(user):
                logger.warning(f"Konto {user.get_full_name()} gesperrt")

            def good(user):
                logger.warning(f"Konto {user.pk} gesperrt")
            """,
        )
        assert find_person_attribute_logging(tmp_path) == {("fstring.py", "bad", "get_full_name")}

    def test_format_arguments(self, tmp_path):
        _write(
            tmp_path,
            "formatcall.py",
            """
            class Service:
                def bad(self, client):
                    logger.error("Person {} fehlt".format(client.pseudonym))

                def good(self, client):
                    logger.error("Person {} fehlt".format(client.id))
            """,
        )
        assert find_person_attribute_logging(tmp_path) == {("formatcall.py", "Service.bad", "pseudonym")}

    def test_extra_keyword(self, tmp_path):
        _write(
            tmp_path,
            "extra.py",
            """
            def bad(user):
                logger.info("Einladung versendet", extra={"user": user.email})

            def good(user):
                logger.info("Einladung versendet", extra={"user_pk": user.pk})
            """,
        )
        assert find_person_attribute_logging(tmp_path) == {("extra.py", "bad", "email")}

    def test_non_logger_receivers_and_plain_attribute_access_are_ignored(self, tmp_path):
        """Django-Messages sind Nutzermeldungen, kein Log-Sink.

        Und ein Attributzugriff ausserhalb eines Log-Aufrufs ist schlicht
        Anwendungscode — der Guard darf ihn nicht melden, sonst ist er
        unbrauchbar.
        """
        _write(
            tmp_path,
            "ignored.py",
            """
            def show(request, user):
                messages.error(request, "Konto %s fehlt" % user.username)
                label = user.first_name
                return label
            """,
        )
        assert find_person_attribute_logging(tmp_path) == set()

    def test_module_level_and_alternative_logger_names(self, tmp_path):
        """``logging.warning``/``self.logger``/``_logger`` zaehlen ebenfalls."""
        _write(
            tmp_path,
            "variants.py",
            """
            import logging

            logging.warning("Start %s", FACILITY.name)


            class Job:
                def run(self, user):
                    self.logger.info("Lauf %s", user.last_name)


            def helper(user):
                _logger.debug("Hilfe %s", user.display_name)
            """,
        )
        assert find_person_attribute_logging(tmp_path) == {
            ("variants.py", "<modul>", "name"),
            ("variants.py", "Job.run", "last_name"),
            ("variants.py", "helper", "display_name"),
        }


class TestAppCodeHasNoPersonNamesInLogTemplates:
    """Der Guard ueber den echten App-Code."""

    def test_no_unlisted_person_attribute_logging(self):
        if not APP_ROOT.exists():
            pytest.skip(f"{APP_ROOT} nicht vorhanden")
        violations = sorted(find_person_attribute_logging(APP_ROOT) - set(ALLOWLIST))
        assert not violations, (
            "Log-Aufruf mit Personen-/Namensattribut im Argument. Der PII-Scrub "
            "schuetzt den eingesetzten Wert nur musterbasiert — ein Klarname "
            "passiert ihn. Bitte die technische Kennung loggen (``user_pk``, "
            "``facility_id``, ``client.id``) und den Namen weglassen; siehe "
            "docs/admin-guide.md § 5.6. Echte Ausnahmen mit Begruendung in "
            "ALLOWLIST eintragen.\n"
            "Refs #2227.\n"
            f"Betroffen (Datei, Geltungsbereich, Attribut): {violations}"
        )

    def test_allowlist_has_no_stale_entries(self):
        """Eine Ausnahme, die niemand mehr braucht, verdeckt die naechste."""
        if not APP_ROOT.exists():
            pytest.skip(f"{APP_ROOT} nicht vorhanden")
        findings = find_person_attribute_logging(APP_ROOT)
        stale = sorted(entry for entry in ALLOWLIST if entry not in findings)
        assert not stale, f"ALLOWLIST-Eintraege ohne Fundstelle — bitte entfernen: {stale}"

    def test_every_allowlist_entry_carries_a_reason(self):
        missing = sorted(entry for entry, reason in ALLOWLIST.items() if not (reason or "").strip())
        assert not missing, f"ALLOWLIST-Eintraege ohne Begruendung: {missing}"

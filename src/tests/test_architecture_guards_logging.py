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

Seit Refs #2321 (H-2) sieht der Scan zusaetzlich die **Zwischenvariable**:
``n = user.username; logger.warning("%s", n)`` wird gemeldet. Die Verfolgung
ist bewusst einfach und lokal — Zuweisungen ``<name> = <ausdruck mit
Personenattribut>`` im selben Geltungsbereich (verschachtelte Funktionen und
Klassen bilden ihren eigenen). Das deckt die Schreibweise ab, die beim
Umformulieren einer gemeldeten Zeile als naechstes entsteht.

Was der Guard NICHT leistet (bewusst): Er verfolgt keinen Wert ueber
Funktionsgrenzen, keine Container (Liste/Dict) und keinen Hilfsaufruf, der den
Namen erst besorgt (``lookup_name(pk)``). Dagegen steht weiterhin die Regel im
Handbuch und der fail-closed-Scrub des Sentry-Pfades.
"""

import ast
import textwrap
from collections import Counter
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
    ): {
        # Genau die beiden begruendeten Aufrufe dieser Funktion. Ein dritter
        # macht den Guard rot — die Begruendung gilt fuer diese Stellen, nicht
        # fuer den Geltungsbereich.
        "count": 2,
        "reason": (
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
    },
    # Refs #2321 (H-2): erst die Verfolgung der Zwischenvariablen macht diese
    # drei Stellen sichtbar — der Name steht nicht im Aufruf selbst, sondern in
    # der kurz vorher gebauten Meldung.
    (
        "management/commands/create_statistics_snapshots.py",
        "Command.handle",
        "name",
    ): {
        "count": 1,
        "reason": (
            "``facility.name`` ist der Name der EINRICHTUNG, kein Personenname — "
            "dieselbe Begruendung wie beim Snapshot-Eintrag oben. Die Zeile meldet "
            "eine uebersprungene bzw. gescheiterte Einrichtung; in einer Installation "
            "mit mehreren Einrichtungen ist der Name genau das, was den Ausfall "
            "zuordenbar macht. Auf dem Weg zu einem Error-Tracker geht der Wert nicht "
            "mit — der Hook ersetzt die Zeile durch ihr Template (Refs #2198). Sichtbar "
            "geworden ist die Stelle erst mit der Verfolgung der Zwischenvariablen "
            "(Refs #2321, H-2): der Name wandert ueber die kurz vorher gebaute Meldung "
            "in die Vorlage. Verbesserungsmoeglichkeit (eigenes Ticket, Datei liegt "
            "ausserhalb dieser Aenderung): ``facility_id`` im ``extra``."
        ),
    },
    (
        "management/commands/create_statistics_snapshots.py",
        "Command._report_failure",
        "name",
    ): {
        "count": 1,
        "reason": (
            "``facility.name`` ist der Name der EINRICHTUNG, kein Personenname — "
            "dieselbe Begruendung wie beim Snapshot-Eintrag oben. Die Zeile meldet "
            "eine uebersprungene bzw. gescheiterte Einrichtung; in einer Installation "
            "mit mehreren Einrichtungen ist der Name genau das, was den Ausfall "
            "zuordenbar macht. Auf dem Weg zu einem Error-Tracker geht der Wert nicht "
            "mit — der Hook ersetzt die Zeile durch ihr Template (Refs #2198). Sichtbar "
            "geworden ist die Stelle erst mit der Verfolgung der Zwischenvariablen "
            "(Refs #2321, H-2): der Name wandert ueber die kurz vorher gebaute Meldung "
            "in die Vorlage. Verbesserungsmoeglichkeit (eigenes Ticket, Datei liegt "
            "ausserhalb dieser Aenderung): ``facility_id`` im ``extra``."
        ),
    },
    (
        "management/commands/enforce_retention.py",
        "Command._warn_missing_settings",
        "name",
    ): {
        "count": 1,
        "reason": (
            "``facility.name`` ist der Name der EINRICHTUNG, kein Personenname — "
            "dieselbe Begruendung wie beim Snapshot-Eintrag oben. Die Zeile meldet "
            "eine uebersprungene bzw. gescheiterte Einrichtung; in einer Installation "
            "mit mehreren Einrichtungen ist der Name genau das, was den Ausfall "
            "zuordenbar macht. Auf dem Weg zu einem Error-Tracker geht der Wert nicht "
            "mit — der Hook ersetzt die Zeile durch ihr Template (Refs #2198). Sichtbar "
            "geworden ist die Stelle erst mit der Verfolgung der Zwischenvariablen "
            "(Refs #2321, H-2): der Name wandert ueber die kurz vorher gebaute Meldung "
            "in die Vorlage. Verbesserungsmoeglichkeit (eigenes Ticket, Datei liegt "
            "ausserhalb dieser Aenderung): ``facility_id`` im ``extra``."
        ),
    },
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


def _person_attributes(call, aliases=None):
    """Personen-/Namensattribute in den Argumenten eines Aufrufs.

    ``ast.walk`` ueber jedes Argument deckt alle im Ticket genannten Formen in
    einem Durchgang ab: ``%s``-Positionsargumente, f-String-Teile
    (``JoinedStr``/``FormattedValue``), die Argumente eines
    ``"…".format(…)``-Aufrufs und die Werte in ``extra={…}`` (Keyword).

    Refs #2321 (H-2): ``aliases`` sind die lokalen Variablen, die im selben
    Geltungsbereich aus einem Personenattribut befuellt wurden. Ohne sie blieb
    ``n = user.username; logger.warning("%s", n)`` unsichtbar — also genau die
    Schreibweise, in die eine gemeldete Zeile beim Umformulieren rutscht.
    Gemeldet wird das urspruengliche Attribut, damit Fundstelle und Allowlist
    dieselbe Sprache sprechen.
    """
    aliases = aliases or {}
    found = set()
    for argument in [*call.args, *(keyword.value for keyword in call.keywords)]:
        for node in ast.walk(argument):
            if isinstance(node, ast.Attribute) and node.attr in PERSON_ATTRIBUTES:
                found.add(node.attr)
            elif isinstance(node, ast.Name) and node.id in aliases:
                found.add(aliases[node.id])
    return found


def _scope_body(node):
    """Direkte Kinder eines Geltungsbereichs OHNE verschachtelte Bereiche.

    Eine verschachtelte Funktion/Klasse bringt ihre eigenen Namen mit; wuerden
    deren Zuweisungen mitgezaehlt, meldete der Guard Stellen, an denen der Name
    gar nicht sichtbar ist.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from ast.walk(child)


def _person_aliases(scope_node):
    """Lokale Namen, die aus einem Personenattribut befuellt werden.

    Erfasst ``n = user.username``, die Mehrfachzuweisung ``a = b =
    client.pseudonym`` und ``n: str = user.get_full_name()``. Erweiterungen
    (``+=``) und Entpackungen bleiben absichtlich aussen vor: dort ist der Wert
    schon zusammengesetzt, und die Verfolgung waere nicht mehr "einfach".
    """
    aliases = {}
    for node in _scope_body(scope_node):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        attributes = {
            inner.attr
            for inner in ast.walk(value)
            if isinstance(inner, ast.Attribute) and inner.attr in PERSON_ATTRIBUTES
        }
        if not attributes:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = sorted(attributes)[0]
    return aliases


def find_person_attribute_logging(root):
    """Fundstellen als ``Counter`` ueber ``(datei, geltungsbereich, attribut)``.

    ``root`` ist parametrisiert, damit der Selbsttest gegen ``tmp_path``
    laeuft und nie Proben im echten Baum ablegt. Der Geltungsbereich ist der
    gepunktete Name der umschliessenden Klasse/Funktion (``<modul>`` fuer
    Aufrufe auf Modulebene) — bewusst statt einer Zeilennummer, die sich bei
    jeder Umformatierung verschiebt.

    Refs #2227: Gezaehlt wird, wie oft der Schluessel vorkommt. Als blosse
    Menge deckte ein Allowlist-Eintrag beliebig viele gleichartige Aufrufe
    desselben Geltungsbereichs ab — die Begruendung galt dann fuer eine
    Stelle, die niemand mehr gelesen hatte.
    """
    root = Path(root)
    findings = Counter()

    def walk(node, scope, relative, aliases):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope = (*scope, node.name)
            # Refs #2321 (H-2): die geerbten Namen bleiben sichtbar (eine
            # innere Funktion sieht die aeussere Zuweisung), die eigenen kommen
            # dazu.
            aliases = {**aliases, **_person_aliases(node)}
        if isinstance(node, ast.Call) and _is_logger_call(node):
            for attribute in _person_attributes(node, aliases):
                findings[(relative, ".".join(scope) or "<modul>", attribute)] += 1
        for child in ast.iter_child_nodes(node):
            walk(child, scope, relative, aliases)

    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        walk(tree, (), path.relative_to(root).as_posix(), {})
    return findings


def allowlist_violations(findings, allowlist):
    """Abweichungen zwischen Fundstellen und Allowlist.

    Liefert ``[(schluessel, gefunden, erwartet), …]``. Gemeldet wird jede
    Abweichung in BEIDE Richtungen: eine nicht eingetragene Fundstelle
    (erwartet 0), eine zusaetzliche Fundstelle an einer eingetragenen Stelle
    (erwartet < gefunden) und ein Eintrag, dessen Fundstelle weggefallen ist
    (gefunden < erwartet). Erst die Trefferzahl macht den mittleren Fall
    sichtbar — sonst deckt eine einmal geschriebene Begruendung jeden
    weiteren gleichartigen Aufruf im selben Geltungsbereich mit ab.
    """
    problems = []
    for key in sorted(set(findings) | set(allowlist)):
        found = findings.get(key, 0)
        expected = allowlist.get(key, {}).get("count", 0)
        if found != expected:
            problems.append((key, found, expected))
    return problems


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
        assert dict(find_person_attribute_logging(tmp_path)) == {("percent.py", "bad", "username"): 1}

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
        assert dict(find_person_attribute_logging(tmp_path)) == {("fstring.py", "bad", "get_full_name"): 1}

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
        assert dict(find_person_attribute_logging(tmp_path)) == {("formatcall.py", "Service.bad", "pseudonym"): 1}

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
        assert dict(find_person_attribute_logging(tmp_path)) == {("extra.py", "bad", "email"): 1}

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
        assert dict(find_person_attribute_logging(tmp_path)) == {}

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
        assert dict(find_person_attribute_logging(tmp_path)) == {
            ("variants.py", "<modul>", "name"): 1,
            ("variants.py", "Job.run", "last_name"): 1,
            ("variants.py", "helper", "display_name"): 1,
        }


class TestIntermediateVariablesAreFollowed:
    """Refs #2321 (H-2): ``n = user.username; logger.warning("%s", n)``.

    Bis dahin sah der Scan nur den direkten Attributzugriff im Aufruf — eine
    gemeldete Zeile war also durch schlichtes Zwischenspeichern zu umgehen.
    """

    def test_local_variable_carries_the_attribute(self, tmp_path):
        _write(
            tmp_path,
            "indirect.py",
            """
            def bad(user):
                n = user.username
                logger.warning("Konto %s fehlt", n)
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {("indirect.py", "bad", "username"): 1}

    def test_fstring_message_built_before_the_call(self, tmp_path):
        """Die Form, die im echten Baum vorkommt: erst die Meldung bauen,
        dann die fertige Meldung loggen."""
        _write(
            tmp_path,
            "message.py",
            """
            def bad(facility):
                message = f"Keine Einstellungen fuer '{facility.name}', uebersprungen."
                logger.warning("job: %s", message)
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {("message.py", "bad", "name"): 1}

    def test_annotated_assignment_counts(self, tmp_path):
        _write(
            tmp_path,
            "annotated.py",
            """
            def bad(user):
                n: str = user.get_full_name()
                logger.info("%s", n)
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {("annotated.py", "bad", "get_full_name"): 1}

    def test_unrelated_variable_stays_invisible(self, tmp_path):
        _write(
            tmp_path,
            "clean.py",
            """
            def good(user):
                n = user.pk
                logger.info("Konto %s fehlt", n)
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {}

    def test_alias_of_a_sibling_scope_does_not_leak(self, tmp_path):
        """Ein gleichnamiger Name in einer ANDEREN Funktion faerbt nicht ab —
        sonst meldete der Guard Stellen, an denen der Wert gar nicht steht."""
        _write(
            tmp_path,
            "scopes.py",
            """
            def bad(user):
                n = user.username
                logger.warning("%s", n)


            def good(request):
                n = request.path
                logger.info("%s", n)
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {("scopes.py", "bad", "username"): 1}

    def test_outer_scope_alias_is_visible_inside(self, tmp_path):
        _write(
            tmp_path,
            "nested.py",
            """
            def outer(user):
                n = user.username

                def inner():
                    logger.warning("%s", n)

                inner()
            """,
        )
        assert dict(find_person_attribute_logging(tmp_path)) == {("nested.py", "outer.inner", "username"): 1}


class TestAllowlistPinsTheNumberOfHits:
    """Ein Allowlist-Eintrag deckt genau die begruendeten Aufrufe — nicht mehr.

    Vorher war der Schluessel ``(Datei, Geltungsbereich, Attribut)`` eine
    Menge: ein weiterer gleichartiger Aufruf im selben Geltungsbereich lief
    unter derselben Begruendung mit, ohne dass ihn jemand gesehen haette.
    Die erwartete Trefferzahl macht ihn sichtbar.
    """

    SOURCE = """
        def melden(facility):
            logger.info("Lauf uebersprungen %s", facility.name)
            logger.info("Snapshot geschrieben %s", facility.name)
        """

    def _findings(self, tmp_path, source):
        _write(tmp_path, "probe.py", source)
        return find_person_attribute_logging(tmp_path)

    def test_repeated_calls_in_one_scope_are_counted(self, tmp_path):
        findings = self._findings(tmp_path, self.SOURCE)
        assert dict(findings) == {("probe.py", "melden", "name"): 2}

    def test_pinned_count_accepts_exactly_the_listed_hits(self, tmp_path):
        findings = self._findings(tmp_path, self.SOURCE)
        allowlist = {("probe.py", "melden", "name"): {"count": 2, "reason": "Begruendung"}}
        assert allowlist_violations(findings, allowlist) == []

    def test_one_more_call_in_the_same_scope_turns_the_guard_red(self, tmp_path):
        """Der eigentliche Punkt: ein dritter Aufruf faellt auf."""
        findings = self._findings(tmp_path, self.SOURCE + '    logger.info("Noch eine %s", facility.name)\n        ')
        allowlist = {("probe.py", "melden", "name"): {"count": 2, "reason": "Begruendung"}}
        assert allowlist_violations(findings, allowlist) == [(("probe.py", "melden", "name"), 3, 2)]

    def test_unlisted_finding_is_reported_with_expectation_zero(self, tmp_path):
        findings = self._findings(tmp_path, self.SOURCE)
        assert allowlist_violations(findings, {}) == [(("probe.py", "melden", "name"), 2, 0)]

    def test_entry_without_hits_is_reported_as_stale(self, tmp_path):
        findings = self._findings(tmp_path, "def leer():\n    pass\n")
        allowlist = {("weg.py", "alt", "name"): {"count": 1, "reason": "Begruendung"}}
        assert allowlist_violations(findings, allowlist) == [(("weg.py", "alt", "name"), 0, 1)]


class TestAppCodeHasNoPersonNamesInLogTemplates:
    """Der Guard ueber den echten App-Code."""

    def test_no_unlisted_person_attribute_logging(self):
        if not APP_ROOT.exists():
            pytest.skip(f"{APP_ROOT} nicht vorhanden")
        violations = allowlist_violations(find_person_attribute_logging(APP_ROOT), ALLOWLIST)
        assert not violations, (
            "Log-Aufruf mit Personen-/Namensattribut im Argument. Der PII-Scrub "
            "schuetzt den eingesetzten Wert nur musterbasiert — ein Klarname "
            "passiert ihn. Bitte die technische Kennung loggen (``user_pk``, "
            "``facility_id``, ``client.id``) und den Namen weglassen; siehe "
            "docs/admin-guide.md § 5.6. Echte Ausnahmen mit Begruendung in "
            "ALLOWLIST eintragen.\n"
            "Refs #2227.\n"
            "Gemeldet wird auch ein ZUSAETZLICHER Aufruf an einer bereits "
            "begruendeten Stelle: die Begruendung gilt fuer die dort "
            "eingetragene Trefferzahl, nicht fuer den Geltungsbereich.\n"
            f"Betroffen ((Datei, Geltungsbereich, Attribut), gefunden, erwartet): {violations}"
        )

    def test_allowlist_has_no_stale_entries(self):
        """Eine Ausnahme, die niemand mehr braucht, verdeckt die naechste."""
        if not APP_ROOT.exists():
            pytest.skip(f"{APP_ROOT} nicht vorhanden")
        findings = find_person_attribute_logging(APP_ROOT)
        stale = sorted(entry for entry in ALLOWLIST if not findings.get(entry))
        assert not stale, f"ALLOWLIST-Eintraege ohne Fundstelle — bitte entfernen: {stale}"

    def test_every_allowlist_entry_pins_a_positive_count(self):
        """Ohne Trefferzahl waere der Eintrag wieder ein Freibrief."""
        broken = sorted(
            entry
            for entry, listed in ALLOWLIST.items()
            if not isinstance(listed.get("count"), int) or listed["count"] < 1
        )
        assert not broken, f"ALLOWLIST-Eintraege ohne erwartete Trefferzahl: {broken}"

    def test_every_allowlist_entry_carries_a_reason(self):
        missing = sorted(entry for entry, listed in ALLOWLIST.items() if not (listed.get("reason") or "").strip())
        assert not missing, f"ALLOWLIST-Eintraege ohne Begruendung: {missing}"

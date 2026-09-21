"""Guard: der Log-Scrub laeuft linear und maskiert unveraendert (Refs #2269).

``core.logging.scrub`` laeuft seit #2228 ueber JEDE Zeile beider
produktiv konfigurierter Formatter. Das E-Mail-Muster hatte keinen linken
Anker: in einem langen Zeichenlauf ohne Leerzeichen begann die Suche an
jeder Position erneut und las den Lauf bis zum Ende — quadratische
Laufzeit (gemessen am Ausgangsstand: 2 000 Zeichen 0,014 s,
16 000 Zeichen 0,877 s, 100 000 Zeichen 39,6 s). Anfragegesteuerte
Zeichenketten (Pfade in ``Not Found:``-Zeilen, CSP-Reports) landen genau
dort; das ist ein Verstaerkungsfaktor fuer Anfragefluten.

Zwei Zusagen werden hier gepinnt:

* **Laufzeit** — das Verhaeltnis der Laufzeit bei 16 000 zu der bei
  2 000 Zeichen bleibt unter einer grosszuegigen linearen Schranke. Kein
  absoluter Millisekunden-Wert: der haengt an der Maschine. Gemessen wird
  Best-of-5 auf gleichem Zeichenvolumen je Messung, damit weder ein
  Scheduler-Ausrutscher noch die Timer-Aufloesung das Urteil bestimmt.
* **Maskierung** — die Umformung maskiert exakt dieselben Zeichen wie
  vorher. Verglichen wird gegen die Musterliste von vor dem Fix, ueber
  die vorhandenen Guard-Faelle und ueber generierte Eingaben (Adressen am
  Anfang/Ende/mittig, in Klammern und Anfuehrungszeichen, mit Plus-/
  Punkt-Teilen, mit Unicode-Nachbarn, direkt aneinander, in URLs und
  Query-Werten).

  **Eine** Ausgabe-Abweichung ist bekannt und hier gepinnt: zwei ohne
  Trennzeichen aneinandergrenzende Adressen ergaben frueher zwei
  Platzhalter (``<email><email>``), jetzt einen. Maskiert sind dieselben
  Zeichenpositionen — ohne die Wiederholung im Muster waere aus dem
  zweiten Platzhalter dagegen wieder Klartext geworden (der Anker sperrt
  den Wiedereinstieg mitten im Zeichenlauf). Das ist der Grund fuer die
  ``(?:…)+``-Form.

Die Aequivalenz deckt ``settings/_sentry.py`` mit ab: der Tracker-Pfad
benutzt dieselben Funktionen dieses Moduls.
"""

import re
import time

import pytest

import core.logging as core_logging
from core.logging import _SCRUB_PATTERNS, scrub, scrub_text

#: Das E-Mail-Muster, wie es VOR dem Fix aussah — ohne linken Anker.
#: Es ist die Vergleichsbasis der Aequivalenz, nicht der Produktivstand.
_EMAIL_BEFORE_THE_FIX = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

#: Zeichenvolumen je Einzelmessung. Gleich gross fuer kurze wie lange
#: Eingaben, damit beide Messwerte dieselbe Groessenordnung haben und die
#: Timer-Aufloesung aus dem Verhaeltnis herausfaellt.
_CHARS_PER_MEASUREMENT = 48_000

#: Schranke fuer das Verhaeltnis 16 000 zu 2 000 Zeichen. Linear waeren 8
#: (der Laengenfaktor); der Ausgangsstand lag bei rund 65. Die Schranke
#: ist bewusst weit: gepinnt wird die Wachstumsklasse, nicht die Maschine.
_LINEAR_BOUND = 20.0

_SMALL = 2_000
_LARGE = 16_000


def _patterns_before_the_fix():
    """Die Musterliste mit dem unverankerten E-Mail-Muster von vor dem Fix.

    Bewusst die heutige Liste mit EINEM ausgetauschten Eintrag statt einer
    vollstaendigen Kopie: verglichen wird genau die Aenderung dieses
    Tickets, und ein spaeter ergaenztes Muster laesst den Vergleich nicht
    an einer veralteten Kopie scheitern.
    """
    before = [
        (_EMAIL_BEFORE_THE_FIX, repl) if repl == "<email>" else (pattern, repl) for pattern, repl in _SCRUB_PATTERNS
    ]
    assert sum(1 for _, repl in before if repl == "<email>") == 1, "Genau ein E-Mail-Muster erwartet"
    return before


def _email_pattern_before_the_fix():
    """Das E-Mail-Muster von vor dem Fix."""
    return _EMAIL_BEFORE_THE_FIX


def _email_pattern_now():
    """Das heutige E-Mail-Muster aus der Produktivliste."""
    treffer = [pattern for pattern, repl in _SCRUB_PATTERNS if repl == "<email>"]
    assert len(treffer) == 1, "Genau ein E-Mail-Muster erwartet"
    return treffer[0]


def _masked_positions(pattern, text):
    """Die Zeichenpositionen, die *pattern* in *text* maskiert."""
    positionen = set()
    for treffer in pattern.finditer(text):
        positionen.update(range(*treffer.span()))
    return positionen


#: Mehrere unmittelbar aufeinanderfolgende Platzhalter zu einem.
_ADJACENT_PLACEHOLDERS = re.compile(r"(?:<email>)+")


def _collapsed(text):
    """Ausgabe ohne die Information, WIE VIELE Platzhalter nebeneinander stehen."""
    return _ADJACENT_PLACEHOLDERS.sub("<email>", text)


def _scrub_before_the_fix(text):
    """``scrub`` mit der Musterliste von vor dem Fix."""
    for pattern, repl in _patterns_before_the_fix():
        text = pattern.sub(repl, text)
    return text


# --- Eingaben ---------------------------------------------------------------

#: Adressen mit den Formen, die das Muster unterscheiden muss.
_ADDRESSES = (
    "a@b.de",
    "nutzer@example.org",
    "nutzer.name+tag@example.co.uk",
    "n-x_y@sub.domain.example",
    "UPPER@EXAMPLE.DE",
    "mueller@exämple.de",
    "x@y.z",
    "kein.at.zeichen.example.de",
)

#: Umgebungen, in denen eine Adresse in einer Log-Zeile vorkommt.
_CONTEXTS = (
    "{a}",
    "Login failed for {a}",
    "{a} am Anfang",
    "ganz am Ende {a}",
    "({a})",
    '"{a}"',
    "'{a}'",
    "<{a}>",
    "[{a}]",
    "text{a}",
    "{a}text",
    "{a}{a}",
    "{a},{a}",
    "{a} {a}",
    "ä{a}",
    "{a}ö",
    "Grüße an {a}!",
    "https://host.example/pfad?mail={a}&q=1",
    "/pfad/{a}/",
    "Not Found: /suche/{a}",
    "GET /notify?email={a}",
    "user={a};",
    "Authorization: Bearer abc.def-123 {a}",
    "password=hunter2&mail={a}",
    "token=abcdefghijklmnopqrstuvwxyz {a}",
    "{a}.",
    ".{a}",
    "-{a}",
    "{a}-",
    "+{a}",
    "@{a}",
    "{a}@",
)

#: Generierte Eingaben (Kontexte x Adressen).
GENERATED_INPUTS = tuple(context.format(a=address) for context in _CONTEXTS for address in _ADDRESSES)

#: Die Faelle, die die vorhandenen Scrub-Guards pinnen
#: (``test_logging.py``, ``test_sentry_scrubber.py``).
EXISTING_GUARD_CASES = (
    "Login failed for tester@example.com",
    "Reset sent to alice+filter@example.co.uk",
    "Authorization: Bearer abc123xyz.token-9",
    "Authorization: Basic dXNlcjpwYXNz",
    "GET /login?password=hunter2&x=1",
    "Passwort=geheim123",
    "cookie: sessionid=abcdefghijklmnopqrstuvwxyz012345",
    "csrftoken=abcdefghijklmnopqrstuvwxyz012345",
    "token=short",
    "Fehler fuer klient@example.org",
    "GET /notify?email=klient@example.org",
    "klient@example.org ist ungültig",
    "(url=/invite/MTIz/cx1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c/ kontakt=klient@example.org)",
    "email=anna@example.org ende",
    "Kontakt klient@example.org",
    "Not Found: /invite/MTIz/cx1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c/",
    "",
)

ALL_INPUTS = EXISTING_GUARD_CASES + GENERATED_INPUTS


class TestMaskingIsUnchanged:
    """Die Umformung des Musters aendert kein einziges Ergebnis."""

    def test_the_generated_sample_is_large_enough(self):
        """Der Auftrag verlangt mindestens 200 generierte Eingaben."""
        assert len(GENERATED_INPUTS) >= 200
        assert len(set(GENERATED_INPUTS)) == len(GENERATED_INPUTS), "Dubletten taeuschen Abdeckung vor"

    def test_the_sample_masks_something_at_all(self):
        """Gegenprobe: ein Vergleich, der nichts maskiert, belegt nichts."""
        maskiert = [text for text in GENERATED_INPUTS if "<email>" in scrub(text)]
        assert len(maskiert) >= 150, f"nur {len(maskiert)} der {len(GENERATED_INPUTS)} Eingaben werden maskiert"

    def test_the_email_pattern_masks_the_same_characters(self):
        """Die harte Zusage: dieselben Zeichenpositionen, Eingabe fuer Eingabe.

        Zeichenpositionen statt Ausgabetext, weil genau das die Zusage des
        Tickets ist — was vorher maskiert war, bleibt maskiert, und es
        kommt nichts hinzu. Die Zahl der Platzhalter ist demgegenueber
        Darstellung.
        """
        vorher = _email_pattern_before_the_fix()
        jetzt = _email_pattern_now()
        abweichungen = [
            (text, sorted(_masked_positions(vorher, text) ^ _masked_positions(jetzt, text)))
            for text in ALL_INPUTS
            if _masked_positions(vorher, text) != _masked_positions(jetzt, text)
        ]
        assert not abweichungen, f"{len(abweichungen)} Abweichung(en), erste: {abweichungen[0]!r}"

    def test_pattern_scrub_is_equivalent(self):
        """``scrub`` liefert dasselbe wie vor dem Fix — bis auf die Zahl der
        direkt aneinandergrenzenden Platzhalter (siehe Modul-Docstring)."""
        abweichungen = [
            (text, _scrub_before_the_fix(text), scrub(text))
            for text in ALL_INPUTS
            if _collapsed(_scrub_before_the_fix(text)) != _collapsed(scrub(text))
        ]
        assert not abweichungen, f"{len(abweichungen)} Abweichung(en), erste: {abweichungen[0]!r}"

    def test_the_known_difference_is_only_the_placeholder_count(self):
        """Die bekannte Abweichung ist vorhanden, benannt und harmlos.

        Ein Test, der die Abweichung nur wegkuerzt, verdeckt sie. Hier
        steht, WELCHE Eingaben betroffen sind und dass der Unterschied
        ausschliesslich in mehrfachen Platzhaltern besteht — nie in
        wieder sichtbarem Klartext.
        """
        betroffen = [text for text in ALL_INPUTS if _scrub_before_the_fix(text) != scrub(text)]
        assert betroffen, "Die Stichprobe muss den bekannten Fall enthalten, sonst belegt sie ihn nicht"
        for text in betroffen:
            alt, neu = _scrub_before_the_fix(text), scrub(text)
            assert "<email><email>" in alt, f"unerwartete Abweichungsklasse: {text!r} -> {alt!r} / {neu!r}"
            assert _collapsed(alt) == neu, f"mehr als die Platzhalterzahl geaendert: {text!r}"
            assert neu.count("<email>") < alt.count("<email>")

    def test_sink_scrub_is_equivalent(self, monkeypatch):
        """Dasselbe fuer den vollstaendigen Sink-Scrub (Muster + Pfade).

        ``scrub_text`` ist der Weg, den beide Formatter und der
        Tracker-Hook gehen; die Musterliste wird dafuer auf den Stand vor
        dem Fix gesetzt.
        """
        neu = [scrub_text(text) for text in ALL_INPUTS]
        monkeypatch.setattr(core_logging, "_SCRUB_PATTERNS", _patterns_before_the_fix())
        alt = [scrub_text(text) for text in ALL_INPUTS]
        monkeypatch.undo()
        abweichungen = [
            (text, a, n) for text, a, n in zip(ALL_INPUTS, alt, neu, strict=True) if _collapsed(a) != _collapsed(n)
        ]
        assert not abweichungen, f"{len(abweichungen)} Abweichung(en), erste: {abweichungen[0]!r}"

    def test_scrub_stays_idempotent(self):
        """Zweimal scrubben aendert nichts mehr (Zusage aus ``test_logging``)."""
        for text in ALL_INPUTS:
            einmal = scrub(text)
            assert scrub(einmal) == einmal, f"nicht idempotent: {text!r}"


# --- Laufzeit ---------------------------------------------------------------


def _payload(art, laenge):
    """Zeichenkette OHNE Leerzeichen — der Fall aus dem Ticket."""
    if art == "wortlauf":
        return "x" * laenge
    if art == "mit-at":
        return "abc@" * (laenge // 4)
    if art == "mit-gleich-doppelpunkt":
        return "a=1:b=2:" * (laenge // 8)
    return "nutzer.name+tag@example.org." * (laenge // 28)


PAYLOAD_KINDS = ("wortlauf", "mit-at", "mit-gleich-doppelpunkt", "mit-adressen")


def _seconds_per_call(funktion, text, laeufe=5):
    """Best-of-*laeufe* Sekunden je Aufruf, auf gleichem Zeichenvolumen.

    Wiederholt wird so oft, dass jede Messung dieselbe Zeichenmenge
    verarbeitet: die Messwerte fuer kurze und lange Eingaben liegen damit
    in derselben Groessenordnung, und die Timer-Aufloesung faellt aus dem
    Verhaeltnis heraus.
    """
    wiederholungen = max(1, _CHARS_PER_MEASUREMENT // len(text))
    bestzeit = float("inf")
    for _ in range(laeufe):
        start = time.perf_counter()
        for _ in range(wiederholungen):
            funktion(text)
        bestzeit = min(bestzeit, time.perf_counter() - start)
    return bestzeit / wiederholungen


def _growth(funktion, art):
    """Laufzeitverhaeltnis 16 000 zu 2 000 Zeichen."""
    klein = _seconds_per_call(funktion, _payload(art, _SMALL))
    gross = _seconds_per_call(funktion, _payload(art, _LARGE))
    return gross / klein


@pytest.mark.parametrize("art", PAYLOAD_KINDS)
def test_scrub_runtime_grows_linearly(art):
    """Der vollstaendige Muster-Scrub waechst hoechstens linear.

    Der Laengenfaktor ist 8; die Schranke laesst das Zweieinhalbfache zu.
    """
    faktor = _growth(scrub, art)
    assert faktor < _LINEAR_BOUND, f"{art}: Laufzeit waechst um Faktor {faktor:.1f} statt hoechstens {_LINEAR_BOUND}"


@pytest.mark.parametrize("art", ("wortlauf", "mit-at"))
@pytest.mark.parametrize("index", range(len(_SCRUB_PATTERNS)))
def test_every_single_pattern_grows_linearly(index, art):
    """Auch jedes EINZELNE Muster — die Fehlerklasse darf nicht zurueckkommen.

    Ein neues Muster ohne linken Anker faellt hier auf, bevor es in der
    Summe untergeht.
    """
    pattern, repl = _SCRUB_PATTERNS[index]
    faktor = _growth(lambda text: pattern.sub(repl, text), art)
    assert faktor < _LINEAR_BOUND, f"Muster {index} ({pattern.pattern!r}) waechst bei '{art}' um Faktor {faktor:.1f}"

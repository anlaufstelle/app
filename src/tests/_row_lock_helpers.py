"""Zeilensperren und Lesungen am ausgefuehrten SQL beobachten — nach ihrer Bedeutung, nicht nach dem Wortlaut.

Ein :class:`Mitschnitt` (``connection.execute_wrapper``) haelt jede
ausgefuehrte Anweisung samt Parametern fest. Ausgewertet wird sie von
PostgreSQL selbst: ``EXPLAIN (VERBOSE, FORMAT JSON)`` mit denselben Parametern
plant die Anweisung, ohne sie auszufuehren. Der Plan nennt

* die gelesenen Tabellen ueber ihren echten Namen (``Relation Name``) — gleich,
  ob die Anweisung einen Tabellenalias setzt oder wie sie Bezeichner quotet;
* eine Zeilensperre als ``LockRows``-Knoten ueber genau diesen Tabellen (bei
  ``FOR ... OF`` nur ueber den dort genannten);
* die Filterbedingungen in normalisierter, voll qualifizierter Form: ``= x``
  und ``IN (x)`` gelten gleich, Reihenfolge der Bedingungen, Projektion,
  Sortierung, ``LIMIT`` und weitere Klauseln spielen keine Rolle.

Aus dem Text der Anweisung wird nur die Sperrstaerke gelesen (``FOR UPDATE``,
``FOR NO KEY UPDATE``, ``FOR SHARE``, ``FOR KEY SHARE``) — die weist der Plan
nicht aus. Ausgewertet werden nur lesende Anweisungen (``SELECT``/``WITH``).

:meth:`Mitschnitt.nach_sperre` stellt Wettlaeufe deterministisch nach: die
Aktion laeuft einmal, direkt nachdem die erste Anweisung mit einer Zeilensperre
auf der genannten Tabelle ausgefuehrt ist. Sie haengt damit an der beobachteten
Sperre, nicht am Python-Aufrufpfad, der sie nimmt; ihre eigenen Abfragen und
die Auswertung selbst landen nicht im Mitschnitt.
"""

import json
import re
import uuid
from contextlib import contextmanager

from django.db import connection, transaction

_BEZ = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
_KONST = r"(?:'((?:[^']|'')*)'|(-?\d+(?:\.\d+)?|true|false))(?:::[A-Za-z_][\w ]*(?:\[\])?)?"
_SPALTE = rf"\(?({_BEZ})\.({_BEZ})\)?(?:::[A-Za-z_][\w ]*)?"
_SPALTE_GLEICH_KONST = re.compile(rf"\({_SPALTE} = {_KONST}\)")
_KONST_GLEICH_SPALTE = re.compile(rf"\({_KONST} = {_SPALTE}\)")
_SPALTE_GLEICH_ANY = re.compile(rf"\({_SPALTE} = ANY \({_KONST}\)\)")
_SPERRKLAUSEL = re.compile(
    rf"\bFOR\s+(NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b(?:\s+OF\s+({_BEZ}(?:\s*,\s*{_BEZ})*))?",
    re.IGNORECASE,
)
_LESEND = re.compile(r"^\s*\(*\s*(?:SELECT|WITH)\b", re.IGNORECASE)
_BEDINGUNGSFELDER = ("Index Cond", "Recheck Cond", "Filter", "Join Filter", "Hash Cond", "Merge Cond", "TID Cond")
_UNTERPLAENE = ("SubPlan", "InitPlan")


def _wert(wert):
    """UUID-Werte normalisiert (Objekt, Text mit oder ohne Bindestriche), sonst ``str``."""
    try:
        return uuid.UUID(str(wert)).hex
    except ValueError:
        return str(wert)


def _name(bezeichner):
    bezeichner = bezeichner.strip()
    if bezeichner.startswith('"'):
        return bezeichner[1:-1].replace('""', '"')
    return bezeichner.lower()


def _konstante(text, roh):
    return text.replace("''", "'") if text is not None else roh


def _array_werte(literal):
    return [teil.strip().strip('"') for teil in literal.strip("{}").split(",") if teil.strip()]


def _knoten(knoten, unter_sperre=False):
    """Alle Planknoten mit der Angabe, ob ein ``LockRows`` ueber ihnen ihre Zeilen sperrt."""
    yield knoten, unter_sperre
    sperrt = unter_sperre or knoten.get("Node Type") == "LockRows"
    for kind in knoten.get("Plans", ()):
        yield from _knoten(kind, sperrt and kind.get("Parent Relationship") not in _UNTERPLAENE)


class Abfrage:
    """Eine ausgefuehrte Anweisung, von PostgreSQL geplant (erst bei Bedarf)."""

    def __init__(self, mitschnitt, sql, params):
        self.sql = sql
        self.params = params
        self._mitschnitt = mitschnitt
        self._ausgewertet = False
        self.tabellen = set()
        self.gesperrt = {}
        self.bedingungen = {}

    def _auswerten(self):
        if self._ausgewertet:
            return
        self._ausgewertet = True
        if not _LESEND.match(self.sql):
            return
        plan = self._mitschnitt._erklaeren(self.sql, self.params)[0]["Plan"]
        aliase, unter_sperre, texte = {}, {}, []
        for knoten, gesperrt in _knoten(plan):
            if "Relation Name" in knoten:
                alias = knoten.get("Alias", knoten["Relation Name"])
                aliase[alias] = knoten["Relation Name"]
                unter_sperre[alias] = unter_sperre.get(alias, False) or gesperrt
            texte.extend(knoten[feld] for feld in _BEDINGUNGSFELDER if feld in knoten)
        self.tabellen = set(aliase.values())
        for klausel in _SPERRKLAUSEL.finditer(self.sql):
            staerke = " ".join(klausel.group(1).upper().split())
            ziele = [_name(b) for b in re.findall(_BEZ, klausel.group(2))] if klausel.group(2) else None
            for alias, tabelle in aliase.items():
                if unter_sperre[alias] and (ziele is None or alias in ziele or tabelle in ziele):
                    self.gesperrt.setdefault(tabelle, staerke)
        for text in texte:
            for q, s, t, r in _SPALTE_GLEICH_KONST.findall(text):
                self._bedingung(aliase, q, s, [_konstante(t or None, r)])
            for t, r, q, s in _KONST_GLEICH_SPALTE.findall(text):
                self._bedingung(aliase, q, s, [_konstante(t or None, r)])
            for q, s, t, r in _SPALTE_GLEICH_ANY.findall(text):
                self._bedingung(aliase, q, s, _array_werte(_konstante(t or None, r)))

    def _bedingung(self, aliase, qualifizierer, spalte, werte):
        tabelle = aliase.get(_name(qualifizierer))
        if tabelle is not None:
            self.bedingungen.setdefault(tabelle, {}).setdefault(_name(spalte), set()).update(map(_wert, werte))

    def liest(self, tabelle):
        self._auswerten()
        return tabelle in self.tabellen

    def sperrt(self, tabelle):
        self._auswerten()
        return tabelle in self.gesperrt


class Zugriff:
    """Eine Anweisung aus Sicht EINER Tabelle: Sperrart und Filterbedingungen auf ihr."""

    def __init__(self, abfrage, tabelle):
        self.abfrage = abfrage
        self.tabelle = tabelle

    @property
    def sperrart(self):
        return self.abfrage.gesperrt.get(self.tabelle)

    def traegt(self, **erwartet):
        """Bindet die Anweisung die Tabelle je Spalte an genau diese Wertemenge (``=`` oder ``IN``)?"""
        bedingungen = self.abfrage.bedingungen.get(self.tabelle, {})
        return all(bedingungen.get(spalte) == {_wert(w) for w in werte} for spalte, werte in erwartet.items())

    def __repr__(self):
        return f"Zugriff({self.tabelle}, sperrart={self.sperrart}, bedingungen={self.abfrage.bedingungen})"


class Mitschnitt(list):
    """Schneidet jede Anweisung samt Parametern mit (``connection.execute_wrapper``)."""

    def __init__(self):
        super().__init__()
        self._still = False
        self._schranken = []

    def __call__(self, execute, sql, params, many, context):
        if self._still:
            return execute(sql, params, many, context)
        ergebnis = execute(sql, params, many, context)
        if not many:
            abfrage = Abfrage(self, sql, params)
            self.append(abfrage)
            self._schranken_ausloesen(abfrage)
        return ergebnis

    @contextmanager
    def _leise(self):
        vorher, self._still = self._still, True
        try:
            yield
        finally:
            self._still = vorher

    def _erklaeren(self, sql, params):
        with self._leise(), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("EXPLAIN (VERBOSE, FORMAT JSON) " + sql, params)
            (plan,) = cursor.fetchone()
        return json.loads(plan) if isinstance(plan, str) else plan

    def _schranken_ausloesen(self, abfrage):
        for schranke in [s for s in self._schranken if abfrage.sperrt(s[0])]:
            self._schranken.remove(schranke)
            with self._leise():
                schranke[1]()

    def nach_sperre(self, tabelle, aktion):
        """``aktion`` laeuft einmal, direkt nach der ersten Zeilensperre auf ``tabelle``."""
        self._schranken.append((tabelle, aktion))

    def offene_schranken(self):
        """Tabellen, deren Sperre fuer eine ``nach_sperre``-Aktion nie beobachtet wurde."""
        return [tabelle for tabelle, _ in self._schranken]

    def sperren(self, tabelle):
        return [Zugriff(a, tabelle) for a in self if a.sperrt(tabelle)]

    def lesungen(self, tabelle):
        """Anweisungen, die ``tabelle`` lesen, ohne ihre Zeilen zu sperren."""
        return [Zugriff(a, tabelle) for a in self if a.liest(tabelle) and not a.sperrt(tabelle)]

    def sperrfolge(self):
        """Die gesperrten Tabellen in der Reihenfolge ihrer ersten Sperre."""
        folge = []
        for abfrage in self:
            abfrage._auswerten()
            folge.extend(t for t in sorted(abfrage.gesperrt) if t not in folge)
        return folge


@contextmanager
def mitschnitt():
    """Mitschnitt aller Anweisungen der Standardverbindung innerhalb des Blocks."""
    aufzeichnung = Mitschnitt()
    with connection.execute_wrapper(aufzeichnung):
        yield aufzeichnung

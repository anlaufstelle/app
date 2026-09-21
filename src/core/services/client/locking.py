"""Kanonische Sperr-Reihenfolge auf Personenzeilen (Refs #2268).

**Projektweite Regel:** mehrere Zeilen DERSELBEN Tabelle werden immer in
aufsteigender PK-Reihenfolge gesperrt. Fuer ``core_client`` laeuft das ueber
diesen einen Helfer — nicht, weil ein zweiter Aufruf teuer waere, sondern
weil jede zusaetzliche Sperr-Form eine eigene Reihenfolge mitbringt und
Reihenfolgen sich nur global vergleichen lassen.

Ohne die Regel sperren Pfade in ihrer *fachlichen* Reihenfolge: die
Fallzuordnung erst die Fallperson, dann die Ereignisperson; eine
Zusammenfuehrung erst die Quelle, dann das Ziel. Fuer zwei Personen A < B
ergibt Fall(B)/Ereignis(A) gegen ein Zusammenfuehren A->B einen Zyklus —
die eine Seite haelt B und verlangt A, die andere haelt A und verlangt B.
PostgreSQL loest ihn mit ``40P01`` auf: kein Datenverlust (die unterlegene
Transaktion faellt ganz zurueck), aber ein vermeidbarer Serverfehler unter
Last.

**Eine Abfrage statt zweier.** Zwei nacheinander abgesetzte
``select_for_update``-Statements lassen zwischen sich ein Fenster, in dem die
erste Zeile schon gehalten und die zweite noch frei ist — genau das Fenster,
in dem der Zyklus entsteht. Ein einziges Statement mit ``ORDER BY pk`` sperrt
die Zeilen in Sortierreihenfolge; die Ordnung ist damit die der Datenbank,
nicht die der Aufruferin.

Fachliche Pruefungen gehoeren NACH das Sperren. Eine Pruefung zwischen zwei
Sperren haelt die erste Zeile laenger, als noetig ist, und macht die
Reihenfolge von ihrem Ausgang abhaengig.
"""

from __future__ import annotations

from typing import Any

from core.models import Client


def client_pks(client_refs) -> list:
    """Die eindeutigen PKs aus Instanzen, PKs und ``None`` — aufsteigend.

    Sortiert wird ueber die Zeichenkette: UUIDs vergleichen sich als Text
    zeichenweise wie als Wert (die Trennstriche stehen an festen Stellen), und
    Aufrufer duerfen PK und Instanz mischen. ``None`` (anonymes Ereignis,
    Aufgabe ohne Personenbezug) ist kein Personenbezug und faellt heraus.
    """
    return sorted({getattr(ref, "pk", ref) for ref in client_refs if getattr(ref, "pk", ref) is not None}, key=str)


def lock_clients(client_refs, *, facility=None) -> dict[Any, Client]:
    """Sperrt Personenzeilen in EINER Abfrage, aufsteigend nach PK.

    Idempotent: Dubletten und ``None`` fallen heraus, eine leere Menge kostet
    keine Abfrage (das Gate einer personenlosen Aufgabe bleibt gratis).
    Zurueck kommen die frisch gelesenen Zeilen nach PK — moeglicherweise stale
    Instanzen der Aufruferin sind damit ersetzt. Fehlende PKs fehlen im
    Ergebnis; ob das ein Fehler ist, entscheidet die Aufruferin.

    ``facility`` scopt die Sperre zusaetzlich auf eine Einrichtung. Es ist ein
    Angebot, keine Pflicht: die Zeilen sind ohnehin RLS-gescopt (ADR-037), und
    die drei Personenpfade pruefen die Einrichtung mit eigenen, fachlichen
    Meldungen — ein Scope davor wuerde sie in ein „nicht gefunden" verwandeln.
    """
    pks = client_pks(client_refs)
    if not pks:
        return {}
    rows = Client.objects.for_facility(facility) if facility is not None else Client.objects.all()
    return {row.pk: row for row in rows.filter(pk__in=pks).select_for_update().order_by("pk")}

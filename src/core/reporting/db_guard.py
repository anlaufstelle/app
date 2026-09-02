"""Transaktionslokaler Query-Deckel fuer Backend-Aufrufe (Refs #2070).

Ein Reporting-Backend fuehrt beliebige, backend-eigene Queries aus
(``build_dataset``). Ohne Obergrenze haelt eine entgleiste Query einen
Gunicorn-Worker so lange, wie die Datenbank braucht — auf der Statistik-Seite,
im Export **und** im monatlichen Snapshot-Lauf, der ueber alle Einrichtungen
iteriert. Der Deckel begrenzt den Schaden auf genau einen Backend-Aufruf; der
daraus entstehende ``QueryCanceled`` erreicht die Aufrufer als
``django.db.utils.OperationalError`` und wird dort wie **jeder andere**
Backend-Fehler isoliert (Fehlerkarte / HTTP 502 / uebersprungenes
Snapshot-Backend) — kein Sonderfall.

**Warum ``SET LOCAL`` und nicht ``SET``** (Refs #733, ``CONN_MAX_AGE = 60`` in
``settings/prod.py``): Django reicht Connections zwischen Requests weiter. Ein
session-weit gesetzter Wert bliebe auf der wiederverwendeten Connection stehen
und traefe spaetere, unbeteiligte Requests. Die RLS-GUCs der
``FacilityScopeMiddleware`` sind bewusst session-weit (``is_local=false``) —
dieser Deckel ist bewusst das Gegenteil und kollidiert deshalb nicht mit ihnen.

**Warum ``set_config(..., true)`` und nicht ``SET LOCAL statement_timeout =
%s``:** PostgreSQL erlaubt in ``SET`` keine Bind-Parameter; nur die
Funktionsform nimmt den Wert parametrisiert entgegen. Sie ist exakt
aequivalent (drittes Argument ``is_local``) und vermeidet String-Interpolation
in SQL. Dasselbe Muster wie
:func:`core.services.system._db_admin.rls_bypass_for_read`.

**Warum trotz Transaktionslokalitaet ein ``finally``-Restore:** ``SET LOCAL``
endet mit der **Transaktion**, nicht mit dem Savepoint. Unter
``pytest.mark.django_db`` (und generell in jedem umschliessenden
``atomic()``-Block) wird ``transaction.atomic()`` zu einem SAVEPOINT — der
Wert bliebe nach dem Verlassen des Blocks bis zum Ende der aeusseren
Transaktion stehen. Der Restore macht den Kontextmanager deshalb in beiden
Faellen sauber.
"""

import logging
from contextlib import contextmanager

from django.conf import settings
from django.db import connection, transaction

logger = logging.getLogger(__name__)


@contextmanager
def reporting_statement_timeout(timeout_ms: int | None = None):
    """Fuehrt den Block mit ``statement_timeout`` aus ``settings`` aus.

    ``timeout_ms`` uebersteuert das Setting
    ``REPORTING_BACKEND_STATEMENT_TIMEOUT_MS`` (Default 10000 ms). Ein Wert
    ``<= 0`` schaltet den Deckel ab (PostgreSQL-Semantik von ``0``), ohne dass
    der Aufrufer eine Fallunterscheidung braucht.

    No-op auf Nicht-PostgreSQL-Backends (SQLite), damit derselbe Aufruf in
    jeder Umgebung steht.
    """
    if connection.vendor != "postgresql":
        yield
        return

    millis = settings.REPORTING_BACKEND_STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms

    with connection.cursor() as cur:
        cur.execute("SHOW statement_timeout")
        previous = cur.fetchone()[0]

    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT set_config('statement_timeout', %s, true)", [f"{max(int(millis), 0)}ms"])
        yield
        # Restore NUR auf dem Erfolgspfad — bewusst kein ``finally``: schlaegt
        # der Block mit einem Datenbankfehler fehl (genau der ``QueryCanceled``,
        # den dieser Deckel ausloest), ist die Transaktion abgebrochen und jedes
        # weitere ``execute`` wuerde mit ``InFailedSqlTransaction`` den
        # eigentlichen Fehler ueberschreiben. Auf dem Fehlerpfad raeumt
        # PostgreSQL selbst auf: ``ROLLBACK``/``ROLLBACK TO SAVEPOINT`` macht
        # ``SET LOCAL`` rueckgaengig.
        cur.execute("SELECT set_config('statement_timeout', %s, true)", [previous])

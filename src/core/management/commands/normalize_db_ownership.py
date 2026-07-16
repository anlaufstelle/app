"""Management command: Tabellen-Ownership auf den Datenbank-Owner normalisieren.

Refs #1085: Der Deploy-Migrate-Job (``docker-migrate.sh``) connectet als
BYPASSRLS-**Admin**-Rolle (``POSTGRES_USER``-Override, Refs #863) — damit
RunPython-Datenmigrationen in RLS-geschützte Tabellen schreiben können.

``REASSIGN OWNED BY current_user TO <db_owner>`` überträgt sämtliche vom
aktuellen DB-User besessenen Objekte (Tabellen, Sequenzen, Views, …) auf den
**Datenbank-Owner** (``pg_database.datdba``, env-agnostisch ermittelt).

Refs #1026 (A1.4): Seit der Runtime-/Owner-Rollentrennung ist der DB-Owner die
**Admin-Rolle** selbst (``ALTER DATABASE OWNER TO admin`` in
``deploy/postgres-init/01-app-role.sh``). Da ``migrate`` als eben diese Rolle
läuft, ist ``current_user == db_owner`` und das Command ein **No-op** — die
App-Runtime bekommt ihre Tabellen-Rechte per DML-Grant
(``ALTER DEFAULT PRIVILEGES … TO app``), nicht mehr über Ownership. Das Command
bleibt als idempotentes Sicherheitsnetz: liefe ``migrate`` versehentlich als
eine Nicht-Owner-Rolle, würde es die entstandenen Objekte wieder dem DB-Owner
zuweisen (generalisiert das per-Tabelle-Muster aus ``0093_cache_table_owner.py``,
Refs #1030). In dev/test/e2e läuft ``migrate`` ohnehin als DB-Owner — ebenfalls
No-op. Mehrfachläufe sind unschädlich.

Wird in ``docker-migrate.sh`` direkt nach erfolgreichem ``migrate`` aufgerufen
(analog zum ``check_db_roles``-Gate, Refs #1002).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import connection


@dataclass(frozen=True)
class OwnershipResult:
    """Ergebnis eines Normalisierungslaufs."""

    current_user: str
    db_owner: str
    reassigned: bool


# REASSIGN OWNED env-agnostisch — der DO-Block ermittelt den DB-Owner selbst
# und quotet Rollennamen via ``%I`` (Muster aus 0093, Refs #1030). Rollennamen
# werden nie aus Python interpoliert. Der Guard macht den Block für sich
# idempotent/No-op-fest, auch wenn er als Owner läuft.
_REASSIGN_SQL = """
DO $$
DECLARE
    db_owner text;
BEGIN
    SELECT pg_catalog.pg_get_userbyid(datdba) INTO db_owner
        FROM pg_catalog.pg_database
        WHERE datname = current_database();
    IF db_owner <> current_user THEN
        EXECUTE format('REASSIGN OWNED BY %I TO %I', current_user, db_owner);
    END IF;
END
$$;
"""


def normalize_db_ownership() -> OwnershipResult:
    """Weist alle vom aktuellen DB-User besessenen Objekte dem DB-Owner zu."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_user")
        current_user = cur.fetchone()[0]
        cur.execute(
            "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname = current_database()"
        )
        db_owner = cur.fetchone()[0]
        if current_user == db_owner:
            return OwnershipResult(current_user=current_user, db_owner=db_owner, reassigned=False)
        cur.execute(_REASSIGN_SQL)
    return OwnershipResult(current_user=current_user, db_owner=db_owner, reassigned=True)


class Command(BaseCommand):
    help = "Weist vom aktuellen DB-User besessene Objekte dem DB-Owner (App-Rolle) zu (Refs #1085)."

    def handle(self, *args, **options):
        result = normalize_db_ownership()
        if result.reassigned:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Ownership normalisiert: Objekte von {result.current_user!r} -> DB-Owner {result.db_owner!r}."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"Kein Reassign nötig: {result.current_user!r} ist bereits DB-Owner."))

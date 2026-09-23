"""Pins fuer die Reihenfolge der FK-Vorsperre (``core.services.fk_locks``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 1 Mutant.
Gruppe:

* ``fk_lock_targets`` — der PK-Zweitschluessel der Sortierung entfaellt.

Der Vertrag lautet „Tabelle, dann PK“, damit zwei Pfade mit ueberlappenden
Zielmengen ihre Sperren nie gegenlaeufig nehmen. Heute traegt jede Tabelle
hoechstens einen Fremdschluessel je Modell; der Pin baut deshalb ein Modell
mit drei Fremdschluesseln in DIESELBE Tabelle. Die Ziele sammelt die Funktion
in einer Menge — deren Iterationsreihenfolge wird hier auf die
Gegenreihenfolge gezwungen, damit ein fehlender Zweitschluessel sicher
auffaellt und nicht nur mit der Wahrscheinlichkeit der Hash-Reihenfolge.
"""

import uuid

from django.db import models

from core.models import Organization
from core.services import fk_locks

_PKS = [
    uuid.UUID("00000000-0000-4000-8000-000000000001"),
    uuid.UUID("00000000-0000-4000-8000-000000000002"),
    uuid.UUID("00000000-0000-4000-8000-000000000003"),
]


def _fk(name):
    feld = models.ForeignKey(Organization, on_delete=models.CASCADE, null=True, related_name="+")
    feld.set_attributes_from_name(name)
    feld.model = Organization
    return feld


class _DreiFksInEineTabelle:
    class _meta:  # noqa: N801 — spiegelt ``Model._meta``
        felder = (_fk("erste"), _fk("zweite"), _fk("dritte"))

        @classmethod
        def get_fields(cls, include_parents=True, include_hidden=False):
            return cls.felder


class _Gegenmenge(set):
    """Eine Menge, die in absteigender PK-Reihenfolge iteriert."""

    def __iter__(self):
        return iter(sorted(super().__iter__(), key=lambda entry: str(entry[2]), reverse=True))


class TestFkLockTargets:
    """``fk_lock_targets``: sortiert nach Tabelle, dann PK."""

    def test_ziele_derselben_tabelle_in_pk_reihenfolge(self, monkeypatch):
        """x_fk_lock_targets__mutmut_19: ohne PK im Sortierschluessel blieben
        die Ziele derselben Tabelle in der Iterationsreihenfolge der Menge —
        hier der erzwungenen Gegenreihenfolge."""
        monkeypatch.setattr(fk_locks, "set", _Gegenmenge, raising=False)
        werte = {"erste_id": _PKS[2], "zweite_id": _PKS[0], "dritte_id": _PKS[1]}
        assert fk_locks.fk_lock_targets(_DreiFksInEineTabelle, werte) == [
            ("core_organization", "id", _PKS[0]),
            ("core_organization", "id", _PKS[1]),
            ("core_organization", "id", _PKS[2]),
        ]

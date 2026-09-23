"""Pins fuer die Sortierung der Schnellvorlagen (``core.services.dashboard.quick_templates``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 1 Mutant.
Gruppe:

* ``list_templates_for_user`` — der Namens-Zweitschluessel der Sortierung
  entfaellt.

Ein ausdrueckliches ``order_by`` ersetzt ``Meta.ordering`` vollstaendig; ohne
``name`` waere die Reihenfolge bei gleichem ``sort_order`` der Datenbank
ueberlassen. Damit das nicht vom Zufall der Datenbank abhaengt, zwingt der
Pin die Gegenreihenfolge: fehlt der Name unter den Sortierschluesseln der
Abfrage, haengt ein Abfrage-Wrapper ``name DESC`` an — genau die Reihenfolge,
die eine Datenbank ohne Zweitschluessel liefern duerfte. Abfragen, die selbst
nach dem Namen sortieren, laesst der Wrapper unveraendert; eine Sortierung in
Python beruehrt er nie. Gepinnt ist allein das Ergebnis: gleichwertige
Umsetzungen (``name`` als Zweitschluessel mit weiteren Schluesseln dahinter,
Namenssortierung in der Datenbank plus stabile Python-Sortierung nach
``sort_order``, vollstaendige Python-Sortierung) bleiben gruen.
"""

import re

import pytest
from django.db import connection

from core.models import QuickTemplate
from core.services.dashboard.quick_templates import list_templates_for_user

pytestmark = pytest.mark.django_db

_ORDER_BY = re.compile(r"\bORDER\s+BY\s+(?P<schluessel>.+?)(?=\s+(?:LIMIT|OFFSET|FOR)\b|$)", re.IGNORECASE | re.DOTALL)
_NAME = '"core_quicktemplate"."name"'


def _schluessel(sql):
    """Die Sortierschluessel als ``[(ausdruck, richtung), ...]``."""
    treffer = _ORDER_BY.search(sql)
    if not treffer:
        return []
    ergebnis = []
    for teil in treffer.group("schluessel").split(","):
        woerter = teil.strip().rsplit(None, 1)
        if len(woerter) == 2 and woerter[1].upper() in ("ASC", "DESC"):
            ergebnis.append((woerter[0], woerter[1].upper()))
        else:
            ergebnis.append((teil.strip(), "ASC"))
    return ergebnis


class _Gegenreihenfolge(list):
    """Haengt ``name DESC`` an, wenn die Vorlagen-Abfrage den Namen nicht sortiert; merkt sich das SQL.

    Das gemerkte SQL dient nur dem Nachweis, dass der Wrapper aktiv war — an
    seine Sortierschluessel bindet sich der Pin nicht.
    """

    def __call__(self, execute, sql, params, many, context):
        if 'FROM "core_quicktemplate"' in sql:
            self.append(sql)
            if not any("name" in ausdruck for ausdruck, _ in _schluessel(sql)):
                treffer = _ORDER_BY.search(sql)
                if treffer:
                    ende = treffer.end("schluessel")
                    sql = f"{sql[:ende]}, {_NAME} DESC{sql[ende:]}"
                else:
                    sql = f"{sql} ORDER BY {_NAME} DESC"
        return execute(sql, params, many, context)


class TestListTemplatesForUser:
    """``list_templates_for_user``: ``sort_order``, dann ``name``."""

    def test_gleicher_rang_sortiert_nach_name(self, facility, doc_type_contact, staff_user):
        """x_list_templates_for_user__mutmut_17: ohne ``name`` im ``order_by``
        kaeme bei gleichem ``sort_order`` die erzwungene Gegenreihenfolge
        (Zeta vor Alpha) zurueck."""
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Zeta", sort_order=1)
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Alpha", sort_order=1)
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Omega", sort_order=0)
        gegen = _Gegenreihenfolge()
        with connection.execute_wrapper(gegen):
            namen = [t.name for t in list_templates_for_user(staff_user, facility)]
        assert gegen, "die Vorlagen-Abfrage lief nicht durch den Wrapper"
        assert namen == ["Omega", "Alpha", "Zeta"]

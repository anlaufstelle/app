"""Pins fuer die Sortierung der Schnellvorlagen (``core.services.dashboard.quick_templates``).

Quelle: Mutations-Delta des Checkpoints release-0.99.3, 1 Mutant.
Gruppe:

* ``list_templates_for_user`` — der Namens-Zweitschluessel der Sortierung
  entfaellt.

Ein ausdrueckliches ``order_by`` ersetzt ``Meta.ordering`` vollstaendig; ohne
``name`` waere die Reihenfolge bei gleichem ``sort_order`` der Datenbank
ueberlassen. Der Pin haelt die ``ORDER BY``-Klausel der Abfrage fest und legt
die Vorlagen zusaetzlich in Gegenreihenfolge an.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import QuickTemplate
from core.services.dashboard.quick_templates import list_templates_for_user

pytestmark = pytest.mark.django_db


class TestListTemplatesForUser:
    """``list_templates_for_user``: ``sort_order``, dann ``name``."""

    def test_gleicher_rang_sortiert_nach_name(self, facility, doc_type_contact, staff_user):
        """x_list_templates_for_user__mutmut_17: ohne ``name`` im ``order_by``
        steht der Namens-Zweitschluessel nicht mehr in der Abfrage."""
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Zeta", sort_order=1)
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Alpha", sort_order=1)
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_contact, name="Omega", sort_order=0)
        with CaptureQueriesContext(connection) as ctx:
            namen = [t.name for t in list_templates_for_user(staff_user, facility)]
        assert namen == ["Omega", "Alpha", "Zeta"]
        (abfrage,) = [q["sql"] for q in ctx.captured_queries if 'FROM "core_quicktemplate"' in q["sql"]]
        assert abfrage.endswith('ORDER BY "core_quicktemplate"."sort_order" ASC, "core_quicktemplate"."name" ASC')

"""Pin-Tests der Welle K (0.98) — Prio-2-Testluecken in ``core.services.client.export``.

Refs #2193.

Quelle: Batch ``export-settings`` der Survivor-Triage (``triage-096-merge-2-
misc.json``). Gepinnt werden zwei Sortier-Mutanten der Art.-15/20-Auskunft —
beide ohne Datenschutz-Bezug (der Inhalt bleibt vollstaendig, nur die
Lesbarkeit/Determinismus der Reihenfolge leidet):

* **``_gather_workitems``**: ``order_by("-created_at", "pk")`` ->
  ``order_by("pk")``. Die Aufgabenliste der Auskunft steht nicht mehr
  chronologisch (neueste zuerst), sondern nach UUID4-Primary-Key — faktisch
  zufaellig.
* **``_ordered_attachments``**: der ``"pk"``-Tiebreak faellt aus der
  Sortierkette. Der Docstring der Funktion macht die Reihenfolge zur
  Zusicherung (Einzel-Abfrage und Sammel-Index muessen dieselbe Folge
  liefern) — ohne den PK-Tiebreak ist die Folge bei Gleichstand (gleiches
  Feld, gleicher ``sort_order``, gleicher ``created_at``) undefiniert.

**Warum ``_ordered_attachments`` hier ueber die Sortierkette der Query
gepinnt wird, nicht ueber eine Datensatz-Assertion.** Der bestehende Pin
``test_mutation_pins_094_export_3.py::TestOrderedAttachmentsOrdering::
test_pk_is_the_stable_tiebreak`` (NICHT angefasst) verlaesst sich darauf,
dass Postgres vier gleichrangige Zeilen ohne entscheidendes ORDER-BY-Kriterium
in Einfuegereihenfolge zurueckgibt — eine Planner-Abhaengigkeit, die laut
Survivor-Triage in 0.96 nicht mehr haelt und den Mutanten dort nicht mehr
toetet. Diese Datei prueft stattdessen direkt und plan-unabhaengig, dass
``"pk"`` das letzte Kriterium der ``order_by``-Kette ist.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat keine
``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Attachment, Client, WorkItem
from core.services.client import export_client_data
from core.services.client.export import _ordered_attachments

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _gather_workitems — chronologische Sortierung (neueste zuerst)
# ---------------------------------------------------------------------------


class TestGatherWorkitemsOrdering:
    def test_workitems_are_listed_newest_first(self, facility, admin_user):
        """Mutante ``x__gather_workitems__mutmut_4``
        (``order_by("-created_at", "pk")`` -> ``order_by("pk")``).

        Drei Aufgaben mit gestaffeltem ``created_at`` (per ``.update()``, weil
        ``auto_now_add`` das Feld beim Anlegen ueberschreibt). Original:
        neueste zuerst. Mutant: UUID4-Primary-Key-Ordnung — faktisch
        zufaellig, hier mit an Anlage-Reihenfolge orientierten PKs praktisch
        sicher abweichend von der chronologischen Folge.
        """
        client = Client.objects.create(facility=facility, pseudonym="Export-WI", created_by=admin_user)
        aeltestes = WorkItem.objects.create(facility=facility, client=client, created_by=admin_user, title="Aeltestes")
        mittleres = WorkItem.objects.create(facility=facility, client=client, created_by=admin_user, title="Mittleres")
        neuestes = WorkItem.objects.create(facility=facility, client=client, created_by=admin_user, title="Neuestes")

        basis = timezone.now() - timedelta(days=10)
        WorkItem.objects.filter(pk=aeltestes.pk).update(created_at=basis)
        WorkItem.objects.filter(pk=mittleres.pk).update(created_at=basis + timedelta(days=1))
        WorkItem.objects.filter(pk=neuestes.pk).update(created_at=basis + timedelta(days=2))

        data = export_client_data(client, facility, admin_user)

        assert [wi["title"] for wi in data["work_items"]] == ["Neuestes", "Mittleres", "Aeltestes"]


# ---------------------------------------------------------------------------
# _ordered_attachments — der PK-Tiebreak in der Sortierkette
# ---------------------------------------------------------------------------


class TestOrderedAttachmentsOrderByClause:
    def test_order_by_ends_with_the_pk_tiebreak(self):
        """Mutante ``x__ordered_attachments__mutmut_8`` (``"pk"`` als viertes
        Kriterium aus ``order_by`` entfernt).

        Plan-unabhaengiger Nachweis direkt auf der Sortierkette der Query
        (siehe Modul-Docstring): ``query.order_by`` enthaelt exakt die vier
        Kriterien in der aufgerufenen Reihenfolge, unabhaengig davon, wie
        Postgres gleichrangige Zeilen ohne entscheidendes Kriterium tatsaechlich
        zurueckgeben wuerde.
        """
        geordnet = _ordered_attachments(Attachment.objects.all())

        assert geordnet.query.order_by == ("field_template__name", "sort_order", "created_at", "pk")

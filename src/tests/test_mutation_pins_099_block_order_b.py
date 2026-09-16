"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Vollmutation-Survivor-
Triage in ``core.services.config.block_order``, Batch ``block_order-b``
(deckt ausschliesslich ``iter_blocks_in_order`` ab; Batch ``block_order-a``
deckt andere Funktionen derselben Datei in einer eigenen Pin-Datei/Worktree
ab — keine Ueberschneidung).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-block_order-b.json`` (Triage
``triage-099-block_order-b.json``).

Beide Survivor sitzen in ``iter_blocks_in_order`` und betreffen je einen
Halbsatz, der laut Docstring der Funktion einen eigenen Vertrag traegt:

* ``x_iter_blocks_in_order__mutmut_6`` — ``.select_related("field_template")``
  wird zu ``.select_related(None)``. Der JOIN-Verlust ist am RUECKGABEWERT
  nicht sichtbar (gleiche Objekte, gleiche Reihenfolge), aber an der
  ausgefuehrten Query-Zahl: ``_block_sort_key`` liest ``dtf.field_template``
  fuer jeden freien Block beim Sortieren — ohne Prefetch je ein zusaetzliches
  SELECT (N+1). Pin: ``CaptureQueriesContext`` um den Aufruf, exakte
  Query-Zahl 2 (Original) vs. 4 (Mutant) bei zwei ungruppierten Feldern.

* ``x_iter_blocks_in_order__mutmut_18`` — der innere Tie-Break
  ``members.sort(key=lambda d: (d.sort_order, str(d.pk)))`` wird zu
  ``str(None)``. Bei Positions-Gleichstand INNERHALB einer Gruppe (laut
  Docstring nur bei Alt-Daten/Races relevant) macht das den Sort zum
  No-Op — die Ausgabe folgt dann der physischen Zeilenlage statt einer
  deterministischen pk-Reihenfolge. Pin nach dem Muster von
  ``test_mutation_pins_094_export_3.py``: zwei Gruppenmitglieder mit
  identischem Innen-``sort_order`` bekommen explizite UUID-PKs
  (``uuid.UUID(int=n)``), angelegt in der Reihenfolge pk_hoch VOR
  pk_niedrig — Postgres liefert bei einem Seq-Scan-Gleichstand ueber zwei
  Tupel die stabile Einfuegereihenfolge (empirisch verifiziert, Grundlage
  des Tiebreak-Pins). Original sortiert deterministisch pk-aufsteigend
  (pk_niedrig, pk_hoch); der Mutant belaesst die Einfuegereihenfolge
  (pk_hoch, pk_niedrig) — beide Reihenfolgen unterscheiden sich und machen
  den Mutanten beobachtbar.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate
from core.services.config import iter_blocks_in_order

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _pk(n: int) -> uuid.UUID:
    """Deterministische UUID mit bekannter Sortierlage (``_pk(1) < _pk(2)``).

    Zufaellige PKs (``uuid.uuid4``) machen jede Aussage ueber „nach pk
    sortiert" unpruefbar; ``id`` ist bei ``DocumentTypeField`` aber
    schreibbar (Muster ``test_mutation_pins_094_export_3.py``).
    """
    return uuid.UUID(int=n)


def _ft(facility, name: str) -> FieldTemplate:
    return FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.TEXT)


class TestIterBlocksInOrderSelectRelated:
    """Pinnt ``x_iter_blocks_in_order__mutmut_6`` (verlorener select_related-Prefetch)."""

    def test_free_fields_are_prefetched_without_n_plus_one_field_template_queries(self, facility):
        """Zwei ungruppierte Felder, kein Gruppen-Block.

        Original: 1 Query fuer ``FieldGroup`` (leer) + 1 Query fuer
        ``DocumentTypeField`` MIT ``select_related("field_template")`` — der
        spaetere Zugriff auf ``dtf.field_template.name`` in
        ``_block_sort_key`` kostet dank JOIN keine weitere Query, macht
        also insgesamt EXAKT 2 Queries. Ohne den Prefetch
        (``x_iter_blocks_in_order__mutmut_6``) kostet der Zugriff je freiem
        Feld eine eigene Query -> 4 Queries bei zwei Feldern.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Prefetch-Sonde")
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=_ft(facility, "Erstes Feld"), sort_order=0
        )
        DocumentTypeField.objects.create(
            document_type=doc_type, field_template=_ft(facility, "Zweites Feld"), sort_order=1
        )

        with CaptureQueriesContext(connection) as ctx:
            blocks = iter_blocks_in_order(doc_type)

        assert [block["kind"] for block in blocks] == ["field", "field"]
        assert len(ctx.captured_queries) == 2


class TestIterBlocksInOrderInnerTieBreak:
    """Pinnt ``x_iter_blocks_in_order__mutmut_18`` (entwerteter pk-Tiebreak)."""

    def test_group_members_with_equal_sort_order_are_ordered_deterministically_by_pk(self, facility):
        """Zwei Gruppenmitglieder mit gleichem Innen-``sort_order`` (Alt-Daten-Gleichstand).

        ``dtf_hoch`` (groessere pk) wird ZUERST angelegt, ``dtf_niedrig``
        (kleinere pk) DANACH — die Einfuegereihenfolge ist damit
        pk-ABSTEIGEND. Original-Code sortiert die Gruppenmitglieder trotzdem
        deterministisch pk-AUFSTEIGEND (``dtf_niedrig``, ``dtf_hoch``); der
        Mutant (Tiebreak-Schluessel immer ``str(None)``) belaesst wegen der
        Sortier-Stabilitaet die Einfuegereihenfolge, liefert also
        (``dtf_hoch``, ``dtf_niedrig``) — abweichend von der Erwartung.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Tiebreak-Sonde")
        group = FieldGroup.objects.create(document_type=doc_type, name="Gruppe", sort_order=0)

        dtf_hoch = DocumentTypeField.objects.create(
            id=_pk(2),
            document_type=doc_type,
            field_template=_ft(facility, "Feld Hoch"),
            field_group=group,
            sort_order=0,
        )
        dtf_niedrig = DocumentTypeField.objects.create(
            id=_pk(1),
            document_type=doc_type,
            field_template=_ft(facility, "Feld Niedrig"),
            field_group=group,
            sort_order=0,
        )
        assert dtf_niedrig.pk < dtf_hoch.pk  # Vorbedingung: pk-Reihenfolge != Einfuegereihenfolge

        blocks = iter_blocks_in_order(doc_type)

        assert [block["kind"] for block in blocks] == ["group"]
        assert [field.pk for field in blocks[0]["fields"]] == [dtf_niedrig.pk, dtf_hoch.pk]

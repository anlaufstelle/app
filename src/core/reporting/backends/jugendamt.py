"""Jugendamt-Sachbericht als ``DatasetBackend`` (M5 P2, Refs #1061).

Migration der bisher hart kodierten Statistik aus
``core/services/system/export.py`` in das Plugin-System. Die Aggregation ist
**unveraendert** uebernommen (Verhaltensparitaet): ``summary`` traegt exakt das
bisherige Dict-Format — inklusive ``by_category`` als Liste von Tupeln, ueber
die ``core/export/jugendamt_pdf.html`` iteriert und die
``_merge_jugendamt_stats`` nach dem JSON-Roundtrip (Snapshots) wieder
normalisiert.

Neu ist allein :meth:`JugendamtBackend.validate_completeness`: die Checks
brauchen die Rohdaten, die nicht im Datensatz landen (nicht zugeordnete
Dokumenttypen, Ereignisse ohne Personenbezug).
"""

from collections import OrderedDict

from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import CompletenessIssue, DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend

# Mapping: DocumentType.system_type -> Jugendamt-Kategorie (fehlend = ausgeschlossen).
# ``ban``/``note``/NULL fehlen bewusst — sie gehoeren nicht in den Bericht.
# Zweitschlüssel ``DocumentType.slug`` (seit M2 vorhanden) folgt als eigenes
# Folge-Issue — Integrationspunkt.
CATEGORY_MAP = {
    "contact": "Kontakte",
    "crisis": "Beratung",
    "medical": "Versorgung",
    "needle_exchange": "Versorgung",
    "accompaniment": "Vermittlung",
    "counseling": "Beratung",
    "referral": "Vermittlung",
}

# Anzeigelabels der Altersgruppen — lazy wie bisher, damit der Snapshot-Payload
# byte-gleich zum Bestand bleibt.
_AGE_LABELS = {
    "u18": _("Unter 18"),
    "18_26": _("18–26"),
    "27_plus": _("27+"),
    "unknown": _("Unbekannt"),
}


# Ereignisse, deren Dokumenttyp keiner Jugendamt-Kategorie zugeordnet ist —
# ``system_type`` NULL/leer eingeschlossen. Das ``isnull``-ODER ist bewusst
# redundant: Django rendert ``~Q(...__in=[...])`` auf einer NULL-baren Spalte
# als ``NOT (x IN (...) AND x IS NOT NULL)`` und faengt NULL-Zeilen damit
# bereits mit ein. Blankes SQL-``NOT IN`` tut das nicht — der explizite Zweig
# haelt die Absicht unabhaengig von diesem ORM-Detail fest.
_UNMAPPED_Q = Q(document_type__system_type__isnull=True) | ~Q(document_type__system_type__in=tuple(CATEGORY_MAP))


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("jugendamt")
class JugendamtBackend(DatasetBackend):
    """Kontakte/Beratung/Versorgung/Vermittlung je Zeitraum, plus Altersgruppen."""

    # Refs #1279: das sichtbare Label wurde bewusst umbenannt; interne
    # Bezeichner (Slug, Modul, Kategorien-Map) bleiben "jugendamt".
    name = _("Beispiel-Sachbericht")
    snapshot_enabled = True

    def get_field_specs(self) -> list[FieldSpec]:
        """Spalten der tabellarischen Projektion (eine Zeile je Kategorie)."""
        return [
            FieldSpec("category", "Kategorie"),
            FieldSpec("count", "Anzahl"),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Aggregiert Ereignisse zu Jugendamt-Kategorien und Altersgruppen."""
        base_qs = _base_queryset(facility, date_from, date_to)

        # Aggregation ueber DocumentType.system_type
        by_dt = (
            base_qs.values("document_type__system_type")
            .annotate(count=Count("id"))
            .order_by("document_type__system_type")
        )

        # Konsolidierung auf die Jugendamt-Kategorien
        category_counts: OrderedDict[str, int] = OrderedDict()
        total = 0
        for row in by_dt:
            system_type = row["document_type__system_type"]
            cat = CATEGORY_MAP.get(system_type)
            if cat is None:
                # Nicht zugeordnete Typen still ueberspringen — auch ``total``
                # zaehlt sie nicht mit (Bestandsverhalten). Sichtbar werden sie
                # ueber ``validate_completeness``.
                continue
            category_counts[cat] = category_counts.get(cat, 0) + row["count"]
            total += row["count"]

        by_category = [(name, count) for name, count in category_counts.items()]

        # Altersgruppen (nur Ereignisse mit Personenbezug)
        by_age_cluster = [
            {
                "cluster": row["client__age_cluster"],
                "label": _AGE_LABELS.get(row["client__age_cluster"], ""),
                "count": row["count"],
            }
            for row in base_qs.exclude(client__isnull=True)
            .values("client__age_cluster")
            .annotate(count=Count("id"))
            .order_by("-count")
        ]

        unique_clients = base_qs.exclude(client__isnull=True).values("client").distinct().count()

        return DatasetResult(
            mode="aggregate",
            records=[{"category": name, "count": count} for name, count in by_category],
            summary={
                "total": total,
                "by_category": by_category,
                "by_age_cluster": by_age_cluster,
                "unique_clients": unique_clients,
            },
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Beziffert, was der Bericht **nicht** zeigt.

        Beide Checks laufen gegen die Rohdaten, nicht gegen den Datensatz:
        nicht zugeordnete Dokumenttypen fehlen dort per Definition, und
        Ereignisse ohne Personenbezug sind in den Kategorien enthalten, in
        Altersgruppen/Personenzahl aber nicht.
        """
        # Alle vier Kennzahlen in *einer* Aggregation: ``build_dataset`` ruft
        # diese Methode immer mit, und der Hybrid-/Snapshot-Pfad ruft
        # ``build_dataset`` je Zeitsegment einmal auf — Einzel-``count()``s
        # wuerden sich dort vervielfachen.
        counts = _base_queryset(facility, date_from, date_to).aggregate(
            total=Count("id"),
            without_client=Count("id", filter=Q(client__isnull=True)),
            unmapped_events=Count("id", filter=_UNMAPPED_Q),
            unmapped_types=Count("document_type_id", filter=_UNMAPPED_Q, distinct=True),
        )
        issues: list[CompletenessIssue] = []

        # (a) Dokumenttypen ohne Jugendamt-Zuordnung (inkl. system_type NULL/leer).
        if counts["unmapped_events"]:
            issues.append(
                CompletenessIssue(
                    "unmapped_system_types",
                    f"{counts['unmapped_events']} Ereignisse in {counts['unmapped_types']} "
                    "Dokumenttypen ohne Jugendamt-Zuordnung im Zeitraum",
                    "warning",
                )
            )

        # (b) Ereignisse ohne Personenbezug — die Luecke in by_age_cluster
        # und unique_clients.
        if counts["without_client"]:
            share = round(counts["without_client"] * 100 / counts["total"])
            issues.append(
                CompletenessIssue(
                    "events_without_client",
                    f"{counts['without_client']} von {counts['total']} Ereignissen ohne Personenbezug "
                    f"({share} %) — nicht in Altersgruppen/Personenzahl enthalten",
                    "info",
                )
            )

        return issues

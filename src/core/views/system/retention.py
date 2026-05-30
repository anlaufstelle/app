"""Cross-Facility-Retention-Uebersicht fuer super_admin (Refs #875)."""

from datetime import date

from django.db.models import Count, Min, Q
from django.views.generic import TemplateView

from core.models import Facility
from core.models.retention import RetentionProposal
from core.views.system.mixins import SystemAuditMixin


class SystemRetentionView(SystemAuditMixin, TemplateView):
    """Cross-Facility-Aggregation der ``RetentionProposal``-Statistik.

    Zeigt pro Einrichtung die Anzahl Vorschlaege je Status, das naechste
    Faelligkeitsdatum (``min(deletion_due_at)`` der PENDING) und die Zahl
    bereits ueberfaelliger PENDING-Vorschlaege. Read-Only — Aktionen
    laufen weiterhin im Facility-Kontext (``/retention/``).

    Datenfluss: ein einziger ``GROUP BY``-Query mit ``Count(case=...)``-
    Aggregation pro Status. Das skaliert gut, solange das Volumen klein
    bleibt (selten >1000 Proposals pro Facility) — bei groesserem Volumen
    waere ein dedicated dashboard-aggregations-table sinnvoll.

    Der RLS-Bypass laeuft ueber ``app.is_super_admin='true'`` (Migration
    0085) und ist im :class:`SystemAuditMixin` bereits gesetzt.
    """

    template_name = "core/system/retention.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        today = date.today()

        # Eine GROUP-BY-Query pro Facility liefert Counts je Status,
        # plus min(deletion_due_at) ueber alle PENDING und Count der
        # ueberfaelligen PENDING. ``Count(filter=...)`` ist seit Django
        # 2.0 verfuegbar — wir bauen damit eine Pivot-Tabelle direkt im
        # ORM, ohne pro Status eine eigene Subquery zu brauchen.
        rows_qs = RetentionProposal.objects.values("facility_id").annotate(
            count_pending=Count("id", filter=Q(status=RetentionProposal.Status.PENDING)),
            count_approved=Count("id", filter=Q(status=RetentionProposal.Status.APPROVED)),
            count_held=Count("id", filter=Q(status=RetentionProposal.Status.HELD)),
            count_deferred=Count("id", filter=Q(status=RetentionProposal.Status.DEFERRED)),
            count_rejected=Count("id", filter=Q(status=RetentionProposal.Status.REJECTED)),
            next_due_date=Min(
                "deletion_due_at",
                filter=Q(status=RetentionProposal.Status.PENDING),
            ),
            overdue_count=Count(
                "id",
                filter=Q(
                    status=RetentionProposal.Status.PENDING,
                    deletion_due_at__lt=today,
                ),
            ),
        )

        # Aus der Aggregation einen Lookup nach facility_id bauen, dann
        # ueber alle Facilities iterieren — auch jene ohne Proposals
        # tauchen so mit Nullen auf. Das ist explizit gewollt, um zu
        # zeigen, dass dort schlicht nichts ansteht.
        agg_by_facility = {row["facility_id"]: row for row in rows_qs}
        facilities = Facility.objects.select_related("organization").order_by("name")

        rows = []
        totals = {
            "count_pending": 0,
            "count_approved": 0,
            "count_held": 0,
            "count_deferred": 0,
            "count_rejected": 0,
            "overdue_count": 0,
        }
        critical_count = 0
        for facility in facilities:
            agg = agg_by_facility.get(facility.pk, {})
            row = {
                "facility": facility,
                "count_pending": agg.get("count_pending", 0) or 0,
                "count_approved": agg.get("count_approved", 0) or 0,
                "count_held": agg.get("count_held", 0) or 0,
                "count_deferred": agg.get("count_deferred", 0) or 0,
                "count_rejected": agg.get("count_rejected", 0) or 0,
                "next_due_date": agg.get("next_due_date"),
                "overdue_count": agg.get("overdue_count", 0) or 0,
            }
            row["is_critical"] = row["overdue_count"] > 0
            if row["is_critical"]:
                critical_count += 1
            for key in totals:
                totals[key] += row[key]
            rows.append(row)

        context.update(
            {
                "rows": rows,
                "totals": totals,
                "critical_count": critical_count,
                "today": today,
            }
        )
        return context

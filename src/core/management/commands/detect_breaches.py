"""Management-Command: ``detect_breaches`` (Refs #685).

Faehrt die Heuristiken aus ``services/breach_detection`` ueber alle
Facilities und schreibt SECURITY_VIOLATION-AuditLog-Eintraege fuer
neue Findings. Geeignet als Cron-Job (z.B. stuendlich).
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import AuditLog, Facility
from core.services.audit import audit_event
from core.services.compliance import run_all_detections, run_system_detections

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Heuristik-basierte Breach-Detection über alle Facilities (Refs #685)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--facility",
            type=str,
            default=None,
            help="Limit auf eine einzelne Facility per Name.",
        )

    def handle(self, *args, **options):
        # Refs #1512: wie enforce_retention (Refs #1016 A1.1) und
        # verify_audit_chain/backfill_audit_chain (Refs #1070) fail-loud, statt
        # RLS-blind 0 Facilities/Zeilen zu scannen und faelschlich "Keine neuen
        # Breach-Findings." zu melden. Der Compliance-Cron MUSS als Rolle mit
        # BYPASSRLS (Admin) laufen.
        if not _has_rls_bypass_context():
            raise CommandError(
                "detect_breaches läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — sonst "
                "sähe der Lauf 0 Facilities/Zeilen und meldete fälschlich 'Keine neuen "
                "Breach-Findings.' (Refs #1512)."
            )
        facility_name = options["facility"]
        facilities = Facility.objects.all()
        if facility_name:
            facilities = facilities.filter(name=facility_name)
            # Refs #2068 (a) / Cross-Review Finding 4: CommandError statt
            # stderr+return. Ein ``return`` beendete den Lauf mit Exit-Code 0 —
            # ein Tippfehler im Cron-``--facility`` sah damit wie ein
            # erfolgreicher Lauf aus, obwohl gar nichts verarbeitet wurde.
            # Vorbild ist ``generate_dsgvo_package``, das das schon richtig
            # macht. Mehrfach-Treffer sind seit dem UNIQUE auf
            # ``Facility.name`` unmoeglich; bleibt genau dieser 0-Treffer-Fall.
            if not facilities.exists():
                raise CommandError(f"Facility '{facility_name}' not found.")

        total = 0
        for facility in facilities:
            entries = run_all_detections(facility)
            total += len(entries)
            for entry in entries:
                self.stdout.write(
                    f"  [{facility.name}] {entry.detail.get('kind')} - count={entry.detail.get('count')} "
                    f"(audit_id={entry.pk})"
                )

        # Refs #1368: installationsweite Heuristiken (Bursts gegen unbekannte
        # Usernames haben keine Facility) — einmal pro Lauf, nicht je Facility.
        # Nur beim Voll-Lauf (ohne ``--facility``-Einschraenkung).
        if not facility_name:
            for entry in run_system_detections():
                total += 1
                self.stdout.write(
                    f"  [system] {entry.detail.get('kind')} - count={entry.detail.get('count')} (audit_id={entry.pk})"
                )

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Keine neuen Breach-Findings."))
        else:
            self.stdout.write(self.style.WARNING(f"{total} neue Breach-Finding(s) — siehe Audit-Log."))

        processed = facilities.count()
        self.stdout.write(
            f"Run coverage: processed={processed}, skipped=0, failed=0, system_scan={not bool(facility_name)}."
        )
        # Refs #2583: a filtered scan omits the system heuristics and cannot prove cron health.
        if not facility_name:
            audit_event(
                AuditLog.Action.BREACH_SCAN_COMPLETED,
                user=None,
                facility=None,
                target_type="BreachScanRun",
                detail={
                    "facilities": processed,
                    "findings": total,
                    "job_run": {
                        "scope": "installation",
                        "status": "success",
                        "processed": processed,
                        "skipped": 0,
                        "failed": 0,
                    },
                },
            )

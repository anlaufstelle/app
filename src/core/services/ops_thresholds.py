"""Schwellwerte der Betriebs-Ueberwachung — eine Quelle fuer alle Sichten (Refs #1872).

Vor diesem Modul lagen fuer *dieselbe* Frage („wann ist ein Lauf ueberfaellig?")
vier untereinander verschiedene Zahlen im Baum:

- ``compliance/backup.py``: OK <= 24 h, WARNING <= 72 h, sonst CRITICAL,
- ``system/health.py``: ``BACKUP_STALE_THRESHOLD_HOURS = 36`` fuer die
  ``is_stale``-Anzeige derselben Backup-Zeile,
- ``views/health.py``: ``BACKUP_WARN_HOURS = 48`` fuer ``/health/``,
- ``compliance/retention.py``: OK <= 7 d, WARNING <= 14 d — fuer einen Job, den
  ``deploy/crontab`` **taeglich** faehrt.

Ergebnis war eine Backup-Zeile, die im ``/system/``-Dashboard „Veraltet" (36 h),
im Compliance-Dashboard aber „OK" (<= 24 h … 72 h WARNING) melden konnte, und
eine Retention-Ampel, die einen seit sechs Tagen toten Cron gruen anzeigte.

**Ableitung der Zielwerte** — nicht gegriffen, sondern aus dem tatsaechlichen
Laufintervall der Jobs (``deploy/crontab`` bzw. ``docs/ops-runbook.md`` § 3.1):

===============================  ==================  =========  =========
Job                              Intervall           WARNING    CRITICAL
===============================  ==================  =========  =========
``backup.sh`` (Host-Cron 02:00)  taeglich (24 h)     > 36 h     > 48 h
``enforce_retention`` (03:00)    taeglich (24 h)     > 36 h     > 48 h
``verify_audit_chain`` (03:30)   taeglich (24 h)     > 36 h     > 48 h
===============================  ==================  =========  =========

Regel: **WARNING ab 1,5 x Intervall, CRITICAL ab 2 x Intervall.** Ein gesunder
Tageslauf haelt das Alter unter 24 h plus etwas Jitter; 36 h ist damit nur
erreichbar, wenn genau ein Lauf ausgefallen ist (die halbe Intervall-Marge
faengt verzoegerte Starts und den Sommerzeit-Sprung ab), 48 h erst, wenn zwei
Laeufe hintereinander ausgefallen sind. Beide Zahlen waren im Baum schon
vorhanden (36 aus ``system/health.py``, 48 aus ``views/health.py`` und
``docs/monitoring-guide.md``) — konsolidiert wird also auf die bestehenden
Werte, es kommt **keine fuenfte** Schwelle hinzu. Insbesondere bleibt die
``/health/``-Degradierungsgrenze bei unveraenderten 48 h, damit externes
Monitoring durch die Konsolidierung nicht laut wird.

Die stuendlichen bzw. monatlichen Jobs (``detect_breaches``,
``refresh_statistics_view``, ``create_statistics_snapshots``) behalten ihre
eigenen, in ``compliance/cron.py`` dokumentierten Schwellen: sie haben ein
anderes Intervall und teilen mit den drei Taeglichen keine Zahl.
"""

from __future__ import annotations

# Laufintervall der drei taeglichen Betriebsjobs, in Stunden.
DAILY_JOB_INTERVAL_HOURS = 24

# Ein Lauf ausgefallen (1,5 x Intervall) — im UI gelb, kein /health/-Degrade.
DAILY_JOB_WARN_HOURS = 36

# Zwei Laeufe ausgefallen (2 x Intervall) — rot, und auf /health/ ``degraded``.
DAILY_JOB_CRITICAL_HOURS = 48

# Sprechende Aliase je Job. Bewusst eigene Namen statt roher Zahlen an den
# Aufrufstellen: verschiebt sich spaeter ein Laufintervall (z.B. Backup auf
# zweimal taeglich), aendert sich hier genau eine Zeile.
BACKUP_WARN_HOURS = DAILY_JOB_WARN_HOURS
BACKUP_CRITICAL_HOURS = DAILY_JOB_CRITICAL_HOURS

RETENTION_WARN_HOURS = DAILY_JOB_WARN_HOURS
RETENTION_CRITICAL_HOURS = DAILY_JOB_CRITICAL_HOURS

AUDIT_CHAIN_WARN_HOURS = DAILY_JOB_WARN_HOURS
AUDIT_CHAIN_CRITICAL_HOURS = DAILY_JOB_CRITICAL_HOURS

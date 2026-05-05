"""Shared constants used across models and services."""

from django.utils.translation import gettext_lazy as _

# Contact stage choices — single source of truth.
# Must match Client.ContactStage values.
CONTACT_STAGE_CHOICES = [
    ("identified", _("Identifiziert")),
    ("qualified", _("Qualifiziert")),
]

# Pagination — Listenseiten (Klient*innen, Fälle, Aufgaben)
DEFAULT_PAGE_SIZE = 25
AUDIT_PAGE_SIZE = 50
# Maximale Seitenzahl — verhindert Seq-Scans bei ?page=99999. Postgres-OFFSET
# waechst linear, daher cappen wir den Page-Index in den Listenviews
# (clients/cases/audit). Refs #733, Audit-Massnahme #32.
MAX_PAGE = 500

# Refs #803 (C-36): Maximalanzahl Eintraege pro Feed-Typ in services/feed.py.
# Frueher hartcodierte 200 verteilt auf 5 Stellen — busy days ueber 200
# Eintraegen wurden stumm abgeschnitten. Eine zentrale Konstante laesst sich
# in Tests und ueber Settings (falls noetig) anpassen.
FEED_MAX_PER_TYPE = 200

# Refs #803: WorkItem-Inbox-Cap. Eigene Konstante, weil die Inbox bewusst
# kein Paginator-Browsen anbietet — sie zeigt die Top-N und einen
# "weitere ueber Filter/Detailsuche"-Hinweis.
WORKITEM_INBOX_CAP = 50

# Rate-Limits (django-ratelimit) — pro User, sliding window
RATELIMIT_BULK_ACTION = "30/h"
RATELIMIT_MUTATION = "60/h"
RATELIMIT_FREQUENT = "120/h"

# Retention — Schwellen (in Tagen) für Urgency-Markierung
# `red`: Löschung in <= RETENTION_URGENCY_RED_DAYS Tagen fällig
# `yellow`: Löschung in <= RETENTION_URGENCY_YELLOW_DAYS Tagen fällig
RETENTION_URGENCY_RED_DAYS = 7
RETENTION_URGENCY_YELLOW_DAYS = 30

# File-Upload — fail-closed Defaults (Refs #771).
# Greifen, wenn die Facility keine ``Settings``-Row hat oder
# ``allowed_file_types`` leer/whitespace-only ist. Verhindert das stille
# Oeffnen jedes Dateityps in unkonfigurierten Mandanten. Werte spiegeln
# die produktiven Default-Settings (vgl. ``Settings.allowed_file_types``).
DEFAULT_ALLOWED_FILE_TYPES = frozenset({"pdf", "jpg", "jpeg", "png", "docx", "odt"})
DEFAULT_MAX_FILE_SIZE_MB = 10

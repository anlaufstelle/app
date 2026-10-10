"""Demo authentication constants shared by seed and offline bootstrap."""

from django.utils.translation import gettext_lazy as _

# Public password for synthetic demo accounts, never for production accounts.
DEMO_PASSWORD = "anlaufstelle2026"  # noqa: S105
DEMO_OFFLINE_BOOTSTRAP_PENDING = "demo_offline_bootstrap_pending"
DEMO_OFFLINE_BOOTSTRAP_ERROR = _(
    "Offline-Schlüssel konnte nicht aktiviert werden. Bitte regulär anmelden "
    "oder die Demo nach dem nächsten Reset erneut starten."
)

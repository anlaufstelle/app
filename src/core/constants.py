"""Shared constants used across models and services."""

from django.db import models
from django.utils.translation import gettext_lazy as _

# Contact stage choices — single source of truth.
# Must match Client.ContactStage values.
CONTACT_STAGE_CHOICES = [
    ("identified", _("Nur Pseudonym")),
    ("qualified", _("Mit Zusatzangaben")),
]


class ApprovalStatus(models.TextChoices):
    """Status eines Vier-Augen-Antrags — geteilt (Refs #1834).

    ``DeletionRequest`` (Löschantrag) und ``RectificationRequest``
    (Art.-16-Berichtigungsantrag) führten dieselben drei Werte samt Labels je
    einmal. Beide Modelle behalten ihr ``Status``-Klassenattribut als Alias auf
    diese Klasse, damit die Aufrufstellen (``DeletionRequest.Status.PENDING``)
    unverändert bleiben. Präzedenz: :data:`CONTACT_STAGE_CHOICES`.

    **Nicht** hierher gehört ``RetentionProposal.Status``: fünf Werte,
    ``held``/``deferred`` als eigene Zustände und „Freigegeben" statt
    „Genehmigt" — dort ist die Namensgleichheit oberflächlich.
    """

    PENDING = "pending", _("Ausstehend")
    APPROVED = "approved", _("Genehmigt")
    REJECTED = "rejected", _("Abgelehnt")


# Pagination — Listenseiten (Klient*innen, Fälle, Aufgaben)
DEFAULT_PAGE_SIZE = 25
AUDIT_PAGE_SIZE = 50
# Maximale Seitenzahl — verhindert Seq-Scans bei ?page=99999. Postgres-OFFSET
# waechst linear, daher cappen wir den Page-Index in den Listenviews
# (clients/cases/audit). Refs #733.
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

# Refs #1149: Im aufklappbaren Bereich "Kürzlich erledigt" (letzte 7 Tage)
# zeigt die Inbox standardmaessig nur die letzten N Eintraege; der Rest wird
# erst auf Wunsch eingeblendet. Haelt den Rueckblick visuell schlank, ohne den
# 7-Tage-Zeitraum zu beschneiden.
WORKITEM_RECENT_DONE_PREVIEW = 5

# Refs #1761 (DSGVO-Hygiene): Grenzen des ``RecentClientVisit``-Prune in
# ``track_client_visit``. ``RECENT_VISIT_MAX_PER_USER`` ist die bestehende
# „letzte N pro User"-Kappung (bis #1761 als Magic Number im Service);
# ``RECENT_VISIT_MAX_AGE_DAYS`` ergaenzt sie um eine Altersfrist, damit eine
# Besuchsspur nicht unbegrenzt weiterlebt, nur weil dieselbe Person dauerhaft
# in den Top N bleibt. Die Zeile traegt zwar kein eigenes PII-Feld (nur FKs +
# ``visited_at``), verknuepft aber Person und Mitarbeiter:in ueber die Zeit —
# reine Datensparsamkeit, kein Klartext-Leck.
RECENT_VISIT_MAX_PER_USER = 20
RECENT_VISIT_MAX_AGE_DAYS = 90

# MFA-Setup-Grace-Frist (Refs #689): Fenster nach dem ersten Login, in dem ein
# rollen-erzwungener Nutzer die 2FA einrichten kann, bevor der Zugang gesperrt
# wird. Bewusst gesetzte Zwaenge (``mfa_required``/facility-weit) greifen ohne
# Grace. Der Reminder-Banner erscheint ab ``MFA_SETUP_REMINDER_DAYS`` Resttagen.
MFA_SETUP_GRACE_DAYS = 30
MFA_SETUP_REMINDER_DAYS = 14

# Rate-Limits (django-ratelimit) — pro User, sliding window
RATELIMIT_BULK_ACTION = "30/h"
RATELIMIT_MUTATION = "60/h"
RATELIMIT_FREQUENT = "120/h"
# Refs #1354 (Befund K1c): dediziertes Limit fuer den Offline-Bundle-GET.
# Sammel-Mitnahme von 20 Klienten (#1326) + Revalidierung aller 20 beim
# naechsten Online-Event = 40 GETs/h bestimmungsgemaesser Gebrauch — das
# vorher geteilte RATELIMIT_BULK_ACTION (30/h) war zu knapp.
RATELIMIT_OFFLINE_BUNDLE = "120/h"
# Refs #1760 (Fix-Runde 1): eigenes Limit fuer die Offline-Widerrufsliste.
# Bewusst NICHT ``RATELIMIT_OFFLINE_BUNDLE`` mitbenutzt, aus zwei Gruenden:
#
# (a) **Anderes Lastprofil.** Der Bundle-Fetch ist eine bewusste Nutzeraktion
#     ("Person mitnehmen") plus Revalidierung; der Widerrufs-Poll laeuft dagegen
#     bei JEDEM Seitenaufbau (die App nutzt kein hx-boost) und zusaetzlich vor
#     jedem Sync. Client-seitig ist der Boot-Poll auf einen Lauf pro Minute
#     gedrosselt (``sync-orchestrator.js``) — also bis zu 60/h allein daraus. Mit
#     120/h waere eine intensive Dokumentationsstunde am Limit.
# (b) **Der Kanal ist sicherheitskritisch und darf nicht als Seiteneffekt
#     mitgedrosselt werden.** Wird der Widerruf abgewiesen (429), laeuft der
#     Replay trotzdem weiter — ein spaeteres Nachschaerfen des Bundle-Limits
#     duerfte diesen Pfad nicht stillschweigend treffen.
#
# Die Antwort ist PII-frei (opake UUIDs bereits anonymisierter Personen) und
# winzig, ein grosszuegiges Limit kostet also nichts. Der Bucket ist ohnehin
# eigenstaendig (django-ratelimit leitet die Gruppe aus Modul + Qualname der
# View-Methode ab) — hier geht es um den WERT, nicht um die Trennung.
RATELIMIT_OFFLINE_REVOCATIONS = "600/h"

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

# Default fuer die ``Settings.allowed_file_types``-Row einer KONFIGURIERTEN
# Facility. Bewusst als benannte Konstante gefuehrt, damit der Model-Default
# nicht als Magic-String driftet, sondern an genau einer Stelle steht und der
# Drift-Guard (``src/tests/test_allowed_file_types_drift.py``, Refs #1478/COD-11)
# ihn gegen ``DEFAULT_ALLOWED_FILE_TYPES`` prueft.
#
# Maintainer-Entscheid (2026-07-17, Refs #1640): ``odt`` ist erlaubt — das
# Risikoprofil entspricht ``docx``, LibreOffice ist in Sozialarbeit/Verwaltung
# verbreitet. Beide Defaults sind deckungsgleich; der Drift-Guard sichert das
# per Gleichheits-Assertion ab, statt eine Divergenz zu pinnen.
DEFAULT_SETTINGS_ALLOWED_FILE_TYPES = "pdf,jpg,jpeg,png,docx,odt"

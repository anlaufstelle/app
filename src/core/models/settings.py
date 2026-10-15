"""Facility settings (singleton per facility)."""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.constants import DEFAULT_SETTINGS_ALLOWED_FILE_TYPES
from core.models.managers import FacilityScopedManager

# Smallest k-anonymity threshold that can be stored, and the value below which
# the settings page and the report page warn about the re-identification risk.
K_ANONYMITY_MIN = 2
K_ANONYMITY_WARN_BELOW = 3

# Default retention periods (days). Shared so tests can assert the same values
# the model fields default to, instead of re-hardcoding the literals (#1205).
DEFAULT_RETENTION_ANONYMOUS_DAYS = 90
DEFAULT_RETENTION_IDENTIFIED_DAYS = 365
DEFAULT_RETENTION_QUALIFIED_DAYS = 3650

# Absolutes Sitzungs-Maximum ab Login in Stunden (Refs #1868). Gleiches Muster
# wie DEFAULT_RETENTION_*_DAYS: die Konstante ist die einzige Quelle des
# Literals — der Login-View und der Grace-Stempel der
# SessionIdleRefreshMiddleware greifen fuer Nutzer ohne Einrichtung bzw. ohne
# Settings-Zeile darauf zurueck.
DEFAULT_SESSION_ABSOLUTE_HOURS = 12

# Refs #2136 (N-2): Vorgabeziel der Aktion "Schnell verlassen". Kriterien der
# Auswahl: oeffentlich, HTTPS, ohne Consent-Banner und ohne Werbe-/Tracking-
# Interstitial (ein Cookie-Dialog auf der Fluchtseite kostet genau die Sekunden,
# die die Aktion sparen soll) und inhaltlich so unauffaellig, dass die Seite im
# Verlauf oder ueber die Schulter nicht als "Fluchtseite" lesbar ist. Die
# Konstante ist die einzige Quelle des Literals — dieselbe Vorgabe steht in
# static/js/quick-exit.js als Fallback fuer die Offline-Shell (dort gibt es
# keinen Server, der sie rendern koennte); der Gleichlauf ist getestet.
DEFAULT_QUICK_EXIT_URL = "https://www.wikipedia.org/"


class Settings(models.Model):
    """Configuration for a facility (1:1 with Facility)."""

    objects = FacilityScopedManager()

    facility = models.OneToOneField(
        "core.Facility",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="settings",
        verbose_name=_("Einrichtung"),
    )
    facility_full_name = models.CharField(
        max_length=300,
        blank=True,
        verbose_name=_("Vollständiger Name"),
    )
    default_document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Standard-Dokumentationstyp"),
    )
    # Security N8 (Refs #1445): Untergrenze 1 Tag. 0/negativ erzeugt in
    # core.retention.strategies einen Cutoff >= jetzt und wuerde den
    # Gesamtbestand zum Loeschen vorschlagen. Validator (Form/full_clean) +
    # CheckConstraint (DB, siehe Meta) erzwingen die Untergrenze doppelt.
    session_timeout_minutes = models.IntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        verbose_name=_("Session-Timeout (Minuten)"),
    )
    # Refs #1868: Obergrenze fuer die Lebensdauer EINER Sitzung, gemessen ab
    # Login. Das Inaktivitaets-Fenster aus #1858 kennt keine — eine Sitzung
    # lebt, solange irgendetwas pingt; ein entwendetes Cookie damit beliebig
    # lange. Obergrenze 168 h (eine Woche), damit sich das Cap nicht durch
    # einen absurd hohen Wert faktisch abschalten laesst.
    session_absolute_hours = models.PositiveIntegerField(
        default=DEFAULT_SESSION_ABSOLUTE_HOURS,
        validators=[MinValueValidator(1), MaxValueValidator(168)],
        verbose_name=_("Maximale Sitzungsdauer (Stunden)"),
        help_text=_(
            "Nach dieser Zeit ab der Anmeldung endet die Sitzung in jedem Fall — "
            "unabhängig von der Aktivität. Muss größer sein als das "
            "Session-Timeout. Eine Änderung greift erst beim nächsten Login, "
            "weil die Frist beim Anmelden eingefroren wird."
        ),
    )
    retention_anonymous_days = models.IntegerField(
        default=DEFAULT_RETENTION_ANONYMOUS_DAYS,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung anonym (Tage)"),
    )
    retention_identified_days = models.IntegerField(
        default=DEFAULT_RETENTION_IDENTIFIED_DAYS,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung identifiziert (Tage)"),
    )
    retention_qualified_days = models.IntegerField(
        default=DEFAULT_RETENTION_QUALIFIED_DAYS,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung qualifiziert (Tage)"),
    )
    retention_activities_days = models.IntegerField(
        default=365,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung Aktivitäten (Tage)"),
    )
    # Refs #2042: eigene, bewusst KURZE Frist fuer die Uebergabenotiz. Sie ist
    # eine fluechtige Dienstnotiz und kein Fallvorgang — 90 Tage decken den
    # Rueckblick auf einen Dienstplan-Zyklus ab, ohne dass Freitext ueber
    # Personen jahrelang liegen bleibt (Art. 5 Abs. 1 lit. e DSGVO).
    # ``PositiveIntegerField`` statt ``IntegerField`` + Check-Constraint:
    # negative Werte schliesst schon der Spaltentyp aus, und die
    # Enforcement-Funktion bricht bei ``< 1`` defensiv ab (Muster
    # ``enforce_activities``, Security N8).
    retention_handover_notes_days = models.PositiveIntegerField(
        default=90,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung Übergabenotizen (Tage)"),
        help_text=_(
            "Wie lange manuelle Schichtnotizen in der Übergabe aufbewahrt werden, "
            "bevor enforce_retention sie endgültig löscht."
        ),
    )
    client_trash_days = models.PositiveIntegerField(
        default=30,
        verbose_name=_("Papierkorb-Frist (Tage)"),
        help_text=_(
            "Wie lange soft-deletete Personen im Papierkorb verbleiben, "
            "bevor enforce_retention sie automatisch anonymisiert. "
            "Während dieser Frist kann ein Admin den Datensatz wiederherstellen."
        ),
    )
    deletion_request_retention_days = models.PositiveIntegerField(
        default=1095,
        validators=[MinValueValidator(1)],
        verbose_name=_("Aufbewahrung abgeschlossener Anträge (Tage)"),
    )
    auditlog_retention_months = models.PositiveIntegerField(
        default=24,
        verbose_name=_("AuditLog-Aufbewahrung (Monate)"),
        help_text=_(
            "Wie lange AuditLog-Einträge aufbewahrt werden, bevor das "
            "enforce_retention-Kommando sie löscht. 0 = deaktiviert "
            "(unbegrenzte Aufbewahrung)."
        ),
    )
    # ADR-050 D3 / M-1 / M-5 (Refs #2065): eigene Frist fuer den INHALT alter
    # Historienfassungen. Bewusst in Monaten und mit demselben Vorgabewert wie
    # ``auditlog_retention_months`` — Betreiber sollen sich nur einen Zeitraum
    # merken muessen. ``0`` = aus (kein ``MinValueValidator``, anders als bei
    # den ``retention_*_days``: dort waere 0 eine Massen-Loeschung, hier ist es
    # das Abschalten des Sweeps).
    eventhistory_detail_months = models.PositiveIntegerField(
        default=24,
        verbose_name=_("Historien-Inhalte aufbewahren (Monate)"),
        help_text=_(
            "Wie lange die Werte alter Ereignis-Fassungen lesbar bleiben, bevor "
            "enforce_retention sie redigiert. Die Fassung selbst (wer, wann, "
            "welche Felder) bleibt erhalten. Die jüngste Fassung eines lebenden "
            "Ereignisses wird nie redigiert. 0 = deaktiviert."
        ),
    )
    # ADR-050 D8 / M-7 (Refs #2065): Stufe 2 — die bereits redigierte Zeile
    # (Chronologie ohne Inhalte) wird geloescht, wenn ihre Redaktion so lange
    # zurueckliegt. Gemessen ab ``EventHistory.redacted_at``, nicht ab
    # ``changed_at``: die Frage lautet „wie lange steht die Zeile schon leer".
    eventhistory_chronicle_months = models.PositiveIntegerField(
        default=24,
        verbose_name=_("Historien-Chronologie aufbewahren (Monate)"),
        help_text=_(
            "Wie lange eine bereits redigierte Historien-Fassung nach ihrer "
            "Redaktion noch aufbewahrt wird, bevor enforce_retention sie löscht. "
            "0 = deaktiviert (unbegrenzte Aufbewahrung der Chronologie)."
        ),
    )
    allowed_file_types = models.CharField(
        max_length=500,
        blank=True,
        # Refs #1478 (COD-11): benannte Konstante statt Magic-String, damit der
        # Model-Default nicht gegen DEFAULT_ALLOWED_FILE_TYPES driftet (Wert
        # unveraendert -> keine Migration).
        default=DEFAULT_SETTINGS_ALLOWED_FILE_TYPES,
        verbose_name=_("Erlaubte Dateitypen"),
        help_text=_("Kommagetrennte Dateiendungen (z.B. pdf,jpg,png,docx)"),
    )
    max_file_size_mb = models.PositiveIntegerField(
        default=10,
        verbose_name=_("Max. Dateigröße (MB)"),
    )
    # Refs #2129 (Befund N-5): Bis hierher galt eine Obergrenze nur PRO DATEI.
    # Im Mehr-Mandanten-Betrieb konnte eine Einrichtung mit lauter legitimen
    # Uploads den gemeinsamen MEDIA_ROOT fuellen — die Folge traf alle. ``None``
    # heisst „kein einrichtungseigenes Limit"; dann greift der Betreiber-Backstop
    # ``FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB``. Wirksam ist immer das striktere
    # der beiden (siehe ``services/file_vault/quota.py``).
    max_storage_mb = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Speicherkontingent (MB)"),
        help_text=_(
            "Gesamtgröße aller Dateianhänge dieser Einrichtung, inklusive Papierkorb. "
            "Leer = kein einrichtungseigenes Kontingent; es gilt dann der vom Betrieb "
            "gesetzte Standardwert."
        ),
    )
    mfa_enforced_facility_wide = models.BooleanField(
        default=False,
        verbose_name=_("2FA-Pflicht für alle Nutzer"),
        help_text=_(
            "Wenn aktiv, gilt MFA-Zwang (TOTP) automatisch für alle Nutzer dieser "
            "Einrichtung — unabhängig vom einzelnen Benutzerfeld mfa_required."
        ),
    )
    retention_auto_approve_after_defer = models.BooleanField(
        default=False,
        verbose_name=_("Auto-Freigabe nach mehrfacher Zurückstellung"),
        help_text=_(
            "Wenn aktiv, werden Löschvorschläge nach Überschreiten der maximalen "
            "Zurückstellungszahl automatisch freigegeben, statt manuelle Entscheidung "
            "zu erzwingen."
        ),
    )
    retention_max_defer_count = models.PositiveIntegerField(
        default=2,
        validators=[MinValueValidator(1)],
        verbose_name=_("Maximale Zurückstellungen"),
        help_text=_(
            "Nach wie vielen Zurückstellungen eine endgültige Entscheidung "
            "erzwungen oder (bei Auto-Freigabe) automatisch freigegeben wird."
        ),
    )
    retention_rejected_retry_days = models.PositiveIntegerField(
        default=365,
        validators=[MinValueValidator(1)],
        verbose_name=_("Wiedervorlage nach Ablehnung (Tage)"),
    )
    retention_open_case_inactive_days = models.PositiveIntegerField(
        default=365,
        validators=[MinValueValidator(1)],
        verbose_name=_("Hinweis bei ruhenden Fällen (Tage)"),
    )
    retention_use_k_anonymization = models.BooleanField(
        default=False,
        verbose_name=_("K-Anonymisierung statt Hard-Delete"),
        help_text=_(
            "Wenn aktiv, wird bei DSGVO-Löschung statt Hard-Delete eine "
            "K-Anonymisierung durchgeführt. Der Datensatz bleibt statistisch "
            "auswertbar, ist aber nicht mehr re-identifizierbar."
        ),
    )
    k_anonymity_threshold = models.PositiveIntegerField(
        default=5,
        # k = 1 suppresses nothing (every cell from 1 up is published), so the
        # smallest storable threshold is 2; below ``K_ANONYMITY_WARN_BELOW`` the
        # settings page and the report page show a risk warning (ADR-023).
        validators=[MinValueValidator(K_ANONYMITY_MIN)],
        verbose_name=_("K-Anonymitäts-Schwelle"),
        help_text=_(
            "Mindest-Gruppengröße für K-Anonymität (z.B. k=5 → mindestens 5 "
            "Datensätze pro Bucket). Je höher der Wert, desto stärker die "
            "Anonymisierung."
        ),
    )
    search_trigram_threshold = models.FloatField(
        default=0.3,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        verbose_name=_("Fuzzy-Search-Schwelle"),
        help_text=_(
            "Mindest-Ähnlichkeit (0.0–1.0) für Fuzzy-Treffer im Pseudonym. "
            "Kleinere Werte liefern mehr, aber ungenauere Treffer; größere "
            "Werte sind strenger. Standard: 0.3."
        ),
    )
    # Refs #2136 (N-2): Mitschauen-/Shoulder-Surfing-Schutz. Beide Felder sind
    # einrichtungsweit (wie alles in dieser Zeile) — die Zielseite ist eine
    # fachliche Entscheidung der Einrichtung, nicht des Betreibers der Instanz.
    quick_exit_url = models.URLField(
        max_length=200,
        default=DEFAULT_QUICK_EXIT_URL,
        # URLField bringt einen URLValidator mit, der auch ftp/ftps zulaesst.
        # Der zusaetzliche Validator schneidet das auf http/https zu: alles
        # andere ist im Browser entweder kein Navigationsziel oder (javascript:,
        # data:) ein Skript-Injektionsvektor in location.replace().
        validators=[URLValidator(schemes=["http", "https"])],
        verbose_name=_("Ziel für „Schnell verlassen“"),
        help_text=_(
            "Adresse, die die Aktion „Schnell verlassen“ öffnet. Eine "
            "unverfängliche, öffentliche Seite ohne Cookie-Banner wählen — sie "
            "steht danach im Verlauf des Geräts. Nur http/https."
        ),
    )
    privacy_screen_on_blur = models.BooleanField(
        default=False,
        verbose_name=_("Sichtschutz bei Fenster- oder Tab-Wechsel"),
        help_text=_(
            "Wenn aktiv, verdeckt eine neutrale Fläche den Inhalt, sobald das "
            "Fenster den Fokus verliert oder der Tab in den Hintergrund geht. "
            "Die Freigabe erfolgt per Klick oder Tastendruck; es läuft kein "
            "Timer. Empfohlen für Arbeitsplätze mit Publikumsverkehr."
        ),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Aktualisiert am"),
    )

    class Meta:
        verbose_name = _("Einstellungen")
        verbose_name_plural = _("Einstellungen")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(retention_rejected_retry_days__gte=1) & models.Q(retention_open_case_inactive_days__gte=1)
                ),
                name="settings_retention_review_min_1",
            ),
            models.CheckConstraint(
                condition=models.Q(search_trigram_threshold__gte=0.0) & models.Q(search_trigram_threshold__lte=1.0),
                name="settings_trigram_threshold_range",
            ),
            # Security N8 (Refs #1445): retention_*_days + session_timeout_minutes
            # muessen >= 1 sein — schuetzt gegen 0/negative Fristen, die einen
            # Massen-Loeschvorschlag ausloesen (siehe Validatoren oben).
            models.CheckConstraint(
                condition=(
                    models.Q(session_timeout_minutes__gte=1)
                    & models.Q(retention_anonymous_days__gte=1)
                    & models.Q(retention_identified_days__gte=1)
                    & models.Q(retention_qualified_days__gte=1)
                    & models.Q(retention_activities_days__gte=1)
                    & models.Q(deletion_request_retention_days__gte=1)
                ),
                name="settings_retention_days_min_1",
            ),
            # Refs #1868: dieselbe Doppelung wie Security N8 — Validatoren
            # greifen nur ueber Form/full_clean, ein Direct-Insert (Shell,
            # Datenmigration, Fixture) laeuft daran vorbei. Der Constraint haelt
            # die Spanne 1..168 h UND die Cross-Field-Regel „Maximum echt
            # groesser als das Inaktivitaets-Fenster" auch dort.
            models.CheckConstraint(
                condition=(
                    models.Q(session_absolute_hours__gte=1)
                    & models.Q(session_absolute_hours__lte=168)
                    & models.Q(session_timeout_minutes__lt=models.F("session_absolute_hours") * 60)
                ),
                name="settings_session_absolute_cap",
            ),
        ]

    def clean(self):
        """Cross-Field-Regel: absolutes Maximum > Inaktivitaets-Fenster (#1868).

        Ein Cap kleiner/gleich dem Idle-Timeout macht das Inaktivitaets-Fenster
        wirkungslos — die Sitzung waere immer schon absolut abgelaufen, bevor
        die Inaktivitaet ueberhaupt greifen kann. Das sieht im Betrieb wie ein
        Bug aus („ich werde staendig ausgeloggt, obwohl ich arbeite"), ist aber
        eine stille Fehlkonfiguration. Der CheckConstraint in ``Meta`` haelt
        dieselbe Regel auf DB-Ebene.
        """
        super().clean()
        cap_hours = self.session_absolute_hours
        idle_minutes = self.session_timeout_minutes
        if cap_hours is None or idle_minutes is None:
            return
        if cap_hours * 60 <= idle_minutes:
            raise ValidationError(
                {
                    "session_absolute_hours": _(
                        "Die maximale Sitzungsdauer muss größer sein als das "
                        "Session-Timeout (%(idle)s Minuten). Sonst endet jede "
                        "Sitzung, bevor der Inaktivitäts-Timeout greifen kann."
                    )
                    % {"idle": idle_minutes}
                }
            )

    def __str__(self):
        return f"Einstellungen: {self.facility.name}"

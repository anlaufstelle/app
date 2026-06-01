"""Verzeichnis Verarbeitungstaetigkeiten (VVT) gem. DSGVO Art. 30.

Statisches Verzeichnis aller Verarbeitungstaetigkeiten der Anlaufstelle-
Installation. Wird im ``/system/vvt/``-Bereich (Refs #876) read-only
angezeigt und kann fuer Audits / Datenschutzerklaerungen herangezogen
werden.

Quellen:

* `docs/fachkonzept-anlaufstelle.md` — Abschnitt "Datenschutz als Grund-
  prinzip" (§ 6) und "Sicherheit" (§ 17): nennt die TOMs (RLS, AES-GCM,
  Audit-Trail, MFA, ClamAV, Pseudonymisierung).
* `docs/faq.md` — Abschnitte zu Aufbewahrungsfristen, DSGVO-Paket und
  Rollenmodell.

Wenn fachliche Details unklar sind, ist die Formulierung bewusst
konservativ. Einzelne Punkte (z.B. konkrete Zahlen zu Speicherfristen
fuer Backups oder Logs) sind als gemeinhin uebliche Werte angesetzt;
die Einrichtung muss bei Bedarf eine eigene, an ihrem Hosting-Setup
ausgerichtete Fassung pflegen.

Die Liste umfasst mindestens die sechs Pflicht-Verarbeitungs-
taetigkeiten aus dem Auftrag (Refs #876).

Strings sind via :func:`django.utils.translation.gettext_lazy` lazy
uebersetzbar (Refs #878).
"""

from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Statische Konstante: Verarbeitungstaetigkeiten
# ---------------------------------------------------------------------------
#
# Jeder Eintrag enthaelt die Mindestangaben nach Art. 30 Abs. 1 DSGVO
# (Bezeichnung, Zweck, Rechtsgrundlage, Datenkategorien, Empfaenger,
# Speicherfrist, TOMs). Die ``id`` ist ein stabiler Schluessel zum
# Verlinken / Anchor-Setzen aus Templates und externer Doku.
PROCESSING_ACTIVITIES = [
    {
        "id": "klienten_stammdaten",
        "title": _("Klienten-Stammdaten"),
        "purpose": _(
            "Beratungsdokumentation und Falllaufzeit-Steuerung in "
            "niedrigschwelligen Einrichtungen. Kein Klarname-Feld — der "
            "primaere Identifikator ist ein Pseudonym."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. e (Wahrnehmung einer Aufgabe im "
            "oeffentlichen Interesse) i.V.m. SGB VIII §13 (Jugendsozial"
            "arbeit) bzw. SGB XII (Hilfen in Lebenslagen). "
            "Sozialdatenschutz: SGB X §§67–85a."
        ),
        "data_categories": [
            _("Pseudonym (kein Klarname)"),
            _("Soziodemografische Eckdaten (Alterscluster, Geschlecht)"),
            _("Kontaktstufe (anonym / identifiziert / qualifiziert)"),
            _("Zugeordnete Einrichtung (Facility)"),
        ],
        "recipients": [
            _("Intern: Mitarbeiter:innen der jeweiligen Einrichtung (facility-gescopt, kein Cross-Facility-Zugriff)"),
            _("Anwendungsbetreuung der Einrichtung (Audit, DSGVO-Paket)"),
        ],
        "retention_period": _(
            "Konfigurierbar pro Kontaktstufe und Dokumentationstyp. "
            "Anonyme Kontakte: 12 Monate (danach Aggregation). "
            "Identifizierte Kontakte: 36 Monate nach letztem Kontakt. "
            "Qualifizierte Kontakte: 10 Jahre nach Falllauf-Ende "
            "(SGB VIII §97)."
        ),
        "toms": [
            _("Pseudonymisierung by Design"),
            _("Row Level Security (RLS) pro Facility"),
            _("Verschluesselung at-rest (AES-GCM) sensibler Felder"),
            _("AuditLog auf Lese- und Schreibzugriffe"),
            _("Rollenbasierte Zugriffskontrolle (5 Rollen)"),
            _("MFA / TOTP fuer sensible Rollen"),
        ],
    },
    {
        "id": "falldaten",
        "title": _("Falldaten (Events, Episoden, Dokumentationen)"),
        "purpose": _(
            "Zeitstrom-basierte Dokumentation von Kontakten, Beratungen "
            "und Hilfeprozessen. Strukturierung zusammenhaengender "
            "Arbeit zu Faellen und Episoden. Grundlage der Wirkungs"
            "messung und Berichterstattung gegenueber Foerdermittel"
            "gebern."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. e i.V.m. SGB VIII §§ 61–65 "
            "(Sozialdaten in der Jugendhilfe), Art. 9 Abs. 2 lit. b/h "
            "fuer besondere Datenkategorien (Gesundheits-, "
            "Sozialdaten). § 203 StGB (Schweigepflicht)."
        ),
        "data_categories": [
            _("Ereignisbeschreibungen / Beratungsnotizen"),
            _("Strukturierte Feldwerte (DocumentType-spezifisch)"),
            _("Kontaktanlass, Themen, Vermittlungen"),
            _("Ggf. Gesundheits-/Suchtkontext (verschluesselt)"),
            _("Zeitstempel, Bearbeiter:in, Sensitivitaetsstufe"),
        ],
        "recipients": [
            _("Intern: Mitarbeiter:innen der Einrichtung mit passender Rolle und Sensitivitaetsfreigabe"),
            _("Keine Weitergabe an Dritte — pseudonymisierte Statistik ggf. an Foerdermittelgeber (Jugendamt-Export)"),
        ],
        "retention_period": _(
            "Pro DocumentType konfigurierbar; Default an Kontaktstufe "
            "der zugehoerigen Person gebunden. Sensible Inhalte werden "
            "vor Loeschung k-anonymisiert oder redigiert."
        ),
        "toms": [
            _("Sensitivitaets-basierte Sichtbarkeit (DocumentType-Level + FieldTemplate-Override)"),
            _("Verschluesselung sensibler Felder (AES-GCM, Schluessel ausserhalb der DB)"),
            _("Optimistic Locking gegen versehentliches Ueberschreiben"),
            _("AuditLog jeder Lese- und Schreibaktion"),
            _("Soft-Delete + Vier-Augen-Prinzip vor finaler Loeschung"),
            _("Encrypted File Vault mit ClamAV-Scan fail-closed fuer Anhaenge"),
        ],
    },
    {
        "id": "auditlog",
        "title": _("Audit-Log (Zugriffs- und Aenderungsprotokoll)"),
        "purpose": _(
            "Nachweis der Rechenschaftspflicht (DSGVO Art. 5 Abs. 2). "
            "Erkennung von Missbrauch, Beweismittel im Datenleck-Fall, "
            "Rekonstruktion von Aenderungen. Append-Only — kein UPDATE "
            "oder DELETE durch die Anwendung."
        ),
        "legal_basis": _(
            "DSGVO Art. 32 (Sicherheit der Verarbeitung) i.V.m. Art. 5 "
            "Abs. 2 (Rechenschaftspflicht). § 80 SGB X "
            "(Datensicherheit)."
        ),
        "data_categories": [
            _("Benutzerkennung (User-ID, Username)"),
            _("Aktion (LOGIN, EVENT_CREATED, SYSTEM_VIEW etc.)"),
            _("Ziel-Typ und Ziel-ID (z.B. Event-UUID)"),
            _("Zeitstempel"),
            _("IP-Adresse"),
            _("Zugehoerige Einrichtung (oder NULL bei System-Events)"),
        ],
        "recipients": [
            _("Anwendungsbetreuung der Einrichtung (Facility-Audit-Log)"),
            _(
                "Systemadministration (Cross-Facility-Audit ueber "
                "/system/audit/, mit eigenem SYSTEM_VIEW-Eintrag pro "
                "Aufruf)"
            ),
        ],
        "retention_period": _(
            "Standardmaessig 24 Monate rollierend. Pruning durch "
            "geplanten ``enforce_retention``-Job, Konfiguration pro "
            "Installation. Audit-Eintraege zu offenen Vorfaellen "
            "koennen per LegalHold ueber die Standardfrist hinaus "
            "aufbewahrt werden."
        ),
        "toms": [
            _("Append-Only durch DB-Konvention und RLS-Policies"),
            _("Hash-Chain-Integritaet (manipulationssicher)"),
            _("Zugriff nur fuer Anwendungsbetreuung und Systemadministration"),
            _("Cross-Facility-Auswertung bedingt eigenen SYSTEM_VIEW-Audit"),
            _("Backup taeglich (siehe Verarbeitung 'Backup-Daten')"),
        ],
    },
    {
        "id": "auth_login",
        "title": _("Login- und Authentifizierungsdaten"),
        "purpose": _(
            "Authentifizierung der Mitarbeiter:innen, Schutz gegen "
            "Brute-Force, Sitzungsverwaltung. Nachweis fuer "
            "Datenschutz-Vorfallpruefung."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. b (Vertragserfuellung — "
            "Arbeitsvertrag) und Art. 32 (Sicherheit der Verarbeitung)."
        ),
        "data_categories": [
            _("Username, E-Mail-Adresse"),
            _("Passwort-Hash (PBKDF2/Argon2, niemals Klartext)"),
            _("MFA-Geraetesecret (TOTP, verschluesselt)"),
            _("Backup-Codes (Hash)"),
            _("Login-Versuche (Erfolg/Fehler) inkl. IP und Zeitstempel"),
            _("Account-Lockout-Status"),
        ],
        "recipients": [
            _("Intern: Anwendungsbetreuung der Einrichtung"),
            _("Systemadministration (User-Management ueber Django-Admin)"),
        ],
        "retention_period": _(
            "Konto-Stammdaten: bis zur Loeschung des Accounts. "
            "Login-Versuche und Lockouts: 90 Tage (Sicherheits-"
            "Forensik). Recovery-Codes bis zu deren Verbrauch oder "
            "Neuerstellung."
        ),
        "toms": [
            _("Passwort-Hashing mit aktuellen Verfahren (PBKDF2/Argon2)"),
            _("Rate-Limiting auf Login-Versuche (max. 5/Minute pro IP)"),
            _("Account-Lockout nach 10 Fehlversuchen"),
            _("Optionale 2FA per TOTP (RFC 6238)"),
            _("Recovery-Codes als Self-Service-Backup"),
            _("Sudo-Mode fuer destruktive Aktionen (15 Min Re-Auth)"),
            _("HTTPS-only mit HSTS"),
        ],
    },
    {
        "id": "backup",
        "title": _("Backup-Daten"),
        "purpose": _(
            "Wiederherstellbarkeit der Anwendung im Stoerfall (DSGVO "
            "Art. 32 Abs. 1 lit. c). Schutz vor Datenverlust durch "
            "Hardware-Defekt, Bedienfehler oder Ransomware."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. f (berechtigtes Interesse an "
            "Verfuegbarkeit) i.V.m. Art. 32 (Sicherheit der "
            "Verarbeitung)."
        ),
        "data_categories": [
            _("Vollstaendige PostgreSQL-Datenbankdumps"),
            _("File-Vault-Inhalte (verschluesselt)"),
            _("Konfigurationsdaten der Installation"),
        ],
        "recipients": [
            _("Intern: Systemadministration (Restore-Berechtigung)"),
            _("Backup-Speicherort gemaess Hosting-Vertrag (Auftragsverarbeiter, AV-Vertrag erforderlich)"),
        ],
        "retention_period": _(
            "Taegliche Backups, rollierend ueblicherweise 30 Tage; "
            "wochenweise Aufbewahrung bis 12 Wochen, monatlich bis "
            "12 Monate. Konkrete Frist gemaess Hosting-Setup der "
            "jeweiligen Installation."
        ),
        "toms": [
            _("Backup-Verschluesselung at-rest"),
            _("Getrennter Speicherort (offsite oder anderer Verfuegbarkeitsbereich)"),
            _("Reihenfolge Backup -> Retention -> Snapshots, sodass Loeschvorgaenge nicht im Backup ueberleben"),
            _("Regelmaessige Restore-Tests"),
            _("Zugriff nur fuer Systemadministration (4-Augen empfohlen bei Restore)"),
        ],
    },
    {
        "id": "dsgvo_requests",
        "title": _("DSGVO-Auskunfts- und Berichtigungsantraege"),
        "purpose": _(
            "Bearbeitung der Betroffenenrechte nach DSGVO Art. 15-22 "
            "(Auskunft, Berichtigung, Loeschung, Einschraenkung, "
            "Datenuebertragbarkeit, Widerspruch). Erstellung von "
            "Datenexport-Paketen und Dokumentation der Bearbeitung."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. c (rechtliche Verpflichtung) "
            "i.V.m. Art. 12-22 (Betroffenenrechte). SGB X § 83 "
            "(Auskunft an Betroffene)."
        ),
        "data_categories": [
            _("Antragsteller:in (sofern identifiziert)"),
            _("Antrags-Typ (Auskunft, Berichtigung, Loeschung, ...)"),
            _("Bearbeitungs-Status und -datum"),
            _("Erzeugte Datenpakete (JSON, PDF) mit allen personenbezogenen Daten der Person"),
            _("Korrespondenz / Aktenvermerke der Anwendungsbetreuung"),
        ],
        "recipients": [
            _("Anwendungsbetreuung der Einrichtung (Bearbeitung)"),
            _("Leitung der Einrichtung (4-Augen bei Loeschung qualifizierter Daten)"),
            _("Antragsteller:in (Auslieferung des Datenpakets)"),
        ],
        "retention_period": _(
            "Bearbeitungsvorgaenge: 3 Jahre nach Abschluss "
            "(Nachweis der Erfuellung). Erzeugte Datenpakete: nach "
            "Abholung loeschen. Berichtigungs-/Loeschungsvorgaenge: "
            "im AuditLog dauerhaft (siehe Verarbeitung 'AuditLog')."
        ),
        "toms": [
            _("Berichtigung organisatorisch ueber Mitarbeiter:innen / Leitung — kein Self-Service durch Klient:in"),
            _("Vier-Augen-Prinzip fuer Loeschung qualifizierter Daten"),
            _("Sudo-Mode-Re-Auth vor Erstellung von Datenpaketen"),
            _("AuditLog jeder Auskunft / Berichtigung / Loeschung"),
            _("Verschluesselte Auslieferung der Datenpakete"),
        ],
    },
]


def get_processing_activities():
    """Liefert die Liste aller registrierten Verarbeitungstaetigkeiten.

    Rueckgabe ist ein flacher Iterable-Snapshot — nicht die interne
    Konstante. Aenderungen an der Rueckgabe wirken sich nicht auf
    nachfolgende Aufrufer aus.
    """
    return list(PROCESSING_ACTIVITIES)


def get_activity(activity_id):
    """Liefert die Verarbeitungstaetigkeit mit der gegebenen ``id``.

    Rueckgabe: das Dict aus :data:`PROCESSING_ACTIVITIES` oder ``None``,
    falls keine Taetigkeit mit dieser ID registriert ist.
    """
    for activity in PROCESSING_ACTIVITIES:
        if activity["id"] == activity_id:
            return activity
    return None

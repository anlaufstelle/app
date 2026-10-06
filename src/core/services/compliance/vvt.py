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

Where domain details are unclear, the wording is deliberately
conservative. The backup retention mirrors the rotation of the shipped
``scripts/ops/backup.sh``; the facility must maintain its own version
aligned with its hosting setup (off-site retention in particular).

Die Liste umfasst mindestens die sechs Pflicht-Verarbeitungs-
taetigkeiten aus dem Auftrag (Refs #876).

Strings sind via :func:`django.utils.translation.gettext_lazy` lazy
uebersetzbar (Refs #878).
"""

from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.models.settings import (
    DEFAULT_RETENTION_ANONYMOUS_DAYS,
    DEFAULT_RETENTION_IDENTIFIED_DAYS,
    DEFAULT_RETENTION_QUALIFIED_DAYS,
)

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
        "title": _("Personen-Stammdaten"),
        "purpose": _(
            "Beratungsdokumentation und Falllaufzeit-Steuerung in "
            "niedrigschwelligen Einrichtungen. Kein Klarname-Feld — der "
            "primaere Identifikator ist ein Pseudonym."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. e (Wahrnehmung einer Aufgabe im "
            "oeffentlichen Interesse). Einrichtungsspezifische Grundlage: "
            "einschlaegiges Fach- und Sozialrecht der Einrichtung "
            "(konkrete Rechtsgrundlage vor Freigabe ergaenzen)."
        ),
        "data_categories": [
            _("Pseudonym (kein Klarname)"),
            _("Soziodemografische Eckdaten (Altersgruppe; Geschlecht ggf. über Zusatzfelder erhoben)"),
            _("Kontaktstufe (anonym / nur Pseudonym / mit Zusatzangaben)"),
            _("Zugeordnete Einrichtung (Facility)"),
        ],
        "recipients": [
            _("Intern: Mitarbeiter:innen der jeweiligen Einrichtung (facility-gescopt, kein Cross-Facility-Zugriff)"),
            _("Anwendungsbetreuung der Einrichtung (Audit, DSGVO-Paket)"),
        ],
        "retention_period": format_lazy(
            # Refs #2425: the period starts per event (``occurred_at``) or at the
            # close of the linked case (``closed_at``), see
            # ``core.retention.enforcement``; a newer contact extends nothing.
            # Refs #2463: ``enforce_qualified`` only reaches events of a closed
            # case, so events without a case or in an open case never expire
            # through the stage period. ``anonymize_clients`` only takes a
            # person with at least one event and without an active hold on the
            # person, one of their cases or events.
            _(
                "Konfigurierbar pro Kontaktstufe; eine Frist am "
                "Dokumentationstyp gilt zusätzlich (es gilt die zuerst "
                "ablaufende). Standardfristen je Ereignis: Anonyme Kontakte: "
                "{anonymous_days} Tage ab dem jeweiligen Kontakt; Kontakte der "
                "Stufe „Nur Pseudonym“: {identified_days} Tage ab dem "
                "jeweiligen Kontakt; Kontakte der Stufe „Mit Zusatzangaben“: "
                "{qualified_days} Tage ab Abschluss des zugeordneten Falls; "
                "Ereignisse dieser Stufe ohne Fall oder in offenen Fällen "
                "verfallen nicht über diese Frist, nur über eine Frist am "
                "Dokumentationstyp. Der tägliche Aufbewahrungslauf löscht ohne "
                "Freigabeschritt; eine Person mit mindestens einem Ereignis "
                "anonymisiert er, sobald alle ihre Ereignisse gelöscht sind und "
                "keine Aufbewahrungssperre (Legal Hold) auf der Person, einem "
                "ihrer Fälle oder Ereignisse besteht."
            ),
            anonymous_days=DEFAULT_RETENTION_ANONYMOUS_DAYS,
            identified_days=DEFAULT_RETENTION_IDENTIFIED_DAYS,
            qualified_days=DEFAULT_RETENTION_QUALIFIED_DAYS,
        ),
        "toms": [
            _("Pseudonymisierung by Design"),
            _("Row Level Security (RLS) pro Facility"),
            _("Verschluesselung at-rest (AES-GCM) sensibler Felder"),
            # Refs #1862: Hier stand "AuditLog auf Lese- und Schreibzugriffe"
            # — eine unzutreffende Angabe nach Art. 30 Abs. 1 lit. g DSGVO.
            # Protokolliert wird jede Aenderung, von Lesezugriffen aber nur
            # ``VIEW_QUALIFIED`` (``views/clients.py``), ``EXPORT``,
            # ``DOWNLOAD``, ``OFFLINE_*_READ`` und ``SYSTEM_VIEW``. Listen-,
            # Detail- und Suchaufrufe erzeugen keinen Eintrag; dass die
            # Volltextsuche bewusst unprotokolliert bleibt, entscheidet
            # ADR-043.
            _(
                "AuditLog auf jede Aenderung; von Lesezugriffen nur die "
                "Personen-Detailseite der Stufe „Mit Zusatzangaben“, Auskunft "
                "und Export, Datei-Download, Offline-Mitnahme sowie "
                "facility-uebergreifende Zugriffe — sonstige Listen-, Detail- "
                "und Suchaufrufe werden nicht protokolliert (ADR-043)"
            ),
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
            "DSGVO Art. 6 Abs. 1 lit. e; Art. 9 Abs. 2 lit. b/h fuer "
            "besondere Datenkategorien (Gesundheits-, Sozialdaten). "
            "Einrichtungsspezifische Grundlage: einschlaegiges Fach- und "
            "Sozialrecht der Einrichtung (konkrete Rechtsgrundlage vor "
            "Freigabe ergaenzen). § 203 StGB, soweit anwendbar."
        ),
        "data_categories": [
            _("Ereignisbeschreibungen / Beratungsnotizen"),
            _("Strukturierte Feldwerte (je Dokumentationstyp konfiguriert)"),
            _("Kontaktanlass, Themen, Vermittlungen"),
            _("Ggf. Gesundheits-/Suchtkontext (verschluesselt)"),
            _("Zeitstempel, Bearbeiter:in, Sensitivitaetsstufe"),
        ],
        "recipients": [
            _("Intern: Mitarbeiter:innen der Einrichtung mit passender Rolle und Sensitivitaetsfreigabe"),
            _(
                "Keine Weitergabe an Dritte — pseudonymisierte Statistik ggf. an "
                "Foerdermittelgeber/Behoerden (Sachbericht-Export)"
            ),
        ],
        "retention_period": _(
            "Pro Dokumentationstyp konfigurierbar; Default an Kontaktstufe "
            "der zugehoerigen Person gebunden. Sensible Inhalte werden "
            "vor Loeschung k-anonymisiert oder redigiert."
        ),
        "toms": [
            _("Sensitivitaets-basierte Sichtbarkeit (je Dokumentationstyp, einzelne Feldvorlagen koennen sie anheben)"),
            _("Verschluesselung sensibler Felder (AES-GCM, Schluessel ausserhalb der DB)"),
            _("Optimistic Locking gegen versehentliches Ueberschreiben"),
            # Refs #1862: Hier stand "AuditLog jeder Lese- und Schreibaktion".
            # Der Ersatz muss drei Dinge genau treffen, sonst ersetzt er eine
            # unzutreffende Art.-30-Angabe nur durch die naechste:
            #
            # 1. **Schreibzugriffe sind nicht deckungsgleich.** Ereignisse
            #    kennen Anlegen (``EVENT_CREATE``), Bearbeiten
            #    (``EVENT_UPDATE``) und Loeschen (``DELETE``,
            #    ``services/events/crud.py``). Ein **Fall** kennt
            #    CASE_CREATE/UPDATE/CLOSE/REOPEN — und **keine** Loeschung:
            #    kein Produktionspfad setzt ``is_deleted`` je auf einem Fall,
            #    ``DeletionRequest.TargetType`` kennt nur ``EVENT``/``CLIENT``
            #    (``services/case/cases.py``, ADR-039 §6). Eine
            #    ``AuditLog.Action.CASE_DELETE`` gibt es folgerichtig nicht.
            # 2. **Episoden und Wirkungsziele sind seit #1959 protokolliert.**
            #    Bis dahin sagte diese TOM "gar nicht protokolliert" — die
            #    damals einzige zutreffende Aussage: ``models/case.py``
            #    (``create_episode``) und ``services/case/goals.py``
            #    speicherten nur, ohne ``audit_event`` und ohne
            #    ``log_activity``. Mit #1959 schreiben ``update_episode``
            #    (``services/case/episodes.py``) und ``update_goal``
            #    ``EPISODE_UPDATE``/``GOAL_UPDATE``, die Ereignis-Zuordnung
            #    ``CASE_EVENT_ASSIGNED``/``CASE_EVENT_REMOVED``. Die Angabe
            #    zaehlt deshalb jetzt auf, statt zu verneinen. **Nicht**
            #    dazugekommen sind Anlegen, Abschliessen und Erreichen —
            #    ebenso wenig eine ``EventHistory`` (die gibt es weiterhin
            #    ausschliesslich fuer Event-Objekte) oder ein Eintrag im
            #    Aktivitaets-Feed; die TOM nennt darum ausdruecklich nur die
            #    Bearbeitung.
            # 3. **Von Lesezugriffen** bleiben nur ``EXPORT``, ``DOWNLOAD``,
            #    ``OFFLINE_*_READ`` und ``SYSTEM_VIEW``; Detail-, Listen- und
            #    Suchaufrufe erzeugen keinen Eintrag (ADR-043).
            #
            # Refs #1818 (ADR-044): Der frueher hier stehende Klammerzusatz „ein
            # Fall kann nicht geloescht werden" ist mit den Fall-Loesch-Routen
            # (``views/case_deletion.py``, Antrag/Papierkorb/Wiederherstellung/
            # Tilgung) hinfaellig geworden und faellt in derselben Aenderung
            # (DE + EN + .mo). Er wird nicht durch eine Gegenaussage ersetzt,
            # sondern durch die drei Aktionen, die es jetzt gibt — eine
            # Art.-30-Angabe zaehlt auf, was protokolliert wird.
            _(
                "AuditLog auf Anlegen, Bearbeiten und Loeschen von Ereignissen; "
                "auf Anlegen, Bearbeiten, Schliessen, Wiedereroeffnen, Loeschen, "
                "Wiederherstellen und Anonymisieren von Faellen; auf das "
                "Bearbeiten von Episoden und Wirkungszielen sowie das Zuordnen "
                "und Loesen von Ereignissen zu Faellen; von Lesezugriffen nur "
                "Export, Datei-Download, Offline-Mitnahme und "
                "facility-uebergreifende Zugriffe — Detail-, Listen- und "
                "Suchaufrufe werden nicht protokolliert (ADR-043)"
            ),
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
            _("Unveraenderbarkeit per DB-Trigger erzwungen (UPDATE/DELETE blockiert)"),
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
        # Refs #2464: the minimum ages mirror the ``-mtime +7/+28/+90`` values
        # in ``scripts/ops/backup.sh`` (``find`` counts full days only, and the
        # rotation only runs in a successful run); the off-site target is only
        # ever added to.
        "retention_period": _(
            "Lokale Sicherungsstände entfernt backup.sh bei seinem ersten "
            "erfolgreichen Lauf, in dem sie mindestens 8 (täglich), 29 "
            "(wöchentlich) bzw. 91 volle Tage (monatlich) alt sind. Das ist "
            "eine Altersgrenze, keine feste Höchstfrist. Nach einem "
            "fehlgeschlagenen Off-Site-Abgleich ist die lokale Rotation "
            "ausgesetzt, bis ein Abgleich wieder gelingt. Off-Site-Kopien "
            "löscht backup.sh nicht; ihre Frist legt die Einrichtung über "
            "eine Aufbewahrungsregel am Speicherort fest."
        ),
        "toms": [
            _("Backup-Verschluesselung at-rest"),
            _("Getrennter Speicherort (offsite oder anderer Verfuegbarkeitsbereich)"),
            _(
                "Sicherung vor dem Aufbewahrungslauf: Ein Sicherungsstand enthält "
                "auch die Daten, die der folgende Lauf löscht; sie verschwinden "
                "lokal erst mit der Rotation dieses Sicherungsstands, in "
                "Off-Site-Kopien erst durch die Aufbewahrungsregel am Ziel"
            ),
            _("Regelmaessige Restore-Tests"),
            _("Zugriff nur fuer Systemadministration (4-Augen empfohlen bei Restore)"),
        ],
    },
    {
        "id": "dsgvo_requests",
        "title": _("DSGVO-Auskunfts- und Berichtigungsantraege"),
        "purpose": _(
            "Technische Unterstuetzung der implementierten Betroffenenrechte "
            "nach DSGVO Art. 15, 16, 17 und 20 (Auskunft, Berichtigung, "
            "Loeschung und Datenuebertragbarkeit). Antraege auf Einschraenkung "
            "(Art. 18) oder Widerspruch (Art. 21) werden ausserhalb der "
            "Anwendung im organisatorischen Prozess der Einrichtung "
            "aufgenommen; die Anwendung stellt dafuer kein Intake-Formular "
            "bereit. Erstellung von Datenexport-Paketen und Dokumentation der "
            "Bearbeitung."
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
            _(
                "Berichtigung organisatorisch ueber Mitarbeiter:innen / Leitung — "
                "kein Self-Service durch die betroffene Person"
            ),
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

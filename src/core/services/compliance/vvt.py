"""Verzeichnis Verarbeitungstaetigkeiten (VVT) gem. DSGVO Art. 30.

Statisches Verzeichnis aller Verarbeitungstaetigkeiten der Anlaufstelle-
Installation. Wird im ``/system/vvt/``-Bereich (Refs #876) read-only
angezeigt und kann fuer Audits / Datenschutzerklaerungen herangezogen
werden.

Quellen:

* `docs/fachkonzept-anlaufstelle.md` — sections "Datenschutz als Grund-
  prinzip" (§ 6) and "Sicherheit" (§ 17): name the TOMs (RLS, Fernet field
  encryption, audit trail, MFA, ClamAV, pseudonymisation).
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
            "primäre Identifikator ist ein Pseudonym."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. e (Wahrnehmung einer Aufgabe im "
            "öffentlichen Interesse). Einrichtungsspezifische Grundlage: "
            "einschlägiges Fach- und Sozialrecht der Einrichtung "
            "(konkrete Rechtsgrundlage vor Freigabe ergänzen)."
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
            # Keep stage anchors and approval wording aligned with the retention services.
            _(
                "Konfigurierbare Ereignisfristen, ergänzt durch die Höchstfrist (verkürzt nur) am Dokumentationstyp. "
                "Anonym: {anonymous_days} Tage ab Ereignisdatum; Nur Pseudonym: {identified_days} Tage ab "
                "Ereignisdatum; Mit Zusatzangaben: {qualified_days} Tage ab Fallabschluss, ohne Fall ab "
                "Ereignisdatum, im offenen Fall ab letzter fachlicher Fallaktivität (Fallback Anlage). Der "
                "Standardlauf legt für Mit Zusatzangaben Vorschläge an. Den Vorschlag legt der Lauf an. Die manuelle "
                "Freigabe braucht zwei verschiedene aktive Personen der eigenen Einrichtung, jeweils mit Rolle "
                "Leitung oder Anwendungsbetreuung und dem Recht „Löschbestätigung“. Die erste Freigabe lässt den "
                "Vorschlag ausstehend (PENDING, „1 von 2 Freigaben“); erst die zweite andere Person gibt ihn "
                "endgültig frei (APPROVED). Mit nur einem berechtigten Konto bleibt es bei „1 von 2“; die erste "
                "Freigabe allein erlaubt keine Löschung. Zurückstellen, Legal Hold (auch aus der Personen-, Fall- "
                "oder Ereignisakte) und Ablehnung verwerfen die erste Freigabe mit Audit; bei einem erneuten "
                "manuellen Freigabeweg sind wieder zwei Freigaben nötig. Die Einrichtung benennt mindestens zwei "
                "berechtigte Konten. Die Compliance-Karte „Genehmiger für den Aufbewahrungslauf“ prüft Rolle und "
                "Anzahl (0 = kritisch, 1 = Warnung, mindestens 2 = ok). Die Karte „Genehmiger für Löschanträge“ zählt "
                "weiterhin aktive Löschantragsberechtigte unabhängig von ihrer Rolle. Ausnahme: Die Einrichtung kann "
                "die Auto-Freigabe nach mehrfacher Zurückstellung ausdrücklich einschalten (Opt-in, standardmäßig "
                "aus); nach der konfigurierten maximalen Zahl von Zurückstellungen und Ablauf der Wiedervorlage "
                "genehmigt der Lauf ohne zweite Person. Hold und Art. 18 schützen auch diesen Weg. Zurückstellen "
                "schützt bis zum Datum; Ablehnen bis zu einer neuen Vorlage frühestens nach der konfigurierten "
                "Wiedervorlagefrist (Vorgabe 365 Tage). Personen werden anonymisiert, sobald alle Ereignisse gelöscht "
                "sind; ohne Ereignis nur für „Nur Pseudonym“ ohne offene Fälle nach {identified_days} Tagen ab dem "
                "jüngsten Zeitpunkt aus Personenänderung, fachlicher Fallaktivität, Aufgabenänderung und "
                "Anhangsänderung an Person oder Fall (Anlage, Ersetzung oder Entfernung). Personen „Mit "
                "Zusatzangaben“ ohne Ereignis sind von der automatischen Anonymisierung ausgenommen; der "
                "Vorschlagsweg unterstützt bisher nur Ereignisse, eine automatische Fristvollstreckung für diese "
                "Personen fehlt. Hold und Art. 18 stellen zurück."
            ),
            anonymous_days=DEFAULT_RETENTION_ANONYMOUS_DAYS,
            identified_days=DEFAULT_RETENTION_IDENTIFIED_DAYS,
            qualified_days=DEFAULT_RETENTION_QUALIFIED_DAYS,
        ),
        "toms": [
            _("Pseudonymisierung by Design"),
            _("Row Level Security (RLS) pro Facility"),
            _("Verschlüsselung at-rest sensibler Felder (Fernet: AES-128-CBC mit HMAC-SHA256)"),
            # Refs #1862: Hier stand "AuditLog auf Lese- und Schreibzugriffe"
            # — eine unzutreffende Angabe nach Art. 30 Abs. 1 lit. g DSGVO.
            # Protokolliert wird jede Aenderung, von Lesezugriffen aber nur
            # ``VIEW_QUALIFIED`` (``views/clients.py``), ``EXPORT``,
            # ``DOWNLOAD``, ``OFFLINE_*_READ`` und ``SYSTEM_VIEW``. Listen-,
            # Detail- und Suchaufrufe erzeugen keinen Eintrag; dass die
            # Volltextsuche bewusst unprotokolliert bleibt, entscheidet
            # ADR-043.
            _(
                "AuditLog auf jede Änderung; von Lesezugriffen nur die "
                "Personen-Detailseite der Stufe „Mit Zusatzangaben“, Auskunft "
                "und Export, Datei-Download, Offline-Mitnahme sowie "
                "facility-übergreifende Zugriffe — sonstige Listen-, Detail- "
                "und Suchaufrufe werden nicht protokolliert (ADR-043)"
            ),
            _("Rollenbasierte Zugriffskontrolle (5 Rollen)"),
            _(
                "MFA / TOTP per Rollen-Default für alle Rollen verpflichtend "
                "(Leitung, Fachkraft und Assistenz nach 30 Tagen Übergangsfrist)"
            ),
        ],
    },
    {
        "id": "falldaten",
        "title": _("Falldaten (Events, Episoden, Dokumentationen)"),
        "purpose": _(
            "Zeitstrom-basierte Dokumentation von Kontakten, Beratungen "
            "und Hilfeprozessen. Strukturierung zusammenhängender "
            "Arbeit zu Fällen und Episoden. Grundlage der Wirkungs"
            "messung und Berichterstattung gegenüber Fördermittel"
            "gebern."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. e; Art. 9 Abs. 2 lit. b/h für "
            "besondere Datenkategorien (Gesundheits-, Sozialdaten). "
            "Einrichtungsspezifische Grundlage: einschlägiges Fach- und "
            "Sozialrecht der Einrichtung (konkrete Rechtsgrundlage vor "
            "Freigabe ergänzen). § 203 StGB, soweit anwendbar."
        ),
        "data_categories": [
            _("Ereignisbeschreibungen / Beratungsnotizen"),
            _("Strukturierte Feldwerte (je Dokumentationstyp konfiguriert)"),
            _("Kontaktanlass, Themen, Vermittlungen"),
            _("Ggf. Gesundheits-/Suchtkontext (je nach Feldkonfiguration verschlüsselt)"),
            _("Zeitstempel, Bearbeiter:in, Sensitivitätsstufe"),
        ],
        "recipients": [
            _("Intern: Mitarbeiter:innen der Einrichtung mit passender Rolle und Sensitivitätsfreigabe"),
            _(
                "Keine Weitergabe an Dritte — pseudonymisierte Statistik ggf. an "
                "Fördermittelgeber/Behörden (Sachbericht-Export)"
            ),
        ],
        # Refs #2462: the document type period is an additional strategy
        # (``enforce_document_type_retention``), not a replacement of the
        # stage period. ``_soft_delete_events`` empties the content at once;
        # k-anonymity only exists in the reporting layer, not in retention.
        # Refs #2492: ``enforce_eventhistory_retention`` keeps the newest
        # version of a living event (``_aged_living_event_rows``) unless the
        # person is anonymised (``_anonymized_client_rows`` redacts every
        # version regardless of the period), defers held rows, and treats
        # ``months <= 0`` as "period off"; ``enforce_handover_notes`` deletes hard and has no hold
        # branch. Defaults: ``eventhistory_*_months`` 24,
        # ``retention_handover_notes_days`` 90.
        "retention_period": _(
            "Ereignisse folgen den Fristen der Kontaktstufe ihrer Person "
            "(siehe Verarbeitung „Personen-Stammdaten“); eine Frist am "
            "Dokumentationstyp gilt zusätzlich, es gilt die zuerst "
            "ablaufende. Die Löschung setzt das Ereignis auf gelöscht: "
            "Zusatzfelder und Suchtext werden sofort geleert, Dateianhänge "
            "gelöscht; die Werte früherer Fassungen redigiert regulär der "
            "nächste Aufbewahrungslauf. Bei nicht gelöschten Ereignissen "
            "redigiert er die Werte früherer Fassungen nach 24 Monaten "
            "(einstellbar, 0 schaltet die Frist ab); die jüngste Fassung "
            "behält ihre Werte, außer bei Ereignissen einer anonymisierten "
            "Person: dort redigiert er alle Fassungen unabhängig von der "
            "Frist. Die Fassung selbst (wer, wann, welche Felder) bleibt, bis "
            "er sie 24 Monate nach der Redaktion löscht (einstellbar, 0 "
            "schaltet die Frist ab). Fälle haben "
            "keine eigene Frist, Episoden und Wirkungsziele folgen ihrem "
            "Fall. Daten unter einer Aufbewahrungssperre (Legal Hold) sind "
            "ausgenommen. Übergabenotizen löscht der Aufbewahrungslauf "
            "standardmäßig 90 Tage nach ihrer letzten Änderung endgültig "
            "(einstellbar); für sie gibt es keinen Legal Hold."
        ),
        "toms": [
            _("Sensitivitäts-basierte Sichtbarkeit (je Dokumentationstyp, einzelne Feldvorlagen können sie anheben)"),
            _("Verschlüsselung sensibler Felder (Fernet: AES-128-CBC mit HMAC-SHA256, Schlüssel außerhalb der DB)"),
            _("Optimistic Locking gegen versehentliches Überschreiben"),
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
                "AuditLog auf Anlegen, Bearbeiten und Löschen von Ereignissen; "
                "auf Anlegen, Bearbeiten, Schließen, Wiedereröffnen, Löschen, "
                "Wiederherstellen und Anonymisieren von Fällen; auf das "
                "Bearbeiten von Episoden und Wirkungszielen sowie das Zuordnen "
                "und Lösen von Ereignissen zu Fällen; von Lesezugriffen nur "
                "Export, Datei-Download, Offline-Mitnahme und "
                "facility-übergreifende Zugriffe — Detail-, Listen- und "
                "Suchaufrufe werden nicht protokolliert (ADR-043)"
            ),
            # Refs #2492: four eyes only where the code asks for a second
            # person (``client_deletion``, ``case/deletion``, event deletion
            # of qualified persons); ``AttachmentPurgeView`` is lead/admin
            # without a request.
            _(
                "Soft-Delete; Vier-Augen-Prinzip für manuelle Löschungen von "
                "Personen sowie von Ereignissen und Fällen der Stufe „Mit "
                "Zusatzangaben“; Dateien vernichten Leitung und "
                "Anwendungsbetreuung ohne zweite Person"
            ),
            _("Encrypted File Vault mit ClamAV-Scan fail-closed für Anhänge"),
        ],
    },
    {
        "id": "auditlog",
        "title": _("Audit-Log (Zugriffs- und Änderungsprotokoll)"),
        "purpose": _(
            "Nachweis der Rechenschaftspflicht (DSGVO Art. 5 Abs. 2). "
            "Erkennung von Missbrauch, Beweismittel im Datenleck-Fall, "
            "Rekonstruktion von Änderungen. Einträge werden nur angehängt "
            "und im laufenden Betrieb weder geändert noch gelöscht; nach "
            "Ablauf der Audit-Frist löscht sie der tägliche "
            "Aufbewahrungslauf (siehe Speicherfrist)."
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
            _("Zugehörige Einrichtung (oder NULL bei System-Events)"),
        ],
        "recipients": [
            _("Anwendungsbetreuung der Einrichtung (Facility-Audit-Log)"),
            _(
                "Systemadministration (Cross-Facility-Audit über "
                "/system/audit/, mit eigenem SYSTEM_VIEW-Eintrag pro "
                "Aufruf)"
            ),
        ],
        # Facility and system chains have separate periods; held targets survive pruning.
        "retention_period": _(
            "Standardmäßig 24 Monate je Einrichtung; Einträge ohne Einrichtung ebenfalls "
            "24 Monate installationsweit (AUDITLOG_SYSTEM_RETENTION_MONTHS). Jeweils 0 = aus. "
            "Der tägliche Aufbewahrungslauf lässt Audit-Zeilen mit Einrichtung zu Zielen "
            "unter aktivem Legal Hold "
            "stehen. Sicherheitsverstöße, System-Marker des Aufbewahrungslaufs, Nachweise der "
            "Historien-Redaktion (Ereignis-Historie) und herausgegebener Berichtszeiträume bleiben dauerhaft. "
            "Die Audit-Frist gilt unabhängig von der Lebensdauer der Fachdaten; "
            "Anonymisierung redigiert die Audit-Zeilen nicht."
        ),
        "toms": [
            # Refs #2492: ``prune_auditlog`` deletes expired rows inside
            # ``bypass_replication_triggers``; ``backfill_audit_chain`` uses the
            # same bypass to rewrite ``prev_hash``/``entry_hash`` only. The
            # promise names both paths.
            _(
                "Append-Only durch DB-Konvention und RLS-Policies; Ausnahme ist "
                "das Löschen abgelaufener Einträge durch den Aufbewahrungslauf"
            ),
            _(
                "Unveränderbarkeit per DB-Trigger erzwungen (UPDATE/DELETE "
                "blockiert); der Aufbewahrungslauf umgeht den Trigger "
                "transaktional, um abgelaufene Einträge zu löschen, die "
                "Wartungsfunktion für die Hash-Kette, um nur deren Prüfwerte "
                "neu zu setzen; fachliche Inhalte ändert keiner der beiden Wege"
            ),
            _("Zugriff nur für Anwendungsbetreuung und Systemadministration"),
            _("Cross-Facility-Auswertung bedingt eigenen SYSTEM_VIEW-Audit"),
            _("Backup täglich (siehe Verarbeitung 'Backup-Daten')"),
        ],
    },
    {
        "id": "auth_login",
        "title": _("Login- und Authentifizierungsdaten"),
        "purpose": _(
            "Authentifizierung der Mitarbeiter:innen, Schutz gegen "
            "Brute-Force, Sitzungsverwaltung. Nachweis für "
            "Datenschutz-Vorfallprüfung."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. b (Vertragserfüllung — Arbeitsvertrag) und Art. 32 (Sicherheit der Verarbeitung)."
        ),
        "data_categories": [
            _("Username, E-Mail-Adresse"),
            _("Passwort-Hash (PBKDF2, niemals Klartext)"),
            _("MFA-Gerätesecret (TOTP, verschlüsselt)"),
            _("Backup-Codes (Hash)"),
            _("Login-Versuche (Erfolg/Fehler) inkl. IP und Zeitstempel"),
            _("Account-Lockout-Status"),
        ],
        "recipients": [
            _("Intern: Anwendungsbetreuung der Einrichtung"),
            _("Systemadministration (User-Management über Django-Admin)"),
        ],
        # Refs #2492: ``login_lockout.is_locked`` derives the lockout from
        # ``LOGIN_FAILED`` audit rows; there is no separate attempt table and
        # no 90-day sweep.
        "retention_period": _(
            "Konto-Stammdaten: bis zur Löschung des Accounts. "
            "Login-Versuche und Kontosperren: als Audit-Einträge, aus denen "
            "die Sperre berechnet wird; sie folgen der Audit-Frist (siehe "
            "Verarbeitung 'Audit-Log'), Fehlversuche unter unbekanntem "
            "Benutzernamen und von Konten ohne Einrichtung folgen der installationsweiten "
            "Audit-Frist (Vorgabe 24 Monate, 0 = aus). Recovery-Codes bis zu deren Verbrauch oder "
            "Neuerstellung."
        ),
        "toms": [
            _("Passwort-Hashing mit aktuellem Verfahren (PBKDF2)"),
            _("Rate-Limiting auf Login-Versuche (max. 5/Minute je IP, 10/Stunde je Benutzername)"),
            _(
                "Kontosperre nach 10 Fehlversuchen in 15 Minuten je Konto und IP "
                "(Systemadministration ausgenommen, für sie gelten die Ratelimits)"
            ),
            _("2FA per TOTP (RFC 6238), per Rollen-Default verpflichtend"),
            _("Recovery-Codes als Self-Service-Backup"),
            _("Sudo-Mode für destruktive Aktionen (15 Min Re-Auth)"),
            _("HTTPS-only mit HSTS"),
        ],
    },
    {
        "id": "backup",
        "title": _("Backup-Daten"),
        "purpose": _(
            "Wiederherstellbarkeit der Anwendung im Störfall (DSGVO "
            "Art. 32 Abs. 1 lit. c). Schutz vor Datenverlust durch "
            "Hardware-Defekt, Bedienfehler oder Ransomware."
        ),
        "legal_basis": _(
            "DSGVO Art. 6 Abs. 1 lit. f (berechtigtes Interesse an "
            "Verfügbarkeit) i.V.m. Art. 32 (Sicherheit der "
            "Verarbeitung)."
        ),
        "data_categories": [
            _("Vollständige PostgreSQL-Datenbankdumps"),
            _("File-Vault-Inhalte (verschlüsselt)"),
            _("Konfigurationsdaten der Installation"),
        ],
        "recipients": [
            _("Intern: Systemadministration (Restore-Berechtigung)"),
            _("Backup-Speicherort gemäß Hosting-Vertrag (Auftragsverarbeiter, AV-Vertrag erforderlich)"),
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
            _("Backup-Verschlüsselung at-rest"),
            _("Getrennter Speicherort (offsite oder anderer Verfügbarkeitsbereich)"),
            _(
                "Sicherung vor dem Aufbewahrungslauf: Ein Sicherungsstand enthält "
                "auch die Daten, die der folgende Lauf löscht; sie verschwinden "
                "lokal erst mit der Rotation dieses Sicherungsstands, in "
                "Off-Site-Kopien erst durch die Aufbewahrungsregel am Ziel"
            ),
            _("Regelmäßige Restore-Tests"),
            _("Zugriff nur für Systemadministration (4-Augen empfohlen bei Restore)"),
        ],
    },
    {
        "id": "dsgvo_requests",
        "title": _("DSGVO-Auskunfts- und Berichtigungsanträge"),
        "purpose": _(
            "Technische Unterstützung der implementierten Betroffenenrechte "
            "nach DSGVO Art. 15, 16, 17 und 20 (Auskunft, Berichtigung, "
            "Löschung und Datenübertragbarkeit). Anträge auf Einschränkung "
            "(Art. 18) oder Widerspruch (Art. 21) werden außerhalb der "
            "Anwendung im organisatorischen Prozess der Einrichtung "
            "aufgenommen; die Anwendung stellt dafür kein Intake-Formular "
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
            _("Antrags-Typ (Auskunft, Berichtigung, Löschung, ...)"),
            _("Bearbeitungs-Status und -datum"),
            _("Erzeugte Datenpakete (JSON, PDF) mit allen personenbezogenen Daten der Person"),
            _("Korrespondenz / Aktenvermerke der Anwendungsbetreuung"),
        ],
        "recipients": [
            _("Anwendungsbetreuung der Einrichtung (Bearbeitung)"),
            _(
                "Leitung der Einrichtung (4-Augen bei der Löschung von Personen "
                "sowie von Ereignissen und Fällen der Stufe „Mit Zusatzangaben“)"
            ),
            _("Antragsteller:in (Auslieferung des Datenpakets)"),
        ],
        # Refs #2492: no code path deletes ``DeletionRequest`` or
        # ``RectificationRequest`` rows. Anonymising a person (manual and
        # retention run, ``services/client/main.py`` ``_redact_*`` helpers,
        # ``retention/anonymization.py``) empties the rectification texts and
        # sets the reasons of the person's deletion requests and legal holds
        # to "[Anonymisiert]"; proven by the residue sweeps in
        # ``test_pii_residue_sweep.py``. The audit rows of these actions are
        # not in ``PRUNE_EXEMPT_ACTIONS``.
        "retention_period": _(
            "Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge werden nach "
            "deletion_request_retention_days (Vorgabe 1095 Tage) ab Abschluss hart gelöscht; "
            "offene Anträge bleiben. Berichtigungsanträge haben weiterhin keine technische Frist. "
            "Anonymisierung redigiert deren Inhalte und die Begründungen zugehöriger Löschanträge "
            "und Aufbewahrungssperren zu „[Anonymisiert]“. Audit-Nachweise folgen ihrer eigenen Audit-Frist. "
            "Erzeugte Datenpakete "
            "sind nach Abholung organisatorisch zu löschen. Monatsaggregate bleiben unbefristet "
            "als statistischer Restbestand erhalten."
        ),
        "toms": [
            _(
                "Berichtigung organisatorisch über Mitarbeiter:innen / Leitung — "
                "kein Self-Service durch die betroffene Person"
            ),
            _(
                "Vier-Augen-Prinzip für die Löschung von Personen sowie von "
                "Ereignissen und Fällen der Stufe „Mit Zusatzangaben“"
            ),
            _("Sudo-Mode-Re-Auth vor Erstellung von Datenpaketen"),
            _("AuditLog jeder Auskunft / Berichtigung / Löschung"),
            _("Verschlüsselte Auslieferung der Datenpakete"),
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

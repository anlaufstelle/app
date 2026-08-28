# Informationspflichten für Beschäftigte (Art. 13/14 DSGVO, § 26 BDSG)

**Verantwortlicher:** {{ facility_name }}
**Erstellt:** {{ date }}
**Version:** 1.0 (Softwarestand v0.89, 2026-08-28)

---

## 1. Verantwortlicher und Datenschutzkontakt

{{ facility_name }}
[Adresse und Kontaktdaten eintragen]

Datenschutzbeauftragte:r:
[Name und Kontaktdaten eintragen]

## 2. Zwecke und Rechtsgrundlagen

Die Anwendung verarbeitet Beschäftigtendaten, soweit dies für Einrichtung,
Durchführung und Beendigung des Beschäftigungsverhältnisses sowie für einen
sicheren, nachvollziehbaren Systemzugang erforderlich ist:

- Benutzerkonto, Einrichtungszuordnung, Rollen und Berechtigungen verwalten;
- Anmeldung, Mehrfaktor-Authentifizierung und Kontosperren absichern;
- sicherheits- und fachbezogene Änderungen im Audit-Log nachweisen;
- Einladungs- und Sicherheitsnachrichten versenden.

Rechtsgrundlagen sind Art. 6 Abs. 1 lit. b DSGVO in Verbindung mit § 26 BDSG
für Zwecke des Beschäftigungsverhältnisses sowie Art. 6 Abs. 1 lit. c und
Art. 32 DSGVO für gesetzliche Nachweis- und Sicherheitsmaßnahmen. Soweit eine
andere Rechtsgrundlage erforderlich ist, ergänzt die Einrichtung sie vor
Ausgabe dieser Information.

## 3. Datenkategorien

- Benutzername, Anzeigename, E-Mail-Adresse und Rolle;
- Telefonnummer, Einrichtungszuordnung, Sprache und interne Kontonotiz, sofern
 diese Felder genutzt werden;
- Passwort-Hash, MFA-Pflicht und -Geräte, verschlüsselte TOTP-Secrets,
 gehashte Wiederherstellungscodes und Sicherheitsfristen;
- Anmelde-, Sperr- und Audit-Daten wie Zeitpunkt, Aktion, Konto- und
 Einrichtungskennung sowie bei protokollierten Vorgängen die IP-Adresse.

Die Anwendung ist kein Personalverwaltungssystem. Lohn-, Krankheits-,
Leistungsbeurteilungs- oder sonstige Personalaktendaten gehören nicht in freie
Kontonotizen.

## 4. Empfänger und Übermittlungen

Zugriff erhalten nur die nach dem Rollenkonzept zuständigen Personen der
Einrichtung und die Systemadministration. Hosting-, E-Mail-, Tracker- oder
Backup-Dienstleister erhalten Daten nur im Rahmen der jeweils vereinbarten
Auftragsverarbeitung.

Drittlandübermittlungen: [keine / Dienst, Land und geeignete Garantie
eintragen]. Die Einrichtung führt eingesetzte Auftrags- und
Unterauftragsverarbeiter in ihrem AVV beziehungsweise in dessen § 6.

## 5. Speicherdauer

- Bei Ausscheiden wird das Konto deaktiviert. Die im VVT vorgesehene Löschung
 nach drei Jahren ist ein organisatorischer Prozess und wird von der
 Anwendung nicht automatisch erzwungen; gesetzliche Aufbewahrungspflichten
 oder ein dokumentierter Sperrgrund können entgegenstehen.
- Reguläre Audit-Log-Einträge werden {{ auditlog_retention_months }} Monate
 rollierend aufbewahrt. Der Wert `0` schaltet diese Löschung ab;
 Sicherheitsverstöße und technische Nachweismarker können dauerhaft
 ausgenommen sein.
- Sitzungen enden nach den konfigurierten Inaktivitäts- und Absolutfristen;
 abgelaufene Datenbank-Sitzungen werden täglich bereinigt.
- MFA-Geräte und Wiederherstellungscodes bestehen bis Widerruf, Verbrauch oder
 Löschung des Kontos.

## 6. Rechte

Beschäftigte haben nach Maßgabe der gesetzlichen Voraussetzungen insbesondere
Rechte auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung,
Datenübertragbarkeit und Widerspruch sowie ein Beschwerderecht bei der
zuständigen Datenschutz-Aufsichtsbehörde.

Anlaufstelle für die Ausübung der Rechte:
[Kontakt und Verfahren eintragen]

## 7. Pflicht zur Bereitstellung und automatisierte Entscheidungen

Die für Konto und Berechtigungen erforderlichen Daten müssen bereitgestellt
werden, wenn die Anwendung dienstlich genutzt werden soll. Ohne sie ist ein
sicherer Zugang nicht möglich. Eine automatisierte Entscheidungsfindung oder
Profiling findet nicht statt.

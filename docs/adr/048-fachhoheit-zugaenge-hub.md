# ADR-048: Fachhoheit und Sicherheitsgrenzen des Zugänge-Hubs

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Tobias Nix
- **Refs:** #1871, [#1941](https://github.com/anlaufstelle/app/issues/1941#issuecomment-5385075873) (M4-Entscheid E4)

## Context

Benutzerkonten, Rollen, Einladungen und Account-Sperren wurden bisher im
technischen Django-Admin unter `/admin-mgmt/` gepflegt. Der Verwaltungs-Hub
trägt dagegen die fachliche Verantwortung einer Einrichtung und ist der
natürliche Ort für diese täglichen Aufgaben.

Die Verlagerung ist sicherheitsrelevant. `core_user` besitzt absichtlich keine
Row-Level-Security: Ein Benutzerkonto kann auch installationsweit sein und der
Login muss vor einem bekannten Einrichtungs-Kontext arbeiten. Jede neue
Fachoberfläche muss die Einrichtungsgrenze deshalb selbst vollständig
erzwingen. Gleichzeitig darf ein `facility_admin` weder die Rolle
`super_admin` vergeben noch den eigenen letzten Administrationszugang durch
Herabstufung oder Deaktivierung verlieren.

Ein `super_admin` hat keinen fachlichen Einrichtungs-Kontext. Würde der
Verwaltungs-Hub für diese Rolle geöffnet, bräuchte der gesamte Bereich eine
neue, sichtbare und auditierte Auswahl der Zieleinrichtung. Ein still aus dem
Objekt abgeleiteter Kontext wäre für eine einrichtungsübergreifende Rolle zu
leicht zu verwechseln.

## Decision

### 1. Fachliche Benutzerverwaltung gehört in den Verwaltungs-Hub

`facility_admin` verwaltet unter **Verwaltung → Zugänge** ausschließlich die
Konten der eigenen Einrichtung. Die Oberfläche deckt diese regulären Vorgänge
ab:

- Konto mit verpflichtender E-Mail-Adresse anlegen und per zeitlich begrenztem
  Setup-Link einladen,
- Benutzername, E-Mail-Adresse und Anzeigename ändern,
- eine der vier einrichtungsgebundenen Rollen vergeben,
- Zugang deaktivieren oder reaktivieren,
- individuelle MFA-Pflicht und das Recht zur Löschbestätigung pflegen,
- eine noch offene Einladung erneut senden und eine Login-Sperre aufheben.

Die Liste darf ohne erneute Passworteingabe gelesen werden. **Jeder**
Schreibweg und bereits der Aufruf eines Anlage- oder Bearbeitungsformulars
verlangt Sudo-Re-Authentifizierung. Mutationen sind zusätzlich CSRF-geschützt
und pro Benutzer rate-limitiert.

### 2. Der Facility-Scope ist eine Service-Invariante

Views greifen nie mit einer unbeschränkten Primärschlüsselabfrage auf `User`
zu. Der gemeinsame Service-Scope filtert ausdrücklich nach der aktuellen
Einrichtung und schließt `super_admin` aus. Dieselbe Prüfung gilt erneut im
Schreibservice; fremde oder installationsweite Ziele erscheinen als 404.

Formular und Service verbieten die Rolle `super_admin` unabhängig voneinander.
Ein Admin darf den eigenen Zugang weder deaktivieren noch unter
`facility_admin` herabstufen. Sicherheitsrelevante Änderungen und
Einladungsversand werden mit Ziel und handelndem Admin im append-only AuditLog
festgehalten.

### 3. `super_admin` und technische Sonderfälle bleiben im Django-Admin

Die Systemadministration benutzt weiterhin `/admin-mgmt/`. Sie legt
Einrichtungen und deren ersten `facility_admin` an und bearbeitet technische
Sonderfälle dort unter dem bestehenden Sudo- und Audit-Regime. Ein
einrichtungsübergreifender Verwaltungs-Hub ist ausdrücklich nicht Teil dieser
Entscheidung.

Auch Quick-Templates und Zeitfenster bleiben vorerst technische
Admin-Funktionen. Der Django-Admin bleibt als sichtbarer Notausgang erhalten,
ist aber für die laufende Benutzerpflege einer Einrichtung nicht mehr der
reguläre Weg.

### 4. Keine endgültige Kontolöschung in der Fach-UI

Der reguläre Lebenszyklus endet mit **Deaktivieren**. Eine endgültige Löschung
kann Audit-Attribution, offene Aufgaben und weitere Referenzen betreffen und
ist daher kein beiläufiger CRUD-Vorgang. Sie bleibt einem geprüften
technischen Sonderfall vorbehalten; die Fach-UI bietet weder Schaltfläche noch
Endpoint dafür an.

## Consequences

### Positiv

- Die Anwendungsbetreuung erledigt den vollständigen täglichen Zugangsprozess
  in derselben Fachnavigation wie Einstellungen und Formulare.
- Die größte Schutzlücke der nicht per RLS gesicherten User-Tabelle wird durch
  einen einzigen, getesteten Facility-Scope und IDOR-Gegenproben begrenzt.
- Rollenvergabe, Deaktivierung, MFA-Vorgaben, Einladungen und Entsperren sind
  handelnden Personen zurechenbar.
- Die System-/Fachtrennung bleibt sichtbar; es gibt keinen impliziten
  Cross-Facility-Kontext für `super_admin`.

### Negativ / bewusst akzeptiert

- Systemadministration und technische Sonderfälle bleiben in einer zweiten
  Oberfläche.
- Quick-Templates und Zeitfenster sind weiterhin nicht im Verwaltungs-Hub
  pflegbar.
- Eine Einrichtung ohne verfügbaren `facility_admin` braucht weiterhin einen
  technischen Recovery-Weg.
- Das fehlende RLS-Netz für `core_user` verlangt dauerhaft explizite
  Service- und IDOR-Tests für jeden neuen Benutzerverwaltungsweg.

## Alternatives considered

- **Verwaltungs-Hub auch für `super_admin` öffnen:** wäre bequemer, benötigt
  aber eine sichere Einrichtungswahl und würde die Governance des gesamten
  Hubs ändern — eigenes Vorhaben, hier verworfen.
- **Django-Admin als einzigen Weg behalten:** vermeidet neue Oberfläche,
  vermischt aber tägliche Fachadministration mit der technischen Notfall- und
  Bootstrap-Ebene — verworfen.
- **Nur UI-Filter, Service nimmt freie User-ID an:** weniger Code, aber ohne RLS
  ein direkter IDOR-Pfad — verworfen.
- **Konten endgültig löschen:** scheinbar vollständiges CRUD, aber mit
  unnötigem Referenz- und Nachweisrisiko gegenüber der reversiblen
  Deaktivierung — verworfen.

## References

- Rollenmodell: [ADR-018](018-rollenmodell-superadmin.md)
- Sudo-geschützte technische Administration: [ADR-019](019-custom-admin-site-sudo.md)
- Facility-Scoping und RLS-Grenzen: [ADR-005](005-facility-scoping-and-rls.md)
- Append-only AuditLog: [ADR-007](007-auditlog-append-only.md)

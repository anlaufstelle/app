# Manuelle Test-Matrix — Sektion B

> Teil der [Manual-Test-Matrix](manual-test-matrix.md) (Refs #1071 Block B). Setup-Block, Status-Legende, TC-ID-Schema, Browser-/Mobile-Konventionen und die Anhänge stehen im [Hub](manual-test-matrix.md); die Gesamtübersicht in `test-matrix-index.md`.

---

## SEKTION B — Anwender-Komplett (systematisch)

> **Zielgruppe:** Tobias als Entwickler/Tester. Systematisches Durchgehen aller Bereiche vor jedem Major-Release.
>
> **Format:** Pro Bereich ein `<details open>`-Block mit Header (Routen, View-Klassen, E2E-Coverage, Spezial-Setup) und allen Cases. Bereiche in dieser Reihenfolge:
> AUTH → MFA → ACCT → SUDO → PWA → CLIENT → CASE → EPI → GOAL → EVT → ATT → WI → DEL → RET → SRCH → ZS → HOV → STAT → AUDIT → DSGVO → OFFL → SYS → CONFIG → COND → CLIENT-DYN/CASE-DYN → IMPORT → RECT.

<details open>
<summary><strong>🔐 AUTH — Authentifizierung (11 Cases)</strong></summary>

**Routen:** `/login/`, `/logout/`, `/password-change/`, `/password-reset/`, `/password-reset/done/`, `/password-reset/<uidb64>/<token>/`, `/password-reset/complete/` 
**Views:** `src/core/views/auth.py` (`CustomLoginView`, `CustomLogoutView`, `RateLimitedPasswordResetView`, `CustomPasswordChangeView`) 
**Services:** `src/core/services/login_lockout.py` (`is_locked`, `unlock`, Schwelle 10/15min), `src/core/services/password.py` (12-Zeichen-Initial-Generator), `src/core/services/audit_hash.py` (`hmac_hash_email`), `src/core/models/user.py` (`User.ensure_offline_key_salt`) 
**Middleware:** `core.middleware.password_change.ForcePasswordChangeMiddleware` 
**Settings:** `AUTH_PASSWORD_VALIDATORS` (min_length=12, BSI/NIST), `SESSION_COOKIE_AGE=1800`, `SESSION_SAVE_EVERY_REQUEST=False`, `AUDIT_HASH_KEY`, Audit-Action-Choices in `src/core/models/audit.py` 
**Signals:** `src/core/signals/audit.py` (LOGIN/LOGOUT/LOGIN_FAILED via Django-Auth-Signals) 
**E2E-Coverage:** `test_auth_roles.py`, `test_password_reset.py`, `test_logout_cleanup.py`, `test_security_hardening.py` 
**Spezial-Setup:**
- Login-Lockout-Test braucht 10 sequenzielle Fehlversuche innerhalb von 15 Min — IP-Rate-Limit (5/m) muss vorher umgangen werden (z.B. Cookie-Reset zwischen Bursts oder Limit per Settings hochsetzen).
- Pwd-Reset-Test braucht `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` (dev/e2e-Default) oder MailHog für Token-Capture.
- Seed-User: `admin`, `emma` (Leitung), `miriam` (Fachkraft), `lena` (Assistenz) — Passwort `anlaufstelle2026`. Quelle: `src/core/seed/constants.py`.

---

### TC-ID: ENT-AUTH-01 — Erfolgreicher Login mit korrektem Passwort

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ✓ | `test_auth_roles.py` |


**Vorbereitung:**
- Privates Browser-Fenster öffnen, kein aktiver Login.
- `make seed` ausgeführt, sodass `miriam` mit `anlaufstelle2026` existiert.

**Schritte:**
1. `https://localhost:8844/login/` aufrufen.
2. Username `miriam`, Passwort `anlaufstelle2026` eingeben.
3. Submit klicken.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/` (Zeitstrom).
- AuditLog enthält Eintrag `action=login` mit `user=miriam`, befüllter `ip_address`, aktuellem `timestamp`, `facility` gesetzt.
- Session-Cookie `sessionid` ist gesetzt mit `Secure`, `HttpOnly`, `SameSite=Lax`.
- `request.session["mfa_verified"]` ist `False` gesetzt (siehe `CustomLoginView.form_valid`), MFA-Middleware leitet erst weiter, wenn Device existiert.
- Im Header sichtbar: Username/Rolle der angemeldeten Person.

**DSGVO/Security-Note:**
- Erfolgreicher Login wird auditiert (Art. 32 TOMs); Audit-Signal in `src/core/signals/audit.py`.
- Session-Timeout 30 Min aus `SESSION_COOKIE_AGE=1800`, ggf. überschrieben durch `facility.settings.session_timeout_minutes`.

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-02 — Login mit falschem Passwort (1× Versuch, kein Lockout)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ⚪ | `test_auth_roles.py` |


**Vorbereitung:**
- Privates Fenster, kein aktiver Login.
- AuditLog vor dem Test mit `LOGIN_FAILED`-Count notieren (Baseline).

**Schritte:**
1. `https://localhost:8844/login/` aufrufen.
2. Username `miriam`, Passwort `falsch123!` eingeben.
3. Submit klicken.

**Erwartetes Ergebnis:**
- HTTP 200, Login-Form wird mit Fehlermeldung `"Bitte geben Sie einen korrekten Benutzernamen und ein Passwort ein"` o.ä. neu gerendert.
- AuditLog hat **einen** neuen `LOGIN_FAILED`-Eintrag mit `user=miriam`, `facility=miriam.facility`, `ip_address` befüllt, `detail.username="miriam"`.
- Kein Session-Cookie für eingeloggten User (Anonymous-Session OK).
- `is_locked(miriam)` gibt weiterhin `False` zurück (1 < 10).

**DSGVO/Security-Note:**
- LOGIN_FAILED wird auditiert (Art. 32, Forensik bei Brute-Force).
- Antwortzeit/HTML soll nicht unterscheiden, ob User existiert oder nicht (Anti-Enumeration).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-03 — Login-Lockout nach 10 Fehlversuchen in 15 Min

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** IP-Rate-Limit (5/m) vorübergehend deaktiviert ODER `RATELIMIT_ENABLE=False` in Test-Settings.

**Vorbereitung:**
- Privates Fenster, kein aktiver Login.
- AuditLog für `miriam` leeren oder `LOGIN_UNLOCK` als Cutoff setzen.

**Schritte:**
1. 10× hintereinander auf `/login/` Username `miriam` + falsches Passwort eingeben (innerhalb von <15 Min).
2. Beim 11. Versuch das **korrekte** Passwort `anlaufstelle2026` eingeben.

**Erwartetes Ergebnis:**
- Versuche 1–10: jeweils `LOGIN_FAILED`-AuditLog-Eintrag.
- Versuch 11: trotz korrektem Passwort kein Login. Form-Fehler:
  `"Ihr Konto ist nach mehreren fehlgeschlagenen Versuchen temporär gesperrt. Bitte später erneut versuchen oder Administration kontaktieren."`
- Zusätzlicher AuditLog-Eintrag `LOGIN_FAILED` mit `detail.reason="locked"` und `detail.message="Login blockiert durch Account-Lockout"`.
- Kein `LOGIN`-Erfolgs-Eintrag, kein Session-Cookie.
- `is_locked(miriam)` gibt `True` zurück (Schwelle erreicht innerhalb 15-Minuten-Fenster).
- Admin kann via `unlock()` / `LOGIN_UNLOCK`-Audit den Cutoff setzen, danach werden die alten FAILED-Einträge ignoriert.

**DSGVO/Security-Note:**
- Lockout-Konstanten: `LOCKOUT_THRESHOLD = 10`, `LOCKOUT_WINDOW = timedelta(minutes=15)` in `src/core/services/login_lockout.py`.
- Concurrency-Schutz via `transaction.atomic()` + `User.objects.select_for_update()` (Refs #737).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-04 — IP-Rate-Limit (5/min POST) — 6. Versuch HTTP 429

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C || `test_security_hardening.py` |

**Voraussetzung:** `django_ratelimit` aktiv (Default in dev/e2e).

**Vorbereitung:**
- Cache leeren, sodass IP-Bucket leer startet (z.B. Redis flushen oder LocMem-Cache neu).
- Privates Fenster, gleiche Quell-IP.

**Schritte:**
1. Innerhalb 60 Sekunden 5× POST auf `/login/` mit Username `miriam` + falschem Passwort.
2. 6. Versuch innerhalb derselben Minute durchführen.

**Erwartetes Ergebnis:**
- Versuche 1–5: HTTP 200, Login-Form mit Fehler.
- Versuch 6: `block=True` greift → HTTP 429 (Ratelimited)-Response (Django-Default-Page oder Custom-Page).
- Es entstehen **keine** zusätzlichen `LOGIN_FAILED`-Einträge für gesperrte Versuche, da die View gar nicht ausgeführt wird.
- Nach 60 Sekunden Wartezeit ist neue Anfrage wieder möglich.

**DSGVO/Security-Note:**
- Decorator: `@method_decorator(ratelimit(key="ip", rate="5/m", method="POST", block=True))` in `CustomLoginView.post`.
- Schützt vor klassischem Brute-Force von einer IP (Refs #598).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-05 — Username-Rate-Limit (10/h POST) — verteiltes Botnet

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C || `test_security_hardening.py` |

**Voraussetzung:** mindestens 2 Quell-IPs simulierbar (z.B. via `X-Forwarded-For` mit `TRUSTED_PROXY_HOPS=1`) oder Direkt-Cache-Manipulation.

**Vorbereitung:**
- Cache leeren.
- Username-Bucket-Key wird in `_login_username_key` lowercased + gestrippt — Variationen wie `Miriam` und ` miriam ` zählen auf denselben Bucket.

**Schritte:**
1. Mit IP A: 5 fehlgeschlagene Logins für `miriam`.
2. Mit IP A: 6. Versuch → IP-Limit greift (HTTP 429).
3. Mit IP B (eine Minute später, sodass IP-Bucket frei): weitere fehlgeschlagene Logins, insgesamt bis 10× kumuliert über IPs.
4. 11. Versuch über beliebige IP für `miriam`.

**Erwartetes Ergebnis:**
- Bei Versuch 11 (egal welche IP): HTTP 429 — Username-Bucket voll, `block=True`.
- Die Sperre gilt für `miriam` für 1 Stunde, andere Usernames sind nicht betroffen.
- `Miriam`, ` miriam ` und `miriam` zählen alle in denselben Bucket (Lowercased+Strip in `_login_username_key`).

**DSGVO/Security-Note:**
- Decorator: `@method_decorator(ratelimit(key=_login_username_key, rate="10/h", method="POST", block=True))`.
- Schützt vor verteilten Angriffen mit rotierenden IPs (Botnet) — echter User würde nach max. 10 falschen Eingaben in 1h auf Pwd-Reset gehen (Refs #598).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-06 — Logout-Cleanup mit Clear-Site-Data-Header

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ✓ | `test_logout_cleanup.py` |

**Voraussetzung:** aktiver Login.

**Vorbereitung:**
- Login als `miriam`.
- DevTools → Application → IndexedDB: prüfen, dass `offline-queue`/Client-Cache-Stores beschrieben sind (z.B. nach Klick auf einen Klienten).
- Network-Tab geöffnet.

**Schritte:**
1. Logout-Link/Button klicken (POST `/logout/`).
2. Im Network-Tab den Logout-Response inspizieren.
3. Application-Tab erneut prüfen (IndexedDB, LocalStorage, SessionStorage).

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/login/` (`next_page = "/login/"`).
- Response-Header enthält **`Clear-Site-Data: "storage"`** (siehe `CustomLogoutView.dispatch`).
- Browser räumt LocalStorage, SessionStorage, IndexedDB für die Origin auf — IndexedDB-Stores sind leer.
- Session-Cookie ist gelöscht (kein gültiger `sessionid` mehr).
- AuditLog enthält `LOGOUT`-Eintrag mit `user=miriam`, `facility`, `ip_address` (`on_user_logged_out`-Signal).

**DSGVO/Security-Note:**
- Clear-Site-Data räumt offline gespeicherte (verschlüsselte) Klienten-Daten beim Logout (DSGVO-Datenminimierung; verschlüsseltes IndexedDB-Material wäre ohne Pwd unbrauchbar, aber Cleanup ist explizit).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-07 — Session-Timeout nach 30 Min Inaktivität

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** Schnellvariante: `facility.settings.session_timeout_minutes = 1` setzen (Admin → Core → Einstellungen) und neu einloggen — ein globales `SESSION_COOKIE_AGE`-Test-Setting wirkt NICHT, weil der Login es per `set_expiry(facility-Wert)` immer überschreibt.

**Vorbereitung:**
- Login als `miriam`.
- Session-Cookie inspizieren: `Max-Age=1800` (bzw. `60` in der Schnellvariante).
- Optional: facility.settings.session_timeout_minutes anders gesetzt → bestätigen, dass `CustomLoginView.form_valid` `request.session.set_expiry(timeout)` mit dem Facility-Wert aufruft.

**Schritte:**
1. 31 Min nichts klicken (Schnellvariante: 61 s).
2. Beliebige Seite z.B. `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/login/?next=/clients/` (Django-Default für `LOGIN_URL=/login/`).
- Vorherige Session ist invalide.
- Aktivität verlängert die Session: `SessionIdleRefreshMiddleware` (#1858) erneuert das Fenster bei authentifizierten Requests, gedrosselt auf einen Session-Write je 10 % der Laufzeit (Default: alle 3 Min). Wer aktiv arbeitet, bleibt beliebig lange eingeloggt; der Logout kommt erst nach 27–30 Min echter Inaktivität.
- Gegenprobe: >30 Min aktiv weiterklicken (mind. alle paar Minuten ein Request) → KEIN Logout mitten in der Arbeit.
- Nach Re-Login wird `next` korrekt aufgelöst.

**DSGVO/Security-Note:**
- Setting `SESSION_COOKIE_AGE=1800` in `src/anlaufstelle/settings/base.py`.
- `SESSION_SAVE_EVERY_REQUEST=False` reduziert DB-Write-Amplifikation (Refs #733); das Inaktivitäts-Fenster liefert NICHT dieses Flag, sondern allein die `SessionIdleRefreshMiddleware` (#1858).
- Hintergrund-Sync zählt nicht als Aktivität: Offline-Replays (`X-Offline-Replay`) und die Sync-API (`/api/v1/offline/…`) erneuern das Fenster nicht.
- Login speichert ggf. `session.set_expiry(facility.settings.session_timeout_minutes * 60)` (override).
- Das Inaktivitäts-Fenster ist NICHT die einzige Grenze: darüber liegt das absolute Sitzungs-Maximum aus #1868 — eigener Case `ENT-AUTH-11`.

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-08 — Passwort-Wechsel (Validatoren greifen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** aktiver Login als `miriam`.

**Vorbereitung:**
- Vorher (Salt vor Wechsel) `User.offline_key_salt` lesen (z.B. via Shell oder `/auth/offline-key-salt/` POST). Notieren.
- AuditLog `OFFLINE_KEY_FETCH`-Count notieren.

**Schritte:**
1. `/password-change/` aufrufen.
2. Test A — Validierung (negativ):
   - Altes Pwd: `anlaufstelle2026`
   - Neues Pwd: `kurz` (zu kurz, < 12 Zeichen)
   - Submit → Form-Fehler `"Dieses Passwort ist zu kurz."` (MinimumLengthValidator, min_length=12).
3. Test B — Validierung (negativ): neues Pwd `123456789012` (rein-numerisch) → `NumericPasswordValidator` schlägt an.
4. Test C — Validierung (negativ): neues Pwd `password1234` (häufig) → `CommonPasswordValidator` schlägt an.
5. Test D — Validierung (negativ): neues Pwd ähnlich wie Username/Email → `UserAttributeSimilarityValidator` greift.
6. Test E — Erfolg: neues Pwd `Sicher2026!Stark` (>=12, gemischt, kein Häufiges) → Submit.

**Erwartetes Ergebnis:**
- A–D: HTTP 200, Form mit Validator-Fehlermeldung re-rendert.
- E: HTTP 302 Redirect auf `/` (`success_url="/"`).
- Nach E:
  - `user.must_change_password` ist auf `False` zurückgesetzt (siehe `CustomPasswordChangeView.form_valid`).
  - `user.offline_key_salt` ist auf `""` (leer) zurückgesetzt → wird beim nächsten `/auth/offline-key-salt/`-Aufruf neu generiert.
  - Session bleibt gültig (Django-Standard `update_session_auth_hash`).
- Validatoren-Reihenfolge laut `AUTH_PASSWORD_VALIDATORS` in `base.py`.

**DSGVO/Security-Note:**
- Min-Length=12 entspricht BSI/NIST für §203/Art.-9-Daten (Refs #789).
- Salt-Rotation invalidiert alte client-seitige IndexedDB-Schlüssel — alte verschlüsselte Records werden beim nächsten Login als „garbage" verworfen.

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-09 — Passwort-Reset per E-Mail (HMAC-Hash im AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | unauthentifiziert | C/F/S | ⚪ | `test_password_reset.py` |

**Voraussetzung:** `EMAIL_BACKEND=console` (dev) oder MailHog erreichbar; `DJANGO_AUDIT_HASH_KEY` gesetzt (sonst SHA256-Fallback).

**Vorbereitung:**
- E-Mail von `miriam` ist befüllt (via Seed/Admin).
- AuditLog mit `PASSWORD_RESET_REQUESTED` vor dem Test inspizieren (Baseline).
- Console-Output / MailHog beobachten.

**Schritte:**
1. `/password-reset/` aufrufen.
2. E-Mail von `miriam` eingeben, Submit.
3. Generische „Wir haben Ihnen eine E-Mail geschickt"-Seite (`/password-reset/done/`) bestätigen.
4. Aus Console/MailHog den Reset-Link `/password-reset/<uidb64>/<token>/` öffnen.
5. Neues Passwort `Sicher2026!NeuPw` 2× eingeben, Submit → `/password-reset/complete/`.
6. Mit `miriam` und neuem Pwd erfolgreich einloggen.
7. Test mit **unbekannter** E-Mail wiederholen — Schritt 2 mit `niemand@example.com`.

**Erwartetes Ergebnis:**
- Schritt 2 (existierend): AuditLog enthält `PASSWORD_RESET_REQUESTED` mit `user=miriam`, `target_type="User"`, `target_id=miriam.pk`, `detail={"email_hash": "<hex>"}`. **Kein Klartext-E-Mail im Log.**
- Schritt 7 (unbekannt): AuditLog hat ebenfalls `PASSWORD_RESET_REQUESTED`-Eintrag mit `user=None`, `target_type=""`, `detail={"email_hash": "<hex>"}` — gleicher generischer Done-Screen, **keine Enumeration** möglich.
- Reset-Token ist gültig (Django-Default: 3 Tage), nach Pwd-Setzen `LOGIN`-AuditLog erfolgreich.
- Rate-Limit `5/m IP POST` auf `/password-reset/` greift bei Spam (HTTP 429 ab dem 6. Versuch).

**DSGVO/Security-Note:**
- Klartext-E-Mails im append-only AuditLog widersprächen DSGVO-Datenminimierung (Refs #791) → HMAC-Hash via `hmac_hash_email` in `services/audit_hash.py`.
- Gleiche E-Mail → gleicher Hash (Lookup für Forensik möglich), aber keine PII in 24-Monats-Retention.
- Anti-Enumeration: identische Response-Page egal ob E-Mail bekannt oder nicht (Decorator `try/except`-geschützt).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-10 — must_change_password-Flow (frischer User)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | beliebig (z.B. neue Fachkraft) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** neuer User mit `must_change_password=True` — Konto **ohne** E-Mail-Adresse im Admin anlegen (die Neuanlage setzt das Flag und zeigt ein Klartext-Initialpasswort an) oder das Flag an einem bestehenden Konto von Hand setzen. **Nicht** über den Invite-Flow: Ein abgeschlossener Einladungs- oder Reset-Vorgang löscht das Flag seit #1969 mit, dieser Weg erzeugt die Voraussetzung also nicht mehr.

**Vorbereitung:**
- User `neue_fk` mit Initial-Pwd (12 Zeichen aus `generate_initial_password`) und `must_change_password=True` anlegen.
- Privates Fenster.

**Schritte:**
1. `/login/` mit `neue_fk` + Initial-Pwd → Submit.
2. Sofort versuchen, `/clients/` direkt aufzurufen.
3. Auf `/password-change/` neues, regelkonformes Pwd setzen.
4. Erneut `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: Login erfolgreich (HTTP 302).
- Schritt 2: `ForcePasswordChangeMiddleware` redirected zu `/password-change/` (302), unabhängig vom angeforderten Pfad. EXEMPT_URLS bleiben erreichbar (`/login/`, `/logout/`, `/password-change/`, `/password-reset/`, `/static/`).
- Schritt 3: `CustomPasswordChangeView.form_valid` setzt `must_change_password=False` und leert `offline_key_salt`. Redirect auf `/`.
- Schritt 4: Anwendung normal nutzbar, kein Redirect mehr.

**DSGVO/Security-Note:**
- Mittelweg gegen frische Initial-Passwörter, die im Klartext (Onboarding-PDF, Admin-Mail) existieren — User muss sofort eigenes Pwd setzen.
- Initial-Generator: `generate_initial_password(length=12)` aus `services/password.py` (12 Zeichen, ASCII letters+digits, BSI-konform).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUTH-11 — Absolutes Sitzungs-Maximum (Cap ab Login, aktivitätsunabhängig)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Auth | fachkraft (`miriam`) | C || — (Unit/Integration: `test_middleware_session_idle.py`) |

**Voraussetzung:** Schnellvariante über den Admin — sonst wären es 12 Stunden Wartezeit.

**Vorbereitung:**
- Admin → Core → Einstellungen → eigene Einrichtung:
  - `Session-Timeout (Minuten)` auf **1** setzen (das Maximum muss größer sein als das Timeout, gerechnet in Minuten).
  - `Maximale Sitzungsdauer (Stunden)` auf **1** setzen → wirksames Cap 60 Min, Inaktivität 1 Min. Speichern.
- Gegenprobe der Validierung: `Maximale Sitzungsdauer (Stunden)` = 1 **bei** `Session-Timeout` = 60 speichern wollen.
- Frisch als `miriam` einloggen (die Frist wird beim **Login** eingefroren — eine Settings-Änderung wirkt NICHT auf eine schon laufende Sitzung).
- Für die schnellste Variante (60 s statt 60 min) stattdessen direkt in der Session-Zeile arbeiten: in der Django-Shell die eigene `django_session`-Zeile dekodieren und `_session_absolute_deadline` (Unix-Zeitstempel) auf `time.time() - 1` setzen und die Zeile speichern.

**Schritte:**
1. Validierungs-Gegenprobe speichern (Maximum 1 h bei Timeout 60 min).
2. Nach dem Login **durchgehend aktiv bleiben**: alle ~30 s eine Seite aufrufen (z.B. `/clients/` ↔ `/zeitstrom/`), bis die Stunde ab Login voll ist (bzw. sofort, wenn die Deadline per Shell in die Vergangenheit gesetzt wurde).
3. Nächste Seite aufrufen.
4. Im Admin → Core → Audit-Logs den letzten Eintrag prüfen.
5. Optional (HTMX-Pfad): statt einer Vollseite eine HTMX-getriebene Aktion auslösen (z.B. Tippen in die Suche der Seitenleiste), während die Deadline überschritten ist.

**Erwartetes Ergebnis:**
- Schritt 1: Das Formular **lehnt ab** mit einer Meldung am Feld „Maximale Sitzungsdauer (Stunden)" („muss größer sein als das Session-Timeout (60 Minuten)"). Werte außerhalb 1–168 werden ebenfalls abgelehnt.
- Schritt 2: Kein Logout — solange die Deadline nicht erreicht ist, hält die Aktivität die Sitzung offen (Verhalten aus `ENT-AUTH-07` unverändert).
- Schritt 3: HTTP 302 auf `/login/?next=…` **trotz durchgehender Aktivität**. Genau das ist der Unterschied zu `ENT-AUTH-07`.
- Die Session-Zeile in `django_session` ist **weg** (nicht nur abgelaufen).
- Schritt 4: Ein `LOGOUT`-Eintrag für `miriam` mit dem Zeitpunkt des letzten Requests — dieselbe Spur wie bei einem Abmelde-Klick.
- Schritt 5: Die HTMX-Aktion führt zu einem **vollständigen Seitenwechsel** auf `/login/` (Server antwortet `200` + `HX-Redirect`), nicht zu einer Login-Seite im Partial-Bereich.
- Nach Re-Login läuft eine frische Frist; die Anwendung ist normal nutzbar.
- **Aufräumen:** `Session-Timeout` auf 30 und `Maximale Sitzungsdauer` auf 12 zurücksetzen.

**DSGVO/Security-Note:**
- Feld `Settings.session_absolute_hours` (Default 12 h, 1–168), Cross-Field-Regel doppelt abgesichert: `Settings.clean()` + DB-`CheckConstraint` `settings_session_absolute_cap`.
- Deadline liegt als `_session_absolute_deadline` in der Session und wird **nur beim Login** gestempelt; die Aktivitäts-Erneuerung schiebt sie nie nach (sonst wäre das Maximum ein zweites Sliding Window).
- Das Cap gilt **auch** für die Pfade, die als Hintergrund-Sync vom Inaktivitäts-Fenster ausgenommen sind (`X-Offline-Replay`, `/api/v1/offline/…`) — ein dauersyncendes Gerät hat kein Schlupfloch.
- Bestands-Sessions ohne den Schlüssel (angelegt vor dem Update) werden nicht abgemeldet, sondern bekommen einmalig den Default-Cap ab dem nächsten Request.
- Hintergrund: #1868, Trade-offs in [`security-notes.md`](../security-notes.md).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🔑 MFA — Zwei-Faktor-Authentifizierung (9 Cases)</strong></summary>

**Routen:** `/mfa/setup/`, `/mfa/verify/`, `/mfa/settings/`, `/mfa/disable/`, `/mfa/backup-codes/`, `/mfa/backup-codes/regenerate/` 
**Views:** `src/core/views/mfa.py` (`MFASetupView`, `MFAVerifyView`, `MFASettingsView`, `MFADisableView`, `MFABackupCodesView`, `MFARegenerateBackupCodesView`) 
**Services:** `src/core/services/mfa.py` (`generate_backup_codes`, `verify_backup_code`, `remaining_backup_codes`, SHA-256-Hash, 128-bit Entropie via `secrets.token_urlsafe(16)`) 
**Middleware:** `core.middleware.mfa.MFAEnforcementMiddleware` (EXEMPT_URLS für /login/, /logout/, /mfa/, /static/, /sw.js, /manifest.json, /auth/offline-key-salt/, /health/) 
**Settings:** `OTP_TOTP_ISSUER="Anlaufstelle"`; Apps: `django_otp`, `django_otp.plugins.otp_totp`, `django_otp.plugins.otp_static` 
**E2E-Coverage:** `test_mfa_setup_flow.py`, `test_mfa_backup_codes.py`, `test_security_hardening.py` 
**Spezial-Setup:**
- TOTP-Code-Generierung im Test: `pyotp` mit gleichem Secret wie QR-Code (Base32-decoded aus `device.bin_key`).
- Backup-Codes: 22-Zeichen URL-safe Base64 (case-sensitive!) ab Refs #790. Legacy-Format `xxxx-xxxx` (8 Hex + Dash) wird beim Verify toleriert.
- `MFADisableView` ist `RequireSudoModeMixin` — vor Disable muss Sudo-Mode aktiv sein.
- `request.user.is_mfa_enforced` = `True`, wenn `User.mfa_required=True` ODER `facility.settings.mfa_enforced_facility_wide=True`.

---

### TC-ID: ENT-MFA-01 — TOTP-Setup: QR scannen, Test-Code bestätigen, Aktivierung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ✓ | `test_mfa_setup_flow.py` |

**Voraussetzung:** eingeloggt; bisher kein bestätigtes TOTPDevice.

**Vorbereitung:**
- Login als `miriam`.
- DB-Check: `TOTPDevice.objects.filter(user=miriam, confirmed=True).exists() == False`.

**Schritte:**
1. `/mfa/settings/` aufrufen → Status „2FA nicht aktiv".
2. Auf „2FA einrichten" klicken oder direkt `/mfa/setup/` öffnen.
3. QR-Code wird als Data-URL-PNG angezeigt; alternativ Base32-Secret darunter sichtbar.
4. Authenticator-App (z.B. FreeOTP+) öffnen → Account hinzufügen → QR scannen ODER Secret manuell eintippen (Issuer "Anlaufstelle").
5. App liefert 6-stelligen Code; Code in das `token`-Input eingeben → Submit.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect auf `/mfa/backup-codes/`.
- DB: `TOTPDevice` für `miriam` mit `confirmed=True`.
- Session: `request.session["mfa_verified"] = True`, `request.session["mfa_backup_codes"]` befüllt mit 10 Codes.
- AuditLog: zwei neue Einträge — `MFA_ENABLED` (`detail.event="mfa_setup_confirmed"`) und `BACKUP_CODES_GENERATED` (`detail.count=10`).
- Backup-Codes werden auf `/mfa/backup-codes/` einmalig angezeigt; Reload zeigt nichts mehr (Session-Pop).
- Rate-Limit `10/min/User` auf `MFASetupView.post` (gegen Brute-Force des Setup-Tokens).

**DSGVO/Security-Note:**
- TOTP-Secret ist 160-bit per django-otp-Default; QR-Code via `qrcode`-Lib gerendert, kein externer Service.
- Backup-Codes ab Refs #790: 128 Bit Entropie, in DB SHA-256-Hash truncated auf 16 Hex (Pre-Image-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-02 — Login mit MFA: Username/Pwd + TOTP-Verify

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ✓ | `test_mfa_setup_flow.py` |

**Voraussetzung:** ENT-MFA-01 erfolgreich; bestätigtes TOTPDevice für `miriam`.

**Vorbereitung:**
- Logout aus aktiver Session.
- Authenticator-App geöffnet, Code für „Anlaufstelle" sichtbar.

**Schritte:**
1. `/login/` → `miriam` + `anlaufstelle2026` → Submit.
2. Folgender 302 → `MFAEnforcementMiddleware` leitet auf `/mfa/verify/` (Session ist nicht mfa_verified).
3. Auf `/mfa/verify/`: aktuellen 6-stelligen TOTP-Code eingeben → Submit.
4. Versuch B (negativ): falschen Code eingeben.

**Erwartetes Ergebnis:**
- Schritt 3: HTTP 302 auf `LOGIN_REDIRECT_URL=/`. Session `mfa_verified=True`. Anwendung nutzbar.
- Schritt 4: HTTP 200, Form mit Fehlermeldung `"Der Code ist ungültig. Bitte erneut versuchen."`. AuditLog `MFA_FAILED` mit `detail.event="mfa_token_invalid"`, `detail.mode="totp"`.
- Rate-Limit `5/min/User` auf `MFAVerifyView.post`. Bei 6+ Versuchen → HTTP 429.
- Eingegebene Token werden mit `.strip().replace(" ", "")` normalisiert (Toleranz für Leerzeichen).

**DSGVO/Security-Note:**
- Refs #683: Session-Hijack-Schutz — auch mit gestohlenem Session-Cookie braucht Angreifer den TOTP-Code.
- AuditLog für jeden fehlgeschlagenen Verify-Versuch (Forensik).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-03 — Backup-Codes generieren (10×, SHA-256-gehasht)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-01; `miriam` hat MFA aktiv.

**Vorbereitung:**
- Login + MFA-Verify.
- DB-Check: `StaticDevice` mit `name="backup"` für `miriam`, `StaticToken.objects.filter(device=...).count()` notieren.

**Schritte:**
1. Auf `/mfa/backup-codes/` (über Setup-Flow oder Regenerate-Action) — 10 Codes anzeigen lassen.
2. Code-Format prüfen: 22 Zeichen URL-safe Base64 (alphanumerisch + `-` + `_`, case-sensitive).
3. DB-Inspektion: `StaticToken.token`-Feld pro Code prüfen.

**Erwartetes Ergebnis:**
- UI zeigt **genau 10** Codes als Liste/Card, mit Druck-/Copy-Hint.
- Code-Format: `^[A-Za-z0-9_-]{22}$`, jedes Codes 128 Bit Entropie (`secrets.token_urlsafe(16)`).
- DB: Pro Code ein `StaticToken`-Eintrag; **`token`-Feld enthält nicht den Klartext**, sondern SHA-256-Hex-Digest, truncated auf 16 Hex-Zeichen (`hashlib.sha256(code).hexdigest()[:16]`).
- Bestehender `StaticDevice` für `miriam` wird wiederverwendet, alte Tokens werden gelöscht (`device.token_set.all().delete()`).
- AuditLog: `BACKUP_CODES_GENERATED` (Setup) bzw. `BACKUP_CODES_REGENERATED` (Regenerate) mit `detail.count=10`.

**DSGVO/Security-Note:**
- Refs #790 (C-22): DB-Leak != Backup-Code-Kompromittierung (Pre-Image-Angriff gegen 128-Bit-Eingabe = 2^64 Trial, infeasibel).
- Codes werden NUR EINMAL angezeigt — Anzeige-Seite konsumiert `request.session.pop("mfa_backup_codes")`.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-04 — Login mit Backup-Code (One-Time)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-03; mind. 1 ungenutzter Backup-Code notiert.

**Vorbereitung:**
- Logout.
- TOTP-App nicht erreichbar simuliert (mental: User hat Phone verloren).

**Schritte:**
1. `/login/` mit `miriam` + Pwd → Submit → Redirect auf `/mfa/verify/`.
2. Auf `/mfa/verify/` Modus auf „Backup-Code" wechseln (Form-Field `mode=backup`).
3. Backup-Code eingeben (case-sensitive, 22 Zeichen) → Submit.

**Erwartetes Ergebnis:**
- HTTP 302 auf `LOGIN_REDIRECT_URL=/`.
- Session `mfa_verified=True`.
- DB: `StaticToken` für genutzten Code wurde via `match.delete()` entfernt → `remaining_backup_codes(miriam) == 9`.
- AuditLog: `BACKUP_CODES_USED` mit `detail.remaining=9`.
- `device.set_last_used_timestamp(commit=False)` setzt Timestamp; `throttle_reset` wird ausgeführt.

**DSGVO/Security-Note:**
- Single-use ist Pflicht — der Match-Eintrag wird sofort aus DB gelöscht.
- Throttle-Mixin (django-otp) verhindert Backup-Code-Brute-Force (1s Delay nach Miss).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-05 — Backup-Code-Reuse-Verbot

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C || `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-04; ein Code wurde bereits einmal genutzt und in DB gelöscht.

**Vorbereitung:**
- User ist nach ENT-MFA-04 eingeloggt.
- Logout, dann erneut Login bis `/mfa/verify/`.

**Schritte:**
1. `/login/` → Pwd → `/mfa/verify/`.
2. Modus „Backup-Code" wählen.
3. **Denselben** schon verwendeten Backup-Code aus ENT-MFA-04 eingeben → Submit.

**Erwartetes Ergebnis:**
- HTTP 200, Form mit Fehler `"Der Code ist ungültig. Bitte erneut versuchen."`.
- Session bleibt `mfa_verified=False`.
- AuditLog: neuer `MFA_FAILED`-Eintrag mit `detail.event="mfa_token_invalid"`, `detail.mode="backup"`.
- `device.throttle_increment()` wurde aufgerufen — wiederholte Misses verzögern weitere Verifies (django-otp ThrottlingMixin).
- `verify_backup_code` returnt `False`, da `device.token_set.filter(token__in=[hashed, token]).first()` kein Match findet (Token wurde gelöscht).

**DSGVO/Security-Note:**
- Single-use ist Pflicht: Refs #790 — Match in EINER Query gegen Hash UND Cleartext (Legacy), aber Treffer wird direkt deleted.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-06 — Backup-Codes regenerieren (alte ungültig)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_backup_codes.py` |

**Voraussetzung:** ENT-MFA-03; bestehende Backup-Codes gespeichert (mind. 1 noch nicht verbraucht, notiert).

**Vorbereitung:**
- Login + MFA-Verify.
- AuditLog vor Test prüfen.
- Aktuellen TOTP-Code aus App parat haben.

**Schritte:**
1. `/mfa/settings/` aufrufen → Button „Codes neu erzeugen" oder Direkt-POST auf `/mfa/backup-codes/regenerate/`.
2. Im Regenerate-Form aktuellen TOTP-Code eingeben → Submit.
3. Auf `/mfa/backup-codes/` werden 10 **neue** Codes einmalig angezeigt.
4. Logout, dann neuer Login → `/mfa/verify/` → Modus „Backup-Code" → einen **alten** Code aus Schritt-Vor-Test eingeben.

**Erwartetes Ergebnis:**
- Schritt 2 (negativ ohne TOTP): „TOTP-Code fehlt oder ist ungültig.", Redirect auf `/mfa/settings/`, keine Regen.
- Schritt 2 (positiv): Redirect `/mfa/backup-codes/`. AuditLog: `BACKUP_CODES_REGENERATED` mit `detail.count=10`.
- Alle vorherigen `StaticToken`-Einträge sind gelöscht; 10 neue mit SHA-256-Hashes vorhanden.
- Schritt 4: alter Code wird **nicht** akzeptiert (HTTP 200 Form-Fehler, `MFA_FAILED`-Audit).
- Rate-Limit `5/min/User` auf `MFARegenerateBackupCodesView.post`.

**DSGVO/Security-Note:**
- Aktueller TOTP wird vor Regen geprüft (Defense-in-Depth gegen gestohlene Session).
- `generate_backup_codes` läuft `@transaction.atomic` — partielle Replacements unmöglich.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-07 — MFA deaktivieren (mit Sudo-Mode)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`) | C/F/S | ⚪ | `test_mfa_setup_flow.py` |

**Voraussetzung:** ENT-MFA-01; `is_mfa_enforced=False` (kein User-Flag, kein facility-weiter Zwang).

**Vorbereitung:**
- Login + MFA-Verify.
- Sudo-Mode ist NICHT aktiv (Default nach Login, `sudo_until` fehlt in Session).

**Schritte:**
1. `/mfa/settings/` öffnen.
2. Button „2FA deaktivieren" klicken → POST auf `/mfa/disable/`.
3. Erste Reaktion: `RequireSudoModeMixin` redirected auf `/sudo/?next=/mfa/disable/`.
4. Auf `/sudo/` Pwd `anlaufstelle2026` eingeben → Submit.
5. Redirect zurück auf `/mfa/disable/` (POST).
6. MFA-Disable-Action wird ausgeführt.
7. Versuch B: User mit `is_mfa_enforced=True` versucht Disable.

**Erwartetes Ergebnis:**
- Schritt 6: `TOTPDevice.objects.filter(user=miriam).delete()` löscht alle Devices, `request.session.pop("mfa_verified")`. Redirect `/mfa/settings/`. Status zeigt „2FA nicht aktiv". Success-Message: „Zwei-Faktor-Authentifizierung deaktiviert."
- AuditLog: `MFA_DISABLED` mit `detail.event="mfa_disabled"`.
- Rate-Limit `RATELIMIT_MUTATION` (Default: z.B. 30/h) auf `MFADisableView.post`.
- Versuch B: Error-Message „Zwei-Faktor-Authentifizierung ist für dein Konto verpflichtend.", Redirect auf `/mfa/settings/`, Devices unverändert. Kein AuditLog `MFA_DISABLED`.

**DSGVO/Security-Note:**
- `RequireSudoModeMixin` (Refs #683) erzwingt Re-Auth — gestohlene Session reicht nicht.
- Backup-Codes (`StaticDevice`) bleiben in DB — werden beim nächsten MFA-Setup überschrieben.

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-08 — `mfa_required=True` erzwingen (Setup-Redirect)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | leitung (`emma`) | C || `test_mfa_setup_flow.py` |

**Voraussetzung:** `emma` (LEAD), `mfa_required=True` per Admin-UI gesetzt; bisher kein TOTPDevice.

**Vorbereitung:**
- Admin setzt `User.mfa_required=True` für `emma`.
- DB: `TOTPDevice.objects.filter(user=emma, confirmed=True).exists() == False`.

**Schritte:**
1. `/login/` mit `emma` + Pwd → Submit.
2. `/clients/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: Login OK (HTTP 302).
- Schritt 2: `MFAEnforcementMiddleware` greift (`is_mfa_enforced=True`, kein Device) → 302 Redirect auf `/mfa/setup/`.
- Solange `emma` kein bestätigtes Device hat, sind alle Routes außer EXEMPT_URLS (`/login/`, `/logout/`, `/mfa/`, `/static/`, `/i18n/`, `/health/`, `/sw.js`, `/manifest.json`, `/auth/offline-key-salt/`, `/password-change/`, `/password-reset/`) gesperrt.
- Nach erfolgreichem Setup (analog ENT-MFA-01) wird der reguläre Flow fortgesetzt.

**DSGVO/Security-Note:**
- Property `User.is_mfa_enforced` aggregiert User- und Facility-Flags (`mfa_required=True ODER facility.settings.mfa_enforced_facility_wide=True`).

**Status:** ☐ Offen

---

### TC-ID: ENT-MFA-09 — `mfa_enforced_facility_wide=True` (alle User der Facility)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| MFA | fachkraft (`miriam`), assistenz (`lena`), leitung (`emma`) | C || `test_mfa_setup_flow.py` |

**Voraussetzung:** Facility-Settings `mfa_enforced_facility_wide=True` (über Admin-UI oder Shell gesetzt). Keiner der drei Test-User hat ein Device.

**Vorbereitung:**
- `facility.settings.mfa_enforced_facility_wide = True; facility.settings.save()`.
- Alle User der Facility ohne TOTPDevice.

**Schritte:**
1. Login als `miriam` → Pwd → versucht `/clients/`.
2. Login als `lena` → Pwd → versucht `/`.
3. Login als `emma` → Pwd → versucht `/cases/`.

**Erwartetes Ergebnis:**
- Bei allen drei: `MFAEnforcementMiddleware` redirected auf `/mfa/setup/` (Case 1 in `_required_redirect`).
- `is_mfa_enforced` kommt durch `facility.settings.mfa_enforced_facility_wide=True` zustande, auch wenn `User.mfa_required=False`.
- Ohne Setup keine App-Nutzung außer EXEMPT_URLS.
- Nach Setup landet jeder User regulär im Zielsystem.

**DSGVO/Security-Note:**
- Facility-weite Erzwingung ist Admin-Werkzeug für Care-Provider mit erhöhtem Schutzbedarf (z.B. Suchthilfe + Art. 9 DSGVO).
- Audit aller Setups: `MFA_ENABLED`-Eintrag pro User.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>👤 ACCT — Account & Profil (5 Cases)</strong></summary>

**Routen:** `/account/`, `/i18n/setlang/`, `/auth/offline-key-salt/` 
**Views:** `src/core/views/account.py` (`AccountProfileView`), `src/core/views/auth.py` (`OfflineKeySaltView`, `set_user_language`) 
**Mixin:** `AssistantOrAboveRequiredMixin` (Account ist für Assistenz und höher) 
**Services:** `src/core/models/user.py` (`User.ensure_offline_key_salt`) 
**Modelfelder:** `User.preferred_language`, `User.phone`, `User.notes`, `User.offline_key_salt` 
**E2E-Coverage:** `test_account_profile.py` 
**Spezial-Setup:**
- Profil-Edit ist (Stand: aktueller Code) READ-ONLY-Dashboard mit Stats-Widget; Stamm-Pflege (Telefon/Notes) läuft über die Admin-UI (`/admin-mgmt/`) — TC-ACCT-03 entsprechend.
- Sprache wird beim Setlang-Endpoint sowohl in Session/Cookie (Django-Default) ALS AUCH auf `User.preferred_language` persistiert.
- Salt-Endpoint ist POST-only (kein GET), damit der Aufruf nicht in der Browser-Historie landet.

---

### TC-ID: ENT-ACCT-01 — Profil-Detail anzeigen (Username, E-Mail, Rolle, Facility)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C/F/S | ✓ | `test_account_profile.py` |

**Voraussetzung:** eingeloggt, MFA-Status egal (für Profil keine Sondergates).

**Vorbereitung:**
- Login als `miriam`.
- AuditLog `LOGIN`-Eintrag erzeugt heute (für recent_events-Widget irrelevant, aber Datenbasis).
- Mind. 1 WorkItem assigned_to=`miriam` (aus Seed).

**Schritte:**
1. `/account/` aufrufen.
2. Profilbereich (Username, Display-Name, E-Mail, Rolle, Facility) prüfen.
3. Stats-Widget prüfen (`events_today`, `open_cases`, `my_open_tasks`, `total_open_tasks`).
4. „Zuletzt besucht"-Liste prüfen (RecentClientVisit, max 8).
5. „Letzte Ereignisse"-Liste (Created_by=user, max 10).
6. Offene Aufgaben + abgeschlossene (max 10 / 5).

**Erwartetes Ergebnis:**
- HTTP 200, Template `core/account/profile.html` rendert.
- Sichtbar: `miriam`, `Miriam Schmidt`, Rolle „Fachkraft", Facility-Name aus Seed.
- Stats-Counts entsprechen DB-Wahrheit (Filter auf `current_facility` und sichtbare Events `Event.objects.visible_to(user)`).
- Querysets sind `current_facility`-gescoped (Multi-Facility-Sicherheit).
- `AssistantOrAboveRequiredMixin` blockiert nichts für `miriam` (Rolle STAFF >= ASSISTANT).

**DSGVO/Security-Note:**
- Read-Only-Profilseite — keine Self-Service-Pflege sensitiver Felder hier (siehe ENT-ACCT-03).

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-02 — Sprache wechseln DE↔EN (Session-Cookie + User-Pref)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C/F/S | ⚪ | `test_account_profile.py` |

**Voraussetzung:** eingeloggt; Default-Sprache `de`.

**Vorbereitung:**
- Login als `miriam`, `User.preferred_language="de"`.
- Locale-Files `de` und `en` kompiliert (`make compile-messages`).

**Schritte:**
1. Im Header/Footer Sprachumschalter klicken (Form POST `/i18n/setlang/` mit `language=en`).
2. Beliebige Seite wie `/clients/` neu laden — UI-Strings auf Englisch.
3. DB: `User.preferred_language` lesen.
4. Logout, Re-Login.
5. Erneut `/clients/` aufrufen.

**Erwartetes Ergebnis:**
- Schritt 1: HTTP 302 (Django `set_language`-Default zu `next` oder `Referer`); Session-Sprach-Cookie (`django_language`) wird gesetzt.
- Schritt 2: UI auf Englisch (z.B. „Clients" statt „Klient*innen").
- Schritt 3: `User.preferred_language=="en"` (durch `User.objects.filter(pk=user.pk).update(preferred_language=language)` in `set_user_language`).
- Schritt 5: UI bleibt auf Englisch — Persistenz auf User-Modell wirkt nach Re-Login (UserLanguageMiddleware liest die User-Pref).
- Validierung: nur Sprachcodes aus `settings.LANGUAGES` (`de`, `en`) werden akzeptiert; `language=fr` wird ignoriert.

**DSGVO/Security-Note:**
- POST-only-Endpoint via `@require_POST` — keine CSRF-Bypass-Möglichkeit über GET.

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-03 — Telefon-Nummer + Notes pflegen (Admin-UI)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | admin → Pflege für `miriam` | C/F/S | ⚪ | — (manuell, Admin-UI) |

**Voraussetzung:** admin eingeloggt mit MFA-Verify (Admin braucht MFA-Pflicht in Prod).

**Vorbereitung:**
- Login als `admin`, ggf. MFA-Setup.
- Admin-UI `/admin-mgmt/` zugänglich.

**Schritte:**
1. `/admin-mgmt/core/user/` öffnen.
2. `miriam` auswählen.
3. Felder `phone` (z.B. `+49 30 1234567`) und `notes` (z.B. „Mo-Mi vor Ort, Do remote") befüllen → Save.
4. `/account/` als `miriam` aufrufen — Werte angezeigt? (UI-Komponente prüft, ob Phone/Notes überhaupt im Profil-Template sichtbar sind.)

**Erwartetes Ergebnis:**
- DB: `miriam.phone == "+49 30 1234567"`, `miriam.notes == "Mo-Mi vor Ort, Do remote"`.
- Audit-Trail: Standard-Django-Admin `LogEntry` (NICHT eigener AuditLog — nur `User_role_changed` etc. ist gehookt).
- Felder sind als CharField/TextField persistiert (siehe `User.phone`, `User.notes` in `src/core/models/user.py`).
- Im Profil ggf. nur lesend angezeigt (Self-Service-Edit nicht implementiert).

**DSGVO/Security-Note:**
- Phone/Notes sind Stammdaten — RLS (Refs ADR-001) sichert ohnehin facility-Scoping.
- Kein Self-Service: bewusste Org-Entscheidung, Mitarbeiter-Stammdaten via Leitung pflegen lassen.

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-04 — Offline-Key-Salt-Endpoint (Rate-Limit 10/min/User)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C || `test_account_profile.py` |

**Voraussetzung:** eingeloggt.

**Vorbereitung:**
- DB: `User.offline_key_salt` für `miriam` ist leer (Initial-Zustand).
- Cache leer (für Rate-Limit-Reset).

**Schritte:**
1. POST auf `/auth/offline-key-salt/` (mit CSRF-Token, ohne Body) durchführen — z.B. via Frontend-Bootstrap nach Login.
2. JSON-Response inspizieren: `{"salt": "<base64url>"}`.
3. Format prüfen: 16-Byte Base64URL ohne Padding.
4. Endpoint sofort nochmal aufrufen → Salt soll **identisch** sein (Lazy-Persist, keine Rotation).
5. 10× weitere POSTs in <1 min → 11. Versuch sollte HTTP 429 liefern.
6. AuditLog `OFFLINE_KEY_FETCH` prüfen.
7. GET-Versuch auf gleicher URL.

**Erwartetes Ergebnis:**
- Schritt 2: HTTP 200, JSON-Body, `salt`-Feld 22 Zeichen lang (16 Bytes Base64URL ohne Padding, vom `ensure_offline_key_salt`-Service).
- Schritt 3: `User.offline_key_salt` ist persistiert (DB).
- Schritt 4: gleicher Salt-Wert (kein Re-Generate).
- Schritt 5: 11. POST → HTTP 429 (Rate-Limit `10/m/User`, `block=True`, in `OfflineKeySaltView.post`).
- Schritt 6: pro Aufruf (auch innerhalb Rate-Limit) ein AuditLog `OFFLINE_KEY_FETCH` mit `target_obj=user`, `facility`, `detail.event="offline_key_salt_fetched"`.
- Schritt 7: HTTP 405 Method Not Allowed (View ist POST-only).

**DSGVO/Security-Note:**
- POST-only verhindert Eintrag in Browser-History.
- Salt ist 128 Bit (`secrets.token_bytes(16)`) — Brute-Force-resistente PBKDF2-Eingabe.
- AuditLog-Stream zeigt Mass-Fetches an (Forensik bei kompromittierter Session).

**Status:** ☐ Offen

---

### TC-ID: ENT-ACCT-05 — Offline-Key-Salt nach Pwd-Wechsel rotieren

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Acct | fachkraft (`miriam`) | C || `test_account_profile.py` |

**Voraussetzung:** ENT-ACCT-04 lief; `miriam.offline_key_salt` enthält einen Wert.

**Vorbereitung:**
- Aktuellen Salt-Wert (Salt_alt) notieren (DB-Read oder weitere POST-Antwort vor dem Pwd-Wechsel).

**Schritte:**
1. `/password-change/` aufrufen.
2. Altes Pwd + neues Pwd setzen (siehe ENT-AUTH-08 für Validatoren).
3. Submit → Redirect `/`.
4. Erneut POST `/auth/offline-key-salt/` durchführen.
5. Neues Salt mit Salt_alt vergleichen.

**Erwartetes Ergebnis:**
- Nach Schritt 3: `User.offline_key_salt = ""` (`CustomPasswordChangeView.form_valid` setzt explizit leer; `update_fields=["must_change_password", "offline_key_salt"]`).
- Schritt 4: `ensure_offline_key_salt(user)` generiert neuen 16-Byte-Salt → DB-Persist.
- Schritt 5: Salt_neu ≠ Salt_alt (mit überwältigender Wahrscheinlichkeit; kollisionsfrei für 128-Bit-Token).
- Alte client-seitige IndexedDB-Records werden beim nächsten Login als `garbage` verworfen — Frontend re-derived Schlüssel mit neuem Salt+Pwd.
- AuditLog: weiterer `OFFLINE_KEY_FETCH`-Eintrag.

**DSGVO/Security-Note:**
- Pwd-Wechsel rotiert auch Offline-Schlüssel — Schlüsselmaterial im Browser ist nicht mehr nutzbar, falls jemand dazwischen das alte Pwd erlangt hat (Refs `CustomPasswordChangeView.form_valid`-Kommentar).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🛡️ SUDO — Re-Authentication (4 Cases)</strong></summary>

**Routen:** `/sudo/` 
**Views:** `src/core/views/sudo_mode.py` (`SudoModeView`) 
**Services:** `src/core/services/sudo_mode.py` (`enter_sudo`, `is_in_sudo`, `clear_sudo`, `RequireSudoModeMixin`) 
**Settings:** `SUDO_MODE_ENABLED=True` (Prod/Dev), `SUDO_MODE_TTL_SECONDS=900` (15 Min) 
**Genutzt von:** `MFADisableView` (LoginRequired+RequireSudoMode); ggf. DSGVO-Export, Pseudonym-Daten-Download (Refs #683) 
**E2E-Coverage:** `test_security_hardening.py` (indirekt über MFA-Disable-Flow), Unit-Tests in `src/tests/test_sudo_mode.py` 
**Spezial-Setup:**
- In Tests deaktiviert: `settings/test.py` setzt `SUDO_MODE_ENABLED=False`. Für E2E-Tests bleibt es aktiv.
- AuditAction `SUDO_MODE_ENTERED` existiert; `SUDO_MODE_EXIT` existiert **nicht** im Code (kein expliziter Exit-Audit beim Logout — `clear_sudo` ist still). Entsprechend testen wir nur `SUDO_MODE_ENTERED`.

---

### TC-ID: ENT-SUDO-01 — Sudo-Mode betreten via Re-Auth

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** eingeloggt mit MFA-Verify.

**Vorbereitung:**
- Login als `miriam`, MFA verifiziert.
- DB: `request.session.get("sudo_until")` ist `None` (frische Session).
- Sudo-pflichtige Aktion vorbereitet (z.B. „2FA deaktivieren" auf `/mfa/settings/`).

**Schritte:**
1. Auf `/mfa/settings/` „2FA deaktivieren" klicken → POST `/mfa/disable/`.
2. `RequireSudoModeMixin.dispatch` greift, da `is_in_sudo(request)==False` → 302 auf `/sudo/?next=/mfa/disable/`.
3. Auf `/sudo/`-Form Pwd `anlaufstelle2026` eingeben → Submit (POST).
4. Session-State und Redirect prüfen.

**Erwartetes Ergebnis:**
- Schritt 3: `authenticate(request, username="miriam", password="anlaufstelle2026")` returnt User; `enter_sudo(request)` setzt `session["sudo_until"] = int(time.time()) + 900`.
- AuditLog: `SUDO_MODE_ENTERED` mit `user=miriam`, `target_type="User"`, `target_id=miriam.pk`, `detail.next="/mfa/disable/"`.
- HTTP 302 Redirect auf `safe_redirect_path("/mfa/disable/")`.
- Folge-POST auf `/mfa/disable/` ist jetzt erlaubt (`is_in_sudo==True`).
- Falsches Pwd → HTTP 403 mit gerendertem `auth/sudo_mode.html` und Error-Message „Passwort ist nicht korrekt.", **kein** `enter_sudo`-Aufruf.

**DSGVO/Security-Note:**
- Schutz gegen Session-Hijack (Refs #683): gestohlenes Cookie reicht nicht ohne aktuelles Pwd.
- `safe_redirect_path` blockiert offene Redirects auf externe URLs.
- Rate-Limit `5/m/User` auf `SudoModeView.post` (Brute-Force-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-02 — Sudo-Timeout nach 15 Min → Re-Auth nötig

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** ENT-SUDO-01; aktive Session mit Sudo-Mode.

**Vorbereitung:**
- Test-Setting `SUDO_MODE_TTL_SECONDS=2` (für schnellen Test) ODER `session["sudo_until"]` in der Session-DB manipulieren auf `now-1`.
- Sudo-pflichtige Aktion bereit.

**Schritte:**
1. Direkt nach Sudo-Entry erste sudo-pflichtige Aktion ausführen → erfolgreich.
2. 16 Min warten (oder mit gemockter `time.time()` / Setting).
3. Erneut sudo-pflichtige Aktion versuchen.

**Erwartetes Ergebnis:**
- Schritt 1: erfolgreich (Sudo-Window noch offen).
- Schritt 3: `is_in_sudo(request)` returnt `False`, da `time.time() >= session["sudo_until"]`.
- 302 Redirect auf `/sudo/?next=<originalpfad>` — User muss erneut sein Pwd eingeben.
- Nach erneuter Re-Auth: AuditLog hat einen ZWEITEN `SUDO_MODE_ENTERED`-Eintrag.
- Setting `SUDO_MODE_TTL_SECONDS` aus `os.environ.get("SUDO_MODE_TTL_SECONDS", "900")`.

**DSGVO/Security-Note:**
- 15-Min-Fenster begrenzt Angriffsfläche bei kurzzeitiger Pwd-Kompromittierung.

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-03 — Sudo-pflichtige View ohne Sudo → Redirect /sudo/

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** eingeloggt; `session["sudo_until"]` nicht gesetzt.

**Vorbereitung:**
- Login + MFA-Verify, kein Sudo-Mode.
- Sudo-Required-View ist `MFADisableView`.

**Schritte:**
1. Direkt POST/GET auf `/mfa/disable/` (z.B. via curl mit Session-Cookie oder Frontend-Klick).
2. Response inspizieren.
3. Auf `/sudo/?next=/mfa/disable/` landen, Form sehen.

**Erwartetes Ergebnis:**
- HTTP 302 Redirect: `Location: /sudo/?next=/mfa/disable/`.
- Hinweis: laut Code-Kommentar zur Aufgabe „403 + Redirect zu /sudo/" — der Code im `RequireSudoModeMixin.dispatch` macht **`return redirect(sudo_url)`** (302), keinen 403. Dieser Test-Case erwartet **302**, nicht 403 — damit bestätigen wir das tatsächliche Verhalten.
- `auth/sudo_mode.html` rendert mit Pwd-Form.
- Erst nach erfolgreicher Re-Auth (siehe ENT-SUDO-01) ist `/mfa/disable/` erreichbar.

**DSGVO/Security-Note:**
- Verhalten stimmt mit Service-Doc überein (`src/core/services/sudo_mode.py`): „Mixin RequireSudoModeMixin redirected zu /sudo/ mit ?next=".

**Status:** ☐ Offen

---

### TC-ID: ENT-SUDO-04 — AuditLog SUDO_MODE_ENTERED nach Re-Auth

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sudo | fachkraft (`miriam`) | C || `test_security_hardening.py` |

**Voraussetzung:** ENT-SUDO-01 lief.

**Vorbereitung:**
- AuditLog-Filter `action=SUDO_MODE_ENTERED` für `miriam` vor und nach Test bereitstellen.

**Schritte:**
1. Nach erfolgreicher Re-Auth (Schritt 3 in ENT-SUDO-01) AuditLog inspizieren.
2. Abfrage: `AuditLog.objects.filter(user=miriam, action="sudo_mode_entered").latest("timestamp")`.
3. (Negativ-Test) Logout durchführen — prüfen, dass **kein** SUDO_MODE_EXIT-Audit-Action existiert (die Action ist im Code nicht definiert).

**Erwartetes Ergebnis:**
- Schritt 2: ein neuer Eintrag mit:
  - `action="sudo_mode_entered"`
  - `user=miriam`
  - `facility=miriam.facility`
  - `target_type="User"`, `target_id=str(miriam.pk)`
  - `detail={"next": "<safe redirect path>"}`
  - `timestamp` ~ jetzt
- Schritt 3: `AuditLog.Action`-TextChoices enthält `SUDO_MODE_ENTERED`, **NICHT** `SUDO_MODE_EXIT`. Das ist eine bewusste Aktualisierung der Aufgabenstellung — der Code-Status ist Truth Source. `clear_sudo(request)` läuft still (z.B. beim Logout via Session-Pop), erzeugt **keinen** dedizierten Audit-Eintrag.

**DSGVO/Security-Note:**
- AuditLog-Eintrag ermöglicht Forensik bei kompromittierten Sessions: wann hat der User Sudo-Fenster geöffnet?
- Kein Exit-Audit ist OK — Sudo-Fenster ist zeitbeschränkt; Logout/Session-End räumt implizit auf.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📲 PWA — Progressive Web App (5 Cases)</strong></summary>

**Routen:** `/sw.js`, `/manifest.json`, `/offline/` 
**Views:** `src/core/views/pwa.py` (`ServiceWorkerView`, `ManifestView`, `OfflineFallbackView`) 
**Statisch:** `src/static/manifest.json`, `src/static/js/sw.js`, `src/templates/offline.html` 
**Cache-Versionierung:** `CACHE_NAME = "anlaufstelle-v9"` in `sw.js` 
**App-Shell:** `/static/css/styles.css`, Icons (192/512 PNG+SVG), `/offline/` 
**Strategien (laut sw.js):**
- Static-Assets: stale-while-revalidate (Refs #618)
- HTML/HTMX-Navigation: network-first → Cache-Fallback → `/offline/`-Template (Refs #701)
- POST/PUT auf URL_PATTERNS.QUEUE_PATTERNS bei Netzausfall: IndexedDB-Queue via `requestQueueAck` (Refs #573, #662)
- Multipart-POST: kein Queue, sofortiger 503 mit „Datei-Upload erfordert Internetverbindung"
- Attachment-/Export-Downloads: network-only (Refs #751)
**E2E-Coverage:** `test_pwa_offline.py` 
**Spezial-Setup:**
- Service-Worker registriert sich nur über HTTPS (oder localhost). E2E-Server auf `https://localhost:8844/` ist OK.
- Manifest muss von `/manifest.json` (Root-Scope!) geliefert werden, nicht aus `/static/`. Begründung im View-Docstring: Android Chrome akzeptiert sonst Scope `/` nicht.
- Service-Worker-Header: `Service-Worker-Allowed: /` für Root-Scope.

---

### TC-ID: ENT-PWA-01 — Manifest.json (gültiges JSON, name + icons)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | unauthentifiziert / beliebig | C/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** Statische Files vorhanden (`make collectstatic` für Prod, in dev/e2e direkt aus `src/static/`).

**Vorbereitung:**
- Server läuft auf `https://localhost:8844/`.

**Schritte:**
1. `https://localhost:8844/manifest.json` aufrufen (auch ohne Login, da MFA-EXEMPT).
2. Response-Header prüfen: `Content-Type: application/manifest+json`.
3. Body als JSON parsen.
4. Felder validieren: `name`, `short_name`, `id`, `start_url`, `scope`, `display`, `background_color`, `theme_color`, `icons`.
5. Icons-Liste: 4 Einträge (192/512 PNG, 192/512 SVG), `purpose=any` bzw. `any maskable`.

**Erwartetes Ergebnis:**
- HTTP 200, MIME `application/manifest+json`.
- Body parst als JSON ohne Fehler.
- Pflichtfelder vorhanden:
  - `"name": "Anlaufstelle"`, `"short_name": "Anlaufstelle"`
  - `"id": "/"`, `"start_url": "/"`, `"scope": "/"`
  - `"display": "standalone"`
  - `"background_color": "#f9fafb"`, `"theme_color": "#4f46e5"`
  - `"icons"` mit ≥ 4 Einträgen, mind. ein 192×192 und ein 512×512 PNG.
- Datei wird mit `lru_cache(maxsize=1)` aus dem Filesystem gelesen — Performance-Test: 2. Aufruf ohne Disk-I/O.

**DSGVO/Security-Note:**
- Manifest enthält keine PII; rein statische Konfiguration.
- Scope `/` setzt voraus, dass Manifest auf Root-Pfad ausgeliefert wird (Android Chrome strict).

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-02 — Service-Worker Registrierung + Cache-Strategie

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C/F/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** HTTPS; Browser unterstützt SW.

**Vorbereitung:**
- Privates Fenster, DevTools → Application → Service Workers offen.

**Schritte:**
1. Beliebige App-Seite aufrufen (z.B. `/login/`).
2. SW-Registrierung in Application-Tab beobachten.
3. Direkt `https://localhost:8844/sw.js` aufrufen → Header prüfen.
4. Application → Cache Storage → `anlaufstelle-v9` öffnen, App-Shell-URLs prüfen.
5. Reload-Test: zweimal Static-Asset (z.B. `/static/css/styles.css`) abrufen — Network-Tab beobachten (stale-while-revalidate).

**Erwartetes Ergebnis:**
- Schritt 2: SW ist `activated and is running`, Scope `/`.
- Schritt 3: HTTP 200, `Content-Type: application/javascript`, `Service-Worker-Allowed: /`.
- Schritt 4: Cache `anlaufstelle-v9` enthält App-Shell aus `APP_SHELL`:
  - `/static/css/styles.css`
  - `/static/icons/icon-192.png`, `/static/icons/icon-512.png`
  - `/static/icons/icon-192.svg`, `/static/icons/icon-512.svg`
  - `/offline/`
- Schritt 5: zweiter Reload servert aus Cache (Cache-Hit), gleichzeitig Hintergrund-Fetch der neuen Version → Cache-Update (Refs #618).
- Bei `caches.keys()`-Wechsel werden alte CACHE_NAMEs (v8 etc.) im `activate`-Event gelöscht.

**DSGVO/Security-Note:**
- SW-Scope-Restriction (Service-Worker-Allowed) verhindert, dass aus `/static/`-Pfaden Root-Skripte registriert werden.
- App-Shell enthält keine PII; verschlüsselte Klienten-Daten leben in IndexedDB (separate Store), nicht im SW-Cache.

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-03 — Install-Prompt auf Chromium (Add to Home Screen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C | ✓ | — (manuell) |

**Voraussetzung:** HTTPS (kein self-signed Cert in echtem Test, sonst Chromium verweigert PWA-Install — für E2E auf `https://localhost:8844/` ist Chromium toleranter, aber `beforeinstallprompt` triggert nicht zwingend; für formal vollständigen Test reale Domain mit Cert nötig).

**Vorbereitung:**
- Chromium oder Edge mit aktivem User-Engagement (mehrere Visits + 30s aktiv).
- DevTools → Application → Manifest prüfen: keine Errors.

**Schritte:**
1. App `/` öffnen, einige Sekunden interagieren.
2. Chrome-Menü → „App installieren" / Install-Icon in Adressleiste.
3. Bei mobilem Chrome: 3-Punkt-Menü → „Zum Startbildschirm hinzufügen".
4. Bestätigen.
5. App-Icon auf Home-Screen / im Launcher verifizieren.
6. Vom Icon starten — App öffnet im standalone-Modus (kein Browser-Chrome).

**Erwartetes Ergebnis:**
- Manifest-Validierung in DevTools zeigt keine Errors („App is installable").
- Install-Prompt erscheint (Chromium-Heuristiken erfüllt: Manifest valid, SW registriert, HTTPS, Engagement).
- Installierte App startet mit `display=standalone`, Theme-Color `#4f46e5`, Background `#f9fafb`.
- Start-URL `/` wird geöffnet.

**DSGVO/Security-Note:**
- Keine Install-Side-Effects, die PII speichern. App-Daten leben weiter in browserseitiger IndexedDB (Origin-gebunden) — DSGVO-Datenminimierung auf Logout via Clear-Site-Data (siehe ENT-AUTH-06).

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-04 — Offline-Page-Fallback

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | fachkraft (`miriam`) | C/F/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** SW registriert + aktiviert (siehe ENT-PWA-02); `/offline/` ist im App-Shell-Cache. **Seit #1544/#1499 :** `/offline/` ist kein Einstiegspunkt mehr, sondern nur noch terminaler Fallback — `/clients/`, `/workitems/` und `/` haben eigene Shells (siehe ENT-PWA-06).

**Vorbereitung:**
- Login als `miriam`.
- DevTools → Application → Service Workers: „Offline" aktivieren ODER Network → Throttling → „Offline".
- Mind. einmal `/clients/` und `/cases/` online aufgerufen, damit der Cache-Fallback befüllt ist (Default-HTML-Cache greift gleich, wenn vorhanden).

**Schritte:**
1. DevTools auf „Offline" setzen.
2. Eine **bisher nicht** besuchte Route aufrufen, die auch keine eigene Offline-Shell hat, z.B. `/reports/?filter=foo`.
3. `/cases/` aufrufen (Fälle haben **keine** eigene Offline-Shell).
4. Klienten-Detail-URL aufrufen, die ohnehin Offline-Variante hat: `/clients/<pk>/`.
5. `/offline/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Schritt 2: SW-Fetch-Listener probiert Netz (fail) → versucht Cache-Match (Miss) → liefert `OFFLINE_FALLBACK_URL` (`/offline/`) **in-place** als Fallback (URL bleibt kanonisch). Response: HTTP 200 mit der statischen Selbsthilfe-Seite „Keine lokale Ansicht verfügbar" (Links zu `/clients/`, `/workitems/`, `/`, „Erneut versuchen"; seit #1494 rein statisches Markup, kein JS-abhängiger Spinner).
- Schritt 3: `/cases/` hat keine eigene Shell (Fälle sind bewusst **nicht** Teil des Tab-Shell-Zielbilds) → landet wie Schritt 2 in-place auf demselben Fallback.
- Schritt 4: SW erkennt via `URL_PATTERNS.extractClientPk` einen Klienten-Pfad und liefert seit #1322 den pk-losen Client-Shell **in-place** an der kanonischen URL aus (kein Redirect mehr; `offline-client-view.js` liest die pk aus `location.pathname` und rendert aus IndexedDB).
- Schritt 5: HTTP 200, Content-Type `text/html; charset=utf-8`, Body aus `render_to_string("offline.html")`.

**DSGVO/Security-Note:**
- Inline-CSS in `offline.html` notwendig, da SW im Offline-Fall nicht auf Static-Assets-Pipeline zugreifen kann (Refs #701).
- Offline-Klienten-Viewer rendert nur was lokal verschlüsselt vorliegt — kein Server-Round-Trip → keine ungewollte Re-Connect-Lecks.

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-05 — Update-Flow: neue SW-Version → User-Hinweis + Reload

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | beliebig | C/S | ⚪ | — (manuell, schwer zu automatisieren) |

**Voraussetzung:** bisheriger SW mit `CACHE_NAME="anlaufstelle-v9"` aktiv; Code-Bump auf `v10` durchgeführt (oder Mock).

**Vorbereitung:**
- Initial-Setup: laden, SW aktivieren, App-Shell-Cache `anlaufstelle-v9` aufbauen.
- Server auf neue Version deployen, in der `CACHE_NAME` auf `anlaufstelle-v10` erhöht ist (Pre-Test: in `sw.js` editieren, Server reloaden).

**Schritte:**
1. App neu laden (Hard-Reload sind nicht nötig; Browser prüft SW automatisch).
2. DevTools → Application → Service Workers: „waiting to activate"-Status für die neue SW-Version sehen.
3. „skipWaiting"-Verhalten: laut Code ruft `install`-Handler `self.skipWaiting()` auf — alte SW wird sofort durch neue ersetzt.
4. `activate`-Handler löscht alte Caches via `caches.keys() … filter(k !== CACHE_NAME).delete()`.
5. App reloaden — beobachte Cache-Liste in DevTools (nur `anlaufstelle-v10` übrig).
6. Static-Assets werden bei Bedarf via stale-while-revalidate aus dem neuen Cache geliefert.

**Erwartetes Ergebnis:**
- Neue SW installiert + aktiviert sich automatisch (Refs `self.skipWaiting()` + `self.clients.claim()` im Code).
- Alte Cache-Stores (`anlaufstelle-v9`, ältere) sind entfernt.
- Nutzer-sichtbar: keine UI-Banner-Implementierung im aktuellen Code (Update-Toast wäre Custom-JS) — Reload reicht.
- Anmerkung: Aktuell **kein expliziter** „Update verfügbar"-Banner im Frontend — wenn die Aufgabe einen User-Hinweis fordert, ist das ein offener Punkt (manuell verifizierbar: kein Banner sichtbar, aber Update funktioniert silent).

**DSGVO/Security-Note:**
- Stale-while-revalidate (Refs #618) verhindert, dass alter Bug-Code über Cache-Lock festgehalten wird.
- Keine PII in SW-Cache, deshalb ist Cache-Wipe bei Version-Bump unkritisch.

**Status:** ☐ Offen

---

### TC-ID: ENT-PWA-06 — Offline-Tab-Shells (Aufgaben/Zeitstrom kalt-offline in-place) + `/offline/`-Rückbau

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Pwa | fachkraft (`miriam`) | C/F/S | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** SW registriert + aktiviert, `CACHE_NAME=anlaufstelle-v22` im App-Shell-Cache (Refs #1543); mindestens eine Person offline mitgenommen (siehe ENT-OFFL-01), davon idealerweise eine mit Aufgabe/Ereignis, plus ein personloser (anonymer) Offline-Eintrag aus vorheriger kalt-offline-Erfassung (ENT-PWA-Create-Shells, Refs #1499).

**Vorbereitung:**
- Login als `miriam`, mindestens eine Person „Offline mitnehmen".
- App **einmal online** ausliefern lassen (Kaltstart-Precache: `/workitems/` und `/` müssen als Shells im App-Shell-Cache liegen).
- DevTools → Network → Throttling → „Offline" (bzw. CDP `setOfflineMode`).

**Schritte:**
1. Kalt-offline direkt `/workitems/` aufrufen (Reload bzw. neuer Tab, keine vorherige Online-Navigation auf diese URL in dieser Session nötig).
2. Kalt-offline direkt `/` aufrufen.
3. Online zurückwechseln und `/` erneut aufrufen (Kontrollprobe).
4. Eine URL ohne eigene Shell offline aufrufen, z.B. `/reports/` (Kontrollprobe gegen ENT-PWA-04).

**Erwartetes Ergebnis:**
- Schritt 1: SW liefert die pk-lose Aufgabenlisten-Shell **in-place** an `/workitems/` aus (kein Redirect, URL bleibt kanonisch). Gerendert wird `listOfflineWorkItemsAggregated()` — Aufgaben **aller** gültigen mitgenommenen Personen-Bundles **plus** personlose/anonyme Standalone-Aufgaben, klar als „ohne Person"/„Person offline entfernt" markiert; Titel, Status-/Prioritäts-Badges und Fälligkeit spiegeln `inbox_content.html`/`_workitem_row.html`.
- Schritt 2: SW liefert die pk-lose Zeitstrom-Shell **in-place** an `/` aus. Gerendert wird `listOfflineEventsAggregated()` als chronologische „lokale Chronik" (neueste zuerst) über alle mitgenommenen Personen **plus** anonyme/personlose Einträge (als „Anonym" markiert); deutlicher Hinweis, dass nur die offline verfügbaren Vorgänge gezeigt werden, nicht die vollständige Historie der Einrichtung.
- Schritt 3: **Online bleibt `/` der echte Zeitstrom-Cockpit** (kein Offline-Shell sichtbar) — der SW-Zweig (`isZeitstromRoot`) greift ausschließlich im `respondWith`-catch (Netzfehler), der Online-Login-Redirect an `/` ist unberührt.
- Schritt 4: `/reports/` hat keine eigene Shell → Fallback auf `/offline/` (ENT-PWA-04) — die statische Seite „Keine lokale Ansicht verfügbar" mit Links zu `/clients/`, `/workitems/`, `/`.
- Insgesamt: Mit ENT-PWA-04 (Rückbau) + diesem Test ist das Zielbild „Eine Welt" abgedeckt — Personen (`/clients/`,), Aufgaben (`/workitems/`) und Zeitstrom (`/`) rendern offline in gewohnter Optik an ihrer kanonischen Adresse; `/offline/` ist reiner technischer Fallback ohne Produktpfad.

**DSGVO/Security-Note:**
- Beide Shells sind PII-frei im Markup (Alpine `x-show` auf vorberechneten Booleans, CSP-konform) — die eigentlichen Personendaten kommen ausschließlich client-seitig aus der verschlüsselten IndexedDB, kein Server-Round-Trip.
- Dieselbe read-only Gate-Logik wie die Personenliste: abgelaufene/schema-fremde Bundles werden beim Aggregieren nur ausgelassen (kein Purge als Render-Seiteneffekt); noch nicht synchronisierte Zeilen (modified/new/conflict/dead) bleiben laut S1-Invariante sichtbar.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>👥 CLIENT — Klient:innen-Management (16 Cases)</strong></summary>

**Routen:** `/clients/`, `/clients/new/`, `/clients/<uuid>/`, `/clients/<uuid>/edit/`, `/clients/<uuid>/export/json/`, `/clients/<uuid>/export/pdf/`, `/clients/<uuid>/delete/`, `/clients/trash/`, `/clients/<uuid>/restore/`, `/clients/<uuid>/anonymize/`, `/api/clients/autocomplete/` 
**Views:** `src/core/views/clients.py` (`ClientListView`, `ClientCreateView`, `ClientDetailView`, `ClientUpdateView`, `ClientAutocompleteView`, `ClientDataExportJSONView`, `ClientDataExportPDFView`) + `src/core/views/client_deletion.py` (`ClientDeleteRequestView`, `ClientTrashView`, `ClientRestoreView`, `ClientAnonymizeView`) 
**Services:** `src/core/services/clients.py` (`create_client`, `update_client`, `request_client_deletion`, `restore_client`, `track_client_visit`, `get_pending_deletion_request`, `pending_deletion_client_ids`, `anonymize_client_now`) 
**E2E-Coverage:** `test_clients_search.py`, `test_client_edit.py`, `test_client_deletion_workflow.py`, `test_client_deletion_status.py`, `test_client_autocomplete_recency.py`, `test_client_export.py`, `test_min_contact_stage_anonymous.py`, `test_contact_stage.py`, `test_fuzzy_search.py` 
**Spezial-Setup:** Cross-Facility-Tests benötigen `make seed FACILITIES=2` und 2 parallele Browser-Profile (`admin` in Facility 1, `admin_2` in Facility 2). DSGVO-Exporte erfordern Sudo-Re-Auth (Refs #683).

---

### TC-ID: ENT-CLIENT-01 — Klient:innen-Liste mit Pagination

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py` |


**Vorbereitung:**
- Mit `fachkraft` / `anlaufstelle2026` einloggen.
- Seed mit `make seed` (Standard-Scale legt mind. 25 Klient:innen an).

**Schritte:**
1. `/clients/` aufrufen.
2. Header „Personen" sowie Filter (Suche, Stage, Altersgruppe) prüfen.
3. Pagination am Fuß sichten (max. 20/Seite Default).
4. Auf „Seite 2" klicken — HTMX tauscht das Tabellen-Partial.

**Erwartetes Ergebnis:**
- Liste rendert max. 20 Klient:innen pro Seite mit Pseudonym, Stage-Badge, Altersgruppe und Datum des letzten Kontakts.
- HTMX-Tausch ohne Full-Page-Reload (Network-Tab: nur Partial `core/clients/partials/table.html`).
- Sortierung alphabetisch nach `pseudonym`, „Letzter Kontakt"-Spalte annotiert via `Max("events__occurred_at")`.

**DSGVO/Security-Note:**
- Liste enthält nur Pseudonyme (Art. 5 Datenminimierung).
- Cross-Facility-Daten unsichtbar dank `Client.objects.for_facility()` + RLS.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-02 — Klient:in anlegen (Pseudonym, Altersgruppe, Stage)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py::test_client_create_pseudonym_uniqueness` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eindeutiges Pseudonym vorbereiten (z.B. `Manuell-TC02-<random>`).

**Schritte:**
1. `/clients/new/` aufrufen — Headline „Neue Person".
2. Pseudonym `Manuell-TC02-<random>` eintragen.
3. Kontaktstufe auf „Identifiziert" lassen (Default).
4. Altersgruppe „18–26" wählen.
5. Notizen leer lassen.
6. Auf „Person anlegen" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/clients/<uuid>/` (Detail).
- Erfolgsmeldung „Person wurde angelegt." erscheint als Toast.
- Detail-Headline zeigt das eingegebene Pseudonym.
- Stage-Badge „Identifiziert", Altersgruppe „18–26" sichtbar.

**DSGVO/Security-Note:**
- `AuditLog` Action `CLIENT_CREATE` wird geschrieben (siehe `services/clients.py::create_client`).
- Activity-Eintrag „Person … angelegt" erscheint im Aktivitäts-Feed.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-03 — Anonyme:r Klient:in: Event ohne Klient:in mit DocType-Mindeststufe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_min_contact_stage_anonymous.py` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/events/new/` aufrufen.
2. Im Dropdown „Dokumentationstyp" die Option „Kontakt" wählen.
3. Hinweistexte um das Klient:innen-Feld beobachten.
4. Auf „Beratungsgespräch" wechseln und Hinweis erneut prüfen.
5. Mit „Kontakt" und ohne Klient:innen-Auswahl absenden.

**Erwartetes Ergebnis:**
- Bei „Kontakt" erscheint Text „… anonym gespeichert" — Klient:in optional.
- Bei „Beratungsgespräch" erscheint Hinweis „Mindest-Kontaktstufe …" — Klient:in zwingend Stage `qualified`.
- Submit ohne Klient:in (DocumentType „Kontakt") legt Event mit `is_anonymous=True` an, Detail-Seite zeigt Badge „Anonym".

**DSGVO/Security-Note:**
- Anonyme Events haben keine `client`-FK und sind nicht über Personenfilter rückführbar (Art. 5 Datenminimierung).
- Refs #394, #472, #486.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-04 — Klient:in bearbeiten + Stage hochstufen erzeugt Audit-Eintrag

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_client_edit.py::test_edit_contact_stage_saves` |

**Voraussetzung:** vorhandene Person mit Stage `identified` (z.B. `Blitz-08`)

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eigene Test-Person anlegen (Pseudonym `TC04-<random>`, Stage `identified`), um Seed-Daten nicht zu mutieren.

**Schritte:**
1. `/clients/?q=TC04` aufrufen, Person anklicken.
2. „Bearbeiten" klicken — Edit-Formular lädt mit vorausgefüllten Werten.
3. Pseudonym leicht ändern (z.B. `TC04-edit-<random>`).
4. Kontaktstufe von „Identifiziert" auf „Qualifiziert" wechseln.
5. „Speichern" klicken.
6. Im Admin-Bereich (`/admin/core/auditlog/`) den letzten Eintrag prüfen.

**Erwartetes Ergebnis:**
- Redirect auf Detail-Seite `/clients/<uuid>/`.
- Erfolgs-Toast „Person wurde aktualisiert.".
- Detail zeigt neues Pseudonym + Badge „Qualifiziert".
- Zwei `AuditLog`-Einträge: `CLIENT_UPDATE` (mit `detail.changed_fields=["pseudonym","contact_stage"]`) und `STAGE_CHANGE` (mit `old_stage=identified`, `new_stage=qualified`).
- Activity-Feed zeigt „… qualifiziert" + „… aktualisiert".

**DSGVO/Security-Note:**
- Stage-Wechsel zu `qualified` ist begründungspflichtig (Art. 5/6 — neuer Verarbeitungsumfang). Audit-Eintrag erfüllt Rechenschaftspflicht (Art. 5 Abs. 2).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-05 — Detail-View mit Timeline, aktiven Cases, WorkItems

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ✓ | `test_clients_search.py::test_client_detail_event_timeline` |

**Voraussetzung:** Seed-Person mit Events + offenen Cases (z.B. `Stern-42`)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Treffer anklicken.
2. Detail-View beobachten — Headline `Stern-42`.
3. Sektionen prüfen: Stage-Badge, „Kontakt-Chronik", „Aktive Fälle", offene WorkItems, ggf. aktive Bans.
4. Erste Event-Karte aufklappen / anklicken.

**Erwartetes Ergebnis:**
- Headline = Pseudonym.
- Badge „Qualifiziert".
- Event-Liste sortiert absteigend nach `occurred_at`, mit Document-Type-Label.
- Sektion „Aktive Fälle" listet offene Cases mit `lead_user`.
- WorkItems sortiert nach Priorität (URGENT → IMPORTANT → NORMAL).

**DSGVO/Security-Note:**
- `track_client_visit` legt `RecentClientVisit`-Eintrag an (für Recency-Sortierung im Autocomplete).
- Bei `contact_stage=qualified` wird `AuditLog.VIEW_QUALIFIED` geschrieben.
- Events höherer Sensitivity sind über `Event.objects.visible_to(user)` für niedrigere Rollen unsichtbar (Refs #522).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-06 — Header-Suche per Pseudonym-Substring (HTMX-Typeahead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_clients_search.py::test_client_list_search`, `test_fuzzy_search.py::test_global_dropdown_shows_similar` |


**Vorbereitung:**
- Mit `fachkraft` einloggen, auf Dashboard `/`.

**Schritte:**
1. Header-Suchfeld (`[data-testid='global-search-input']`) anklicken.
2. `Stern` eintippen (kein Enter).
3. HTMX-Dropdown mit Treffern beobachten.
4. Variante: Tippfehler `Schmitt` (vorher `Schmidt`-Person anlegen) → Sektion „Ähnliche Pseudonyme".

**Erwartetes Ergebnis:**
- Dropdown öffnet via HTMX nach ~150 ms Debounce.
- Sektion „Personen" zeigt Substring-Match (`Stern-42`).
- Bei Tippfehler erscheint zusätzliche Sektion „Ähnliche Pseudonyme" (pg_trgm Similarity > 0.3, Refs #536).
- Klick auf Treffer navigiert zu `/clients/<uuid>/`.

**DSGVO/Security-Note:**
- Suchergebnisse sind facility-gescoped (RLS) — Pseudonyme aus anderen Einrichtungen erscheinen nicht.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-07 — Autocomplete im Event-Form (Recency-Sortierung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_client_autocomplete_recency.py` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/events/new/` aufrufen.
2. Klient:innen-Feld (`input[placeholder='Pseudonym eingeben...']`) anklicken — ohne Tippen.
3. Dropdown (`[role='listbox']`) erscheint mit `[role='option']`-Einträgen.
4. Reihenfolge mit `/api/clients/autocomplete/?q=` (Browser-Devtools) abgleichen.
5. `Stern` eintippen — Filter aktiv, Recency-Order bleibt.
6. Treffer per Klick übernehmen.

**Erwartetes Ergebnis:**
- Dropdown öffnet sofort beim Fokus (Alpine.js `@focus`-Handler).
- API liefert max. 30 Einträge sortiert nach `last_contact desc nulls_last`, dann `pseudonym`.
- Frontend-Reihenfolge identisch zur API.
- Klick auf Treffer befüllt Feld mit Pseudonym, Dropdown schließt.

**DSGVO/Security-Note:**
- Autocomplete ist rate-limited mit 30/Min/User (Refs #737, `block=True` → 429 statt schweigender Drop).
- `min_stage`-Param filtert Personen unterhalb der DocumentType-Mindeststufe (Refs #507).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-08 — JSON-Datenauskunft (LEAD+, Sudo-Re-Auth, Audit)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | leitung | C/F/S | ⚪ | `test_client_export.py::TestClientExportJSON` |

**Voraussetzung:** Sudo-Mode aktivierbar

**Vorbereitung:**
- Mit `leitung` / `anlaufstelle2026` einloggen.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Treffer öffnen.
2. „Datenauskunft"-Dropdown im Desktop-Header (`.hidden.md\\:flex`) anklicken.
3. Auf „JSON-Export" klicken.
4. Falls Sudo-Mode nicht aktiv → Re-Auth-Seite, Passwort eingeben, zurück.
5. Erneut „JSON-Export" klicken.
6. Download annehmen.

**Erwartetes Ergebnis:**
- Erster Klick ohne Sudo → Redirect auf Sudo-Form.
- Nach Re-Auth → Download startet, Filename-Pattern `datenauskunft_<YYYY-MM-DD>.json` ohne Pseudonym.
- JSON enthält `client`, `events`, `cases`, `episodes`, `goals`, `attachments_meta` (siehe `services/client_export.py`).
- `AuditLog`-Eintrag `EXPORT` mit `detail.format=JSON`, `target_type=Client-JSON`.

**DSGVO/Security-Note:**
- Art. 20 DSGVO Datenportabilität.
- Sudo-Mode (Refs #683) verhindert, dass eine gestohlene Session den Export auslösen kann.
- Rate-Limit 10/h/User schützt vor Massenexport.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-09 — PDF-Datenauskunft (LEAD+, Layout-Prüfung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | leitung | C/F/S | ⚪ | `test_client_export.py::TestClientExportPDF` |

**Voraussetzung:** Sudo-Mode aktiv

**Vorbereitung:**
- Mit `leitung` einloggen, Sudo-Mode bereits aktiviert.

**Schritte:**
1. `/clients/?q=Stern-42` aufrufen, Detail öffnen.
2. „Datenauskunft" → „PDF-Export" wählen.
3. PDF in Reader öffnen.

**Erwartetes Ergebnis:**
- Filename `datenauskunft_<YYYY-MM-DD>.pdf` ohne Pseudonym, Mime `application/pdf`.
- PDF enthält Kopf mit Einrichtungsname, Pseudonym, Erstellungsdatum.
- Sektionen: Stammdaten, Kontakt-Chronik, Fälle/Episoden, Wirkungsziele.
- Pseudonyme statt Klarnamen, keine Anhänge inline.
- `AuditLog` `EXPORT` mit `detail.format=PDF`, `target_type=Client-PDF`.

**DSGVO/Security-Note:**
- Art. 15 DSGVO Auskunftsrecht.
- Fachkraft-Direktzugriff auf `/clients/<uuid>/export/pdf/` → 403 (LeadOrAdminRequiredMixin).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-10 — Lösch-Antrag stellen (Vier-Augen-Workflow, AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_staff_can_request_client_deletion`, `test_full_four_eyes_workflow` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/` aufrufen, beliebige Person öffnen.
2. Button mit `data-testid='client-delete-request-btn'` klicken.
3. Auf `/clients/<uuid>/delete/` Begründung in Textarea eingeben (z.B. „Manueller TC10").
4. „Löschantrag stellen" klicken.
5. Mit `leitung` parallel `/deletion-requests/` öffnen.
6. Antrag „Prüfen" → „Genehmigen".
7. Mit `fachkraft` ursprüngliche Detail-URL erneut aufrufen.

**Erwartetes Ergebnis:**
- Nach Antrag: Redirect auf Detail mit Toast „Löschantrag gestellt. Er erscheint in der Löschanträge-Liste und muss von einer zweiten Person mit dem Recht ‚Löschbestätigung' bestätigt werden." — die Meldung verspricht **keine** Benachrichtigung mehr, weil es keine gibt (Refs #1736).
- `DeletionRequest` mit `target_type=CLIENT`, `status=PENDING`, `requested_by=fachkraft`.
- Detailseite trägt danach Badge + Banner des laufenden Antrags (siehe ENT-CLIENT-15).
- Nach Genehmigung: `Client.is_deleted=True`, `AuditLog` `CLIENT_SOFT_DELETED`.
- Direktaufruf der Detail-URL nach Genehmigung → 404 (Detail-View filtert `is_deleted=False`).

**DSGVO/Security-Note:**
- Art. 17 DSGVO „Recht auf Löschung" mit Vier-Augen-Prinzip (Refs #626).
- Reviewer ≠ Antragsteller — sonst `ValidationError` „Reviewer darf nicht der Antragsteller sein".

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-11 — Trash-View nur für Admin

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | admin | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_full_four_eyes_workflow` |

**Voraussetzung:** mind. 1 soft-deletete Person (vorher TC10 ausführen oder per Seed)

**Vorbereitung:**
- Mit `fachkraft` Direktzugriff auf `/clients/trash/` testen → erwartet 403.
- Mit `admin` einloggen.

**Schritte:**
1. `fachkraft`-Sitzung: `/clients/trash/` aufrufen → 403 prüfen.
2. `admin`-Sitzung: `/clients/trash/` aufrufen.
3. Liste sichten — soft-gelöschte Personen mit `deleted_at`, `deleted_by`.

**Erwartetes Ergebnis:**
- Fachkraft erhält 403 (`AdminRequiredMixin`).
- Admin sieht Tabelle mit Spalten Pseudonym, Gelöscht-am, Gelöscht-von.
- Sortierung absteigend nach `deleted_at`.
- Pro Eintrag **zwei** Aktionen: „Wiederherstellen" und „Jetzt endgültig anonymisieren" (`data-testid='client-anonymize-now-btn'`, siehe ENT-CLIENT-16).
- Über der Tabelle steht der Semantik-Hinweis (`data-testid='client-trash-semantics-hint'`), dass Personen hier erst nach **bestätigter** Löschung erscheinen — ein laufender Antrag allein genügt nicht (Refs #1736).

**DSGVO/Security-Note:**
- Trash respektiert Facility-Scoping (`Client.objects.for_facility(facility)`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-12 — Wiederherstellung aus Trash innerhalb 30 Tage

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | admin | C/F/S | ⚪ | `test_client_deletion_workflow.py::test_full_four_eyes_workflow` |

**Voraussetzung:** soft-deletete Person verfügbar (z.B. nach TC10)

**Vorbereitung:**
- Mit `admin` einloggen.

**Schritte:**
1. `/clients/trash/` öffnen.
2. „Wiederherstellen"-Button bei der Test-Person klicken (POST `/clients/<uuid>/restore/`).
3. Redirect zur Detail-Seite verfolgen.
4. `/clients/` aufrufen — Person sollte wieder in Standard-Liste erscheinen.

**Erwartetes Ergebnis:**
- Redirect auf `/clients/<uuid>/` mit Toast „Person wiederhergestellt.".
- `Client.is_deleted=False`, `deleted_at=None`.
- `AuditLog` `CLIENT_RESTORED` mit `detail.pseudonym`.
- Eintrag verschwindet aus Trash, erscheint wieder in `ClientListView`.

**DSGVO/Security-Note:**
- Default-Frist: `Settings.client_trash_days=30` (anpassbar in Facility-Settings).
- Nach Ablauf: `enforce_retention` ruft `anonymize_client` automatisch auf (`anonymize_eligible_soft_deleted_clients`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-13 — Cross-Facility-Verbot: admin_2 sieht Klient:in von admin nicht

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / RLS | admin (F1) + admin_2 (F2) | C || `test_clients.py`, `test_rls.py` |

**Voraussetzung:** `make seed FACILITIES=2` — zwei parallele Facilities mit getrennten Admins.

**Vorbereitung:**
- Browser-Profil 1: `admin` (Facility 1) eingeloggt.
- Browser-Profil 2 (Inkognito): `admin_2` (Facility 2) eingeloggt.

**Schritte:**
1. Profil 1: `/clients/` öffnen, Person z.B. `Stern-42` anklicken, UUID aus URL kopieren.
2. Profil 2: `https://localhost:8844/clients/<uuid-aus-F1>/` direkt aufrufen.
3. Profil 2: `/clients/<uuid>/edit/` direkt aufrufen.
4. Profil 2: `/api/clients/autocomplete/?q=Stern` aufrufen.

**Erwartetes Ergebnis:**
- Direkter Detail-Aufruf in Profil 2 → 404 (`get_object_or_404(..., facility=request.current_facility)`).
- Edit-URL → 404 vor Permission-Check (Facility-Scoping ist erste Hürde).
- Autocomplete liefert keine F1-Pseudonyme.

**DSGVO/Security-Note:**
- Pflicht-RLS-Test: Postgres `app.current_facility_id`-Session-Var + `FacilityScopedManager`.
- Verstoß gegen Facility-Boundary triggert `AuditLog` `FORBIDDEN` (sofern Code-Pfad erreicht).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-14 — Mobile-Liste auf iPhone-Viewport (Card-Layout, Touch-Targets)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen | fachkraft | C | ✓ | `test_mobile_workflows.py` |


**Vorbereitung:**
- Chrome DevTools → Device-Toolbar → iPhone 14 (390 × 844).
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/clients/` öffnen.
2. Layout der Personen-Liste sichten (Cards statt Tabelle).
3. Tab-Reihenfolge per Tastatur (`Tab`) prüfen.
4. Auf eine Karte tippen → Detail.
5. Auf Detail-Seite Pull-to-Refresh oder Scroll-Verhalten prüfen.
6. Touch-Target-Größen messen (z.B. „Bearbeiten"-Link, Filter-Selects).

**Erwartetes Ergebnis:**
- Karten-Layout statt Tabelle (Tailwind-Breakpoints `sm:hidden`/`hidden sm:table`).
- Touch-Targets ≥ 44 × 44 px (WCAG 2.1 AA, manuell gemessen via Devtools).
- Keine horizontale Scroll-Leiste.
- Filter-Selects öffnen native Mobile-Dropdowns.

**DSGVO/Security-Note:**
- Keine zusätzlichen Daten auf Mobile sichtbar (gleiche Pseudonymisierung).

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-15 — Laufender Löschantrag ist am Datensatz sichtbar (Badge, Banner, Zweitantrag)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / DeletionRequests | fachkraft (`miriam`) | C/F | ⚪ | `test_client_deletion_status.py::TestPendingRequestIsVisible` |

**Code-Referenz:** `get_pending_deletion_request` / `pending_deletion_client_ids` in [src/core/services/client/main.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/client/main.py), Partial [`clients/partials/delete_request_action.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/clients/partials/delete_request_action.html) (Refs #1736)

**Vorbereitung:**
- Mit `fachkraft` einloggen, eigene Test-Person mit eindeutigem Pseudonym anlegen (Seed-Datensätze nicht mutieren).

**Schritte:**
1. Person öffnen, „Löschen beantragen" → Begründung „Manueller TC15" → abschicken.
2. Auf der Detailseite Badge und Banner sichten.
3. `/clients/?q=<Pseudonym>` aufrufen und die Zeile in der Liste sichten.
4. `/clients/<uuid>/delete/` erneut direkt aufrufen und ein zweites Mal abschicken.

**Erwartetes Ergebnis:**
- Detailseite: Badge „Löschung beantragt" (`data-testid='client-deletion-pending-badge'`) neben dem Pseudonym **und** Banner (`data-testid='client-deletion-pending-banner'`) mit Datum, antragstellender Person und dem Hinweis auf die nötige Zweitbestätigung; der Banner verlinkt auf `/deletion-requests/` (`data-testid='client-deletion-pending-link'`).
- Statt des Buttons „Löschen beantragen" steht ein **deaktiviertes** „Löschantrag läuft" (`data-testid='client-delete-request-pending'`).
- Personenliste: Zeile trägt denselben Hinweis (`data-testid='client-row-deletion-pending-badge'`).
- Zweiter Versuch: **kein** Erfolgs-Toast, sondern Info „Für diese Person läuft bereits ein Löschantrag … die eingegebene Begründung wurde nicht übernommen." Es entsteht **kein** zweiter `DeletionRequest`, und die zuletzt eingegebene Begründung wird nicht gespeichert.

**DSGVO/Security-Note:**
- Der Service ist idempotent; vorher meldete die View trotzdem Erfolg und verwarf die Begründung still — ein Fake-Erfolg auf einem Art.-17-Pfad.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-16 — Papierkorb-Aktion „Jetzt endgültig anonymisieren" (inkl. Legal-Hold-Grenze)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Retention | admin (facility_admin) | C/F | ⚪ | `test_client_deletion_status.py::TestTrashAnonymizeAction` |

**Code-Referenz:** `anonymize_client_now` in [src/core/services/client/main.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/client/main.py), `ClientAnonymizeView` in [src/core/views/client_deletion.py](https://github.com/anlaufstelle/app/blob/main/src/core/views/client_deletion.py) (Refs #1737, #1756)

**Voraussetzung:** Eine Person mit Notizen, mindestens einem Fall **und mindestens einem dokumentierten Ereignis** (sonst greift die Historien-Redaktion strukturell nicht — ohne Ereignis-Historie steigt der Anonymisierungspfad vor dem Trigger-Bypass aus und der Fall aus #2085 wird gar nicht erst erreicht), deren Löschung im Vier-Augen-Prinzip **bestätigt** wurde (also im Papierkorb liegt) — z. B. Ergebnis von ENT-CLIENT-10.

**Vorbereitung:**
- Mit `admin` einloggen, Pseudonym der Test-Person notieren.

**Schritte:**
1. `/clients/trash/` öffnen.
2. „Jetzt endgültig anonymisieren" bei der Test-Person klicken → im Bestätigungsdialog **Abbrechen**.
3. Erneut klicken → im Dialog **OK**.
4. Papierkorb-Liste erneut sichten.
5. Gegenprobe Legal Hold: eine zweite Person im Papierkorb mit aktivem Sperrvermerk (auf der Person oder einem ihrer Ereignisse) versehen und die Aktion dort auslösen.
6. Gegenprobe Deep-Link: `/clients/<uuid>/edit/` einer Person im Papierkorb direkt aufrufen.
7. Gegenprobe Terminalität: bei der soeben anonymisierten Person erneut `/clients/trash/` sichten und anschließend `/clients/<uuid>/restore/` direkt per POST aufrufen.

**Erwartetes Ergebnis:**
- Der Dialog nennt Pseudonym und Wirkung ausdrücklich als **unwiderruflich** („Eine Wiederherstellung ist danach nicht mehr möglich").
- Nach **Abbrechen**: nichts passiert, der Datensatz bleibt unverändert im Papierkorb.
- Nach **OK**: Redirect auf `/clients/trash/` mit Toast „Person wurde anonymisiert. Frühere Fassungen ihrer Ereignis-Dokumentation werden spätestens mit dem nächtlichen Aufbewahrungslauf getilgt."; Pseudonym, Notizen, Zusatzfelder sowie Fall-/Ereignisinhalte sind getilgt, der Datensatz bleibt soft-gelöscht (keine Auferstehung). **Kein Serverfehler** — unter dem Drei-Rollen-Modell endete die Aktion bis 0.90 mit HTTP 500 (#2085).
- Die **Ereignis-Historie** (`/events/<id>/`) zeigt unmittelbar danach noch ihre Werte — das ist erwartet. Erst nach einem `enforce_retention`-Lauf steht dort überall „– (redigiert)". Auf einem Einzelrollen-Setup (lokal/Dev) ist sie dagegen sofort redigiert; beide Ausgänge sind korrekt, die Meldung sagt „spätestens".
- `AuditLog` `CLIENT_ANONYMIZED` mit `detail.trigger="manual"` — unterscheidbar vom nächtlichen `enforce_retention`-Lauf.
- Bei aktivem Legal Hold: Fehlermeldung „Für diese Person besteht eine Aufbewahrungssperre (Legal Hold) — Anonymisierung nicht möglich.", der Datensatz bleibt unverändert.
- `/clients/<uuid>/edit/` einer Person im Papierkorb → **404** (wie die Detailansicht); ein POST darauf schreibt nichts fort.
- Bei der anonymisierten Person fehlt „Wiederherstellen“ in der Zeile; der Direkt-POST auf `/clients/<uuid>/restore/` endet mit der Meldung „Diese Person wurde endgültig anonymisiert und kann nicht wiederhergestellt werden.“ und der Datensatz bleibt im Papierkorb (#2085).
- Gegenprobe Rolle: `fachkraft` und `leitung` erhalten auf `/clients/<uuid>/anonymize/` 403 (`FacilityAdminRequiredMixin`).

**DSGVO/Security-Note:**
- Zweck: Art.-17-Sofortlöschung ohne 30 Tage Papierkorb **und** Auflösen von Konfigurations-Blockaden ohne Datenresurrektion (siehe ENT-CONFIG-25).
- Der Vier-Augen-Antragsweg bleibt der einzige Weg **in** den Papierkorb — die Aktion wirkt nur auf `is_deleted=True`.
- Ein Sperrvermerk darf durch eine manuelle Aktion nicht ausgehebelt werden (Spoliationsschutz, Refs #1066).

**Status:** ☐ Offen

---


</details>

---

<details open>
<summary><strong>📁 CASE — Fall-Lebenszyklus (12 Cases)</strong></summary>

**Routen:** `/cases/`, `/cases/new/`, `/cases/<uuid>/`, `/cases/<uuid>/edit/`, `/cases/<uuid>/close/`, `/cases/<uuid>/reopen/`, `/cases/<uuid>/assign-event/`, `/cases/<uuid>/remove-event/<uuid>/`, `/api/cases/for-client/` 
**Views:** `src/core/views/cases.py` (`CaseListView`, `CaseCreateView`, `CaseDetailView`, `CaseUpdateView`, `CaseCloseView`, `CaseReopenView`, `CaseAssignEventView`, `CaseRemoveEventView`, `CasesForClientView`) 
**Services:** `src/core/services/cases.py` (`create_case`, `update_case`, `close_case`, `reopen_case`, `assign_event_to_case`, `remove_event_from_case`) 
**E2E-Coverage:** `test_cases.py` (TestCaseCRUD, TestCasePermissions) 
**Spezial-Setup:** Cases sind `Pflichtfeld client` (Refs #748). Schließen/Wiedereröffnen erfordert LEAD+. Assistenz-Rolle hat 0 Zugriff (`StaffRequiredMixin`).

---

### TC-ID: ENT-CASE-01 — Fall-Liste mit Status-Filter (offen/geschlossen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_case_list_filter_by_status` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` aufrufen — Headline „Fälle".
2. Status-Filter Dropdown auf „Offen" stellen.
3. HTMX-Tabellen-Tausch beobachten (`#case-table`).
4. Filter auf „Geschlossen" wechseln.
5. Suche `q=` mit Titel-Substring testen (z.B. ein Wort aus einem Seed-Titel).

**Erwartetes Ergebnis:**
- Liste zeigt Spalten Titel, Person, Lead, Status-Badge, Erstellt-am.
- HTMX tauscht Partial `core/cases/partials/table.html` ohne Full-Reload.
- Filter `status=open|closed` ist URL-persistent (`pagination_params` enthält Filter).
- Sortierung absteigend nach `created_at`.

**DSGVO/Security-Note:**
- Liste facility-gescoped (`Case.objects.for_facility`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-02 — Neuen Fall für existierende Person anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_case` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eindeutigen Titel `TC02-Case-<random>` vorbereiten.

**Schritte:**
1. `/cases/new/` aufrufen.
2. Titel eintragen.
3. Beschreibung mit Testtext füllen.
4. `lead_user`-Select öffnen, Index 1 auswählen (= erste Lead-Option).
5. Pseudonym-Autocomplete fokussieren, ersten Treffer wählen.
6. „Fall erstellen" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/cases/<uuid>/` mit Toast „Fall wurde erstellt.".
- Detail-Headline = Titel.
- Person und Lead in Sidebar/Meta sichtbar, Status-Badge „Offen".
- `AuditLog` `CASE_CREATE`.

**DSGVO/Security-Note:**
- `client` ist Pflichtfeld (Refs #748). Ohne Person → `ValidationError` „Fälle müssen einer Person zugeordnet sein.".

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-03 — Fall + automatische Episode (separat angelegt nach Erstellung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ⚪ | `test_cases.py::TestEpisodes::test_create_episode` |


**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Schritte aus TC-ID ENT-CASE-02 (Fall anlegen) durchspielen.
2. Auf der Detail-Seite Link „Neue Episode" anklicken (`/cases/<uuid>/episodes/new/`).
3. Titel `TC03-Episode-<random>`, `started_at=2025-01-15`.
4. „Episode anlegen" submitten.

**Erwartetes Ergebnis:**
- Redirect zur Fall-Detail-Seite.
- Episoden-Sektion zeigt neuen Eintrag mit Status „aktiv" (kein `ended_at`).
- Implementation: Episode wird **nicht automatisch** beim Case-Anlegen erzeugt (siehe `services/cases.py::create_case` — kein Auto-Episode-Call). Manuell über Sub-Form anzulegen.

**DSGVO/Security-Note:**
- Keine zusätzliche PII; Episode erbt Facility-Scope vom Case (RLS via JOIN).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-04 — Detail-View mit Episoden, Goals, Events, Status-Badge

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_case_detail_shows_info` |

**Voraussetzung:** Fall mit Episoden + Goals + Events vorhanden (Seed)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` öffnen, ersten Fall klicken.
2. Detail-Layout sichten:
   - Headline = Titel.
   - Status-Badge (Offen/Geschlossen).
   - Meta-Info „Fallverantwortlich", „Erstellt am", „Person".
   - Sektionen Episoden, Goals, Events (zugeordnet/nicht zugeordnet).

**Erwartetes Ergebnis:**
- `select_related("client", "lead_user", "created_by")` in `CaseDetailView` — keine N+1-Queries (in Devtools-Performance-Profile sichtbar).
- `prefetch_related("milestones")` für Goals.
- Events-Sektion zeigt zugeordnete Events absteigend nach `occurred_at`.
- „Nicht zugeordnete Events" füllen sich mit Events derselben Person ohne `case`.

**DSGVO/Security-Note:**
- Events werden via `Event.objects.visible_to(user)` gefiltert (Sensitivity-Layer, Refs #522).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-05 — Fall bearbeiten (Titel, Beschreibung, Lead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ✓ | `test_cases.py::test_edit_case` |

**Voraussetzung:** eigener Test-Fall (TC02 zuerst)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Auf Detail-Seite des Test-Falls „Bearbeiten" klicken.
2. Titel auf `TC05-Updated-<random>` ändern.
3. Beschreibung leicht modifizieren.
4. Lead auf anderen User wechseln.
5. „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect auf `/cases/<uuid>/`.
- Toast „Fall wurde aktualisiert.".
- Headline = neuer Titel.
- `AuditLog` `CASE_UPDATE` mit `detail.changed_fields=["title","description","lead_user"]` (PII-frei).

**DSGVO/Security-Note:**
- Optimistic Locking (Refs #531): `expected_updated_at` wird beim POST geprüft. Bei Konflikt → Toast-Fehlermeldung.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-06 — Fall schließen mit Begründung (LEAD+ erforderlich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | leitung | C/F/S | ⚪ | `test_cases.py::test_close_case` |

**Voraussetzung:** offener Fall vorhanden

**Vorbereitung:**
- Mit `leitung` einloggen.

**Schritte:**
1. Eigenen Fall anlegen (analog TC02).
2. Detail-Seite öffnen.
3. „Schließen"-Button im Desktop-Header (`.hidden.md\\:flex`) klicken (POST `/cases/<uuid>/close/`).
4. Status-Badge prüfen.

**Erwartetes Ergebnis:**
- Redirect auf Detail-Seite.
- Toast „Fall wurde geschlossen.".
- Status-Badge wechselt von „Offen" auf „Geschlossen".
- „Wiedereröffnen"-Button erscheint.
- `AuditLog` `CASE_CLOSE`.
- `Case.closed_at` gesetzt auf `timezone.now()`.

**DSGVO/Security-Note:**
- Fachkraft (`StaffRequiredMixin` reicht für CRUD, aber Close erfordert `LeadOrAdminRequiredMixin`) → Schließen-Button bei Fachkraft nicht sichtbar (Test in `test_staff_cannot_close_case`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-07 — Geschlossenen Fall wiedereröffnen (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | leitung | C/F/S | ⚪ | `test_cases.py::test_reopen_case` |

**Voraussetzung:** geschlossener Fall (TC06 zuerst)

**Vorbereitung:**
- Mit `leitung` einloggen, geschlossener Fall vorhanden.

**Schritte:**
1. Detail-Seite des geschlossenen Falls öffnen.
2. „Wiedereröffnen" klicken (POST `/cases/<uuid>/reopen/`).
3. Status-Badge prüfen.

**Erwartetes Ergebnis:**
- Toast „Fall wurde wiedereröffnet.".
- Status-Badge wieder „Offen".
- `closed_at=None`.
- `AuditLog` `CASE_REOPEN`.
- „Schließen"-Button erscheint erneut.

**DSGVO/Security-Note:**
- Activity-Feed: „Fall … wiedereröffnet" (Refs `Activity.Verb.REOPENED`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-08 — Event einem Fall zuordnen (HTMX)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Events | fachkraft | C/F/S | ⚪ | `test_cases.py` |

**Voraussetzung:** Fall + nicht zugeordnetes Event derselben Person

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Person hat mind. 1 Event ohne `case`.

**Schritte:**
1. Fall-Detail öffnen.
2. Sektion „Nicht zugeordnete Events" — beliebigen Eintrag wählen.
3. „Zu Fall hinzufügen" klicken (POST `/cases/<uuid>/assign-event/` mit `event_id`).
4. HTMX-Tausch beobachten — Event wandert in Sektion „Zugeordnete Events".

**Erwartetes Ergebnis:**
- HTMX rendert Partial `core/cases/partials/event_list.html`.
- `Event.case_id = case.pk` gespeichert.
- Konsistenz: Anonyme Events gehen nur zu Cases ohne Client (`assign_event_to_case`-Validierung).
- Bei Mismatch (Person des Events ≠ Person des Falls) → ValidationError „Person des Ereignisses passt nicht …".

**DSGVO/Security-Note:**
- `get_visible_event_or_404` schützt vor Cross-Sensitivity-Zuordnung.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-09 — Event aus Fall entfernen (HTMX)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Events | fachkraft | C/F/S | ⚪ | `test_cases.py` |

**Voraussetzung:** Fall mit zugeordnetem Event (TC08)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. In Sektion „Zugeordnete Events" beim Event auf „Entfernen" klicken (POST `/cases/<uuid>/remove-event/<event_pk>/`).
3. HTMX-Swap beobachten.

**Erwartetes Ergebnis:**
- Partial wird neu gerendert, Event wandert zurück in „Nicht zugeordnete Events".
- `Event.case = None`.
- Event existiert weiter, kein Soft-Delete.

**DSGVO/Security-Note:**
- Keine zusätzliche Audit-Action (Service ist Reservierung für künftiges Audit, siehe Code-Kommentar).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-10 — „Cases-für-Klient:in"-API auf Detail-Page

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / API | fachkraft | C || `test_cases.py` |

**Voraussetzung:** Person mit ≥ 2 offenen Fällen

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Test-Person (z.B. `Stern-42`) anlegen oder Seed-Fall + 2. Fall manuell anlegen.

**Schritte:**
1. `/clients/?q=Stern-42` → Detail öffnen.
2. Sektion „Aktive Fälle" zählt offene Cases.
3. Optional: in Devtools `/api/cases/for-client/?client=<uuid>` direkt aufrufen.

**Erwartetes Ergebnis:**
- Detail listet alle offenen (`status=OPEN`) Fälle absteigend nach `created_at`.
- API JSON `[{"id": "<uuid>", "title": "..."}]`.
- Rate-Limit 30/Min (Refs `RATELIMIT_FREQUENT`-Pendant; hier `30/m`).

**DSGVO/Security-Note:**
- Endpoint facility-gescoped (`Case.objects.filter(facility=request.current_facility, ...)`).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-11 — Update mit ungültigen Feldern → Toast statt Full-Page-Fehler

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C/F/S | ⚪ | `test_cases.py` |

**Voraussetzung:** eigener Test-Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Fall im Edit-Modus.

**Schritte:**
1. Edit-Form öffnen.
2. Pflichtfeld Titel leeren.
3. „Speichern" klicken.
4. Browser-Devtools → Netzwerk-Tab beobachten.

**Erwartetes Ergebnis:**
- Form rendert mit Validierungsfehlern (Form-Errors zu „Titel: Dieses Feld ist erforderlich.").
- Wenn `expected_updated_at`-Konflikt simuliert (zwei Tabs gleichzeitig editieren): `ValidationError` aus `check_version_conflict` → Toast „Fall wurde von … geändert", Redirect zurück auf Edit-Seite.
- Kein 500-Error.

**DSGVO/Security-Note:**
- Optimistic Locking verhindert Lost-Updates bei zwei Bearbeiter:innen (Refs #531).

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-12 — Mobile-Detail mit Tab-Navigation Episoden/Goals/Events

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle | fachkraft | C | ✓ | `test_mobile_workflows.py` |


**Vorbereitung:**
- Chrome Devtools → Device-Toolbar iPhone 14.
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/cases/` → ersten Fall öffnen.
2. Mobile-Layout sichten — Sektionen ggf. als Akkordeon oder Tabs.
3. Touch-Target der Aktionen prüfen („Bearbeiten", „Schließen").
4. Sticky-Header-Verhalten beim Scrollen testen.

**Erwartetes Ergebnis:**
- Sektionen Episoden, Goals, Events sind unter dem Header lesbar (kein horizontales Scrollen).
- Buttons ≥ 44 × 44 px.
- Wenn Tabs verwendet: aktiver Tab visuell hervorgehoben, Tab-Wechsel ohne Page-Reload.
- Status-Badge bleibt in Sticky-Header sichtbar.

**DSGVO/Security-Note:**
- Keine Layout-bedingten Änderungen an sichtbaren Pseudonymen / Sensitivity-Filtern.

**Status:** ☐ Offen

---


</details>

---

<details open>
<summary><strong>🔄 EPI — Episoden (5 Cases)</strong></summary>

**Routen:** `/cases/<case_pk>/episodes/new/`, `/cases/<case_pk>/episodes/<pk>/edit/`, `/cases/<case_pk>/episodes/<pk>/close/` 
**Views:** `src/core/views/case_episodes.py` (`EpisodeCreateView`, `EpisodeUpdateView`, `EpisodeCloseView`) 
**Services:** `src/core/services/episodes.py` (`create_episode`, `update_episode`, `close_episode`) 
**E2E-Coverage:** `test_cases.py::TestEpisodes` 
**Spezial-Setup:** Episoden sind nur für `Case.Status=OPEN` anlegbar. Idempotenz beim Schließen (zweimal close → no-op).

---

### TC-ID: ENT-EPI-01 — Episode anlegen (Sub-URL `/cases/<uuid>/episodes/new/`)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_episode` |

**Voraussetzung:** offener Fall vorhanden

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Fall via TC-ID ENT-CASE-02 anlegen oder existierenden offenen Fall öffnen.

**Schritte:**
1. Fall-Detail öffnen.
2. Link „Neue Episode" klicken — Redirect auf `/cases/<case_pk>/episodes/new/`.
3. Titel `TC01-Episode-<random>`, `started_at=2025-01-15` eintragen.
4. Beschreibung kurz füllen.
5. „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect zurück zur Fall-Detail-Seite.
- Episoden-Sektion zeigt neuen Eintrag mit Status „aktiv" (`ended_at=None`).
- Toast „Episode wurde erstellt.".
- `Episode.created_by = current_user`.

**DSGVO/Security-Note:**
- `started_at` ist Pflichtfeld (DateField); bei leerem POST → Form-Error.
- Default falls Service direkt aufgerufen: `timezone.now().date()`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-02 — Episode bearbeiten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ | `test_episodes.py` |

**Voraussetzung:** Fall mit aktiver Episode (TC-ID ENT-EPI-01)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. Episode in Liste anklicken / „Bearbeiten" daneben.
3. URL `/cases/<case_pk>/episodes/<pk>/edit/` öffnet Edit-Form.
4. Titel auf `TC02-Updated-<random>`, Beschreibung anpassen, `started_at` ändern.
5. „Speichern".

**Erwartetes Ergebnis:**
- Redirect zur Fall-Detail-Seite.
- Toast „Episode wurde aktualisiert.".
- Aktualisierte Werte in Liste sichtbar.
- `update_episode` validiert Allowlist (`title`, `description`, `started_at`, `ended_at`).

**DSGVO/Security-Note:**
- Mass-Assignment-Schutz im Service: ungültiges Feld → `ValueError`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-03 — Episode mit Outcome schließen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ | `test_cases.py::test_close_episode` |

**Voraussetzung:** aktive Episode

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall + Episode angelegt.

**Schritte:**
1. Fall-Detail öffnen.
2. Bei der Episode Button „Abschließen" klicken (POST `/cases/<case_pk>/episodes/<pk>/close/`).
3. Status der Episode prüfen.

**Erwartetes Ergebnis:**
- Toast „Episode wurde abgeschlossen.".
- Episode-Status wechselt von „aktiv" auf „abgeschlossen".
- `ended_at = timezone.now().date()` (Default falls nicht übergeben).
- Idempotenz: erneuter Klick auf „Abschließen" hat keinen Effekt (`if episode.ended_at is not None: return`).

**DSGVO/Security-Note:**
- Outcome-Modell (`core.models.outcome`) ist getrennt von Episode-Schließen — Outcomes hängen an `OutcomeGoal`, nicht an Episode-Closure.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-04 — Permission: Assistenz darf Episode nicht anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden / Permissions | assistenz | C || `test_cases.py::test_assistant_cannot_access_cases` (verwandt) |

**Voraussetzung:** offener Fall mit bekannter UUID

**Vorbereitung:**
- Mit `admin` einloggen, UUID eines offenen Falls notieren.
- Mit `assistenz` einloggen.

**Schritte:**
1. `assistenz`-Sitzung: `/cases/<known-uuid>/episodes/new/` direkt aufrufen.
2. HTTP-Status prüfen.
3. Bonus: `/cases/` → 403 (verwandter Test).

**Erwartetes Ergebnis:**
- 403 Forbidden (`StaffRequiredMixin` auf `EpisodeCreateView`).
- Keine Episode angelegt.
- Kein Audit-Eintrag.

**DSGVO/Security-Note:**
- Assistenz-Rolle ist Read-only-Light (siehe `views/mixins.py`); Schreibrechte nur ab `fachkraft`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EPI-05 — Mehrere Episoden pro Fall (parallel aktiv möglich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Episoden | fachkraft | C/F/S | ⚪ | `test_episodes.py` |

**Voraussetzung:** offener Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Fall-Detail öffnen.
2. Episode A anlegen (`started_at=2025-01-01`, Titel `Phase A`).
3. Erneut „Neue Episode" — Episode B (`started_at=2025-02-15`, Titel `Phase B`).
4. Liste sortiert nach `started_at` desc — Phase B oben.
5. Beide Episoden sollen Status „aktiv" zeigen (kein automatischer Close von A).

**Erwartetes Ergebnis:**
- Beide Episoden gespeichert, beide aktiv (`ended_at=None`).
- Standardsortierung `Meta.ordering = ["-started_at"]`.
- Kein Code-Pfad zwingt zu seriellen Episoden — parallele Phasen sind fachlich erlaubt.

**DSGVO/Security-Note:**
- Wenn Episode-Anonymisierung greift (TC-ID ENT-CLIENT-10/12): `Episode.title="Episode (anonymisiert)"`, `description=""` (Bulk-Update via `services/clients.py::anonymize_client`).

**Status:** ☐ Offen

</details>

---

<details open>
<summary><strong>🎯 GOAL — Wirkungsziele & Meilensteine (7 Cases)</strong></summary>

**Routen:** `/cases/<case_pk>/goals/new/`, `/cases/<case_pk>/goals/<pk>/edit/`, `/cases/<case_pk>/goals/<pk>/toggle/`, `/cases/<case_pk>/goals/<goal_pk>/milestones/new/`, `/cases/<case_pk>/milestones/<pk>/toggle/`, `/cases/<case_pk>/milestones/<pk>/delete/` 
**Views:** `src/core/views/case_goals.py` (`GoalCreateView`, `GoalUpdateView`, `GoalToggleView`, `MilestoneCreateView`, `MilestoneToggleView`, `MilestoneDeleteView`) 
**Services:** `src/core/services/goals.py` (`create_goal`, `update_goal`, `achieve_goal`, `unachieve_goal`, `create_milestone`, `toggle_milestone`, `delete_milestone`) 
**E2E-Coverage:** `test_cases.py::TestGoalsAndMilestones` 
**Spezial-Setup:** Alle Endpoints sind HTMX-only und rendern `core/cases/partials/goals_section.html`. Rate-Limit `RATELIMIT_FREQUENT` (60/min/User).

---

### TC-ID: ENT-GOAL-01 — Wirkungsziel anlegen (HTMX-Inline-Form)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_goal` |

**Voraussetzung:** offener Fall

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall öffnen.

**Schritte:**
1. Fall-Detail → Sektion `#goals-section` lokalisieren.
2. Inline-Input „Neues Wirkungsziel" mit Titel `TC01-Ziel-<random>` füllen.
3. „Hinzufügen" klicken — POST auf `/cases/<case_pk>/goals/new/`.
4. HTMX-Swap der Sektion abwarten.

**Erwartetes Ergebnis:**
- Sektion `goals-section` neu gerendert, neues Ziel erscheint.
- Ziel-Status: „offen" (nicht erreicht).
- `OutcomeGoal.created_by = current_user`.
- Kein Full-Page-Reload (Network-Tab: nur Partial).

**DSGVO/Security-Note:**
- Beschreibung optional. Empfehlung: keine PII / Klarnamen — Help-Text muss Sensitivity-Hinweis tragen (siehe Help-Text in `models/outcome.py` — derzeit kein Help-Text → Verbesserungsoption).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-02 — Wirkungsziel bearbeiten (HTMX-Update)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ⚪ | `test_goals_htmx.py` |

**Voraussetzung:** vorhandenes Ziel (TC-ID ENT-GOAL-01)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Sektion `goals-section` öffnen.
2. Ziel-Bearbeiten-Toggle klicken (Edit-Inline-Form).
3. Titel und Beschreibung anpassen.
4. „Speichern" — POST `/cases/<case_pk>/goals/<pk>/edit/`.

**Erwartetes Ergebnis:**
- HTMX-Swap rendert Sektion neu.
- Aktualisierte Felder sichtbar.
- `update_goal` setzt nur explizit übergebene Felder (None → unchanged).

**DSGVO/Security-Note:**
- Kein dediziertes AuditLog für Goal-Updates (Code prüfen: `services/goals.py::update_goal` schreibt kein AuditLog). Empfehlung als Verbesserung ab `services/goals.py:29`.

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-03 — Goal-Toggle (erreicht/offen) via HTMX

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele | fachkraft | C/F/S | ✓ | `test_goals_htmx.py` |

**Voraussetzung:** vorhandenes Ziel

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall mit Ziel öffnen.

**Schritte:**
1. Goal-Toggle-Button (Checkbox / Status-Badge) klicken — POST `/cases/<case_pk>/goals/<pk>/toggle/`.
2. Status-Wechsel beobachten („offen" → „erreicht").
3. Erneut klicken → zurück auf „offen".

**Erwartetes Ergebnis:**
- Erste Klick: `goal.is_achieved=True`, `goal.achieved_at=timezone.localdate()`.
- Zweite Klick: `is_achieved=False`, `achieved_at=None` (`unachieve_goal`).
- HTMX-Partial-Refresh ohne Reload.
- Idempotenz: Wenn `is_achieved=True` und `achieve_goal` erneut aufgerufen → no-op (Service-Return).

**DSGVO/Security-Note:**
- Kein PII-Risiko; reine Status-Mutation.

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-04 — Meilenstein anlegen (Sub von Goal)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ✓ | `test_cases.py::test_create_milestone` |

**Voraussetzung:** vorhandenes Ziel

**Vorbereitung:**
- Mit `fachkraft` einloggen, Fall mit Ziel öffnen.

**Schritte:**
1. Im Ziel-Block Inline-Input `input[placeholder='Neuer Meilenstein']` lokalisieren.
2. Titel `TC04-MS-<random>` eingeben.
3. „+"-Button rechts daneben klicken — POST `/cases/<case_pk>/goals/<goal_pk>/milestones/new/`.
4. HTMX-Swap abwarten.

**Erwartetes Ergebnis:**
- Sektion `goals-section` rendert mit neuem Meilenstein als Listenpunkt.
- Default `is_completed=False`, `sort_order=0`.
- Visuell: kein `line-through`-Style.

**DSGVO/Security-Note:**
- Meilensteine erben Facility-Scope vom Goal → Case → Facility (RLS via Joins).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-05 — Meilenstein-Toggle via HTMX

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ⚪ | `test_cases.py::test_toggle_milestone` |

**Voraussetzung:** vorhandener Meilenstein (TC-ID ENT-GOAL-04)

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Meilenstein-Button klicken (POST `/cases/<case_pk>/milestones/<pk>/toggle/`).
2. Visuelle Änderung beobachten — `line-through`-Klasse erscheint, Häkchen statt leerer Kreis.
3. Erneut klicken — wieder rückgängig.

**Erwartetes Ergebnis:**
- Erste Klick: `is_completed=True`, `completed_at=timezone.localdate()`, `<span class="line-through">`.
- Zweite Klick: `is_completed=False`, `completed_at=None`.
- HTMX rendert Partial mit korrektem CSS-State.

**DSGVO/Security-Note:**
- Kein Audit-Trail (analog zu Goals — siehe Empfehlung TC-ID ENT-GOAL-02).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-06 — Meilenstein löschen (mit AuditLog)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Meilensteine | fachkraft | C/F/S | ⚪ | `test_goals_htmx.py` |

**Voraussetzung:** vorhandener Meilenstein

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. Meilenstein-Lösch-Button klicken (POST `/cases/<case_pk>/milestones/<pk>/delete/`).
2. HTMX-Swap beobachten — Eintrag verschwindet aus Liste.
3. AuditLog im Admin prüfen (`/admin/core/auditlog/?action=MILESTONE_DELETE`).

**Erwartetes Ergebnis:**
- Meilenstein DB-deleted (Hard-Delete, kein Soft-Delete).
- `AuditLog` `MILESTONE_DELETE` mit `detail.title` und `detail.case_id`.
- Activity-Eintrag „Meilenstein '…' gelöscht".

**DSGVO/Security-Note:**
- Im Gegensatz zu Client/Case ist das Hard-Delete bei Meilensteinen akzeptabel (kein PII, leichtgewichtige Domänenobjekte).

**Status:** ☐ Offen

---

### TC-ID: ENT-GOAL-07 — Mehrere Goals + Meilensteine in Detail-View

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Wirkungsziele / Meilensteine | fachkraft | C/F/S | ✓ | `test_goals_htmx.py` |

**Voraussetzung:** leerer Fall (oder ausreichend Ziel-Slots)

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Eigener Test-Fall.

**Schritte:**
1. Fall-Detail öffnen.
2. Drei Wirkungsziele anlegen: „Wohnung", „Job", „Schuldenberatung".
3. Pro Ziel zwei Meilensteine ergänzen.
4. Bei Ziel „Wohnung" einen Meilenstein toggeln (erledigt).
5. Bei Ziel „Job" das Goal selbst toggeln (erreicht).

**Erwartetes Ergebnis:**
- Drei Goal-Blöcke nebeneinander/untereinander, jeder mit eigener Meilenstein-Liste.
- `prefetch_related("milestones")` bewirkt: keine N+1-Queries (in Devtools-Performance prüfbar).
- Toggle-States bleiben nach Refresh erhalten.
- Sortierung Goals: `Meta.ordering = ["-created_at"]` (neueste zuerst).
- Sortierung Milestones: `Meta.ordering = ["sort_order"]`.

**DSGVO/Security-Note:**
- Bei Anonymisierung der Person bleiben Goals + Milestones inhaltlich erhalten (keine Mutation in `anonymize_client`); falls erforderlich, manueller Eingriff durch Leitung.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📝 EVT — Events / Dokumentation (10 Cases)</strong></summary>

**Routen:** `/events/new/`, `/events/<uuid>/`, `/events/<uuid>/edit/`, `/events/<uuid>/delete/`, `/api/events/fields/` 
**Views:** `src/core/views/events.py` (`EventCreateView`, `EventDetailView`, `EventUpdateView`, `EventDeleteView`, `EventFieldsPartialView`) 
**Services:** `src/core/services/events/crud.py` (`create_event`, `update_event`, `soft_delete_event`, `attach_files_to_new_event`), `src/core/services/event.py` (Re-Export-Stub Refs #777), `src/core/services/sensitivity.py` (`user_can_see_event`, `get_visible_event_or_404`, `remove_restricted_fields`), `src/core/services/quick_templates.py` 
**E2E-Coverage:** `test_quick_capture.py`, `test_fieldtemplate_default_value.py`, `test_min_contact_stage_anonymous.py` 
**Spezial-Setup:** Anonyme Events benötigen DocumentType ohne `min_contact_stage`. Sensitivity-Tests benötigen DocumentTypes der Stufen `normal` / `elevated` / `high`. Optimistic-Concurrency-Test braucht zwei parallele Browser-Tabs oder Inkognito-Fenster.

---

### TC-ID: ENT-EVT-01 — Event mit DocumentType anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_quick_capture.py` |

**Voraussetzung:** ENT-CLIENT-02 (mind. eine Klient:in mit Kontaktstufe `qualifiziert`).

**Vorbereitung:**
- Pseudonym/UUID einer geseedeten Klient:in notieren.
- Mindestens ein DocumentType („Beratung", Sensitivity `normal`) ist via Seed vorhanden.

**Schritte:**
1. `/events/new/` aufrufen.
2. Klient:in via Autocomplete-Feld (Pseudonym tippen) auswählen — `?client=<uuid>` wird in URL/Hidden-Input gespiegelt.
3. Aus Dropdown DocumentType „Beratung" wählen — HTMX-Request `GET /api/events/fields/?document_type=<id>` lädt dynamische Felder via `EventFieldsPartialView` (Status 200).
4. Pflichtfelder ausfüllen, `occurred_at` auf jetzt setzen.
5. Submit-Button „Speichern" klicken.

**Erwartetes Ergebnis:**
- Redirect 302 → `/events/<new-uuid>/`.
- Flash-Message „Kontakt wurde dokumentiert."
- AuditLog-Eintrag `event_create` mit `target_type=Event`, `detail.document_type="Beratung"`, `detail.is_anonymous=false`.
- `EventHistory`-Eintrag mit `action=CREATE`.
- Activity-Log mit `verb=CREATED` und Summary „Beratung für \<Pseudonym\>".
- Event erscheint in Klient-Timeline (`/clients/<uuid>/`) und Zeitstrom (`/`).

**DSGVO/Security-Note:**
- Sensitivity-Filter steuert Zeitstrom-Sichtbarkeit (Art. 5 DSGVO Zweckbindung).
- Beim POST greift `ratelimit(key=user, rate=RATELIMIT_MUTATION)`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-02 — Dynamische Felder via HTMX (EventFieldsPartialView)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_fieldtemplate_default_value.py` |

**Voraussetzung:** mehrere DocumentTypes mit unterschiedlichen FieldTemplates (Text, Date, Select, File, Sensitivity-Override).

**Vorbereitung:**
- DocumentType A (3 Felder) und DocumentType B (5 Felder, davon eins mit `default_value`) müssen existieren.

**Schritte:**
1. `/events/new/` öffnen, Browser-Devtools → Netzwerk-Tab.
2. Erstes DocumentType A im Dropdown wählen.
3. HTMX-Request `GET /api/events/fields/?document_type=<A>` beobachten → Antwort 200, Partial `dynamic_fields.html` mit 3 Inputs.
4. DocumentType-Dropdown auf B wechseln.
5. Zweiter HTMX-Request `GET /api/events/fields/?document_type=<B>` → 200, 5 Inputs, Default-Wert sichtbar im entsprechenden Feld (`field.initial = ft.get_default_initial()`).
6. Felder umschalten und prüfen, dass das `#dynamic-fields-target` durch HTMX-Swap ersetzt wird (kein voller Reload).

**Erwartetes Ergebnis:**
- HTMX-Swap ersetzt nur das Felder-Container-Div.
- Default-Werte des FieldTemplates werden vor-ausgefüllt.
- Bei Auswahl eines `HIGH`-DocumentType durch fachkraft (`STAFF`-Rolle) → 403 Forbidden, weil `user_can_see_document_type` False liefert (`PermissionDenied` in `EventFieldsPartialView.get`).

**DSGVO/Security-Note:**
- Sensitivity-Guard auf Partial-Endpoint verhindert, dass Assistant/Staff Feldlabels für höhere Stufen sehen (Refs #774).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-03 — Quick-Capture via QuickTemplate

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ✓ | `test_quick_capture.py` |

**Voraussetzung:** QuickTemplate „Kurzkontakt" (DocumentType „Kontakt", Prefill für 2 Felder).

**Vorbereitung:**
- QuickTemplate per Seed/Admin angelegt, dem User zugänglich (`list_templates_for_user` liefert es).

**Schritte:**
1. `/events/new/?template=<quicktemplate-uuid>` aufrufen.
2. Prüfen: DocumentType „Kontakt" ist vorausgewählt; 2 Felder enthalten Prefill-Werte (`apply_template`).
3. Felder unverändert lassen, `occurred_at` setzen, Submit.
4. Detail-View des neuen Events öffnen.

**Erwartetes Ergebnis:**
- Form ist mit Template-Werten vor-befüllt.
- Event speichert sich mit Template-Werten als `data_json`.
- AuditLog `event_create`.
- Bei ungültigem `?template=<uuid>` (anderer Facility / Inactive) → Template wird ignoriert, Form lädt mit Default-DocumentType (kein Crash, kein Hint).

**DSGVO/Security-Note:**
- `get_template_for_user` filtert Templates auf Facility und Sensitivity-Sichtbarkeit (Mandantentrennung).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-04 — Anonymes Event ohne Klient:in

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | `test_min_contact_stage_anonymous.py` |

**Voraussetzung:** DocumentType „Notiz" mit `min_contact_stage=""` (leer → erlaubt anonym).

**Vorbereitung:**
- DocumentType „Notiz" ohne Mindest-Kontaktstufe vorhanden.

**Schritte:**
1. `/events/new/` aufrufen, Klient-Feld leer lassen.
2. DocumentType „Notiz" wählen.
3. Felder ausfüllen, Submit.

**Erwartetes Ergebnis:**
- Event wird mit `is_anonymous=True`, `client=NULL` gespeichert.
- AuditLog `event_create` mit `detail.is_anonymous=true`.
- Detail-View zeigt „Anonym" statt Pseudonym.
- **Negativer Pfad:** Wird DocumentType mit `min_contact_stage=qualifiziert` ohne Klient:in gewählt → ValidationError „Für diesen Dokumentationstyp muss eine Person ausgewählt werden, da eine Mindest-Kontaktstufe vorausgesetzt wird." Form rendert erneut mit Fehlermeldung.

**DSGVO/Security-Note:**
- Anonyme Events dürfen nicht an Cases mit zugeordneter Person gehängt werden (`create_event` validiert: ValidationError „Anonyme Ereignisse dürfen nicht an Fälle mit zugeordneter Person gehängt werden.").

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-05 — Event bearbeiten — EventHistory(UPDATE)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ENT-EVT-01 (bestehendes Event vom selben User).

**Vorbereitung:**
- Event-UUID des unter EVT-01 angelegten Events.
- User ist `created_by` (Assistant darf nur eigene Events editieren — `dispatch` prüft `is_staff_or_above` ODER `created_by == user`).

**Schritte:**
1. `/events/<uuid>/edit/` aufrufen.
2. Formular ist mit dekodierten Werten gefüllt (`safe_decrypt`).
3. Ein Textfeld ändern (z.B. „Notiz" auf „Folgekontakt").
4. Submit.

**Erwartetes Ergebnis:**
- Redirect 302 → `/events/<uuid>/`.
- Flash „Ereignis wurde aktualisiert."
- Neuer `EventHistory`-Eintrag mit `action=UPDATE`, `data_before` (alter Wert) und `data_after` (neuer Wert).
- `event.updated_at` aktualisiert.
- Detail-View zeigt neuen Wert.

**DSGVO/Security-Note:**
- `remove_restricted_fields` entfernt Felder oberhalb der User-Sensitivity vor Update; restriktive Felder bleiben unverändert per Re-Insert aus `event.data_json`.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-06 — Optimistic-Concurrency-Konflikt (2 Tabs)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C/F/S | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ENT-EVT-05 (existierendes Event).

**Vorbereitung:**
- Event-UUID, zwei parallele Browser-Sessions (Tab A + Tab B oder Inkognito).

**Schritte:**
1. Tab A: `/events/<uuid>/edit/` → Formular geladen, `expected_updated_at` (hidden) = T0.
2. Tab B: dieselbe URL → Formular geladen, `expected_updated_at` = T0.
3. Tab A: Feld ändern → Submit. Erfolg, Event-`updated_at` = T1.
4. Tab B: Anderes Feld ändern → Submit (mit veraltetem `expected_updated_at=T0`).
5. Tab B als HTMX-Request: HTTP 409 mit JSON `{error: "conflict", server_state: {data_json, updated_at, document_type_name}, client_expected: T0}`.
6. Tab B als Standard-Browser-Submit: Redirect → `/events/<uuid>/edit/` mit Fehler-Flash, Konflikt-Toast.

**Erwartetes Ergebnis:**
- `check_version_conflict` in `update_event` wirft `ValidationError`.
- Bei JSON/HTMX-Accept: 409 mit `filtered_server_data_json(user, event)` (sensitivity-gefiltert).
- Bei Browser: Flash-Error + Redirect.
- AuditLog: kein `event_update` für den Konflikt-Versuch.

**DSGVO/Security-Note:**
- `filtered_server_data_json` blendet Felder aus, die der Konflikt-Resolver-User nicht sehen darf — kein Leak höher klassifizierter Inhalte über die 409-Response (Refs #575).

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-07 — Event als HIGH-Sensitivity → nur Lead/Admin sieht Inhalt

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | leitung + fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** DocumentType mit `sensitivity=high`.

**Vorbereitung:**
- DocumentType „Krisenintervention" mit `sensitivity=high` vorhanden.

**Schritte:**
1. Als `leitung` einloggen.
2. `/events/new/` → DocumentType „Krisenintervention" wählen → Event mit sensiblem Inhalt anlegen.
3. UUID notieren, Logout.
4. Als `fachkraft` (STAFF-Rolle, `ROLE_MAX_SENSITIVITY=1=ELEVATED`) einloggen.
5. `/events/<uuid>/` aufrufen.
6. Zeitstrom `/` aufrufen — prüfen, ob Event in Liste auftaucht.

**Erwartetes Ergebnis:**
- Schritt 5: HTTP 404 (nicht 403!) — `get_visible_event_or_404` liefert Http404 statt PermissionDenied, damit die Existenz nicht geleakt wird.
- Schritt 6: Event taucht im Zeitstrom NICHT auf (`Event.objects.visible_to(user)` filtert).
- Als `leitung` (LEAD-Rolle): Event ist sichtbar, Detail-View rendert vollständig.

**DSGVO/Security-Note:**
- 404 statt 403 verhindert Metadaten-Leak (Pseudonym, DocumentType-Name) an niedriger eingestufte Rollen.
- `ROLE_MAX_SENSITIVITY`: ASSISTANT=0, STAFF=1, LEAD/ADMIN=2.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-08 — Event mit Case-Zuordnung anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ENT-CLIENT-02 (Klient:in qualifiziert), Case zur Klient:in vorhanden.

**Vorbereitung:**
- Klient-UUID, Case-UUID; `case.client_id == client.pk`.

**Schritte:**
1. `/events/new/?client=<client-uuid>` aufrufen.
2. Im Case-Dropdown den vorhandenen Case auswählen (Form lädt Cases via `cases_for_client`-API).
3. DocumentType, Felder ausfüllen, Submit.

**Erwartetes Ergebnis:**
- Event speichert sich mit `case_id=<case.pk>`.
- Activity-Log mit Verbindung zum Case.
- **Negativer Pfad:** Anderer Klient + Case einer fremden Klient:in → ValidationError „Person des Ereignisses passt nicht zur Person des Falls."
- **Negativer Pfad:** Anonymes Event + Case → ValidationError (siehe EVT-04).

**DSGVO/Security-Note:**
- Cross-Facility-Schutz: `case.facility_id != facility.pk` → ValidationError „Fall gehört nicht zur selben Einrichtung wie das Ereignis."

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-09 — Event löschen → Lösch-Antrag bei qualifiziertem Client

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | — (siehe DEL-Block) |

**Voraussetzung:** ENT-EVT-01 (Event mit `client.contact_stage=QUALIFIED`).

**Vorbereitung:**
- Event von eigener User-Hand (oder Lead-Login).

**Schritte:**
1. `/events/<uuid>/delete/` aufrufen → Confirm-Page.
2. Begründung eintragen, Bestätigen.

**Erwartetes Ergebnis:**
- Wenn `client.contact_stage == QUALIFIED`: KEIN Direct-Delete, sondern `DeletionRequest` (Status PENDING) wird angelegt → Flash „Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden." Redirect → `/`.
- Wenn `client.contact_stage != QUALIFIED` ODER anonymes Event: `soft_delete_event` direkt → Flash „Ereignis wurde gelöscht.", AuditLog `delete`.
- StaffRequiredMixin: Assistants haben hier keinen Zugriff (403 oder Redirect to login je nach Mixin).
- `dispatch`-Check: Staff darf nur eigene Events löschen, Lead/Admin alles.

**DSGVO/Security-Note:**
- Vier-Augen-Prinzip auf qualifizierten Daten — siehe DEL-Block für Approve/Reject-Workflow.

**Status:** ☐ Offen

---

### TC-ID: ENT-EVT-10 — Event-Detail blendet Felder oberhalb der Rolle aus

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** DocumentType mit gemischten Sensitivities (z.B. DocumentType `normal` mit FieldTemplates `normal`/`elevated`/`high`).

**Vorbereitung:**
- Event als Lead angelegt mit Werten in allen drei Feld-Sensitivities.

**Schritte:**
1. Als `fachkraft` (STAFF) einloggen.
2. `/events/<uuid>/` aufrufen.

**Erwartetes Ergebnis:**
- Felder mit `field_template.sensitivity=high` werden NICHT gerendert (oder als „Eingeschränkt" maskiert).
- Felder `normal` und `elevated` sichtbar.
- Effektive Sensitivity = max(doc_type, field) → `effective_sensitivity` aus `services/sensitivity.py`.
- Im Bearbeiten-Form: HIGH-Felder fehlen via `remove_restricted_fields`; beim Save bleiben die Original-Werte erhalten (Re-Insert aus `event.data_json`).

**DSGVO/Security-Note:**
- Ausgeblendete Felder werden NICHT geleert oder überschrieben — Lead/Admin sieht weiterhin den ursprünglichen Wert.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📎 ATT — Datei-Anhänge / File-Vault (16 Cases)</strong></summary>

**Routen:** `/attachments/`, `/events/<event-uuid>/attachments/<attachment-uuid>/download/`, `/clients/<client-uuid>/attachments/<attachment-uuid>/download/`, `/cases/<case-uuid>/attachments/<attachment-uuid>/download/` 
**Views:** `src/core/views/attachments.py` (`AttachmentListView`, `BaseAttachmentDownloadView` → `AttachmentDownloadView`, `ClientAttachmentDownloadView`, `CaseAttachmentDownloadView`) 
**Services:** `src/core/services/file_vault/` (`store_encrypted_file`, `get_decrypted_file_stream`, `soft_delete_attachment_chain`, `_enforce_allowed_file_types`, `_enforce_magic_bytes`, `_run_virus_scan` — alle owner-generisch), `src/core/services/attachments/` (`finalize_staged_files`, `apply_attachment_changes`, `discard_type_switch_file_chains`, `build_attachment_context`, `attachment_download_url`), `src/core/services/compliance` (`get_visible_attachment_for_owner_or_404`), `src/core/services/virus_scan.py`, `src/core/services/encryption.py` (Fernet/MultiFernet) 
**Modell:** `Attachment` (bis M24 `EventAttachment`, Refs #1738) — eigene `facility_id`, genau **ein** Owner (`event`/`client`/`case`, `CheckConstraint attachment_exactly_one_owner`), direkte RLS-Policy (Migrationen `0129`–`0131`, [ADR-039](../adr/039-owner-generische-dateianhaenge.md)) 
**E2E-Coverage:** `test_attachment_versioning_stage_b.py`, `test_attachments_client_case.py`, `test_file_vault.py`, `test_crypto_session.py` 
**Spezial-Setup:** ClamAV-Daemon muss erreichbar sein (`CLAMAV_ENABLED=True`). Im **lokalen** Stack läuft der Dienst seit #1062 nur noch auf Anforderung: `docker compose --profile clamav up -d clamav` — ohne Profil zieht `docker compose up` ihn nicht mehr hoch. EICAR-String als Datei vorbereiten: `echo -n 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > eicar.txt`. DocumentType muss FILE-FieldTemplate haben — seit M24 auch an Typen mit `entity_type` **Person** oder **Fall** (der Seed liefert dort keine mit, Wegwerf-Typen anlegen; Konvention `E2E-M24-*`).

---

### TC-ID: ENT-ATT-01 — PDF-Upload an Event

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ | `test_file_vault.py` |

**Voraussetzung:** DocumentType mit FILE-FieldTemplate „Anhang".

**Vorbereitung:**
- Test-PDF (kleiner 1 MB) bereithalten.

**Schritte:**
1. `/events/new/` öffnen, DocumentType wählen.
2. PDF im File-Input des Felds „Anhang" auswählen.
3. Form ausfüllen, Submit.
4. Detail-View → Anhang-Liste prüfen.

**Erwartetes Ergebnis:**
- `Attachment`-Record mit `event_id=<event-uuid>` (`client_id`/`case_id` NULL), eigener `facility_id`, `is_current=True`, `entry_id=<uuid>`, `sort_order=0`.
- Datei liegt als `<uuid>.enc` unter `MEDIA_ROOT/<facility-id>/` (Fernet-verschlüsselt).
- `original_filename_encrypted` ist Fernet-Token (kein Klartext-Filename auf Disk).
- `event.data_json[<slug>] = {"__files__": True, "entries": [{"id": <uuid>, "sort": 0}]}`.
- Im Detail-View: Filename, Größe, MIME sichtbar.
- ClamAV-Scan VOR Encryption (Reihenfolge in `store_encrypted_file`: Whitelist → ClamAV → Magic-Bytes → Encrypt).

**DSGVO/Security-Note:**
- Original-Filename verschlüsselt persistiert (Art. 32 DSGVO).
- Disk-Cleanup bei DB-Fehler in `store_encrypted_file` via `output_path.unlink(missing_ok=True)` (#662).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-02 — Datei-Download (entschlüsselt im Streaming)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ | `test_file_vault.py` |

**Voraussetzung:** ENT-ATT-01 (vorhandener Anhang).

**Vorbereitung:**
- Event-UUID + Attachment-UUID notieren.

**Schritte:**
1. `/events/<event-uuid>/` öffnen.
2. Auf Anhang-Link klicken (`/events/<event-uuid>/attachments/<att-uuid>/download/`).
3. Bei PDF: Inline-Anzeige im Browser (`Content-Disposition: inline`).
4. Mit `?download=1` aufrufen → erzwungener Download (`Content-Disposition: attachment`).

**Erwartetes Ergebnis:**
- Antwort 200 mit korrektem `Content-Type` (z.B. `application/pdf`).
- `Content-Length` = `attachment.file_size`.
- AuditLog `download` mit `target_type=Attachment`, `target_id=<att-uuid>`, `detail={owner_type: "event", owner_id, event_id, field}` — `event_id` steht bei Ereignis-Anhängen **zusätzlich** weiter drin, damit Bestandsauswertungen nicht brechen (Refs #1738).
- Datei wird via `decrypt_file_stream` chunkweise dekodiert (kein Memory-Spike bei großen Dateien).
- Inline-Whitelist: `image/jpeg`, `image/png`, `image/gif`, `image/webp`, `application/pdf`, `text/plain`. Andere Typen (z.B. `text/html`, `image/svg+xml`) → forced `attachment` (XSS-Schutz, Issue #508).

**DSGVO/Security-Note:**
- Wenn Disk-Datei fehlt: HTTP 404 (logger.error), nicht halb-übertragene Connection-Reset.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-03 — ClamAV-positiv (EICAR-String) → Upload abgewiesen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_vault.py` |

**Voraussetzung:** ClamAV läuft (`CLAMAV_ENABLED=True`), DocumentType mit FILE-Feld.

**Vorbereitung:**
- EICAR-Datei `eicar.txt` mit Standard-Testsignatur erzeugen.

**Schritte:**
1. `/events/new/` → DocumentType wählen.
2. EICAR-Datei im FILE-Feld auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- Form rendert mit ValidationError „Datei wurde von Virenscanner abgewiesen: Eicar-Test-Signature".
- KEIN `Attachment` angelegt, KEINE `.enc`-Datei auf Disk (rollback durch fehlgeschlagene Validation, bevor `encrypt_file` läuft).
- AuditLog `security_violation` mit `detail={reason: "virus_detected", filename_hash: "<64-stelliger HMAC>", filename_ext: "txt", signature: "Eicar-Test-Signature"}` — der Original-Dateiname steht **nicht** im Detail (Refs #1751; das Audit-Log ist append-only und `SECURITY_VIOLATION` von der Aufbewahrungsfrist ausgenommen).
- Atomare Transaktion: Auch das Event wird NICHT angelegt (Rollback durch `transaction.atomic` in `EventCreateView.post`, Refs #584).

**DSGVO/Security-Note:**
- Fail-closed: `VirusScannerUnavailableError` (z.B. ClamAV down) → Upload ebenfalls abgewiesen, AuditLog mit `reason="virus_scanner_unavailable"` (Issue #524).
- Scanner läuft VOR Encryption — ein verschlüsselter Virus auf Disk wäre sonst nicht mehr scanbar.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-04 — ClamAV-negativ (saubere Datei) → Upload erfolgreich

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_vault.py` |

**Voraussetzung:** ClamAV läuft, harmlose Test-Datei (z.B. PDF mit Beispieltext).

**Schritte:**
1. `/events/new/` → DocumentType wählen.
2. Saubere PDF im FILE-Feld auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- `scan_file` liefert `ScanResult(clean=True, infected=False)`.
- Upload läuft durch: Magic-Bytes-Check OK → Encryption → DB-Record.
- KEIN `security_violation`-AuditLog.
- Event + Attachment erscheinen in Detail-View.

**DSGVO/Security-Note:**
- Bei `CLAMAV_ENABLED=False` (Dev/Test ohne Daemon): Bypass mit `ScanResult(clean=True, infected=False)` ohne ClamAV-Kontakt — die Whitelist und Magic-Bytes-Checks bleiben trotzdem aktiv.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-05 — Datei-Versioning (Stufe B Multi-Entry)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_attachment_versioning_stage_b.py` |

**Voraussetzung:** ENT-ATT-01 (existierender Anhang).

**Vorbereitung:**
- Event-UUID, Anhang `v1.pdf` bereits hochgeladen (`entry_id=E1`, `sort_order=0`).

**Schritte:**
1. `/events/<uuid>/edit/` → Bearbeiten-Form.
2. Im FILE-Feld eine REPLACE-Aktion auf `entry_id=E1` durchführen (Hidden-Input `<slug>__replace__<E1>=v2.pdf`).
3. Submit.
4. Detail-View → Versionshistorie prüfen.
5. Optional: weitere Datei `v3.pdf` als ADD anhängen → neuer `entry_id=E2`.
6. Optional: REMOVE auf `entry_id=E1` → CSV `<slug>__remove=E1`.

**Erwartetes Ergebnis:**
- Nach REPLACE: Alter Attachment-Record `is_current=False`, `superseded_by=<v2-pk>`, `superseded_at=<now>`. Neuer Record `is_current=True`, `entry_id=E1` (übernommen), `sort_order=0` (übernommen).
- Nach ADD: neuer `entry_id=E2`, `sort_order = max(existing) + 1`.
- Nach REMOVE: `soft_delete_attachment_chain(event, E1, user)` setzt `deleted_at=now` auf alle Versionen der Kette E1.
- `event.data_json[<slug>]["entries"]` enthält nur noch nicht-gelöschte Heads.
- Disk-Datei der alten Version bleibt liegen (Versionshistorie) bis Event-Anonymize/Delete.
- Identische Mechanik an Person und Fall (ENT-ATT-10/11) — die Versionskette ist owner-agnostisch.

**DSGVO/Security-Note:**
- Versionshistorie ist Audit-relevant — physisches Löschen erst beim Event-Delete via `delete_attachments_for(owner)` (bis M24 `delete_event_attachments(event)`).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-06 — MIME-Whitelist: .exe → Ablehnung + AuditLog

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_vault_magic_bytes.py` |

**Voraussetzung:** `Settings.allowed_file_types` enthält pdf, jpg, png, docx (kein exe).

**Vorbereitung:**
- Test-Datei `harmless.exe` (z.B. eine umbenannte Text-Datei).

**Schritte:**
1. `/events/new/` → DocumentType + FILE-Feld.
2. `harmless.exe` auswählen.
3. Submit.

**Erwartetes Ergebnis:**
- Form-Validation (`DynamicEventDataForm.clean`) lehnt ab: „Dateityp .exe nicht erlaubt. Erlaubt: pdf, jpg, png, docx".
- Falls über Form vorbei direkt der Service aufgerufen wird (programmatisch): `_enforce_allowed_file_types` wirft `ValidationError` und schreibt AuditLog `security_violation` mit `reason="extension_not_allowed"`, `detail={extension: "exe", allowed: [...]}`.
- HTTP-Status: bleibt auf der Form (200 mit Fehler), nicht 415 — das Test-Schema beschreibt 415 für direkten Service-Bypass.
- Fail-closed: Bei leerer/fehlender Settings-Row → Default-Whitelist `DEFAULT_ALLOWED_FILE_TYPES` aus `core.constants` (Refs #771).

**DSGVO/Security-Note:**
- Doppelte Validierung (Form + Service) — direkter Service-Aufruf umgeht Form, Service-Layer ist letzte Instanz (Refs #610).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-07 — Größenlimit: > Settings.max_file_size_mb → Ablehnung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C | ⚪ | `test_file_upload_errors.py` |

**Voraussetzung:** `Settings.max_file_size_mb=10` (oder Default 10 MB), DocumentType mit FILE-Feld.

**Vorbereitung:**
- Test-Datei größer 10 MB (z.B. 15 MB Random-Bytes).

**Schritte:**
1. `/events/new/` → 15-MB-Datei auswählen.
2. Submit.

**Erwartetes Ergebnis:**
- Form-Validation wirft Fehler: „Datei zu groß (15 MB). Maximum: 10 MB".
- KEIN Upload, KEIN AuditLog.
- HTTP 200 mit Form-Error (nicht 413 — 413 wäre nur bei nginx-Layer-Limit).
- Fail-closed: Settings.DoesNotExist → `DEFAULT_MAX_FILE_SIZE_MB=10` aus Constants.

**DSGVO/Security-Note:**
- Größenlimit pro Facility konfigurierbar — Multi-Tenant-Setting.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-08 — Crypto: Fernet/MultiFernet — Round-Trip + Key-Rotation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft ||| `test_crypto_session.py` |

**Voraussetzung:** `ENCRYPTION_KEYS` mit 2 Keys (Comma-separated).

**Vorbereitung:**
- 2 Attachment-Uploads auf demselben Event nacheinander (User A bei T0, User B bei T1).

**Schritte:**
1. User A: Datei hochladen.
2. Key-Rotation: zweiten Key in `ENCRYPTION_KEYS` als ersten setzen, alten dahinter.
3. User B: Datei hochladen → wird mit neuem Key verschlüsselt.
4. User A: alten Anhang erneut downloaden.
5. User B: neuen Anhang downloaden.

**Erwartetes Ergebnis:**
- Beide Downloads liefern Original-Bytes.
- `MultiFernet` versucht Decryption mit allen Keys (alter Key entschlüsselt alten Anhang, neuer Key den neuen).
- Encryption verwendet immer den ERSTEN Key (rotation-friendly).
- `safe_decrypt` mit `default=""` fängt `InvalidToken` ab, wenn ein Key gänzlich entfernt wurde.
- `get_fernet`-Cache (lru_cache) wird bei `override_settings` per `setting_changed`-Signal invalidiert.

**DSGVO/Security-Note:**
- Key-Rotation ohne Re-Encryption-Migration möglich (MultiFernet).
- Filename-Verschlüsselung (`encrypt_field`) verwendet dieselbe Fernet-Instanz wie Disk-Encryption.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-09 — Zentrale Anhang-Übersicht `/attachments/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft | C/F/S | ✓ | `test_file_vault.py`, `test_attachment_overview.py` |

**Voraussetzung:** mehrere Events mit Anhängen (mind. 5).

**Schritte:**
1. `/attachments/` aufrufen.
2. Antwort: HTML-Liste der letzten 200 Anhänge der Facility, sortiert nach `-created_at`.
3. Filter `?document_type=<id>` setzen → nur Anhänge dieses DocumentType.
4. Filter `?client=<uuid>` setzen → nur Anhänge dieser Klient:in.
5. HTMX-Request (Header `HX-Request: true`) → liefert nur Partial `attachment_table.html`.

**Erwartetes Ergebnis:**
- Basis-Queryset scopt **direkt** über `Attachment.facility` (nicht mehr über den Join `event__facility`) — sonst wären personen-/falleigene Zeilen unsichtbar (Refs #1738).
- Sensitivity-Filter VOR Slicing: nur Anhänge, deren Owner-`document_type.sensitivity` (Coalesce über alle drei Owner) UND `field_template.sensitivity` in der Allow-List der Rolle liegen. Owner **ohne** Dokumentationstyp ⇒ Zeile für jede Rolle unsichtbar (fail-closed, Sentinel).
- Cap auf 200 Einträge — größere Mengen via Filter erreichbar.
- Spalten: Filename (entschlüsselt via `get_original_filename`), Dateigröße (`format_file_size`), DocumentType-Name, **„Zugeordnet zu"** (Ereignis/Person/Fall, verlinkt), Pseudonym oder „—" bei anonym.
- Soft-deleted Owner ausgeblendet — je Owner-Typ `NULL`-tolerant (`event__is_deleted=False`, `client__is_deleted=False`, `case__is_deleted=False`).
- **B1 (Refs #1738):** vom Benutzer entfernte (soft-deletete) Anhänge sind **nicht** gelistet — vorher standen sie mit Name, Größe und aktivem Download-Link da, obwohl der Download-View sie mit 404 abweist.
- **B3 (Refs #1738):** `?client=<nicht-uuid>` (entsteht im Normalbetrieb durch das keyup-getriggerte Freitextfeld) liefert **keine HTTP 500** mehr, sondern eine leere Liste — fail-closed, nicht die ungefilterte Gesamtliste.
- `select_related` für Event/Client/Case/FieldTemplate/CreatedBy → keine N+1.

**DSGVO/Security-Note:**
- Sensitivity-Filter in einer einzigen Query (`Q(field_template__sensitivity="") | Q(field_template__sensitivity__in=allowed)`) — vermeidet Loop-basierte Filterung (Memory-effizient bei großen Facilities).
- **B2 (Refs #1738):** das Dropdown „Dokumentationstyp" ist an dieselbe Sensitivitätsgrenze gebunden wie die Zeilen. Vorher standen dort **alle** Typnamen im HTML jeder Rolle — ein Typname („Krisengespräch") ist selbst eine Information. Siehe ENT-ATT-13.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-10 — Datei an einer Person: hochladen, ersetzen, entfernen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft (`miriam`) | C/F | ✓ | `test_attachments_client_case.py`, `test_client_case_attachments_ui.py` |

**Voraussetzung:** Als `admin` ein Personen-Dokumentationstyp (`entity_type` = Person) mit einem Datei-Feld (`field_type: file`) angelegt — seit M24 zulässig (vorher dreischichtig gesperrt, „E8").

**Vorbereitung:**
- Zwei Test-PDFs `v1.pdf` und `v2.pdf` (< 1 MB) bereithalten.
- Eine Person existiert bzw. wird in Schritt 1 neu angelegt.

**Schritte:**
1. `/clients/new/` (oder `/clients/<uuid>/edit/`) öffnen, den Personen-Typ wählen — die Zusatzfelder laden per HTMX nach und enthalten das **Datei-Feld**.
2. `v1.pdf` wählen, Person speichern.
3. Detailseite der Person öffnen — Dateiname, Größe und Download-Link sind sichtbar.
4. Auf den Dateinamen klicken → Download.
5. Person erneut bearbeiten, `v1.pdf` durch `v2.pdf` ersetzen, speichern.
6. Detailseite: Vorversion im aufklappbaren Akkordeon prüfen.
7. Person erneut bearbeiten, die Datei **entfernen**, speichern.

**Erwartetes Ergebnis:**
- Schritt 1: Das Formular trägt `enctype="multipart/form-data"`; das Datei-Feld erscheint (kein stiller Skip, keine Log-Warnung mehr).
- Schritt 2: `Attachment`-Record mit `client_id=<person-uuid>`, `event_id`/`case_id` = NULL, `facility_id` = Facility der Person, `is_current=True`. Die Datei liegt als `<uuid>.enc` unter `MEDIA_ROOT/<facility-id>/` — **derselbe Pfad-Aufbau wie bei Ereignis-Anhängen**, keine Datei-Moves.
- Schritt 4: URL ist `/clients/<person-uuid>/attachments/<att-uuid>/download/`; Antwort 200 mit korrektem `Content-Type`, AuditLog `download` mit `target_type=Attachment`, `detail={owner_type: "client", owner_id, field}` (**kein** `event_id`).
- Schritt 5: alter Record `is_current=False` + `superseded_at` gesetzt, neuer Record trägt dieselbe `entry_id`.
- Schritt 6: Vorversion bleibt herunterladbar.
- Schritt 7: die Kette ist soft-deletet (`deleted_at`), verschwindet aus Detailansicht, Formular und `/attachments/`; der Download antwortet 404.

**DSGVO/Security-Note:**
- Die `facility` des Anhangs wird **immer** aus `owner.facility` abgeleitet, nie aus dem Request — Cross-Facility-Ablage ist damit kein erreichbarer Zustand (Pin-Test, [ADR-039](../adr/039-owner-generische-dateianhaenge.md) §2).
- Personen-Dateien haben **keine** eigene Aufbewahrungsfrist (`DocumentType.retention_days` ist event-only); sie fallen mit der Anonymisierung der Person.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-11 — Datei an einem Fall: hochladen und herunterladen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft (`miriam`) | C/F | ✓ | `test_attachments_client_case.py`, `test_client_case_attachments_ui.py` |

**Voraussetzung:** Fall-Dokumentationstyp (`entity_type` = Fall) mit Datei-Feld; eine Person mit mindestens einem Fall.

**Schritte:**
1. `/cases/<uuid>/edit/` öffnen, den Fall-Typ wählen, eine PDF im Datei-Feld auswählen, speichern.
2. Fall-Detailseite öffnen, auf den Dateinamen klicken.
3. Die Attachment-UUID notieren und die **Ereignis**-Download-URL mit derselben UUID versuchen (`/events/<beliebige-event-uuid>/attachments/<att-uuid>/download/`).

**Erwartetes Ergebnis:**
- Schritt 1: `Attachment` mit `case_id=<fall-uuid>`, `event_id`/`client_id` = NULL; `facility_id` = Facility des Falls.
- Schritt 2: Download über `/cases/<fall-uuid>/attachments/<att-uuid>/download/`, AuditLog-`detail` trägt `owner_type: "case"`.
- Schritt 3: **404** — das Gate prüft „Anhang **an diesem Owner**"; eine Anhang-UUID an einer fremden Owner-Route ist keine gültige Kombination (IDOR-Probe).

**DSGVO/Security-Note:**
- Es gibt keinen Fall-Löschworkflow; falleigene Dateien fallen mit der Anonymisierung der Person, ein Legal Hold auf der Person schützt sie transitiv ([ADR-039](../adr/039-owner-generische-dateianhaenge.md) §6).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-12 — Download-Gate der Owner-Routen: 404-Semantik bei Typ-, 403 bei Feld-Sensitivität

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft (`miriam`) + leitung (`emma`) | C || `test_attachment_download_owner_routes.py`, `test_compliance_attachment_gate.py` |

**Voraussetzung:** zwei Personen-Typen — einer mit Sensibilität **hoch** (Typ-Ebene) und ein normaler Typ mit einem Datei-**Feld** der Sensibilität **hoch**. Je eine Person mit Datei anlegen (Upload als `emma`, weil Fachkräfte maximal `elevated` sehen).

**Schritte:**
1. Als `miriam` die Download-URL des Anhangs am **HIGH-Typ** direkt aufrufen.
2. Als `miriam` die Download-URL des Anhangs am **HIGH-Feld** (normaler Typ) direkt aufrufen.
3. Als `miriam` die Download-URL eines Anhangs einer Person aus einer **fremden Einrichtung** aufrufen.
4. Als `miriam` einen Anhang aufrufen, dessen Person im **Papierkorb** liegt.
5. Als `emma` beide Downloads aus 1 und 2 wiederholen.

**Erwartetes Ergebnis:**
- Schritt 1 → **404**, nicht 403: die Zusatzfelder-Sektion eines für die Rolle unsichtbaren Typs ist komplett abwesend; ein 403 bestätigte die Existenz des Anhangs. Dasselbe gilt, wenn der Owner **gar keinen** Dokumentationstyp hat (fail-closed).
- Schritt 2 → **403**: das Feld existiert innerhalb eines sichtbaren Typs, die Rolle darf nur dieses Feld nicht sehen.
- Schritte 3 und 4 → **404** (Facility-Scope bzw. `is_deleted`).
- Schritt 5 → beide **200**.
- Fehlt die Datei auf der Platte, ist die Antwort **404** plus `logger.error` — keine halb übertragene Connection.
- Alle drei Download-Routen teilen **einen** Ratelimit-Eimer (`attachment_download`): wiederholte Aufrufe über verschiedene Owner-Routen zählen auf dasselbe Budget.

**DSGVO/Security-Note:**
- Fünfstufiges Gate, fail-closed in jeder Stufe ([ADR-039](../adr/039-owner-generische-dateianhaenge.md) §12): Owner-Lookup → Typ-Sensitivität → Anhang am Owner → Feld-Sensitivität → Datei auf Disk.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-13 — Assistenz-Negativprobe: keine Fall-Dateien, kein Fall-Typname

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | assistenz (`lena`) | C | ✓ | `test_attachments_client_case.py`, `test_attachment_overview.py`, `test_attachment_mutation_authz.py` |

**Voraussetzung:** ENT-ATT-11 (Fall mit Datei) und mindestens ein HIGH-Dokumentationstyp mit Anhang.

**Schritte:**
1. Als `lena` `/attachments/` aufrufen.
2. Das Dropdown „Dokumentationstyp" öffnen und zusätzlich den HTML-Quelltext der Seite durchsuchen.
3. Die Fall-Download-URL aus ENT-ATT-11 direkt aufrufen.
4. Die Personen-Download-URL aus ENT-ATT-10 direkt aufrufen (Gegenprobe).

**Erwartetes Ergebnis:**
- Schritt 1: **keine** fall-eigene Zeile in der Liste — Fälle sind für die Assistenz unsichtbar, ihre Dateien folgerichtig auch. Ohne diesen Filter wäre die Übersicht ein Nebeneingang in die Fall-Dokumentation.
- Schritt 2: das Dropdown enthält **keine** Typen oberhalb ihrer Sensitivitätsstufe und keine Fall-Typen — auch nicht im rohen HTML (**B2**, Refs #1738). Normale Typen bleiben wählbar.
- Schritt 3: **403/Redirect** durch `StaffRequiredMixin` — der Fall-Download verlangt Fachkraft-Rechte, ein Assistenz-Download wäre eine Gate-Umgehung.
- Schritt 4: **200** — der Personen-Download steht ab Assistenz offen (`AssistantOrAboveRequiredMixin`).

**DSGVO/Security-Note:**
- Der B2-Befund war ein Leck auf der Konfigurations-Metaebene: die Ergebniszeilen waren korrekt gegated, der **Typname** stand trotzdem im HTML jeder Assistenz. Die Existenz eines Dokumentationstyps „Krisengespräch" darf eine Rolle, die keine Zeile davon sehen darf, nicht ableiten können.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-14 — Übersicht: Spalte „Zugeordnet zu" und Personen-Filter über drei Quellen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft (`miriam`) | C/F | ✓ | `test_attachments_client_case.py`, `test_attachment_overview.py` |

**Voraussetzung:** **Dieselbe** Person trägt drei Dateien: eine an einem ihrer Ereignisse, eine direkt an der Person (ENT-ATT-10) und eine an einem ihrer Fälle (ENT-ATT-11).

**Schritte:**
1. `/attachments/` aufrufen und die Spalte „Zugeordnet zu" ansehen.
2. Je einen Link dieser Spalte anklicken.
3. Den Personen-Filter mit dem Pseudonym dieser Person setzen.
4. Zusätzlich den Dokumentationstyp-Filter setzen (kombiniert).

**Erwartetes Ergebnis:**
- Schritt 1: Die Spalte weist je Zeile **Ereignis**, **Person** oder **Fall** aus.
- Schritt 2: Der Link führt auf die jeweilige Detailseite (`/events/<uuid>/`, `/clients/<uuid>/`, `/cases/<uuid>/`); für Rollen ohne Fall-Zugriff ist der Fall-Eintrag nicht verlinkt.
- Schritt 3: **Alle drei** Dateien erscheinen — der Filter greift über die drei Wege, auf denen eine Datei zu einer Person gehört (`event__client_id`, `client_id`, `case__client_id`). Fände er nur den Ereignis-Weg, wären zwei Dateien unauffindbar.
- Schritt 4: Beide Filter sind kombinierbar; die Liste aktualisiert sich per HTMX ohne Seitenreload.

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-15 — Typwechsel-Rückfrage beziffert Feldwerte **und** Dateien

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | fachkraft (`miriam`) | C/F | ✓ | `test_attachments_client_case.py`, `test_type_switch_confirm_files.py` |

**Voraussetzung:** Person mit einem Personen-Typ, der **beides** trägt: mindestens ein befülltes Textfeld und mindestens eine hinterlegte Datei. Ein zweiter Personen-Typ mit anderem Feldset existiert.

**Schritte:**
1. `/clients/<uuid>/edit/` öffnen — über dem Feldblock steht die amber Warnbox.
2. Den Dokumentationstyp auf den zweiten Typ wechseln, „Speichern" klicken.
3. Im Rückfrage-Dialog **„Abbrechen"** wählen.
4. Erneut „Speichern", diesmal **„OK"**.
5. Detailseite und `/attachments/` prüfen.
6. Dasselbe am Fall (`/cases/<uuid>/edit/`).

**Erwartetes Ergebnis:**
- Schritt 2: Der Dialogtext nennt **beides** — die Anzahl der verworfenen **Zusatzfeld-Werte** und, als zweiter Satz, die Anzahl der entfernten **Dateien** (Singular/Plural korrekt).
- Schritt 3: nichts wird gespeichert, das Formular bleibt bedienbar (kein Dauerhaft-Sperren des Speichern-Buttons).
- Schritt 4: `data_json` enthält nur noch Slugs des neuen Typs; die betroffenen Datei-Ketten sind **soft-deletet**, nicht physisch gelöscht.
- Schritt 5: die Dateien sind aus Detailansicht und Übersicht verschwunden, ihre Downloads antworten 404.
- Trägt die Person **keine** Datei, nennt der Dialog nur die Feldwerte (kein leerer Zusatzsatz).

**DSGVO/Security-Note:**
- Soft-Delete statt Hard-Delete ist Absicht: der Typwechsel ist eine Konfigurationsentscheidung, keine Löschentscheidung. Physisch entfernt werden die Dateien erst über die Löschpfade des Owners ([ADR-039](../adr/039-owner-generische-dateianhaenge.md) §7, Marker-Invariante I-M1).

**Status:** ☐ Offen

---

### TC-ID: ENT-ATT-16 — Art.-15-Auskunft: Anhang-Metadaten in JSON und PDF

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Attachments | leitung (`emma`) oder facility_admin | C | ⚪ | `test_client_export.py` |

**Voraussetzung:** Eine Person mit Dateien an **allen drei** Owner-Typen (ENT-ATT-10/11 plus ein Ereignis-Anhang); zusätzlich eine ersetzte Vorversion und eine im Formular entfernte Datei, damit die Versionskette Zustände zeigt. Der Export verlangt Re-Authentifizierung (Sudo-Mode).

**Schritte:**
1. `/clients/<uuid>/export/json/` ziehen.
2. Die Schlüssel `attachments`, `cases[i].attachments`, `events[i].attachments` und `deleted_events[i].attachments` prüfen.
3. Die Zusatzfeld-Werte im JSON prüfen (Feld vom Typ Datei).
4. `/clients/<uuid>/export/pdf/` ziehen und den Abschnitt „Dateianhänge" sowie die Spalte „Dateianhänge" in Fall- und Ereignistabelle prüfen.
5. Gegenprobe: eine Person **ohne** jede Datei exportieren.

**Erwartetes Ergebnis:**
- Jeder Eintrag trägt genau: `field`, `field_slug`, `filename`, `file_size`, `mime_type`, `uploaded_at`, `uploaded_by`, `is_current`, `superseded_at`, `removed_at`. **Keine** Binärinhalte, kein `storage_filename`, keine Attachment-UUID.
- `attachments` (personen-eigen) ist **immer** vorhanden — bei der Person aus Schritt 5 als leere Liste `[]`.
- `cases[i].attachments` / `events[i].attachments` erscheinen **nur, wenn nicht leer** (Bestandsformat bleibt bitgleich); gelöschte Ereignisse tragen sie ebenso.
- Die **vollständige Versionskette** ist enthalten: ersetzte Glieder mit `is_current=false` + `superseded_at`, entfernte mit gesetztem `removed_at`.
- `filename` ist **entschlüsselt**; ist das Token unlesbar, steht dort `[verschlüsselt]` — **nicht** `download` und kein erfundener Name.
- `mime_type` ist der beim Upload verifizierte Typ (libmagic); nur Bestandszeilen ohne Erkennung fallen auf den browser-gemeldeten zurück.
- Schritt 3: Datei-Felder tragen den Platzhalter `[Dateianhang – Angaben unter „Dateianhänge"]` — **nicht** das rohe `__file__`/`__files__`-Marker-Dict samt Attachment-UUIDs.
- Schritt 4: Das PDF führt dieselben Angaben; der Hinweiskasten oben nennt als Ausschlüsse nur noch die Beschäftigten-Identität im Zugriffsprotokoll und die Binärinhalte der Dateien.

**DSGVO/Security-Note:**
- Der Ereignis-Zweig der Sammel-Abfrage filtert nach Sensitivität (`visible_to`) — Anhang-Metadaten eines für diese Rolle unsichtbaren Ereignisses liegen erst gar nicht im Speicher.
- Soft-deletete Anhänge sind bis zum physischen Löschen weiterhin **gespeicherte Daten der betroffenen Person**: sie zu verschweigen wäre eine unvollständige Auskunft, sie unmarkiert zu listen ebenso irreführend — daher `removed_at`.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📋 WI — WorkItems / Inbox (10 Cases)</strong></summary>

**Routen:** `/workitems/`, `/workitems/new/`, `/workitems/<uuid>/`, `/workitems/<uuid>/edit/`, `/workitems/bulk-status/`, `/workitems/bulk-priority/`, `/workitems/bulk-assign/`, `/api/workitems/<uuid>/status/` 
**Views:** `src/core/views/workitems.py` (`WorkItemInboxView`, `WorkItemDetailView`), `src/core/views/workitem_actions.py` (`WorkItemCreateView`, `WorkItemUpdateView`, `WorkItemStatusUpdateView`), `src/core/views/workitem_bulk.py` (`WorkItemBulkStatusView`, `WorkItemBulkPriorityView`, `WorkItemBulkAssignView`) 
**Services:** `src/core/services/workitems.py` (`create_workitem`, `update_workitem`, `update_workitem_status`, `duplicate_recurring_workitem`, `bulk_update_workitem_status`, `bulk_update_workitem_priority`, `bulk_assign_workitems`), `src/core/services/locking.py` (`check_version_conflict`) 
**E2E-Coverage:** `test_workitem_ui.py`, `test_workitem_edit.py`, `test_workitem_due_filter.py`, `test_workitems_deletion.py` 
**Spezial-Setup:** Inbox-Cap = `WORKITEM_INBOX_CAP` (Constants). Mobile-Inbox via Viewport-Resize (Playwright `iPhone 12`).

---

### TC-ID: ENT-WI-01 — Inbox-Default-View (offen + zugewiesen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** mind. 5 WorkItems der Facility (Mix offen/in_progress/done).

**Schritte:**
1. `/workitems/` aufrufen.
2. Drei Listen sichtbar: „Offen", „In Bearbeitung", „Erledigt (letzte 7 Tage)".

**Erwartetes Ergebnis:**
- „Offen": `status=OPEN` UND (`assigned_to=user` ODER `assigned_to IS NULL`) — eigene + nicht zugewiesene.
- „In Bearbeitung": dito mit `status=IN_PROGRESS`.
- „Erledigt": `status IN (DONE, DISMISSED)` UND `updated_at >= now-7d`.
- Sortierung: `due_date_bucket` (overdue=0, today=1, future=2, none=9), dann `due_date`, dann `priority_order` (URGENT=0, IMPORTANT=1, NORMAL=2), dann `-created_at`.
- Cap pro Liste: `WORKITEM_INBOX_CAP`. `*_has_more`-Flag bei Überlauf.
- HTMX-Request → Partial `inbox_content.html` (nur Inhalt, kein Layout).

**DSGVO/Security-Note:**
- Facility-Scoping über `WorkItem.objects.for_facility(facility)`.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-02 — WorkItem anlegen (Titel, Priorität, Assignee)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |


**Schritte:**
1. `/workitems/new/` aufrufen.
2. Titel „Folgegespräch vereinbaren", Beschreibung, Item-Type, Priorität „important", Assignee = leitung, Due-Date in 7 Tagen.
3. Optional Klient:in zuweisen (`?client=<uuid>`).
4. Submit.

**Erwartetes Ergebnis:**
- Redirect 302 → `/workitems/`.
- Flash „Aufgabe wurde erstellt."
- WorkItem-Record mit `created_by=user`, `assigned_to=leitung`, `status=OPEN`.
- AuditLog `workitem_create` mit `target_type=WorkItem`, `target_id=<uuid>`.
- Activity-Log mit `verb=CREATED`, Summary „Aufgabe: Folgegespräch vereinbaren".
- Inbox zeigt das WorkItem in „Offen".

**DSGVO/Security-Note:**
- StaffRequiredMixin auf Create — Assistants sehen nur die Inbox, dürfen aber keine eigenen Aufgaben anlegen.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-03 — WorkItem bearbeiten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitem_edit.py` |

**Voraussetzung:** ENT-WI-02 (existierendes WorkItem vom selben User).

**Schritte:**
1. `/workitems/<uuid>/edit/` aufrufen.
2. Titel und Beschreibung ändern, Priorität von „important" auf „urgent" setzen.
3. Submit.

**Erwartetes Ergebnis:**
- `update_workitem` schreibt geänderte Felder. AuditLog `workitem_update` mit `detail.changed_fields=["title","description","priority"]` (kein PII-Wert).
- Activity-Log mit `verb=UPDATED`.
- Permission: Lead/Admin, `created_by` oder `assigned_to` (`can_user_mutate_workitem`). Andere → 403 Forbidden.
- Optimistic-Locking via `expected_updated_at` aus Hidden-Input.

**DSGVO/Security-Note:**
- Auch Edit ist auf StaffRequiredMixin — Assistants haben Read-only auf Detail-View, aber Edit-Button erscheint nur bei `can_edit=True` (Refs #753).

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-04 — Status-Toggle via HTMX (open → in_progress → done)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_ui.py` |

**Voraussetzung:** WorkItem `status=OPEN`, ohne `assigned_to`.

**Schritte:**
1. Inbox `/workitems/` → Karte des WorkItems.
2. Status-Button „Starten" klickt POST `/api/workitems/<uuid>/status/` mit `status=in_progress` (HTMX).
3. Erneut Status-Button → POST `status=done`.

**Erwartetes Ergebnis:**
- Schritt 2: `update_workitem_status` lockt Row via `select_for_update`, setzt `status=IN_PROGRESS` UND `assigned_to=user` (Auto-Assign), HTMX-Response 200 mit Partial `item_card.html`.
- Schritt 3: `status=DONE`, `completed_at=now`, Activity `COMPLETED`. Wenn `recurrence != NONE` → Folge-WorkItem via `duplicate_recurring_workitem` (siehe WI-09).
- Idempotenz-Guard: Doppel-Klick auf denselben Status → no-op (kein doppelter Activity-Eintrag, Refs #129/#733).
- Permission: `can_user_mutate_workitem` (Lead/Admin/Creator/Assignee). Andere → 403.
- Ungültiger Status → 400 Bad Request.

**DSGVO/Security-Note:**
- Concurrency-sicheres Update — keine Race auf zwei Tabs.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-05 — Bulk-Status: 3 WorkItems → done

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitem_ui.py` |

**Voraussetzung:** 3 WorkItems offen, alle vom User mutierbar.

**Schritte:**
1. `/workitems/` → Checkboxen aktivieren bei 3 Items.
2. Bulk-Aktion „Erledigt" wählen, POST `/workitems/bulk-status/` mit `workitem_ids[]=<3 uuids>`, `status=done`.

**Erwartetes Ergebnis:**
- `bulk_update_workitem_status` setzt `status=DONE`, `completed_at=now` für alle 3 Items.
- 3 AuditLog-Einträge `workitem_update` mit `detail.changed_fields=["status"]`, `detail.bulk=true`.
- Flash „3 Aufgaben aktualisiert."
- Bei wiederkehrenden Items (recurrence != NONE): `duplicate_recurring_workitem` läuft pro Item (Idempotenz via `recurrence_duplicated_at`-Marker, Refs #596).
- **Negativer Pfad:** Wenn nur 1 von 3 Items nicht mutierbar (z.B. fremdes Lead-Item) → HTTP 403 für gesamten Bulk-Call (kein Partial-Update, Refs #583).
- Rate-Limit: `RATELIMIT_BULK_ACTION`.

**DSGVO/Security-Note:**
- Pro-Item-Ownership-Check, damit Bulk nicht mehr erlaubt als Single-Route.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-06 — Bulk-Priorität: 3 WorkItems → urgent

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitems_bulk.py` |

**Voraussetzung:** 3 WorkItems mit Priorität `normal`, alle mutierbar.

**Schritte:**
1. Inbox → 3 Items selektieren.
2. POST `/workitems/bulk-priority/` mit `priority=urgent`.

**Erwartetes Ergebnis:**
- `bulk_update_workitem_priority`: alle 3 Items auf `urgent`.
- 3 AuditLog-Einträge `workitem_update`, `changed_fields=["priority"]`.
- Sortierung in Inbox: `priority_order=URGENT=0` → 3 Items wandern an die Spitze ihres `due_date_bucket`.
- Ungültige Priorität → 400 Bad Request.

**DSGVO/Security-Note:**
- Wie WI-05: pro-Item-Ownership.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-07 — Bulk-Assign: 3 WorkItems → leitung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | leitung | C | ⚪ | `test_workitems_bulk.py` |

**Voraussetzung:** 3 unzugewiesene WorkItems.

**Schritte:**
1. Inbox → 3 Items selektieren.
2. POST `/workitems/bulk-assign/` mit `assigned_to=<leitung-user-id>`.

**Erwartetes Ergebnis:**
- `bulk_assign_workitems`: alle 3 Items mit `assigned_to=leitung`.
- 3 AuditLog-Einträge `workitem_update`, `changed_fields=["assigned_to"]`.
- Bei `assigned_to=""` (leer) → Assignment wird entfernt (`assignee_or_none=None`).
- **Negativer Pfad:** Unbekannte User-ID → 400 „Unbekannte Benutzerin/Benutzer".
- **Negativer Pfad:** User aus anderer Facility → 400 (User-Filter `facility=request.current_facility`).

**DSGVO/Security-Note:**
- Cross-Facility-Schutz im Assignee-Lookup.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-08 — Filter: Fälligkeit (heute, überfällig, Woche, ohne Frist)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/F/S | ✓ | `test_workitem_due_filter.py` |

**Voraussetzung:** WorkItems mit verschiedenen `due_date`-Werten:
- 1 überfällig (gestern, status=OPEN)
- 1 heute fällig
- 1 in 3 Tagen
- 1 in 14 Tagen
- 1 ohne Frist

**Schritte:**
1. `/workitems/` aufrufen.
2. URL-Param `?due=overdue` → nur überfällige (gestern).
3. `?due=today` → nur heute.
4. `?due=week` → heute + nächste 7 Tage (heute + 3-Tage-Item).
5. `?due=none` → nur ohne Frist.

**Erwartetes Ergebnis:**
- `_apply_filters` validiert `due` gegen `DUE_FILTER_CHOICES`. Ungültige Werte werden ignoriert.
- `overdue`: `due_date < today AND status IN (OPEN, IN_PROGRESS)` (erledigte überfällige fallen raus).
- `today`: `due_date == today`.
- `week`: `today <= due_date <= today+7d`.
- `none`: `due_date IS NULL`.
- HTMX-Request mit Filtern → Partial mit reduzierter Liste.

**DSGVO/Security-Note:**
- Keine PII-Leak im Filter.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-09 — Recurrence: Wiederkehrendes WorkItem (täglich/wöchentlich)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C | ⚪ | `test_workitems_bulk.py` |

**Voraussetzung:** WorkItem mit `recurrence=WEEKLY`, `due_date=today`, `status=OPEN`.

**Schritte:**
1. WorkItem im Inbox auf `done` setzen.
2. Inbox erneut laden.

**Erwartetes Ergebnis:**
- `update_workitem_status` mit `new_status=DONE` triggert `duplicate_recurring_workitem`.
- Neues WorkItem entsteht: gleiche Felder (`title`, `description`, `priority`, `assigned_to`, `client`, `item_type`, `recurrence`), neue `due_date = today + 7d` (für WEEKLY), `status=OPEN`.
- `remind_at`-Offset bleibt erhalten (relativ zur due_date).
- Activity-Log mit Verb `CREATED`, Summary „Wiederkehrende Folgeaufgabe: \<title\>".
- **Idempotenz (Refs #596):** Quelle bekommt `recurrence_duplicated_at=now`. Ein zweites DONE→OPEN→DONE-Toggle erzeugt KEIN drittes Item.
- Recurrence-Optionen: NONE, WEEKLY, MONTHLY (calendar-aware via `_add_months`, 31.01. + 1 Monat = 28./29.02.), QUARTERLY, YEARLY.

**DSGVO/Security-Note:**
- Original-Klient:in wird mit übernommen — kein Re-Linking nötig.

**Status:** ☐ Offen

---

### TC-ID: ENT-WI-10 — Mobile-Inbox auf iPhone-Viewport

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| WorkItems | fachkraft | C/S | ✓ | `test_mobile_workflows.py` |

**Voraussetzung:** WorkItems vorhanden, Playwright mit `iPhone 12`-Device-Profil.

**Schritte:**
1. Browser-Viewport auf 390x844 (iPhone 12) setzen.
2. `/workitems/` aufrufen.
3. Tab-Switch zwischen „Offen / In Bearbeitung / Erledigt" (Single-Column-Layout auf Mobile).
4. Status-Button auf einer Karte tippen (HTMX-Toggle).
5. Filter-Drawer öffnen.

**Erwartetes Ergebnis:**
- Layout: Single-Column statt 3-Spalten-Grid (Tailwind-`md:grid-cols-3`).
- Tap-Targets > 44px (iOS-HIG).
- Status-Toggle funktioniert per Tap, Karte updated via HTMX-Swap (kein Full-Reload).
- Keine horizontalen Scrollbars.
- Filter-Drawer schließt nach Anwendung (Alpine.js).

**DSGVO/Security-Note:**
- Streetwork-Use-Case: Mobile-Inbox muss offline-fähige Reads liefern (siehe Offline-Cluster).

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>🗑️ DEL — Lösch-Anträge / Vier-Augen (6 Cases)</strong></summary>

**Routen:** `/deletion-requests/`, `/deletion-requests/<uuid>/review/` 
**Views:** `src/core/views/event_deletion.py` (`DeletionRequestListView`, `DeletionRequestReviewView`), `src/core/views/client_deletion.py` (Verzweigung über `target_type`) 
**Services:** `src/core/services/events/deletion.py` (`request_deletion`, `approve_deletion`, `reject_deletion`), `src/core/services/clients.py` (`approve_client_deletion`, `reject_client_deletion`), `src/core/services/events/crud.py` (`soft_delete_event`) 
**E2E-Coverage:** `test_workitems_deletion.py`, `test_client_deletion_workflow.py`, `test_client_deletion_status.py` 
**Spezial-Setup:** Vier-Augen-Test braucht 2 separate Sessions (Antragsteller + Reviewer). DeletionRequest existiert pro `target_type` (Event vs. Client).

---

### TC-ID: ENT-DEL-01 — Lösch-Antrag stellen (fachkraft auf qualifiziertes Event)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | fachkraft | C | ⚪ | `test_workitems_deletion.py` |

**Voraussetzung:** ENT-EVT-01 (Event mit `client.contact_stage=QUALIFIED`, `created_by=user`).

**Schritte:**
1. `/events/<uuid>/delete/` aufrufen.
2. Begründung eingeben: „Doppelt erfasst, siehe Event #abc.".
3. Bestätigen.

**Erwartetes Ergebnis:**
- `EventDeleteView.post`: `event.client.contact_stage == QUALIFIED` → kein Direct-Delete, sondern `request_deletion(event, user, reason)` legt `DeletionRequest` (status=PENDING) an.
- Flash: „Löschantrag wurde gestellt und muss von einer Leitung genehmigt werden."
- Redirect → `/`.
- Event bleibt sichtbar (kein Soft-Delete).
- **Idempotenz (#530):** Zweiter Antrag auf dasselbe Event mit existierendem PENDING → derselbe Record wird zurückgegeben, kein Duplikat.

**DSGVO/Security-Note:**
- Vier-Augen-Prinzip auf qualifizierten Daten — Antrag muss von Lead/Admin reviewed werden.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-02 — Antrags-Liste anzeigen (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_workitems_deletion.py` |

**Voraussetzung:** DEL-01 (mind. 1 PENDING-Request).

**Schritte:**
1. `/deletion-requests/` aufrufen.

**Erwartetes Ergebnis:**
- `LeadOrAdminRequiredMixin` — fachkraft/assistenz bekommen 403 oder Redirect.
- Drei Listen: PENDING, APPROVED, REJECTED.
- Pro Eintrag: Antragsteller, Datum, Reason, Target-Type (Event/Client), Link zu Review-Page.
- `select_related("requested_by", "reviewed_by")` → keine N+1.
- Listen sind als `list(...)` evaluiert für `|length`-Tag ohne extra COUNT (Refs #640).

**DSGVO/Security-Note:**
- Mandantentrennung via `for_facility`.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-03 — Antrag genehmigen (LEAD, andere Person als Antragsteller)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_client_deletion_workflow.py` |

**Voraussetzung:** DEL-01 (PENDING-Request von `fachkraft`).

**Schritte:**
1. `/deletion-requests/<dr-uuid>/review/` aufrufen.
2. Review-Page zeigt Event-Detail (oder Client-Detail bei `target_type=Client`).
3. POST mit `action=approve`.

**Erwartetes Ergebnis:**
- `approve_deletion(dr, reviewer)` (atomar):
  - `soft_delete_event(event, reviewer)` → `event.is_deleted=True`, `event.data_json={}`, alle Attachments via `delete_attachments_for(event)` (Disk-Cleanup), `EventHistory(action=DELETE, data_before=<redacted>)`, AuditLog `delete`.
  - `dr.status=APPROVED`, `dr.reviewed_by=reviewer`, `dr.reviewed_at=now`.
- Flash „Löschantrag wurde genehmigt."
- Redirect → `/` (Event) bzw. `/deletion-requests/` (Client).
- Bei `target_type=Client`: `approve_client_deletion` (eigener Service-Pfad in `services/clients.py`).
- Rate-Limit `RATELIMIT_MUTATION` auf POST.

**DSGVO/Security-Note:**
- `build_redacted_delete_history` redact PII vor Persistierung — Audit-Trail bleibt, ohne den ursprünglichen Inhalt zu rekonstruieren.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-04 — Antrag ablehnen mit Begründung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_workitems_deletion.py` |

**Voraussetzung:** DEL-01 (PENDING-Request).

**Schritte:**
1. `/deletion-requests/<dr-uuid>/review/` aufrufen.
2. POST mit `action=reject`.

**Erwartetes Ergebnis:**
- `reject_deletion(dr, reviewer)`: `dr.status=REJECTED`, `dr.reviewed_by=reviewer`, `dr.reviewed_at=now`.
- KEIN `soft_delete_event`.
- Flash „Löschantrag wurde abgelehnt."
- Event bleibt vollständig erhalten.
- Antragsteller erfährt das Ergebnis über die Liste (kein Mail-Notify im aktuellen Stand).

**DSGVO/Security-Note:**
- Ablehnung lässt das Event vollständig — keine Datenmanipulation durch Reject-Pfad.

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-05 — Vier-Augen-Verbot: Antragsteller versucht Selbst-Genehmigung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | leitung | C | ⚪ | `test_client_deletion_workflow.py` |

**Voraussetzung:** DeletionRequest, bei dem `requested_by=leitung` (selbe Person).

**Vorbereitung:**
- Lead hat selbst einen Lösch-Antrag gestellt (z.B. via `/events/<uuid>/delete/` als Lead, weil das Event qualifizierten Klienten betrifft).

**Schritte:**
1. Als `leitung` (= Antragsteller) auf `/deletion-requests/<dr-uuid>/review/` POST mit `action=approve`.

**Erwartetes Ergebnis:**
- `DeletionRequestReviewView.post`: `dr.requested_by == request.user` → Flash-Error „Sie können Ihren eigenen Löschantrag nicht genehmigen."
- Redirect → `/deletion-requests/<dr-uuid>/review/` (kein Status-Change).
- DeletionRequest bleibt PENDING.
- KEIN `soft_delete_event`.
- Aufgabe muss durch ZWEITEN Lead/Admin genehmigt werden.
- Hinweis: Im Code wird der Check via Flash + Redirect umgesetzt, NICHT via 403 — das Test-Schema im Plan beschreibt 403 als Erwartung; das tatsächliche Verhalten ist „weiche" Ablehnung mit Redirect. Test sollte beide akzeptieren oder die Implementierung sollte auf 403 umgestellt werden.

**DSGVO/Security-Note:**
- Zentrales Compliance-Feature: Vier-Augen-Prinzip ist DSGVO Art. 5 (Integrität) und Art. 32 (Sicherheit der Verarbeitung).

**Status:** ☐ Offen

---

### TC-ID: ENT-DEL-06 — Rückkanal: die antragstellende Person findet ihren Antrag wieder

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DeletionRequests | fachkraft (`miriam`) + leitung (`emma`) | C/F | ⚪ | `test_client_deletion_status.py::TestRequesterFeedbackChannel` |

**Code-Referenz:** `User.can_access_deletion_requests` / `User.sees_all_deletion_requests` in [src/core/models/user.py](https://github.com/anlaufstelle/app/blob/main/src/core/models/user.py) — Navigation und View werten **dasselbe** Gate aus (Refs #1736)

**Voraussetzung:** `fachkraft` hat weder eine Leitungsrolle noch das Recht *Löschbestätigung*.

**Schritte:**
1. Als `fachkraft` einloggen und **vor** dem Antrag die Navigation sichten.
2. Für eine eigene Test-Person einen Löschantrag stellen.
3. Navigation erneut sichten, „Löschanträge" öffnen.
4. Vom Personen-Detail aus dem Banner-Link folgen.
5. Als `leitung` denselben Antrag entscheiden (genehmigen oder ablehnen).
6. Als `fachkraft` die Navigation ein drittes Mal sichten.

**Erwartetes Ergebnis:**
- Schritt 1: **kein** Menüpunkt „Löschanträge" (`data-testid='nav-deletion-requests'` fehlt).
- Schritt 3: Menüpunkt vorhanden; `/deletion-requests/` liefert 200 und zeigt **ausschließlich den eigenen** Antrag — keine fremden Begründungen und Ziel-Pseudonyme.
- Schritt 4: Der Banner-Link vom Personen-Detail führt auf dieselbe Liste.
- Schritt 6: Nach der Entscheidung ist der Menüpunkt wieder weg (kein offener eigener Antrag mehr).
- Gegenprobe Leitung/Genehmiger-Pool: `leitung` bzw. eine Person mit dem Recht *Löschbestätigung* sieht **alle** Anträge der Einrichtung.
- Jeder Antrag in der Liste nennt sein Ziel konkret (Pseudonym der Person bzw. Titel/Typ des Ereignisses) statt nur „Client"/„Event"; ein für die Rolle **unsichtbares** Ereignis behält ein neutrales Label ohne Link.

**DSGVO/Security-Note:**
- Der Rückkanal darf keine Fremd-PII öffnen: Zugang über einen eigenen offenen Antrag schaltet die Liste auf die eigenen Anträge (`sees_all_deletion_requests=False`).
- Die Ziel-Beschriftung läuft fail-closed über `Event.objects.visible_to()` — sonst leckte der Dokumentationstyp-Name genau die Metadaten, die die Sensibilitäts-Policy verbirgt.

**Status:** ☐ Offen

---

</details>

<details open>
<summary><strong>📦 RET — Aufbewahrungsrichtlinien (2 Cases)</strong></summary>

**Routen:** `/retention/`, `/api/retention/<uuid>/approve/`, `/api/retention/<uuid>/hold/`, `/retention/bulk-approve/`, `/retention/bulk-defer/`, `/retention/bulk-reject/` 
**Views:** `src/core/views/retention.py` 
**Services:** `src/core/services/retention.py` 
**Management-Commands:** `enforce_retention`, `reencrypt_fields` 
**E2E-Coverage:** `test_retention_dashboard.py` 
**Spezial-Setup:** Backdate-Daten via SQL (`UPDATE core_event SET created_at = NOW() - INTERVAL '400 days'`); `enforce_retention --simulate-date=` für Trockenlauf.

---

### TC-ID: ENT-RET-01 — Retention-Dashboard öffnen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** Backdate-Daten vorhanden (Events/Clients mit `created_at` älter als Retention-Schwelle)

**Vorbereitung:**
- Mit `leitung` einloggen.
- DB-Skript für Backdate ausgeführt.

**Schritte:**
1. `/retention/` aufrufen.
2. Tabs „Ablaufende", „Holds", „Historie" der Reihe nach anklicken.
3. Spaltenüberschriften und Sortierung prüfen.

**Erwartetes Ergebnis:**
- Liste der Daten mit ablaufender Frist (Events > 90/365/3650 Tage je Sensitivität).
- Holds-Tab listet aktive Sperren.
- Historie-Tab zeigt vergangene Approvals/Rejections inkl. Akteur:in und Zeitstempel.

**DSGVO/Security-Note:**
- Aufbewahrungsfristen aus `settings.retention_*_days` (Art. 5 Speicherbegrenzung, Art. 17 Löschpflicht).

**Status:** ☐ Offen

---

### TC-ID: ENT-RET-03 — Bulk-Defer: 3 Einträge zurückstellen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Aufbewahrung | leitung | C/F/S | ⚪ | `test_retention_dashboard.py` |

**Voraussetzung:** ENT-RET-01, mindestens 3 ablaufende Einträge

**Vorbereitung:**
- `/retention/` geöffnet, Tab „Ablaufende".

**Schritte:**
1. 3 Einträge markieren.
2. „Bulk-Defer" klicken, Begründung eingeben („Klärung mit der Fachbehörde offen").
3. Defer-Dauer (z.B. 30 Tage) wählen, bestätigen.
4. Eintrag-Detail prüfen → Defer-Counter sehen.

**Erwartetes Ergebnis:**
- 3 Einträge fallen aus „Ablaufende" raus.
- `defer_count` pro Eintrag um 1 erhöht.
- Neue Frist = alte Frist + 30 Tage.
- Begründung in Audit gespeichert.

**DSGVO/Security-Note:**
- Defer dokumentiert Aufschub-Grund (Art. 5 Abs. 2 Rechenschaftspflicht).

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>🔎 SRCH — Suche (6 Cases)</strong></summary>

**Routen:** `/search/`, Header-Typeahead via HTMX (`/search/typeahead/`) 
**Views:** `src/core/views/search.py` 
**Services:** `src/core/services/search.py` 
**E2E-Coverage:** `test_clients_search.py`, `test_filters.py`, `test_fuzzy_search.py`

---

### TC-ID: ENT-SRCH-01 — Globale Header-Suche (HTMX-Typeahead)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** mit Seed-Daten (≥ 5 Klient:innen, ≥ 3 Cases, ≥ 3 Events).

**Vorbereitung:**
- Mit `fachkraft` einloggen.
- Beliebige Seite mit Header offen.

**Schritte:**
1. Im Header-Suchfeld 3 Zeichen eines Pseudonym-Prefix tippen (z.B. „abe").
2. Auf das Typeahead-Dropdown achten.
3. Eines der Suchergebnisse klicken.

**Erwartetes Ergebnis:**
- Dropdown öffnet sich nach 300ms HTMX-Debounce.
- Top-3 Treffer pro Typ (Klient, Case, Event) gruppiert sichtbar.
- Klick navigiert zur Detail-Seite des Treffers.
- Keine Cross-Facility-Treffer.

**DSGVO/Security-Note:**
- Typeahead respektiert RLS (Art. 32 — Datenintegrität).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-02 — Erweiterte Suche `/search/` mit Volltext + Filter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C/F/S | ⚪ | `test_clients_search.py` |


**Vorbereitung:**
- `/search/` aufrufen.

**Schritte:**
1. Suchbegriff eingeben (z.B. „Jugendhilfe").
2. Filter „Typ = Case" + „Stage = aktiv" wählen.
3. „Suchen" klicken.
4. Treffer prüfen.

**Erwartetes Ergebnis:**
- Volltext-Treffer in Notizen/Tags/Pseudonym werden gefunden.
- Filter werden korrekt angewendet (nur Cases mit Stage „aktiv").
- Trefferzahl im Header („12 Ergebnisse").
- Sortierung nach Relevanz (Default).

**DSGVO/Security-Note:**
- Volltextsuche umfasst nur Felder mit angemessener Vertraulichkeitsstufe (Art. 5 lit. f Vertraulichkeit).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-03 — Fuzzy-Search: Tippfehler trotzdem Treffer

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_fuzzy_search.py` |

**Voraussetzung:** Klient mit Pseudonym „Schwalbe" vorhanden.

**Vorbereitung:**
- `/search/` geöffnet.

**Schritte:**
1. Pseudonym mit Tippfehler tippen: „Schwlabe".
2. Suchen.
3. Trefferliste prüfen.

**Erwartetes Ergebnis:**
- „Schwalbe" erscheint mit „≈ Match" oder Edit-Distance-Hinweis.
- Mindestens ein Fuzzy-Treffer (PostgreSQL `pg_trgm` oder Levenshtein).
- Score < 1.0 sichtbar.

**DSGVO/Security-Note:**
- Fuzzy darf keine Cross-Facility-Treffer liefern (RLS-Einhaltung).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-04 — Filter-Persistence: `?q=…&stage=…` bleibt bei Pagination

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_filters.py` |

**Voraussetzung:** ≥ 30 passende Treffer (für Pagination)

**Vorbereitung:**
- `/search/?q=Beratung&stage=aktiv` aufrufen.

**Schritte:**
1. Auf Seite 1 prüfen: URL hält Parameter.
2. „Nächste Seite" klicken.
3. URL prüfen.
4. Über „Zurück" blättern.

**Erwartetes Ergebnis:**
- URL bleibt: `/search/?q=Beratung&stage=aktiv&page=2`.
- Treffer auf Seite 2 entsprechen weiterhin dem Filter.
- Filter-Inputs zeigen weiterhin „Beratung" / „aktiv".

**DSGVO/Security-Note:**
- Suchparameter dürfen nicht mit personenbezogenen Daten in Server-Logs landen (interne Konvention: Pseudonyme statt Klarnamen).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-05 — Mobile-Suche (iPhone-Viewport)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-13-Profil (390×844).

**Vorbereitung:**
- Mobile-Viewport gesetzt.
- Eingeloggt.

**Schritte:**
1. Header-Burger-Menü öffnen.
2. „Suche" antippen.
3. Suchbegriff tippen.
4. Treffer aufrufen.

**Erwartetes Ergebnis:**
- Suchfeld nimmt volle Breite ein.
- Touch-Targets ≥ 44px.
- Tastatur überdeckt Treffer nicht (scroll-into-view).
- Treffer-Detail responsive.

**DSGVO/Security-Note:**
- Auf Mobilgeräten kein Caching der Suche im Browser-History (Cache-Control: no-store für `/search/`).

**Status:** ☐ Offen

---

### TC-ID: ENT-SRCH-06 — Cross-Facility-Verbot: Nur eigene Facility (RLS)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Suche | fachkraft | C | ⚪ | `test_clients_search.py` |

**Voraussetzung:** Zwei Facilities A/B mit je eigenen Klient:innen; Pseudonym „Mustermann" existiert in beiden.

**Vorbereitung:**
- Mit Fachkraft aus Facility A einloggen.

**Schritte:**
1. `/search/?q=Mustermann` aufrufen.
2. Trefferanzahl prüfen.
3. Mit `Facility-Switcher` zu Facility B wechseln (sofern Rolle erlaubt).
4. Erneut suchen.

**Erwartetes Ergebnis:**
- In Facility A nur Treffer aus Facility A (1 Treffer).
- In Facility B nur Treffer aus B.
- Direktes Aufrufen einer fremden UUID liefert 404.
- Audit: kein Daten-Leak in Logs.

**DSGVO/Security-Note:**
- RLS verhindert facilityübergreifende Sichtbarkeit (Art. 32 — Mandantentrennung). Belegt durch `src/tests/test_rls.py`.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📰 ZS — Zeitstrom (5 Cases)</strong></summary>

**Routen:** `/` (Home/Zeitstrom), `/zeitstrom/feed/` (HTMX-Partial) 
**Views:** `src/core/views/zeitstrom.py` 
**Services:** `src/core/services/feed.py` 
**E2E-Coverage:** `test_zeitstrom_enrichment.py`, `test_zeitstrom_events.py`, `test_zeitstrom_filter_bug.py`

---

### TC-ID: ENT-ZS-01 — Zeitstrom-Feed (Home `/`, neueste Aktivitäten)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ≥ 10 Aktivitäten in den letzten 7 Tagen.

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/` aufrufen.
2. Feed durchscrollen.
3. Auf Eintragsdetail klicken (z.B. Event).

**Erwartetes Ergebnis:**
- Liste mit Aktivitäten in absteigender Zeitordnung.
- Pro Eintrag: Icon, Akteur:in, Aktion, Bezugsobjekt, Zeit (relative Anzeige „vor 2 h").
- Klick öffnet Detail des Bezugsobjekts.

**DSGVO/Security-Note:**
- Feed enthält keine Klarnamen, nur Pseudonyme (Art. 4 Nr. 5).

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-02 — Pagination des Feeds (HTMX-Polling/Infinite-Scroll)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ≥ 50 Aktivitäten

**Vorbereitung:**
- `/` geöffnet.

**Schritte:**
1. Bis zum Ende scrollen.
2. Auf „Mehr laden" oder Auto-Polling warten.
3. Netzwerk-Tab beobachten.

**Erwartetes Ergebnis:**
- HTMX-Request an `/zeitstrom/feed/?page=2` löst aus.
- Neue Einträge werden ans Ende angehängt.
- Kein Full-Page-Reload.
- Bei Live-Polling: alle 30 s Request, neue Einträge oben hinzufügen.

**DSGVO/Security-Note:**
- Polling-Frequenz nicht zu kurz (Performance + Logging-Volumen).

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-03 — Filter nach Activity-Typ

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_filter_bug.py` |

**Voraussetzung:** ENT-ZS-01

**Vorbereitung:**
- Feed offen.

**Schritte:**
1. Filter-Chip „Events" wählen.
2. Filter-Chip „Cases" wählen (multi-select oder umschalten).
3. Filter zurücksetzen.

**Erwartetes Ergebnis:**
- URL aktualisiert sich (`?type=event` o. ä.).
- Liste zeigt nur passende Aktivitäten.
- Reset-Button stellt Default-Ansicht wieder her.

**DSGVO/Security-Note:**
- Filter ändert nicht die Sichtbarkeitsregeln, nur die Anzeigemenge.

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-04 — Sensitivity-Filter: Niedrige Rolle sieht keine sensitiven Events

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | assistenz | C | ⚪ | `test_zeitstrom_enrichment.py` |

**Voraussetzung:** Sensitives Event (`sensitivity='qualified'`) im Feed.

**Vorbereitung:**
- Mit `assistenz` einloggen.
- Vergleichbar dazu mit `leitung` einloggen, um Diff zu prüfen.

**Schritte:**
1. Mit `assistenz`: `/` aufrufen.
2. Feed durchsuchen — gibt es das sensitive Event?
3. Mit `leitung`: identische Aktion.
4. Direktaufruf der Event-UUID als `assistenz`.

**Erwartetes Ergebnis:**
- `assistenz` sieht das sensitive Event NICHT im Feed (oder nur als Stub „Eintrag verborgen").
- `leitung` sieht es vollständig.
- Direktaufruf als `assistenz`: 403/404.

**DSGVO/Security-Note:**
- Need-to-Know-Prinzip (Art. 5 lit. f Vertraulichkeit, Art. 32). Klassifikation `qualified` = nur Leitung+.

**Status:** ☐ Offen

---

### TC-ID: ENT-ZS-05 — Partial-Refresh: Live-Update bei neuem Event ohne Reload

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Zeitstrom | fachkraft | C | ⚪ | `test_zeitstrom_events.py` |

**Voraussetzung:** ENT-ZS-01

**Vorbereitung:**
- Tab A: `/` als `fachkraft`.
- Tab B: Event-Create als `leitung`.

**Schritte:**
1. In Tab B neues Event anlegen.
2. In Tab A 30 s warten (oder manuelles HTMX-Trigger).
3. Feed in Tab A beobachten.

**Erwartetes Ergebnis:**
- HTMX-Polling/Trigger lädt neuen Eintrag.
- Eintrag erscheint oben mit Highlight (z.B. kurzes Aufblitzen).
- Kein Full-Reload, Scroll-Position bleibt.

**DSGVO/Security-Note:**
- Live-Updates respektieren Sensitivity-Regeln (vgl. ENT-ZS-04).

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>🤝 HOV — Übergabe (5 Cases)</strong></summary>

**Routen:** `/uebergabe/`, `/uebergabe/print/` 
**Views:** `src/core/views/handover.py` 
**Services:** `src/core/services/handover.py` 
**E2E-Coverage:** `test_handover.py`

---

### TC-ID: ENT-HOV-01 — Übergabe-View `/uebergabe/` mit Stats-Grid

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** mit ≥ 3 offenen Cases, ≥ 5 Events des laufenden Tages.

**Vorbereitung:**
- Mit `fachkraft` einloggen.

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Stats-Grid (offene Cases, geplante Events, akute Hinweise) prüfen.
3. Sektionen lesen.

**Erwartetes Ergebnis:**
- Stats-Grid zeigt Zahlen für „Offene Cases", „Termine heute", „Eilige Hinweise".
- Sektion „Heute angekommen" / „Heute abzuschließen" / „Wichtige Hinweise" befüllt.
- Letzte Aktualisierung-Zeitstempel sichtbar.

**DSGVO/Security-Note:**
- Anzeige aus Sicht der eigenen Facility (RLS); Pseudonyme statt Klarnamen.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-02 — Filter: nach Schicht (Vormittag/Nachmittag), Rolle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | leitung | C | ✓ | `test_handover.py` |

**Voraussetzung:** ENT-HOV-01, Cases mit Schichtzuordnung.

**Vorbereitung:**
- `/uebergabe/` offen.

**Schritte:**
1. Filter „Schicht = Vormittag" wählen.
2. Stats neu laden lassen (HTMX-Refresh).
3. Filter „Rolle = fachkraft" zusätzlich aktivieren.
4. Filter zurücksetzen.

**Erwartetes Ergebnis:**
- Liste reduziert sich auf Vormittagsschicht.
- Mit zusätzlichem Rollenfilter: nur Cases von Fachkräften.
- URL-Parameter (`?schicht=vormittag&role=fachkraft`) bleiben erhalten.
- Reset stellt vollständige Übersicht wieder her.

**DSGVO/Security-Note:**
- Filter sind Anzeigeoptionen — keine Umgehung der Berechtigungen.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-03 — Print-CSS: `Strg+P` rendert druckfertiges Layout

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C/F/S | ⚪ | `test_handover.py` |

**Voraussetzung:** ENT-HOV-01

**Vorbereitung:**
- `/uebergabe/` offen.

**Schritte:**
1. `Strg+P` drücken (oder Print-Button klicken).
2. Druckvorschau prüfen.
3. Alternativ `/uebergabe/print/` direkt aufrufen.

**Erwartetes Ergebnis:**
- Druckvorschau ohne Header/Sidebar/Buttons.
- Kompaktes Layout mit klaren Abschnitten.
- Schwarzweiß-tauglich, kein Hintergrund-Cyan.
- Seitenumbruch sinnvoll.
- Kopfzeile mit Datum und Facility-Name.

**DSGVO/Security-Note:**
- Druckausgabe enthält keine Klarnamen — nur Pseudonyme + Schichtnotizen. Hinweis im Footer „Vertraulich — DSGVO".

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-04 — Mobile-Übergabe (essentielle Info auf iPhone)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** Browser-DevTools mit iPhone-Profil (390×844). Stand-by-Workflow (Bereitschaft).

**Vorbereitung:**
- Mobile-Viewport.
- Eingeloggt als `fachkraft`.

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Stats-Grid + Top-3-Hinweise sichtbar?
3. Akkordeons aufklappen (Cases / Hinweise).
4. „Anrufen"-Link an Klient prüfen (sofern vorhanden) — `tel:` öffnet Wählvorgang.

**Erwartetes Ergebnis:**
- Stats-Grid stapelt sich vertikal.
- Touch-Targets ≥ 44px.
- Akkordeon mit weicher Animation.
- Eilige Hinweise „über der Falte".
- `tel:`-Link funktioniert (Stand-by-Anruf möglich).

**DSGVO/Security-Note:**
- Mobile-Caching deaktiviert (`Cache-Control: no-store`) für `/uebergabe/`.

**Status:** ☐ Offen

---

### TC-ID: ENT-HOV-05 — Empty-State: Keine offenen Cases → freundliche Meldung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Übergabe | fachkraft | C | ✓ | `test_handover.py` |

**Voraussetzung:** Frische Facility ohne Cases (oder alle Cases geschlossen).

**Vorbereitung:**
- Test-Facility mit leerem Zustand vorbereiten (`make seed` Variante mit `--scale=empty` o. ä.).

**Schritte:**
1. `/uebergabe/` aufrufen.
2. Inhalte beobachten.

**Erwartetes Ergebnis:**
- Statt leerer Liste: freundliche Empty-State-Karte („Keine offenen Cases — schöne Schicht!").
- Illustration oder Emoji (kontextabhängig).
- CTA: „Neuen Case anlegen" (sofern Rolle berechtigt).

**DSGVO/Security-Note:**
- Empty-State enthält keine Hinweise auf andere Facilities.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📊 STAT — Statistik (15 Cases)</strong></summary>

**Routen:** `/statistics/`, `/statistics/chart-data/`, `/statistics/export/csv/`, `/statistics/export/pdf/`, `/statistics/export/sample-report-1/`, `/statistics/reports/panel/`, `/statistics/reports/export/` 
**Views:** `src/core/views/statistics.py` 
**Services:** `src/core/services/statistics.py`, `src/core/services/export.py`, `src/core/reporting/` (Registry + Backends) 
**Management-Commands:** `create_statistics_snapshots` 
**E2E-Coverage:** `test_export_statistics.py`, `test_statistics_charts.py`, `test_statistics_dashboard.py`, `test_statistics_snapshot.py`, `test_reporting_backends.py`

---

### TC-ID: ENT-STAT-01 — Statistik-Dashboard `/statistics/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_dashboard.py` |

**Voraussetzung:** Seed-Daten mit verteiltem Eventaufkommen über mehrere Monate.

**Vorbereitung:**
- Mit `leitung` einloggen.

**Schritte:**
1. `/statistics/` aufrufen.
2. KPI-Karten (Cases gesamt, neue Klient:innen, Events-Anzahl) lesen.
3. Charts-Sektion einsehen.

**Erwartetes Ergebnis:**
- KPI-Karten gefüllt mit korrekten Zahlen für aktuelle Periode.
- Charts (Bar/Line) gerendert mit Chart.js.
- Tabs für Zeitraum (Tag/Woche/Monat/Quartal/Jahr).

**DSGVO/Security-Note:**
- Statistik nur aggregiert, keine Einzelfall-Identifikation (Art. 89 Statistikprivileg).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-02 — Charts (Bar/Line) — Filter Q1/Q2/Q3/Q4

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C/F/S | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** ENT-STAT-01, Daten über alle 4 Quartale verteilt.

**Vorbereitung:**
- `/statistics/` offen.

**Schritte:**
1. Quartal-Filter „Q1" wählen.
2. Charts re-rendern beobachten.
3. Q2/Q3/Q4 nacheinander durchklicken.
4. „Ganzes Jahr" zurücksetzen.

**Erwartetes Ergebnis:**
- Charts aktualisieren sich pro Quartal (Bar zeigt Monate, Line zeigt Verlauf).
- Achsenbeschriftung sinnvoll (Jan/Feb/März für Q1).
- Tooltip beim Hover zeigt exakte Werte.
- Browser-Übergreifend (C/F/S) konsistent.

**DSGVO/Security-Note:**
- Charts liefern aggregierte Werte, kein Reverse-Engineering möglich (k-Anonymität in Praxis).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-04 — CSV-Export `/statistics/export/csv/` (LEAD+)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, Filter gesetzt (z.B. Q1/2026).

**Schritte:**
1. „CSV-Export" klicken.
2. Datei herunterladen.
3. In Tabellenkalkulation (LibreOffice) öffnen.
4. Zeilenanzahl + Spaltenüberschriften prüfen.

**Erwartetes Ergebnis:**
- Datei `statistics-2026-Q1.csv` (oder ähnlich).
- UTF-8 mit BOM für Excel-Kompatibilität.
- Spalten: Periode, Metrik, Wert, Facility.
- Zeilenanzahl entspricht Filter.

**DSGVO/Security-Note:**
- Export nur Aggregate. Filename nicht personenbezogen. CSV-Inhalt enthält keine Klarnamen oder UUIDs einzelner Klient:innen.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-05 — PDF-Export `/statistics/export/pdf/`

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, Filter Q1/2026.

**Schritte:**
1. „PDF-Export" klicken.
2. Download abwarten.
3. PDF in PDF-Viewer öffnen.
4. Inhalte prüfen.

**Erwartetes Ergebnis:**
- PDF mit Logo-Header, Facility-Name, Berichtszeitraum.
- KPI-Tabelle + Charts (statisch eingebettet als PNG/Vector).
- Footer mit Druckdatum + Aktor:in.
- Maschinenlesbare Metadaten (Title, Author).

**DSGVO/Security-Note:**
- PDF-Metadaten enthalten keinen Klarnamen, nur Username/Rolle (z.B. „Erstellt durch leitung@facility-A").

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-06 — Beispiel-Sachbericht-Export (strukturierter Bericht)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_export_statistics.py` |

**Voraussetzung:** Daten für Berichtsperiode (z.B. Halbjahr).

**Vorbereitung:**
- `/statistics/` offen.

**Schritte:**
1. „Beispiel-Sachbericht" wählen.
2. Berichtsperiode auswählen.
3. Format (CSV/PDF) wählen.
4. Download.

**Erwartetes Ergebnis:**
- Strukturierter Bericht nach kommunalem Schema (z.B. §31/§32 SGB VIII Kennzahlen).
- Kategorien wie „Erstkontakte", „Beratungen", „Weitervermittlungen".
- Periode korrekt.

**DSGVO/Security-Note:**
- Übermittlung an Behörde rechtsgrundlagengestützt (§ 79a SGB VIII / § 35a SGB I); Bericht enthält nur Aggregate.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-07 — Chart-Data-API `/statistics/chart-data/` als JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_charts.py` |

**Voraussetzung:** ENT-STAT-01

**Vorbereitung:**
- `/statistics/` offen, DevTools Network-Tab.

**Schritte:**
1. Filter ändern → Chart re-rendert.
2. Im Network-Tab den Request `/statistics/chart-data/?...` finden.
3. Response prüfen (JSON-Struktur).
4. Direkt aufrufen ohne Auth (Logout) — sollte 302/403 zurückgeben.

**Erwartetes Ergebnis:**
- JSON-Response mit `labels`, `datasets`, `meta`.
- Chart.js konsumiert ohne Transformation.
- Ohne Auth: Login-Redirect.

**DSGVO/Security-Note:**
- API erfordert Authentifizierung; CSRF-geschützt; respektiert Facility-Scope.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-08 — Rollen-Sicht: assistenz darf Stat nicht öffnen → 403

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | assistenz | C | ⚪ | `test_statistics_dashboard.py` |


**Vorbereitung:**
- Mit `assistenz` einloggen.

**Schritte:**
1. Direktaufruf `/statistics/` als `assistenz`.
2. Direktaufruf `/statistics/export/csv/`.
3. Mit `fachkraft` erneut versuchen (sofern Policy = LEAD+).

**Erwartetes Ergebnis:**
- `assistenz`: 403 oder Redirect mit Fehlermeldung „Nicht berechtigt".
- `fachkraft` ggf. ebenfalls 403, da Statistik LEAD+ (laut Header).
- Audit-Eintrag „access_denied".

**DSGVO/Security-Note:**
- Need-to-Know (Art. 32). Statistik nur für Leitung/Admin → minimiert Profilrisiko.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-09 — Performance: > 1000 Events → Stat lädt < 3 s (Smoke)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_statistics_dashboard.py` |

**Voraussetzung:** Seed mit `--scale=large` (≥ 1000 Events).

**Vorbereitung:**
- Browser-DevTools mit Performance-Tab.
- Cache leer (Hard-Reload).

**Schritte:**
1. `/statistics/` öffnen, Performance-Aufnahme starten.
2. Time-to-Interactive ablesen.
3. Filter wechseln (Q1 → Q2) → erneut messen.

**Erwartetes Ergebnis:**
- Initial-Load < 3 s (Time to Interactive).
- Filterwechsel < 1 s (HTMX-Partial via Snapshot statt Live-Aggregat).
- Kein N+1 in Slow-Query-Log (PostgreSQL).

**DSGVO/Security-Note:**
- Performance via Snapshots (`StatisticsSnapshot`-Tabelle), nicht via Live-Aggregat über Klientendaten.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-10 — Berichte-Sektion: Backend wählen → Vorschau-Panel

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_reporting_backends.py` |

**Voraussetzung:** Seed-Daten mit Ereignissen im aktuellen Zeitraum.

**Vorbereitung:**
- Mit `leitung` einloggen, `/statistics/` aufrufen, bis zur Sektion „Berichte" scrollen.

**Schritte:**
1. Dropdown „Bericht" öffnen.
2. „Beispiel-Sachbericht" wählen (kein Reload — HTMX lädt das Panel nach).
3. Panel lesen: Titel, Zeitraum-Zeile, Vorschau-Tabelle, Fußnote.

**Erwartetes Ergebnis:**
- Panel erscheint unterhalb des Dropdowns, ohne Seitenwechsel.
- Titel = Backend-Name, darunter „Zeitraum: <von> – <bis>" im ausgewählten Zeitraum.
- Vorschau-Tabelle mit den Spalten des Backends (Kategorie/Anzahl); bei Zeilen-Backends max. 10 Zeilen plus „Zeigt X von Y Zeilen".
- Fußnote „Werte unter der Kleinstfallzahl-Schwelle werden unterdrückt (k-Anonymität)."
- Keine Konsolen-Fehler durch die Berichts-Interaktion (der Alpine-CSP-Parserhinweis `Unexpected token: fetchAndRender` tritt bei **jedem** HTMX-Swap dieser Seite auf, auch beim reinen Zeitraumwechsel, und ist nicht berichtsspezifisch).

**DSGVO/Security-Note:**
- Die Vorschau rendert `prepare_export()`, nicht den Rohdatensatz — sie zeigt exakt das, was auch im Export landet (keine Kleinstfallzahlen in der Vorschau).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-11 — Vollständigkeits-Hinweise im Berichts-Panel

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_reporting_backends.py`, `src/tests/reporting/test_reporting_sample_report_1.py::TestValidateCompleteness` |

**Voraussetzung:** ENT-STAT-10; Zeitraum mit Ereignissen ohne Personenbezug bzw. ohne Berichtszuordnung (Seed liefert beides).

**Vorbereitung:**
- Panel „Beispiel-Sachbericht" offen.

**Schritte:**
1. Bereich oberhalb der Vorschau-Tabelle lesen.
2. Zeitraum auf einen Bereich ohne solche Lücken stellen (z.B. sehr kurzer Zeitraum) und erneut wählen.

**Erwartetes Ergebnis:**
- Mit Lücken: Badges „Hinweis"/„Info" mit Klartext, z.B. „… Ereignisse ohne Personenbezug im Zeitraum — nicht in Altersgruppen/Personenzahl enthalten".
- Ohne Lücken: grüner Kasten „Keine Vollständigkeitsprobleme erkannt".
- Fällt eine Zahl unter die Kleinstfallzahl-Schwelle, bleibt die Meldung stehen, nennt aber **keine Ziffer** („… unterdrückt (k-Anonymität)").

**DSGVO/Security-Note:**
- Die Meldungen dürfen die unterdrückte Zahl nicht rückrechenbar machen: keine Bezugsgröße, kein Prozentanteil in der Export-/Panel-Fassung.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-12 — Berichts-Downloads in drei Formaten (CSV/JSON/XML)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_reporting_backends.py` |

**Voraussetzung:** ENT-STAT-10.

**Vorbereitung:**
- Panel „Beispiel-Sachbericht" offen.

**Schritte:**
1. „CSV-Export" klicken.
2. „JSON-Export" klicken.
3. „XML-Export" klicken.
4. Dateien öffnen und mit der Panel-Vorschau vergleichen.

**Erwartetes Ergebnis:**
- Drei Downloads mit Dateinamen `sample-report-1_<von>_<bis>.csv` / `.json` / `.xml` (Slug des Backends, Zeitraum aus dem Panel).
- Inhalt deckungsgleich mit der Vorschau (gleiche Zeilen, gleiche Suppressionen).
- `assistenz`/`fachkraft` erhalten beim Direktaufruf von `/statistics/reports/export/` 403 (LEAD+, vgl. ENT-STAT-08).

**DSGVO/Security-Note:**
- Aggregate ohne Personenbezug; Übermittlung an Dritte nur rechtsgrundlagengestützt (vgl. ENT-STAT-06).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-13 — Neu registriertes Backend erscheint automatisch im Dropdown

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_reporting_backends.py`, `src/tests/reporting/test_reporting_registry.py` |

**Voraussetzung:** Beispiel-Backend `src/core/reporting/backends/example.py` vorhanden (Copy-Vorlage).

**Vorbereitung:**
- `/statistics/` als `leitung` offen.

**Schritte:**
1. Dropdown „Bericht" öffnen und die Einträge lesen.
2. „Beispiel: Ereignisse je Dokumenttyp" wählen.
3. „CSV-Export" klicken.

**Erwartetes Ergebnis:**
- Das Dropdown listet beide registrierten Backends (nach Slug sortiert), ohne dass View oder Template dafür angepasst wurden — die Registry entdeckt Backend-Module selbst.
- Panel des Beispiel-Backends zeigt eigene Spalten (Dokumenttyp/System-Typ/Anzahl) und die Zeilen-Zählung.
- Download heißt `example_<von>_<bis>.csv`.

**DSGVO/Security-Note:**
- Ein Backend, das für die Einrichtung nicht verfügbar ist (`is_available() == False`), darf weder im Dropdown erscheinen noch per Direkt-URL erreichbar sein (404).

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-14 — Zeitraum-Durchreichung: Dashboard-Zeitraum gilt für Bericht und Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `test_reporting_backends.py`, `src/tests/reporting/test_reporting_views.py::TestBerichteFormZeitraumDurchreichung` |

**Voraussetzung:** ENT-STAT-10.

**Vorbereitung:**
- `/statistics/` offen, Standard-Zeitraum aktiv.

**Schritte:**
1. Oben „Jahr" wählen (HTMX tauscht den Seiteninhalt; das Berichts-Panel ist danach leer).
2. Erneut „Beispiel-Sachbericht" wählen.
3. Zeitraum-Zeile des Panels lesen und mit dem Rest der Seite vergleichen.
4. Mit „Von/Bis" einen eigenen Zeitraum setzen, „Anwenden", Bericht erneut wählen.
5. Über einen Export-Link den Download ziehen und den Dateinamen prüfen.

**Erwartetes Ergebnis:**
- Panel-Zeitraum entspricht dem oben gewählten Zeitraum (z.B. „1. Januar … – …" nach „Jahr").
- Die Export-Links tragen denselben Zeitraum als `date_from`/`date_to`; der Dateiname des Downloads enthält beide Daten.
- Ein unzulässiger Zeitraum (Bis vor Von, zu lange Spanne) führt zu einer Fehlermeldung statt zu einem stillen Standard-Zeitraum.

**DSGVO/Security-Note:**
- Datenminimierung: exportiert wird nur der gewählte Zeitraum, nicht der Gesamtbestand.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-15 — Audit-Eintrag nach Berichts-Export

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `src/tests/reporting/test_reporting_views.py::TestDatasetExportView` |

**Voraussetzung:** ENT-STAT-12 (mindestens ein Download durchgeführt).

**Vorbereitung:**
- Zweiter Tab mit `/audit/`.

**Schritte:**
1. Im Berichts-Panel „JSON-Export" klicken.
2. `/audit/` neu laden und den obersten Eintrag öffnen.
3. Anschließend nur das Panel öffnen (Backend wählen, nichts herunterladen) und `/audit/` erneut prüfen.

**Erwartetes Ergebnis:**
- Der Export erzeugt genau einen Audit-Eintrag der Aktion „Export" mit Ziel-Typ `DatasetExport` und Detail-Feldern `backend`, `format`, `date_from`, `date_to`.
- Die reine Panel-Vorschau erzeugt **keinen** Audit-Eintrag (interne Ansicht, Parität zum Dashboard).

**DSGVO/Security-Note:**
- Rechenschaftspflicht (Art. 5 Abs. 2): jede Datenausleitung ist nachvollziehbar; der Audit-Eintrag enthält selbst keine Berichtsdaten.

**Status:** ☐ Offen

---

### TC-ID: ENT-STAT-16 — K-Anonymität: Kleinstfallzahlen im Export unterdrückt

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Statistik | leitung | C | ⚪ | `src/tests/reporting/test_reporting_sample_report_1.py::TestPrepareExport`, `test_reporting_backends.py` |

**Voraussetzung:** Kleinstfallzahl-Schwelle der Einrichtung gesetzt (Standard); Zeitraum mit Kategorien unterhalb der Schwelle.

**Vorbereitung:**
- Panel „Beispiel-Sachbericht" offen.

**Schritte:**
1. Vorschau-Tabelle lesen: Zellen unterhalb der Schwelle.
2. CSV-Export ziehen und dieselben Zellen im Export prüfen.
3. JSON- und XML-Export gegenprüfen.
4. Summen/Gesamtwerte gegen die sichtbaren Einzelwerte rechnen.

**Erwartetes Ergebnis:**
- Unterdrückte Zellen erscheinen in Vorschau **und** Export als „—" bzw. leer, nie als Zahl.
- Kein Feld erlaubt die Rückrechnung einer unterdrückten Zelle (Gesamtsumme/Bezugsgrößen fallen mit, wenn sie den Wert verraten würden).
- Der Hinweis auf die Unterdrückung bleibt sichtbar — der Bericht wirkt nicht fälschlich vollständig (CSV: Kommentarzeile im Metadaten-Kopf; JSON/XML/Panel: Vollständigkeits-Hinweise).

**DSGVO/Security-Note:**
- Art. 89/Statistikprivileg setzt Nicht-Identifizierbarkeit voraus: Kleinstfallzahlen sind der Haupt-Reidentifikationshebel externer Berichte.

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>🔍 AUDIT — Audit-Log (5 Cases)</strong></summary>

**Routen:** `/audit/`, `/audit/<uuid>/` 
**Views:** `src/core/views/audit.py` (AuditLogListView, AuditLogDetailView) 
**Services:** `src/core/services/audit.py`, `src/core/services/audit_hash.py` 
**Models:** `src/core/models/audit.py` (AuditLog, append-only, immutable) 
**E2E-Coverage:** `test_audit.py`, `test_audit_detail.py` 
**Spezial-Setup:** Append-Only-Probe via `python manage.py shell`. HMAC-Key aus Settings (`AUDIT_HMAC_KEY`).

---

### TC-ID: ENT-AUDIT-01 — Audit-Log Liste mit Pagination + Filter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** einige Aktionen geloggt (Login, Klient anlegen, Event löschen).

**Vorbereitung:**
- Mit admin einloggen.
- In zweiter Browser-Session als fachkraft einloggen, einen Klient anlegen und ein Event löschen — damit Audit-Einträge entstehen.

**Schritte:**
1. `/audit/` aufrufen.
2. Pagination prüfen (max. 50 Einträge pro Seite).
3. Filter nach Action-Type, User, Datumsbereich anwenden (HTMX-Partial-Update).
4. Sortierung der Spalte `timestamp` prüfen (absteigend = neueste zuerst).

**Erwartetes Ergebnis:**
- Liste mit Spalten User, Action, Timestamp, IP, Target-ID, Facility.
- Pagination via `?page=N`, max. 50 Einträge/Seite.
- Filter funktionieren als HTMX-Partial (kein Full-Page-Reload).
- Sortierung absteigend nach `timestamp` (neueste zuerst).

**DSGVO/Security-Note:**
- Audit-Log nur für ADMIN sichtbar (Art. 32 Zweckbindung, RLS).
- Keine Klartext-Emails in Action-Targets sichtbar (HMAC-Hash).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-02 — Detail-View zeigt Diff vorher/nachher

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit_detail.py` |

**Voraussetzung:** mindestens ein UPDATE-Eintrag im Audit-Log (z.B. Klient bearbeitet).

**Vorbereitung:**
- Als fachkraft einen Klient bearbeiten (Vorname ändern). Damit entsteht ein UPDATE-Audit mit `before`/`after`-Snapshot.

**Schritte:**
1. `/audit/` aufrufen, UPDATE-Eintrag suchen, anklicken.
2. Detail-View `/audit/<uuid>/` öffnen.
3. Alle Felder prüfen: User, Action, Timestamp, IP, User-Agent, Target-Model, Target-ID, Facility, `before`-Snapshot, `after`-Snapshot, HMAC-Signatur.
4. Diff-Darstellung prüfen (rot/grün, geänderte Felder hervorgehoben).

**Erwartetes Ergebnis:**
- Alle Felder lesbar, Snapshots als JSON oder strukturierte Tabelle.
- Diff zeigt nur geänderte Felder farblich hervorgehoben.
- HMAC-Signatur sichtbar (Validierungs-Indikator falls implementiert).

**DSGVO/Security-Note:**
- Snapshot enthält keine Klartext-Emails (HMAC-Hash an deren Stelle).
- Detail-View ist nur ADMIN zugänglich (Re-Auth/Sudo nicht erforderlich, da bereits ADMIN).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-03 — Filter nach Action-Type

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** Audit-Einträge mit verschiedenen Action-Types vorhanden (LOGIN, EXPORT, DELETE, CREATE, UPDATE).

**Vorbereitung:**
- Login als verschiedene User, einen Export auslösen, einen Datensatz löschen.

**Schritte:**
1. `/audit/` aufrufen.
2. Filter „Action-Type = LOGIN" anwenden — nur LOGIN-Events sichtbar.
3. Filter wechseln auf EXPORT — nur EXPORT-Events sichtbar.
4. Filter wechseln auf DELETE — nur DELETE-Events sichtbar.
5. Filter zurücksetzen (leer) — alle Events wieder sichtbar.

**Erwartetes Ergebnis:**
- Filter setzt Query-Parameter `?action=LOGIN` (HTMX-Partial).
- Liste zeigt nur Events des gewählten Action-Type.
- Combinierte Filter (Action + User + Datumsbereich) reduzieren weiter.

**DSGVO/Security-Note:**
- Filter ändern nicht die Sichtbarkeit fremder Facilities (RLS bleibt aktiv).
- Filter-Werte aus geschlossenem Choice-Set, kein freier String-Input (Injection-Schutz).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-05 — HMAC-Email-Probe (kein Klartext)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** `AUDIT_HMAC_KEY` in Settings gesetzt.

**Vorbereitung:**
- Test-User mit bekannter Email vorhanden (z.B. `fachkraft@example.com`).

**Schritte:**
1. Pwd-Reset für `fachkraft@example.com` über `/accounts/password_reset/` auslösen.
2. Als admin in `/audit/` den frischen LOGIN/PASSWORD_RESET-Eintrag öffnen.
3. Detail-View des Eintrags inspizieren.
4. Im `target`-Feld nach „fachkraft@example.com" suchen — darf NICHT vorhanden sein.
5. Stattdessen einen HMAC-Hash (Hex-String, 32+ Zeichen) finden.
6. In zweiter Probe: zweimal denselben Email-Hash erzeugen (zwei Pwd-Resets) → Hash deterministisch (gleicher Wert).

**Erwartetes Ergebnis:**
- Klartext-Email niemals in Audit-Snapshot, nur HMAC-Hash.
- Hash deterministisch bei gleicher Email + gleichem Key.
- Hash unbrauchbar zur Recovery (one-way).

**DSGVO/Security-Note:**
- **DSGVO Art. 32 (Pseudonymisierung):** Email als personenbezogenes Datum darf nicht in Audit-Log dauerhaft gespeichert werden.
- HMAC + geheimer Key = pseudonyme Korrelation möglich (z.B. „alle Aktionen User X"), aber kein Klartext-Recovery aus Audit-Backup.
- Bei `AUDIT_HMAC_KEY`-Rotation werden alte Korrelationen unbrauchbar (gewollt).

**Status:** ☐ Offen

---

### TC-ID: ENT-AUDIT-06 — Pagination + Sortierung bei > 1000 Einträgen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Audit | admin | C/F/S | ⚪ | `test_audit.py` |

**Voraussetzung:** > 1000 AuditLog-Einträge in DB (per Seed/Skript).

**Vorbereitung:**
- Skript: `for i in range(1100): AuditLog.objects.create(...)` oder `make seed --scale large`.

**Schritte:**
1. `/audit/` aufrufen.
2. Erste Seite: 50 Einträge, oberster Eintrag = neuester Timestamp.
3. Pagination: Seite 2, 3, …, letzte Seite. Performance prüfen (< 500 ms pro Page).
4. Tiefe Pagination: `?page=22` direkt anspringen.
5. Letzte Seite: ältester Eintrag, Anzahl ggf. < 50.
6. Pagination-Counter: „Seite 22 von 22, 1100 Einträge gesamt".

**Erwartetes Ergebnis:**
- Sortierung absteigend (neueste zuerst) konsistent über alle Seiten.
- Pagination-Performance konstant (DB-Index auf `timestamp` greift).
- Kein N+1-Query-Problem (Debug-Toolbar prüfen).

**DSGVO/Security-Note:**
- Pagination verhindert vollständigen Audit-Dump in einem Request (DoS-Schutz).
- Audit-Liste hat keine „CSV-Export aller Einträge"-Funktion (Zweckbindung).

**Status:** ☐ Offen

---


</details>

<details open>
<summary><strong>📜 DSGVO — DSGVO-Paket (10 Cases)</strong></summary>

**Routen:** `/dsgvo/`, `/dsgvo/download/<slug>/` 
**Views:** `src/core/views/dsgvo.py` (DSGVOPackageView, DSGVODocumentDownloadView) 
**Services:** `src/core/services/dsgvo_package.py` 
**Command:** `src/core/management/commands/generate_dsgvo_package.py` 
**E2E-Coverage:** `test_dsgvo_package.py` 
**Spezial-Setup:** Sudo-Mode aktiv (Re-Auth innerhalb 15 Min). Markdown-Templates in `src/templates/dsgvo/`.

---

### TC-ID: ENT-DSGVO-01 — DSGVO-Paket öffnen (Admin + Sudo)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** Admin eingeloggt, Sudo-Token gültig (frische Re-Auth).

**Vorbereitung:**
- Als admin einloggen.
- `/sudo/` aufrufen, Passwort erneut eingeben → Sudo-Token gesetzt (15 Min gültig).

**Schritte:**
1. `/dsgvo/` aufrufen.
2. Übersicht aller 7 Templates sichtbar: Art. 13/14, Art. 28, Art. 30, Art. 32, Art. 35, Beschäftigten-Information und Löschkonzept.
3. Pro Template: Titel, Kurzbeschreibung, Download-Button, letzter Generierungs-Timestamp.
4. Footer-Hinweis: „Templates rechtlich geprüft, keine Rechtsberatung".

**Erwartetes Ergebnis:**
- Liste aller 7 DSGVO-Templates mit Download-Links.
- Sudo-Indikator („Sudo aktiv, läuft ab um HH:MM") sichtbar.
- Templates dynamisch generiert (nicht statisch im Repo, sondern aus Facility-Daten).

**DSGVO/Security-Note:**
- **Re-Auth/Sudo (Art. 32):** Hochsensible Operation, deshalb zusätzliche Authentifizierung.
- Audit-Log-Eintrag „DSGVO_PACKAGE_VIEWED" pro Aufruf.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-02 — Re-Auth-Loop ohne Sudo

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** Admin eingeloggt, Sudo-Token abgelaufen oder nicht gesetzt.

**Vorbereitung:**
- Als admin einloggen.
- Falls Sudo aktiv: 15 Min warten oder Cookie/Session-Sudo-Key löschen.

**Schritte:**
1. `/dsgvo/` aufrufen ohne aktiven Sudo.
2. Erwarte 403 oder Redirect zu `/sudo/?next=/dsgvo/`.
3. Auf `/sudo/` Passwort erneut eingeben.
4. Nach erfolgreicher Re-Auth automatischer Redirect zurück zu `/dsgvo/`.
5. Nach 15 Min ohne Aktivität: erneuter Aufruf → wieder Re-Auth-Loop.

**Erwartetes Ergebnis:**
- Ohne Sudo kein Zugriff auf `/dsgvo/` und `/dsgvo/download/<slug>/`.
- Redirect-Kette: `/dsgvo/` → `/sudo/?next=/dsgvo/` → nach Pwd-Eingabe → `/dsgvo/`.
- Sudo-Token TTL = 15 Min (in Settings konfiguriert).

**DSGVO/Security-Note:**
- **Sudo schützt vor Session-Hijacking:** Selbst mit gestohlenem Session-Cookie keine DSGVO-Doku-Downloads ohne Pwd.
- Failed-Sudo-Versuche werden in AuditLog (`SUDO_FAILED`) geloggt.
- Brute-Force-Limit: max. 5 Versuche/IP/Stunde.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-03 — Verarbeitungsverzeichnis Art. 30 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 30 — Verarbeitungsverzeichnis" Download klicken.
2. Datei `verarbeitungsverzeichnis-art-30.md` (oder PDF) wird heruntergeladen.
3. Datei öffnen — Markdown-Struktur:
   - Verantwortlicher (Facility-Name, Adresse, Email aus Facility-Settings)
   - Datenschutzbeauftragter (DPO aus Settings)
   - Verarbeitungstätigkeiten (Klientenverwaltung, Fallakte, …)
   - Kategorien betroffener Personen + Daten
   - Empfänger, Drittländer, Löschfristen, TOMs-Verweis
4. Generierungs-Timestamp im Footer.

**Erwartetes Ergebnis:**
- Markdown rendert korrekt, alle Platzhalter ersetzt (kein `{{ facility_name }}` mehr).
- Inhalt facility-spezifisch (eigene Adresse, eigener DPO).
- Audit-Log-Eintrag `DSGVO_EXPORT` mit slug=`verarbeitungsverzeichnis-art-30`.

**DSGVO/Security-Note:**
- **Art. 30 ist Pflicht-Doku:** Jeder Verantwortliche muss ein Verarbeitungsverzeichnis führen.
- Template ist juristisch geprüft, aber facility-spezifische Felder müssen im Admin gepflegt werden (Hinweis-Banner).

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-04 — DSFA-Template Art. 35 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 35 — DSFA (Datenschutz-Folgenabschätzung)" Download klicken.
2. Datei `dsfa-art-35.md` herunterladen.
3. Inhalt prüfen:
   - Beschreibung der Verarbeitung
   - Notwendigkeit + Verhältnismäßigkeit
   - Risiken für Betroffene (Diskriminierung, Identitätsdiebstahl, …)
   - Abhilfemaßnahmen (TOMs, Pseudonymisierung, RLS)
   - Bewertung (Risiko hoch/mittel/niedrig)
4. Verweis auf Art. 35 Abs. 7 (Pflichtfelder).

**Erwartetes Ergebnis:**
- DSFA-Template strukturiert nach Art. 35 Abs. 7.
- Sozialarbeit-spezifische Risiken vorausgefüllt (besonders schutzbedürftige Personen, Gesundheitsdaten).
- Audit-Eintrag mit slug=`dsfa-art-35`.

**DSGVO/Security-Note:**
- **Art. 35 ist Pflicht für Sozialdaten:** Hohes Risiko für Rechte und Freiheiten (Kategorie „besonders schutzbedürftige Personen").
- DPO-Konsultation in Template als Schritt verankert.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-05 — AV-Vertrag Art. 28 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 28 — AV-Vertrag (Auftragsverarbeitung)" klicken.
2. Datei `av-vertrag-art-28.md` herunterladen.
3. Inhalt prüfen:
   - Auftraggeber (Verantwortlicher) + Auftragsverarbeiter (z.B. Hosting-Provider)
   - Gegenstand, Dauer, Art der Verarbeitung
   - Pflichten des Auftragsverarbeiters (Art. 28 Abs. 3 a-h)
   - Subunternehmer-Klausel
   - Weisungsrecht
   - Haftung + Vertragsstrafen
4. Unterschriftsfelder beider Parteien.

**Erwartetes Ergebnis:**
- AV-Vertrag strukturiert nach Art. 28 Abs. 3.
- Eigener Hosting-Provider (z.B. Hetzner) als Beispiel-Auftragsverarbeiter eingetragen.
- Subunternehmer-Liste mit Zustimmungsklausel.

**DSGVO/Security-Note:**
- **Art. 28 ist Pflicht bei Auftragsverarbeitung:** Hosting, Backup, Email-Provider etc. brauchen AV-Vertrag.
- Template-Hinweis: „Vertrag ist juristisch zu prüfen, kein Rechtsberatung".

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-06 — TOMs-Template Art. 32 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 32 — Technische und organisatorische Maßnahmen (TOMs)" klicken.
2. Datei `toms-art-32.md` herunterladen.
3. Inhalt prüfen:
   - Vertraulichkeit (Zutritts-, Zugangs-, Zugriffskontrolle)
   - Integrität (Eingabe-, Weitergabekontrolle)
   - Verfügbarkeit (Verfügbarkeits-, Wiederherstellbarkeitskontrolle)
   - Belastbarkeit (Pen-Tests, Notfallplan)
   - Pseudonymisierung + Verschlüsselung (HMAC-Hash, AES-GCM, RLS)
4. Konkrete App-Maßnahmen vorausgefüllt (Argon2id, MFA, Audit-Log, Backup-Verschlüsselung).

**Erwartetes Ergebnis:**
- TOMs-Template enthält alle Schutzziele aus Art. 32 Abs. 1.
- App-spezifische Maßnahmen automatisch eingetragen (Argon2id, RLS, Sudo, …).
- Verweis auf Backup-Verschlüsselung, ClamAV, CSP.

**DSGVO/Security-Note:**
- **Art. 32 verpflichtet zu „Stand der Technik":** Argon2id (statt PBKDF2), AES-GCM (statt CBC), TLS 1.3.
- TOMs-Doku ist Pflicht bei Audit durch Aufsichtsbehörde.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-07 — Informationspflichten Art. 13/14 download

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. Auf „Art. 13/14 — Informationspflichten" klicken.
2. Datei `informationspflichten-art-13-14.md` herunterladen.
3. Inhalt prüfen:
   - Identität + Kontakt des Verantwortlichen
   - Kontaktdaten DPO
   - Zwecke der Verarbeitung + Rechtsgrundlage (z.B. SGB VIII § 61)
   - Empfänger / Empfängerkategorien
   - Drittlandtransfers (i.d.R. „nein")
   - Speicherdauer (Aktenführung-Fristen aus SGB)
   - Betroffenenrechte (Art. 15-22, Beschwerderecht Art. 77)
4. Zwei Varianten: Erhebung beim Betroffenen (Art. 13) vs. Dritter (Art. 14).

**Erwartetes Ergebnis:**
- Beide Varianten klar getrennt.
- Sozialdaten-spezifische Rechtsgrundlagen (SGB VIII, BDSG).
- Klartext, betroffenenfreundliche Sprache.

**DSGVO/Security-Note:**
- **Art. 13 = Datenerhebung beim Betroffenen, Art. 14 = bei Dritten** (z.B. eine Behörde meldet Klientel an die Anlaufstelle).
- Information muss zum Zeitpunkt der Erhebung gegeben werden.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-08 — Löschkonzept herunterladen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. „Löschkonzept" herunterladen.
2. Gerenderte Fristen für Kontakte, Aktivitäten, Papierkorb und Audit-Log mit den Einrichtungs-Einstellungen vergleichen.
3. Backup-Frist, Legal-Hold-Grenze und Footer mit Settings-Hash prüfen.

**Erwartetes Ergebnis:**
- Kein `{{ ... }}`-Platzhalter bleibt im Dokument stehen.
- Die sechs einrichtungsbezogenen Fristen entsprechen den aktuellen Einstellungen.
- Die Backup-Frist entspricht der längsten Rotation des ausgelieferten `backup.sh`.

**DSGVO/Security-Note:**
- Das Dokument trennt die Löschung im aktiven System von der Rotation integritätsgesicherter Sicherungsstände.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-09 — Beschäftigten-Informationspflichten herunterladen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** ENT-DSGVO-01 erfolgreich.

**Vorbereitung:**
- `/dsgvo/` mit aktivem Sudo geöffnet.

**Schritte:**
1. „Informationspflichten für Beschäftigte" herunterladen.
2. Verantwortlichen, Zwecke, Datenkategorien und Rechtsgrundlagen prüfen.
3. Gerenderte Audit-Log-Frist und Hinweise zu Betroffenenrechten kontrollieren.

**Erwartetes Ergebnis:**
- Das Dokument nennt Art. 13/14 DSGVO und § 26 BDSG.
- Der Einrichtungsname und die aktuelle Audit-Log-Frist sind eingesetzt.
- Kein `{{ ... }}`-Platzhalter bleibt im Dokument stehen.

**DSGVO/Security-Note:**
- Das Artefakt richtet sich an Beschäftigte und ist von den Informationspflichten für Klient*innen getrennt.

**Status:** ☐ Offen

---

### TC-ID: ENT-DSGVO-10 — AuditLog-Eintrag pro Download (Audit-Spur)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| DSGVO | admin | C/F/S | ⚪ | `test_dsgvo_package.py` |

**Voraussetzung:** mindestens ein DSGVO-Download durchgeführt.

**Vorbereitung:**
- Alle 7 verschiedenen DSGVO-Dokumente als admin herunterladen.

**Schritte:**
1. `/audit/` aufrufen.
2. Filter „Action-Type = EXPORT" oder „DSGVO_EXPORT" anwenden.
3. Pro Download ein Audit-Eintrag mit:
   - User (admin-Username)
   - Action = `DSGVO_EXPORT`
   - Target = Slug (z.B. `verarbeitungsverzeichnis-art-30`)
   - Timestamp
   - IP + User-Agent
4. Detail-View `/audit/<uuid>/` zeigt vollständige Metadaten.

**Erwartetes Ergebnis:**
- Pro Download genau 1 Audit-Eintrag.
- Slug eindeutig pro Template (7 verschiedene Slugs für 7 Templates).
- Audit-Log auch für DSGVO-Doku-Aufruf selbst (Meta-Audit).

**DSGVO/Security-Note:**
- **Audit-Spur fürs Audit:** Wer hat wann welche DSGVO-Doku heruntergeladen — relevant bei Aufsichts-Audit.
- Häufung von Downloads = Indiz für Audit-Vorbereitung oder Datenleck-Verdacht.
- Append-Only schützt diese Meta-Audits ebenfalls.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📡 OFFL — Offline & PWA (12 Cases)</strong></summary>

**Routen:** `/offline/`, `/offline/clients/<uuid>/`, `/offline/conflicts/`, `/api/offline/bundle/client/<uuid>/`, `/auth/offline-key-salt/`, `/manifest.json`, `/sw.js` 
**Views:** `src/core/views/offline.py` (OfflineBootstrapView, OfflineBundleView, OfflineClientDetailView, OfflineConflictListView) 
**Services:** `src/core/services/system/offline.py` 
**E2E-Coverage:** `test_offline_apis.py`, `test_offline_login_bootstrap.py`, `test_offline_store.py`, `test_pwa_offline.py` 
**Spezial-Setup:** Service Worker im Browser, IndexedDB, DevTools → Application-Tab. Streetwork-Geräte simulieren (mobile Viewport 375x667).

---

### TC-ID: ENT-OFFL-01 — Login-Bootstrap lädt Offline-Bundle in IndexedDB

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_login_bootstrap.py` |

**Voraussetzung:** fachkraft-Account, mindestens 5 Klient zugeordnet.

**Vorbereitung:**
- Chrome DevTools öffnen, Tab „Application" → IndexedDB. Vor Login: leer.

**Schritte:**
1. Login als fachkraft via `/login/`.
2. Nach erfolgreichem Login: DevTools → Application → IndexedDB → Datenbank `anlaufstelle-offline` aufgeklappt.
3. Object-Stores prüfen: `clients`, `cases`, `events`, `meta`.
4. Daten-Anzahl prüfen — entspricht der Anzahl der zugewiesenen Klient (RLS + Caseworker-Filter).
5. Network-Tab: Bootstrap-Call `GET /api/offline/bootstrap/` mit Status 200.
6. Lease-Eintrag in `meta`-Store: `expires_at` in der Zukunft (z.B. +7 Tage).

**Erwartetes Ergebnis:**
- IndexedDB nach Login mit verschlüsselten Daten gefüllt (AES-GCM).
- Anzahl entspricht Caseworker-Zuordnung, keine fremden Klient.
- Bootstrap-Trigger in Login-Flow integriert (kein manueller Reload nötig).

**DSGVO/Security-Note:**
- **AES-GCM-Verschlüsselung in IndexedDB (Art. 32):** Bei verlorenem Gerät keine Klartext-Klientendaten lesbar.
- Schlüssel ist password-derived (PBKDF2), nicht im Klartext gespeichert.
- Lease-Mechanismus erzwingt regelmäßige Re-Auth.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-02 — Offline-Client-Bundle als JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** fachkraft eingeloggt, mindestens 1 Klient zugewiesen.

**Vorbereitung:**
- Klient-UUID notieren (z.B. aus `/clients/`).

**Schritte:**
1. `/api/offline/bundle/client/<uuid>/` direkt aufrufen.
2. Response: `Content-Type: application/json`.
3. JSON-Struktur prüfen:
   - `client`: Stammdaten (Name, Geburtsdatum, …)
   - `cases`: Liste der Fälle
   - `events`: Termine
   - `notes`: Notizen
   - `documents_meta`: Metadaten Dokumente (ohne Binärinhalt)
   - `_version`: Sync-Token / Timestamp
4. Versuch mit fremder UUID (nicht zugewiesen) → 404 oder 403.

**Erwartetes Ergebnis:**
- JSON vollständig, alle relations vorgeladen (kein N+1).
- Document-Binärinhalte NICHT inkludiert (zu groß, separater Endpoint).
- Sync-Token für Konflikt-Erkennung enthalten.

**DSGVO/Security-Note:**
- API-Authentifizierung via Session-Cookie + CSRF.
- Cross-User/Cross-Facility-Probe: 404 (RLS).
- Bundle ist „Stand zum Zeitpunkt des Calls" (snapshot).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-03 — Offline-Client-Detail (lokaler Scaffold)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** ENT-OFFL-01 erfolgreich, IndexedDB gefüllt.

**Vorbereitung:**
- DevTools → Network → „Offline" aktivieren.

**Schritte:**
1. Bei aktivem Offline-Modus: `/offline/clients/<uuid>/` aufrufen.
2. Seite rendert komplett aus IndexedDB (kein Netz-Call).
3. Anzeige: Stammdaten, Fall-Liste, Notizen.
4. Edit-Button verfügbar — Notiz hinzufügen.
5. Notiz wird lokal gespeichert (Pending-Sync-Marker im IndexedDB-Store `pending_writes`).

**Erwartetes Ergebnis:**
- Seite lädt offline ohne Netzfehler.
- Service-Worker liefert HTML-Scaffold + JS für Hydration aus Cache.
- Lokaler Edit funktioniert, Pending-Marker sichtbar (z.B. „⏳ Wird beim nächsten Sync übertragen").

**DSGVO/Security-Note:**
- Daten-Decryption via password-derived Key (im Memory, nicht persistiert).
- Bei Browser-Schließen → Key weg, beim nächsten Login Re-Decrypt.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-04 — Service-Worker Registration

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** frischer Browser-Profile (kein vorhandener SW).

**Vorbereitung:**
- Chrome → DevTools → Application → Service Workers (vor Login: leer).

**Schritte:**
1. `/` aufrufen (Login-Seite oder Landing).
2. JavaScript registriert SW: `navigator.serviceWorker.register('/sw.js')`.
3. DevTools → Application → Service Workers: Status `activated`, Source `/sw.js`.
4. Cache Storage: `anlaufstelle-static-v<N>`, `anlaufstelle-runtime-v<N>` Buckets sichtbar.
5. SW-Update-Test: Code-Änderung in `sw.js` → Reload → Update-Event.

**Erwartetes Ergebnis:**
- SW erfolgreich registriert (Status `activated`).
- Pre-Cache statischer Assets (CSS, JS, Manifest, Offline-Fallback-HTML).
- Runtime-Cache für API-Bundles.

**DSGVO/Security-Note:**
- SW läuft nur auf HTTPS (oder localhost) — Same-Origin-Policy.
- Kein Caching sensibler API-Responses ohne Auth-Check (Conditional-Caching).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-05 — Offline-Fallback-Seite

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** ENT-OFFL-04 erfolgreich, SW aktiv.

**Vorbereitung:**
- DevTools → Network → „Offline" aktivieren.

**Schritte:**
1. `/clients/` aufrufen während offline (Online-Seite, kein Offline-Scaffold).
2. SW fängt Request ab, liefert `/offline/`-Fallback-HTML aus Cache.
3. Fallback-Seite zeigt: „Du bist offline. Verfügbare Klient-Daten findest du unter Offline-Bereich."
4. Link „Offline-Klient öffnen" zu `/offline/clients/<uuid>/` für gecachten Klient.
5. Online wieder aktivieren → Reload → normale `/clients/`-Seite lädt.

**Erwartetes Ergebnis:**
- Fallback-Seite klar erkennbar, kein „weißer Bildschirm".
- Hinweise auf verfügbare Offline-Funktionen.
- SW unterscheidet zwischen API-404 und Netzwerk-Fehler.

**DSGVO/Security-Note:**
- Fallback-Seite enthält keine sensiblen Daten (statischer Inhalt).
- Pending-Writes-Counter sichtbar („3 Notizen warten auf Sync").

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-06 — Manifest.json gültig (PWA-Installation)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** fachkraft eingeloggt.

**Vorbereitung:**
- Chrome auf Mobile-Device (oder DevTools Device-Mode 375x667).

**Schritte:**
1. `/manifest.json` direkt aufrufen.
2. JSON-Struktur prüfen: `name`, `short_name`, `start_url`, `display: standalone`, `background_color`, `theme_color`, `icons` (mehrere Auflösungen 192/512), `scope`.
3. Chrome zeigt „Zum Startbildschirm hinzufügen"-Banner an.
4. Banner akzeptieren — App-Icon im Homescreen.
5. App vom Homescreen starten — öffnet im Standalone-Mode (kein Browser-UI).
6. Lighthouse PWA-Audit: alle Pflicht-Checks grün.

**Erwartetes Ergebnis:**
- Manifest valide nach W3C-Spec.
- PWA-Installation auf Android + iOS möglich.
- Lighthouse-PWA-Score > 90.

**DSGVO/Security-Note:**
- Standalone-Mode versteckt URL-Bar — Phishing-Risiko (mitigiert durch fest verdrahtete Anlaufstelle-Domain).
- Manifest enthält keine personenbezogenen Daten (statisch).

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-07 — Sync-Konflikt-Liste

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_store.py` |

**Voraussetzung:** mindestens 2 Sync-Konflikte erzeugt (gleicher Datensatz offline + online geändert).

**Vorbereitung:**
- Klient-Notiz offline ändern auf Gerät A.
- Parallel selben Klient online ändern (z.B. via Web-UI).
- Gerät A wieder online → Sync triggert Konflikt.

**Schritte:**
1. `/offline/conflicts/` aufrufen.
2. Liste der Konflikte mit Spalten: Klient, Feld, lokaler Wert, Server-Wert, Zeitstempel beider Versionen.
3. Pro Konflikt Buttons: „Lokal übernehmen", „Server übernehmen", „Manuell mergen".
4. Filter nach Klient oder Feld.
5. Counter im Header: „2 ungelöste Konflikte".

**Erwartetes Ergebnis:**
- Konflikte klar dargestellt, beide Versionen lesbar.
- Sortierung nach Zeitstempel (neueste zuerst).
- Badge mit Konflikt-Counter sichtbar (HTMX-Refresh).

**DSGVO/Security-Note:**
- Konflikt-Daten in Audit-Log (`SYNC_CONFLICT_DETECTED`).
- Beide Versionen werden vorübergehend gespeichert bis Auflösung.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-08 — Sync-Konflikt-Review (Merge-Entscheidung)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_store.py` |

**Voraussetzung:** ENT-OFFL-07 erfolgreich, mindestens 1 offener Konflikt.

**Vorbereitung:**
- `/offline/conflicts/` mit > 0 Konflikten geöffnet.

**Schritte:**
1. Auf einen Konflikt klicken → Detail-Ansicht.
2. Nebeneinander beide Versionen (Diff-Style, rot/grün).
3. „Server übernehmen" wählen → lokaler Wert verworfen, Server-Wert in IndexedDB übernommen.
4. Konflikt aus Liste entfernt, Counter dekrementiert.
5. Audit-Log-Eintrag `SYNC_CONFLICT_RESOLVED` mit Entscheidung (server-wins/local-wins/merge).
6. Bei „Manuell mergen" → Editor mit beiden Werten, freier Text.

**Erwartetes Ergebnis:**
- Auflösung persistiert auf Server + lokal.
- Audit-Log dokumentiert Entscheidung + User.
- Keine doppelten Auflösungen möglich (Idempotenz).

**DSGVO/Security-Note:**
- **Art. 5 (Richtigkeit):** Konflikt-Auflösung ist nachvollziehbar dokumentiert.
- Bei manuellem Merge: User trägt Verantwortung, AuditLog enthält finalen Text.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-09 — Offline-Crypto: Salt + PBKDF2 + AES-GCM

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** fachkraft eingeloggt.

**Vorbereitung:**
- DevTools → Network-Tab geöffnet.

**Schritte:**
1. Login als fachkraft.
2. Network-Tab: Call `GET /auth/offline-key-salt/` mit Status 200.
3. Response-Body: JSON mit `salt` (Base64-String, 16+ Bytes), `iterations` (z.B. 600.000), `hash_algo` (z.B. sha256).
4. JS-Code prüfen: PBKDF2 mit Pwd + Salt + Iterations → Key-Material.
5. Key wird via Web-Crypto-API in AES-GCM-Encrypter verwandelt (256-bit).
6. IndexedDB-Daten manuell inspizieren: nur ciphertext + iv pro Eintrag, kein Klartext.

**Erwartetes Ergebnis:**
- Salt facility-spezifisch oder user-spezifisch (nicht global).
- PBKDF2-Iterations ≥ OWASP-Empfehlung (600.000 für SHA-256).
- AES-GCM mit 256-bit Key, IV pro Eintrag random.
- Keine Klartext-Daten in IndexedDB.

**DSGVO/Security-Note:**
- **Art. 32 (Verschlüsselung at rest):** Browser-Speicher ist „at rest" — daher AES-GCM Pflicht.
- Salt + Pwd → Key-Derivation erfüllt „Stand der Technik".
- IV-Wiederverwendung wäre Sicherheitslücke, daher random pro Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-10 — Salt-Rotation nach Pwd-Wechsel

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** ENT-OFFL-09 erfolgreich, IndexedDB mit verschlüsselten Daten gefüllt.

**Vorbereitung:**
- IndexedDB-Snapshot (Eintrag mit ciphertext kopieren).

**Schritte:**
1. Pwd-Wechsel via `/accounts/password_change/` durchführen.
2. Im Backend wird `User.offline_key_salt` geleert und gespeichert (`CustomPasswordChangeView`, `src/core/views/auth.py:219-220`; Queryset-Reset bei Rollen-/Aktiv-Änderung in `src/core/signals/offline_invalidation.py:89-97`).
3. Logout, dann erneuter Login mit neuem Pwd.
4. Salt-Endpoint liefert neuen Salt → neuer abgeleiteter Key.
5. IndexedDB-Decrypt-Versuch alter Eintrag mit neuem Key → Fehler (alte Daten nicht entschlüsselbar).
6. Offline-Bootstrap lädt Daten neu mit neuem Key (Re-Bootstrap).
7. Audit-Log-Eintrag `OFFLINE_KEY_FETCH` mit Detail `offline_key_salt_fetched` (`src/core/views/auth.py:240`).

**Erwartetes Ergebnis:**
- Alte lokale Daten unentschlüsselbar nach Pwd-Wechsel (gewollt: Schutz bei kompromittiertem Pwd).
- Re-Bootstrap automatisch nach erstem Login mit neuem Pwd.
- Keine Datenverlust-Gefahr (Server-Daten bleiben).

**DSGVO/Security-Note:**
- **Defense bei Pwd-Kompromittierung:** Neuer Salt invalidiert alle alten Offline-Caches auf gestohlenen Geräten.
- Pending-Writes vor Pwd-Wechsel müssen gesynct werden, sonst Daten-Verlust.
- UI-Hinweis: „Bitte erst Sync abwarten, dann Pwd ändern".

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-11 — Cache-Lease abgelaufen → Re-Bootstrap

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_offline_apis.py` |

**Voraussetzung:** ENT-OFFL-01 erfolgreich, Lease in `meta`-Store gesetzt.

**Vorbereitung:**
- DevTools → IndexedDB → `meta` → `lease.expires_at` manuell auf Vergangenheit setzen (z.B. `2020-01-01`).

**Schritte:**
1. App neu laden / Login-Status prüfen.
2. Code prüft beim Start: Lease abgelaufen?
3. Bei Ablauf: Lokale Daten werden invalidiert (oder als „stale" markiert).
4. UI-Banner: „Cache abgelaufen, bitte neu laden".
5. Bootstrap-Call wird automatisch oder per Klick ausgelöst.
6. Neuer Lease gesetzt mit neuem `expires_at`.

**Erwartetes Ergebnis:**
- Abgelaufener Lease blockiert Offline-Zugriff (oder warnt prominent).
- Re-Bootstrap lädt frische Daten.
- Pending-Writes werden VOR Invalidation gesynct (Daten-Verlust-Schutz).

**DSGVO/Security-Note:**
- **Lease ist Datenminimierung-Mechanismus (Art. 5):** Daten verbleiben nicht unbegrenzt offline.
- Default-Lease-TTL z.B. 7 Tage — Streetwork-Realismus, aber kein Lifetime-Cache.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-12 — Streetwork-Workflow (End-to-End-Smoke)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_pwa_offline.py` |

**Voraussetzung:** fachkraft mit zugewiesenen Klient.

**Vorbereitung:**
- Mobile Device (oder Chrome DevTools Mobile-Mode 375x667).
- Network ON.

**Schritte:**
1. **Online (Büro):** Login als fachkraft. Bootstrap lädt Bundle (ENT-OFFL-01).
2. **Offline (Außendienst):** DevTools-Network → „Offline".
3. `/offline/clients/<uuid>/` öffnen → Daten sichtbar.
4. Notiz hinzufügen: „Beratungstermin durchgeführt, Klient stabil."
5. Pending-Marker erscheint (⏳).
6. Status-Update: Termin als „durchgeführt" markieren.
7. **Online wieder (zurück im Büro):** DevTools-Network → wieder online.
8. SW erkennt Network → triggert Sync der Pending-Writes.
9. Pending-Marker verschwinden, Notiz + Status auf Server.
10. Audit-Log: 2 Einträge (`NOTE_CREATED`, `EVENT_UPDATED`) mit Sync-Origin = `offline_sync`.

**Erwartetes Ergebnis:**
- Vollständiger Streetwork-Zyklus funktioniert offline → online.
- Keine Daten verloren.
- Sync ist idempotent (Re-Trigger erzeugt keine Duplikate).

**DSGVO/Security-Note:**
- **Streetwork-Realität:** Sozialarbeiter dokumentieren oft im Außendienst ohne Netz.
- Pending-Writes sind verschlüsselt in IndexedDB (AES-GCM).
- Audit-Log markiert Sync-Origin für forensische Nachvollziehbarkeit.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-13 — Logout löscht lokale Offline-Daten

<!-- E2E: test_logout_cleanup.py (test_logout_indexeddb_databases_contain_no_plaintext deckt IndexedDB-Wipe ab) -->

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft | C | ✓ | `test_logout_cleanup.py` |

**Voraussetzung:** ENT-OFFL-01 erfolgreich, IndexedDB mit Bundle gefüllt.

**Vorbereitung:**
- DevTools → Application → IndexedDB Snapshot vor Logout.

**Schritte:**
1. Logout-Button klicken.
2. DevTools → IndexedDB nach Reload prüfen:
   - `clients`-Store leer.
   - `pending`-Store leer.
   - `meta.lease` gelöscht.
3. Cookies: Session-Cookie gelöscht (Clear-Site-Data-Header ENT-AUTH-06).
4. Erneuter Login als anderer User → vorherige Klient-Daten nicht sichtbar.

**Erwartetes Ergebnis:**
- Lokale Daten beim Logout vollständig gelöscht.
- Geräte-Wechsel zwischen User:innen leakt keine Daten.

**DSGVO/Security-Note:** Art. 5 Abs. 1 lit. e (Speicherbegrenzung) + Art. 32 (TOM). Shared-Device-Szenario in Sozialarbeit-Teams.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-14 — Rollen-/Sensitivity-Änderung invalidiert Offline-Bundle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | facility_admin + fachkraft | C (2 Profile) |||

**Voraussetzung:** fachkraft A mit aktivem Offline-Bundle, `assistenz`-Rolle.

**Schritte:**
1. fachkraft A: einloggen, Offline-Bundle laden (`ENT-OFFL-01`).
2. fachkraft A: DevTools → IndexedDB → ein Event mit `HIGH`-Sensitivity-Feld sichtbar.
3. `facility_admin` B (anderer Browser): Rolle von A auf `assistenz` herabstufen.
4. fachkraft A: Seite neu laden / Sync triggern.
5. Erwartung: Bundle wird neu geladen mit den jetzt erlaubten Sensitivities, `HIGH`-Felder sind weg.

**Erwartetes Ergebnis:**
- Bundle respektiert die *aktuelle* Rolle, nicht die zur Bootstrap-Zeit.
- Falls aktuell nicht durchgesetzt: Issue eröffnen — Rollen-Downgrade muss Re-Bootstrap erzwingen.

**DSGVO/Security-Note:** Art. 25 (Privacy-by-Default). Rollenwechsel ist häufiger Auslöser für Datenschutz-Risiken bei Personalwechsel.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-15 — Lease-Verlust durch Server-Reset

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft + admin | C | ✓ ||

**Voraussetzung:** Lease aktiv, IndexedDB gefüllt.

**Schritte:**
1. fachkraft: Bundle laden, in IndexedDB sichtbar.
2. admin (per `manage.py shell` / SSH): Server-seitig `OfflineLease`-Eintrag für diesen User löschen oder revoke flag setzen.
3. fachkraft: Seite neu laden.
4. Bundle-Status-Endpoint meldet „Lease invalidiert".
5. UI zeigt deutlich, dass Offline-Bundle nicht mehr nutzbar ist.
6. Lokale Daten bleiben verschlüsselt in IndexedDB — werden aber nicht angezeigt und nicht synct.

**Erwartetes Ergebnis:**
- Server kann eine Lease zentral revoken (für gestohlenes/verlorenes Gerät).
- Daten bleiben verschlüsselt; ohne gültige Lease kein Decrypt.
- Re-Bootstrap nur nach erneutem Login.

**DSGVO/Security-Note:** Defense gegen Geräte-Verlust — kritischer Sozialarbeit-Use-Case.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-16 — Offline-Sync-Konflikt mit gelöschtem Event

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft + leitung | C (2 Profile) |||

**Schritte:**
1. fachkraft A: offline gehen.
2. A bearbeitet ein Event E1 lokal (pending-Write).
3. leitung B (online): selbes Event E1 soft-deleten (z.B. via Retention-Approve).
4. A: wieder online → Sync läuft.
5. Sync erkennt: Server-Event ist `deleted=True`.

**Erwartetes Ergebnis:**
- Pending-Write wird nicht stillschweigend verworfen.
- Konflikt landet in Sync-Konflikt-Liste (ENT-OFFL-07) mit klarem Hinweis: „Event wurde inzwischen gelöscht/retention-anonymisiert".
- User kann entscheiden: lokale Notiz exportieren oder verwerfen.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-17 — Offline-Sync-Konflikt mit retention-betroffenem Event

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft + admin | C |||

**Schritte:**
1. fachkraft A: offline mit Event E2 (älter, kurz vor Retention-Frist).
2. Server-Cron: `enforce_retention` läuft, E2 wird anonymisiert (`data_json={}`).
3. A: offline-Bearbeitung an E2 (Notiz hinzufügen).
4. A: online → Sync.
5. Sync vergleicht Pre-/Post-Snapshot: Server-Event ist `_redacted`.

**Erwartetes Ergebnis:**
- Sync verweigert Write auf anonymisiertes Event.
- Konflikt-Liste zeigt: „Event wurde anonymisiert — Notiz nicht synct, lokal markieren oder verwerfen".

**DSGVO/Security-Note:** Verhindert „Wiederbelebung" anonymisierter Daten durch Offline-Sync.

**Status:** ☐ Offen

---

### TC-ID: ENT-OFFL-18 — Offline-Upload mit später serverseitig abgelehntem Dateityp

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Offline | fachkraft + facility_admin | C |||

**Schritte:**
1. fachkraft A: offline gehen, Event mit `.docx`-Anhang lokal vorbereiten (Client hat noch `docx` in `allowed_file_types`).
2. facility_admin B (online): Settings → `allowed_file_types` ändern auf `pdf,jpg,png` (docx raus).
3. A: online → Sync versucht Upload.
4. Server-Service rejectet mit Validation-Error.

**Erwartetes Ergebnis:**
- Upload wird sauber abgelehnt, **kein** 500.
- Pending-Eintrag bekommt klare Fehlermeldung in der Sync-Konflikt-Liste: „Dateityp nicht mehr erlaubt".
- AuditLog `SECURITY_VIOLATION` mit `reason=extension_not_allowed` für den Pfad.
- User kann lokal Datei behalten und Konflikt manuell auflösen (z.B. PDF konvertieren und neu hochladen).

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>⚙️ SYS — System & Operations (6 Cases)</strong></summary>

**Routen:** `/health/`, `/csp-report/`, `/robots.txt`, `/manifest.json`, `/?lang=de` 
**Views:** `src/core/views/health.py`, `src/core/views/csp_report.py`, `src/core/views/robots.py`, `src/core/views/pwa.py` 
**E2E-Coverage:** `test_security_hardening.py` 
**Spezial-Setup:** Health prüft externe Dienste (ClamAV, Redis, Backup-Status). CSP-Report-Endpoint mit Rate-Limit.

---

### TC-ID: ENT-SYS-01 — Health-Endpoint liefert JSON

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich (Monitoring) | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** alle Container laufen (Postgres, Redis, ClamAV).

**Vorbereitung:**
- Backup-Job mindestens 1x ausgeführt (Cron oder manuell).

**Schritte:**
1. `/health/` ohne Auth aufrufen.
2. Response-Code `200`.
3. `Content-Type: application/json`.
4. JSON-Struktur prüfen:
   - `status: "healthy"`
   - `db: "ok"`
   - `redis: "ok"` oder `redis_status: "connected"`
   - `clamav: "ok"` oder `clamav_status: "running"`
   - `last_backup_age_hours: <number>` (z.B. 6)
   - `version: "0.9.1"`
   - `timestamp: ISO-8601`
5. `SECURE_REDIRECT_EXEMPT` prüfen — Endpoint erreichbar ohne HTTPS-Redirect.

**Erwartetes Ergebnis:**
- Endpoint öffentlich, ohne Login.
- JSON für Monitoring-Tools (Uptime-Robot, Prometheus-Probe).
- Antwort < 500 ms.

**DSGVO/Security-Note:**
- **Bewusst öffentlich** für Monitoring (kein Auth-Wall).
- Antwort enthält keine personenbezogenen Daten, keine User-Counts.
- Verhindert Information-Leak: keine Stack-Traces, keine internen Hostnames.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-02 — ClamAV-Down → Health zeigt unhealthy

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |


**Vorbereitung:**
- ClamAV-Container stoppen: `sudo docker compose stop clamav`.

**Schritte:**
1. `/health/` aufrufen.
2. Response-Code: `503` (Service Unavailable) oder `200` mit `status: "degraded"`.
3. JSON-Body: `clamav: "unreachable"` oder `clamav_status: "down"`.
4. Andere Felder bleiben grün (DB, Redis).
5. ClamAV wieder starten: `sudo docker compose start clamav`.
6. Nach 30s Reload — wieder `healthy`.

**Erwartetes Ergebnis:**
- Health-Check erkennt ClamAV-Ausfall korrekt.
- Status-Code-Differenzierung (503 vs. 200) je nach Konfiguration.
- Recovery automatisch nach Container-Restart (kein App-Reload nötig).

**DSGVO/Security-Note:**
- **Virus-Scan ist DSGVO-relevant (Art. 32):** Kein Upload ohne aktiven Scanner.
- Bei ClamAV-Down: Datei-Uploads müssen blockiert werden (Fail-Secure).

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-03 — Robots.txt mit Disallow für sensible Pfade

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |


**Vorbereitung:**
- Keine.

**Schritte:**
1. `/robots.txt` aufrufen.
2. Response-Code 200, `Content-Type: text/plain`.
3. Inhalt prüfen — `Disallow:`-Einträge für:
   - `/admin/`
   - `/clients/`
   - `/cases/`
   - `/dsgvo/`
   - `/audit/`
   - `/api/`
   - `/sudo/`
   - `/login/` (optional)
4. `Allow:` ggf. für `/`, `/about/`, `/impressum/`, `/datenschutzerklaerung/`.
5. `Sitemap:`-Eintrag (falls vorhanden).

**Erwartetes Ergebnis:**
- Robots.txt deckt alle authentifizierten Bereiche per Disallow.
- Suchmaschinen-Indizierung sensibler Pfade verhindert.
- Statisch oder via View generiert.

**DSGVO/Security-Note:**
- **Suchmaschinen-Hygiene:** Auch wenn Auth-Wall greift, sollten Login-URLs nicht in Google-Index.
- Defense-in-Depth: ergänzt `noindex`-Header und Auth.
- Achtung: Robots.txt ist „freiwillige Empfehlung", kein Sicherheits-Mechanismus.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-04 — CSP-Report-Endpoint akzeptiert nur standardisierte Report-Medientypen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_csp_report.py` |

**Voraussetzung:** CSP-Header in Responses gesetzt mit `report-uri /csp-report/`.

**Vorbereitung:**
- `curl` zur Hand, oder Playwright mit `request.fetch()`.

**Schritte:**
1. POST `/csp-report/` mit `Content-Type: application/csp-report` und valider JSON-Payload (`{"csp-report": {"document-uri": "...", "violated-directive": "..."}}`) → Status 204.
2. POST mit `Content-Type: application/reports+json` und valider Reporting-API-Liste → Status 204.
3. POST mit `Content-Type: application/json` (falsch) → Status 400 oder 415.
4. POST ohne Body → 400.
5. Rate-Limit-Test: 11 POSTs in unter 1 Minute von gleicher IP → 11. Request liefert 429 (Too Many Requests).
6. Browser-Test: CSP-Verletzung absichtlich auslösen (Inline-Script auf Test-Seite) → automatischer POST von Browser → 204.
7. Logging-Verzeichnis prüfen: CSP-Reports in Logs (z.B. `/var/log/anlaufstelle/csp.log`).

**Erwartetes Ergebnis:**
- Endpoint akzeptiert nur `application/csp-report` und `application/reports+json`, nicht generisches JSON.
- Rate-Limit 10 Requests/min/IP greift.
- Reports werden geloggt, aber nicht in DB persistiert (DoS-Schutz).
- Kein PII in CSP-Reports (URLs sind öffentlich).

**DSGVO/Security-Note:**
- **CSP-Reports sind Sicherheitsfeature:** XSS-Versuche werden gemeldet.
- Rate-Limit verhindert DoS via Report-Flood.
- Keine personenbezogenen Daten in Reports → kein DSGVO-Konflikt.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-05 — Locale-Wechsel via URL-Parameter

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_i18n_locale.py` |

**Voraussetzung:** beide Locale-Dateien (de, en) kompiliert.

**Vorbereitung:**
- Locale-Files: `make compilemessages` oder Container-Restart.

**Schritte:**
1. `/?lang=de` aufrufen → Login-Seite auf Deutsch (z.B. „Anmelden", „Passwort").
2. `/?lang=en` aufrufen → Login-Seite auf Englisch („Sign in", „Password").
3. `Accept-Language: en-US`-Header senden ohne `?lang=` → automatisch Englisch.
4. `Accept-Language: de-DE` → Deutsch.
5. Locale-Cookie wird gesetzt (`django_language` oder ähnlich).
6. Eingeloggte User-Profile-Setting überschreibt Browser-Default.

**Erwartetes Ergebnis:**
- Locale-Wechsel funktioniert via URL, Cookie und User-Setting.
- Reihenfolge: User-Setting > Cookie > URL > Accept-Language > Default (de).
- Keine doppelten Strings in i18n (alle Templates verwenden `{% trans %}` oder `{% blocktrans %}`).

**DSGVO/Security-Note:**
- Locale-Cookie ist „functional" — kein Consent nötig (Art. 6 Abs. 1 lit. f).
- Keine PII in Locale-Cookie.

**Status:** ☐ Offen

---

### TC-ID: ENT-SYS-06 — 404/500-Error-Pages gestyled, keine Stack-Traces

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Sys | öffentlich | C/F/S | ⚪ | `test_security_hardening.py` |

**Voraussetzung:** mit `DEBUG=False` (Prod-ähnlich).

**Vorbereitung:**
- `make run-prod` oder `DJANGO_SETTINGS_MODULE=anlaufstelle.settings.prod`.

**Schritte:**
1. `/this-page-does-not-exist-12345/` aufrufen → 404-Seite.
2. Inhalt prüfen: gestyltes Template (Tailwind), Anlaufstelle-Logo, freundliche Fehlermeldung, „Zurück zur Startseite"-Link.
3. Quelltext: kein Stack-Trace, kein Hostname, keine Pfad-Leaks.
4. Künstlichen 500er erzeugen: `/test/raise-500/` (falls Test-Endpoint existiert) oder DB-Connection killen.
5. 500-Seite: ähnlich gestyled, „Internal Server Error", Hinweis auf Status-Page (falls vorhanden).
6. Audit-Log oder Sentry-Eintrag prüfen — Fehler intern erfasst.
7. Prod-Mode: `DEBUG = False` → keine Django-Yellow-Page.

**Erwartetes Ergebnis:**
- 404 + 500 sind eigene Templates (`templates/404.html`, `templates/500.html`).
- Keine Stack-Traces im Browser sichtbar.
- Sentry/Logging fängt Fehler intern ab.
- Konsistentes Design mit Rest der App.

**DSGVO/Security-Note:**
- **Information-Leak-Schutz:** Stack-Traces, DB-Queries, Settings dürfen nie in Browser-Output.
- Bei 500: nicht generisch („Es ist ein Fehler aufgetreten"), sondern mit Korrelations-ID für Support.
- Korrelations-ID enthält keine PII (UUID).

**Status:** ☐ Offen

</details>

---

### SETUP — Einrichtungs-/Konfigurationsassistent

> **Forward-looking Bereich (Refs #908).** Feature wird in #917 implementiert und ist aktuell **roadmap-blocked** (geplante Custom-Admin-UI + Config-Loader). Cases werden mit dem Feature ergänzt. Vorgesehenes Schema gemäß Audit §4.5:
>
> - `ENT-SETUP-01` — Neue Facility per Assistent vollständig anlegen (End-to-End ohne Shell)
> - `ENT-SETUP-02` — Dokumentationsbibliothek auswählen (Template-Übernahme und Anpassung)
> - `ENT-SETUP-03` — Riskante Einstellung wird gewarnt (Guardrails)
> - `ENT-SETUP-04` — Setup schreibt AuditLog (Nachvollziehbarkeit)

---

### COMP — Betriebs-/Compliance-Dashboard

> Feature umgesetzt in #919. Aggregiert 11 Checks (`/system/compliance/`) mit Status `ok`/`warning`/`critical`/`unknown`. Baut auf #902 (`check_db_roles`) auf. **Verwandtes LOKAL/SSH-Pendant:** Die `D.OPS`-Cases (#903) prüfen dieselben Zustände per `psql` / SSH; das Dashboard zeigt sie für Betreiber:innen ohne Server-Zugriff.

<details open>

### TC-ID: ENT-COMP-01 — DB-Rollencheck zeigt sichere App-Rolle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | super_admin | C || `src/tests/test_compliance_service.py::TestDbRoleChecks` |

**Code-Referenz:** [`src/core/services/compliance/db_roles.py`](https://github.com/anlaufstelle/app/blob/main/src/core/services/compliance/db_roles.py) `_db_role_checks`, ruft `check_db_roles()` aus #902.

**Voraussetzung:** Drei-Rollen-Modell aus #902 aktiv (`POSTGRES_BOOTSTRAP_PASSWORD` + `POSTGRES_ADMIN_USER` + `POSTGRES_ADMIN_PASSWORD` gesetzt).

**Schritte:**
1. Als `super_admin` einloggen.
2. `/system/compliance/` aufrufen.
3. Kategorie „Datenbank" prüfen: drei Checks sichtbar.
4. App-DB-Rolle NOSUPERUSER → Status `ok`, Detail `rolsuper=False`.
5. App-DB-Rolle kein BYPASSRLS → Status `ok`, Detail `rolbypassrls=False`.
6. Admin-DB-Rolle → Status `ok`, Detail `rolbypassrls=True`.

**Negativtest:**
1. Per `psql` als Bootstrap-Superuser: `ALTER ROLE anlaufstelle SUPERUSER;`
2. Dashboard neu laden → App-NOSUPERUSER wird `critical` mit Action-Hint „manage.py check_db_roles".
3. Zurück: `ALTER ROLE anlaufstelle NOSUPERUSER;`

**DSGVO/Security-Note:** Art. 32 Abs. 1 lit. b (Schutz vor unbefugter Verarbeitung) — RLS bleibt nur wirksam, wenn die App-Rolle keine Superuser-Privilegien hat.

**Status:** ☐ Offen

---

### TC-ID: ENT-COMP-02 — Backup veraltet erzeugt Warnung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | super_admin | C || `src/tests/test_compliance_service.py::TestBackupChecks` |

**Code-Referenz:** `_backup_checks` + `system_health.last_backup_info()`.

**Schritte:**
1. Frisches Backup vorhanden → `/system/compliance/` Kategorie „Backup": Status `ok`, Detail enthält Pfad + Alter (z.B. „6h").
2. Backup-Verzeichnis leeren (oder älter machen via `touch -t 202401010000 <file>`).
3. Dashboard neu laden:
   - 36-48h Alter → Status `warning`, Action-Hint „Backup-Cron pruefen" (Schwellen aus `core/services/ops_thresholds.py`, Refs #1872).
   - >48h → Status `critical`.
   - Kein Backup → im Compliance-Dashboard Status `unknown` mit Hinweis auf `settings.BACKUP_DIR`; in der Betriebsansicht `/system/` dagegen `warning` (Refs #1872).

**Status:** ☐ Offen

---

### TC-ID: ENT-COMP-03 — ClamAV down ist critical

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | super_admin | C || `src/tests/test_compliance_service.py::TestClamavChecks` |

**Code-Referenz:** `_clamav_checks` ruft `virus_scan.ping()` + `virus_scan.signature_info()`.

**Schritte:**
1. ClamAV-Container läuft → Kategorie „Virus-Scan": Erreichbarkeit `ok`, Signatur `ok` oder `warning` (je nach Alter).
2. Container stoppen: `docker compose stop clamav`.
3. Dashboard neu laden → Erreichbarkeit `critical` mit Action-Hint „docker compose ps clamav".
4. Container wieder starten: `docker compose start clamav`.
5. Sofort nach Start: Signatur-Status kann kurzzeitig `unknown` sein (Signatur-Daten werden noch geladen).
6. Nach ein paar Minuten: zurück auf `ok` / `warning`.

**Negativtest Signatur:**
- Wenn `freshclam` länger als 7 Tage nicht lief → Signatur-Check wird `warning`.

**Status:** ☐ Offen

---

### TC-ID: ENT-COMP-04 — Retention-Job überfällig ist warning/critical

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Compliance | super_admin | C || `src/tests/test_compliance_service.py::TestRetentionChecks` |

**Code-Referenz:** `_retention_checks` liest neuesten `RETENTION_RUN_COMPLETED`-AuditLog (Refs #919 C2).

**Voraussetzung:** `enforce_retention`-Cron muss in den letzten 7 Tagen einmal erfolgreich gelaufen sein.

**Schritte:**
1. `docker compose exec web python manage.py enforce_retention` einmal ausführen.
2. Dashboard neu laden → Kategorie „Retention": Status `ok`, Detail „Letzter Lauf vor 0 Tag(en)".
3. **Warning-Pfad:** AuditLog-Eintrag älter als 7 Tage machen (LOKAL/SSH per `bypass_replication_triggers`):
   ```python
   from django.utils import timezone
   from datetime import timedelta
   from core.models import AuditLog
   from core.services._db_admin import bypass_replication_triggers

   entry = AuditLog.objects.filter(action="retention_run_completed").latest("timestamp")
   with bypass_replication_triggers():
       AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(days=10))
   ```
4. Dashboard neu laden → Status `warning`.
5. Auf 30 Tage älter machen → Status `critical`.

**Status:** ☐ Offen

</details>

---

### PRIV — Datenschutz-Review (Freitext)

> **Forward-looking Bereich (Refs #908).** Feature wird in #918 implementiert. Cases werden mit dem Feature ergänzt. Vorgesehenes Schema:
>
> - `ENT-PRIV-01` — Freitext-Review listet riskante Inhalte (`Client.notes`, `Case.description`, `Episode.description`, `WorkItem.description`)
> - `ENT-PRIV-02` — Review respektiert Rollen-Sichtbarkeit (kein neuer Leak)
> - `ENT-PRIV-03` — Review-Aktion wird auditiert

---

### REPORT — Datenschutzfreundliche externe Berichte

> **Forward-looking Bereich (Refs #908).** Feature wird in #921 implementiert. Cases werden mit dem Feature ergänzt. Vorgesehenes Schema:
>
> - `ENT-REPORT-01` — Externer Bericht unterdrückt kleine Gruppen (K-Anonymity)
> - `ENT-REPORT-02` — Externer Bericht enthält keine Pseudonym-Rankings

---

### A11Y — Accessibility (WCAG-Stichproben)

> **Nachweis:** Der systematische Barrierefreiheits-Nachweis liegt in [`docs/accessibility-audit.md`](../accessibility-audit.md) (Refs #1059); automatisiertes Gate: `make test-a11y` (axe-core, Bestandsschutz-Baseline `src/tests/e2e/a11y_baseline.json`). Die A11Y-TCs `ENT-A11Y-01..12` bleiben hier **zusätzlich als Regressions-Stichproben** dokumentiert — die belegte automatisierte Abdeckung je Fall steht unter „Automatisiert durch". Bis der Screenreader-Handtest (Audit-Dokument § 3) abgenommen ist, laufen sie als manuelle Smoke-Tests vor Releases (siehe `release-test-profiles.md` Sektion KEEP-MANUAL).
>
> **Bereich (Refs #912).** Manuelle Stichproben, ergänzt um die belegte automatisierte Abdeckung — **kein** Ersatz für den vollständigen Audit-Nachweis. Ziel: Regressionen in häufig genutzten Flows erkennen, nicht Compliance-Zertifizierung.

<details open>

### TC-ID: ENT-A11Y-01 — Tastaturbedienung Hauptnavigation

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

**Automatisiert durch:** `src/tests/e2e/test_accessibility.py::TestSkipLink` (Skip-Link ist auf `/`, `/events/new/`, `/clients/` der erste Tab-Stopp und springt nach `#main-content`; Login/Passwort-Reset sind bewusst ausgenommen, siehe Erwartung), `src/tests/test_a11y_semantics.py::TestNavigationDisclosureState` (`Escape` schließt Sidebar-„Neu erstellen" und mobiles „Mehr"), `src/tests/test_a11y_semantics.py::TestOverflowMenuState` (`Escape` schließt das ⋮-Menü **und** bindet den Fokus zurück auf `x-ref="trigger"`).

**Schritte:**
1. Auf Dashboard einloggen.
2. Erste `Tab`-Betätigung: Skip-Link ist der erste Tab-Stopp (Ausnahme Login/Passwort-Reset, dort steht der Fokus laut R-B2-11 bewusst im ersten Feld — `Shift+Tab` erreicht dort den davorstehenden Skip-Link).
3. Ohne Maus, nur per `Tab` durch die Hauptnavigation tabben.
4. Pro Navigationspunkt: `Enter` aktiviert den Link.
5. `Shift+Tab` führt zurück.
6. Ein Aufklappmenü (Sidebar „Neu erstellen", mobiles „Mehr", ⋮-Menü einer Detailseite) mit `Enter` öffnen, mit `Escape` schließen.

**Erwartetes Ergebnis:**
- Jeder Navigationspunkt ist per Tab erreichbar.
- Tab-Reihenfolge entspricht der visuellen Reihenfolge (oben→unten, links→rechts).
- Keine Fokus-Falle: man kann auch wieder rauf-tabben.
- `Escape` schließt das offene Menü **und** der Fokus kehrt sichtbar auf die öffnende Schaltfläche zurück (kein Fokusverlust auf `<body>`).

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-02 — Sichtbarer Fokus auf interaktiven Elementen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F/S |||

**Automatisiert durch:** keine — die tatsächliche Sichtbarkeit eines Fokus-Rings auf einer bestimmten Hintergrundfarbe ist weder per axe noch im Quelltext-Audit prüfbar; laut `docs/accessibility-audit.md` § 4.2 (2.4.7) ist das Kriterium „im Quelltext korrekt angelegt, die Sichtbarkeit auf farbigen Flächen ist aber zu bestätigen" — bleibt reiner Handtest.

**Schritte:**
1. Per Tab durch ein Formular (z.B. Event anlegen).
2. Pro Element prüfen: sichtbarer Fokus-Ring vorhanden?
3. Auch bei Buttons, Links, Form-Feldern, Toggles.

**Erwartetes Ergebnis:**
- Jedes fokussierte Element zeigt einen erkennbaren Fokus-Indikator.
- Kontrast Fokus-Indikator zum Hintergrund ist deutlich (≥ 3:1).
- Kein `outline: none` ohne Ersatz-Indikator.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-03 — Fokus-Reihenfolge in Formularen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Automatisiert durch:** `src/tests/e2e/test_accessibility.py::TestAxeFormularzustaende::test_event_form_with_dynamic_fields` (axe-Lauf auf dem frisch per HTMX geswappten Feldblock, `page_key=event_create_dynamic_fields`), `src/tests/test_a11y_mechanical_templates.py::test_condition_editor_uses_slot_specific_partial_ids` und `::test_condition_board_create_and_edit_forms_have_unique_label_targets` (nachgeladene Felder behalten eindeutige, mit `<label for>` verknüpfte IDs — Voraussetzung für eine sinnvolle Tab-Reihenfolge nach dem Swap).

**Schritte:**
1. Event-Edit-Formular öffnen mit dynamisch generierten Feldern.
2. Per Tab durchgehen.
3. Reihenfolge: oben→unten, niemals Sprünge.
4. Auch HTMX-nachgeladene Felder müssen nach Sortierung tabbar sein.

**Erwartetes Ergebnis:**
- Tab-Reihenfolge folgt der visuellen Layout-Reihenfolge.
- Nach HTMX-Swap: neuer Inhalt ist in Tab-Reihenfolge integriert.
- Das Feld „Zeitpunkt" (`id_occurred_at`) trägt kein positives `tabindex` mehr (Anti-Pattern entfernt, #805–#812); es ist kein zusätzliches, vorgezogenes Tab-Ziel.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-04 — Modal/Dialog Rolle und Bedienbarkeit

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

> **Erwartung korrigiert (B3):** Der ursprüngliche Fall verlangte eine Fokusfalle. Für den einzigen echten Dialog der Anwendung (`components/_inline_create_modal.html`, Inline-Personenanlage im Kontaktformular) ist das laut `docs/accessibility-audit.md` § 4 (A-03) bewusst **nicht** umgesetzt: Rolle und Beschriftung sind korrekt, ein Fokus-Einschluss ist ARIA-Praxisempfehlung statt Erfolgskriterium, und die sichtbare „Abbrechen"-Schaltfläche hält die Tastaturbedienung offen. Geprüft wird deshalb Rolle/Name plus die tatsächlich vorhandene Bedienbarkeit, nicht mehr eine Fokusfalle.

**Automatisiert durch:** `src/tests/test_event_inline_client_create.py::TestClientInlineCreateView::test_staff_gets_minimal_modal_form` (`role="dialog"` gesetzt, `aria-modal="true"` bewusst **nicht** gesetzt).

**Schritte:**
1. Im Kontaktformular (`/events/new/`) „Neue Person anlegen" öffnen (`role="dialog"`, `aria-labelledby`).
2. Per Tab durch die Dialog-Felder und die Buttons „Person anlegen"/„Abbrechen".
3. Tab nach dem letzten Button: Fokus verlässt den Dialog in den Rest der Seite (kein Fokus-Einschluss — bewusste Ausnahme A-03).
4. „Abbrechen" per Tastatur (`Enter`) auslösen.

**Erwartetes Ergebnis:**
- Der Dialog trägt `role="dialog"` mit `aria-labelledby` auf die sichtbare Überschrift.
- Es gibt **keine** Fokusfalle — das ist keine Regression, sondern die dokumentierte Ausnahme A-03.
- „Abbrechen" ist jederzeit per Tastatur erreichbar und schließt den Dialog zuverlässig; damit bleibt die Tastaturbedienung trotz fehlendem Fokus-Einschluss gegeben.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-05 — Screen-Reader-Labels für Icon-Buttons

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Automatisiert durch:** `src/tests/e2e/test_accessibility.py` (axe-Regeln u. a. `button-name`/`aria-*` über alle 25 Seiten-Zustände der Seiten-Matrix, Bestandsschutz-Baseline), `src/tests/test_a11y_mechanical_templates.py::test_favorite_icon_has_text_alternative` (Favoriten-Stern mit sr-only-Text), `::test_shared_list_controls_have_specific_names` (Listen-Icon-Buttons mit spezifischem Namen), `src/tests/test_a11y_semantics.py::TestOverflowMenuState` (⋮-Icon-Button mit `aria-haspopup`/`aria-expanded`/`aria-controls`).

**Schritte:**
1. DevTools-Accessibility-Tree öffnen oder VoiceOver/NVDA aktivieren.
2. Icon-Buttons in Tabellen (Edit/Delete/Show) untersuchen.
3. Jedes Icon hat ein `aria-label`, `title` oder sichtbaren Text.

**Erwartetes Ergebnis:**
- Kein Icon-Button ohne semantisches Label.
- Screen-Reader liest sinnvolle Aktionen, nicht „Button-svg".

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-06 — Fehlermeldungen mit Formularfeldern verknüpft

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Automatisiert durch:** `src/tests/e2e/test_accessibility.py::TestAxeFormularzustaende::test_event_form_validation_errors` (axe-Lauf auf der server-gerenderten Fehlerdarstellung, `page_key=event_create_errors`), `src/tests/test_a11y_mechanical_templates.py::test_password_input_supports_error_relationships`, `::test_case_controls_have_accessible_names_and_errors`, `::test_error_free_password_pages_have_no_dangling_describedby`, `src/tests/test_a11y_semantics.py::TestAutocompleteErrorWiring` (optionales wie Pflicht-Personenfeld verknüpfen `aria-invalid`/`aria-describedby` korrekt mit dem Fehlertext).

**Schritte:**
1. Event-Form absenden mit leerem Pflichtfeld.
2. Validierungsfehler wird angezeigt.
3. DevTools: Fehler-Element ist per `aria-describedby` mit dem Feld verknüpft.
4. Feld selbst hat `aria-invalid="true"`.

**Erwartetes Ergebnis:**
- Screen-Reader liest beim Fokussieren des Felds die Fehlermeldung.
- Fehler-Text ist visuell beim Feld, nicht nur global im Toast.
- Der Fokus springt nach dem Neuladen mit Fehlern auf das erste fehlerhafte Feld statt auf den Anfang des nachgeladenen Bereichs.
- **Bekannte Ausnahme:** Auf genau dieser Seite (`event_create_errors`) meldet axe zusätzlich einen einzelnen, nicht verorteten `listitem`-Befund (serious) — offen dokumentiert in `docs/accessibility-audit.md` § 2.5, Folge-Ticket #2154. Das ist keine neue Regression, solange der Baseline-Eintrag unverändert bei einem Knoten steht.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-07 — Farbkontrast Badges/Alerts/Buttons

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C |||

**Automatisiert durch:** `src/tests/e2e/test_accessibility.py` (axe-Regel `color-contrast` über alle 25 Seiten-Zustände, Bestandsschutz-Baseline), `src/tests/test_a11y_mechanical_templates.py::test_colored_action_buttons_use_conforming_backgrounds`, `::test_event_and_handover_low_opacity_text_is_removed`, `::test_handover_highlight_timestamp_uses_conforming_text_token`, `::test_shared_border_token_meets_the_calculated_contrast_target`, `src/tests/test_a11y_semantics.py::TestTimeFilterTabs::test_active_tab_time_span_switches_to_the_readable_colour`.

**Schritte:**
1. DevTools-Contrast-Checker auf Badges (Status, Priorität).
2. Auf Alert-Toast-Hintergründe (success/warning/error).
3. Auf Primary-Buttons.

**Erwartetes Ergebnis:**
- Text/Hintergrund-Kontrast für normale Schriftgröße ≥ 4.5:1 (WCAG AA).
- Für große Schrift (≥ 18.66px bold) ≥ 3:1.
- Bewusst akzeptierte Restrisiken (nicht erneut als Befund aufmachen): gedämpfter Fließtext 4,57:1/5,07:1 knapp über der Schwelle (Audit-Dokument § 4, A-09) und `orange-600` in den Hub-Kacheln bei ≥ 20 px fett (A-10, 3,56:1 über der Großschrift-Schwelle 3:1).

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-08 — Reduced-Motion-Verhalten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C/F |||

**Automatisiert durch:** keine — `prefers-reduced-motion` ist nicht Teil des axe-Regelumfangs (`docs/accessibility-audit.md` § 1.2: `wcag2a`/`wcag2aa`/`wcag21a`/`wcag21aa`) und war nicht Gegenstand des Quelltext-Audits; bleibt reiner Handtest.

**Schritte:**
1. OS-Setting `prefers-reduced-motion: reduce` aktivieren (macOS: Bedienungshilfen, Windows: Vereinfachte Darstellung, DevTools: Rendering-Panel).
2. App-Animationen (Modal-Slide-In, Toast-Fade, HTMX-Transitions) auslösen.

**Erwartetes Ergebnis:**
- Animationen sind reduziert oder ausgeschaltet.
- Keine schnellen Flicker oder Parallax-Effekte.
- Funktionale Bewegung (Scroll-to-Element) bleibt erlaubt.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-09 — Mobile Zoom 200 % ohne Layoutbruch

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C | ✓ | `test_mobile_workflows.py` |

**Schritte:**
1. Mobile-Viewport (375×812, iPhone-SE-Size) in DevTools setzen.
2. Browser-Zoom auf 200 % (Strg/Cmd+Plus mehrmals).
3. Dashboard, Klient-Liste, Event-Form durchgehen.

**Erwartetes Ergebnis:**
- Kein horizontales Scrollen (Reflow funktioniert).
- Buttons und Links sind weiterhin tap-fähig (Touch-Target ≥ 44×44 px).
- Keine abgeschnittenen Texte.
- Ergänzend offen (nicht durch diesen Test abgedeckt): Text-Only-Zoom und erhöhter Textabstand (1.4.4/1.4.12) — Handtest M-17 in `docs/accessibility-audit.md` § 3.2.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-10 — Screenreader-Durchlauf Zeitstrom/Ereignis/Personen-Detail

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | NVDA/VoiceOver |||

**Automatisiert durch:** keine — Ansagen sind nur mit echtem Screenreader beurteilbar; die zugrundeliegende Semantik wird von `src/tests/test_a11y_semantics.py` (u. a. `TestHistoryEntryDisclosure`, `TestOverflowMenuState`, `TestTimeFilterTabs`) und `src/tests/e2e/test_accessibility.py` (axe je Seiten-Zustand) belegt.

**Voraussetzung:** NVDA (Windows) oder VoiceOver (macOS/iOS) aktiv; Rolle Fachkraft angemeldet.

**Schritte:**
1. Zeitstrom-Startseite (`/`) linear mit dem Screenreader durchlesen: Überschriftennavigation, dann die Zeitfilter-Tabs mit `Tab`/Pfeiltasten (M-02, M-09), Datumsnavigation antesten (M-11).
2. Ein Ereignis öffnen (`/events/<id>/`): Kopfbereich, Verlauf-Details-Umschalter öffnen/schließen (M-03), Statuspunkte in verlinkten Übersichten vorlesen lassen (M-07, sofern auf der Seite vorhanden).
3. Eine Personen-Detailseite öffnen (`/clients/<id>/`): ⋮-Menü „Weitere Aktionen" öffnen/schließen (M-03), Pagination einer Anhangsliste mit der Linkliste des Screenreaders prüfen (M-12).

**Erwartetes Ergebnis:**
- Überschriftenebenen springen nicht (h1→h2→h3), jede Seite hat genau eine `h1`.
- Zustandsbehaftete Bedienelemente (Zeitfilter, ⋮-Menü, Verlauf-Toggle) sagen ihren Zustand an, wie in § 2.2 des Audit-Dokuments für die jeweilige Korrektur beschrieben (A11Y-008/012/015/017/030/040).
- Pagination-Links werden als „Vorherige Seite"/„Nächste Seite" angesagt, nicht als Glyphen (A11Y-016).
- Kein Inhalt, der nur über Farbe unterscheidbar wäre, bleibt beim linearen Vorlesen unverständlich.

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-11 — Live-Region-Ansage nach HTMX-Swap

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | NVDA/VoiceOver + C |||

**Automatisiert durch:** `src/tests/test_a11y_semantics.py::TestListResultAnnouncement` (Live-Region liegt außerhalb des Swap-Ziels, trägt `role="status"`/`aria-live="polite"`, `data-hx-no-focus` verhindert die Doppelansage durch `focus-management.js`, die HTMX-Partial-Antwort liefert die Region **nicht** doppelt mit), `tests/js/focus-management.test.mjs` (Abschnitt „Trefferansage (A11Y-035/036)": Ansage feuert trotz `data-hx-no-focus`, auch während laufender Live-Suche, genau einmal auch bei gleicher Trefferzahl).

**Voraussetzung:** NVDA (Windows) oder VoiceOver (macOS/iOS) aktiv; Rolle Fachkraft angemeldet.

**Schritte:**
1. Personenliste (`/clients/`) öffnen, Suchbegriff tippen, Fokus **nicht** bewegen, 2 Sekunden warten.
2. Dasselbe auf der Fallliste (`/cases/`) mit einem Filter statt Freitext.
3. Einen Suchbegriff eintippen, der zu keinem Treffer führt.

**Erwartetes Ergebnis:**
- Der Screenreader sagt nach dem HTMX-Swap genau einmal „N Personen angezeigt."/„N Fälle angezeigt." an (Singular „1 Person angezeigt."/„1 Fall angezeigt."), ohne dass der Fokus das Suchfeld verlässt.
- Bei keinem Treffer wird „Keine Person gefunden."/„Kein Fall gefunden." angesagt.
- Keine Doppelansage (weder durch den Fokus-Manager noch durch eine im Partial mitgelieferte zweite Live-Region).

**Status:** ☐ Offen

---

### TC-ID: ENT-A11Y-12 — Workitem-Autocomplete-Tastaturpfad

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Accessibility | fachkraft | C + NVDA/VoiceOver |||

**Automatisiert durch:** `src/tests/test_a11y_semantics.py::TestWorkitemClientAutocomplete` (Refs #1989; `role="combobox"`, `aria-autocomplete="list"`, `aria-controls`, die vier Tastatur-Handler Pfeil-runter/-hoch/Enter/Escape, `role="listbox"`/`role="option"` mit `aria-selected`, optionales Feld ohne `aria-required`), `::TestAutocompleteErrorWiring` (Fehlerverknüpfung bei ungültigem, optionalem Personenfeld).

**Schritte:**
1. `/workitems/new/` als Fachkraft öffnen, im Feld „Person" zwei Zeichen eines vorhandenen Pseudonyms tippen.
2. Nur mit der Tastatur weiterarbeiten: Pfeil-runter/-hoch wandert durch die Vorschläge, `Enter` übernimmt den hervorgehobenen Treffer, `Escape` schließt die Liste.
3. Aufgabe ohne Personenauswahl speichern (Feld ist optional) — Gegenprobe.
4. Eine bestehende Aufgabe **mit** fest zugeordneter Person bearbeiten (`/workitems/<id>/edit/`) — Gegenprobe auf den gepinnten Zweig ohne Autocomplete.

**Erwartetes Ergebnis:**
- Screenreader sagt Rolle „Kombinationsfeld", den Erweitert-Zustand und die hervorgehobene Option an.
- `Enter` übernimmt den Treffer ins verborgene Feld, `Escape` schließt die Liste ohne Auswahl.
- Das Feld bleibt erkennbar optional (kein `*`, kein `aria-required`); ein ungültiger Wert erzeugt eine mit `aria-describedby` verknüpfte Fehlermeldung.
- Der gepinnte Bearbeiten-Zweig zeigt weiterhin die Person als festen Text ohne Suchfeld.

**Status:** ☐ Offen

</details>

---

<details open>
<summary><strong>🌐 I18N — Sprachen (DE/EN) (5 Cases)</strong></summary>

**Routen:** alle (Sprachdimension) · `/i18n/setlang/` 
**Views/Middleware:** `src/core/middleware/user_language.py` (`UserLanguageMiddleware`), `src/core/views/auth.py` (`set_user_language`) 
**Kataloge:** `src/locale/de/LC_MESSAGES/django.po`, `src/locale/en/LC_MESSAGES/django.po` 
**E2E-Coverage:** `test_i18n_locale.py`, `test_middleware_user_language.py` 
**Zweck:** systematische Sprach-Cross-Coverage über alle Bereiche — ergänzt das bestehende [ENT-ACCT-02](#tc-id-ent-acct-02--sprache-wechseln-deen-session-cookie--user-pref) (Switch-Mechanik) um Katalog-Vollständigkeit, Persistenz und Lokalisierung. Refs #973.

### TC-ID: ENT-I18N-01 — DE→EN-Umschaltung persistiert über Navigation & Re-Login

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| I18N | fachkraft (`miriam`) | C/F/S | ⚪ | `test_i18n_locale.py` |

**Voraussetzung:** eingeloggt, Default-Sprache `de`.

**Schritte:**
1. Header-Sprachumschalter `EN` klicken.
2. Über mehrere Bereiche navigieren (Zeitstrom → Personen → Fälle → Aufgaben).
3. Logout, Re-Login, erneut Zeitstrom öffnen.

**Erwartetes Ergebnis:**
- Nach Schritt 1: UI-Titel/Navigation auf Englisch („Timeline", „People", „Cases", „Tasks").
- Schritt 2: Sprache bleibt EN über alle Navigationen (kein Rückfall auf DE).
- Schritt 3: EN bleibt nach Re-Login (Persistenz via `User.preferred_language`).

**Status:** ☐ Offen

---

### TC-ID: ENT-I18N-02 — EN-Katalog-Vollständigkeit: keine deutschen Fallback-Strings in EN-UI

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| I18N | fachkraft (`miriam`) | C |||

**Voraussetzung:** Sprache auf EN, alle Bereiche durchklicken.

**Schritte:**
1. Mit aktiver EN-Sprache Navigation + Hauptseiten durchgehen.
2. Auf deutsche UI-Strings achten (keine DB-Daten — die bleiben sprachneutral).

**Erwartetes Ergebnis:**
- Alle **UI-Template-Strings** erscheinen auf Englisch.
- **Bekannter Fehler:** Nav „Arbeitszentrale" und Button „Neue Person" bleiben deutsch; EN-Katalog hat 121 fuzzy + 122 leere Einträge → #974. Bis behoben: `❌ Fail #974` im Run-Log.

**Status:** ☐ Offen

---

### TC-ID: ENT-I18N-03 — Anonyme Nutzer:innen erhalten DE-Default (Accept-Language ignoriert)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| I18N | anonym | C || `test_middleware_user_language.py` |

**Schritte:**
1. Ausgeloggt `/login/` mit `Accept-Language: en` aufrufen.

**Erwartetes Ergebnis:**
- Login-Seite auf Deutsch (Default `de`); `UserLanguageMiddleware` ignoriert den Browser-Header bewusst (Refs #670).

**Status:** ☐ Offen

---

### TC-ID: ENT-I18N-04 — Sprachwechsel mitten im Workflow (offenes Formular)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| I18N | fachkraft (`miriam`) | C/F |||

**Schritte:**
1. Event-/Personen-Formular öffnen, Felder teilweise füllen.
2. Sprache umschalten (EN↔DE).
3. Formular erneut betrachten.

**Erwartetes Ergebnis:**
- Labels wechseln die Sprache; eingegebene Werte gehen nicht verloren bzw. Verhalten ist dokumentiert (Anhang B, Lücke #6).

**Status:** ☐ Offen

---

### TC-ID: ENT-I18N-05 — Datums-/Zahlenlokalisierung DE vs. EN

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| I18N | fachkraft (`miriam`) | C |||

**Schritte:**
1. Personen-Liste in DE betrachten (Datum „25. Mai 2026").
2. Auf EN umschalten (Datum „May 25, 2026").

**Erwartetes Ergebnis:**
- Datums-/Zeitformat folgt dem aktiven Locale (`L10N`); keine gemischten Formate.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>🧩 CONFIG — Formular-Konfiguration (25 Cases)</strong></summary>

**Routen:** `/config/document-types/`, `/config/document-types/new/`, `/config/document-types/<uuid>/`, `/config/document-types/<uuid>/edit/`, `/config/document-types/<uuid>/delete/`, `/config/document-types/<uuid>/fields/add/`, `/config/document-types/<uuid>/fields/<uuid>/remove/`, `/config/document-types/<uuid>/groups/new/`, `/config/document-types/<uuid>/groups/<uuid>/edit/`, `/config/document-types/<uuid>/groups/<uuid>/delete/`, `/config/document-types/<uuid>/reorder/groups/`, `/config/document-types/<uuid>/reorder/fields/`, `/config/document-types/<uuid>/reorder/move-field/`, `/config/field-templates/`, `/config/field-templates/new/`, `/config/field-templates/<uuid>/edit/`, `/config/field-templates/<uuid>/delete/`, `/config/field-templates/<uuid>/toggle-active/` 
**Views:** `src/core/views/config/document_types.py` (`ConfigDocumentType{List,Create,Detail,Update,Delete}View`, `ConfigDocTypeField{Add,Remove}View`), `src/core/views/config/field_templates.py` (`ConfigFieldTemplate{List,Create,Update,Delete,Toggle}View`), `src/core/views/config/field_groups.py` (`ConfigFieldGroup{Create,Update,Delete}View`), `src/core/views/config/reorder.py` (`ConfigReorderGroupsView`, `ConfigReorderFieldsView`, `ConfigMoveFieldView`) — alle `FacilityAdminRequiredMixin` 
**Services:** `src/core/services/config.py` (`save_document_type`, `delete_document_type`, `save_field_template`, `field_template_delete_blockers` + `field_template_trashed_client_count` (Papierkorb-Anteil), `delete_field_template`, `set_field_template_active`, `save_field_group`, `delete_field_group`, `add_field_to_doctype`, `remove_field_from_doctype`, `reorder_groups`, `reorder_fields`, `move_field`, Exception `ConfigDeleteBlocked`) 
**Models:** `DocumentType` (`system_type`, `report_key`), `FieldTemplate` (`slug`, `report_key`, `is_active`), `FieldGroup` (transitiv facility-gescoped über `document_type.facility`), `DocumentTypeField` (`sort_order`, `field_group`) 
**E2E-Coverage:** `test_navigation_config.py`, `test_config_document_types.py`, `test_config_field_templates.py`, `test_config_field_groups.py`, `test_config_reorder.py`, `test_config_service.py`, `test_event_form_groups.py`, `test_seed.py`; Rollen-/IDOR-Vollmatrix generisch über `test_authz_idor.py`; RLS-Transitivität über `test_rls_functional.py` 
**Spezial-Setup:**
- Rolle **facility_admin** (`admin`) ist Voraussetzung für alle CRUD-Cases — das Rollen-Gate selbst wird negativ mit `emma` (lead), `miriam` (staff), `lena` (assistant) und `superadmin` getestet.
- Für die Cross-Facility-/RLS-Cases zusätzlich `admin_2` (Facility 2) — `make seed FACILITIES=2` bzw. `--scale medium`.
- Der Feld-Board-Bereich (`/config/document-types/<uuid>/`) braucht mindestens einen DocumentType mit zugeordneten Feldern — der Seed-Typ „Kontakt" eignet sich direkt (Gruppen „Basisdaten"/„Leistungen & Notizen" + ungruppiertes „Straßenkontakt").

---

### TC-ID: ENT-CONFIG-01 — Reiter „Dokumentationstypen" nur für facility_admin sichtbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin (facility_admin) | C/F | ⚪ | `test_navigation_config.py` |

**Vorbereitung:**
- Mit `admin` einloggen.
- Hinweis: Seit der Navigations-Neuordnung (#1801) ist der Einstieg kein Sidebar-Eintrag „Konfiguration" mehr, sondern der Reiter „Dokumentationstypen" im Bereich „Verwaltung"; die Adresse `/config/document-types/` ist unverändert.

**Schritte:**
1. `/verwaltung/` aufrufen (bzw. „Verwaltung" in der Desktop-Sidebar anklicken), Reiterzeile auf „Dokumentationstypen" prüfen.
2. Viewport auf Mobile (iPhone 15) umschalten, mobiles „Mehr"-Menü öffnen.
3. Nacheinander mit `emma` (lead), `miriam` (staff), `lena` (assistant) und `superadmin` einloggen, jeweils Sidebar/Mehr-Menü prüfen.

**Erwartetes Ergebnis:**
- `admin` sieht in der Sidebar „Verwaltung" und dort den Reiter „Dokumentationstypen" (Link auf `/config/document-types/`); im mobilen Menü erscheint derselbe Einstieg.
- Keine der anderen vier Rollen sieht den Eintrag — weder Desktop noch Mobile; `emma` (lead) sieht zwar „Datenschutz", dort aber keinen Verwaltungs-Reiter.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-02 — Direktzugriff `/config/document-types/` ohne facility_admin → 403

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | lead/staff/assistant/super_admin | C || `test_config_document_types.py`, `test_authz_idor.py` |

**Vorbereitung:**
- Mit `emma` (lead) eingeloggt.

**Schritte:**
1. URL `/config/document-types/` direkt in die Adresszeile eingeben (nicht über Navigation).
2. Wiederholen mit `miriam` (staff), `lena` (assistant), `superadmin`.

**Erwartetes Ergebnis:**
- Alle vier Rollen erhalten HTTP 403 (Forbidden-Seite) — kein Redirect zum Login (bereits authentifiziert), keine Konfigurationsdaten sichtbar.
- `admin` (facility_admin) erhält auf derselben URL HTTP 200 (Kontrollprobe).

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-03 — DocumentType anlegen (system_type wählbar)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_document_types.py` |

**Vorbereitung:**
- Mit `admin` eingeloggt, `/config/document-types/` geöffnet.

**Schritte:**
1. „Neuer Dokumenttyp" öffnen.
2. Name „Manuell-TC03", Sensitivität und einen `system_type` („Hausverbot" oder „Krisengespräch" — seit #1850 die einzigen beiden Werte) wählen, absenden.
3. Detail-Seite des neu angelegten Typs prüfen.

**Erwartetes Ergebnis:**
- Neuer DocumentType erscheint in der Liste, mit dem gewählten `system_type`.
- Die Auswahl des `system_type` belegt den `Berichtsschlüssel` im Formular vor („Krisengespräch" → `crisis`, „Hausverbot" → „Gehört ausdrücklich in keinen Bericht") — der Vorschlag bleibt vor dem Absenden änderbar.
- Nach dem Anlegen Redirect auf die Detail-Seite (`/config/document-types/<uuid>/`).
- AuditLog erhält einen `config_create`-Eintrag mit `target_id` des neuen Typs.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-04 — DocumentType bearbeiten: system_type read-only, report_key nur nachtragbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_document_types.py` |

**Vorbereitung:**
- DocumentType mit gesetztem `system_type` **und** gesetztem `report_key` existiert (z.B. aus ENT-CONFIG-03).
- Zusätzlich ein Typ **ohne** `report_key` (z.B. ein einrichtungseigener Typ ohne Berichtszuordnung) für den Nachtrag-Pfad.

**Schritte:**
1. Bearbeiten-Formular des Typs aus ENT-CONFIG-03 öffnen.
2. Prüfen, dass `system_type` im Formular nicht editierbar ist (deaktiviert/nur Anzeige).
3. Prüfen, dass der bereits gesetzte `Berichtsschlüssel` ebenfalls gesperrt ist.
4. Sensitivität auf einen anderen Wert ändern, speichern.
5. Bearbeiten-Formular des Typs ohne `report_key` öffnen, einen `Berichtsschlüssel` wählen, speichern.

**Erwartetes Ergebnis:**
- Die geänderte Sensitivität wird übernommen.
- `system_type` bleibt unverändert — auch wenn versucht wird, ihn per manipuliertem POST-Body zu ändern (server-seitig immutable).
- Der gesetzte `report_key` bleibt ebenfalls unverändert (Änderbarkeits-Regel: leer→gesetzt erlaubt, gesetzt→anders und gesetzt→leer verboten, #1849); der leere lässt sich nachtragen und ist danach gesperrt.
- `config_update`-Audit-Einträge mit `changed_fields`: einmal `sensitivity`, einmal `report_key` — nie `system_type`.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-05 — DocumentType löschen mit Referenz-Blocker (Events vorhanden)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_document_types.py` |

**Vorbereitung:**
- DocumentType mit mindestens einem existierenden Event (z.B. Seed-Typ „Kontakt").

**Schritte:**
1. Löschen-Bestätigungsseite öffnen (`/config/document-types/<uuid>/delete/`).
2. Blocker-Hinweis lesen (Anzahl referenzierender Events).
3. Löschen trotzdem bestätigen (POST).

**Erwartetes Ergebnis:**
- GET zeigt einen Blocker-Hinweis mit Event-Anzahl, nicht nur „geht nicht".
- POST wird abgelehnt: Redirect zurück zur Detail-Seite, Toast/Message mit Hinweis auf referenzierende Ereignisse, DocumentType bleibt bestehen.
- Kein `config_delete`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-06 — DocumentType löschen ohne Referenzen (Hard-Delete)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_document_types.py` |

**Vorbereitung:**
- Frisch angelegter DocumentType ohne Events (z.B. Rest von ENT-CONFIG-03, falls noch nicht gelöscht — sonst neu anlegen).

**Schritte:**
1. Löschen-Bestätigung öffnen — kein Blocker sichtbar.
2. Löschen bestätigen.

**Erwartetes Ergebnis:**
- DocumentType ist danach weder in der Liste noch abrufbar (404 auf die alte Detail-URL).
- Redirect auf die Liste `/config/document-types/`.
- `config_delete`-Audit-Eintrag vorhanden.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-07 — FieldTemplate anlegen mit Slug-Autogenerierung

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_templates.py` |

**Vorbereitung:**
- Mit `admin` eingeloggt, `/config/field-templates/` geöffnet.

**Schritte:**
1. „Neues Feld" mit Name „Mein Test Feld", Typ „Text" anlegen (kein Slug-Eingabefeld im Formular).
2. Liste/Detail prüfen.

**Erwartetes Ergebnis:**
- Slug wird automatisch aus dem Namen generiert (`mein-test-feld`).
- `config_create`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-08 — FieldTemplate bearbeiten: Slug bleibt fix

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_templates.py` |

**Vorbereitung:**
- FieldTemplate aus ENT-CONFIG-07 (Slug `mein-test-feld`) existiert.

**Schritte:**
1. Bearbeiten öffnen, Namen auf „Ganz anderer Name" ändern, speichern.

**Erwartetes Ergebnis:**
- Name wird übernommen, Slug bleibt exakt `mein-test-feld` — obwohl der neue Name einen anderen Slug ergeben würde.
- Kein Slug-Eingabefeld im Bearbeiten-Formular sichtbar/editierbar.
- `config_update`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-09 — FieldTemplate-Löschschutz mit Blocker-Meldung + „Stattdessen deaktivieren"

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_templates.py` |

**Vorbereitung:**
- FieldTemplate, das einem DocumentType zugeordnet ist UND/ODER in einem Event-Anhang verwendet wird UND/ODER im `data_json` eines Events vorkommt (z.B. Seed-Feld „Dauer" oder „Notiz" von „Kontakt").

**Schritte:**
1. Löschen-Bestätigung für das zugeordnete Feld öffnen.
2. Blocker-Meldung(en) lesen.
3. Auf „Stattdessen deaktivieren" klicken.
4. Erneut versuchen, das (jetzt inaktive) Feld zu löschen.

**Erwartetes Ergebnis:**
- GET zeigt für jede zutreffende Referenz-Art (Typ-Zuordnung, Anhang, Event-Daten) eine eigene Blocker-Zeile mit Klartext-Grund und dem Hinweis „Stattdessen deaktivieren".
- „Stattdessen deaktivieren" setzt `is_active=False` (Toggle), FieldTemplate bleibt bestehen, `config_update`-Audit mit `changed_fields=["is_active"]`.
- Der erneute Löschversuch bleibt blockiert, obwohl das Feld inaktiv ist — kein `config_delete`.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-10 — FieldTemplate Toggle aktiv/inaktiv

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_templates.py` |

**Vorbereitung:**
- Unreferenziertes, aktives FieldTemplate.

**Schritte:**
1. In der Liste auf „Deaktivieren" klicken.
2. Listenfilter nach „inaktiv" prüfen.
3. Erneut auf „Aktivieren" klicken.

**Erwartetes Ergebnis:**
- Status wechselt sichtbar zwischen aktiv/inaktiv, Listenfilter `is_active` greift korrekt.
- Jeder Toggle erzeugt einen eigenen `config_update`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-11 — Feld im Feld-Board einer Gruppe zuordnen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F/S | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- DocumentType-Detail mit mindestens einer Gruppe (z.B. „Kontakt"/„Basisdaten"), freies (noch nicht zugeordnetes) FieldTemplate.

**Schritte:**
1. Detail-Seite `/config/document-types/<uuid>/` öffnen.
2. Im „Feld hinzufügen"-Formular ein Feld + Ziel-Gruppe auswählen, absenden.

**Erwartetes Ergebnis:**
- Feld erscheint im Board unter der gewählten Gruppe, ans Ende einsortiert.
- Board wird per HTMX-Partial aktualisiert (kein Full-Page-Reload).
- Erneuter Versuch, dasselbe Feld nochmal zuzuordnen, ergibt einen Formfehler im Board-Partial statt 500.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-12 — Feld im Feld-Board ohne Gruppe zuordnen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- DocumentType-Detail, freies FieldTemplate.

**Schritte:**
1. „Feld hinzufügen" ohne Gruppen-Auswahl absenden.

**Erwartetes Ergebnis:**
- Feld erscheint in der Sektion „Ohne Gruppe", ans Ende einsortiert.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-13 — Feld aus DocumentType entfernen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- Ein zugeordnetes Feld im Board.

**Schritte:**
1. Im Board bei einem Feld „Entfernen" klicken.

**Erwartetes Ergebnis:**
- Feld verschwindet aus dem Board (Board-Partial); das FieldTemplate selbst bleibt in `/config/field-templates/` bestehen — nur die Zuordnung wird gelöscht.
- `config_delete`-Audit-Eintrag für die Zuordnung (nicht für das FieldTemplate).

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-14 — Feldgruppe anlegen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- DocumentType-Detail geöffnet.

**Schritte:**
1. „Gruppe hinzufügen", Name „Manuell-Gruppe" eingeben (optional „einklappbar" aktivieren), absenden.
2. Denselben Namen ein zweites Mal versuchen.

**Erwartetes Ergebnis:**
- Neue Gruppe erscheint leer im Board.
- Der doppelte Gruppenname (innerhalb desselben DocumentType) ergibt einen Formfehler statt 500.
- `config_create`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-15 — Feldgruppe umbenennen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- Bestehende Gruppe (z.B. aus ENT-CONFIG-14).

**Schritte:**
1. Gruppe bearbeiten, Namen ändern, speichern.

**Erwartetes Ergebnis:**
- Neuer Name im Board sichtbar, interner Slug bleibt stabil (keine Brüche in Zuordnungen).
- `config_update`-Audit-Eintrag.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-16 — Feldgruppe löschen — Felder fallen in „Ohne Gruppe"

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_field_groups.py` |

**Vorbereitung:**
- Gruppe mit mindestens einem zugeordneten Feld.

**Schritte:**
1. Gruppe löschen.

**Erwartetes Ergebnis:**
- Gruppe verschwindet aus dem Board.
- Zuvor zugeordnete Felder werden NICHT gelöscht, sondern erscheinen danach unter „Ohne Gruppe".
- `config_delete`-Audit-Eintrag für die Gruppe.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-17 — Drag-and-Drop: Gruppen-Reihenfolge ändern

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_reorder.py` |

**Vorbereitung:**
- DocumentType mit ≥2 Gruppen (Seed-Typ „Kontakt": „Basisdaten" + „Leistungen & Notizen").

**Schritte:**
1. Zweite Gruppe per Drag-Handle (⠿) vor die erste ziehen.

**Erwartetes Ergebnis:**
- Reihenfolge im Board wechselt sofort (Board-Partial).
- `config_reorder`-Audit-Eintrag mit `detail.operation="group_order"`.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-18 — Drag-and-Drop: Feld-Reihenfolge innerhalb einer Gruppe

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_reorder.py` |

**Vorbereitung:**
- Gruppe mit ≥2 Feldern (z.B. „Leistungen & Notizen" von „Kontakt").

**Schritte:**
1. Zweites Feld innerhalb derselben Gruppe per Drag-Handle vor das erste ziehen.

**Erwartetes Ergebnis:**
- Reihenfolge innerhalb der Gruppe wechselt, Feld bleibt in derselben Gruppe.
- `config_reorder`-Audit-Eintrag mit `detail.operation="field_order"`.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-19 — Drag-and-Drop: Feld zwischen Gruppen/nach „Ohne Gruppe" verschieben

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_reorder.py` |

**Vorbereitung:**
- Mindestens zwei Gruppen bzw. eine Gruppe + „Ohne Gruppe"-Sektion mit Feldern.

**Schritte:**
1. Feld aus Gruppe A per Drag in Gruppe B ziehen.
2. Ein weiteres Feld aus einer Gruppe in die Sektion „Ohne Gruppe" ziehen.

**Erwartetes Ergebnis:**
- Feld gehört danach zur Ziel-Gruppe (bzw. keiner Gruppe mehr), Position im Ziel korrekt einsortiert.
- `config_reorder`-Audit-Eintrag mit `detail.operation="move_field"`.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-20 — Drag-and-Drop-Reihenfolge bleibt nach Reload erhalten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration | admin | C/F | ⚪ | `test_config_reorder.py` |

**Vorbereitung:**
- ENT-CONFIG-17 bis ENT-CONFIG-19 durchgeführt (oder eine Drag-Operation frisch reproduziert).

**Schritte:**
1. Nach einer Drag-and-Drop-Operation die Seite per Browser-Reload (F5) neu laden.

**Erwartetes Ergebnis:**
- Reihenfolge/Gruppenzugehörigkeit entspricht weiterhin dem Stand nach dem Drag — serverseitig persistiert, nicht nur clientseitiges DOM-Artefakt.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-21 — Config-Änderungen erzeugen CONFIG_*-AuditLog-Einträge

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Audit | admin | C || `test_config_service.py`, `test_config_document_types.py` |

**Vorbereitung:**
- Mit `admin` eingeloggt, Zugriff auf `/audit/`.

**Schritte:**
1. Nacheinander: DocumentType anlegen, bearbeiten, löschen; FieldTemplate anlegen, Toggle; Gruppe anlegen; eine Reorder-Operation ausführen.
2. `/audit/` aufrufen, nach `action` filtern.

**Erwartetes Ergebnis:**
- Für jede Aktion ein passender Eintrag — `config_create`, `config_update`, `config_delete`, `config_reorder` — mit korrektem `target_id`, `user=admin`, gesetzter `facility`.
- Blockierte Löschversuche (siehe ENT-CONFIG-05/ENT-CONFIG-09) erzeugen KEINEN `config_delete`-Eintrag.

**DSGVO/Security-Note:**
- Konfigurationsänderungen an Formularen/Feldern sind selbst auditpflichtig (Art. 32 TOMs) — Verwaltungsentscheidungen über Datenerhebung müssen nachvollziehbar sein.

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-22 — Cross-Facility-Verbot: fremde Facility sieht/ändert Config-Objekte nicht (404)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / RLS | admin (F1) + admin_2 (F2) | C || `test_authz_idor.py`, `test_rls_functional.py` |

**Voraussetzung:** `make seed FACILITIES=2` (zwei Facilities mit je eigenem admin).

**Vorbereitung:**
- Browser-Profil 1: `admin` (Facility 1), legt DocumentType „F1-Only" samt FieldTemplate und FieldGroup an, notiert die UUIDs.
- Browser-Profil 2 (Inkognito): `admin_2` (Facility 2).

**Schritte:**
1. Mit `admin_2` die UUIDs der F1-Objekte direkt aufrufen: `/config/document-types/<uuid>/`, `.../edit/`, `.../delete/`.
2. Gleiches für die FieldTemplate- und FieldGroup-Detail-/Edit-/Delete-Routen sowie die drei Reorder-Endpunkte.
3. In der eigenen Liste `/config/document-types/` von `admin_2` prüfen, dass „F1-Only" nicht auftaucht.

**Erwartetes Ergebnis:**
- Alle Zugriffe auf fremde Objekt-IDs liefern **404** (nicht 403 — kein Existenz-Leak).
- Liste von `admin_2` zeigt ausschließlich Facility-2-Objekte.
- Kein POST verändert das fremde Objekt (in DB unverändert).

**DSGVO/Security-Note:**
- RLS greift transitiv auch für `FieldGroup` (kein direkter `facility`-FK, sondern über `document_type.facility`, Migration 0111).

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-23 — Event-Formular zeigt einklappbare Feldgruppe „Leistungen & Notizen"

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Events | fachkraft (`miriam`) | C/F | ✓ | `test_event_form_groups.py`, `test_seed.py` |

**Vorbereitung:**
- Standard-Seed vorhanden (Typ „Kontakt" mit Gruppen „Basisdaten"/„Leistungen & Notizen").
- Mit `miriam` eingeloggt.

**Schritte:**
1. Neues Event mit Dokumenttyp „Kontakt" anlegen.
2. Gruppen-Header „Basisdaten" und „Leistungen & Notizen" im Formular betrachten.
3. Auf den Header „Leistungen & Notizen" klicken (Ein-/Ausklappen).

**Erwartetes Ergebnis:**
- Beide Gruppen erscheinen mit eigenem Header; „Basisdaten" ist nicht einklappbar, „Leistungen & Notizen" ist einklappbar und reagiert auf den Klick.
- Felder „Leistungen", „Notiz", „Anhang" liegen unter „Leistungen & Notizen"; „Dauer"/„Altersgruppen-Cluster" unter „Basisdaten".

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-24 — Event-Formular: „Straßenkontakt" erscheint unter „Ohne Gruppe"

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Events | fachkraft (`miriam`) | C/F | ✓ | `test_event_form_groups.py`, `test_seed.py` |

**Vorbereitung:**
- Wie ENT-CONFIG-23.

**Schritte:**
1. Im selben Event-Formular für „Kontakt" zur Sektion „Ohne Gruppe" scrollen.

**Erwartetes Ergebnis:**
- Sektion „Ohne Gruppe" ist sichtbar und enthält das Feld „Straßenkontakt" — bewusst ungruppiert demonstriert (Refs #1056).

**Status:** ☐ Offen

---

### TC-ID: ENT-CONFIG-25 — Lösch-Blocker weist Papierkorb-Anteil aus und nennt den Ausweg

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Retention | admin (facility_admin) | C/F | ⚪ | `test_client_deletion_status.py::TestConfigBlockerNamesTheWayOut` |

**Code-Referenz:** `delete_document_type` (`_append_trash_share`) / `field_template_delete_blockers` + `field_template_trashed_client_count` in [src/core/services/config.py](https://github.com/anlaufstelle/app/blob/main/src/core/services/config.py), Partial [`config/partials/trash_blocker_hint.html`](https://github.com/anlaufstelle/app/blob/main/src/templates/core/config/partials/trash_blocker_hint.html) (Refs #1737)

**Voraussetzung:** Ein Personen-Dokumentationstyp (`entity_type=client`), der **ausschließlich** von einer Person benutzt wird, die inzwischen im **Papierkorb** liegt (Löschung im Vier-Augen-Prinzip bestätigt).

**Schritte:**
1. Als `admin` `/config/document-types/<uuid>/delete/` des Typs aufrufen.
2. Blocker-Begründung und Hinweisbox lesen.
3. Dem Link aus der Hinweisbox folgen.
4. Dort die Person per „Jetzt endgültig anonymisieren" abräumen (siehe ENT-CLIENT-16) und den Lösch-Dialog erneut aufrufen.
5. Gegenprobe für Feldvorlagen: `/config/field-templates/<uuid>/delete/` einer nur von derselben Papierkorb-Person belegten Vorlage.

**Erwartetes Ergebnis:**
- Die Blocker-Begründung weist den **Papierkorb-Anteil** getrennt aus statt pauschal „müsste zuerst geändert werden" zu verlangen — ein Schritt, der für Papierkorb-Datensätze im Fach-UI gar nicht möglich ist.
- Die Hinweisbox (`data-testid='config-delete-trash-hint'`) nennt den Ausweg und verlinkt auf den Papierkorb (`data-testid='config-delete-trash-link'`, Ziel `/clients/trash/`).
- Nach der Anonymisierung ist der Blocker verschwunden und der Typ bzw. die Vorlage löschbar.
- Beide Lösch-Dialoge (Dokumentationstyp **und** Feldvorlage) zeigen dieselbe Hinweisbox.

**DSGVO/Security-Note:**
- Der Ausweg darf keine Datenresurrektion erzwingen: „Wiederherstellen, umkonfigurieren, erneut löschen" holte einen bereits gelöschten Personendatensatz kurzzeitig zurück ins Fach-UI.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>🔀 COND — Bedingte Feldlogik (6 Cases)</strong></summary>

**Routen:** `/config/document-types/<uuid>/` (Abschnitt „Bedingte Sichtbarkeit"), `/config/document-types/<uuid>/conditions/new/`, `/config/document-types/<uuid>/conditions/<uuid>/edit/`, `/config/document-types/<uuid>/conditions/<uuid>/delete/`, `/config/document-types/<uuid>/conditions/value-input/`, `/events/new/` + `/partials/events/fields/` (Live-Sichtbarkeit im Event-Formular) 
**Views:** `src/core/views/config/conditions.py` (`ConditionCreateView`, `ConditionUpdateView`, `ConditionDeleteView`, `ConditionValueInputView`, alle `FacilityAdminRequiredMixin`); `src/core/views/events.py` (`EventFieldsPartialView`, Event-CRUD-Views mit `DynamicEventDataForm`) 
**Services:** `src/core/services/config.py` (`create_field_condition`, `update_field_condition`, `delete_field_condition`); `src/core/services/conditions.py` (`evaluate_conditions`, `find_cycle`, `normalize_value`, `build_conditions_payload` — gemeinsamer Evaluator FE/BE, ADR-035) 
**Models:** `FieldCondition` (`target_field`, `depends_on_field`, `operator`, `value`, `group_index`; kein eigener facility-FK, transitiv über `target_field.document_type.facility`, RLS doppelt-transitiv) 
**Frontend:** `src/static/js/alpine/conditions.js` (`window.AnlaufstelleConditions`, `Alpine.data("conditionEvaluator"/"conditionalField")`) — CSP-safe (kein `unsafe-eval`), Payload via `json_script` 
**E2E-Coverage:** `test_field_conditions.py`, `test_condition_parity.py`; Unit-/Modell-/Formularsuiten: `test_conditions_evaluator.py`, `test_field_condition_model.py`, `test_event_form_conditions.py`, `test_config_conditions.py` 
**Spezial-Setup:**
- Rolle **facility_admin** (`admin`) ist Voraussetzung für den Regel-Editor (Config-UI); das Event-Formular selbst nutzt `miriam` (fachkraft) analog zum regulären Dokumentationsfluss.
- Wegwerf-DocType-Konvention `E2E-Cond-<uuid4>` mit mindestens zwei Feldern (mindestens eine zulässige Quelle) — bewusst kein Seed-DocType mit vorkonfigurierten Bedingungen (Scope-Auslassung E20).
- Für ENT-COND-06 zusätzlich ein verschlüsseltes Textfeld und ein `file`-Feld am Wegwerf-DocType (beide als Quelle unzulässig).

---

### TC-ID: ENT-COND-01 — Regel anlegen (typisiertes Wert-Input)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Bedingte Feldlogik | admin (facility_admin) | C/F | ⚪ | `test_field_conditions.py`, `test_config_conditions.py` |

**Vorbereitung:**
- Mit `admin` eingeloggt.
- Wegwerf-DocType (`E2E-Cond-…`) mit mindestens zwei Feldern angelegt: einer Zahl-Quelle und einem mehrzeiligen Text-Ziel.

**Schritte:**
1. DocType-Detailseite öffnen → Abschnitt „Bedingte Sichtbarkeit" → „Neue Bedingung".
2. Zielfeld auswählen, danach Quellfeld auswählen — das Wert-Eingabefeld lädt automatisch typisiert nach (bei einer Zahl-Quelle ein `number`-Input).
3. Operator „ist größer als" wählen, Wert `10` eingeben, „Bedingung hinzufügen" absenden.

**Erwartetes Ergebnis:**
- Das Board zeigt die neue Regel „Wenn „<Quelle>" ist größer als" mit dem Wert `10` unter „Zeige „<Ziel>"".
- Das Wert-Eingabefeld wechselt je nach Quelltyp typisiert (number/date/time-Input, select/boolean-Dropdown; bei `is_empty`/`is_not_empty` kein Eingabefeld, sondern ein Hinweistext).
- Ein `config_create`-AuditLog-Eintrag mit `target_slug`, `source_slug`, `operator`, `group_index` im Detail wird erzeugt.

**Status:** ☐ Offen

---

### TC-ID: ENT-COND-02 — Zyklusfehler

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Bedingte Feldlogik | admin (facility_admin) | C || `test_field_conditions.py`, `test_field_condition_model.py`, `test_config_conditions.py` |

**Vorbereitung:**
- ENT-COND-01 durchgeführt (Regel „Quelle → Ziel" existiert bereits).

**Schritte:**
1. Im selben Board eine Gegenrichtungs-Regel anlegen: Zielfeld = Quelle (aus ENT-COND-01), Quellfeld = Ziel, Operator „ist nicht leer".
2. Formular absenden.

**Erwartetes Ergebnis:**
- Das Formular wird mit einer Fehlermeldung neu gerendert: „Diese Bedingung erzeugt einen Zyklus: …" inkl. Pfad über beide Feldnamen.
- Die Zyklus-Regel wird NICHT gespeichert — das Board zeigt weiterhin ausschließlich die ursprüngliche Regel aus ENT-COND-01.

**Status:** ☐ Offen

---

### TC-ID: ENT-COND-03 — Live-Ein-/Ausblenden ohne Reload

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft (`miriam`) | C/F | ✓ | `test_field_conditions.py`, `test_condition_parity.py` |

**Vorbereitung:**
- ENT-COND-01 durchgeführt.
- Mit `miriam` eingeloggt.

**Schritte:**
1. Neues Event mit dem Wegwerf-DocType anlegen — das Zielfeld ist initial NICHT sichtbar (Quelle leer).
2. Quellfeld auf einen Wert über dem Schwellenwert setzen (z.B. `15`).
3. Quellfeld wieder auf einen Wert unter dem Schwellenwert setzen (z.B. `5`).
4. Im selben Formular den Dokumentationstyp abwählen und erneut auf den Wegwerf-DocType wechseln (HTMX-Swap von `#dynamic-fields`).

**Erwartetes Ergebnis:**
- Das Zielfeld erscheint SOFORT nach Schritt 2 — ganz ohne Reload oder Server-Roundtrip.
- Das Zielfeld verschwindet SOFORT wieder nach Schritt 3.
- Nach dem DocType-Wechsel (Schritt 4) greift die Bedingung erneut unverändert (Payload + Evaluator-Komponente werden mit dem geswappten Partial neu initialisiert).
- Keine CSP-Violations/JS-Fehler in der Konsole während des gesamten Ablaufs.

**Status:** ☐ Offen

---

### TC-ID: ENT-COND-04 — Hidden-Required

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Events | fachkraft (`miriam`) | C/F | ✓ | `test_field_conditions.py`, `test_event_form_conditions.py` |

**Vorbereitung:**
- Am Wegwerf-DocType ist das Zielfeld als Pflichtfeld (`is_required`) konfiguriert; die Quelle bleibt leer, sodass das Zielfeld versteckt ist.

**Schritte:**
1. Event-Formular OHNE das (versteckte) Pflichtfeld auszufüllen absenden.
2. Gegenprobe: Quellfeld vor dem Absenden über den Schwellenwert setzen (Zielfeld wird sichtbar), Zielfeld leer lassen, erneut absenden.

**Erwartetes Ergebnis:**
- Schritt 1: kein Validierungsfehler auf dem versteckten Pflichtfeld — das Formular wird erfolgreich gespeichert, Redirect auf die Event-Detailseite.
- Schritt 2: sobald das Feld sichtbar ist, greift die reguläre Pflichtfeld-Validierung wieder — Absenden schlägt mit dem Standard-Pflichtfeld-Fehler fehl.

**Status:** ☐ Offen

---

### TC-ID: ENT-COND-05 — Kaskade (zweistufig)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Events | admin + fachkraft (`miriam`) | C || `test_event_form_conditions.py`, `test_conditions_evaluator.py` |

**Vorbereitung:**
- Wegwerf-DocType mit drei Feldern: A (Zahl) → B (Text, Pflicht, Bedingung „A ist größer als 10") → C (Text, Pflicht, Bedingung „B ist nicht leer").

**Schritte:**
1. Event anlegen, Feld A auf einen Wert unter dem Schwellenwert setzen (B bleibt versteckt).
2. Beobachten, dass C ebenfalls versteckt bleibt — obwohl C keine eigene Bedingung auf A hat, sondern nur auf B.
3. Feld A über den Schwellenwert setzen, B ausfüllen.
4. Beobachten, dass C daraufhin erscheint.

**Erwartetes Ergebnis:**
- Ein verstecktes Quellfeld gilt für seine Abhängigen als leer — die Kaskade versteckt C automatisch mit, sobald B versteckt ist.
- Das Formular mit versteckten B/C (beide Pflichtfelder) ist ohne Validierungsfehler absendbar.
- Sobald B und C sichtbar werden, greift deren jeweilige Pflichtfeld-Validierung wieder normal.

**Status:** ☐ Offen

---

### TC-ID: ENT-COND-06 — verschlüsselte/file-Quelle abgewiesen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Bedingte Feldlogik | admin (facility_admin) | C || `test_field_conditions.py`, `test_field_condition_model.py`, `test_config_conditions.py` |

**Vorbereitung:**
- Wegwerf-DocType zusätzlich um ein verschlüsseltes Textfeld und ein `file`-Feld ergänzt.

**Schritte:**
1. Im Regel-Editor „Neue Bedingung" öffnen und das Quellfeld-Dropdown betrachten.
2. Zur Gegenprobe per direktem POST (DevTools/curl) das `file`-Feld bzw. das verschlüsselte Feld als `depends_on_field` übergeben.

**Erwartetes Ergebnis:**
- Das Quellfeld-Dropdown enthält weder das `file`-Feld noch das verschlüsselte Feld (das Zielfeld-Dropdown enthält beide weiterhin).
- Ein erzwungener POST mit `file`-/verschlüsseltem Feld als Quelle wird serverseitig abgewiesen — Modell-`clean()`-Defense mit „Dieser Feldtyp kann nicht als Quelle für Bedingungen verwendet werden." bzw. „Verschlüsselte Felder sind als Quelle nicht zulässig.".

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>🧩 CLIENT-DYN / CASE-DYN — Dynamische Zusatzfelder an Person und Fall (6 Cases)</strong></summary>

**Routen:** `/clients/new/`, `/clients/<uuid>/edit/`, `/clients/<uuid>/` (Abschnitt „Zusatzfelder"), `/partials/clients/fields/`; `/cases/new/`, `/cases/<uuid>/edit/`, `/cases/<uuid>/`, `/partials/cases/fields/`; Konfiguration unter `/config/document-types/` (Typ mit `entity_type` Person/Fall anlegen) 
**Views:** `src/core/views/clients.py` (`ClientCreateView`, `ClientUpdateView`, `ClientDetailView`, `ClientFieldsPartialView`); `src/core/views/cases.py` (`CaseCreateView`, `CaseUpdateView`, `CaseDetailView`, `CaseFieldsPartialView`) 
**Forms:** `src/core/forms/dynamic.py` (`DynamicDataForm` — entity-neutrale Basis, `grouped_fields()`; das Flag `exclude_file_fields` ist mit M24 ersatzlos entfallen); `src/core/forms/clients.py` (`DynamicClientDataForm`, `ClientForm` mit hx-get-Typ-Select); `src/core/forms/cases.py` (Fall-Pendant) 
**Services:** `src/core/services/dynamic_data.py` (`merge_dynamic_data` — E11-Typwechsel-Policy, `validate_data_json`, `remove_restricted_fields`, `decrypt_dynamic_text_data`); `src/core/services/client/main.py` (`_check_client_document_type`), `src/core/services/case/cases.py` (`_check_case_document_type`) 
**Models:** `Client.document_type`/`Client.data_json`, `Case.document_type`/`Case.data_json` (Migrationen `0118`/`0119`, `on_delete=PROTECT`); Verschlüsselung über `DynamicDataEncryptionMixin` (Cache an `document_type_id` gebunden) 
**E2E-Coverage:** keine eigene E2E-Suite — Abdeckung über Unit-/View-Suiten `test_entity_dynamic_ui.py`, `test_client_dynamic_data.py`, `test_case_dynamic_data.py`, `test_forms_dynamic.py`, `test_client_export.py`
**Hintergrund:** [ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md) (entity_type, E11-Typwechsel; der dort in §9 beschriebene mehrschichtige FILE-Ausschluss „E8" ist seit M24 abgelöst), [ADR-039](../adr/039-owner-generische-dateianhaenge.md) (Datei-Felder an allen drei Entitäten), [`docs/packages.md`](../packages.md) 
**Spezial-Setup:**
- Rolle **facility_admin** (`admin`) legt die Wegwerf-Typen an, die eigentlichen Fälle laufen als **fachkraft** (`miriam`); für die Sensitivitäts-Probe zusätzlich **assistenz** (`lena`).
- Wegwerf-Typen nach Konvention `E2E-ClientDyn-<uuid4>` (`entity_type` = Person) und `E2E-CaseDyn-<uuid4>` (`entity_type` = Fall) mit mindestens: einem normalen Textfeld, einem Feld mit Sensibilität **hoch** (verschlüsselt) und — nur für CLIENT-DYN-04 — einem zweiten Person-Typ mit **anderem** Feldset. Für CLIENT-DYN-03 zusätzlich ein **Datei**-Feld (seit M24 zulässig, Refs #1738); die Dateipfade selbst prüft die ATT-Sektion (ENT-ATT-10 ff.).
- Der Seed bringt **keine** Person-/Fall-Typen mit (`default.yaml` ist durchgehend `entity_type: event`) — die Typen müssen für diese Cases angelegt werden.

---

### TC-ID: ENT-CLIENT-DYN-01 — Typ-Auswahl an der Person zeigt nur Person-Typen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Zusatzfelder | admin + fachkraft (`miriam`) | C/F | ⚪ | `test_entity_dynamic_ui.py`, `test_client_dynamic_data.py` |

**Vorbereitung:**
- Als `admin` je einen Wegwerf-Typ mit `entity_type` **Person** und einen mit `entity_type` **Fall** angelegt; die Seed-Ereignistypen (Kontakt, Krisengespräch, …) existieren ohnehin.
- Danach als `miriam` eingeloggt.

**Schritte:**
1. `/clients/new/` aufrufen und das Dropdown „Dokumentationstyp" öffnen.
2. Den Person-Wegwerf-Typ auswählen — die Zusatzfelder laden per HTMX in `#dynamic-fields` nach, **ohne** Seiten-Reload.
3. Felder ausfüllen und die Person speichern.
4. Detailseite der Person öffnen.

**Erwartetes Ergebnis:**
- Das Dropdown enthält **ausschließlich** Typen mit `entity_type` Person — weder Ereignis- noch Fall-Typen tauchen auf.
- Nach Schritt 2 stehen die Zusatzfelder in der konfigurierten Block-Reihenfolge (Gruppen und freie Felder ineinander verschachtelt, [ADR-036](../adr/036-globale-block-sequenz-feldordnung.md)).
- Nach dem Speichern zeigt die Detailseite einen Abschnitt „Zusatzfelder" mit Label und Wert je Feld.
- Keine CSP-Violations/JS-Fehler in der Konsole.

**DSGVO/Security-Note:**
- Die Entitätsgrenze wird nicht nur im Dropdown, sondern zusätzlich im Service (`_check_client_document_type`) durchgesetzt — ein manipuliertes POST mit einem Ereignis-Typ wird abgewiesen.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-DYN-02 — Sensible Zusatzfelder: verschlüsselt gespeichert, für Assistenz unsichtbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Zusatzfelder | fachkraft (`miriam`) + assistenz (`lena`) | C || `test_entity_dynamic_ui.py`, `test_client_dynamic_data.py` |

**Vorbereitung:**
- ENT-CLIENT-DYN-01 durchgeführt; die Person trägt einen Wert im Feld mit Sensibilität **hoch**.

**Schritte:**
1. Als `miriam` die Person bearbeiten — das sensible Feld ist mit seinem **Klartext-Wert** vorbelegt.
2. Ein anderes (nicht sensibles) Feld ändern und speichern.
3. Als `lena` (Assistenz) dieselbe Person öffnen und anschließend bearbeiten.
4. Als `lena` ein sichtbares Feld ändern und speichern.
5. Als `miriam` die Person erneut öffnen.

**Erwartetes Ergebnis:**
- Schritt 2: der sensible Wert bleibt unverändert erhalten und liegt in der Datenbank weiterhin als Chiffretext (Fernet) in `data_json`.
- Schritt 3: `lena` sieht das sensible Feld **nicht** — weder im Detail (dort steht „[Eingeschränkt]") noch im Formular.
- Schritt 5: der von `lena` nie gesehene sensible Wert ist **immer noch da** — er wurde nicht durch das Speichern der Assistenz gelöscht (Restricted-Merge).

**DSGVO/Security-Note:**
- Art. 32: Zusatzfelder mit Sensibilität `high` werden at rest verschlüsselt; Art. 5 Abs. 1 lit. f: der Wert verlässt den Server nicht in Richtung einer Rolle, die ihn nicht sehen darf.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-DYN-03 — Datei-Felder sind an Person und Fall konfigurierbar (E8-Sperre gefallen)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Klient:innen | admin (facility_admin) | C || `test_entity_dynamic_ui.py`, `test_forms_dynamic.py`, `test_packages_schema.py` |

**Vorbereitung:**
- Eine Feldvorlage vom Typ **Datei** existiert (der Seed liefert `Anhang` mit).

**Schritte:**
1. Im Feld-Board des Person-Wegwerf-Typs die Datei-Feldvorlage zuordnen; dasselbe am Fall-Wegwerf-Typ.
2. `/clients/new/` mit diesem Typ öffnen.
3. Unter `/config/field-templates/` eine **neue** Feldvorlage mit Feldtyp „Datei" anlegen.
4. Ein Paket mit `field_type: file` an einem `entity_type: client`-Typ per `manage.py load_package --dry-run` einspielen (siehe DEV-PKG-03).

**Erwartetes Ergebnis:**
- Schritt 1: die Zuordnung gelingt — die frühere Fehlermeldung „nur an Ereignis-Typen zulässig" gibt es nicht mehr (alle vier Sperr-Schichten sind mit M24 ersatzlos entfallen, Refs #1738, [ADR-039](../adr/039-owner-generische-dateianhaenge.md) §9).
- Schritt 2: das Formular trägt `enctype="multipart/form-data"` und rendert das Datei-Feld — **kein** stiller Skip, **keine** Warnung im Server-Log mehr.
- Schritt 3: Der Feldtyp „Datei" ist an jedem Entitätstyp wählbar; der frühere Typwechsel-Guard im Formular ist weg.
- Schritt 4: das Paket wird **akzeptiert** (kein Schema-Fehler mehr). `schema_version` bleibt bei `1` — eine Restriktions-Lockerung ist abwärtskompatibel.
- Gegenprobe Auslieferung: `src/core/packages/default.yaml` ordnet weiterhin **kein** Datei-Feld an einem Personen-/Fall-Typ zu — das bleibt Trägerentscheidung.

**Status:** ☐ Offen

---

### TC-ID: ENT-CLIENT-DYN-04 — Typwechsel verwirft die Werte des alten Typs (E11)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Klient:innen / Zusatzfelder | fachkraft (`miriam`) | C/F | ⚪ | `test_entity_dynamic_ui.py`, `test_forms_dynamic.py` |

**Vorbereitung:**
- Zwei Person-Typen mit **unterschiedlichen** Feldsets; die Person trägt aktuell Typ A mit ausgefüllten Feldern.

**Schritte:**
1. Person bearbeiten, im Typ-Dropdown von Typ A auf Typ B wechseln — die Zusatzfelder werden per HTMX ausgetauscht.
2. Den Warnhinweis im Formular lesen, Felder von Typ B ausfüllen und speichern.
3. Detailseite prüfen.
4. Person erneut bearbeiten, den Typ auf „— Kein Typ —" setzen und speichern.

**Erwartetes Ergebnis:**
- Vor dem Speichern weist das Formular sichtbar darauf hin, dass die Zusatzfeld-Werte beim Typwechsel verworfen werden.
- Nach Schritt 2 enthält `data_json` **ausschließlich** Slugs von Typ B — kein Rest von Typ A bleibt als unsichtbarer Ballast liegen.
- Schritt 4 läuft ohne Fehler durch: Typ **und** Zusatzfelder sind danach leer, die Detailseite zeigt keinen Abschnitt „Zusatzfelder" mehr.
- Ein optimistischer Sperr-Konflikt (Datensatz wurde parallel geändert) wird weiterhin als Konfliktmeldung angezeigt, nicht überschrieben.

**DSGVO/Security-Note:**
- Art. 5 Abs. 1 lit. c/e: Werte, die im neuen Feldset keine Bedeutung mehr haben, werden nicht „vorsichtshalber" aufbewahrt.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-DYN-01 — Fall mit Zusatzfeldern anlegen, bearbeiten und ansehen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Fälle / Zusatzfelder | fachkraft (`miriam`) | C/F | ⚪ | `test_entity_dynamic_ui.py`, `test_case_dynamic_data.py` |

**Vorbereitung:**
- Fall-Wegwerf-Typ (`entity_type` = Fall) mit mindestens einem normalen und einem sensiblen Feld angelegt.
- Eine Person mit mindestens einem Fall existiert.

**Schritte:**
1. `/cases/new/` aufrufen, im Typ-Dropdown den Fall-Wegwerf-Typ wählen — Zusatzfelder laden per HTMX nach.
2. Felder ausfüllen, Fall speichern, Detailseite öffnen.
3. Fall erneut bearbeiten: die vorhandenen Werte sind im Klartext vorbelegt; einen Wert ändern und speichern.
4. Audit-Log (`/audit/`) auf den Update-Eintrag prüfen.

**Erwartetes Ergebnis:**
- Das Dropdown enthält **ausschließlich** Fall-Typen (keine Ereignis-, keine Person-Typen).
- Die Detailseite zeigt einen Abschnitt „Zusatzfelder" in Block-Reihenfolge; sensible Felder werden für berechtigte Rollen im Klartext, für andere als „[Eingeschränkt]" angezeigt.
- Der Audit-Eintrag nennt `data_json` unter den geänderten Feldern — **ohne** die Werte selbst.
- Das Speichern über ein zwischenzeitlich veraltetes Formular führt weiterhin zur Konfliktmeldung (`expected_updated_at`), nicht zum stillen Überschreiben.

**Status:** ☐ Offen

---

### TC-ID: ENT-CASE-DYN-02 — Typ in Benutzung lässt sich nicht löschen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Konfiguration / Fälle | admin (facility_admin) | C || `test_entity_dynamic_ui.py`, `test_config_document_types.py` |

**Vorbereitung:**
- ENT-CASE-DYN-01 durchgeführt (mindestens ein Fall trägt den Wegwerf-Typ); analog eine Person mit Person-Typ aus ENT-CLIENT-DYN-01.

**Schritte:**
1. Als `admin` `/config/document-types/` öffnen — die Liste zeigt je Typ ein Badge mit dem Entitätstyp („Ereignis" / „Person" / „Fall").
2. Den Fall-Wegwerf-Typ löschen wollen.
3. Dasselbe für den Person-Wegwerf-Typ.
4. Den Fall aus Schritt 1 löschen (bzw. seinen Typ entfernen) und den Löschversuch wiederholen.

**Erwartetes Ergebnis:**
- Der Löschversuch wird mit einer deutschen Blocker-Meldung abgewiesen, die die **Anzahl** der verweisenden Fälle bzw. Personen nennt.
- Es erscheint **kein** 500er — der `PROTECT`-Constraint wird als Fachmeldung übersetzt.
- Nach Schritt 4 ist der Typ löschbar (oder die Blocker-Liste nennt nur noch die verbleibenden Gründe).
- Der Entitätstyp ist beim Bearbeiten eines bestehenden Typs **nicht** änderbar; ein manipulierter POST ändert den Wert nicht und erzeugt keinen 500er.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>📥 IMPORT — CSV-Import-Wizard (9 Cases)</strong></summary>

**Routen:** `/import/`, `/import/neu/`, `/import/<uuid>/`, `/import/<uuid>/zuordnung/`, `/import/<uuid>/probelauf/`, `/import/<uuid>/uebernehmen/`, `/import/<uuid>/abbrechen/` 
**Views:** `src/core/views/import_wizard.py` (`ImportListView`, `ImportCreateView`, `ImportDetailView`, `ImportMappingView`, `ImportPreviewView`, `ImportCommitView`, `ImportAbortView`) — alle mit `FacilityAdminRequiredMixin`, kein Sudo-Mode 
**Services:** `src/core/services/import_engine.py` (`store_import_upload`, `apply_mapping`, `available_targets`, `run_import`, `commit_import`, `abort_import`, `cleanup_stale_import_jobs`) — schreibt ausschließlich über `create_client`/`create_event` 
**Forms:** `src/core/forms/import_data.py` (`ImportUploadForm` — Zieltyp, Dokumentationstyp, Datei, Trennzeichen-/Zeichensatz-Auswahl) 
**Models:** `src/core/models/import_job.py` (`ImportJob` — `Status`: uploaded → mapped → validated → committed/aborted; `storage_filename`, `original_filename_encrypted` nur verschlüsselt) 
**E2E-Coverage:** `src/tests/e2e/test_import_wizard.py` (Wizard-Roundtrip, Fehlerpfad, `data_json`-Ereignis-Import, Rollen-Gate); ergänzend `test_import_engine.py`, `test_import_views.py`, `test_import_job.py` 
**Spezial-Setup:**
- Rolle **facility_admin** (`admin`) — alle anderen Rollen bekommen 403 (`TestRoleGate`).
- CSV-Testdateien lokal vorbereiten: eine saubere (0-Fehler-Lauf), eine mit mind. einem defekten Datensatz (Zeilenfehler-Lauf), optional eine cp1252-kodierte (Excel-Export) für ENT-IMPORT-03.
- Limits: max. 1000 Datenzeilen / 5 MB (`import_engine.MAX_ROWS`/`MAX_BYTES`).

---

### TC-ID: ENT-IMPORT-01 — Navigation „Datenimport" nur für facility_admin sichtbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin vs. emma/miriam/lena | C | ⚪ | `src/tests/e2e/test_import_wizard.py::TestImportAccessControl` |

**Voraussetzung:** keine.

**Schritte:**
1. Als `emma` (lead), `miriam` (staff) und `lena` (assistant) einloggen und Desktop-Navigation + Mobile-„Mehr" sichten.
2. Als `admin` (facility_admin) einloggen, dieselben Stellen sichten.
3. Als `admin` direkt `/import/` aufrufen; als `miriam` denselben Aufruf versuchen.

**Erwartetes Ergebnis:**
- Schritt 1: kein Menüpunkt „Datenimport" — weder als Reiter des Bereichs „Verwaltung" (`data-testid='subnav-tab-import'`) noch als Zugang dorthin: der Bereichs-Eintrag „Verwaltung" fehlt in der Desktop-Navigation (`data-testid='nav-verwaltung'`) und im mobilen „Mehr"-Menü (`data-testid='mobile-nav-verwaltung'`).
- Schritt 2: Menüpunkt vorhanden, führt zu `/import/`.
- Schritt 3: `admin` erhält 200, `miriam` 403 (`TestRoleGate.test_other_roles_get_403_on_the_list`).

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-02 — Upload mit Trennzeichen-Wahl und Zeilen-/Größenlimit

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C | ⚪ | `src/tests/e2e/test_import_wizard.py::TestImportClientHappyPath`, `test_import_views.py`, `test_import_engine.py` |

**Voraussetzung:** ENT-IMPORT-01 (Zugriff auf `/import/`).

**Schritte:**
1. `/import/neu/` aufrufen, Zieltyp „Person" wählen, eine saubere semikolon-getrennte CSV mit Kopfzeile hochladen, Trennzeichen auf „Automatisch erkennen" belassen.
2. Speichern → Redirect auf Zuordnungs-Schritt (`/import/<uuid>/zuordnung/`).
3. Zweiten Lauf mit einer Datei starten, die **1001** Datenzeilen enthält.
4. Dritten Lauf mit einer Datei > 5 MB starten.
5. Vierten Lauf mit Zieltyp „Ereignis" **ohne** Dokumentationstyp starten.

**Erwartetes Ergebnis:**
- Schritt 2: `ImportJob` im Status `uploaded`, Audit-Eintrag `import_uploaded` (Action-Detail ohne Dateinamen).
- Schritt 3: Fehlermeldung „Die Datei hat mehr als 1000 Datenzeilen.", kein Job angelegt.
- Schritt 4: Fehlermeldung zur Maximalgröße (5 MB), kein Job angelegt.
- Schritt 5: Fehlermeldung — Ereignis-Import ohne Dokumentationstyp ist nicht ausführbar (`test_upload_rejects_an_event_job_without_document_type`).

**DSGVO/Security-Note:**
- Quelldatei liegt sofort verschlüsselt unter `MEDIA_ROOT/<facility>/imports/`; der Originaldateiname wird **nur verschlüsselt** gespeichert (`original_filename_encrypted`) und taucht auch im Audit-Detail nicht auf.

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-03 — cp1252-Fallback bei nicht-UTF-8-Datei

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C || `test_import_engine.py::TestParsing` |

**Voraussetzung:** eine CSV-Datei, in Windows-1252 exportiert (z.B. Umlaute aus älterem Excel).

**Schritte:**
1. `/import/neu/` aufrufen, die cp1252-Datei hochladen, Zeichensatz auf „Automatisch erkennen" belassen.
2. Vorschau (erste Zeilen) auf der Zuordnungsseite prüfen.

**Erwartetes Ergebnis:**
- Umlaute/Sonderzeichen werden korrekt dargestellt (kein Mojibake) — die Engine fällt bei UTF-8-Dekodierfehler automatisch auf cp1252 zurück (`_decode`).
- Alternativ: Zeichensatz explizit auf „Windows-1252" stellen — führt zum selben Ergebnis.

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-04 — Zuordnung: Spalten→Ziele inkl. `data_json.<slug>`, FILE-Felder nie angeboten

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C || `src/tests/e2e/test_import_wizard.py::TestImportEventWithDataJson`, `test_import_engine.py::TestAvailableTargets`, `test_import_views.py` |

**Voraussetzung:** Dokumentationstyp mit mind. einem normalen und einem Datei-Feld existiert; Upload mit Spalten, die auf Stammdaten **und** ein dynamisches Feld zeigen sollen.

**Schritte:**
1. Auf der Zuordnungsseite pro Spalte ein Ziel aus dem Dropdown wählen — mind. eine Spalte auf ein dynamisches Feld (`data_json.<slug>`) mappen.
2. Dropdown-Inhalt der Zieldropdowns sichten: enthält es das Datei-Feld?
3. Speichern mit einer Pflichtspalte (Pseudonym bzw. Zeitpunkt) unbelegt.
4. Speichern mit doppelt vergebenem Ziel (zwei Spalten auf dasselbe Ziel).

**Erwartetes Ergebnis:**
- Schritt 1: Sample-Werte je Spalte werden zur Orientierung angezeigt (`test_mapping_page_shows_sample_values_per_column`).
- Schritt 2: **kein** Datei-Feld im Dropdown — Datei-Uploads laufen nie über den CSV-Import (`test_file_fields_are_never_offered`).
- Schritt 3: Formular meldet die fehlende Pflichtzuordnung, kein Speichern.
- Schritt 4: Formular meldet doppeltes Ziel, kein Speichern.
- Erneutes Öffnen der Zuordnungsseite nach erfolgreichem Speichern zeigt die vorherige Auswahl vorbelegt (`test_mapping_page_keeps_the_previous_selection`).

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-05 — Probelauf mit Fehlern zeigt PII-freie Fehlertabelle

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C || `src/tests/e2e/test_import_wizard.py::TestImportErrorPath`, `test_import_engine.py::TestErrorReportIsPiiFree`, `TestSavepointPerRow` |

**Voraussetzung:** CSV mit mind. zwei fehlerhaften Zeilen (z.B. eine mit bereits vergebenem Pseudonym, eine mit unbekannter Kontaktstufe).

**Schritte:**
1. Zuordnung speichern, „Probelauf" auslösen (`/import/<uuid>/probelauf/`).
2. Fehlertabelle auf der Detailseite sichten.
3. Seitenquelltext (Strg+U bzw. „Seitenquelltext anzeigen") nach den eingegebenen Testzellwerten durchsuchen (z.B. dem konkreten Pseudonym-String).

**Erwartetes Ergebnis:**
- Fehlertabelle zeigt **ausschließlich** Zeile, Spalte, Fehlercode und eine feste Meldung aus dem Katalog (z.B. „Die fachliche Prüfung hat die Zeile abgelehnt.", „Wert ist keine gültige Zahl.") — **niemals** den eingegebenen Zellwert.
- Schritt 3: kein Treffer für die Testzellwerte im gerenderten HTML.
- `job.status` bleibt `validated` mit `error_count > 0` — kein Datensatz wurde angelegt (Probelauf rollt zurück).
- Zähler auf der Detailseite ist als „Würden angelegt (Probelauf)" beschriftet, nicht als „Angelegte Datensätze" (`TestDetailCountLabel`).

**DSGVO/Security-Note:**
- Der Fehlerbericht (`result_json`) ist als `non_pii` klassifiziert — genau diese Case verifiziert die Zusicherung händisch, ergänzend zum Sanitizer-Unit-Test.

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-06 — Übernahme nur bei 0 Fehlern, All-or-Nothing, Quelldatei danach gelöscht

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C | ⚪ | `src/tests/e2e/test_import_wizard.py::TestImportClientHappyPath`, `test_import_engine.py::TestCommitRun`, `TestCommitImport` |

**Voraussetzung:** ENT-IMPORT-05 durchgeführt (fehlerhafter Lauf liegt vor) **und** ein zweiter, fehlerfreier Lauf vorbereitet.

**Schritte:**
1. Beim fehlerhaften Lauf aus ENT-IMPORT-05 versuchen, „Übernehmen" auszulösen (`/import/<uuid>/uebernehmen/`).
2. Beim fehlerfreien Lauf: Probelauf durchführen (0 Fehler), dann „Übernehmen" auslösen.
3. Nach der Übernahme: `/import/<uuid>/` erneut öffnen; die angelegten Personen/Ereignisse im jeweiligen Modul stichprobenartig prüfen (inkl. Activity, EventHistory/Audit falls Ereignis-Import).
4. Prüfen, ob die verschlüsselte Quelldatei unter `MEDIA_ROOT/<facility>/imports/` noch existiert (per SSH/Dev-Zugriff, siehe Sektion D).

**Erwartetes Ergebnis:**
- Schritt 1: Fehlermeldung „Der Importlauf enthält Fehler und kann nicht übernommen werden." — kein Datensatz angelegt.
- Schritt 2: Übernahme erfolgreich, `job.status=committed`, alle Zeilen als Personen/Ereignisse angelegt — **derselbe** Codepfad wie der Probelauf, nur ohne Rollback.
- Schritt 3: Detailseite zeigt „Angelegte Datensätze" (faktische Beschriftung), Audit `import_committed` mit Zählern, ohne Feldwerte.
- Schritt 4: `storage_filename` ist geleert, die `.enc`-Datei ist von der Platte entfernt (`test_commit_deletes_the_upload_and_audits`).

**DSGVO/Security-Note:**
- All-or-Nothing verhindert Teilimporte mit inkonsistentem Stand; die Löschung der Quelldatei nach Übernahme minimiert die Aufbewahrungszeit der rohen CSV (Art. 5 Abs. 1 lit. e).

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-07 — Abbrechen löscht die verschlüsselte Quelldatei ohne Übernahme

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C || `src/tests/e2e/test_import_wizard.py::TestImportErrorPath`, `test_import_engine.py::TestAbortImport` |

**Voraussetzung:** ein Lauf im Status `uploaded` oder `mapped` (noch keine Übernahme).

**Schritte:**
1. Auf der Detail- oder Zuordnungsseite „Abbrechen" auslösen (`/import/<uuid>/abbrechen/`).
2. Detailseite erneut öffnen.
3. Abbrechen ein zweites Mal auf demselben Lauf versuchen.

**Erwartetes Ergebnis:**
- Schritt 1: `job.status=aborted`, keine Personen/Ereignisse angelegt, Audit `import_aborted` mit `reason=user`.
- Die verschlüsselte Quelldatei ist von der Platte entfernt, `storage_filename` geleert.
- Schritt 3: freundliche Meldung „Der Importlauf ist bereits abgeschlossen." statt 500er (`test_second_abort_redirects_instead_of_failing`).

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-08 — Cross-Facility: fremder Import-Lauf ist 404

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin (F1) + admin_1 (F2) | C || `test_import_views.py::TestFacilityScoping` |

**Voraussetzung:** ein Import-Lauf in Facility 1, angelegt von `admin`.

**Schritte:**
1. Als `admin` (F1) einen Lauf anlegen, UUID notieren.
2. Als `admin_1` (F2) einloggen, `/import/` aufrufen — Liste sichten.
3. Direkt-URL `/import/<F1-uuid>/`, `/import/<F1-uuid>/zuordnung/` und `/import/<F1-uuid>/abbrechen/` aufrufen.

**Erwartetes Ergebnis:**
- Schritt 2: Liste zeigt ausschließlich eigene (F2-)Läufe.
- Schritt 3: alle drei Aufrufe liefern 404 — kein Leak, dass der Lauf existiert (`test_foreign_job_detail_is_404`, `test_foreign_job_abort_is_404`).

**DSGVO/Security-Note:**
- Mandantentrennung nach RLS-Muster, IDOR-Probe analog zu den bestehenden `_authz_expectations`-Tests.

**Status:** ☐ Offen

---

### TC-ID: ENT-IMPORT-09 — Statusmaschine: Probelauf/Übernahme ohne vorherigen Schritt wird abgewiesen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Datenimport | admin | C || `test_import_views.py::TestStateMachineGuards` |

**Voraussetzung:** ein frisch hochgeladener Lauf ohne Zuordnung (Status `uploaded`).

**Schritte:**
1. Direkt `/import/<uuid>/probelauf/` aufrufen, ohne vorher die Zuordnung zu speichern.
2. Direkt `/import/<uuid>/uebernehmen/` aufrufen, ohne vorherigen Probelauf.
3. Nach Übernahme (ENT-IMPORT-06) versuchen, dieselbe Zuordnung erneut zu ändern (`/import/<uuid>/zuordnung/`).

**Erwartetes Ergebnis:**
- Jeder Schritt außer der Reihe endet in `messages.error` + Redirect auf die Detailseite — **nie** ein 500er.
- Schritt 3: „Der Importlauf ist bereits abgeschlossen." — ein abgeschlossener Lauf lässt sich nicht neu zuordnen.

**Status:** ☐ Offen

</details>

<details open>
<summary><strong>✍️ RECT — Art.-16-Berichtigungsworkflow (8 Cases)</strong></summary>

**Routen:** `/clients/<uuid>/rectification/new/`, `/rectification/`, `/rectification/<uuid>/review/` 
**Views:** `src/core/views/rectification.py` (`RectificationCreateView` — `StaffRequiredMixin`; `RectificationListView`/`RectificationReviewView` — `LeadOrAdminRequiredMixin`) 
**Services:** `src/core/services/client/rectification.py` (`rectifiable_fields`, `request_rectification`, `approve_rectification`, `reject_rectification`) — Anwendung ausschließlich über `update_client` 
**Models:** `src/core/models/rectification.py` (`RectificationRequest` — `field_path`, `old_value`/`new_value`/`reason`/`review_note` verschlüsselt, partieller Unique-Index `unique_pending_rectification_request`, Check-Constraint gegen Selbst-Review), Migrationen `0138`/`0139` 
**Hintergrund:** [ADR-041](../adr/041-berichtigungsworkflow-intern.md) — ergänzt, ersetzt aber nicht [ADR-013](../adr/013-dsgvo-art16-no-selfservice.md) (kein Klientel-Self-Service) 
**E2E-Coverage:** `src/tests/e2e/test_rectification.py` (Antrag→Prüfung→Genehmigung, `data_json`-Fall, Selbst-Review-Sperre, Ablehnung mit Pflicht-Prüfvermerk, Rollen-Gate); ergänzend `test_rectification_model.py`, `test_rectification_service.py`, `test_rectification_views.py` 
**Spezial-Setup:**
- Antragstellung als **fachkraft** (`miriam`), Prüfung als **leitung** (`emma`) — Vier-Augen-Prinzip braucht zwei Sessions.
- Für die Sichtbarkeits-Probe zusätzlich eine sensible dynamische Feldvorlage, für die Widerruf-Probe eine Person, die anschließend in den Papierkorb gelegt bzw. anonymisiert wird.

---

### TC-ID: ENT-RECT-01 — Navigation „Berichtigungen" nur für lead+ sichtbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | emma (lead) vs. miriam/lena | C | ⚪ | `src/tests/e2e/test_rectification.py::TestRectificationAccessControl` |

**Schritte:**
1. Als `miriam` (staff) und `lena` (assistant) Desktop-Navigation + Mobile-„Mehr" sichten.
2. Als `emma` (lead) und `admin` (facility_admin) dieselben Stellen sichten.

**Erwartetes Ergebnis:**
- Schritt 1: kein Menüpunkt „Berichtigungen" — weder als Reiter des Bereichs „Datenschutz" (`data-testid='subnav-tab-rectification-requests'`) noch als Zugang dorthin (`data-testid='nav-datenschutz'` in der Desktop-Navigation bzw. `'mobile-nav-datenschutz'` im mobilen „Mehr"-Menü); `miriam` sieht aber den Weg, einen Antrag **zu stellen** (Entry-Point an der Personenakte, siehe ENT-RECT-02).
- Schritt 2: Menüpunkt vorhanden, führt zu `/rectification/`.

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-02 — Antrag stellen aus der Personenakte (Stammdaten + dynamische Felder nach Sichtbarkeit)

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | fachkraft (`miriam`) | C/F | ⚪ | `src/tests/e2e/test_rectification.py::TestRectificationApprovalWorkflow`, `TestRectificationDataJsonField`, `test_rectification_views.py::TestCreateView` |

**Voraussetzung:** identifizierte Person mit Dokumentationstyp, der ein normales und ein sensibles (verschlüsseltes) dynamisches Feld sowie ein Datei-Feld trägt.

**Schritte:**
1. Personen-Detail öffnen, „Berichtigung beantragen" (`/clients/<uuid>/rectification/new/`).
2. Zielfeld-Dropdown sichten: welche Felder stehen zur Auswahl?
3. Zielfeld „Pseudonym" wählen, neuen Wert + fachliche Begründung eingeben, absenden.
4. Als `lena` (assistant) denselben Weg versuchen (sensibles Feld sollte für sie nicht wählbar sein, falls sie überhaupt Zugriff auf die Personenakte hat).

**Erwartetes Ergebnis:**
- Schritt 2: Dropdown enthält Stammdaten (Pseudonym, Altersgruppe, Notizen) **und** die für `miriam` sichtbaren dynamischen Felder — **nicht** `contact_stage` (Kontaktstufe, eigener Workflow) und **nicht** das Datei-Feld.
- Schritt 3: `RectificationRequest` im Status `pending`, Audit `rectification_requested` **ohne** Feldwerte im Detail (`test_audit_entry_without_values`).
- Schritt 4: das für `lena` unsichtbare sensible Feld erscheint nicht im Dropdown; ein manipulierter POST auf dieses Feld wird serverseitig abgewiesen (`test_invisible_dynamic_field_is_rejected`).

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-03 — Nur ein offener Antrag je Feld

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | fachkraft (`miriam`) | C || `test_rectification_model.py::TestPendingUniqueness`, `test_rectification_service.py` |

**Voraussetzung:** ENT-RECT-02 (offener Antrag auf „Pseudonym").

**Schritte:**
1. Erneut einen Antrag auf **dasselbe** Zielfeld derselben Person stellen.
2. Einen Antrag auf ein **anderes** Zielfeld derselben Person stellen.
3. Den ersten Antrag genehmigen oder ablehnen lassen (ENT-RECT-04/05), danach erneut einen Antrag auf das nun abgeschlossene Feld stellen.

**Erwartetes Ergebnis:**
- Schritt 1: Formularfehler „Für dieses Feld läuft bereits ein Berichtigungsantrag." — kein zweiter offener Antrag (partieller Unique-Index als Backstop).
- Schritt 2: erfolgreich — die Sperre gilt pro Feld, nicht pro Person.
- Schritt 3: nach Abschluss ist ein neuer Antrag auf dasselbe Feld wieder möglich (`test_closed_request_does_not_block_a_new_one`).

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-04 — Prüfansicht mit entschlüsselten Alt-/Neu-Werten, Genehmigen wendet über `update_client` an

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | leitung (`emma`) | C | ⚪ | `src/tests/e2e/test_rectification.py::TestRectificationApprovalWorkflow`, `TestRectificationDataJsonField`, `test_rectification_views.py::TestReviewView`, `test_rectification_service.py::TestApproveEndToEnd` |

**Voraussetzung:** ENT-RECT-02 (offener Antrag).

**Schritte:**
1. `/rectification/` öffnen — Liste nach Status gruppiert (offen/genehmigt/abgelehnt) sichten.
2. Antrag öffnen (`/rectification/<uuid>/review/`).
3. Alt- und Neu-Wert sichten (im Klartext, entschlüsselt).
4. „Genehmigen" auslösen.
5. Personen-Detail öffnen, geänderten Wert prüfen; Audit-Log auf `CLIENT_UPDATED`/`update_client`-Spur sichten.

**Erwartetes Ergebnis:**
- Schritt 3: Alt-/Neu-Wert sind lesbar (nicht als Fernet-Token), fachliche Begründung ebenfalls.
- Schritt 4: Antrag wird `approved`, Wert am Klient:in geändert — **ausschließlich** über `update_client` (inkl. `expected_updated_at`-Konkurrenzschutz, EventHistory/Audit-Signale wie bei jeder anderen Bearbeitung).
- Schritt 5: Audit `rectification_approved` **ohne** Feldwerte im Detail; die eigentliche Wertänderung erzeugt zusätzlich den regulären `CLIENT_UPDATED`-Audit-Eintrag.
- Bei sensibler Feld-Sichtbarkeit, die sich zwischen Antrag und Prüfung erhöht hat, blockiert das die Genehmigung **nicht** (`test_raised_sensitivity_does_not_block_a_lead_reviewer`).

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-05 — Ablehnen mit Pflicht-Prüfvermerk

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | leitung (`emma`) | C || `src/tests/e2e/test_rectification.py::TestRejectionRequiresReviewNote`, `test_rectification_views.py::TestReviewView`, `test_rectification_service.py::TestReject` |

**Voraussetzung:** ein offener Antrag.

**Schritte:**
1. Review-Seite öffnen, „Ablehnen" **ohne** Prüfvermerk absenden.
2. „Ablehnen" mit Prüfvermerk absenden (z.B. „Angabe entspricht der dokumentierten Wahrnehmung, keine Korrektur nötig.").
3. Personen-Detail öffnen — Wert unverändert?

**Erwartetes Ergebnis:**
- Schritt 1: Formularfehler, Prüfvermerk ist Pflicht (`test_reject_requires_a_note`).
- Schritt 2: Antrag `rejected`, `review_note` verschlüsselt gespeichert (`test_review_note_is_encrypted_at_rest`), Audit `rectification_rejected` ohne Werte.
- Schritt 3: Wert am Klient:in ist **vollständig unverändert**.

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-06 — Vier-Augen-Verbot: Antragstellerin kann eigenen Antrag nicht prüfen

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | leitung (`emma`) | C || `src/tests/e2e/test_rectification.py::TestSelfReviewIsBlocked`, `test_rectification_views.py::test_self_review_is_blocked_in_the_view`, `test_rectification_service.py::TestApproveGuards` |

**Voraussetzung:** ein Antrag, bei dem `requested_by=emma` (Lead stellt selbst einen Antrag).

**Schritte:**
1. Als `emma` einen Berichtigungsantrag für eine eigene Test-Person stellen.
2. Als `emma` (= Antragstellerin) `/rectification/<uuid>/review/` mit „Genehmigen" aufrufen.
3. Als zweite Leitung (`admin`, facility_admin) denselben Antrag prüfen.

**Erwartetes Ergebnis:**
- Schritt 2: Fehlermeldung „Sie können Ihren eigenen Berichtigungsantrag nicht prüfen (Vier-Augen-Prinzip)." — Antrag bleibt `pending`, kein Wertwechsel; der DB-Check-Constraint ist der Backstop hinter der Formularmeldung.
- Schritt 3: zweite Leitung kann normal genehmigen/ablehnen.

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-07 — Konflikt-Meldung bei zwischenzeitlich geändertem Wert

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | fachkraft (`miriam`) + leitung (`emma`) | C/F || `test_rectification_service.py::test_conflict_guard_when_value_changed_meanwhile`, `test_rectification_views.py::test_conflict_shows_message_not_500` |

**Voraussetzung:** ein offener Antrag auf ein Feld einer Person.

**Schritte:**
1. Antrag stellen (Alt-Wert wird beim Antrag selbst festgehalten).
2. Bevor die Leitung prüft: den Ziel-Wert der Person auf einem anderen Weg direkt ändern (z.B. reguläres Bearbeiten-Formular).
3. Als `emma` den Antrag genehmigen.

**Erwartetes Ergebnis:**
- Schritt 3: freundliche Konflikt-Meldung „Der Wert wurde zwischenzeitlich geändert — bitte den Antrag neu stellen." statt 500er oder stillem Überschreiben.
- Antrag bleibt `pending`, der zwischenzeitlich geänderte Wert bleibt unangetastet.
- Konflikt-Vergleich arbeitet auf entschlüsseltem Klartext, nicht auf rohen Fernet-Tokens (`test_conflict_guard_compares_plaintext_not_tokens` — sonst wäre jede Prüfung eines verschlüsselten Felds ein Fehlalarm).

**Status:** ☐ Offen

---

### TC-ID: ENT-RECT-08 — Widerrufene Personen (Papierkorb/anonymisiert) sind nicht beantrag-/prüfbar

| Bereich | Rolle | Browser | Mobile | E2E |
|---------|-------|---------|--------|-----|
| Berichtigungen | fachkraft (`miriam`) + leitung (`emma`) | C || `test_rectification_views.py::test_anonymized_client_is_not_rectifiable`, `test_closed_request_is_404`, `test_revoked_client_request_is_404` |

**Voraussetzung:** ein offener Antrag auf eine Person, die anschließend in den Papierkorb gelegt oder (via Retention/Anonymisierung) anonymisiert wird.

**Schritte:**
1. Antrag stellen, dann die Person löschen (Vier-Augen-Lösch-Workflow) oder anonymisieren lassen.
2. Als `miriam` versuchen, für dieselbe (nun widerrufene) Person einen **neuen** Antrag zu stellen.
3. Als `emma` den **bereits offenen** Antrag der widerrufenen Person prüfen wollen (`/rectification/<uuid>/review/`).

**Erwartetes Ergebnis:**
- Schritt 2: neuer Antrag wird abgewiesen — widerrufene Personen sind nicht berichtigungsfähig.
- Schritt 3: 404 statt 200 mit veralteten Werten — der Genehmigungspfad würde sonst frischen Klartext in eine bereits redigierte Zeile schreiben.
- Die Anonymisierung selbst leert `old_value`/`new_value`/`reason`/`review_note` des Antrags (auch abgeschlossener); Antrags-Metadaten (Status, Feldpfad, Zeitstempel) bleiben als Vier-Augen-Nachweis erhalten (ADR-041, Refs #1792).

**DSGVO/Security-Note:**
- Verhindert, dass die Anonymisierung durch einen nachträglichen Berichtigungs-Approve rückgängig gemacht bzw. kontaminiert wird (Präzedenz-Fix Refs #1792).

**Status:** ☐ Offen

</details>

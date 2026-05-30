# Refactoring-Plan: Anlaufstelle (Strenge Fassung)

**Stand:** 2026-04-30
**Code:** `main` @ [`ec11530`](https://github.com/anlaufstelle/app/commit/ec11530)
**Methode:** Code-First Inventur, danach R-NNN-Vorschläge mit Vorher/Nachher-Skizzen, strikte Trennung Cleanup ↔ Refactoring ↔ Redesign.
**Auditor:** Claude (Opus 4.7), zweiter Durchlauf nach Refactoring-Coach-Brille.
**Verwandt:** [2026-04-30-refactoring-plan-claude.md](2026-04-30-refactoring-plan-claude.md) — selber Tag, RF-NNN-Format, weniger streng. Diese Fassung schärfer auf Boy-Scout-Regeln, Reversibilität, Anti-Refactoring-Liste.

> **Kategorie-Disziplin:** Alles, was Verhalten für legitime Inputs ändert oder Schemas anfasst, ist Redesign — auch wenn nur 5 Zeilen Code. Sicherheits-Bug-Fixes mit kleinem Surface (Open-Redirect, Default-Whitelist, SSRF-Validator) erscheinen in B (Quick Wins) trotz Redesign-Klassifikation, weil das Risiko niedrig und der Aufwand S ist.

---

## A. Refactoring-Strategie

**Was wirklich dran ist:**

1. **Defense-in-Depth-Bypass-Lücken in den frisch eingeführten Layern** — Open-Redirect, File-Upload fail-open, SSRF, IP-Spoof. Pro Stück S, zusammen <1 PT, keine Architektur-Diskussion nötig. Erst die.
2. **Service-Layer-Disziplin nachziehen** — `Client.anonymize` (Model-Body mit Roh-SQL), `services/event.py` (683 LOC, 5 Concerns), `services/retention.py` (974 LOC, vier Strategy-Duplikate). Vor dem Plugin-Schnitt ohne Druck refaktorierbar; danach würde es teurer.
3. **K-Anon und AuditLog-Pruning entscheiden** — beide existieren halbfertig. Setting `retention_use_k_anonymization` ist Dead Code, AuditLog-Pruning hängt am `try/finally` über `DISABLE TRIGGER`. Beides verlangt eine Maintainer-Entscheidung, nicht nur Code.
4. **Self-Hosting-Operatorlauf reparieren** — frischer Stack läuft heute ohne Backups, ohne Retention, ohne RLS-Wirksamkeit (NOSUPERUSER manuell). Keiner dieser Punkte ist „Refactoring i. e. S." — alle sind Redesigns am Betriebs-Schnitt.

**Was nicht dran ist:**

- App-Aufteilung in Bounded-Context-Apps (`casework/`, `documentation/`, `dsgvo/`). Bei 22 Models und 35 Services trägt das nicht. Modul-Naming innerhalb `services/` reicht. **Erst mit zwingend.**
- Migration auf Generic-CBVs (`ListView`, `UpdateView`). 35 Views umzubauen kostet Sprint, bringt keinen funktionalen Nutzen. **Für neue Views als Standard etablieren, bestehende lassen.**
- `factory_boy` / `hypothesis`. Heutige Tests sind lesbar und stabil. Nutzen-Kosten unklar.
- Migrations-Squashing. Pre-1.0 wirkt lockend, aber jede vorhandene Test-Installation müsste re-deployen. **Reversibel nur mit großem Aufwand.**
- Sprachleitlinie #604 in Models/Services/Forms „nachziehen". Das sind 22 + 37 + 2 Strings — Fleißarbeit, nicht Strategie. **Als Boy-Scout-Regel** (s. u.), nicht als eigenes Ticket.

**Reihenfolge (1 Seite):**

```text
Sprint 1 (1 Woche)        Sprint 2 (1 Woche)         Sprint 3 (2 Wochen)         Sprint 4 (1 Woche)
─────────────────────     ──────────────────────     ──────────────────────      ─────────────────────
B-Quick-Wins komplett →   C-Refactorings 1–5    →    C-Refactorings 6–10    →    D-Redesigns
(15× S, niedrig Risiko)   (Tests + 5 strukturelle)   (Service-Aufteilung +       (Operatorlauf,
                                                      HTMX-Mixin + Pagination)    CACHES, k-Anon)
```

**Boy-Scout-Regeln (ab heute, ohne Ticket):**

1. **Wenn du eine View anfasst, in der `request.headers.get("HX-Request")` steht** → prüfe, ob `HTMXPartialMixin` jetzt sinnvoll ist. Wenn ja: umstellen.
2. **Wenn du einen Endpunkt mit `next`-Parameter siehst** → `safe_redirect_path` aus `views/utils.py` nutzen, kein eigenes `startswith("/")`.
3. **Wenn du `Settings.objects.get(facility=...)` neu schreibst** → Default-Fallback erwägen, kein `try/except: return` ohne Ersatz-Verhalten.
4. **Wenn du in `services/retention.py` editierst** → keine **fünfte** Strategy-Variante als Copy-Paste-Block einfügen. Erst RF-003 (Konsolidierung) abwarten oder mitziehen.
5. **Wenn du eine Magic-Number > 1 schreibst** (Limits, Cutoffs, Page-Sizes) → in `core/constants.py`, nicht inline.
6. **Wenn du einen `_("Klient...")`-String berührst** → auf „Person" mitziehen. Der Sweep #604 läuft als Boy-Scout-Refactoring.
7. **Wenn du eine Migration mit `RunPython.noop` schreibst** → 1-Zeilen-Docstring „warum nicht reversibel".
8. **Wenn du in einem View Business-Logik schreibst** (mehr als 5 Zeilen, die nichts mit Request-Parsing oder Template-Auswahl zu tun haben) → in `services/`.
9. **Wenn du Inline-Imports in einem Funktion-Body siehst** → an Modulkopf heben oder Zirkel-Begründung als Kommentar.
10. **Wenn du ein Form-Template ohne `{% if form.non_field_errors %}` siehst** → 4-Zeilen-Block ergänzen (Vorbild `events/create.html:48-54`).

---

## B. Quick Wins (Aufwand S, niedrig Risiko)

> 15 Einträge. Jeder ≤1 h, keine Test-Bedingungen außer Fail-Fast. Reihenfolge zufällig — können parallel passieren.

---

###: `non_field_errors`-Block in 3 Form-Templates ergänzen

```yaml
ID: R-001
Titel: non_field_errors-Block in clients/cases/workitems-Forms ergänzen
Kategorie: Cleanup
Dimension: 2 (Django-Patterns/Templates)
Fundstelle(n):
  - src/templates/core/clients/form.html
  - src/templates/core/cases/form.html
  - src/templates/core/workitems/form.html
  - Vorbild: src/templates/core/events/create.html:48-54
Aufwand: S
Risiko: niedrig (additiv, kein bestehender Code geändert)
Voraussetzungen: keine
Test-Strategie: Form-Render-Test pro Template — Form mit `add_error(None, "Cross-Field-Fehler")` zeigt Block.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (`clients/form.html`, kein Block für Form-Level-Errors):

```html
<form method="post">{% csrf_token %}
  <div>...</div>  {# Felder #}
  <button type="submit">Speichern</button>
</form>
```

**Nachher:**

```html
<form method="post">{% csrf_token %}
  {% if form.non_field_errors %}
    <div class="bg-red-50 border border-red-200 rounded-[10px] p-3">
      {% for error in form.non_field_errors %}
        <p class="text-sm text-red-600">{{ error }}</p>
      {% endfor %}
    </div>
  {% endif %}
  <div>...</div>
  <button type="submit">Speichern</button>
</form>
```

**Begründung:** Service-Layer-`ValidationError`s, die nicht an einem Field hängen, verschwinden heute stumm. UX-Bug, keine Architekturschuld.

---

###: `tabindex`-Anti-Pattern aus `events/create.html` entfernen

```yaml
ID: R-002
Titel: tabindex="1/2/100/101" aus events/create.html entfernen
Kategorie: Cleanup
Dimension: 1, 2 (Code-Level + Templates)
Fundstelle(n): src/templates/core/events/create.html:64,101,175,179
Aufwand: S
Risiko: niedrig (DOM-Reihenfolge ist bereits inhaltlich korrekt)
Voraussetzungen: keine
Test-Strategie: Manueller Tab-Through im Browser. E2E-Tab-Order-Assertion ist over-the-top.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```html
<select name="document_type" id="id_document_type" tabindex="1" ...>
<input id="client-search" tabindex="2" ...>
<button type="submit" id="event-submit-btn" tabindex="100" ...>
<a href="..." tabindex="101" ...>Abbrechen</a>
```

**Nachher:** alle vier `tabindex="..."`-Attribute streichen. DOM-Reihenfolge ist bereits sinnvoll.

**Begründung:** Positive `tabindex`-Werte > 0 brechen die DOM-Reihenfolge — WCAG/ARIA-widrig. Sprünge `1, 2, …, 100, 101` desorientieren Keyboard-Nutzer. Kein guter Grund am Code erkennbar.

---

###: Inline-Imports in `services/retention.py` an Modulkopf heben

```yaml
ID: R-003
Titel: Inline-Imports in retention.py an Top heben
Kategorie: Cleanup
Dimension: 1
Fundstelle(n): src/core/services/retention.py:446, 491, 561-563, 617, 643, 673, 711, 747, 866 (9 Stellen)
Aufwand: S
Risiko: niedrig — falls echter Zirkel auftritt, ist er sichtbar (ImportError beim Laden), nicht stumm.
Voraussetzungen: keine
Test-Strategie: `python manage.py check` + volle Test-Suite. Kein neuer Test nötig.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (in 9 Funktionsrümpfen):

```python
def enforce_anonymous(facility, settings_obj, now, *, dry_run=False):
    from core.models import Event  # ← inline
    held_ids = get_active_hold_target_ids(facility, "Event")
    ...
```

**Nachher:** alle Models am Modulkopf, ein Import-Block (Z.1-12 erweitern). Falls Zirkel auftritt: Begründung als Kommentar, nicht Workaround.

**Begründung:** 9× derselbe Import inline ist klassischer „Workaround-gegen-Zirkel"-Code. Wenn der Zirkel echt ist, soll er an einer Stelle dokumentiert sein. Wenn nicht, weg damit.

---

###: Feed-Slice `[:200]` in benannte Konstante

```yaml
ID: R-004
Titel: Magic Number 200 im Feed-Service in Konstante FEED_MAX_PER_TYPE
Kategorie: Cleanup
Dimension: 1
Fundstelle(n): src/core/services/feed.py:64, 88, 100, 116, 124 (5 Stellen)
Aufwand: S
Risiko: niedrig
Voraussetzungen: keine
Test-Strategie: Bestehende Feed-Tests unverändert grün.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```python
events = events_qs.select_related(...).order_by("-occurred_at")[:200]
...
activities = activities_qs.select_related(...).order_by(...)[:200]
...
```

**Nachher:**

```python
# core/constants.py
FEED_MAX_PER_TYPE = 200  # Default-Cap pro Feed-Typ (Events, Activities, WorkItems, Bans).

# services/feed.py
from core.constants import FEED_MAX_PER_TYPE
events = events_qs.select_related(...).order_by("-occurred_at")[:FEED_MAX_PER_TYPE]
```

**Begründung:** Heute schneidet `[:200]` Tage stumm ab. In einer Konstante ist das (a) sichtbar, (b) zentral änderbar, (c) testbar. Kein Verhaltens-Change.

---

###: `.pre-commit-config.yaml` anlegen

```yaml
ID: R-005
Titel: pre-commit-Hooks für ruff + makemigrations-Check
Kategorie: Cleanup (Tooling)
Dimension: 1, 8
Fundstelle(n): kein .pre-commit-config.yaml im Repo
Aufwand: S
Risiko: niedrig — opt-in, kein CI-Erzwingen.
Voraussetzungen: keine
Test-Strategie: lokal `pre-commit run --all-files` nach Setup, CI-Job danach.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Nachher** (`.pre-commit-config.yaml`):

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: missing-migrations
        name: missing migrations
        entry: python src/manage.py makemigrations --check --dry-run --noinput
        language: system
        pass_filenames: false
        types: [python]
```

**Begründung:** CONTRIBUTING.md erwähnt eine „Pre-Commit-Checkliste" (siehe `CLAUDE.md`), realisiert ist sie nicht. Hooks sind optional und können von Maintainer*innen einzeln installiert werden — kein Zwang.

---

###: Setup-Anleitung-Drift `cd anlaufstelle` → `cd app`

```yaml
ID: R-006
Titel: Setup-Anleitung an Repository-Verzeichnis angleichen
Kategorie: Cleanup
Dimension: 10 (Doku)
Fundstelle(n): docs/admin-guide.md:45, CONTRIBUTING.md:51
Aufwand: S
Risiko: trivial
Voraussetzungen: keine
Test-Strategie: keine — Reproduktion durch frische `git clone` in einer Sitzung.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```bash
git clone https://github.com/anlaufstelle/app.git
cd anlaufstelle  # ← falsch, Klon erzeugt app/
```

**Nachher:**

```bash
git clone https://github.com/anlaufstelle/app.git anlaufstelle
cd anlaufstelle
# oder
git clone https://github.com/anlaufstelle/app.git
cd app
```

**Begründung:** Onboarding-Friktion für jede*n neue*n Contributor. Stolperfall, nicht Architektur.

---

###: Django-Versions-Drift in Doku synchronisieren

```yaml
ID: R-007
Titel: Django 5.1 → 6.0.4 in 4 Doku-Stellen mitziehen
Kategorie: Cleanup
Dimension: 10
Fundstelle(n): README.md:190, CONTRIBUTING.md:11/226, CLAUDE.md:9, docs/ops-runbook.md:5 (alle „5.1") vs. requirements.txt (`django==6.0.4`) und CHANGELOG `[Unreleased]`.
Aufwand: S
Risiko: trivial
Voraussetzungen: keine — bewusst beim **nächsten Release-Tag** in einem Schritt, nicht jetzt.
Test-Strategie: Doc-Sync-Block der Release-Checkliste.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Begründung:** Solange `[Unreleased]` ungetagt, formal okay. Beim nächsten Tag muss eine Stelle (Release-Checkliste) alle vier mitziehen.

---

###: dev-Compose Postgres an `127.0.0.1` binden

```yaml
ID: R-008
Titel: docker-compose.yml Postgres-Port nur auf localhost
Kategorie: Redesign (mini, Verhaltensänderung für Operator, der dev-Compose aufm Server fährt)
Dimension: 8 (Betrieb)
Fundstelle(n): docker-compose.yml:8-9 (`ports: ["5432:5432"]`)
Aufwand: S
Risiko: niedrig — bricht nichts für Local-Dev.
Voraussetzungen: keine
Test-Strategie: `make db` lokal weiterhin grün.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```yaml
postgres:
  ports:
    - "5432:5432"  # exponiert auf allen Interfaces
```

**Nachher:**

```yaml
postgres:
  # Niemals auf Public-Server: dev-Compose enthält triviale Credentials.
  ports:
    - "127.0.0.1:5432:5432"
```

**Begründung:** Wenn jemand versehentlich `docker-compose.yml` (statt `prod.yml`) auf einem Server fährt, öffnet Postgres mit `anlaufstelle/anlaufstelle/anlaufstelle` auf der Public-IP. Kostet eine Zeile, schließt einen kompletten Failure-Mode.

---

###: MV-Refresh-Doku-Inkonsistenz auflösen

```yaml
ID: R-009
Titel: Migration-Docstring „täglich" an Cron-Realität „stündlich" angleichen
Kategorie: Cleanup
Dimension: 10
Fundstelle(n): src/core/migrations/0049_statistics_event_flat_mv.py:6 vs. docs/ops-runbook.md:166,186 (`15 * * * *`)
Aufwand: S
Risiko: trivial
Voraussetzungen: keine
Test-Strategie: keine
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Begründung:** Nicht funktional, aber Doku ist Wahrheitsquelle. Drift behindert künftige Operator*innen.

---

###: Open-Redirect-Helper zentralisieren

```yaml
ID: R-010
Titel: safe_redirect_path aus sudo_mode in views/utils heben
Kategorie: Redesign (Bug-Fix; lehnt //evil ab)
Dimension: 2 (Views), 8 (Sicherheit)
Fundstelle(n):
  - src/core/views/workitem_actions.py:61-63 (kaputt)
  - src/core/views/sudo_mode.py:25-32 (Vorbild)
Aufwand: S
Risiko: niedrig — engt nur Pfad-Akzeptanz ein.
Voraussetzungen: Test schreiben (parametrisches Fuzz)
Test-Strategie:
  - parametrisch über ["/", "/x", "//evil", "///e", "javascript:alert(1)", "http://x", "/x/../../y"]
  - Architektur-Test, der `redirect(<unvalidiertes next>)` per AST-Walk verbietet
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (`workitem_actions.py:61-63`):

```python
next_url = request.POST.get("next")
if next_url and next_url.startswith("/"):
    return redirect(next_url)  # ← matcht auch //evil.com
return redirect("core:workitem_inbox")
```

**Nachher:**

```python
# views/utils.py (NEU)
def safe_redirect_path(raw: str | None) -> str:
    """Open-Redirect-Schutz: nur same-origin Pfade akzeptieren."""
    if not raw:
        return ""
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return ""

# views/workitem_actions.py:
from core.views.utils import safe_redirect_path
next_url = safe_redirect_path(request.POST.get("next"))
if next_url:
    return redirect(next_url)
return redirect("core:workitem_inbox")
```

`views/sudo_mode.py:25-32` (`_safe_next`) auf den neuen Helper umstellen, identisches Verhalten.

**Begründung:** Pattern existiert bereits in `sudo_mode.py:30` korrekt (`not raw.startswith("//")`), in `workitem_actions.py:62` vergessen. Phishing-Vektor mit „authentisch wirkender App-URL".

---

###: File-Upload Default-Whitelist als Fail-Closed

```yaml
ID: R-011
Titel: Default-Whitelist für File-Upload bei Settings.DoesNotExist
Kategorie: Redesign (Verhaltensänderung: heute fail-open, künftig fail-closed)
Dimension: 2, 8
Fundstelle(n):
  - src/core/services/file_vault.py:131-134 (Service-Layer)
  - src/core/forms/events.py:198-200 (Form-Layer)
Aufwand: S
Risiko: niedrig (restriktiver, nicht permissiver)
Voraussetzungen: Test schreiben (Settings null/empty → reject)
Test-Strategie: 3 Cases: null Settings, empty allowed_file_types, normal Settings (regression).
Migrations-Bedarf: ja (post-migrate-Signal: jede Facility hat genau eine Settings-Zeile)
Reversibilität: trivial bei Code, schwerer bei post-migrate-Signal.
```

**Vorher** (`file_vault.py:131-134`):

```python
try:
    facility_settings = Settings.objects.get(facility=facility)
except Settings.DoesNotExist:
    return  # No settings yet → no whitelist to enforce.

allowed = {ext.strip().lower().lstrip(".") for ext in
           (facility_settings.allowed_file_types or "").split(",") if ext.strip()}
if not allowed:
    return  # ← zweiter fail-open
```

**Nachher:**

```python
# core/constants.py (NEU)
DEFAULT_ALLOWED_FILE_TYPES = frozenset({"pdf", "jpg", "jpeg", "png", "docx", "odt"})
DEFAULT_MAX_FILE_SIZE_MB = 10

# services/file_vault.py:
from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
try:
    facility_settings = Settings.objects.get(facility=facility)
    allowed = {ext.strip().lower().lstrip(".") for ext in
               (facility_settings.allowed_file_types or "").split(",") if ext.strip()}
    if not allowed:
        allowed = DEFAULT_ALLOWED_FILE_TYPES
except Settings.DoesNotExist:
    allowed = DEFAULT_ALLOWED_FILE_TYPES
```

Identisch in `forms/events.py:198-200`.

**Begründung:** Heute erzeugt eine Facility ohne Settings (Race oder Bug bei `setup_facility`) ein Whitelist-Loch. Hardcoded Default ist immer da, Operator kann es per Settings überschreiben — heutiges Soll-Verhalten erhalten, Fail-Mode geschlossen.

---

###: SSRF-Validator für Breach-Webhook

```yaml
ID: R-012
Titel: Webhook-URL gegen Schema + private IPs filtern
Kategorie: Redesign (Bug-Fix mit Verhaltensänderung)
Dimension: 8
Fundstelle(n): src/core/services/breach_detection.py:155-171 (`# noqa: S310`)
Aufwand: S
Risiko: niedrig (rein restriktiv)
Voraussetzungen: keine
Test-Strategie:
  - parametrisch über
    ["file://x", "http://127.0.0.1", "http://169.254.169.254", "http://10.0.0.1",
     "http://192.168.0.1", "gopher://x", "ftp://x", "https://valid.example/hook"]
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```python
def _post_webhook(payload):
    url = settings.BREACH_NOTIFICATION_WEBHOOK_URL
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), ...)
    urllib.request.urlopen(req, timeout=5)  # noqa: S310
```

**Nachher:**

```python
import socket, ipaddress
from urllib.parse import urlparse

def _validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"https"}:
        raise ValueError(f"Webhook scheme {parsed.scheme} not allowed (https only).")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError) as exc:
        raise ValueError(f"Webhook host unresolvable: {parsed.hostname}") from exc
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError(f"Webhook target {ip} is private/loopback.")

def _post_webhook(payload):
    url = settings.BREACH_NOTIFICATION_WEBHOOK_URL
    _validate_webhook_url(url)
    req = urllib.request.Request(url, ...)
    urllib.request.urlopen(req, timeout=5)
```

Identische Prüfung in der Settings-Form, damit fehlerhafte URLs gar nicht erst in der DB landen.

**Begründung:** `# noqa: S310` dokumentiert die Annahme „Operator-konfiguriert ⇒ vertrauenswürdig". Das stimmt nur, wenn der Operator nie eine Cloud-Metadata-URL einträgt — pre-Auth-Webhook ohne Schema/IP-Check ist Standard-SSRF.

---

###: Sudo-Mode in `prod.py` per `ImproperlyConfigured` schützen

```yaml
ID: R-013
Titel: SUDO_MODE_ENABLED=False in Produktion verhindern
Kategorie: Redesign (Verhaltensänderung: Server startet bei Fehlkonfig nicht mehr)
Dimension: 8
Fundstelle(n): src/core/services/sudo_mode.py:67-69, src/anlaufstelle/settings/prod.py
Aufwand: S
Risiko: niedrig (engt Konfig ein, hilft Operator)
Voraussetzungen: keine
Test-Strategie: Architektur-Test `test_prod_settings_sudo_mode_required`.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (`services/sudo_mode.py:67-69`):

```python
def dispatch(self, request, *args, **kwargs):
    if not getattr(settings, "SUDO_MODE_ENABLED", True):
        return super().dispatch(request, *args, **kwargs)  # ← Bypass
    ...
```

**Nachher** (zusätzlich in `settings/prod.py`):

```python
from django.core.exceptions import ImproperlyConfigured
if not env.bool("SUDO_MODE_ENABLED", default=True):
    raise ImproperlyConfigured(
        "SUDO_MODE_ENABLED muss in Produktion True sein. "
        "Test-Setting versehentlich übernommen?"
    )
```

**Begründung:** Heute kippt ein versehentlicher `.env`-Eintrag drei Defenses (MFA-Disable, DSGVO-Export, Pseudonym-Daten-Download) in einem Schritt. `prod.py` liest die meisten Sicherheits-Settings ähnlich strikt — Konsistenz herstellen.

---

###: Passwort-Mindestlänge auf 12

```yaml
ID: R-014
Titel: MinimumLengthValidator OPTIONS={"min_length": 12}
Kategorie: Redesign (Verhaltensänderung für neue/geänderte Passwörter)
Dimension: 8
Fundstelle(n): src/anlaufstelle/settings/base.py:127-132
Aufwand: S
Risiko: niedrig (gilt nur für neue/geänderte Passwörter)
Voraussetzungen: keine
Test-Strategie: Form-Validator-Test (10-Zeichen-PW abgelehnt, 12 OK).
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```python
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    ...
]
```

**Nachher:**

```python
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},  # explicit, BSI/NIST-aligned for §203-Daten
    },
    ...
]
```

**Begründung:** Django-Default 8 ist zu kurz für Klartext-Sozialdaten. Initial-Passwort-Generator (`services/password.py:10-13`) verwendet bereits 12 Zeichen — User-Passwörter müssen mindestens dasselbe Niveau erreichen.

---

###: IP-Spoof in Maintenance-Allowlist fixen

```yaml
ID: R-015
Titel: _client_ip in maintenance.py durch get_client_ip aus signals/audit ersetzen
Kategorie: Redesign (Bug-Fix mit Verhaltensänderung)
Dimension: 8
Fundstelle(n): src/core/middleware/maintenance.py:81-86, src/core/signals/audit.py:15-48 (Vorbild)
Aufwand: S
Risiko: niedrig
Voraussetzungen: keine
Test-Strategie: E2E-Test mit gespooftem `X-Forwarded-For: <ops-ip>, <bösartig>` → Wartungsmauer hält.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (`middleware/maintenance.py:81-86`, ungeprüft, mutmaßlich):

```python
def _client_ip(self, request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()  # ← erstes Element, vom Client setzbar
    return request.META.get("REMOTE_ADDR", "")
```

**Nachher:**

```python
from core.signals.audit import get_client_ip  # respektiert TRUSTED_PROXY_HOPS

def _client_ip(self, request):
    return get_client_ip(request)
```

**Begründung:** `signals/audit.get_client_ip` zählt Proxy-Hops von rechts (Caddy → Gunicorn) — die einzige korrekte Variante mit Trust-Boundary. Maintenance ignoriert die Variable und nimmt das erste Element.

---

## C. Strukturelle Refactorings (M/L)

> 10 Einträge, in vorgeschlagener Reihenfolge. Erwartung: Aufwand zwischen ½ Tag (M) und 3 Tagen (L). Tests müssen vor jedem Eintrag stehen, sonst wird's Redesign.

---

###: Charakterisierungstests vor allem anderen

```yaml
ID: R-101
Titel: 8 Charakterisierungstests für die folgenden 9 Refactorings
Kategorie: Refactoring-Vorbereitung
Dimension: 5
Fundstelle(n):
  - src/tests/test_safe_redirect_helper.py (NEU, 1 Datei)
  - src/tests/test_client_anonymize_characterization.py (NEU)
  - src/tests/test_retention_strategies_unit.py (NEU)
  - src/tests/test_event_service_isolation.py (NEU)
  - src/tests/test_file_vault_failclosed.py (NEU)
  - src/tests/test_retention_k_anonymization.py (NEU)
  - src/tests/test_breach_webhook_ssrf.py (NEU)
  - src/tests/test_htmx_partial_mixin.py (NEU)
Aufwand: M (8 Test-Dateien × ~3-5 Cases)
Risiko: niedrig — keine Code-Änderung.
Voraussetzungen: keine
Test-Strategie: Tests selbst sind die Strategie.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Begründung:** Ohne diese Tests sind... keine Refactorings, sondern Vertrauensvorschüsse. „Tests bleiben grün" ist die Definition von Refactoring i. e. S. — also gehören die Tests vorher.

**Vorher/Nachher entfällt** (8 neue Dateien). Test-Skizzen pro Datei stehen im verwandten Plan-1-Dokument unter §11/§12.

---

###: HTMX-Partial-Mixin pilotieren (`ClientListView` + `CaseListView`)

```yaml
ID: R-102
Titel: HTMXPartialMixin auf ClientListView + CaseListView aktivieren
Kategorie: Refactoring (Verhalten gleich, nur Pfad anders)
Dimension: 3 (HTMX)
Fundstelle(n):
  - src/core/views/mixins.py:61-83 (Mixin existiert)
  - src/core/views/clients.py, src/core/views/cases.py (Pilot-Targets)
  - 11 weitere Views mit demselben Branching (`audit.py:70`, `events.py:63`, `workitems.py`, `search.py`, `statistics.py`, `retention.py` ×2, `workitem_bulk.py`, `attachments.py`, `cases.py:98`, `clients.py:73`)
Aufwand: M (2 Views × 30 min + Test-Pattern)
Risiko: niedrig
Voraussetzungen: R-101 (test_htmx_partial_mixin.py)
Test-Strategie: pro umgestellter View 2 Cases (HX-Request true/false → erwartetes Template).
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (typisch, `views/clients.py:71-77`):

```python
class ClientListView(AssistantOrAboveRequiredMixin, View):
    def get(self, request):
        # ... context ...
        if request.headers.get("HX-Request"):
            return render(request, "core/clients/partials/table.html", context)
        return render(request, "core/clients/list.html", context)
```

**Nachher:**

```python
class ClientListView(AssistantOrAboveRequiredMixin, HTMXPartialMixin, View):
    template_name = "core/clients/list.html"
    partial_template_name = "core/clients/partials/table.html"

    def get(self, request):
        # ... context ...
        return self.render_htmx_or_full(context)
```

**Begründung:** Mixin ist bereits geschrieben (`views/mixins.py:61-83`), aber **0 Verwendungen**. Pilot zeigt Aufwand für die übrigen 11 Views. Pattern macht spätere Anpassungen am HTMX-Header-Check (z. B. `HX-Boosted` für progressive enhancement) auf 1 Stelle.

---

###: `Client.anonymize` → `services/clients.anonymize_client`

```yaml
ID: R-103
Titel: Client.anonymize Body in Service-Layer verlagern
Kategorie: Refactoring (Move Method) — Verhalten gleich
Dimension: 1, 2 (Code-Level + Models)
Fundstelle(n): src/core/models/client.py:105-203
Aufwand: M (1-3 PT, mit Tests)
Risiko: mittel — Trigger-Bypass via `SET LOCAL session_replication_role = replica`. Charakterisierungstests aus R-101 sind Sicherheitsnetz.
Voraussetzungen: R-101 (test_client_anonymize_characterization.py)
Test-Strategie:
  - 3 Charakterisierungs-Cases: plain, with-attachments, with-deletion-request
  - jeweils Trigger-State-Snapshot vor/nach (`SHOW session_replication_role`).
Migrations-Bedarf: nein
Reversibilität: mit Aufwand (alle Aufrufer auf neuen Service umstellen)
```

**Vorher** (`models/client.py:105-203`, vereinfacht):

```python
class Client(models.Model):
    ...
    def anonymize(self, *, user=None):
        from core.services.file_vault import delete_event_attachments  # ← inline
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica;")  # ← raw SQL
                # ... 7 Aggregate-Eingriffe (Case, Episode, WorkItem, Event,
                # ... EventHistory, EventAttachment, DeletionRequest) ...
                delete_event_attachments(events_qs)
                self.k_anonymized = True
                ...
                self.save()
```

**Nachher:**

```python
# services/_db_admin.py (NEU)
@contextmanager
def with_replica_role():
    """Bypasst Trigger lokal innerhalb einer Transaktion (für Anonymisierung).

    Begründung: AuditLog/EventHistory-Trigger blockieren UPDATE/DELETE als
    Standard-Schutz. Anonymisierung ist die einzige zugelassene Ausnahme;
    siehe ADR-009 (TODO).
    """
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica;")
        yield

# services/clients.py
def anonymize_client(client, *, user=None):
    with transaction.atomic(), with_replica_role():
        _anonymize_cases(client)
        _anonymize_episodes(client)
        _anonymize_workitems(client)
        events = _anonymize_events(client)
        _redact_event_history(events)
        delete_event_attachments(events)
        _close_deletion_requests(client)
        client.k_anonymized = True
        client.save()

# models/client.py
class Client(models.Model):
    ...
    def anonymize(self, *, user=None):
        """Deprecated: nutze services.clients.anonymize_client."""
        from core.services.clients import anonymize_client
        anonymize_client(self, user=user)
```

**Begründung:** Modell-Layer kennt heute Trigger-Topologie der DB und macht Service-Imports im Body. Verstößt gegen ADR-002. Service-Variante macht das Verhalten testbar pro Subschritt (`_anonymize_*` Helper) und verfügbar für andere Aufrufer (Retention, Bulk-Anonymisierung). `Client.anonymize` bleibt als 1-Zeilen-Delegation für Rückwärtskompatibilität.

---

###: `services/event.py` aufteilen

```yaml
ID: R-104
Titel: services/event.py (683 LOC, 5 Concerns) in 4 Submodule
Kategorie: Refactoring (Move Method/Class) — Verhalten gleich
Dimension: 1
Fundstelle(n): src/core/services/event.py (komplett)
Aufwand: M (1-3 PT)
Risiko: mittel — viele Imports im Code-Base, alle zu prüfen.
Voraussetzungen: R-101 (test_event_service_isolation.py — Tests gegen Service-Schnittstellen, nicht View-getrieben)
Test-Strategie: Re-Export-Hub als Brücke; bestehende View-Tests bleiben unverändert grün.
Migrations-Bedarf: nein
Reversibilität: mit Aufwand
```

**Vorher** (Modul mit 23 Funktionen, 5 Concerns):

```text
services/event.py  (683 LOC)
├── build_field_template_lookup       # Concern: fields
├── filtered_server_data_json         # Concern: sensitivity-filter
├── normalize_file_marker             # Concern: file-marker-parsing
├── create_event / update_event       # Concern: CRUD
├── soft_delete_event
├── request_deletion / approve_deletion / reject_deletion  # Concern: 4-Augen
└── ... (16 weitere)
```

**Nachher:**

```text
services/events/
├── __init__.py    # Re-Export-Hub: alle bisherigen Symbole
├── crud.py        # create_event, update_event, soft_delete_event
├── context.py     # build_event_detail_context, filtered_server_data_json
├── deletion.py    # request_deletion, approve_deletion, reject_deletion
└── fields.py      # build_field_template_lookup, normalize_file_marker, remove_restricted_fields
```

```python
# services/events/__init__.py
from .crud import create_event, update_event, soft_delete_event
from .context import build_event_detail_context, filtered_server_data_json
from .deletion import request_deletion, approve_deletion, reject_deletion
from .fields import build_field_template_lookup, normalize_file_marker, remove_restricted_fields

__all__ = [...]
```

**Aufrufer** (`from core.services.event import …`) bleiben unverändert dank Re-Export-Hub. Schrittweise auf direkten Import (`from core.services.events.crud import create_event`) migrieren — kein Druck.

**Begründung:** Single-Modul mit 5 Concerns macht jede Änderung gefährlich (unbeabsichtigte Side-Effects auf andere Concerns). Datei-Naming markiert die fachliche Trennlinie sichtbar.

---

###: `services/retention.py` Strategy-Konsolidierung

```yaml
ID: R-105
Titel: 4 Retention-Strategien aus 3 Stellen in 1 Generator
Kategorie: Refactoring (Replace Conditional with Polymorphism)
Dimension: 1
Fundstelle(n): src/core/services/retention.py:485-551 (collect_doomed_events), :612-740 (4× enforce_*), :861-973 (create_proposals_for_facility)
Aufwand: L (1 Sprint)
Risiko: hoch — Drift zwischen Vorhersage und Ausführung muss durch Tests abgesichert sein.
Voraussetzungen: R-101 (test_retention_strategies_unit.py mit Cross-Strategy-Intersection und Boundary-Cases)
Test-Strategie:
  - jede Strategy einzeln (4× pos/neg/boundary)
  - Cross-Strategy-Intersection: Event in 2 Kategorien → genau 1 Löschung
  - dry_run vs. real: identische Mengen.
Migrations-Bedarf: nein
Reversibilität: praktisch reversibel (kein Datenmodell-Schritt)
```

**Vorher** (`retention.py:485-551`, ein 67-Zeilen-Block, der **dreimal** in ähnlicher Form auftaucht):

```python
def collect_doomed_events(facility, settings_obj, now):
    """IMPORTANT: Keep in sync with enforce_anonymous, enforce_identified,
    enforce_qualified, and enforce_document_type_retention."""
    held_ids = get_active_hold_target_ids(facility, "Event")
    combined = Event.objects.none()

    # Strategy 1: Anonymous
    cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
    combined = combined | Event.objects.filter(
        facility=facility, is_anonymous=True, is_deleted=False,
        occurred_at__lt=cutoff_anon,
    )

    # Strategy 2: Identified
    cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
    identified_clients = Client.objects.filter(
        facility=facility, contact_stage=Client.ContactStage.IDENTIFIED,
    )
    # ... (analoger Block für Strategy 3, 4) ...
```

**Nachher:**

```python
@dataclass(frozen=True)
class RetentionStrategy:
    name: str  # "anonymous" | "identified" | "qualified" | "document_type"
    cutoff_attr: str | None
    audit_label: str
    def queryset(self, facility, settings_obj, now) -> QuerySet[Event]: ...

def _build_strategies(settings_obj) -> list[RetentionStrategy]:
    return [
        AnonymousRetention(),
        IdentifiedRetention(),
        QualifiedRetention(),
        DocumentTypeRetention(),
    ]

def collect_doomed_events(facility, settings_obj, now):
    held_ids = get_active_hold_target_ids(facility, "Event")
    combined = Event.objects.none()
    for strategy in _build_strategies(settings_obj):
        combined = combined | strategy.queryset(facility, settings_obj, now)
    return combined.exclude(id__in=held_ids)

def enforce_retention(facility, settings_obj, now, *, dry_run=False):
    counts = {}
    for strategy in _build_strategies(settings_obj):
        qs = strategy.queryset(facility, settings_obj, now)
        counts[strategy.name] = qs.count()
        if not dry_run:
            _soft_delete_events(qs, audit_label=strategy.audit_label)
    return counts

def create_proposals_for_facility(facility, settings_obj, now):
    return [
        RetentionProposal(strategy=s.name, count=s.queryset(facility, settings_obj, now).count())
        for s in _build_strategies(settings_obj)
    ]
```

**Begründung:** Heute ist die Drift-Vermeidung als **Kommentar** zementiert (Z.488: „IMPORTANT: Keep in sync"). Bei dem Volumen (974 LOC) ist das fragil. Drei Konsumenten teilen einen Bauplan. Plus: `enforce_*`-Einzelfunktionen können entfallen, wenn `enforce_retention(..., only=["anonymous"])` reicht — kein hartes Muss.

---

###: `EventCreateView`/`UpdateView` schlanker

```yaml
ID: R-106
Titel: Attachments-Marker-Normalisierung und Template-Default-Logik in Service
Kategorie: Refactoring (Move Method)
Dimension: 2 (Views)
Fundstelle(n): src/core/views/events.py:96-167 (CreateView.get), :305-358 (UpdateView.get)
Aufwand: S (1-3 h)
Risiko: niedrig
Voraussetzungen: R-101 (test_event_service_isolation.py)
Test-Strategie: Service-Funktionen (`prepare_initial_context`, `build_attachment_context`) direkt unit-testen.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (Auszug `views/events.py:325-351`):

```python
class EventUpdateView(StaffRequiredMixin, View):
    def get(self, request, pk):
        event = get_object_or_404(Event, pk=pk, facility=request.current_facility)
        existing_attachments_by_slug = {}
        for slug, value in (event.data_json or {}).items():
            entries_meta = normalize_file_marker(value)
            for meta in entries_meta:
                # ... 26 LOC Marker-Anreicherung ...
        # weiteres Bauen von initial_data ...
        return render(request, "core/events/edit.html", {...})
```

**Nachher:**

```python
# services/events/context.py
def build_attachment_context(event):
    """Sammelt Attachments-Metadaten für die Edit-Maske (Legacy + neue Marker)."""
    out = {}
    for slug, value in (event.data_json or {}).items():
        for meta in normalize_file_marker(value):
            ... # Logik 1:1 aus der View
    return out

# views/events.py
class EventUpdateView(StaffRequiredMixin, View):
    def get(self, request, pk):
        event = get_object_or_404(Event, pk=pk, facility=request.current_facility)
        return render(request, "core/events/edit.html", {
            "event": event,
            "attachments_by_slug": build_attachment_context(event),
            ...
        })
```

**Begründung:** View ist heute kein „dünner Wrapper", sondern enthält 50+ Zeilen Business-Logik (Marker-Normalisierung, Default-Doc-Type-Auflösung). Das verstößt gegen ADR-002 und blockiert reine Service-Tests.

---

###: Pagination-Mixin extrahieren + WorkItem-Inbox cappen

```yaml
ID: R-107
Titel: PaginatedListMixin als Pendant zu HTMXPartialMixin
Kategorie: Refactoring (Extract Class) + kleines Redesign (WorkItem-Inbox bekommt erstmals Cap)
Dimension: 2, 6
Fundstelle(n):
  - src/core/views/clients.py:53-59, src/core/views/cases.py:85-86 (DEFAULT_PAGE_SIZE-Pattern)
  - src/core/views/audit.py:49-50 (hartcodiert 50)
  - src/core/views/workitems.py (kein Cap)
Aufwand: M
Risiko: mittel — WorkItem-Inbox-Wechsel ändert UX (200+ Items werden jetzt paginiert).
Voraussetzungen: R-102 (HTMXPartialMixin als Vorbild)
Test-Strategie: Page-Boundary (page=0, page=999999), Filter+Pagination kombiniert, leere Listen.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher** (drei Stellen, drei Patterns):

```python
# views/clients.py:53-59
page_size = DEFAULT_PAGE_SIZE
page_obj = Paginator(qs, page_size).get_page(safe_page_param(request))

# views/cases.py:85-86
... # identisch

# views/audit.py:49-50
page_obj = Paginator(queryset, 50).get_page(request.GET.get("page"))  # ← hartcodiert

# views/workitems.py
... # gar keine Pagination
```

**Nachher:**

```python
# views/mixins.py
class PaginatedListMixin:
    page_size = DEFAULT_PAGE_SIZE  # default 25; pro View überschreibbar

    def paginate(self, queryset, request):
        return Paginator(queryset, self.page_size).get_page(safe_page_param(request))

# views/clients.py
class ClientListView(..., PaginatedListMixin, View):
    page_size = DEFAULT_PAGE_SIZE  # explizit, wenn anders

# views/audit.py
class AuditLogView(..., PaginatedListMixin, View):
    page_size = 50

# views/workitems.py
class WorkItemInboxView(..., PaginatedListMixin, View):
    page_size = 25
```

**Begründung:** 3 leicht unterschiedliche Implementierungen heute, eine fehlt komplett. Mixin macht Pagination zentral änderbar (z.B. später ?per_page=N erlauben).

---

###: `reencrypt_fields`-Command auf `EventHistory` + `EventAttachment` ausweiten

```yaml
ID: R-108
Titel: Re-Encrypt-Pfad deckt alle Schichten mit Klartext-im-Encrypted-Field
Kategorie: Refactoring (Erweiterung, Verhalten erhalten pro Field)
Dimension: 4 (Datenmodell)
Fundstelle(n): src/core/management/commands/reencrypt_fields.py
Aufwand: M
Risiko: mittel — Re-Encrypt mit großen Datenmengen ist langsam; Drill mit alten Keys nötig.
Voraussetzungen: keine Test-Lücken-Pflicht, aber Test mit kleinem Sample-Set ratsam.
Test-Strategie: Migration-Test mit 100 Events + 50 EventHistory + 20 EventAttachment, alle mit altem Key verschlüsselt → Re-Encrypt → mit neuem Key lesbar, alter Key entfernen.
Migrations-Bedarf: nein
Reversibilität: trivial (Multi-Key-Liste schützt)
```

**Vorher:** Command iteriert nur `Event`. Bei Key-Wechsel müssen alle alten Keys in `ENCRYPTION_KEYS` verbleiben.

**Nachher:** Command iteriert `Event`, `EventHistory`, `EventAttachment` mit denselben Re-Encrypt-Schritten. Alte Keys können nach `reencrypt_fields --confirm-rotation` aus der Liste fallen.

**Begründung:** Heute ist eine komplette Key-Rotation effektiv unmöglich, weil EventHistory + Attachment-Encrypted-Felder den alten Key festhalten. Die Multi-Key-Liste löst das nur kosmetisch.

---

###: Ruff-Regeln schrittweise erweitern

```yaml
ID: R-109
Titel: Ruff select um B, UP, SIM, N, S erweitern (per-file-ignores)
Kategorie: Cleanup-Cluster (über Sprint hinweg)
Dimension: 1
Fundstelle(n): pyproject.toml:14-15 (`select = ["E", "F", "I", "W"]`)
Aufwand: M (Regeln × Codebase, 5-20 Findings)
Risiko: niedrig
Voraussetzungen: R-005 (pre-commit) — sonst kommen Findings nur per CI an
Test-Strategie: bestehende Tests grün; CI-Lint-Job grün.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Nachher** (`pyproject.toml`):

```toml
[tool.ruff.lint]
select = [
    "E", "F", "I", "W",   # heute aktiv
    "B",                  # bugbear (mutable defaults, redundante exception-handler)
    "UP",                 # pyupgrade (Modern-Python-Idiome)
    "SIM",                # simplify (kondiotionale Vereinfachung)
    "N",                  # naming
    "S",                  # bandit/security (legacy `# noqa: S310` wird endlich gelesen)
]

[tool.ruff.lint.per-file-ignores]
"src/core/migrations/*" = ["S101", "B008"]  # assert in Tests, Default-Args ok
"src/tests/*" = ["S101", "S106"]
```

**Begründung:** Heute sind `# noqa: S310`-Marker im Code (z. B. `breach_detection.py:167`) totes Inventar — `S` ist nicht aktiv. Plus: `B` (Bugbear) fängt Bug-Klassen, die Tests übersehen.

---

###: AGPL-§13-Footer aus Settings rendern

```yaml
ID: R-110
Titel: SOURCE_CODE_URL aus Settings statt hartcodiert in base.html
Kategorie: Refactoring (kleine Auslagerung; Verhalten für Original-Setup gleich)
Dimension: 2 (Templates), 8 (Settings)
Fundstelle(n): src/templates/base.html:228 (hartcodiert auf upstream)
Aufwand: S
Risiko: niedrig
Voraussetzungen: keine
Test-Strategie: Template-Render-Test mit `SOURCE_CODE_URL` env-var.
Migrations-Bedarf: nein
Reversibilität: trivial
```

**Vorher:**

```html
<a href="https://github.com/anlaufstelle/app">{% trans "Quellcode" %}</a>
```

**Nachher:**

```python
# settings/base.py
SOURCE_CODE_URL = env("SOURCE_CODE_URL", default="https://github.com/anlaufstelle/app")
SOURCE_CODE_VERSION = env("SOURCE_CODE_VERSION", default="")  # optional Commit-SHA
```

```html
<a href="{{ SOURCE_CODE_URL }}">{% trans "Quellcode" %}</a>
{% if SOURCE_CODE_VERSION %}
  <span class="text-[10px] text-ink-muted">{{ SOURCE_CODE_VERSION|truncatechars:8 }}</span>
{% endif %}
```

**Begründung:** AGPL-§13 verlangt Quellcode-Link. Forks/Self-Hoster vergessen heute, den hartcodierten Link anzupassen — und liefern damit technisch falsche §13-Erklärung.

---

## D. Redesigns (XL, mit Vorsicht)

> 5 Redesigns. Hier KEIN Druck zur Umsetzung. Pro Eintrag drei Optionen mit Trade-offs.

---

### D-201: K-Anonymisierung — Anschluss, Entfernen oder Bewusst-Halbfertig

```yaml
ID: D-201
Titel: Settings.retention_use_k_anonymization ist Dead Code
Kategorie: Redesign (verändert Retention-Verhalten — egal welche Option)
Dimension: 4
Fundstelle(n): src/core/migrations/0049_k_anonymization.py, src/core/services/retention.py:771-806, src/core/models/client.py:205-214
Aufwand: S (Pfad A) | M (Pfad B) | 0 (Pfad C)
Risiko: hoch (Pfad A: ändert Retention-Verhalten in Produktion) | mittel (Pfad B: Schema-Änderung, Migration) | hoch (Pfad C: Compliance-Glaubwürdigkeit)
```

**Aktueller Zustand:** Setting + `Client.k_anonymized` + `services/k_anonymization.k_anonymize_client` existieren. `enforce_retention` ruft weiterhin `client.anonymize` (Hard-Delete-Cascade). Das Setting wirkt **nirgends**.

**Option A — Anschließen:**

```python
# services/retention.py:794 (Vorher: client.anonymize() unbedingt)
def anonymize_clients(facility, settings_obj, now):
    candidates = ...
    for client in candidates:
        if settings_obj.retention_use_k_anonymization:
            k_anonymize_client(client, k=settings_obj.k_anonymity_threshold)
        else:
            client.anonymize()
```
**Trade-off:** Pilot-Konfiguration mit `retention_use_k_anonymization=True` ändert Retention-Output. Zwei Verhaltensvarianten zu pflegen.

**Option B — Entfernen:**
Migration entfernt Setting, `Client.k_anonymized`-Feld, `services/k_anonymization.py`, ADR „warum wieder rausgeflogen" schreiben.
**Trade-off:** Investierte Arbeit verworfen. K-Anon-Argument gegenüber Aufsicht entfällt.

**Option C — Bewusst halbfertig lassen + dokumentieren:**
Setting in Admin-UI ausgrauen mit Hinweis „in Vorbereitung", FAQ-Eintrag erläutert den Stand.
**Trade-off:** Schein bleibt, Erwartung muss von Hand gemanagt werden.

**Migrationspfad:** Pfad A → Charakterisierungstests zeigen heute, dass das Setting nichts tut. PR enthält Conditional + Test-Update + FAQ + ADR. Pfad B → Migration `0080_remove_k_anonymization.py` mit `RunPython` (Daten-Migration: `k_anonymized=True`-Clients via Hard-Delete final anonymisieren).

**Empfehlung:** **Option A**, **wenn Tobias** das Pilot-Verhalten ändern darf. Sonst **Option C** als Übergang. Option B nur, wenn der-Förder-Plan k-Anon explizit aufgegeben hat.

---

### D-202: AuditLog-Pruning ohne `DISABLE TRIGGER`

```yaml
ID: D-202
Titel: SIGKILL während Pruning → Trigger bleibt disabled
Kategorie: Redesign (Mechanismus ändert sich)
Dimension: 4
Fundstelle(n): src/core/services/retention.py:822-858
Aufwand: M
Risiko: mittel — Database-Function-Änderung, RLS-/Trigger-Audit nötig.
```

**Aktueller Zustand:**

```python
# retention.py:822-858 (Auszug)
def prune_auditlog(...):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable;")
            try:
                # ... Delete-Statements ...
                deleted_count = ...
            finally:
                cursor.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable;")
```

Bei `SIGKILL` läuft `finally` nicht. Trigger bleibt disabled bis manueller Eingriff.

**Option A — `SECURITY DEFINER`-Funktion:** Eine PL/pgSQL-Funktion mit `SECURITY DEFINER` setzt `session_replication_role = replica` lokal in ihrer eigenen Transaktion und führt das Delete aus. Trigger werden gar nicht erst disabled — nur die Session-Variable in genau dieser Transaktion ist anders.
**Trade-off:** PL/pgSQL-Funktion ist Datenmodell-Schritt (Migration), nicht reine Code-Änderung.

**Option B — Watchdog/Recovery-Hook:** Bei `apps.ready` prüft Django, ob `auditlog_immutable` aktiv ist. Falls nicht → Logge `CRITICAL` und re-enable. Plus Sentry-Capture.
**Trade-off:** Reaktiv statt präventiv. Die Tatsache der Disable-Lücke bleibt; Heilung nur beim nächsten Startup.

**Option C — Beibehalten + `pg_isolation_level=serializable` für Pruning + Health-Check:** Kein Code-Move, aber: Heute ist `finally` der einzige Schutz. Health-Endpoint prüft täglich, ob alle erwarteten Trigger `tgenabled='O'` sind, alarmiert sonst.
**Trade-off:** Detection statt Prevention. Schnellster Pfad.

**Empfehlung:** **A** für DSGVO-Argumentation, **C** als Sofortmaßnahme.

---

### D-203: Soft-Delete-Strategie

```yaml
ID: D-203
Titel: Soft-Delete inkonsistent (nur Event/Attachment)
Kategorie: Redesign (Schema-Änderung) ODER bewusste ADR-Dokumentation
Dimension: 4
Fundstelle(n): src/core/models/event.py:65 (`is_deleted`), src/core/models/attachment.py:34 (`deleted_at`), übrige 20 Models keine.
Aufwand: 0 (ADR) | L (Mixin + Migration für Case/Episode/WorkItem) | XL (Mixin + alle 22 Models)
Risiko: niedrig (ADR) | mittel (selektiv) | hoch (alle Models)
```

**Option A — ADR „warum nur Event soft-deletet":**
Festhalten als bewusste Entscheidung. Begründung: Event ist die einzige Entität, deren Löschung redaktionsfähig sein muss (DSGVO Art. 5 lit. e + § 67 SGB X) und die historische Bedeutung über die Lebenszeit der Klient*in hinaus hat. Andere Aggregate (Case/Episode/WorkItem) sind operationell — Löschung = endgültig.
**Trade-off:** Keine Schema-Änderung, aber jedes neue facility-gescopte Model muss die ADR explizit zitieren.

**Option B — Mixin + selektives Rollout:**
`SoftDeletableMixin` (`deleted_at: DateTimeField | None`, `deleted_by: FK(User) | None`, Manager-Filter). Auf `Case`, `Episode`, `WorkItem` ausrollen — die Aggregate, deren Soft-Delete Sozialarbeit-relevant ist. `Attachment.deleted_at` bleibt (vereinheitlichen mit Mixin später).
**Trade-off:** 3 Schema-Migrationen + alle Manager-/Query-Stellen anpassen + Test-Suite erweitern.

**Option C — Vollständig (alle 22 Models):**
Maximale Konsistenz, maximale Migration. **Nicht empfohlen** — viele Models (`Settings`, `DocumentType`, `FieldTemplate`, `RetentionRule`) haben keine fachliche Soft-Delete-Semantik.

**Empfehlung:** **A jetzt** (1 Sprint-Stunde), **B in 6 Monaten**, wenn der erste Pilot Use-Cases zeigt, in denen Case/WorkItem soft-deletet werden müssen.

---

### D-204: Multi-Worker-Konsistenz durch Redis-Cache

```yaml
ID: D-204
Titel: CACHES-Backend für Maintenance-Cache + Ratelimit
Kategorie: Redesign (neuer Service in Compose)
Dimension: 6, 8
Fundstelle(n): kein CACHES in src/anlaufstelle/settings/, Default = LocMem pro Prozess.
Aufwand: M
Risiko: mittel — Operator muss Redis betreiben.
```

**Aktueller Zustand:** Bei `GUNICORN_WORKERS > 1` hat jeder Worker eigenen LocMem. Maintenance-Cache (`middleware/maintenance.py:14, 45`) und django-ratelimit driften zwischen Workern.

**Option A — Redis-Service in Compose + `CACHES`-Konfig:**

```python
# settings/prod.py
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://redis:6379/1"),
    },
    "ratelimit": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://redis:6379/2"),
    },
}
```

```yaml
# docker-compose.prod.yml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes: [redis-data:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
```

**Trade-off:** Operator muss Redis betreiben (RAM ~50 MB, Persistenz optional). Ein zusätzliches Kontainer-Image.

**Option B — Nur `CACHES = {"default": "DatabaseCache"}` (Postgres):**
`python manage.py createcachetable` legt die Tabelle an. Keine zusätzliche Infrastruktur.
**Trade-off:** DB-Last für Cache-Operations. Bei Pilot-Größenordnung tolerierbar.

**Option C — Beibehalten + `GUNICORN_WORKERS=1` als Konfig-Empfehlung:**
Doku erklärt, dass mehr als 1 Worker ohne Redis Drift erzeugt. Operator entscheidet.
**Trade-off:** Skaliert nicht über einen Worker hinaus.

**Empfehlung:** **A für Pilot mit >1 Worker**, **B als Zwischenlösung** (DB-Cache ist erstaunlich gut für niedrige Schreibraten), **C nur dokumentiert**.

---

### D-205: Self-Hosting-Operatorlauf reparieren

```yaml
ID: D-205
Titel: setup_facility + NOSUPERUSER + Cron-Sidecar als One-Step
Kategorie: Redesign (Operator-Flow ändert sich)
Dimension: 8
Fundstelle(n): docs/coolify-deployment.md:84-100, docs/ops-runbook.md:172-193
Aufwand: M (Skripte) | L (Cron-Sidecar in Compose)
Risiko: mittel — bestehende Installationen müssen migrieren.
```

**Aktueller Zustand:**

1. Operator klont, fährt Compose, ruft `setup_facility` manuell, ruft `psql ALTER ROLE... NOSUPERUSER` manuell, legt Crontab manuell für Backup/Retention/Breach-Detection.
2. Frischer Stack läuft technisch, aber **ohne Backups**, **ohne Retention** (DSGVO Art. 5 lit. e), **ohne Breach-Detection** (Art. 33).

**Option A — `scripts/initial-setup.sh` + Cron-Sidecar:**

```bash
# scripts/initial-setup.sh (NEU)
docker compose run --rm web python manage.py setup_facility \
    --name "$FACILITY_NAME" --admin-email "$ADMIN_EMAIL"
docker compose run --rm db psql -c "ALTER ROLE anlaufstelle_user NOSUPERUSER;"
docker compose run --rm web python manage.py check --deploy
```

```yaml
# docker-compose.prod.yml (Cron-Sidecar)
cron:
  image: ghcr.io/anlaufstelle/app:${APP_VERSION:-latest}
  command: ["supercronic", "/etc/crontab"]
  volumes:
    - ./crontab:/etc/crontab:ro
    - backup-data:/var/lib/anlaufstelle/backup
```

**Trade-off:** Bestehende Installationen müssen Compose-File aktualisieren. Cron-Sidecar bedeutet ein zusätzliches laufendes Image (~100 MB RAM).

**Option B — Health-Endpoint warnt, ohne zu reparieren:**
Health-Endpoint zeigt `db_user_is_superuser=true`, `last_backup_age_hours=∞`, `cron_active=false` als `degraded`.
**Trade-off:** Detection only, Operator muss reagieren. Aber keine Compose-Änderung.

**Option C — In-Container Cron in Web-Service (nicht empfohlen):**
`supercronic` neben Gunicorn im selben Container. Ein Image, ein Restart-Pfad.
**Trade-off:** Container hat zwei Lebens-Verantwortungen — Anti-Pattern.

**Empfehlung:** **A**, mit B als Übergangsmaßnahme — A in einem Quartal, B sofort als Health-Endpoint-Erweiterung.

---

## E. Refactoring-Roadmap

| Sprint | Was | IDs | Output |
|---|---|---|---|
| **: Quick Wins + Sicherheits-Fixes** (1 Woche) | Cleanups, Bug-Fixes mit kleinem Surface |,,,,,,,,,,,,,, | 15 PRs, je ≤1 h |
| **: Charakterisierung + Pilot** (1 Woche) | 8 Charakterisierungstests, HTMX-Mixin als Pilot |, | 2 PRs (1 Test-Bündel, 1 Pilot) |
| **: Service-Aufteilung** (2 Wochen) | `Client.anonymize`, `services/event.py`, `services/retention.py` |,, | 3 PRs |
| **: Strukturverbesserung** (1 Woche) | View-Service-Extraktion, Pagination-Mixin, Re-Encrypt-Erweiterung |,, | 3 PRs |
| **: Tooling + Doku** (1 Woche) | Ruff, AGPL-Footer, ADRs, CoC + DCO |,, sowie CoC + 3 ADRs (File Vault, MFA, Search) | 5 PRs |
| **+: Redesigns** (zwischen und 6) | Maintainer-Entscheidungen | D-201, D-202, D-203, D-204, D-205 | je nach Option 1-3 PRs |

**Kritische Dependencies:**

```text
R-101 (Charakterisierungstests)
  ├── R-102 HTMX-Pilot
  ├── R-103 Client.anonymize-Move
  ├── R-104 event.py-Split
  ├── R-105 retention.py-Konsolidierung
  └── R-106 EventCreate/UpdateView-Service-Extraktion

R-005 pre-commit
  └── R-109 Ruff-Erweiterung
```

Quick Wins haben **keine** Voraussetzungen — können sofort in beliebiger Reihenfolge.

---

## F. Anti-Refactoring-Liste

> Code, der nicht ideal aussieht, aber bleiben soll. Pro Eintrag eine Begründung, die nicht „funktioniert eben" lautet.

### F-1: AuditLog Append-only-Trigger (Migration `0024_auditlog_immutable_trigger.py`)
**Begründung:** Gerichtsfest. Jede Änderung droht den 4-Schicht-Defense zu erodieren. Erweiterungen nur additiv per neuer Migration. R-D-202 ist explizit als „ändert den Disable-Mechanismus, nicht den Trigger" formuliert.

### F-2: PostgreSQL-RLS-Policies (Migration `0047_postgres_rls_setup.py`)
**Begründung:** **Defense-Layer #4** der 4-Schicht-Auth. Refactoring an `0047` ist mandantentrennungs-gefährlich. Neue facility-gescopte Models müssen die `EXPECTED_TABLES` in `test_rls.py` erweitern, nicht 0047.

### F-3: `test_rls_functional.py` (NOSUPERUSER-Cross-Tenant-Test)
**Begründung:** Testtechnisch vorbildlich, einmalig in der Django-Welt. Erweitern erlaubt, durch Mock-Versionen ersetzen verboten — würde den einzigen funktionalen Cross-Tenant-Beweis zerstören.

### F-4: Event-History Append-only-Trigger (Migrationen `0012`, `0074`)
**Begründung:** `0074_redact_legacy_eventhistory_delete.py` hat eine bekannte Lücke geschlossen (Klartext nach Löschung). Erneutes Anfassen führt mit hoher Wahrscheinlichkeit Compliance-Regressionen ein.

### F-5: Pseudonym-Klartext in `Client.pseudonym` (Issue [#717](https://github.com/anlaufstelle/app/issues/717))
**Begründung:** Bewusste Trade-off-Entscheidung gegen Trigram-Suche pro Facility. Real-Risiko niedrig (verschlüsselte Disk + RLS), Refactor-Aufwand hoch (Hash + Suche neu lösen). Bei Pilot-Datenbeobachtung re-evaluieren — heute kein Action-Item.

### F-6: Single-App-Architektur `core` (22 Models, 35 Services)
**Begründung:** Aufteilung in Bounded-Context-Apps wäre die richtige Architektur, lohnt sich aber bei dieser Größenordnung nicht. Modul-Naming innerhalb `services/` reicht. Vor (Plugin-Schnitt) angefasst, nicht jetzt — sonst doppelte Migration.

### F-7: `document_type.py:32-41 SystemType` (BAN, CRISIS, NEEDLE_EXCHANGE)
**Begründung:** Hartcodiert auf Streetwork. Plugin-fähig erst mit. **-Embargo respektieren** (siehe `CLAUDE.md:-Sperre`). Aktuelle Nutzer*innen leben mit den Choices, kein Pilot-Schmerz dokumentiert.

### F-8: Migration auf Generic-CBVs (`ListView`, `UpdateView`)
**Begründung:** 35 Views umzubauen kostet einen Sprint. Bestehender Code ist getestet, lesbar und stabil. Boy-Scout: für **neue** Views als Standard etablieren, bestehende lassen.

### F-9: `factory_boy` / `hypothesis`
**Begründung:** Heute lesbare, stabile Tests. Tooling-Wechsel ohne nachweisbaren Test-Smell ist Selbstzweck. Falls späterer Schmerz auftritt (Setup-Boilerplate, Validator-Bugs übersehen), erneut prüfen.

### F-10: Migrations-Squashing (Pre-1.0-Bereinigung)
**Begründung:** Pre-1.0 wirkt lockend, aber jede vorhandene Test-Installation müsste re-deployen. Reversibilität: praktisch keine. Erst, wenn 1.0 getagt ist und ein Migrations-Reset für alle Pilot-Installationen koordiniert wird.

### F-11: `Manager`-/`QuerySet`-Vereinheitlichung über Boilerplate hinaus
**Begründung:** `FacilityScopedManager` ist konsequent eingesetzt. Custom-QuerySets pro Domäne (`Event.objects.published`, `Client.objects.active`) wären Stilfrage, kein Architektur-Problem. Heutige `.filter`-Aufrufe sind lokal lesbar.

### F-12: Inline-Styles für Tailwind-Komponenten in `base.html`
**Begründung:** Tailwind generiert CSS-Klassen. Die wenigen `style="..."`-Stellen (z. B. `width: {{ percent }}%` für Progress-Bars) sind dynamisch — können nicht per Klasse gelöst werden. CSP-`style-src` darf `'unsafe-inline'` deshalb behalten (oder per `style-src 'self' 'nonce-...'` mit CSP-Nonce).

---

## G. Offene Fragen

> Diese Punkte sind aus dem Repo allein nicht entscheidbar. Maintainer-Antworten sind Voraussetzung für endgültige Empfehlungen.

1. **K-Anonymisierung (D-201): Pfad A, B oder C?** Ist Pilot-Verhaltensänderung erlaubt, oder soll das Setting bewusst bleiben? Antwort entscheidet, ob/ als Charakterisierung (Pfad C) oder als Erfolgs-Test (Pfad A) geschrieben wird.
2. **AuditLog-Pruning (D-202):** Ist die `SECURITY DEFINER`-Funktion akzeptabel im DSGVO-Argument („Trigger wird nicht disabled, nur in **dieser** Transaktion umgangen"), oder ist auch das schon zu viel?
3. **Soft-Delete (D-203):** Welche Aggregate sollen tatsächlich Soft-Delete-fähig sein? Case/Episode/WorkItem werden in der Praxis je gelöscht?
4. **CACHES (D-204):** Pilot-Größenordnung — wie viele Worker laufen heute? Wenn nur 1, ist `LocMem` aktuell ausreichend; dann ist D-204 vorerst Doku-Frage.
5. **Self-Hosting (D-205):** Wie viele Pilot-Installationen existieren bereits, die bei Compose-Änderungen koordiniert migrieren müssen? Beeinflusst, wie aggressiv `scripts/initial-setup.sh` das aktuelle Verhalten ablöst.
6. **-Embargo M0–M6:** Welche A11y-Punkte aus Plan-1 (RF-V/T-Block für Templates) dürfen vor M3 (WCAG-Audit) als reine Bug-Fixes durchgehen (z. B. Tabindex), welche sind „M3 only"?
7. **Bus-Factor:** Ist eine Co-Maintainership oder Eskalations-Klausel mit/Träger geplant? Beeinflusst Sprint-5-Doku (CoC + DCO).
8. **Django 6.0-Migration:** `requirements.txt` zeigt 6.0.4, vier Doku-Stellen 5.1. Ist der Tag im aktuellen Sprint, oder bleibt `[Unreleased]` länger? Beeinflusst (jetzt sync vs. beim Tag).
9. **Sprachleitlinie #604 in Models/Services/Audit-Choices:** Boy-Scout-Regel oder dedizierter Sprint? Audit-Action-Labels brauchen Migration mit Choice-Update.
10. **Pseudonym-Hashing-Strategie (F-5):** Pilot-Daten zeigen Re-Identifikations-Vektoren? Sonst bleibt #717 wie es ist.

---

*Audit-Ende. Erzeugt am 2026-04-30 durch Claude (Opus 4.7), zweite Fassung mit strikter Cleanup/Refactoring/Redesign-Trennung. Alle Code-Snippets sind Vorher/Nachher-Skizzen aus dem aktuellen Code-Stand `ec11530`. Zusätzlich liefert F (Anti-Refactoring) explizite Begründungen, warum 12 Bereiche nicht angefasst werden — die wichtigste Pointe dieses Plans.*

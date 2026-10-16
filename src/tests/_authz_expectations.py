"""AuthZ-Erwartungs-Tabelle — der deklarative Soll-Zustand Rolle × Endpoint (Refs #1055).

Einzige Soll-Quelle für:
- test_authz_matrix.py   (vertikale Matrix, Test-Client)
- test_authz_idor.py     (horizontale Mandantentrennung)
- e2e/test_authz_audit.py (Live-Audit + Report)

Jeder benannte URL-Pattern MUSS hier deklariert sein (Vollständigkeits-Gate).
Neue Endpoints: Eintrag ergänzen = bewusste AuthZ-Entscheidung dokumentieren.

Semantik:
- ``methods``: erlaubte Rollen je HTTP-Methode. Erlaubt heißt: Antwort ist
  weder 403/404 noch Login-Redirect. Verboten heißt: 403 oder 404.
- ``anonymous_ok``: anonymer Zugriff ist KEIN Fehler (public/auth-flow);
  sonst wird für anonym ein Redirect auf /login/ erwartet.
- ``url_kwargs``: ("kwarg", "fixture.attr")-Paare oder ("kwarg", "literal").
  Disambiguierung: Enthält der Wert einen Punkt (``.``), wird er als
  Fixture-Attributpfad aufgelöst (``"fixture_name.attr"``); sonst ist er
  ein Literal und wird unverändert als URL-Kwarg eingesetzt.
- ``idor``: wie url_kwargs, aber mit Objekten der ZWEITEN Facility —
  erwartet wird 404 (kein Existenz-Leak). ``idor_exempt`` begründet,
  warum ein pk-Endpoint keine IDOR-Probe braucht.
- ``sudo``: View trägt RequireSudoModeMixin (in Unit-Tests deaktiviert;
  am Live-Server antwortet ein erlaubter Akteur mit 302 → /sudo/).
- ``extra_ok``: zusätzliche Status, die für ERLAUBTE Akteure ok sind
  (z. B. 404 bei synthetischen Token-Kwargs). Gilt für ALLE deklarierten
  Methoden des Eintrags — bewusst grob gehalten; bei Bedarf
  methodenspezifisch verfeinern.
"""

from dataclasses import dataclass

ROLES = ("facility_admin", "lead", "staff", "assistant", "super_admin")

ALL_AUTH = frozenset(ROLES)
ASSISTANT_PLUS = frozenset({"facility_admin", "lead", "staff", "assistant"})
STAFF_PLUS = frozenset({"facility_admin", "lead", "staff"})
LEAD_PLUS = frozenset({"facility_admin", "lead"})
ADMIN_ONLY = frozenset({"facility_admin"})
SUPER_ONLY = frozenset({"super_admin"})
# Account links (invite, reset, unlock) and the forms requesting them are
# processed only in a session that is not signed in; every signed-in role gets
# a 403 notice (Refs #2391).
SIGNED_OUT_ONLY: frozenset[str] = frozenset()
# Seed-Zustand des Rechts „Löschbestätigung" (Refs #1053).
CONFIRMER = frozenset({"facility_admin", "lead"})


@dataclass(frozen=True)
class Expectation:
    url_name: str
    category: str
    methods: tuple[tuple[str, frozenset[str]], ...]  # (("GET", frozenset), ...)
    url_kwargs: tuple[tuple[str, str], ...] = ()
    idor: tuple[tuple[str, str], ...] = ()
    idor_exempt: str = ""
    anonymous_ok: bool = False
    sudo: bool = False
    extra_ok: tuple[int, ...] = ()


def E(url_name, category, *, get=None, post=None, **kw):
    methods = tuple((m, roles) for m, roles in (("GET", get), ("POST", post)) if roles is not None)
    return Expectation(url_name=url_name, category=category, methods=methods, **kw)


EXPECTATIONS = (
    E(
        "core:system_legal_hold_dismiss",
        "system",
        sudo=True,
        post=SUPER_ONLY,
        url_kwargs=(("pk", "legal_hold.pk"),),
        idor_exempt="Installation administrator explicitly selects a hold across facilities.",
    ),
    E(
        "core:client_stage_downgrade",
        "clients",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
        extra_ok=(409,),
    ),
    E(
        "core:client_legal_hold",
        "retention",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E(
        "core:client_legal_hold_dismiss",
        "retention",
        post=LEAD_PLUS,
        url_kwargs=(("pk", "client_identified.pk"), ("hold_pk", "legal_hold.pk")),
        idor=(("pk", "foreign_client.pk"), ("hold_pk", "foreign_legal_hold.pk")),
        extra_ok=(404, 409),
    ),
    E(
        "core:case_legal_hold",
        "retention",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    E(
        "core:case_legal_hold_dismiss",
        "retention",
        post=LEAD_PLUS,
        url_kwargs=(("pk", "case_open.pk"), ("hold_pk", "legal_hold.pk")),
        idor=(("pk", "foreign_case.pk"), ("hold_pk", "foreign_legal_hold.pk")),
        extra_ok=(404, 409),
    ),
    E(
        "core:event_legal_hold",
        "retention",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "sample_event.pk"),),
        idor=(("pk", "foreign_event.pk"),),
    ),
    E(
        "core:event_legal_hold_dismiss",
        "retention",
        post=LEAD_PLUS,
        url_kwargs=(("pk", "sample_event.pk"), ("hold_pk", "legal_hold.pk")),
        idor=(("pk", "foreign_event.pk"), ("hold_pk", "foreign_legal_hold.pk")),
        extra_ok=(404, 409),
    ),
    # ---- public (anonym erreichbar) -------------------------------------
    E("health", "public", get=ALL_AUTH, anonymous_ok=True),
    E("robots_txt", "public", get=ALL_AUTH, anonymous_ok=True),
    E("csp_report", "public", post=ALL_AUTH, anonymous_ok=True),
    E("login", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True),
    E("service_worker", "public", get=ALL_AUTH, anonymous_ok=True),
    E("manifest", "public", get=ALL_AUTH, anonymous_ok=True),
    E("offline_fallback", "public", get=ALL_AUTH, anonymous_ok=True),
    # Demo-Rechtstexte (Refs #1641): nur bei DEMO_MODE erreichbar, dann
    # oeffentlich ohne Login. Ausserhalb DEMO_MODE (Test-/E2E-/Prod-Config)
    # geben die Views 404 fuer JEDEN Akteur — extra_ok=(404,) deckt die
    # erlaubten Rollen, anonymous_ok den anonymen Zugriff.
    E("demo_imprint", "public", get=ALL_AUTH, anonymous_ok=True, extra_ok=(404,)),
    E("demo_privacy", "public", get=ALL_AUTH, anonymous_ok=True, extra_ok=(404,)),
    # Demo-Bereichsauswahl (nur bei DEMO_MODE; sonst 404 wie die Demo-Rechtstexte) — Refs #2285
    E("demo_scenario_select", "public", get=ALL_AUTH, anonymous_ok=True, extra_ok=(404,)),
    E("demo_scenario_start", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True, extra_ok=(404,)),
    E("set_language", "public", post=ALL_AUTH, anonymous_ok=True),
    E("password_reset", "public", get=SIGNED_OUT_ONLY, post=SIGNED_OUT_ONLY, anonymous_ok=True),
    E("password_reset_done", "public", get=ALL_AUTH, anonymous_ok=True),
    E(
        "password_reset_confirm",
        "public",
        get=SIGNED_OUT_ONLY,
        anonymous_ok=True,
        url_kwargs=(("uidb64", "MQ"), ("token", "abc-defghijklmnop")),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    # L4 (Refs #1375): Einladungs-Setup — analog password_reset_confirm, eigener
    # Token-Generator/Ablauf, anonym zugänglich (Token-basiert, kein Objektbezug).
    E(
        "invite_confirm",
        "public",
        get=SIGNED_OUT_ONLY,
        anonymous_ok=True,
        url_kwargs=(("uidb64", "MQ"), ("token", "abc-defghijklmnop")),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    E("password_reset_complete", "public", get=ALL_AUTH, anonymous_ok=True),
    E("core:lockout_recovery_request", "public", get=SIGNED_OUT_ONLY, post=SIGNED_OUT_ONLY, anonymous_ok=True),
    E("core:lockout_recovery_sent", "public", get=SIGNED_OUT_ONLY, anonymous_ok=True),
    E(
        "core:lockout_recovery_confirm",
        "public",
        get=SIGNED_OUT_ONLY,
        anonymous_ok=True,
        url_kwargs=(("token", "ungueltiges-token"),),
        extra_ok=(404,),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    E("core:lockout_recovery_backup_code", "public", get=SIGNED_OUT_ONLY, post=SIGNED_OUT_ONLY, anonymous_ok=True),
    # ---- auth-flow (eingeloggt, alle Rollen) ----------------------------
    E("logout", "auth-flow", post=ALL_AUTH, anonymous_ok=True),
    E("password_change", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("core:help_overview", "auth-flow", get=ALL_AUTH),
    E(
        "core:help_page",
        "auth-flow",
        get=ALL_AUTH,
        url_kwargs=(("slug", "personen"),),
        idor_exempt="Feste Inhalts-Whitelist ohne Objektbezug",
    ),
    E("offline_key_salt", "auth-flow", post=ALL_AUTH),
    # Disabled demos return 404 even anonymously; TestDemoOfflineKeyReady
    # separately requires a login redirect when demo mode is enabled.
    E("demo_offline_key_ready", "auth-flow", post=ALL_AUTH, anonymous_ok=True, extra_ok=(404,)),
    # POST ohne korrektes Passwort antwortet 403 (Re-Auth-Form, sudo_mode.py) —
    # Authentifizierungs-, keine Autorisierungs-Semantik.
    E("sudo_mode", "auth-flow", get=ALL_AUTH, post=ALL_AUTH, extra_ok=(403,)),
    E("mfa_setup", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("mfa_device_change", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("mfa_verify", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("mfa_settings", "auth-flow", get=ALL_AUTH),
    E("mfa_disable", "auth-flow", get=ALL_AUTH, post=ALL_AUTH, sudo=True),
    E("mfa_backup_codes", "auth-flow", get=ALL_AUTH),
    E("mfa_backup_codes_regenerate", "auth-flow", post=ALL_AUTH),
    # Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492).
    # Passkey entfernen ist sudo-pflichtig (analog mfa_disable, Refs #683) und
    # streng auf den eigenen Nutzer gescopt: ein fremder/nicht existenter pk
    # fuehrt zu „nicht gefunden" + Redirect nach /mfa/settings/ (kein 404-Leak,
    # keine PII) — daher idor_exempt statt IDOR-Probe.
    E(
        "mfa_passkey_delete",
        "auth-flow",
        post=ALL_AUTH,
        sudo=True,
        url_kwargs=(("pk", "1"),),
        idor_exempt="Auf request.user gescopt; fremder pk -> Redirect (kein Existenz-Leak)",
    ),
    # WebAuthn-Ceremony-API (JSON) unter Namespace otp_webauthn. Kein
    # Login-Redirect: die Views antworten fuer Nicht-/Fehl-Autorisierung mit
    # JSON-Fehlerstatus (401/403/400) statt 302 — daher anonymous_ok=True
    # (der Gate prueft fuer Anonyme nur <500).
    #
    # Registrierung (begin/complete) ist NICHT rollen-, sondern SESSION-gegatet:
    # ``check_can_register`` verlangt eine bereits 2FA-verifizierte Session
    # (``mfa_verified``) — sonst 403 ``mfa_verification_required`` (Zweitfaktor-
    # Bypass-Schutz, Refs #1492). Diese Matrix-Zellen laufen mit ``force_login``
    # OHNE verifizierte Session, daher wird die Registrierung fuer JEDE Rolle
    # abgelehnt (leere Erlaubnis-Menge = 403 fuer alle). Der Positiv-Pfad
    # (verifizierte Session) ist in ``test_mfa_webauthn.py`` abgedeckt.
    E("otp_webauthn:credential-registration-begin", "auth-flow", post=frozenset(), anonymous_ok=True),
    E("otp_webauthn:credential-registration-complete", "auth-flow", post=frozenset(), anonymous_ok=True),
    # Assertion (authentication) laeuft bewusst in einer noch unverifizierten
    # Session (das ist der Verify-Schritt selbst) — fuer alle Rollen erreichbar.
    E("otp_webauthn:credential-authentication-begin", "auth-flow", post=ALL_AUTH, anonymous_ok=True),
    E("otp_webauthn:credential-authentication-complete", "auth-flow", post=ALL_AUTH, anonymous_ok=True),
    # JS-i18n-Katalog fuer das Passkey-Bundle — public, statisch, PII-frei.
    E("otp_webauthn:js-i18n-catalog", "public", get=ALL_AUTH, anonymous_ok=True),
    E("core:dashboard", "auth-flow", get=ALL_AUTH),
    E("core:account_profile", "auth-flow", get=ALL_AUTH),
    # ---- facility-read (Assistenz aufwärts; super_admin AUSGESCHLOSSEN) -
    E("core:zeitstrom", "facility-read", get=ASSISTANT_PLUS),
    # Refs #1124: /uebergabe/ ist ein permanenter Redirect auf
    # /?view=uebergabe (RedirectView, keine eigene AuthZ). 301 für alle
    # Akteure inkl. anonym — der Übergabe-Inhalt selbst ist über
    # ZeitstromView (ASSISTANT_PLUS) gegated.
    E("core:handover", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #2042: Schichtnotiz — POST-only, StaffRequiredMixin (Fachkraft, Leitung, Anwendungsbetreuung).
    E("core:handover_note", "facility-write", post=STAFF_PLUS),
    E("core:client_list", "facility-read", get=ASSISTANT_PLUS),
    E(
        "core:client_detail",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E("core:event_list", "facility-read", get=ASSISTANT_PLUS),
    E("core:attachment_list", "facility-read", get=ASSISTANT_PLUS),
    E(
        "core:event_detail",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_event.pk"),),
        idor=(("pk", "foreign_event.pk"),),
    ),
    E(
        "core:attachment_download",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_event.pk"), ("attachment_pk", "authz_attachment.pk")),
        idor=(("pk", "foreign_event.pk"), ("attachment_pk", "foreign_attachment.pk")),
    ),
    # M24/AP4 (Refs #1738, ADR-039 §12): Rollen-Gefaelle der drei
    # Download-Routen. Personen-Anhaenge folgen der Personen-Sicht
    # (ASSISTANT_PLUS), Fall-Anhaenge der Fall-Sicht (STAFF_PLUS) — Faelle sind
    # fuer die Assistenz unsichtbar, ein Assistenz-Download waere eine
    # Gate-Umgehung.
    E(
        "core:client_attachment_download",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "client_identified.pk"), ("attachment_pk", "authz_client_attachment.pk")),
        idor=(("pk", "foreign_client.pk"), ("attachment_pk", "foreign_client_attachment.pk")),
    ),
    E(
        "core:case_attachment_download",
        "facility-read",
        get=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"), ("attachment_pk", "authz_case_attachment.pk")),
        idor=(("pk", "foreign_case.pk"), ("attachment_pk", "foreign_case_attachment.pk")),
    ),
    # Refs #1874: ersatzloses (physisches) Loeschen einer Anhang-Kette. LEAD_PLUS —
    # der reversible Soft-Delete bleibt an den Owner-Formularen bei jeder
    # bearbeitenden Rolle; die unwiderrufliche Vernichtung ist eine Eskalation.
    # LEAD_PLUS deckt zugleich das Rollen-Gefaelle der Owner-Typen ab (Fall-Anhaenge
    # sind STAFF_PLUS, ADR-039 §12) — es braucht keine zweite Fallunterscheidung.
    E(
        "core:attachment_delete",
        "deletion",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "authz_attachment.pk"),),
        idor=(("pk", "foreign_attachment.pk"),),
    ),
    E(
        "core:workitem_detail",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_workitem.pk"),),
        idor=(("pk", "foreign_workitem.pk"),),
    ),
    E("core:search", "facility-read", get=ASSISTANT_PLUS),
    E("core:workitem_inbox", "facility-read", get=ASSISTANT_PLUS),
    E("core:case_list", "facility-read", get=STAFF_PLUS),
    E(
        "core:case_detail",
        "facility-read",
        get=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    # ---- facility-write --------------------------------------------------
    E("core:client_create", "facility-write", get=STAFF_PLUS, post=STAFF_PLUS),
    E(
        "core:client_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E(
        "core:client_delete_request",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E(
        "core:client_restore",
        "facility-write",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "client_trashed.pk"),),
        idor=(("pk", "foreign_client_trashed.pk"),),
    ),
    # Refs #1737 (B): Sofort-Anonymisierung im Papierkorb — gleiches Gate und
    # gleicher Scope wie client_restore (Anwendungsbetreuung, is_deleted=True).
    E(
        "core:client_anonymize",
        "facility-write",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "client_trashed.pk"),),
        idor=(("pk", "foreign_client_trashed.pk"),),
    ),
    # Duplikat-Merge (Refs #1876, ADR-045): Einstieg + Antrag STAFF_PLUS wie
    # der Loeschantrag (die Fachkraft beantragt, der Direktweg ist zusaetzlich
    # per Service-Guard Leitung+). Der Matrix-POST traegt kein ``target`` und
    # konsumiert nichts (Redirect mit Fehlermeldung) — deshalb bewusst KEIN
    # DESTRUCTIVE_RESET-Hook.
    E(
        "core:client_merge",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    # Aufheben = Papierkorb-Pflege, gleiches Gate wie client_restore. Das
    # Matrix-Objekt ist ein gewoehnlicher Papierkorb-Client (kein Tombstone):
    # der Service antwortet mit ValidationError → Meldung + Redirect, nichts
    # wird konsumiert.
    E(
        "core:client_unmerge",
        "facility-write",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "client_trashed.pk"),),
        idor=(("pk", "foreign_client_trashed.pk"),),
    ),
    # Art.-18-Sperrvermerk (Refs #2045): Setzen und Aufheben sind POST-only und
    # an der HTTP-Grenze beide STAFF_PLUS — das Gefaelle (vier-aeugig ODER
    # Leitung) zieht der Service, nicht die Rolle der Route. Der Matrix-POST
    # traegt keinen ``reason`` bzw. trifft eine nicht eingeschraenkte Person:
    # beide Male ValidationError -> Meldung + Redirect, nichts wird konsumiert,
    # deshalb bewusst kein DESTRUCTIVE_RESET-Hook.
    E(
        "core:client_restrict_processing",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E(
        "core:client_lift_processing_restriction",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E("core:case_create", "facility-write", get=STAFF_PLUS, post=STAFF_PLUS),
    E(
        "core:case_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    # Fall-Loeschweg (Refs #1818, ADR-044). Rollen-Gefaelle wie bei den
    # Personen: die Fachkraft loescht/beantragt (STAFF_PLUS, wie die Fallakte
    # selbst), die Anwendungsbetreuung raeumt den Papierkorb auf (ADMIN_ONLY).
    E(
        "core:case_delete_request",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    E(
        "core:case_restore",
        "facility-write",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "case_trashed.pk"),),
        idor=(("pk", "foreign_case_trashed.pk"),),
    ),
    # Sofort-Tilgung im Papierkorb — gleiches Gate und gleicher Scope wie
    # case_restore (Anwendungsbetreuung, is_deleted=True).
    E(
        "core:case_anonymize",
        "facility-write",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "case_trashed.pk"),),
        idor=(("pk", "foreign_case_trashed.pk"),),
    ),
    E(
        "core:case_close",
        "facility-write",
        post=LEAD_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    E(
        "core:case_reopen",
        "facility-write",
        post=LEAD_PLUS,
        url_kwargs=(("pk", "case_closed.pk"),),
        idor=(("pk", "foreign_case_closed.pk"),),
    ),
    E(
        "core:case_assign_event",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
    ),
    E(
        "core:case_remove_event",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"), ("event_pk", "case_event.pk")),
        idor=(("pk", "foreign_case.pk"), ("event_pk", "foreign_case_event.pk")),
    ),
    E(
        "core:episode_create",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"),),
        idor=(("case_pk", "foreign_case.pk"),),
    ),
    E(
        "core:episode_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "episode.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_episode.pk")),
    ),
    E(
        "core:episode_close",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "episode.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_episode.pk")),
    ),
    E(
        "core:goal_create",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"),),
        idor=(("case_pk", "foreign_case.pk"),),
    ),
    E(
        "core:goal_update",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "outcome_goal.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_goal.pk")),
    ),
    E(
        "core:goal_toggle",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "outcome_goal.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_goal.pk")),
    ),
    E(
        "core:milestone_create",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("goal_pk", "outcome_goal.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("goal_pk", "foreign_goal.pk")),
    ),
    E(
        "core:milestone_toggle",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "milestone.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_milestone.pk")),
    ),
    E(
        "core:milestone_delete",
        "facility-write",
        post=STAFF_PLUS,
        url_kwargs=(("case_pk", "case_open.pk"), ("pk", "milestone.pk")),
        idor=(("case_pk", "foreign_case.pk"), ("pk", "foreign_milestone.pk")),
    ),
    E("core:event_create", "facility-write", get=ASSISTANT_PLUS, post=ASSISTANT_PLUS),
    # Mixin: AssistantOrAbove, aber Owner-Regel (events.py: EventUpdateView.dispatch):
    # Assistenz darf nur EIGENE Events bearbeiten — sample_event gehört staff_user,
    # daher gilt für dieses Matrix-Objekt STAFF_PLUS.
    E(
        "core:event_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "sample_event.pk"),),
        idor=(("pk", "foreign_event.pk"),),
    ),
    # Mixin: StaffRequired, aber Owner-Regel (events.py: EventDeleteView.dispatch):
    # Staff darf nur EIGENE Events löschen — sample_event gehört staff_user (nicht
    # dem Matrix-Akteur), daher gilt für dieses Matrix-Objekt LEAD_PLUS.
    E(
        "core:event_delete",
        "facility-write",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "sample_event.pk"),),
        idor=(("pk", "foreign_event.pk"),),
    ),
    E("core:workitem_create", "facility-write", get=STAFF_PLUS, post=STAFF_PLUS),
    # Mixin: StaffRequired, plus Owner/Assignee/Teamaufgaben-Policy (Refs #735,
    # #1125, workitems.py: can_user_mutate_workitem): Staff dürfen eigene,
    # zugewiesene und nicht zugewiesene (Team-)Aufgaben mutieren.
    # sample_workitem ist nicht zugewiesen → Teamaufgabe → STAFF_PLUS.
    E(
        "core:workitem_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "sample_workitem.pk"),),
        idor=(("pk", "foreign_workitem.pk"),),
    ),
    E("core:workitem_bulk_status", "facility-write", get=ASSISTANT_PLUS, post=ASSISTANT_PLUS),
    E("core:workitem_bulk_priority", "facility-write", get=ASSISTANT_PLUS, post=ASSISTANT_PLUS),
    E("core:workitem_bulk_assign", "facility-write", get=ASSISTANT_PLUS, post=ASSISTANT_PLUS),
    # ---- lead-admin -------------------------------------------------------
    # Bereichs-Hub „Datenschutz" (Refs #1813, Konzept #1801 §4.1). Reines
    # Navigations-Chrom: das Gate ist per Regel N2 die Oder-Verknüpfung der
    # Reiter-Gates, das Rollen-Soll deshalb dasselbe LEAD_PLUS wie beim
    # ersten Reiter „Löschanträge" — inklusive derselben zustandsabhängigen
    # Erweiterung (wer selbst einen offenen Löschantrag gestellt hat, wird auf
    # die Antragsliste weitergeleitet statt abgewiesen). Gegenprobe:
    # mx_staff/mx_assistant haben keinen eigenen Antrag → 403; mx_super hat
    # facility=None → 403.
    E("core:datenschutz_uebersicht", "lead-admin", get=LEAD_PLUS),
    E("core:retention_dashboard", "lead-admin", get=LEAD_PLUS),
    E("core:retention_bulk_approve", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:retention_bulk_defer", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:retention_bulk_reject", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:statistics", "lead-admin", get=LEAD_PLUS),
    E("core:statistics_chart_data", "lead-admin", get=LEAD_PLUS),
    E("core:statistics_external_report", "lead-admin", get=LEAD_PLUS),
    E("core:statistics_report_panel", "lead-admin", get=LEAD_PLUS),
    # K4a (Refs #1770): Freigabe-Uebersicht — Rollen-Guard wie das Panel selbst.
    # Zeigt Herausgabe-Metadaten (Zeitraum, Format, Empfaenger/Zweck), keine
    # Personendaten; Leitung und Einrichtungsverwaltung verantworten die
    # Herausgabe und muessen ihre Historie sehen.
    E("core:statistics_report_releases", "lead-admin", get=LEAD_PLUS),
    # ---- deletion (Vier-Augen-Löschworkflow, Refs #1053) -------------------
    # Rollen-Soll = LEAD_PLUS (Seed-Zustand ``CONFIRMER`` ist eine Teilmenge).
    # Refs #1736 (B): das View-Gate (``User.can_access_deletion_requests``) ist
    # zusätzlich zustandsabhängig — wer selbst einen offenen Löschantrag
    # gestellt hat, darf die Liste öffnen und sieht dann NUR die eigenen
    # Anträge. Diese Zeile beschreibt bewusst weiterhin den rollenbasierten
    # Grundzustand (Akteure ohne eigenen Antrag); die Navigation trägt exakt
    # dasselbe Gate.
    E("core:deletion_request_list", "deletion", get=LEAD_PLUS),
    # Confirmation grants no target reading rights (Refs #2516).
    # Role x target visibility is exercised by test_deletion_review_targets.
    E(
        "core:deletion_review",
        "deletion",
        get=CONFIRMER,
        post=CONFIRMER,
        url_kwargs=(("pk", "deletion_request.pk"),),
        idor=(("pk", "foreign_deletion_request.pk"),),
    ),
    # Refs #2040: Selbst-Rückzug. Bewusst OHNE Rollen-Gate — das Recht folgt
    # der Urheberschaft, nicht der Rolle: wer den Antrag gestellt hat, darf ihn
    # zurücknehmen, auch eine Fachkraft ohne Zugang zur vollen Antragsliste.
    # Für JEDEN Akteur, der nicht die antragstellende Person ist, antwortet die
    # Route 404 (nicht 403 — ein 403 bestätigte die Existenz eines fremden
    # Antrags unter geratener UUID). Die Matrix modelliert Urheberschaft nicht,
    # deshalb ``extra_ok=(404,)`` wie bei den Demo-Rechtstexten: kein Rollen-
    # Soll, sondern „jede angemeldete Rolle darf anklopfen, die Eigentums-
    # prüfung entscheidet". Das Eigentums-Gate selbst ist in
    # ``test_deletion_request_withdraw.py`` gepinnt (fremd → 404, eigener
    # PENDING → Dialog), inklusive facility-losem Akteur.
    E(
        "core:deletion_withdraw",
        "deletion",
        get=ALL_AUTH,
        post=ALL_AUTH,
        url_kwargs=(("pk", "deletion_request.pk"),),
        idor=(("pk", "foreign_deletion_request.pk"),),
        extra_ok=(404,),
    ),
    # ---- facility-admin -----------------------------------------------------
    # Bereichs-Hub „Verwaltung" (Refs #1813, Konzept #1801 §4.1): dasselbe Gate
    # wie alle Reiter (Einstellungen, Dokumentationstypen, Feldvorlagen,
    # Datenimport) —
    # keine neue Berechtigung, nur eine andere Anordnung derselben Seiten.
    E("core:verwaltung_uebersicht", "facility-admin", get=ADMIN_ONLY),
    # Refs #1885 / M4-Entscheid E4: einrichtungsweite Sicherheits-, Fristen-
    # und Datei-Policies sind nur fuer die eigene Anwendungsbetreuung und
    # durchgehend hinter dem Sudo-Step-up schreibbar.
    E("core:facility_settings", "facility-admin", get=ADMIN_ONLY, post=ADMIN_ONLY, sudo=True),
    # Refs #1871 / ADR-048: Die Liste ist einrichtungsgebunden lesbar; jede
    # Management-Seite bzw. Mutation verlangt Sudo. ``core_user`` hat kein
    # RLS-Netz, deshalb tragen alle pk-Routen echte Fremd-Facility-Proben.
    E("core:user_access_list", "facility-admin", get=ADMIN_ONLY),
    E("core:user_access_create", "facility-admin", get=ADMIN_ONLY, post=ADMIN_ONLY, sudo=True),
    E(
        "core:user_access_update",
        "facility-admin",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        sudo=True,
        url_kwargs=(("pk", "staff_user.pk"),),
        idor=(("pk", "foreign_staff.pk"),),
    ),
    E(
        "core:user_access_invite",
        "facility-admin",
        post=ADMIN_ONLY,
        sudo=True,
        url_kwargs=(("pk", "staff_user.pk"),),
        idor=(("pk", "foreign_staff.pk"),),
    ),
    E(
        "core:user_access_unlock",
        "facility-admin",
        post=ADMIN_ONLY,
        sudo=True,
        url_kwargs=(("pk", "staff_user.pk"),),
        idor=(("pk", "foreign_staff.pk"),),
    ),
    E(
        "core:user_access_mfa_reset",
        "facility-admin",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        sudo=True,
        url_kwargs=(("pk", "staff_user.pk"),),
        idor=(("pk", "foreign_staff.pk"),),
    ),
    E("core:client_trash", "facility-admin", get=ADMIN_ONLY),
    # Refs #1818: zweiter Papierkorb, gleiches Gate wie der erste.
    E("core:case_trash", "facility-admin", get=ADMIN_ONLY),
    E("core:audit_log", "facility-admin", get=ADMIN_ONLY),
    E(
        "core:audit_detail",
        "facility-admin",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "audit_entry.pk"),),
        idor=(("pk", "foreign_audit_entry.pk"),),
    ),
    # Refs #1252: Das Vorlagen-Bündel (öffentliche Templates + Einrichtungsname
    # + Aufbewahrungsfristen) ist bewusst NICHT sudo-pflichtig — #683 zielte auf
    # den Rohdaten-Export (Art. 15/20), nicht das Doku-Paket. Niedrige Sensibilität.
    E("core:dsgvo_package", "facility-admin", get=ADMIN_ONLY, sudo=False),
    E(
        "core:dsgvo_document",
        "facility-admin",
        get=ADMIN_ONLY,
        sudo=False,
        url_kwargs=(("document", "verarbeitungsverzeichnis"),),
        idor_exempt="Statisches Template, facility-Daten nur der eigenen Einrichtung",
    ),
    # ---- export (Sudo-geschützte Rohdaten-Exporte) -------------------------
    E(
        "core:client_export_json",
        "export",
        get=LEAD_PLUS,
        sudo=True,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E(
        "core:client_export_pdf",
        "export",
        get=LEAD_PLUS,
        sudo=True,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E("core:statistics_csv_export", "export", get=LEAD_PLUS),
    # Refs #1061 (M5 P5): Parität zu den übrigen Statistik-Exports — bewusst
    # OHNE sudo (anders als die Client-Rohdaten-Exporte oben).
    E("core:statistics_report_export", "export", get=LEAD_PLUS),
    # ---- system (nur super_admin, Refs #867) --------------------------------
    E("core:system_dashboard", "system", get=SUPER_ONLY),
    E("core:system_audit_list", "system", get=SUPER_ONLY),
    E(
        "core:system_audit_detail",
        "system",
        get=SUPER_ONLY,
        url_kwargs=(("pk", "audit_entry.pk"),),
        idor_exempt="super_admin ist installationsweit — Mandantentrennung greift hier bewusst nicht",
    ),
    # Refs #1253: Cross-Facility-Audit-Export inkl. IP-Adressen — Bulk-Rohdaten,
    # Analogon zum sudo-gegateten Klienten-Export.
    E("core:system_audit_export", "system", get=SUPER_ONLY, sudo=True),
    E("core:system_organization", "system", get=SUPER_ONLY),
    E("core:system_lockout_list", "system", get=SUPER_ONLY),
    # Refs #1253: Konto-Entsperrung hebt einen Schutz-Lockout auf (destruktiv).
    E("core:system_unlock", "system", post=SUPER_ONLY, sudo=True),
    # Refs #1253: Wartungsmodus = installationsweites 503 (destruktiver Toggle).
    E("core:system_maintenance", "system", get=SUPER_ONLY, post=SUPER_ONLY, sudo=True),
    E("core:system_retention", "system", get=SUPER_ONLY),
    E("core:system_vvt", "system", get=SUPER_ONLY),
    E("core:system_legal_hold_list", "system", get=SUPER_ONLY),
    E("core:system_compliance", "system", get=SUPER_ONLY),
    # ---- config (Formular-Konfiguration, M0, Refs #1056; facility_admin only) --
    E("core:config_doctype_list", "config", get=ADMIN_ONLY),
    E("core:config_doctype_create", "config", get=ADMIN_ONLY, post=ADMIN_ONLY),
    E(
        "core:config_doctype_detail",
        "config",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_doctype_update",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_doctype_delete",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E("core:config_fieldtemplate_list", "config", get=ADMIN_ONLY),
    E("core:config_fieldtemplate_create", "config", get=ADMIN_ONLY, post=ADMIN_ONLY),
    E(
        "core:config_fieldtemplate_update",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "field_template.pk"),),
        idor=(("pk", "foreign_field_template.pk"),),
    ),
    E(
        "core:config_fieldtemplate_delete",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "field_template.pk"),),
        idor=(("pk", "foreign_field_template.pk"),),
    ),
    E(
        "core:config_fieldtemplate_toggle",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "field_template.pk"),),
        idor=(("pk", "foreign_field_template.pk"),),
    ),
    # M0 P6 (Refs #1056): Feld-Zuordnung + FieldGroup-Verwaltung im
    # DocumentType-Detail (HTMX-Board). facility_admin only; IDOR → fremder
    # doc_type → 404 VOR jeder weiteren Pruefung, gemischte Kombis ebenfalls 404.
    E(
        "core:config_doctype_field_add",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_doctype_field_remove",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"), ("dtf_pk", "doc_type_field.pk")),
        idor=(("pk", "foreign_doc_type.pk"), ("dtf_pk", "foreign_doc_type_field.pk")),
    ),
    E(
        "core:config_fieldgroup_create",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_fieldgroup_update",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"), ("group_pk", "field_group.pk")),
        idor=(("pk", "foreign_doc_type.pk"), ("group_pk", "foreign_field_group.pk")),
    ),
    E(
        "core:config_fieldgroup_delete",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"), ("group_pk", "field_group.pk")),
        idor=(("pk", "foreign_doc_type.pk"), ("group_pk", "foreign_field_group.pk")),
    ),
    # M0 P7 (Refs #1056): Drag-and-Drop-Batch-Reorder. facility_admin only; der
    # IDOR-Lookup (fremder doc_type → 404) läuft VOR jeder Payload-Prüfung. Ein
    # leerer Matrix-POST liefert für berechtigte Rollen 400 (nichts umzusortieren)
    # — kein 5xx, kein 403/404 → in der Matrix erlaubt.
    E(
        "core:config_reorder_blocks",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_reorder_fields",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_move_field",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    # M1 P6 (Refs #1057): Regel-Editor für bedingte Feldsichtbarkeit. facility_admin
    # only; IDOR (fremder doc_type/condition → 404) läuft VOR jeder Payload-Prüfung.
    # Ein leerer/ungültiger Matrix-POST liefert für berechtigte Rollen 400 (Form
    # ungültig) bzw. der value-input-GET 400 (kein Quellfeld) — kein 403/404/5xx.
    E(
        "core:config_condition_create",
        "config",
        get=ADMIN_ONLY,  # GET rendert das (server-vorbelegte) Neu-Formular für die UND-/ODER-Affordanzen
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    E(
        "core:config_condition_update",
        "config",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"), ("condition_pk", "field_condition.pk")),
        idor=(("pk", "foreign_doc_type.pk"), ("condition_pk", "foreign_field_condition.pk")),
    ),
    E(
        "core:config_condition_delete",
        "config",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"), ("condition_pk", "field_condition.pk")),
        idor=(("pk", "foreign_doc_type.pk"), ("condition_pk", "foreign_field_condition.pk")),
    ),
    E(
        "core:config_condition_value_input",
        "config",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    # #1718: GET-Refresh des Bedingungs-Boards (HTMX-Nachzug nach Feld-Board-
    # Mutation). facility_admin only; IDOR (fremder doc_type → 404) vor Rendern.
    E(
        "core:config_condition_board",
        "config",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    # #1715: typabhängiger Operator-Slot (HTMX-GET). facility_admin only; IDOR
    # (fremder doc_type → 404) vor Rendern; fehlende Quelle → 400 (kein 403/404/5xx).
    E(
        "core:config_condition_operator_input",
        "config",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "doc_type_contact.pk"),),
        idor=(("pk", "foreign_doc_type.pk"),),
    ),
    # ---- partials-htmx --------------------------------------------------------
    E(
        "core:workitem_status_update",
        "partials-htmx",
        post=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_workitem.pk"),),
        idor=(("pk", "foreign_workitem.pk"),),
    ),
    E("core:client_autocomplete", "partials-htmx", get=ASSISTANT_PLUS),
    # Refs #2039: minimale Personen-Mutation aus dem Kontaktformular —
    # identisches STAFF_PLUS-Gate wie ``client_create``. Ohne URL-Objekt gibt
    # es keine zusaetzliche Foreign-Object-IDOR-Probe; der Eintrag speist die
    # Rollen-/Methodenmatrix und die gemeinsamen IDOR-Deklarationsguards.
    E("core:client_inline_create", "partials-htmx", get=STAFF_PLUS, post=STAFF_PLUS),
    # Refs #2044: reine Warnprobe im Personen-Edit — POST haelt das Pseudonym
    # aus URL-/Access-Logs, schreibt aber nichts und hat dasselbe STAFF_PLUS-
    # Gate wie ``client_update``.
    E("core:client_rename_warning", "partials-htmx", post=STAFF_PLUS),
    # Refs #1876: Duplikat-Hinweis im Anlage-Formular — dasselbe Gate wie
    # ``client_create`` (STAFF_PLUS), denn genau dort wird er nachgeladen.
    E("core:client_similar_hint", "partials-htmx", get=STAFF_PLUS),
    # M2 P7 (Refs #1058): STAFF_PLUS — bewusst enger als das Event-Partial.
    # Personen anlegen/bearbeiten ist STAFF_PLUS (``client_create``), also darf
    # auch die Feldflaeche dieses Formulars nicht tiefer aufgehen.
    E("core:client_fields_partial", "partials-htmx", get=STAFF_PLUS),
    E("core:event_fields_partial", "partials-htmx", get=ASSISTANT_PLUS),
    # Strichlisten-„+1" (Refs #1349/#1906): document_type kommt in Query/POST
    # statt als URL-kwarg: GET-Leerrequests liefern 404, POST-Leerrequests seit
    # #1909 wegen des zwingenden Idempotenz-Schlüssels bereits 400. Scoping/IDOR
    # wird in den Tally-Tests body-basiert geprüft.
    E("core:tally_increment", "partials-htmx", get=ASSISTANT_PLUS, post=ASSISTANT_PLUS, extra_ok=(400, 404)),
    E("core:tally_undo", "partials-htmx", post=ASSISTANT_PLUS, extra_ok=(404,)),
    E("core:zeitstrom_feed_partial", "partials-htmx", get=ASSISTANT_PLUS),
    E("core:zeitstrom_tally_counts_partial", "partials-htmx", get=ASSISTANT_PLUS),
    E("core:zeitstrom_current_shift_partial", "partials-htmx", get=ASSISTANT_PLUS),
    E("core:zeitstrom_focus_box_partial", "partials-htmx", get=ASSISTANT_PLUS),
    # Refs #1917: Banner-Follow-up des Watchers — dieselbe Rollenfläche wie
    # die Zeitstrom-Startseite, Grund-Redaktion übernimmt get_active_bans.
    E("core:zeitstrom_bans_partial", "partials-htmx", get=ASSISTANT_PLUS),
    # JSON-Pollingvertrag: anonyme Requests antworten bewusst mit 401 statt
    # Login-HTML/Redirect; Rollen und Facility-Gate bleiben ASSISTANT_PLUS.
    E("core:zeitstrom_watch", "offline-api", get=ASSISTANT_PLUS, anonymous_ok=True),
    # M2 P8 (Refs #1058): STAFF_PLUS — bewusst enger als das Event-Partial,
    # analog ``client_fields_partial``. Faelle anlegen/bearbeiten ist STAFF_PLUS
    # (``case_create``/``cases_for_client``), also darf auch die Feldflaeche
    # dieses Formulars nicht tiefer aufgehen.
    E("core:case_fields_partial", "partials-htmx", get=STAFF_PLUS),
    E("core:cases_for_client", "partials-htmx", get=STAFF_PLUS),
    E("core:global_search", "partials-htmx", get=ASSISTANT_PLUS),
    E(
        "core:retention_approve",
        "partials-htmx",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "retention_proposal.pk"),),
        idor=(("pk", "foreign_retention_proposal.pk"),),
    ),
    E(
        "core:retention_hold",
        "partials-htmx",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "retention_proposal.pk"),),
        idor=(("pk", "foreign_retention_proposal.pk"),),
    ),
    E(
        "core:retention_dismiss_hold",
        "partials-htmx",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "legal_hold.pk"),),
        idor=(("pk", "foreign_legal_hold.pk"),),
    ),
    # ---- offline-api ------------------------------------------------------------
    E(
        "core:offline_bundle",
        "offline-api",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    # Refs #1518 (#1499): personenloses Facility-Meta-Bundle. Wie offline_bundle
    # ASSISTANT_PLUS, aber pk-los (kein Roster/kein IDOR — Facility-Scope aus
    # request.current_facility).
    E("core:offline_facility_bundle", "offline-api", get=ASSISTANT_PLUS),
    # Refs #1760: personen-genaue Offline-Widerrufsliste. Wie offline_bundle
    # ASSISTANT_PLUS, aber pk-los — der Facility-Scope kommt aus
    # request.current_facility (plus RLS, Migration 0127); kein Objekt-Lookup
    # ueber eine URL-pk, daher kein IDOR-Vektor. Die Antwort traegt nur opake
    # UUIDs bereits anonymisierter Personen (PII-frei).
    E("core:offline_revocations", "offline-api", get=ASSISTANT_PLUS),
    # Refs #1408: dedizierter CSRF-Token-Endpoint fuer den Offline-Replay-
    # Refresh-Pfad. Public wie offline_client_shell — der Token ist kein
    # Geheimnis (Django rendert ihn in jedes Formular), PII-frei; bisherige
    # Refresh-Quelle /login/ war ebenfalls public.
    E("core:offline_csrf", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1476 (R10): Audit-Spur fuer den Offline-Notiz-Export. POST-only,
    # ASSISTANT_PLUS (wie die uebrigen Offline-APIs). Kein Objekt-Lookup: die
    # View schreibt nur einen AuditLog-Eintrag mit der client-seitigen Event-pk
    # als opakem Bezug (kein facility-gescopter DB-Fetch) -> idor_exempt.
    E(
        "core:offline_note_export_audit",
        "offline-api",
        post=ASSISTANT_PLUS,
        idor_exempt="Kein Objekt-Lookup; schreibt nur einen AuditLog-Eintrag (opake Event-pk)",
    ),
    E(
        "core:offline_client_detail",
        "offline-api",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor_exempt="Reines Scaffold ohne DB-Lookup/PII (Docstring der View)",
    ),
    # Refs #1322: pk-loser Shell fuer In-Place-Offline-Rendern. Public wie
    # offline_fallback — PII-frei, muss via SW cache.addAll cachebar sein.
    E("core:offline_client_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1531 (#1499): pk-lose Offline-Personenlisten-Shell.
    # Public wie offline_client_shell — PII-frei (die Liste liegt
    # verschluesselt in IDB), muss via SW cache.addAll pre-cachebar sein.
    # Same-commit-as-URL (authz-Completeness-Gate).
    E("core:offline_client_list_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1521 (#1499): pk-lose Offline-Event-Create-Shell. Public wie
    # offline_client_shell — PII-frei (Katalog liegt verschluesselt in IDB),
    # muss via SW cache.addAll pre-cachebar sein. Same-commit-as-URL
    # (authz-Completeness-Gate, Risiko #8 der Design-Spec).
    E("core:offline_event_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1522 (#1499): pk-lose Offline-WorkItem-Create-Shell. Public wie
    # offline_client_shell — PII-frei (Roster liegt verschluesselt in IDB), muss
    # via SW cache.addAll pre-cachebar sein. Same-commit-as-URL (authz-
    # Completeness-Gate, Risiko #8). Der Staff+-Gate (Risiko #7) sitzt im Client
    # (assignable_users == Staff+-Marker), nicht am Server-Auth des Scaffolds.
    E("core:offline_workitem_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1541 (#1499): pk-lose Offline-Aufgabenlisten-Shell. Public
    # wie offline_client_shell — PII-frei (die aggregierten Aufgaben liegen
    # verschluesselt in IDB), muss via SW cache.addAll pre-cachebar sein.
    # Same-commit-as-URL (authz-Completeness-Gate).
    E("core:offline_workitem_list_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1542 (#1499): pk-lose Offline-Zeitstrom-Shell. Public wie
    # offline_client_shell — PII-frei (die aggregierte lokale Chronik liegt
    # verschluesselt in IDB), muss via SW cache.addAll pre-cachebar sein.
    # Same-commit-as-URL (authz-Completeness-Gate).
    E("core:offline_zeitstrom_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    # Refs #1396: Konflikt-Liste + pk-loser Review-Shell sind pk-los, datenlos
    # (rendern rein aus IndexedDB) und muessen via SW cache.addAll pre-cachebar
    # sein — public wie offline_client_shell. Der auth-gated Review-pk-Endpoint
    # (offline_conflict_review) bleibt bewusst ASSISTANT_PLUS.
    E("core:offline_conflict_list", "public", get=ALL_AUTH, anonymous_ok=True),
    E("core:offline_conflict_shell", "public", get=ALL_AUTH, anonymous_ok=True),
    E(
        "core:offline_conflict_review",
        "offline-api",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_event.pk"),),
        idor_exempt="Reines Scaffold ohne DB-Lookup/PII (Docstring der View)",
    ),
    # --- M4 Import (Refs #1060) -------------------------------------------
    # Masseningest legt in einem Schritt hunderte Klientendatensaetze an —
    # bewusst ADMIN_ONLY (Anwendungsbetreuung), kein Lead/Staff, kein
    # super_admin (der hat keine Facility). Kein Sudo: Konvention ist Sudo nur
    # bei PII-Export und Auth-Risiko, nicht beim Schreiben.
    E("core:import_list", "import", get=ADMIN_ONLY),
    E("core:import_create", "import", get=ADMIN_ONLY, post=ADMIN_ONLY),
    E(
        "core:import_detail",
        "import",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "import_job.pk"),),
        idor=(("pk", "foreign_import_job.pk"),),
    ),
    E(
        "core:import_mapping",
        "import",
        get=ADMIN_ONLY,
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "import_job.pk"),),
        idor=(("pk", "foreign_import_job.pk"),),
    ),
    E(
        "core:import_preview",
        "import",
        get=ADMIN_ONLY,
        url_kwargs=(("pk", "import_job.pk"),),
        idor=(("pk", "foreign_import_job.pk"),),
    ),
    E(
        "core:import_commit",
        "import",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "import_job.pk"),),
        idor=(("pk", "foreign_import_job.pk"),),
    ),
    E(
        "core:import_abort",
        "import",
        post=ADMIN_ONLY,
        url_kwargs=(("pk", "import_job.pk"),),
        idor=(("pk", "foreign_import_job.pk"),),
    ),
    # --- M4 Rectification (Refs #1060) ------------------------------------
    # ADR-041: Antrag stellen ist Fachkraft-Arbeit (staff+), pruefen ist
    # Leitungsarbeit (lead+) — die beiden Stufen sind das Vier-Augen-Prinzip.
    # Die Assistenz bleibt aussen vor: sie dokumentiert, entscheidet aber nicht
    # ueber Datenrichtigkeit.
    E(
        "core:rectification_create",
        "rectification",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
    E("core:rectification_list", "rectification", get=LEAD_PLUS),
    E(
        "core:rectification_review",
        "rectification",
        get=LEAD_PLUS,
        post=LEAD_PLUS,
        url_kwargs=(("pk", "rectification_request.pk"),),
        idor=(("pk", "foreign_rectification_request.pk"),),
        # Ein leerer POST ohne ``action`` ist ein Fachfehler (unbekannte Aktion)
        # und antwortet mit 400 auf derselben Seite — kein 403/404.
        extra_ok=(400,),
    ),
)

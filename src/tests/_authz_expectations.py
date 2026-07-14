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
    # ---- public (anonym erreichbar) -------------------------------------
    E("health", "public", get=ALL_AUTH, anonymous_ok=True),
    E("robots_txt", "public", get=ALL_AUTH, anonymous_ok=True),
    E("csp_report", "public", post=ALL_AUTH, anonymous_ok=True),
    E("login", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True),
    E("service_worker", "public", get=ALL_AUTH, anonymous_ok=True),
    E("manifest", "public", get=ALL_AUTH, anonymous_ok=True),
    E("offline_fallback", "public", get=ALL_AUTH, anonymous_ok=True),
    E("set_language", "public", post=ALL_AUTH, anonymous_ok=True),
    E("password_reset", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True),
    E("password_reset_done", "public", get=ALL_AUTH, anonymous_ok=True),
    E(
        "password_reset_confirm",
        "public",
        get=ALL_AUTH,
        anonymous_ok=True,
        url_kwargs=(("uidb64", "MQ"), ("token", "abc-defghijklmnop")),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    # L4 (Refs #1375): Einladungs-Setup — analog password_reset_confirm, eigener
    # Token-Generator/Ablauf, anonym zugänglich (Token-basiert, kein Objektbezug).
    E(
        "invite_confirm",
        "public",
        get=ALL_AUTH,
        anonymous_ok=True,
        url_kwargs=(("uidb64", "MQ"), ("token", "abc-defghijklmnop")),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    E("password_reset_complete", "public", get=ALL_AUTH, anonymous_ok=True),
    E("core:lockout_recovery_request", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True),
    E("core:lockout_recovery_sent", "public", get=ALL_AUTH, anonymous_ok=True),
    E(
        "core:lockout_recovery_confirm",
        "public",
        get=ALL_AUTH,
        anonymous_ok=True,
        url_kwargs=(("token", "ungueltiges-token"),),
        extra_ok=(404,),
        idor_exempt="Token-basierter Flow, kein Objektbezug",
    ),
    E("core:lockout_recovery_backup_code", "public", get=ALL_AUTH, post=ALL_AUTH, anonymous_ok=True),
    # ---- auth-flow (eingeloggt, alle Rollen) ----------------------------
    E("logout", "auth-flow", post=ALL_AUTH, anonymous_ok=True),
    E("password_change", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("offline_key_salt", "auth-flow", post=ALL_AUTH),
    # POST ohne korrektes Passwort antwortet 403 (Re-Auth-Form, sudo_mode.py) —
    # Authentifizierungs-, keine Autorisierungs-Semantik.
    E("sudo_mode", "auth-flow", get=ALL_AUTH, post=ALL_AUTH, extra_ok=(403,)),
    E("mfa_setup", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("mfa_verify", "auth-flow", get=ALL_AUTH, post=ALL_AUTH),
    E("mfa_settings", "auth-flow", get=ALL_AUTH),
    E("mfa_disable", "auth-flow", post=ALL_AUTH, sudo=True),
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
    E("core:client_list", "facility-read", get=ASSISTANT_PLUS),
    E(
        "core:client_detail",
        "facility-read",
        get=ASSISTANT_PLUS,
        url_kwargs=(("pk", "client_identified.pk"),),
        idor=(("pk", "foreign_client.pk"),),
    ),
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
    E("core:case_create", "facility-write", get=STAFF_PLUS, post=STAFF_PLUS),
    E(
        "core:case_update",
        "facility-write",
        get=STAFF_PLUS,
        post=STAFF_PLUS,
        url_kwargs=(("pk", "case_open.pk"),),
        idor=(("pk", "foreign_case.pk"),),
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
    E("core:retention_dashboard", "lead-admin", get=LEAD_PLUS),
    E("core:retention_bulk_approve", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:retention_bulk_defer", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:retention_bulk_reject", "lead-admin", get=LEAD_PLUS, post=LEAD_PLUS),
    E("core:statistics", "lead-admin", get=LEAD_PLUS),
    E("core:statistics_chart_data", "lead-admin", get=LEAD_PLUS),
    E("core:statistics_external_report", "lead-admin", get=LEAD_PLUS),
    # ---- deletion (Vier-Augen-Löschworkflow, Refs #1053) -------------------
    E("core:deletion_request_list", "deletion", get=LEAD_PLUS),
    E(
        "core:deletion_review",
        "deletion",
        get=CONFIRMER,
        post=CONFIRMER,
        url_kwargs=(("pk", "deletion_request.pk"),),
        idor=(("pk", "foreign_deletion_request.pk"),),
    ),
    # ---- facility-admin -----------------------------------------------------
    E("core:client_trash", "facility-admin", get=ADMIN_ONLY),
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
    E("core:statistics_pdf_export", "export", get=LEAD_PLUS),
    E("core:statistics_jugendamt_export", "export", get=LEAD_PLUS),
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
    # ---- partials-htmx --------------------------------------------------------
    E(
        "core:workitem_status_update",
        "partials-htmx",
        post=ASSISTANT_PLUS,
        url_kwargs=(("pk", "sample_workitem.pk"),),
        idor=(("pk", "foreign_workitem.pk"),),
    ),
    E("core:client_autocomplete", "partials-htmx", get=ASSISTANT_PLUS),
    E("core:event_fields_partial", "partials-htmx", get=ASSISTANT_PLUS),
    # Strichlisten-„+1" (Refs #1349): document_type kommt im POST-Body (nicht als
    # URL-kwarg), daher liefert der matrix-Leer-POST für berechtigte Rollen ein
    # 404 (get_object_or_404) — als extra_ok deklariert. Scoping/IDOR wird
    # body-basiert in test_tally.py geprüft (Muster event_fields_partial).
    E("core:tally_increment", "partials-htmx", post=ASSISTANT_PLUS, extra_ok=(404,)),
    E("core:zeitstrom_feed_partial", "partials-htmx", get=ASSISTANT_PLUS),
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
    # Refs #1408: dedizierter CSRF-Token-Endpoint fuer den Offline-Replay-
    # Refresh-Pfad. Public wie offline_client_shell — der Token ist kein
    # Geheimnis (Django rendert ihn in jedes Formular), PII-frei; bisherige
    # Refresh-Quelle /login/ war ebenfalls public.
    E("core:offline_csrf", "public", get=ALL_AUTH, anonymous_ok=True),
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
    # (authz-Completeness-Gate, Risiko #8 der).
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
)

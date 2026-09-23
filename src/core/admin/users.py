"""Admin fuer User-Verwaltung inkl. Invite-Flow + Unlock-Action (Refs #785, #958)."""

import secrets
import string

from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from core.admin.mixins import FacilityScopedAdminMixin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, User
from core.services.audit import audit_event
from core.services.audit.chain import advisory_lock_id
from core.services.security import is_locked as user_is_locked
from core.services.security import send_invite_email
from core.services.security import unlock as unlock_user
from core.services.user_access import resend_invitation_for_any_user, resend_user_invitation

_INITIAL_PASSWORD_ALPHABET = string.ascii_letters + string.digits
_INITIAL_PASSWORD_LENGTH = 12

# Refs #2018: Grund-Slug der Audit-Zeile eines Admin-Resets. Bewusst KEIN
# ``RevocationReason``-Member: die Enum in
# ``core/services/security/credentials.py`` steuert ueber
# ``REVOCATION_EFFECTS``, WAS ein Ausloeser bewirkt (Salt-Rotation,
# Session-Flush, Zwangswechsel, Lockout-Aufhebung). Der Admin-Reset soll
# genau nichts davon zusaetzlich ausloesen — das neue Passwort entwertet
# Alt-Sessions ohnehin ueber den Session-Auth-Hash und den daraus
# abgeleiteten Offline-Schluessel (Begruendung im Modul-Docstring von
# ``credentials.py``), und ``clear_sudo`` traefe hier die falsche Session
# (die des handelnden Admins, nicht die des Ziels). Was fehlte, war
# ausschliesslich die Rechenschaftszeile.
_ADMIN_PASSWORD_RESET_REASON = "admin_password_reset"  # noqa: S105 — Audit-Slug, kein Passwort


class UserAddForm(UserCreationForm):
    """Add-Form ohne Passwort-Eingabe (Refs #2073).

    ``UserAdmin.save_model`` (s.u.) vergibt das Passwort beim Anlegen
    IMMER selbst — entweder unusable + Einladungs-Link per E-Mail, oder als
    generiertes Klartext-Initialpasswort im Fallback ohne E-Mail. Ein hier
    eingetipptes Passwort würde von ``save_model`` also ohnehin überschrieben.

    Entscheid (statt ``add_fieldsets`` um ``password1``/``password2``/
    ``usable_password`` zu ergänzen, Djangos Standardmuster): die Felder ganz
    entfernen. Ein Formular, das ein Passwort abfragt, dessen Eingabe
    garantiert verworfen wird, ist irreführende UX — die Person tippt ein
    Passwort ein, das nirgends landet. `add_fieldsets` bot diese Felder
    ohnehin schon nicht an; der eigentliche Fehler war, dass sie trotzdem als
    ``required`` in der Formularvalidierung standen (Djangos
    ``SetPasswordMixin.validate_passwords()`` — genau das reparieren wir hier).

    ``validate_passwords()`` ist der zentrale Hook, den ``SetPasswordMixin``
    (Basisklasse von ``UserCreationForm``) für die Passwort-Pflicht aufruft;
    er setzt ``cleaned_data["set_usable_password"]``, das ``_post_clean``
    (Passwort-Validatoren) UND ``save()`` (Passwort setzen) auswerten. Diese
    eine Override-Stelle reicht daher aus, um beide Folgeschritte sauber
    abzuschalten, ohne ``_post_clean``/``save`` selbst zu duplizieren.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("password1", "password2", "usable_password"):
            self.fields.pop(field_name, None)

    def validate_passwords(self, *args, **kwargs):
        del args, kwargs  # ungenutzt: es gibt keine Passwort-Felder mehr zu validieren.
        self.cleaned_data["set_usable_password"] = False


@admin.action(description="Account-Sperre aufheben (Login freigeben)")
def unlock_selected_users(modeladmin, request, queryset):
    """Admin-Action: LOGIN_UNLOCK-AuditLog schreiben, damit is_locked() künftig
    alle bisherigen LOGIN_FAILED-Einträge dieser User ignoriert."""
    from core.signals.audit import get_client_ip

    unlocked = 0
    for user in queryset:
        # N9 (Refs #1446): BEWUSST ohne ip_address — die Admin-Aktion fragt
        # „ist dieser Account überhaupt (von irgendeiner IP) gesperrt?" und soll
        # ihn dann ganz freigeben. Die IP-scoped-Prüfung (nur die aktuelle
        # Admin-IP) wäre hier falsch — sie würde reale Sperren von fremden IPs
        # übersehen. Legacy-Verhalten (alle Fehlversuche zählen) ist korrekt.
        if user_is_locked(user):
            unlock_user(user, unlocked_by=request.user, ip_address=get_client_ip(request))
            unlocked += 1
    if unlocked:
        messages.success(request, f"{unlocked} Account-Sperre(n) aufgehoben.")
    else:
        messages.info(request, "Keine der ausgewählten Accounts war gesperrt.")


@admin.action(description="MFA für ausgewählte Nutzende verpflichtend setzen")
def enforce_mfa_for_selected_users(modeladmin, request, queryset):
    """Setzt ``mfa_required=True`` für die ausgewählten Nutzenden (Refs #689).

    Opt-in-Weg für Bestandskunden („MFA für alle aktivieren"). Das Scoping wird
    HIER explizit erzwungen: Row Level Security deckt die ``user``-Tabelle NICHT
    ab (nur facility-übergreifende Fachtabellen), daher darf ein facility_admin
    über einen manipulierten Changelist-Request keine fremden Konten treffen —
    die Aktion filtert für Nicht-super_admins hart auf die eigene Einrichtung.

    Refs #2019: Der frühere rohe ``queryset.update(mfa_required=True)`` war
    schnell, feuerte aber keine Signale und lief an ``save()`` vorbei — die
    Sammelaktion schwächte bzw. verschärfte den Login-Schutz fremder Konten
    also spurlos, während Rollenwechsel/Deaktivierung längst
    actor-attribuierte Zeilen schreiben (Refs #1369). Sie läuft jetzt je
    Konto über ``save(update_fields=…)`` und schreibt **eine Zeile je
    tatsächlich umgestelltem Konto**.

    ``USER_ACCESS_UPDATED`` mit ``changed_fields=["mfa_required"]`` ist exakt
    die Form, die die Fach-UI für dieselbe Änderung schreibt
    (``services/user_access.update_user_access``, Refs #1871) — der technische
    Admin-Fallback und der Fach-Weg bleiben unter /audit/ dieselbe Zeile, statt
    zwei Vokabeln für einen Vorgang zu führen. Der Service selbst ist hier
    nicht aufrufbar: er verlangt eine Einrichtung im Kontext, die Aktion muss
    aber auch dem einrichtungslosen super_admin offenstehen.

    Die Re-Filter-Logik (``mfa_required=False``) bleibt: ohne sie erzeugte
    jeder Sammelklick eine Zeile je bereits pflichtigem Konto — Audit-Rauschen,
    das die echten Änderungen zudeckt.

    Cross-Review — **Transaktionsklammer + Zeilensperren**:
    Django umschliesst Admin-Actions NICHT automatisch mit
    ``transaction.atomic``. Ohne eigene Klammer blieb ein Flip bestehen, dessen
    Audit-Zeile scheiterte, und bei mehreren Konten entstand ein partiell
    angewandter Lauf. Ohne Sperre konnten zwei parallele Aktionen denselben
    ``mfa_required=False``-Stand materialisieren, beide flippen und beide eine
    Zeile schreiben — zwei Belege fuer EINE tatsaechliche Aenderung, was die
    Zusage „eine Zeile je tatsaechlich umgestelltem Konto" bricht. Die Sperre
    serialisiert den zweiten Klick; danach filtert sein Re-Filter die bereits
    umgestellten Konten korrekt heraus.

    Re-Review — **Ordnung nach dem tatsaechlichen
    Advisory-Lock-Key**: Jeder Audit-Insert nimmt in
    ``services/audit/chain.py`` einen **transaktionsweiten** Advisory-Lock PRO
    FACILITY (``pg_advisory_xact_lock``), der bis zum Commit gehalten wird —
    innerhalb dieser Klammer sammeln sich also die Facility-Locks aller
    auditierten Konten an. Ein super_admin sieht einrichtungsuebergreifend
    (``admin_site.scope_to_facility``), sein Lauf kann somit mehrere
    Facility-Locks halten. Ohne feste Erwerbsreihenfolge deadlocken zwei
    parallele Laeufe — sogar bei voellig disjunkten Kontomengen, wo die
    Zeilensperren nichts serialisieren.

    Nach ``facility_id`` zu ordnen reicht dafuer **nicht**: gesperrt wird
    ``hashtext(name)``, und ``hashtext`` liefert nur 32 Bit, bildet UUIDs also
    nicht injektiv ab. Bei einer Kollision ``h(A) = h(C) = X``, ``h(B) = Y`` mit
    ``A < B < C`` naehme ein Lauf ueber ``{A, B}`` die Keys ``X -> Y``, ein Lauf
    ueber ``{B, C}`` dagegen ``Y -> X`` — der Zyklus bliebe offen. Sortiert wird
    deshalb nach ``(advisory_lock_id, facility_id)``: nach dem echten Lock-Key,
    mit der facility_id als deterministischem Tie-Break bei Kollision. Gleiche
    Keys liegen damit benachbart, der zweite Erwerb desselben xact-Locks ist
    ohnehin ein No-op.

    **Invariante fuer kuenftige Aenderungen:** Wer hier Audit-Zeilen ueber
    mehrere Facilities schreibt, ordnet nach ``advisory_lock_id`` — nicht nach
    facility_id und nicht nach pk. Die SQL-Ordnung ``(facility_id, pk)``
    darunter bleibt fuer die ZEILENsperren zustaendig (Muster #1022 B2 aus
    ``services/case/workitems.py``); Pythons stabile Sortierung erhaelt sie
    innerhalb einer Facility.
    """
    from core.signals.audit import get_client_ip

    if not getattr(request.user, "is_super_admin", False):
        current = getattr(request, "current_facility", None)
        if current is None:
            messages.error(request, "Keine Einrichtung im Kontext — Aktion abgebrochen.")
            return
        queryset = queryset.filter(facility=current)

    actor = request.user
    ip_address = get_client_ip(request)
    updated = 0
    with transaction.atomic():
        # ``list()`` erzwingt, dass ALLE Zeilensperren stehen, bevor der erste
        # Audit-Insert den ersten Facility-Lock nimmt — Sperrklassen bleiben so
        # in fester Reihenfolge (erst Zeilen, dann Facilities).
        betroffen = list(queryset.select_for_update().filter(mfa_required=False).order_by("facility_id", "pk"))
        # Umsortieren auf die Lock-Key-Ordnung: je Facility EIN Lookup, nicht
        # je Konto. ``None`` (Nicht-PostgreSQL) faellt auf 0 — dort nimmt
        # ``assign_chain_fields`` gar keinen Lock, die Ordnung ist dann
        # gleichgueltig und bleibt ueber den Tie-Break trotzdem deterministisch.
        lock_ids = {fid: (advisory_lock_id(fid) or 0) for fid in {obj.facility_id for obj in betroffen}}
        betroffen.sort(key=lambda obj: (lock_ids[obj.facility_id], str(obj.facility_id)))
        for user in betroffen:
            user.mfa_required = True
            # Refs #2172: Der Bulk-Pfad besitzt bereits seinen direkten,
            # IP-attribuierten Audit-Write. Das Signal auditiert deshalb nur
            # Einzel-/Command-Saves und wird hier explizit unterdrückt, damit
            # die Sammelaktion weiterhin genau eine Zeile je Konto schreibt.
            user._audit_actor = actor
            user._audit_skip_mfa_required = True
            user.save(update_fields=["mfa_required"])
            audit_event(
                AuditLog.Action.USER_ACCESS_UPDATED,
                user=user,
                facility=user.facility,
                actor=actor,
                target_obj=user,
                detail={
                    "changed_fields": ["mfa_required"],
                    "username": user.username,
                    "actor_id": actor.pk,
                    "actor_username": actor.get_username(),
                },
                ip_address=ip_address,
            )
            updated += 1
    if updated:
        messages.success(request, f"MFA für {updated} Nutzende verpflichtend gesetzt.")
    else:
        messages.info(request, "Keine Änderung — alle ausgewählten Nutzenden waren bereits MFA-pflichtig.")


@admin.action(description="Einladung erneut senden (Setup-Link)")
def resend_invitation_for_selected_users(modeladmin, request, queryset):
    """Ausstehende Einladungen rollen- und einrichtungsgetreu erneut senden."""
    from core.signals.audit import get_client_ip

    actor = request.user
    ip_address = get_client_ip(request)
    is_super_admin = getattr(actor, "is_super_admin", False)
    facility = getattr(request, "current_facility", None) or getattr(actor, "facility", None)

    for target in queryset:
        if not is_super_admin and (
            facility is None
            or not getattr(actor, "is_facility_admin", False)
            or target.facility_id != facility.pk
            or target.is_super_admin
        ):
            messages.warning(request, f"{target.username}: Erneuter Einladungsversand nicht erlaubt.")
            continue

        try:
            if is_super_admin:
                sent = resend_invitation_for_any_user(target, actor=actor, ip_address=ip_address)
            else:
                sent = resend_user_invitation(target, facility=facility, actor=actor, ip_address=ip_address)
        except (PermissionDenied, ValidationError) as exc:
            reason = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.warning(request, f"{target.username}: {reason}")
            continue

        if sent:
            messages.success(request, f"Einladungslink an {target.email} gesendet")
        else:
            messages.error(request, f"Einladung an {target.email or target.username} konnte nicht versendet werden.")


@admin.register(User, site=anlaufstelle_admin_site)
class UserAdmin(FacilityScopedAdminMixin, BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserAddForm
    change_password_form = AdminPasswordChangeForm
    list_display = ("username", "display_name", "role", "facility", "is_active")
    list_filter = ("role", "facility", "is_active", "is_staff")
    actions = [unlock_selected_users, enforce_mfa_for_selected_users, resend_invitation_for_selected_users]
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Anlaufstelle",
            {
                "fields": (
                    "role",
                    "facility",
                    "display_name",
                    "phone",
                    "notes",
                    "must_change_password",
                    "mfa_required",
                    "preferred_language",
                    # Refs #1053: Grant/Revoke-UI für das Vier-Augen-Recht;
                    # jede Änderung wird via Signal audit-geloggt.
                    "can_confirm_deletion",
                ),
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "fields": ("username", "email"),
            },
        ),
        (
            "Anlaufstelle",
            {
                "fields": ("role", "facility", "display_name"),
            },
        ),
    )

    # Django-native Permission-Felder (Refs #1371, Sicherheitsreview 2026-07-02;
    # adversariales Review 2026-07-11): is_active bleibt fuer ALLE Editoren (auch
    # facility_admin) sichtbar/setzbar — Accounts sperren ist Teil der Facility-
    # Verwaltung. is_staff/is_superuser/groups/user_permissions werden dagegen fuer
    # NIEMANDEN ausgegeben, auch nicht fuer super_admin. Begruendung:
    #   1. Die App nutzt Djangos Permission-Framework nicht — Autorisierung laeuft
    #      ausschliesslich ueber `role` (s. `is_super_admin`-Property).
    #   2. Der Admin-Zugang haengt am Rollen-Gate der Custom-AdminSite
    #      (`AnlaufstelleAdminSite.has_permission` -> `_has_admin_role`), NICHT an
    #      Djangos is_staff/is_superuser (lead/staff/assistant werden geblockt,
    #      selbst mit is_staff=True).
    #   3. Die zur Laufzeit erzwungene Invariante aus #1271/#1297 (Bootstrap-Guard
    #      + CRITICAL-Compliance-Check via `_app_superuser_checks`) verbietet
    #      is_superuser=True fuer App-User ganz.
    # Der legitime Verwaltungsbedarf dieser vier Felder ist damit null; jede
    # Setzbarkeit — auch durch super_admin — waere nur eine latente Privilege-
    # Escalation und ein Widerspruch zur is_superuser-Invariante.
    _HIDDEN_PERMISSION_FIELDS = ("is_staff", "is_superuser", "groups", "user_permissions")

    def get_fieldsets(self, request, obj=None):
        """Djangos native Permission-Felder fuer ALLE Editoren entfernen (Refs #1371).

        is_staff/is_superuser/groups/user_permissions werden bedingungslos aus
        Fieldset UND Form entfernt — auch fuer super_admin, ohne Fruehausstieg.
        Begruendung s. `_HIDDEN_PERMISSION_FIELDS`: die App nutzt Django-Perms
        nicht, der Admin-Zugang haengt am Rollen-Gate der Custom-AdminSite statt
        an is_staff, und die #1271/#1297-Invariante verbietet is_superuser=True
        fuer App-User ganz — der legitime Verwaltungsbedarf ist null.

        `ModelAdmin.get_form` berechnet seine Formfelder aus
        `flatten_fieldsets(self.get_fieldsets(...))` — entfernte Felder existieren
        also serverseitig gar nicht erst im Form. Ein POST-Smuggling-Versuch
        (``is_superuser=on`` im Body ohne zugehoeriges Formfeld) hat dadurch
        keine Wirkung, nicht nur ein UI-Verstecken.
        """
        fieldsets = super().get_fieldsets(request, obj)
        filtered = []
        for name, options in fieldsets:
            fields = options.get("fields", ())
            remaining = tuple(f for f in fields if f not in self._HIDDEN_PERMISSION_FIELDS)
            if remaining == fields:
                filtered.append((name, options))
                continue
            filtered.append((name, {**options, "fields": remaining}))
        return tuple(filtered)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        """Rollen-Vergabe begrenzen (A2.1, Refs #1020).

        facility_admin darf die installationsweite Rolle super_admin nicht
        vergeben. Die eingeschraenkten Choices wirken als UI-Restriktion **und**
        serverseitige Validierung — ein POST ``role=super_admin`` scheitert an
        Djangos Choice-Validierung.
        """
        if db_field.name == "role":
            kwargs["choices"] = self.admin_site.assignable_roles(request)
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    # facility-FK-Scoping (A2.1) ist nach A2.2 zentral im FacilityScopedAdminMixin
    # (formfield_for_foreignkey + save_model-Erzwingung) — Single Source of Truth.

    @staticmethod
    def _is_protected_super_admin(request, obj):
        """super_admin-Konto, das ein Nicht-super_admin nicht verwalten darf (A2.1)."""
        return obj is not None and obj.is_super_admin and not getattr(request.user, "is_super_admin", False)

    def has_change_permission(self, request, obj=None):
        """facility_admin darf super_admin-Konten nicht aendern (A2.1, Refs #1020)."""
        if self._is_protected_super_admin(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """facility_admin darf super_admin-Konten nicht loeschen (A2.1, Refs #1020)."""
        if self._is_protected_super_admin(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        """Token-basierter Invite-Flow beim User-Anlegen.

        Primär: User ohne Passwort anlegen (`set_unusable_password`) und
        Einladungs-E-Mail mit Password-Reset-Token an `obj.email` versenden.

        Fallback ohne E-Mail: Klartext-Initialpasswort generieren und in
        der Admin-Oberfläche anzeigen — mit Warnhinweis, dass dieser Weg
        unsicherer ist. Siehe Issue #528.
        """
        # Refs #1369: Der handelnde Admin wird als transientes Instanz-Attribut
        # durchgereicht (gleiches Muster wie ``_audit_old_*`` in signals/audit.py).
        # Der ``post_save``-Signal-Handler liest es und schreibt es als ``actor``
        # in die ziel-attribuierten Privileg-Audits (Rollenwechsel/Deaktivierung/
        # Loeschbestaetigung) — sonst traegt der immutable Log nur das Ziel und
        # liest sich, als haette es sich selbst das Flag gesetzt. Nicht-request-
        # getriebene ORM-Pfade (Command/Service) setzen dieses Attribut nicht und
        # werden im Signal explizit als ``system`` markiert.
        obj._audit_actor = request.user

        # Defense-in-Depth gegen umgangene Form-Validierung (A2.1, Refs #1020):
        # nur super_admin darf die installationsweite Rolle super_admin vergeben.
        if obj.role == User.Role.SUPER_ADMIN and not getattr(request.user, "is_super_admin", False):
            raise PermissionDenied("Nur super_admin darf die Rolle „Systemadministration“ vergeben.")

        if change:
            super().save_model(request, obj, form, change)
            return

        obj.must_change_password = True

        if obj.email:
            obj.set_unusable_password()
            super().save_model(request, obj, form, change)
            try:
                send_invite_email(obj)
            except Exception:  # pragma: no cover - Mail-Backend-Fehler
                if obj.role == User.Role.SUPER_ADMIN:
                    retry_hint = "Einladung im Django-Admin über die Aktion „Einladung erneut senden“ auslösen."
                else:
                    retry_hint = "Einladung unter Verwaltung → Zugänge per „Einladung erneut senden“ auslösen."
                messages.error(
                    request,
                    f"Einladungs-E-Mail konnte nicht versendet werden. {retry_hint}",
                )
                return
            messages.success(
                request,
                f"Einladungslink wurde an {obj.email} gesendet.",
            )
        else:
            password = "".join(secrets.choice(_INITIAL_PASSWORD_ALPHABET) for _ in range(_INITIAL_PASSWORD_LENGTH))
            obj.set_password(password)
            super().save_model(request, obj, form, change)
            messages.warning(
                request,
                "Kein E-Mail-Adresse hinterlegt — Fallback auf Klartext-Initialpasswort. "
                "Sicherer Weg: E-Mail nachtragen und Einladung erneut versenden.",
            )
            messages.success(
                request,
                f"Initialpasswort für {obj.username}: {password} — Bitte notieren, es wird nicht erneut angezeigt!",
                extra_tags="password-display",
            )

    def user_change_password(self, request, id, form_url=""):
        """Admin-Reset eines FREMDEN Passworts auditieren (Refs #2018).

        Djangos ``UserAdmin.user_change_password`` ruft ``form.save()`` direkt
        auf und umgeht ``save_model()`` vollstaendig — der #1369-Actor-
        Mechanismus (``obj._audit_actor``) greift hier also nicht, und der
        ``post_save``-Diff in ``signals/audit.py`` verfolgt nur Modellfelder
        wie ``role``, ``is_active``, ``mfa_required`` und
        ``can_confirm_deletion``, nicht aber den Passwort-Hash. Uebrig blieb Djangos
        **mutierbares** ``admin.LogEntry``; im append-only, HMAC-verketteten
        AuditLog stand nichts, obwohl ein Credential-Wechsel im Threat-Model
        als Privilegien-Ereignis gilt (Rigor von ``USER_ROLE_CHANGED``, #1369).

        Erkannt wird der Wechsel am **Passwort-Hash vorher/nachher**, nicht am
        Response-Typ. Djangos View kennt zwei Erfolgspfade (neues Passwort und
        „Passwort-Anmeldung deaktivieren") und zwei Redirect-Zweige, von denen
        einer (widerspruechliche Formulardaten) gerade **kein** Wechsel ist —
        der Hash-Vergleich trifft alle vier Faelle richtig und bleibt stabil,
        wenn Django die Zweige umbaut. Der Hash wird ausschliesslich
        verglichen; er landet nie im ``detail``.

        Warum ``CREDENTIALS_REVOKED``: der Vorgang widerruft das bisherige
        Passwort des Ziels samt allem, was daran hing — dieselbe Frage, die
        die Aktion seit #1965 unter /audit/ beantwortet („wann wechselten hier
        zuletzt Zugangsdaten"). Der Grund-Slug unterscheidet den Admin-Reset
        von den drei Selbstbedienungs-Wegen. Kein neuer ``Action``-Wert und
        damit keine Migration.

        Cross-Review — **Transaktionsklammer + Zeilensperre**:
        Diese Django-Sonder-View laeuft weder ueber ``changeform_view`` noch
        unter ``ATOMIC_REQUESTS`` (im Repo nicht gesetzt). Ohne eigene Klammer
        blieb ein Passwortwechsel bestehen, dessen Audit-Insert scheiterte —
        also exakt die Nachweisluecke, die dieses Ticket schliessen soll. Und
        ohne Sperre konnte zwischen dem Vorher- und dem Nachher-Read ein
        paralleler Wechsel einlaufen und diesem Admin zugeschrieben werden,
        sogar bei einem ungueltigen POST (der Hash haette sich ja geaendert).
        Beides deckt jetzt ein ``transaction.atomic`` samt
        ``select_for_update`` auf der Zielzeile ab: Passwortwechsel und
        Audit-Zeile committen gemeinsam oder gar nicht.

        Re-Review, Deadlock-Betrachtung: Dieser Pfad ist vom
        Cross-Facility-Deadlock der Bulk-Aktionen **nicht** betroffen. Er
        behandelt genau EIN Zielkonto, schreibt also genau EINE Audit-Zeile und
        nimmt damit genau EINEN Facility-Advisory-Lock
        (``services/audit/chain.py``) — ein einzelner Lock kann keinen Zyklus
        bilden. Er haelt zudem dieselbe Sperr-KLASSEN-Reihenfolge wie die
        Bulk-Pfade ein (erst die Zeilensperre, dann der Facility-Lock), sodass
        auch zwischen diesem Pfad und einem parallelen Bulk-Lauf kein Zyklus
        entsteht. Diese Reihenfolge ist die Invariante — wer hier weitere
        Audit-Zeilen ueber mehrere Facilities ergaenzt, muss sie nach
        ``facility_id`` ordnen.
        """
        # Lazy wie in ``unlock_selected_users``: ``core.signals.audit`` wird
        # erst nach ``AppConfig.ready()`` importierbar.
        from core.signals.audit import get_client_ip

        if request.method != "POST":
            # GET rendert nur das Formular — nichts zu sperren, nichts zu
            # auditieren. Eine Sperre haette hier nur Kosten und wuerde bis
            # zum Rendern des TemplateResponse gehalten.
            return super().user_change_password(request, id, form_url)

        target_pk = unquote(id)
        with transaction.atomic():
            before = self._locked_user(request, target_pk)
            old_hash = before.password if before is not None else None

            response = super().user_change_password(request, id, form_url)

            if old_hash is None:
                return response
            # Innerhalb der Sperre gelesen: der Hash kann sich seit dem
            # Vorher-Read nur durch DIESEN Request geaendert haben.
            target = self.get_object(request, target_pk)
            if target is None or target.password == old_hash:
                return response

            actor = request.user
            audit_event(
                AuditLog.Action.CREDENTIALS_REVOKED,
                user=target,
                facility=getattr(target, "facility", None),
                actor=actor,
                target_obj=target,
                # Nur Allowlist-Schluessel (Refs #1944) und keinerlei Passwort-
                # Material: ``actor_username``/``username`` bleiben lesbar, auch
                # wenn eines der beiden Konten spaeter geloescht wird (beide FKs
                # sind SET_NULL) — Refs #1369.
                detail={
                    "reason": _ADMIN_PASSWORD_RESET_REASON,
                    "username": target.username,
                    "actor_id": actor.pk,
                    "actor_username": actor.get_username(),
                },
                ip_address=get_client_ip(request),
            )
            return response

    def _locked_user(self, request, object_id):
        """Zielkonto per ``select_for_update`` fuer die laufende Transaktion sperren.

        Spiegelt Djangos ``ModelAdmin.get_object`` (inkl. der Toleranz gegen
        ungueltige IDs) und bleibt ueber ``get_queryset`` facility-gescopt —
        nur eben mit Zeilensperre. ``None`` bedeutet „nicht vorhanden oder
        nicht sichtbar"; der nachfolgende ``super()``-Aufruf beantwortet
        diesen Fall selbst mit 404/403.
        """
        field = self.model._meta.pk
        try:
            pk = field.to_python(object_id)
        except (ValidationError, ValueError):
            return None
        return self.get_queryset(request).select_for_update().filter(pk=pk).first()

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)
        response["Cache-Control"] = "no-store"
        return response

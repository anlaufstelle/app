"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.user_access``.

Quelle: Vollmutation #2174, Batch ``user_access-1``
(``welle-l-0.99/reports/m-scratch/batch-user_access-1.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-user_access-1/triage-099-user_access-1.json``).
65 Survivor-Mutanten in sieben Funktionen: ``_assert_actor`` (11),
``_locked_user_ids`` (12), ``list_user_access`` (4), ``_deliver_invitation``
(10), ``create_invited_user`` (9), ``update_user_access`` (8),
``resend_user_invitation`` (11).

**Reine Meldungstext-Mutationen** (``PermissionDenied``/``ValidationError``-
Argumente: Groß-/Kleinschreibung, ``XX…XX``-Marker, ganze Meldung durch
``None`` ersetzt) sind ``skip`` — kein Aufrufer vergleicht diesen Text, er
fließt nirgends in Hash/Validator/Vergleich/E-Mail-Inhalt ein (siehe Bericht
für die vollständige Liste).

**Durch #2223 überholt:** ``_deliver_invitation`` protokollierte im
Mutantenbaum (Stand 25685845) noch mit ``logger.exception(<Text>, extra=…)``;
F-1 hat das auf ``logger.error(<Text>, extra=…, exc_info=False)`` umgestellt
(fail-closed, keine Backend-Interna im Log). Neun Survivor-Mutanten
(``mutmut_3``–``mutmut_11``) mutieren ausschließlich diesen inzwischen
ersetzten Aufruf — die betroffene Zeile existiert im aktuellen Worktree nicht
mehr in dieser Form, ein Pin würde altes Verhalten festschreiben. Nur
``mutmut_12`` (``return True`` statt ``return False`` im ``except``-Zweig)
bleibt unverändert und wird gepinnt.

**Zwei Äquivalenz-Fälle** (Diff gegen ``__mutmut_orig`` UND Testlauf unter der
Mutante geprüft):

- ``xǁ_locked_user_idsǁ__mutmut_15``: verliert ``user_id__in=user_ids`` in der
  ``LOGIN_UNLOCK``-Teilabfrage. ``last_unlocks`` bekommt dadurch zusätzliche
  Einträge fremder User — die Funktion liest daraus aber ausschließlich per
  ``.get(user_id)`` für ``user_id`` aus der übergebenen ``users``-Liste;
  zusätzliche, ungenutzte Schlüssel sind für keinen Aufrufer beobachtbar.
- ``xǁlist_user_accessǁ__mutmut_8``: verliert ``"pk"`` als drittes
  ``order_by``-Kriterium. ``User.username`` ist (geerbt von
  ``AbstractUser``) global ``unique=True`` — das Paar
  (``display_name``, ``username``) ist damit bereits eindeutig, ein
  ``pk``-Tiebreak kann nie greifen.

Zusicherungen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die eine ``in``-Prüfung bzw.
``pytest.raises(match=…)`` übersehen würde).
"""

from __future__ import annotations

import types
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import AuditLog, User
from core.services.security import LOCKOUT_THRESHOLD
from core.services.security.credentials import RevocationReason
from core.services.user_access import (
    _assert_actor,
    _deliver_invitation,
    _locked_user_ids,
    create_invited_user,
    list_user_access,
    resend_user_invitation,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _legacy_pending_user(facility, username="legacy_pending"):
    """Noch nicht eingerichteter Zugang MIT nutzbarem Alt-Passwort.

    Anders als ``_pending_invite`` in ``test_user_access_hub.py`` (dort immer
    ``set_unusable_password``) braucht ``resend_user_invitation``s
    Entwertungs-Zweig (``target.has_usable_password()``) hier bewusst ein
    NUTZBARES Passwort.
    """
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.org",
        role=User.Role.STAFF,
        facility=facility,
        must_change_password=True,
    )
    user.set_password("legacy-initial-pw")
    user.save(update_fields=["password"])
    return user


def _failed_login(facility, user, timestamp=None):
    return AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"username": user.username},
        **({"timestamp": timestamp} if timestamp is not None else {}),
    )


def _unlock_event(facility, user, timestamp=None):
    AuditLog.objects.create(
        facility=facility,
        user=user,
        action=AuditLog.Action.LOGIN_UNLOCK,
        detail={"unlocked_by": "test"},
        **({"timestamp": timestamp} if timestamp is not None else {}),
    )


class TestAssertActorGegenFremdeAkteursobjekte:
    """``xǁ_assert_actorǁ__mutmut_9/12/19/22/28`` — die Berechtigungswache
    ruft ``getattr(…, default)`` fünfmal auf. Fehlt der 3-Arg-Default (Trailing-
    Komma-Mutanten), crasht eine 2-Arg-``getattr``-Form mit ``AttributeError``
    statt mit ``PermissionDenied`` abzulehnen; flippt ein Default auf ein
    Truthy (``True``/``None``→„vorhanden“), lässt die Wache einen eigentlich
    abzulehnenden Akteur durch. Beide Klassen prüfen wir mit Stand-in-
    Objekten, denen genau das fragliche Attribut fehlt.
    """

    def test_missing_is_authenticated_attribute_denies_conservatively(self, facility):
        """Tötet ``mutmut_9`` (2-Arg-``getattr`` für ``is_authenticated``,
        crasht mit ``AttributeError``) und ``mutmut_12`` (Default ``True``
        statt ``False`` — laesst einen nicht-authentifizierten Akteur mit
        passendem ``is_facility_admin``/``facility_id`` durch)."""
        actor = types.SimpleNamespace(is_facility_admin=True, facility_id=facility.pk)

        with pytest.raises(PermissionDenied):
            _assert_actor(facility, actor)

    def test_missing_is_facility_admin_attribute_denies_conservatively(self, facility):
        """Tötet ``mutmut_19`` (2-Arg-``getattr`` für ``is_facility_admin``,
        crasht mit ``AttributeError``) und ``mutmut_22`` (Default ``True``
        statt ``False`` — laesst einen Nicht-Admin mit passender
        ``facility_id`` durch)."""
        actor = types.SimpleNamespace(is_authenticated=True, facility_id=facility.pk)

        with pytest.raises(PermissionDenied):
            _assert_actor(facility, actor)

    def test_facility_without_pk_attribute_does_not_crash_the_guard(self):
        """Tötet ``mutmut_28`` (2-Arg-``getattr`` für ``facility.pk``): ein
        Facility-Stand-in ohne ``pk``-Attribut lässt das Original still
        durchlaufen (``getattr(…, "pk", None)`` → ``None``); die Mutante
        crasht stattdessen mit ``AttributeError``."""
        facility_stub = types.SimpleNamespace()
        actor = types.SimpleNamespace(is_authenticated=True, is_facility_admin=True, facility_id=None)

        assert _assert_actor(facility_stub, actor) is None


class TestLockedUserIdsQueryIntegritaet:
    """``xǁ_locked_user_idsǁ__mutmut_*`` — die konservative Sperrstatus-
    Schätzung baut zwei Abfragen (letzte Entsperrung je User, Fehlversuche im
    Zeitfenster) und zählt daraus. Jede Mutante hier weicht entweder die
    Filter auf (fremde User/Aktionen/alte Zeitstempel zählen mit) oder
    verwaesserte die Zaehllogik (Reset nach Entsperrung geht verloren,
    Boundary bei exakt gleichem Zeitstempel, Off-by-eins/-zwei je
    Fehlversuch)."""

    def test_unlock_history_does_not_crash_the_lookup(self, facility, staff_user):
        """Tötet ``mutmut_9`` und ``mutmut_10``: beide werfen
        ``.values_list(...)`` auf ein einzelnes Feld zurück; ``dict()`` über
        eine Liste von 1-Tupeln wirft ``ValueError``, sobald mindestens eine
        Entsperr-Zeile existiert."""
        _unlock_event(facility, staff_user)

        assert _locked_user_ids([staff_user]) == set()

    def test_failures_before_the_last_unlock_do_not_count_again(self, facility, staff_user):
        """Tötet ``mutmut_14`` (``action=None`` matcht keine Zeile, da
        ``AuditLog.action`` nicht nullable ist — die Entsperrung bleibt
        unsichtbar), ``mutmut_42`` (``last_unlock`` fest auf ``None``) und
        ``mutmut_43`` (``last_unlocks.get(None)`` — derselbe Nichttreffer).
        Alle drei lassen Fehlversuche VOR der letzten Entsperrung erneut
        mitzählen; mit 8 alten + 2 neuen Fehlversuchen bleibt das Original
        unter der Schwelle (10), die Mutanten erreichen sie."""
        now = timezone.now()
        unlock_at = now - timedelta(minutes=5)
        for offset in range(8):
            _failed_login(facility, staff_user, timestamp=unlock_at - timedelta(seconds=offset + 1))
        _unlock_event(facility, staff_user, timestamp=unlock_at)
        for offset in range(2):
            _failed_login(facility, staff_user, timestamp=unlock_at + timedelta(seconds=offset + 1))

        assert _locked_user_ids([staff_user]) == set()

    def test_other_users_failures_are_not_attributed_to_the_requested_user(self, facility, staff_user, admin_user):
        """Tötet ``mutmut_35``: die Fehlversuchs-Abfrage verliert ihr
        ``user_id__in=user_ids``-Filter und zaehlt JEDEN Fehlversuch der
        Einrichtung — auch die eines Users, der gar nicht in der
        übergebenen Liste steht."""
        for _ in range(LOCKOUT_THRESHOLD):
            _failed_login(facility, admin_user)

        assert _locked_user_ids([staff_user]) == set()

    def test_non_login_failed_actions_are_not_counted(self, facility, staff_user):
        """Tötet ``mutmut_36``: die Fehlversuchs-Abfrage verliert ihr
        ``action=LOGIN_FAILED``-Filter und zaehlt JEDE Audit-Aktion (hier:
        erfolgreiche Anmeldungen, ``LOGIN``) als Fehlversuch mit. Bewusst
        NICHT ``LOGIN_UNLOCK`` verwendet: das wuerde ZUGLEICH die erste
        Teilabfrage (``last_unlocks``) fuellen und den Effekt durch den
        eigenen ``last_unlock``-Vergleich wieder verdecken."""
        for _ in range(LOCKOUT_THRESHOLD):
            AuditLog.objects.create(
                facility=facility,
                user=staff_user,
                action=AuditLog.Action.LOGIN,
                detail={"username": staff_user.username},
            )

        assert _locked_user_ids([staff_user]) == set()

    def test_stale_failures_outside_the_window_are_not_counted(self, facility, staff_user):
        """Tötet ``mutmut_37``: die Fehlversuchs-Abfrage verliert ihr
        ``timestamp__gte=cutoff``-Filter und zaehlt auch laengst abgelaufene
        Fehlversuche (hier: eine Stunde alt, weit ausserhalb der 15-Minuten-
        ``LOCKOUT_WINDOW``)."""
        stale = timezone.now() - timedelta(hours=1)
        for offset in range(LOCKOUT_THRESHOLD):
            _failed_login(facility, staff_user, timestamp=stale - timedelta(seconds=offset))

        assert _locked_user_ids([staff_user]) == set()

    def test_failure_exactly_at_the_unlock_timestamp_does_not_count(self, facility, staff_user):
        """Tötet ``mutmut_46`` (``>=`` statt ``>``): ein Fehlversuch mit
        EXAKT demselben Zeitstempel wie die letzte Entsperrung darf laut
        Original nicht zaehlen (``timestamp > last_unlock``, nicht ``>=``).
        Ein Grenz-Fehlversuch genau auf der Entsperrzeit plus
        ``LOCKOUT_THRESHOLD - 1`` weitere danach bleiben im Original unter
        der Schwelle, die Mutante erreicht sie.

        Die HMAC-Kette (``assign_chain_fields``, Refs #1070) erzwingt strikt
        monotone Zeitstempel je Einrichtung und hebt einen zweiten Eintrag mit
        dem exakt gleichen Zeitstempel bei ``Model.save()`` automatisch um
        1 Mikrosekunde an; ein nachtraegliches ``UPDATE`` blockt der
        Immutability-Trigger (``prevent_auditlog_mutation``). Der Grenzfall
        wird deshalb per ``bulk_create`` angelegt — das ruft ``save()`` nicht
        auf und damit auch nicht die Monotonie-Anhebung, waehrend die
        (hier nullable) Kettenfelder unbesetzt bleiben duerfen."""
        unlock_at = timezone.now() - timedelta(minutes=5)
        _unlock_event(facility, staff_user, timestamp=unlock_at)
        AuditLog.objects.bulk_create(
            [
                AuditLog(
                    facility=facility,
                    user=staff_user,
                    action=AuditLog.Action.LOGIN_FAILED,
                    detail={"username": staff_user.username},
                    timestamp=unlock_at,
                )
            ]
        )
        for offset in range(LOCKOUT_THRESHOLD - 1):
            _failed_login(facility, staff_user, timestamp=unlock_at + timedelta(seconds=offset + 1))

        assert _locked_user_ids([staff_user]) == set()

    def test_count_starts_at_zero_not_one(self, facility, staff_user):
        """Tötet ``mutmut_53`` (``counts.get(user_id, 1) + 1`` statt
        ``counts.get(user_id, 0) + 1``): der erste Fehlversuch zaehlt dadurch
        doppelt. ``LOCKOUT_THRESHOLD - 1`` echte Fehlversuche bleiben im
        Original unter der Schwelle, die Mutante erreicht sie durch den
        Offset-Fehler bei genau einem Fehlversuch weniger."""
        for offset in range(LOCKOUT_THRESHOLD - 1):
            _failed_login(facility, staff_user, timestamp=timezone.now() - timedelta(seconds=offset))

        assert _locked_user_ids([staff_user]) == set()

    def test_each_failure_counts_once_not_twice(self, facility, staff_user):
        """Tötet ``mutmut_54`` (``counts.get(user_id, 0) + 2`` statt ``+ 1``):
        jeder Fehlversuch zaehlt doppelt. Die Haelfte der Schwelle an echten
        Fehlversuchen bleibt im Original unter der Schwelle, die Mutante
        verdoppelt auf die Schwelle."""
        half = LOCKOUT_THRESHOLD // 2
        for offset in range(half):
            _failed_login(facility, staff_user, timestamp=timezone.now() - timedelta(seconds=offset))

        assert _locked_user_ids([staff_user]) == set()


class TestListUserAccessSortierungUndSperrstatus:
    """``xǁlist_user_accessǁ__mutmut_6/7/22`` — Sortierschluessel und der
    ``is_locked``-Vergleich der zurückgegebenen Zeilen."""

    def test_order_by_falls_back_to_username_when_display_name_key_is_dropped(self, facility):
        """Tötet ``mutmut_6`` (``order_by("username", "pk")`` ohne
        ``display_name``): zwei User mit gegenlaeufigem
        Anzeigename-/Login-Alphabet zeigen die Vertauschung."""
        User.objects.create_user(
            username="zulu_login", role=User.Role.STAFF, facility=facility, display_name="Anna Anzeigename"
        )
        User.objects.create_user(
            username="alpha_login", role=User.Role.STAFF, facility=facility, display_name="Berta Anzeigename"
        )

        usernames = [row.user.username for row in list_user_access(facility)]

        assert usernames == ["zulu_login", "alpha_login"]

    def test_order_by_falls_back_to_pk_when_username_tiebreak_is_dropped(self, facility):
        """Tötet ``mutmut_7`` (``order_by("display_name", "pk")`` ohne
        ``username``): bei gleichem Anzeigenamen sortiert das Original nach
        Login-Namen, die Mutante nach Anlage-Reihenfolge (``pk``) — mit
        gegenlaeufiger Anlage-Reihenfolge zum Alphabet zeigt sich der
        Unterschied."""
        first = User.objects.create_user(
            username="zeta_login", role=User.Role.STAFF, facility=facility, display_name="Team"
        )
        second = User.objects.create_user(
            username="alpha_login", role=User.Role.STAFF, facility=facility, display_name="Team"
        )
        assert first.pk < second.pk

        usernames = [row.user.username for row in list_user_access(facility)]

        assert usernames == ["alpha_login", "zeta_login"]

    def test_is_locked_reflects_the_real_lockout_status(self, facility, staff_user, admin_user):
        """Tötet ``mutmut_22`` (``user.pk not in locked`` statt ``in``): der
        gesperrte User muss ``is_locked=True`` zeigen, ein nicht gesperrter
        ``is_locked=False`` — die Mutante vertauscht beide."""
        for _ in range(LOCKOUT_THRESHOLD):
            _failed_login(facility, staff_user)

        rows = {row.user.pk: row.is_locked for row in list_user_access(facility)}

        assert rows[staff_user.pk] is True
        assert rows[admin_user.pk] is False


class TestDeliverInvitationMeldetVersandfehlerAlsFehlschlag:
    """``xǁ_deliver_invitationǁ__mutmut_12`` — der ``except``-Zweig bleibt seit
    #2223 unveraendert bei ``return False``; nur die Protokoll-
    Zeile davor wurde durch den fail-closed-Umbau ersetzt (neun weitere
    Survivor-Mutanten dieser Funktion mutieren ausschließlich diese inzwischen
    andere Zeile und sind ``skip``, siehe Bericht)."""

    def test_email_delivery_failure_is_reported_as_not_sent(self, facility, staff_user, admin_user):
        """Tötet ``mutmut_12`` (``return True`` statt ``return False`` bei
        einer fehlgeschlagenen ``send_invite_email``): ein Versandfehler darf
        niemals als Erfolg zurückgemeldet werden, und es darf kein
        ``USER_INVITE_SENT``-Audit-Eintrag entstehen."""
        with patch("core.services.user_access.send_invite_email", side_effect=Exception("smtp boom")):
            result = _deliver_invitation(staff_user, admin_user, "invite_create", None)

        assert result is False
        assert not AuditLog.objects.filter(action=AuditLog.Action.USER_INVITE_SENT, user=staff_user).exists()


class TestCreateInvitedUserValidierungsfehlerBleibenValidationError:
    """``xǁcreate_invited_userǁ__mutmut_12`` — anders als reine
    Meldungstext-Mutationen (``skip``, siehe Bericht) aendert diese Mutante
    die geworfene EXCEPTION-KLASSE."""

    def test_missing_email_raises_validation_error_not_attribute_error(self, facility, admin_user):
        """Tötet ``mutmut_12`` (``_(None)`` statt ``_("Für den …")``): die
        ``ValidationError`` selbst wird noch geworfen (``gettext_lazy`` ist
        lazy), aber ``.messages`` erzwingt die Auswertung des lazy-Proxys —
        unter der Mutante crasht genau das mit ``AttributeError`` statt den
        Text zu liefern."""
        with pytest.raises(ValidationError) as exc_info:
            create_invited_user(
                facility=facility,
                actor=admin_user,
                username="no_email_user",
                email="",
                display_name="Ohne Mail",
                role=User.Role.STAFF,
                mfa_required=False,
                can_confirm_deletion=False,
            )

        assert exc_info.value.messages == ["Für den Einladungsversand ist eine E-Mail-Adresse erforderlich."]


class TestResendUserInvitationEntwertetNurDasAltPasswort:
    """``xǁresend_user_invitationǁ__mutmut_18/19/20`` — der gezielte
    ``target.save(update_fields=["password"])`` beim Entwerten des
    Alt-Passworts; ``xǁresend_user_invitationǁ__mutmut_21/22/23/24`` — der
    anschliessende ``revoke_credentials``-Aufruf."""

    def test_password_entwertung_speichert_ausschliesslich_das_password_feld(self, facility, admin_user):
        """Tötet ``mutmut_18`` (``update_fields=None`` — vollstaendiger statt
        gezielter Save), ``mutmut_19`` (``update_fields=["XXpasswordXX"]``)
        und ``mutmut_20`` (``update_fields=["PASSWORD"]``) — beide letzteren
        sind keine echten Modellfelder und lassen Djangos ``save()`` mit
        ``ValueError`` abbrechen."""
        target = _legacy_pending_user(facility)
        assert target.has_usable_password()
        captured_update_fields = []
        real_save = User.save

        def spy_save(self, *args, **kwargs):
            if self.pk == target.pk:
                captured_update_fields.append(kwargs.get("update_fields"))
            return real_save(self, *args, **kwargs)

        with (
            patch.object(User, "save", spy_save),
            patch("core.services.user_access.send_invite_email", return_value=True),
        ):
            resend_user_invitation(target, facility=facility, actor=admin_user)

        assert captured_update_fields == [["password"]]

    def test_alt_zugang_wird_beim_resend_ueber_die_eigene_identitaet_widerrufen(self, facility, admin_user):
        """Tötet ``mutmut_21`` (``revoke_credentials(None, reason=…)`` —
        widerruft niemandes Zugang), ``mutmut_22`` (``reason=None`` — laut
        Docstring von ``revoke_credentials`` ein ``ValueError``),
        ``mutmut_23`` (fehlendes ``target``-Argument) und ``mutmut_24``
        (fehlendes ``reason``-Keyword) — die drei letzteren crashen bereits
        an der von ``autospec=True`` erzwungenen echten Signatur."""
        target = _legacy_pending_user(facility)
        assert target.has_usable_password()

        with (
            patch("core.services.user_access.send_invite_email", return_value=True),
            patch("core.services.user_access.revoke_credentials", autospec=True) as revoke,
        ):
            resend_user_invitation(target, facility=facility, actor=admin_user)

        revoke.assert_called_once_with(target, reason=RevocationReason.PRIVILEGE_REVOKED)

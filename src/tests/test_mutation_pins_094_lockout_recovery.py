"""Pin-Tests zum Meilenstein release-0.94 — Survivors in ``core.services.security.lockout_recovery``.

Quelle: Originalmodul ``src/core/services/security/lockout_recovery.py``,
Mutantenbaum ``mutants/src/core/services/security/lockout_recovery.py``.
Herkunft der Auswahl: die Survivor-Triage des Nachlaufs zum Meilenstein
release-0.94 (Ledger des Meilensteins) (13 Mutanten). Schwerpunkte:

* die **Anker-Wahrheit** von :func:`~core.services.security.lockout_recovery._latest_failed_anchor`
  — juengster Eintrag nach ``timestamp`` (nicht nach der zufaelligen UUID-PK),
  deterministischer ``-id``-Tiebreaker und der ``action``-Filter;
* die **Provenienz** des Tokens: :func:`build_recovery_token` ankert am
  anfordernden User, nicht an ``None``;
* der **Parser-Kontrakt** von :func:`verify_recovery_token` (``partition`` am
  ERSTEN Doppelpunkt, Legacy-Anker ``"0"``, Guard um ``user.facility``);
* die **Einmal-Nutzung**: der Verbraucht-Check zaehlt nur LOGIN_UNLOCK-Zeilen
  DES Token-Users, nur solche NACH dem gebundenen Fehlversuch, und behandelt
  den ankerlosen Token ohne ``TypeError``.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); es kommen in
diesem Modul keine uebersetzten Texte vor, deshalb ist kein
``translation.override("de")`` noetig; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py``, alle
Audit-Zeilen sind echte Model-Objekte (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.

Werkzeugkasten dieser Datei: ``AuditLog`` ist append-only (Model-``save()``
UND DB-Trigger ``auditlog_immutable``, Migration 0024) — die in der Triage
vorgeschlagenen ``queryset.update(timestamp=...)``-Tricks sind also nicht
moeglich. Zeitstempel und UUID-PKs werden stattdessen beim ``create()``
gesetzt (``timestamp`` = ``DateTimeField(default=timezone.now)``, ``id`` =
``UUIDField(primary_key=True, default=uuid.uuid4)``); das macht die Pins
deterministisch, ohne die Append-only-Zusage zu verletzen.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.core.signing import TimestampSigner
from django.utils import timezone

from core.models import AuditLog
from core.services.security import lockout_recovery
from core.services.security.login_lockout import unlock

# Feste, sortierbare UUIDs: Postgres vergleicht ``uuid`` byteweise, die
# Reihenfolge entspricht also der der kanonischen Strings. ``_UUID_MAX`` ist
# groesser als jede ``_uuid(n)``-Variante.
_UUID_MAX = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")


def _uuid(n: int) -> uuid.UUID:
    """Kleine, streng monoton wachsende Test-UUID (``n`` = 1..255)."""
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012x}")


def _audit(user, action, *, timestamp, pk=None):
    """Echte AuditLog-Zeile mit gesetztem Zeitstempel (und optional PK)."""
    kwargs = {}
    if pk is not None:
        kwargs["id"] = pk
    return AuditLog.objects.create(
        user=user,
        facility=user.facility,
        action=action,
        detail={"username": user.username},
        timestamp=timestamp,
        **kwargs,
    )


def _sign(payload: str) -> str:
    """Signiert eine Payload von Hand — wie ``build_recovery_token`` es taete."""
    return TimestampSigner(salt=lockout_recovery._SALT).sign(payload)


# ============================================================================
# _latest_failed_anchor — Anker-Wahrheit (3 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestLatestFailedAnchor:
    """Der Anker ist der JUENGSTE LOGIN_FAILED des Users — deterministisch."""

    def test_anchor_follows_timestamp_not_the_random_uuid_pk(self, staff_user):
        """Pin: x__latest_failed_anchor__mutmut_8 — ``.order_by("-timestamp", "-id")`` -> ``.order_by("-id")``.

        Der alte Fehlversuch bekommt die groesstmoegliche UUID, der junge die
        kleinste. Original sortiert nach ``timestamp`` und liefert den jungen
        Eintrag (``str(recent.id)`` == ``"00000000-0000-4000-8000-000000000001"``);
        der Mutant sortiert nur nach der PK und liefert
        ``"ffffffff-ffff-4fff-bfff-ffffffffffff"`` — der Recovery-Link haengt
        dann an einem Fehlversuch VOR der letzten Entsperrung.
        """
        now = timezone.now()
        _audit(
            staff_user,
            AuditLog.Action.LOGIN_FAILED,
            timestamp=now - timedelta(hours=2),
            pk=_UUID_MAX,
        )
        recent = _audit(
            staff_user,
            AuditLog.Action.LOGIN_FAILED,
            timestamp=now - timedelta(minutes=1),
            pk=_uuid(1),
        )

        assert lockout_recovery._latest_failed_anchor(staff_user) == str(recent.id)
        assert lockout_recovery._latest_failed_anchor(staff_user) == "00000000-0000-4000-8000-000000000001"

    def test_anchor_breaks_timestamp_ties_by_highest_id(self, staff_user):
        """Pin: x__latest_failed_anchor__mutmut_9 — ``.order_by("-timestamp", "-id")`` -> ``.order_by("-timestamp",)``.

        Zwei Fehlversuche desselben Users mit IDENTISCHEM Zeitstempel. Original
        bricht den Gleichstand deterministisch mit ``-id`` und liefert
        ``"ffffffff-ffff-4fff-bfff-ffffffffffff"``; ohne Tiebreaker entscheidet
        die Rueckgabereihenfolge der DB und Postgres liefert die zuerst
        eingefuegte Zeile (``"00000000-0000-4000-8000-000000000001"``).

        Wie beim Grenzfall-Pin fuer ``mutmut_73`` ist der Gleichstand nur ueber
        zwei Ketten konstruierbar (``assign_chain_fields`` haelt die Stempel
        **pro Facility** streng monoton, ``AuditLog`` ist append-only): die
        zweite Zeile liegt in der System-Kette (``facility=None``), der Stempel
        knapp in der Zukunft, damit keine Kette ihn anhebt. Bewusst ein
        DB-Ordnungs-abhaengiger Pin — er schreibt die Determinismus-Zusage der
        Query fest, die sonst voellig unbewacht ist.
        """
        stamp = timezone.now() + timedelta(seconds=30)
        low = _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=stamp, pk=_uuid(1))
        high = AuditLog.objects.create(
            id=_UUID_MAX,
            user=staff_user,
            facility=None,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": staff_user.username},
            timestamp=stamp,
        )
        assert high.timestamp == low.timestamp

        assert lockout_recovery._latest_failed_anchor(staff_user) == str(high.id)
        assert lockout_recovery._latest_failed_anchor(staff_user) == "ffffffff-ffff-4fff-bfff-ffffffffffff"

    def test_anchor_ignores_audit_rows_of_other_actions(self, staff_user):
        """Pin: x__latest_failed_anchor__mutmut_13 —
        ``filter(user=user, action=LOGIN_FAILED)`` -> ``filter(user=user,)``.

        Nach dem Fehlversuch entsteht ein juengerer PASSWORD_RESET_REQUESTED —
        genau das schreibt ``LockoutRecoveryRequestView`` nach
        ``build_recovery_token``. Original bleibt beim LOGIN_FAILED
        (``str(failed.id)``), der Mutant ankert an der Reset-Zeile
        (``str(later.id)``); ``verify_recovery_token`` filtert dann auf
        ``action=LOGIN_FAILED``, findet den Anker nicht und weist jeden Token
        dauerhaft ab.
        """
        now = timezone.now()
        failed = _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=now - timedelta(minutes=10))
        later = _audit(
            staff_user,
            AuditLog.Action.PASSWORD_RESET_REQUESTED,
            timestamp=now - timedelta(minutes=1),
        )

        anchor = lockout_recovery._latest_failed_anchor(staff_user)
        assert anchor == str(failed.id)
        assert anchor != str(later.id)


# ============================================================================
# build_recovery_token — Provenienz des Ankers (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestBuildRecoveryToken:
    """Die Payload lautet exakt ``<user.pk>:<anchor-uuid>`` des ANFORDERERS."""

    def test_payload_binds_the_requesting_users_latest_failure(self, staff_user):
        """Pin: x_build_recovery_token__mutmut_4 — ``_latest_failed_anchor(user)`` -> ``_latest_failed_anchor(None)``.

        Original signiert ``f"{staff_user.pk}:{failed.id}"``; der Mutant
        ermittelt den Anker fuer ``user=None``, findet keine user-lose
        LOGIN_FAILED-Zeile und signiert ``f"{staff_user.pk}:"`` — ein
        ankerloser Token, dessen Einmal-Nutzung nur noch am globalen
        LOGIN_UNLOCK-Check haengt. Der exakte Payload-Vergleich toetet ihn.
        """
        failed = _audit(
            staff_user,
            AuditLog.Action.LOGIN_FAILED,
            timestamp=timezone.now() - timedelta(minutes=3),
        )

        token = lockout_recovery.build_recovery_token(staff_user)
        payload = TimestampSigner(salt=lockout_recovery._SALT).unsign(token)

        assert payload == f"{staff_user.pk}:{failed.id}"


# ============================================================================
# verify_recovery_token — Parser, Legacy-Anker, Einmal-Nutzung (9 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestVerifyRecoveryTokenParsing:
    """Payload-Zerlegung und Anker-Auswertung."""

    def test_payload_is_split_at_the_first_colon(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_11 — ``payload.partition(":")`` -> ``payload.rpartition(":")``.

        ``uuid.UUID`` akzeptiert die URN-Schreibweise (``urn:uuid:<hex>``), ein
        Anker DARF also Doppelpunkte enthalten. Original schneidet am ERSTEN
        Doppelpunkt: ``pk_str`` == ``str(staff_user.pk)``, ``anchor_str`` ==
        ``"urn:uuid:<id>"`` -> der Anker wird aufgeloest, Rueckgabe ist der
        User. Der Mutant schneidet am letzten: ``pk_str`` ==
        ``f"{staff_user.pk}:urn:uuid"`` -> ``int()`` wirft ``ValueError`` ->
        Rueckgabe ``None``.
        """
        failed = _audit(
            staff_user,
            AuditLog.Action.LOGIN_FAILED,
            timestamp=timezone.now() - timedelta(minutes=2),
        )
        token = _sign(f"{staff_user.pk}:urn:uuid:{failed.id}")

        assert lockout_recovery.verify_recovery_token(token) == staff_user

    def test_missing_facility_attribute_does_not_raise(self, staff_user, monkeypatch):
        """Pin: x_verify_recovery_token__mutmut_29 —
        ``getattr(user, "facility", None)`` -> ``getattr(user, "facility")``.

        Der Klassen-Descriptor ``User.facility`` wird entfernt (die Instanz
        traegt ``facility_id`` weiter ueber ``attname``, die restlichen Queries
        in ``verify_recovery_token`` brauchen ``.facility`` nicht). Original
        faengt das fehlende Attribut per Default ab, setzt die GUCs auf
        ``None`` und liefert den User; der Mutant ruft ``getattr`` zweiargumentig
        auf und stirbt an ``AttributeError`` statt eines Rueckgabewerts.
        """
        failed = _audit(
            staff_user,
            AuditLog.Action.LOGIN_FAILED,
            timestamp=timezone.now() - timedelta(minutes=2),
        )
        token = _sign(f"{staff_user.pk}:{failed.id}")
        monkeypatch.delattr(type(staff_user), "facility")

        assert lockout_recovery.verify_recovery_token(token) == staff_user

    def test_legacy_zero_anchor_is_treated_as_anchorless(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_36 — ``anchor_str != "0"`` -> ``anchor_str != "XX0XX"``.

        Legacy-Payload ``<pk>:0`` (alte int-Kodierung). Original behandelt sie
        als ankerlos, ueberspringt den UUID-Zweig und laesst den Token bei
        fehlendem LOGIN_UNLOCK zu -> Rueckgabe ``staff_user``; der Mutant
        betritt den Anker-Zweig, ``uuid.UUID("0")`` wirft ``ValueError`` ->
        Rueckgabe ``None``. Die zweite Zusicherung nagelt die andere Haelfte
        des Legacy-Vertrags fest: nach einer Entsperrung ist die Payload tot.
        """
        _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=timezone.now() - timedelta(minutes=4))
        token = _sign(f"{staff_user.pk}:0")

        assert lockout_recovery.verify_recovery_token(token) == staff_user

        _audit(staff_user, AuditLog.Action.LOGIN_UNLOCK, timestamp=timezone.now())
        assert lockout_recovery.verify_recovery_token(token) is None

    def test_anchor_must_point_at_a_login_failed_row(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_49 —
        ``filter(pk=..., user=user, action=LOGIN_FAILED)`` -> ``filter(pk=..., user=user,)``.

        Die Payload ankert an einer LOGIN-Zeile desselben Users. Original
        findet unter dem ``action``-Filter keinen Anker und weist ab
        (``None``); der Mutant akzeptiert die fremde Aktion, uebernimmt deren
        Zeitstempel als Anker und gibt ``staff_user`` zurueck — womit sich der
        Replay-Schutz mit einer beliebig jungen Audit-Zeile aushebeln liesse.
        """
        login_row = _audit(
            staff_user,
            AuditLog.Action.LOGIN,
            timestamp=timezone.now() - timedelta(minutes=1),
        )
        token = _sign(f"{staff_user.pk}:{login_row.id}")

        assert lockout_recovery.verify_recovery_token(token) is None


@pytest.mark.django_db
class TestVerifyRecoveryTokenSingleUse:
    """Der Verbraucht-Check: wessen Unlock, welche Aktion, welche Grenze."""

    def test_unlock_of_another_user_does_not_consume_own_token(self, staff_user, lead_user):
        """Pin: x_verify_recovery_token__mutmut_62 —
        ``filter(user=user, action=LOGIN_UNLOCK)`` -> ``filter(action=LOGIN_UNLOCK)``.

        Zwei User derselben Einrichtung: der Anker von ``staff_user`` liegt
        zwei Stunden zurueck, ``lead_user`` wird eine Stunde spaeter entsperrt.
        Original sieht fuer ``staff_user`` keinen LOGIN_UNLOCK und liefert
        ``staff_user``; der Mutant nimmt den fremden Unlock als
        Verbraucht-Signal und liefert ``None`` — Recovery briche
        fremdverursacht ab.
        """
        now = timezone.now()
        _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=now - timedelta(hours=2))
        token = lockout_recovery.build_recovery_token(staff_user)
        _audit(lead_user, AuditLog.Action.LOGIN_UNLOCK, timestamp=now - timedelta(hours=1))

        assert lockout_recovery.verify_recovery_token(token) == staff_user

    def test_later_login_failed_does_not_consume_the_token(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_63 —
        ``filter(user=user, action=LOGIN_UNLOCK)`` -> ``filter(user=user,)``.

        Waehrend die Recovery-Mail unterwegs ist, laufen weitere Fehlversuche
        auf. Original filtert auf LOGIN_UNLOCK, findet keinen und liefert
        ``staff_user``; der Mutant nimmt die juengste Audit-Zeile beliebiger
        Aktion (den neuen LOGIN_FAILED) und liefert ``None`` — der gueltige
        Link waere entwertet.
        """
        now = timezone.now()
        _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=now - timedelta(hours=2))
        token = lockout_recovery.build_recovery_token(staff_user)
        _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=now - timedelta(hours=1))

        assert lockout_recovery.verify_recovery_token(token) == staff_user

    def test_older_unlock_before_the_anchor_leaves_the_token_valid(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_72 —
        ``(anchor_ts is None or ...)`` -> ``(anchor_ts is not None or ...)``.

        Eine ALTE Entsperrung (vor dem gebundenen Fehlversuch) darf den frisch
        angeforderten Link nicht toeten. Original prueft
        ``last_unlock_ts > anchor_ts`` -> ``False`` -> Rueckgabe
        ``staff_user``; der Mutant kippt schon an ``anchor_ts is not None`` auf
        ``True`` und liefert ``None``: ein wiederholt gesperrter Account
        bekaeme nie wieder einen funktionierenden Recovery-Link.
        """
        now = timezone.now()
        _audit(staff_user, AuditLog.Action.LOGIN_UNLOCK, timestamp=now - timedelta(hours=2))
        _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=now - timedelta(hours=1))
        token = lockout_recovery.build_recovery_token(staff_user)

        assert lockout_recovery.verify_recovery_token(token) == staff_user

    def test_anchorless_token_is_rejected_cleanly_after_an_unlock(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_33 (``anchor_ts = None`` -> ``anchor_ts = ""``) und
        x_verify_recovery_token__mutmut_72 (``anchor_ts is None`` -> ``anchor_ts is not None``).

        Ein User ohne jeden LOGIN_FAILED bekommt den ankerlosen Token
        ``<pk>:`` (die Request-View versendet auch dann). Nach einer
        Entsperrung liefert das Original sauber ``None``. Beide Mutanten
        umgehen den ``anchor_ts is None``-Zweig und werten
        ``last_unlock_ts > anchor_ts`` aus — ``datetime`` gegen ``""`` bzw.
        gegen ``None`` -> ``TypeError`` statt einer Rueckgabe (ungefangener
        500 im Confirm-View).
        """
        token = lockout_recovery.build_recovery_token(staff_user)
        assert TimestampSigner(salt=lockout_recovery._SALT).unsign(token) == f"{staff_user.pk}:"

        unlock(staff_user, unlocked_by=None, trigger="admin")

        assert lockout_recovery.verify_recovery_token(token) is None

    def test_unlock_at_the_exact_anchor_timestamp_still_validates(self, staff_user):
        """Pin: x_verify_recovery_token__mutmut_73 — ``last_unlock_ts > anchor_ts`` -> ``last_unlock_ts >= anchor_ts``.

        Grenzfall: LOGIN_UNLOCK und gebundener LOGIN_FAILED tragen exakt
        denselben Zeitstempel. Original wertet ``>`` aus -> ``False`` ->
        Rueckgabe ``staff_user``; der Mutant wertet ``>=`` aus -> ``True`` ->
        Rueckgabe ``None``.

        Der Gleichstand ist NUR ueber zwei verschiedene Hash-Ketten
        konstruierbar: ``assign_chain_fields`` erzwingt strenge Monotonie
        **pro Facility** (``core/services/audit/chain.py``, kollidierende
        Stempel werden um 1 us angehoben), und ``AuditLog`` ist append-only
        (Model-``save()`` + Trigger ``auditlog_immutable``), das
        ``queryset.update(timestamp=...)`` der Triage-Skizze also unmoeglich.
        Deshalb liegt die Unlock-Zeile in der System-Kette
        (``facility=None`` — vom Chain-Code und den RLS-Policies ausdruecklich
        vorgesehen) und der Stempel knapp in der Zukunft, damit keine der
        beiden Ketten ihn anhebt. Der Verbraucht-Check filtert ohnehin nur
        ueber ``user``/``action``, nicht ueber die Facility.
        """
        stamp = timezone.now() + timedelta(seconds=30)
        failed = _audit(staff_user, AuditLog.Action.LOGIN_FAILED, timestamp=stamp)
        system_unlock = AuditLog.objects.create(
            user=staff_user,
            facility=None,
            action=AuditLog.Action.LOGIN_UNLOCK,
            detail={"username": staff_user.username},
            timestamp=stamp,
        )
        assert system_unlock.timestamp == failed.timestamp

        token = _sign(f"{staff_user.pk}:{failed.id}")

        assert lockout_recovery.verify_recovery_token(token) == staff_user

"""Pin-Tests zum Meilenstein release-0.99 (#2174, #2193) — Survivors in
``core.services.security.login_lockout``.

Quelle: Originalmodul ``src/core/services/security/login_lockout.py``,
Mutantenbaum ``mutants/src/core/services/security/login_lockout.py``.
Herkunft der Auswahl: die Batch-Triage ``security-1`` des Vollmutationslaufs
#2174 (11 Mutanten dieses Moduls). Schwerpunkte:

* ``is_locked``: die Guard-Klausel fuer pk-lose/None-User, der Zeilen-Lock
  fuer die eigentliche Race-Serialisierung (Refs #737) und der
  User-Scope des LOGIN_UNLOCK-Lookups;
* ``unlock``: die super_admin-Ausnahme darf bei fehlendem Attribut nicht
  faelschlich GREIFEN (No-Op = verschwundener Audit-Eintrag) und nicht
  CRASHEN (AttributeError statt Audit-Eintrag).

Konventionen wie ``test_mutation_pins_094_lockout_recovery.py``: Zusicherungen
vergleichen EXAKT; KEINE Imports aus ``test_rls_functional``; Fixtures aus
``src/tests/conftest.py``. Jeder Test nennt seine Mutanten in der Docstring.

``is_super_admin`` (``@property``) und ``facility`` (``ForeignKey``) sind auf
JEDER ``User``-Instanz real vorhanden — die ``getattr``-Default-Werte dieser
Attribute sind deshalb nur ueber ``monkeypatch.delattr(type(user), "<attr>")``
beobachtbar (wie im 0.94-Pin fuer ``verify_recovery_token``); genau das
nutzen mehrere Tests unten.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone

from core.models import AuditLog, User
from core.services.security import is_locked, unlock

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _failed_entries(user, count, *, start):
    """``count`` LOGIN_FAILED-Eintraege ab ``start``, streng aufsteigend."""
    for i in range(count):
        AuditLog.objects.create(
            user=user,
            facility=user.facility,
            action=AuditLog.Action.LOGIN_FAILED,
            detail={"username": user.username},
            timestamp=start + timedelta(seconds=i),
        )


# ============================================================================
# is_locked — Guard-Klausel, Zeilen-Lock, User-Scope (5 Mutanten)
# ============================================================================


class TestIsLockedGuardClause:
    def test_unsaved_user_without_pk_is_treated_as_not_locked(self):
        """Pin: x_is_locked__mutmut_1 —
        ``user is None or not getattr(user, "pk", None)`` -> ``... and ...``.

        Ein NICHT gespeicherter User (``pk`` ist ``None``, aber das Objekt
        selbst ist nicht ``None``) muss ueber den ODER-Zweig sauber als
        "nicht gesperrt" behandelt werden. Der Mutant (UND statt ODER)
        verlangt BEIDE Bedingungen gleichzeitig, faellt fuer dieses Objekt
        durch die Guard-Klausel und laeuft in die spaetere
        ``AuditLog.objects.filter(user=user, ...)`` — ein FK-Filter auf eine
        Instanz ohne PK wirft dort ``ValueError`` statt einer Rueckgabe.
        """
        unsaved = User(username="ghost-unsaved")

        assert is_locked(unsaved) is False


class TestIsLockedSuperAdminDefault:
    def test_missing_is_super_admin_attribute_does_not_raise(self, staff_user, monkeypatch):
        """Pin: x_is_locked__mutmut_17 —
        ``getattr(user, "is_super_admin", False)`` -> ``getattr(user, "is_super_admin")``.

        Mit geloeschtem Property-Deskriptor faellt Original auf ``False``
        zurueck (nicht super_admin) und prueft regulaer weiter (0
        Fehlversuche -> ``False``). Der Mutant ruft ``getattr`` zweiargumentig
        auf und stirbt an ``AttributeError``.
        """
        monkeypatch.delattr(type(staff_user), "is_super_admin")

        assert is_locked(staff_user) is False

    def test_missing_is_super_admin_attribute_defaults_to_not_exempt(self, staff_user, monkeypatch):
        """Pin: x_is_locked__mutmut_20 — Default ``False`` -> ``True``.

        staff_user ist ueber die Schwelle gesperrt. Original faellt (Attribut
        geloescht) auf ``False`` zurueck -> NICHT super_admin -> die
        Lockout-Pruefung greift regulaer und liefert ``True``. Der Mutant
        faellt auf ``True`` zurueck (faelschlich als super_admin behandelt)
        -> No-Op-Rueckgabe ``False``, obwohl der Account gesperrt ist.
        """
        _failed_entries(staff_user, 10, start=timezone.now() - timedelta(minutes=10))
        monkeypatch.delattr(type(staff_user), "is_super_admin")

        assert is_locked(staff_user) is True


class TestIsLockedRowLockTarget:
    @pytest.mark.django_db(transaction=True)
    @pytest.mark.postgres
    def test_locks_the_checked_users_row_not_an_unmatched_one(self, staff_user):
        """Pin: x_is_locked__mutmut_22 —
        ``select_for_update().filter(pk=user.pk)`` -> ``.filter(pk=None)``.

        Race-Guard (Refs #737): ``is_locked()`` muss GENAU die Zeile des
        gepruefen Users sperren, damit parallele Checks serialisiert werden.
        Waehrend ``is_locked()`` (Hintergrund-Thread) noch innerhalb seiner
        ``transaction.atomic()``-Sperre haengt (angehalten unmittelbar NACH
        der ``select_for_update()``-Zeile), versucht eine ZWEITE, unabhaengige
        Verbindung dieselbe Zeile ``nowait`` zu sperren. Original haelt die
        Zeilensperre -> die zweite Verbindung scheitert mit
        ``OperationalError`` ("could not obtain lock"). Der Mutant sperrt
        ``pk=None`` (passt auf keine Zeile) -> die zweite Verbindung bekommt
        die Sperre sofort.
        """
        entered = threading.Event()
        release = threading.Event()
        holder_errors = []
        real_now = timezone.now
        paused = {"done": False}

        def paused_now():
            if not paused["done"]:
                paused["done"] = True
                entered.set()
                release.wait(timeout=10)
            return real_now()

        def holder():
            try:
                with mock.patch("core.services.security.login_lockout.timezone.now", side_effect=paused_now):
                    is_locked(staff_user)
            except Exception as exc:  # pragma: no cover - Sammelassertion
                holder_errors.append(exc)
            finally:
                close_old_connections()

        holder_thread = threading.Thread(target=holder)
        holder_thread.start()
        assert entered.wait(timeout=10), "is_locked() nicht rechtzeitig in der Transaktion angekommen"

        prober_result = {}

        def prober():
            try:
                connection.close()
                with transaction.atomic():
                    User.objects.select_for_update(nowait=True).filter(pk=staff_user.pk).first()
                prober_result["locked"] = False
            except OperationalError:
                prober_result["locked"] = True
            finally:
                close_old_connections()

        prober_thread = threading.Thread(target=prober)
        prober_thread.start()
        prober_thread.join(timeout=10)

        release.set()
        holder_thread.join(timeout=10)

        assert not holder_thread.is_alive(), "Deadlock in is_locked()"
        assert not holder_errors, f"is_locked()-Fehler: {holder_errors!r}"
        assert prober_result.get("locked") is True


class TestIsLockedUnlockScope:
    def test_last_unlock_lookup_is_scoped_to_this_user(self, staff_user, lead_user):
        """Pin: x_is_locked__mutmut_29 —
        ``filter(user=user, action=LOGIN_UNLOCK)`` -> ``filter(action=LOGIN_UNLOCK)``.

        lead_user (gleiche Facility) wurde soeben entsperrt — das darf NICHT
        das Zeitfenster von staff_user beeinflussen. staff_user traegt 10
        LOGIN_FAILED VOR diesem fremden Unlock und hat KEINEN eigenen Unlock
        -> Original liefert ``True``. Der Mutant nimmt den juengsten
        LOGIN_UNLOCK UEBER ALLE User, filtert die Fehlversuche faelschlich
        auf "nach diesem fremden Unlock" -> 0 Treffer -> ``False`` (der
        gesperrte Account erschiene faelschlich entsperrt).
        """
        now = timezone.now()
        _failed_entries(staff_user, 10, start=now - timedelta(minutes=10))
        AuditLog.objects.create(
            user=lead_user,
            facility=lead_user.facility,
            action=AuditLog.Action.LOGIN_UNLOCK,
            detail={"username": lead_user.username},
            timestamp=now - timedelta(minutes=1),
        )

        assert is_locked(staff_user) is True


# ============================================================================
# unlock — super_admin-Ausnahme bei fehlendem Attribut (3 Mutanten)
# ============================================================================


class TestUnlockSuperAdminDefault:
    def test_missing_is_super_admin_attribute_still_writes_the_unlock(self, staff_user, monkeypatch):
        """Pins: x_unlock__mutmut_8 (Default-Argument entfernt),
        x_unlock__mutmut_11 (Default ``False`` -> ``True``).

        Mit geloeschtem Property-Deskriptor faellt Original auf ``False``
        zurueck (nicht super_admin) und schreibt den LOGIN_UNLOCK-Eintrag.
        mutmut_8 ruft ``getattr`` zweiargumentig auf und stirbt an
        ``AttributeError``; mutmut_11 faellt auf ``True`` zurueck (faelschlich
        super_admin) -> No-Op, der Entsperr-Eintrag verschwindet spurlos.
        """
        monkeypatch.delattr(type(staff_user), "is_super_admin")

        entry = unlock(staff_user, unlocked_by=None, trigger="admin")

        assert entry is not None
        assert entry.action == AuditLog.Action.LOGIN_UNLOCK

    def test_missing_facility_attribute_does_not_raise(self, staff_user, monkeypatch):
        """Pin: x_unlock__mutmut_32 —
        ``getattr(user, "facility", None)`` -> ``getattr(user, "facility")``.

        Analog zum 0.94-Pin fuer ``verify_recovery_token`` (Refs #1273):
        Original faengt das fehlende Attribut per Default ab
        (``facility=None`` im Audit-Eintrag) und schreibt den Unlock. Der
        Mutant ruft ``getattr`` zweiargumentig auf und stirbt an
        ``AttributeError`` statt eines Audit-Eintrags.
        """
        monkeypatch.delattr(type(staff_user), "facility")

        entry = unlock(staff_user, unlocked_by=None, trigger="admin")

        assert entry is not None
        assert entry.facility is None

"""Pin-Tests zum Meilenstein release-0.99 (#2174, #2193) — Survivors in
``core.services.security.lockout_recovery``.

Quelle: Originalmodul ``src/core/services/security/lockout_recovery.py``,
Mutantenbaum ``mutants/src/core/services/security/lockout_recovery.py``.
Herkunft der Auswahl: die Batch-Triage ``security-1`` des Vollmutationslaufs
#2174. Von den 11 Mutanten dieses Moduls im Batch sind ZEHN echte
Testluecken:

* ``consume_recovery_token`` darf beim Zeilen-Lock weder auf die
  ``pk``-Bedingung (mutmut_7 — sonst greift ``.get()`` bei mehreren Usern
  derselben Facility fehl, ``MultipleObjectsReturned``) NOCH auf die
  ``facility``-Bedingung (mutmut_8) verzichten: das Facility-Scoping ist ein
  eigener Sicherheitsvertrag (Mandantentrennung), unabhaengig von der
  ``pk``-Eindeutigkeit — belegt per Szenario, in dem die Facility der Zeile
  zwischen dem ersten und dem zweiten internen Lookup wechselt (siehe
  ``TestConsumeRecoveryTokenLockTarget``).
* Die acht Mutanten an der ``_set_session_vars(...)``-Zeile direkt NACH dem
  Zeilen-Lock (Facility-/Rollen-Kontext fuer die RLS-Durchsetzung) sind
  Vertrags-Pins auf den Sicherheitsvertrag SELBST: die produktive
  RLS-Durchsetzung ist ueber diese Testsuite nicht end-to-end nachweisbar
  (die Test-DB-Rolle erzwingt RLS nicht durchgaengig — bekannte
  Infrastrukturgrenze), deshalb wird NICHT auf ein beobachtbares
  RLS-Ergebnis gepinnt (das waere durch die naechste ``verify_recovery_token``
  -Zeile, die dieselben Werte ohnehin erneut setzt, maskierbar), sondern
  direkt auf die an dieser Stelle uebergebenen Argumente — analog zum
  Elevation-Wert-Pin in ``core.services.audit.chain``. Zwei Techniken:
  ein Werte-Spy (``mock.MagicMock(wraps=_set_session_vars)``) fuer die
  Mutanten, die den uebergebenen Wert sichtbar veraendern (Facility ->
  ``None``, ``is_super_admin`` vertauscht/entfernt, falscher Attributname),
  und ein gezielter Monkeypatch, der den ``facility``-Deskriptor NUR
  zwischen dem Zeilen-Lock-Read und der Zielzeile entfernt, fuer den einen
  Mutanten, dessen fehlendes Default-Argument sich sonst in KEINEM
  erreichbaren Szenario vom Original unterscheidet (``facility`` ist auf
  jeder ``User``-Instanz real vorhanden).

Nur EIN Mutant bleibt ``equivalent`` (``_latest_failed_anchor.mutmut_4``):
``.values_list("id", flat=True)`` -> ``.values_list(flat=True)`` (Feldname
entfernt). Django selbst behandelt ``flat=True`` OHNE Feldname als
Spezialfall — ``QuerySet.values_list`` (``django/db/models/query.py``)
setzt in diesem Fall explizit ``fields = [self.model._meta.concrete_fields[0].attname]``,
also GENAU das erstdeklarierte Feld des Modells. Fuer ``AuditLog`` ist das
``id`` (``core/models/audit.py``, ``id`` ist das erste deklarierte Feld) —
die Mutante erzeugt dadurch NICHT „alle Felder", sondern exakt dieselbe
Query wie das Original (per ``CaptureQueriesContext`` verifiziert: SQL ist
zeichengleich, keine Differenz in Feldwahl, Abfragezahl oder Ergebnis).
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.db.models.query import QuerySet
from django.utils import timezone

from core.models import AuditLog, Facility
from core.services.security import lockout_recovery

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _lock_failed_entry(user, *, minutes_ago=5):
    return AuditLog.objects.create(
        user=user,
        facility=user.facility,
        action=AuditLog.Action.LOGIN_FAILED,
        detail={"username": user.username},
        timestamp=timezone.now() - timedelta(minutes=minutes_ago),
    )


class TestConsumeRecoveryTokenLockTarget:
    def test_lock_uses_pk_not_facility_alone_when_multiple_users_share_it(self, staff_user, lead_user):
        """Pin: x_consume_recovery_token__mutmut_7 —
        ``select_for_update().get(pk=user.pk, facility=user.facility)`` ->
        ``.get(facility=user.facility)``.

        staff_user UND lead_user teilen dieselbe Facility (Standard-Fixture).
        Original sperrt gezielt ueber ``pk`` -> genau die Zeile von
        staff_user wird entsperrt. Der Mutant laesst ``pk`` weg und filtert
        nur noch auf ``facility`` -- mit zwei Usern in derselben Facility
        wirft ``.get()`` ``MultipleObjectsReturned`` (bei genau einem User
        je Facility wuerde es sogar STILLSCHWEIGEND die falsche Person
        entsperren).
        """
        _lock_failed_entry(staff_user)
        token = lockout_recovery.build_recovery_token(staff_user)

        result = lockout_recovery.consume_recovery_token(token, ip_address="203.0.113.9")

        assert result == staff_user
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 1
        assert AuditLog.objects.filter(user=lead_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 0

    def test_lock_rejects_a_row_whose_facility_changed_between_the_two_internal_reads(self, staff_user, organization):
        """Pin: x_consume_recovery_token__mutmut_8 —
        ``select_for_update().get(pk=user.pk, facility=user.facility)`` ->
        ``.get(pk=user.pk,)`` (Facility-Bedingung entfernt).

        Facility-Scoping ist ein EIGENER Sicherheitsvertrag (Mandanten-
        trennung) — ``pk``-Eindeutigkeit macht die Facility-Bedingung NUR
        redundant, wenn die Facility eines Users sich waehrend des Ablaufs
        nicht aendert. Das Szenario erzwingt genau das Gegenteil: der
        ``select_for_update``-Aufruf in ``consume_recovery_token`` wird
        umhuellt, sodass unmittelbar VOR dem eigentlichen Zeilen-Read die
        Facility der Zeile per ``.update()`` auf eine ANDERE Einrichtung
        wechselt. ``user.facility`` (der im WHERE verwendete Wert) ist zu
        diesem Zeitpunkt bereits gecacht (der interne ``verify_recovery_token``
        -Aufruf hat ihn zuvor per ``getattr(user, "facility", None)``
        gelesen) und bleibt daher auf der URSPRUENGLICHEN Facility stehen —
        die DB-Zeile traegt inzwischen die NEUE. Original: ``WHERE pk=... AND
        facility=<alte Facility>`` findet die (jetzt umgezogene) Zeile NICHT
        mehr -> ``User.DoesNotExist`` -> ``consume_recovery_token`` liefert
        ``None``, kein Unlock. Der Mutant filtert nur noch auf ``pk`` ->
        findet die Zeile trotzdem -> entsperrt sie, obwohl die Facility der
        Zeile inzwischen eine andere ist als die, an die der Token gebunden
        war.
        """
        facility_b = Facility.objects.create(organization=organization, name="Zweigstelle")
        _lock_failed_entry(staff_user)
        token = lockout_recovery.build_recovery_token(staff_user)

        real_select_for_update = lockout_recovery.User.objects.select_for_update

        def select_for_update_after_facility_moves(*args, **kwargs):
            lockout_recovery.User.objects.filter(pk=staff_user.pk).update(facility=facility_b)
            return real_select_for_update(*args, **kwargs)

        with mock.patch.object(
            lockout_recovery.User.objects,
            "select_for_update",
            side_effect=select_for_update_after_facility_moves,
        ):
            result = lockout_recovery.consume_recovery_token(token, ip_address="203.0.113.20")

        assert result is None
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 0


class TestConsumeRecoveryTokenSessionContextValues:
    """Vertrags-Pin auf die an ``_set_session_vars`` uebergebenen Argumente
    direkt nach dem Zeilen-Lock — NICHT auf ein RLS-Ergebnis (ueber diese
    Testsuite nicht end-to-end nachweisbar, bekannte Infrastrukturgrenze:
    die Test-DB-Rolle erzwingt RLS nicht durchgaengig)."""

    def test_session_vars_carry_the_locked_users_facility_and_role_unaltered(self, staff_user):
        """Pins: x_consume_recovery_token__mutmut_9
        (``getattr(locked_user, "facility", None)`` -> ``None``),
        mutmut_10 (``is_super_admin=False`` -> ``None``),
        mutmut_12 (``is_super_admin``-Kwarg entfernt),
        mutmut_13 (``getattr(locked_user, ...)`` -> ``getattr(None, ...)``),
        mutmut_18 (Attributname -> ``XXfacilityXX``),
        mutmut_19 (Attributname -> ``FACILITY``),
        mutmut_20 (``is_super_admin=False`` -> ``True``).

        ``consume_recovery_token`` ruft ``_set_session_vars`` GENAU dreimal
        auf: einmal intern in jedem der beiden ``verify_recovery_token``-
        Aufrufe (Rahmen, unveraendert) und einmal SELBST unmittelbar nach
        dem Zeilen-Lock (Index 1 — die hier gepinnte Zeile). Ein Werte-Spy
        (``mock.MagicMock(wraps=...)``, ruft die echte Funktion weiterhin
        auf — der Testlauf bleibt funktional korrekt) zeichnet die EXAKTEN
        Aufrufargumente auf. Original uebergibt an dieser Stelle immer
        ``(staff_user.facility, is_super_admin=False)``. Jede der sieben
        Mutanten liefert an genau dieser Stelle einen ANDEREN Wert (Facility
        ``None`` statt der echten Einrichtung, vertauschtes/fehlendes
        ``is_super_admin``, falscher Attributname faellt auf den ``None``-
        Default zurueck) — unabhaengig davon, ob der naechste interne
        ``verify_recovery_token``-Aufruf den (falschen) Kontext fuer seine
        EIGENEN Abfragen anschliessend wieder korrekt ueberschreibt: der
        Sicherheitsvertrag an DIESER Aufrufstelle selbst ist verletzt.
        """
        _lock_failed_entry(staff_user)
        token = lockout_recovery.build_recovery_token(staff_user)

        spy = mock.MagicMock(wraps=lockout_recovery._set_session_vars)
        with mock.patch("core.services.security.lockout_recovery._set_session_vars", spy):
            result = lockout_recovery.consume_recovery_token(token, ip_address="203.0.113.12")

        assert result == staff_user
        assert len(spy.call_args_list) == 3, spy.call_args_list
        assert spy.call_args_list[1] == mock.call(staff_user.facility, is_super_admin=False)

    def test_facility_default_survives_a_descriptor_missing_only_at_lock_time(self, staff_user, monkeypatch):
        """Pin: x_consume_recovery_token__mutmut_17 —
        ``getattr(locked_user, "facility", None)`` -> Default-Argument entfernt.

        ``facility`` ist auf jeder ``User``-Instanz real vorhanden — das
        fehlende Default-Argument unterscheidet sich in KEINEM ueber echte
        Aufrufer erreichbaren Szenario vom Original (der Wert ist stets
        identisch). Beobachtbar wird der Unterschied nur, wenn der
        ``facility``-Deskriptor GENAU zwischen dem Zeilen-Lock-Read (der
        ``user.facility`` bereits VOR dem Aufruf ausgewertet hat, also
        unberuehrt bleibt) und der gepinnten Zeile entfernt wird — dafuer
        wird ``QuerySet.select_for_update`` gezielt umhuellt: NUR das
        Ergebnis des einen ``select_for_update().get(...)``-Aufrufs in
        ``consume_recovery_token`` verliert danach den Deskriptor, die
        ``getattr``-Aufrufe mit Default in ``verify_recovery_token``
        bleiben unberuehrt und liefern weiterhin ``None`` sauber. Original
        faengt das fehlende Attribut per Default ab und schliesst den
        Entsperr-Flow normal ab; der Mutant wirft an der gepinnten Zeile
        ``AttributeError``.
        """
        _lock_failed_entry(staff_user)
        token = lockout_recovery.build_recovery_token(staff_user)

        real_select_for_update = QuerySet.select_for_update

        def select_for_update_then_strip_facility_after_get(self, *args, **kwargs):
            queryset = real_select_for_update(self, *args, **kwargs)
            real_get = queryset.get

            def get_then_strip_facility(*get_args, **get_kwargs):
                obj = real_get(*get_args, **get_kwargs)
                monkeypatch.delattr(type(obj), "facility", raising=False)
                return obj

            queryset.get = get_then_strip_facility
            return queryset

        monkeypatch.setattr(QuerySet, "select_for_update", select_for_update_then_strip_facility_after_get)

        result = lockout_recovery.consume_recovery_token(token, ip_address="203.0.113.13")

        assert result == staff_user
        assert AuditLog.objects.filter(user=staff_user, action=AuditLog.Action.LOGIN_UNLOCK).count() == 1

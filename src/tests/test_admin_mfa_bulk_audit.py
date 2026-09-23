"""Die MFA-Pflicht-Sammelaktion im Admin laeuft ueber einen auditierten Pfad (Refs #2019).

``enforce_mfa_for_selected_users`` setzte ``mfa_required`` per rohem
``QuerySet.update()``. Das feuert keine Signale, laeuft an ``save()`` vorbei und
hinterlaesst deshalb keine Zeile im append-only AuditLog — waehrend
Rollenwechsel, Deaktivierung und Loeschbestaetigungsrecht laengst
actor-attribuierte Eintraege schreiben (Refs #1369) und die Fach-UI dieselbe
MFA-Pflicht als ``USER_ACCESS_UPDATED`` protokolliert (Refs #1871).

Geprueft wird:

* je tatsaechlich umgestelltem Konto genau EINE ``USER_ACCESS_UPDATED``-Zeile
  mit Ziel (``user``) und handelndem Admin (``actor``);
* bereits MFA-pflichtige Konten erzeugen keine Zeile (die Re-Filter-Logik
  bleibt erhalten — sonst produzierte jeder Sammelklick Audit-Rauschen);
* das A2.1-Facility-Scoping der Aktion bleibt unveraendert: ein facility_admin
  trifft ueber einen manipulierten Changelist-Request keine fremden Konten,
  und es entsteht auch keine Audit-Zeile dafuer.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.admin import users as users_admin
from core.admin.users import UserAdmin, enforce_mfa_for_selected_users
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, Facility, User
from core.services.audit.chain import advisory_lock_id

pytestmark = pytest.mark.django_db

ACCESS_UPDATED = AuditLog.Action.USER_ACCESS_UPDATED


def _request(rf, user):
    request = rf.post("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.META["REMOTE_ADDR"] = "198.51.100.7"
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _run(request, queryset):
    enforce_mfa_for_selected_users(UserAdmin(User, anlaufstelle_admin_site), request, queryset)


def _entries(target):
    return AuditLog.objects.filter(action=ACCESS_UPDATED, user=target)


class TestEnforceMfaBulkActionAudit:
    def test_writes_one_audit_entry_per_flipped_user(self, rf, admin_user, staff_user, lead_user):
        assert staff_user.mfa_required is False
        assert lead_user.mfa_required is False

        _run(_request(rf, admin_user), User.objects.filter(pk__in=[staff_user.pk, lead_user.pk]))

        for target in (staff_user, lead_user):
            target.refresh_from_db()
            assert target.mfa_required is True, "Die Sammelaktion muss die MFA-Pflicht weiterhin setzen"
            entry = _entries(target).get()
            assert entry.actor_id == admin_user.pk, "Der handelnde Admin muss als actor stehen"
            assert entry.target_type == "User"
            assert entry.target_id == str(target.pk)
            assert entry.detail["changed_fields"] == ["mfa_required"]
            assert entry.detail["username"] == target.username
            assert entry.detail["actor_username"] == admin_user.username
            assert entry.facility_id == target.facility_id
            assert entry.ip_address == "198.51.100.7"

    def test_already_required_users_produce_no_entry(self, rf, admin_user, staff_user):
        """Re-Filter-Logik bleibt: kein Flip, keine Zeile, kein Audit-Rauschen."""
        staff_user.mfa_required = True
        staff_user.save(update_fields=["mfa_required"])

        _run(_request(rf, admin_user), User.objects.filter(pk=staff_user.pk))

        assert _entries(staff_user).count() == 0

    def test_facility_scoping_is_preserved(self, rf, admin_user, second_facility_user):
        """A2.1: ein facility_admin trifft keine fremden Konten — auch nicht im Audit."""
        _run(_request(rf, admin_user), User.objects.filter(pk=second_facility_user.pk))

        second_facility_user.refresh_from_db()
        assert second_facility_user.mfa_required is False
        assert _entries(second_facility_user).count() == 0

    def test_super_admin_flips_across_facilities(self, rf, super_admin_user, staff_user, second_facility_user):
        """super_admin bleibt ungescopt — je Konto eine Zeile."""
        _run(
            _request(rf, super_admin_user),
            User.objects.filter(pk__in=[staff_user.pk, second_facility_user.pk]),
        )

        for target in (staff_user, second_facility_user):
            target.refresh_from_db()
            assert target.mfa_required is True
            assert _entries(target).get().actor_id == super_admin_user.pk


class TestEnforceMfaBulkActionAtomicity:
    """Cross-Review: der Bulk-Lauf ist alles-oder-nichts.

    Django umschliesst Admin-Actions NICHT automatisch mit
    ``transaction.atomic``. Ohne eigene Klammer blieb ein MFA-Flip bestehen,
    dessen Audit-Zeile scheiterte, und bei mehreren Konten entstand ein
    partiell angewandter Lauf. Ohne Zeilensperre konnten zwei parallele
    Aktionen denselben ``mfa_required=False``-Stand materialisieren und beide
    eine Zeile schreiben — zwei Belege fuer EINE tatsaechliche Aenderung.
    """

    def test_audit_failure_rolls_back_the_whole_bulk(self, rf, admin_user, staff_user, lead_user, monkeypatch):
        """Fail-closed: keine Audit-Zeile ⇒ auch kein Flip, und zwar fuer alle."""

        def _boom(*args, **kwargs):
            raise RuntimeError("Audit-Kette nicht schreibbar")

        # Modul-Referenz stubben (NICHT den connection-Proxy patchen).
        monkeypatch.setattr("core.admin.users.audit_event", _boom)

        with pytest.raises(RuntimeError):
            _run(_request(rf, admin_user), User.objects.filter(pk__in=[staff_user.pk, lead_user.pk]))

        for target in (staff_user, lead_user):
            target.refresh_from_db()
            assert target.mfa_required is False, (
                "Ein MFA-Flip ohne Audit-Zeile ist genau die Luecke aus #2019 — der Lauf muss zurueckgerollt werden"
            )
            assert _entries(target).count() == 0

    def test_selected_rows_are_locked_before_the_flip(self, rf, admin_user, staff_user):
        """Der Re-Filter sperrt die Zeilen — sonst schreiben zwei parallele
        Aktionen zwei Zeilen fuer eine Aenderung."""
        with CaptureQueriesContext(connection) as ctx:
            _run(_request(rf, admin_user), User.objects.filter(pk=staff_user.pk))

        sperren = [
            query["sql"]
            for query in ctx.captured_queries
            if "FOR UPDATE" in query["sql"].upper() and "core_user" in query["sql"].lower()
        ]
        assert sperren, "Der Re-Filter muss die betroffenen Konten per select_for_update sperren"


# --- Kanonische Lock-Reihenfolge ueber Facilities hinweg -------
#
# Jeder Audit-Insert nimmt in ``services/audit/chain.py`` einen
# transaktionsweiten Advisory-Lock PRO FACILITY
# (``pg_advisory_xact_lock(hashtext("audit_chain_<facility_id>"))``). Innerhalb
# der Bulk-Transaktion akkumulieren diese Locks also ueber alle auditierten
# Zeilen. Ein super_admin sieht einrichtungsuebergreifend alle Konten, sein
# Bulk-Lauf kann somit mehrere Facility-Locks halten. Werden sie in
# unterschiedlicher Reihenfolge erworben (A->B hier, B->A dort), deadlocken
# zwei parallele Laeufe — auch bei voellig disjunkten Zeilenmengen, denn die
# Zeilensperren serialisieren dann nichts.
#
# ``order_by("pk")`` ordnet nur die Zeilensperren. Die Facility-Reihenfolge
# ergibt sich daraus zufaellig. Der Vertrag lautet deshalb: Verarbeitung nach
# ``(facility_id, pk)``, damit die Facility-Locks in stabiler, global gleicher
# Reihenfolge genommen werden.

_KLEINE_FACILITY_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_GROSSE_FACILITY_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


@pytest.fixture
def gemischte_facilities(organization):
    """Zwei Facilities, deren UUID-Ordnung der User-PK-Ordnung WIDERSPRICHT.

    ``User`` traegt ein Integer-PK (aufsteigend nach Anlage), ``Facility`` ein
    UUID-PK. Das Konto in der Facility mit der GROSSEN UUID wird zuerst
    angelegt, bekommt also die KLEINERE User-PK. Unter ``order_by("pk")``
    liefe es damit zuerst — die Facility-Locks fielen absteigend an. Genau
    diesen Konflikt braucht der Test, sonst waere er zufaellig gruen.
    """
    gross = Facility.objects.create(id=_GROSSE_FACILITY_ID, organization=organization, name="Zweitstelle")
    user_gross = User.objects.create_user(username="u_in_gross", role=User.Role.STAFF, facility=gross)
    klein = Facility.objects.create(id=_KLEINE_FACILITY_ID, organization=organization, name="Erststelle")
    user_klein = User.objects.create_user(username="u_in_klein", role=User.Role.STAFF, facility=klein)
    assert user_gross.pk < user_klein.pk, "Testaufbau: die PK-Ordnung muss der Facility-Ordnung widersprechen"
    return klein, gross, user_klein, user_gross


class TestEnforceMfaBulkFacilityLockOrder:
    """Der Ordnungsvertrag gilt fuer die TATSAECHLICHEN Advisory-Lock-Keys.

    Gesperrt wird ``hashtext("audit_chain_<facility_id>")``. ``hashtext``
    liefert 32 Bit und bildet UUIDs damit NICHT injektiv ab — nach
    ``facility_id`` zu ordnen sagt ueber die Key-Reihenfolge nichts aus. Bei
    ``h(A) = h(C) = X``, ``h(B) = Y`` und ``A < B < C`` naehme ein Lauf ueber
    ``{A, B}`` die Keys ``X -> Y``, ein Lauf ueber ``{B, C}`` dagegen
    ``Y -> X``: der Deadlock-Zyklus bliebe offen. Geordnet wird deshalb nach
    ``advisory_lock_id``.
    """

    @staticmethod
    def _lock_reihenfolge(monkeypatch, rf, actor, konten):
        """Facility-Reihenfolge der Audit-Writes aufzeichnen (Spy auf der Modul-Referenz)."""
        reihenfolge = []
        original = users_admin.audit_event

        def _spy(*args, **kwargs):
            reihenfolge.append(kwargs["facility"].pk)
            return original(*args, **kwargs)

        monkeypatch.setattr("core.admin.users.audit_event", _spy)
        _run(_request(rf, actor), User.objects.filter(pk__in=[k.pk for k in konten]))
        return reihenfolge

    def test_processing_follows_the_actual_lock_key_order(
        self, rf, super_admin_user, gemischte_facilities, monkeypatch
    ):
        """Die Verarbeitung folgt dem Lock-Key, NICHT der facility_id.

        Die Key-Funktion wird so gestubbt, dass sie die facility_id-Ordnung
        umkehrt. Wuerde weiterhin nach ``facility_id`` sortiert, liefe die
        kleine Facility zuerst und die Lock-Keys fielen absteigend an.
        """
        klein, gross, user_klein, user_gross = gemischte_facilities
        # Bewusst gegenlaeufig zur UUID-Ordnung: kleine Facility -> grosser Key.
        schluessel = {klein.pk: 4711, gross.pk: 42}
        monkeypatch.setattr("core.admin.users.advisory_lock_id", lambda fid: schluessel[fid])

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [user_gross, user_klein])

        assert reihenfolge == [gross.pk, klein.pk], (
            "Verarbeitet werden muss nach dem tatsaechlichen Advisory-Lock-Key, nicht nach facility_id"
        )
        assert [schluessel[fid] for fid in reihenfolge] == sorted(schluessel.values()), (
            "Die Advisory-Lock-Keys muessen aufsteigend erworben werden"
        )

    def test_key_collision_falls_back_to_a_deterministic_tie_break(
        self, rf, super_admin_user, gemischte_facilities, monkeypatch
    ):
        """Kollidierende Keys (der Kern des Befunds) ordnen stabil nach facility_id.

        Bei identischem Lock-Key ist die Erwerbsreihenfolge fuer den Deadlock
        egal — der zweite Erwerb desselben xact-Locks ist ein No-op. Wichtig
        ist, dass die Ordnung trotzdem deterministisch bleibt, damit sie sich
        nicht zwischen zwei Laeufen dreht.
        """
        klein, gross, user_klein, user_gross = gemischte_facilities
        monkeypatch.setattr("core.admin.users.advisory_lock_id", lambda fid: 99)

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [user_gross, user_klein])

        assert reihenfolge == sorted(reihenfolge, key=str), (
            "Bei Key-Kollision muss die facility_id als deterministischer Tie-Break greifen"
        )

    def test_real_lock_keys_are_acquired_in_ascending_order(
        self, rf, super_admin_user, gemischte_facilities, monkeypatch
    ):
        """Gegenprobe ohne Stub: mit den ECHTEN hashtext-Werten aus Postgres."""
        klein, gross, user_klein, user_gross = gemischte_facilities

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [user_gross, user_klein])

        keys = [advisory_lock_id(fid) for fid in reihenfolge]
        assert keys == sorted(keys), f"Advisory-Lock-Keys nicht aufsteigend erworben: {keys}"

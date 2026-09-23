"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.audit.chain``
(Batch ``chain-b``).

Quelle: Vollmutation #2174, Batch ``chain-b``
(``welle-l-0.99/reports/m-scratch/batch-chain-b.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-chain-b/triage-099-chain-b.json``).

Das Modul ``core.services.audit.chain`` ist auf zwei Vollmutations-Batches
verteilt: ``chain-a`` (``_canonical_payload``, ``_system_chain_latest_link_raw``,
``verify_chain``, ``_checkpoint_boundaries`` — eigene Pin-Datei)
und dieser Batch ``chain-b`` (``assign_chain_fields``, ``_canonical_timestamp``,
``_latest_link``, ``compute_entry_hash``, ``advisory_lock_name``,
``advisory_lock_id``).

Die verkettete HMAC-Integritaet (Refs #1070) macht den append-only
``AuditLog`` tamper-evident. Diese sechs Funktionen bilden das Fundament der
Kette: den Advisory-Lock, der konkurrierende Schreiber derselben Facility
serialisiert (Deadlock-Ordnungsvertrag #2019/#2023), die strenge Zeitstempel-
Monotonie pro Facility, die UTC-Kanonisierung des Zeitstempels vor dem Hash
und den HMAC selbst. Mehrere Survivor-Mutanten aendern SQL-Schluesselwort-
Grossschreibung oder die Codec-Namensschreibweise — beides ist in PostgreSQL
bzw. Python nachweislich verhaltensgleich (siehe unten, ``equivalent``).

Killt (Test je Zeile):

- ``test_assign_chain_fields_erwirbt_facility_spezifischen_lock``:
  ``x_assign_chain_fields__mutmut_11``.
- ``test_assign_chain_fields_bumpt_bei_exakter_kollision``:
  ``x_assign_chain_fields__mutmut_23``/``_26``/``_29``.
- ``test_assign_chain_fields_bumpt_bei_unverketteter_bestandszeile``:
  ``x_assign_chain_fields__mutmut_22``.
- ``test_canonical_timestamp_falsy_leer_truthy_isoformat``:
  ``x__canonical_timestamp__mutmut_1``/``_2``.
- ``test_canonical_timestamp_naive_wird_als_utc_interpretiert``:
  ``x__canonical_timestamp__mutmut_4``/``_5``/``_6``.
- ``test_latest_link_nimmt_raw_pfad_nur_bei_echtem_postgres_vendor``:
  ``x__latest_link__mutmut_3``/``_4``/``_5``.
- ``test_latest_link_order_by_hat_id_tiebreak``: ``x__latest_link__mutmut_13``.
- ``test_compute_entry_hash_referenzwert_gegen_unabhaengige_rekonstruktion``:
  ``x_compute_entry_hash__mutmut_5``.
- ``test_advisory_lock_name_facility_vs_system``:
  ``x_advisory_lock_name__mutmut_1``/``_2``.
- ``test_advisory_lock_id_verwendet_facility_spezifischen_namen``:
  ``x_advisory_lock_id__mutmut_11``.

NICHT gepinnt (Triage-JSON, Klasse ``equivalent``): ``x_assign_chain_fields__mutmut_9``,
``x_advisory_lock_id__mutmut_9`` (beide: ``SELECT`` -> ``select`` im SQL-Text)
und ``x_compute_entry_hash__mutmut_8`` (``encode("utf-8")`` -> ``encode("UTF-8")``)
— PostgreSQL-Schluesselwoerter sind gross-/kleinschreibungsunabhaengig und
Pythons Codec-Namensaufloesung ebenso (``"x".encode("utf-8") ==
"x".encode("UTF-8")`` fuer JEDEN Eingabewert, verifiziert; analoger Fall
bereits in ``core.services.audit.hwm._sign``, Batch ``audit-2``). Alle drei
liefern fuer JEDEN Eingabewert byteidentisches Verhalten — adversarial per
``MUTANT_UNDER_TEST`` gegen die volle Datei bestaetigt (bleibt GRUEN).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from django.db import connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.audit import chain as chain_mod
from core.services.audit.chain import (
    _canonical_payload,
    _canonical_timestamp,
    compute_entry_hash,
)
from core.services.audit.hash import _get_audit_hash_key
from core.services.system import bypass_replication_triggers

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# assign_chain_fields
# ---------------------------------------------------------------------------


def test_assign_chain_fields_erwirbt_facility_spezifischen_lock(monkeypatch, facility, staff_user):
    """Killt ``x_assign_chain_fields__mutmut_11``: ``advisory_lock_name(instance.facility_id)``
    -> ``advisory_lock_name(None)``. Der Advisory-Lock-Name MUSS aus der
    tatsaechlichen ``facility_id`` der neuen Zeile abgeleitet werden — sonst
    erwerben alle Schreiber denselben globalen (Systemketten-)Lock statt des
    facility-spezifischen, und der im Docstring zugesicherte Lock-Reihenfolge-
    Vertrag (#2019/#2023) fuer Aufrufer, die ``advisory_lock_name`` selbst
    nutzen, stimmt nicht mehr mit dem tatsaechlich erworbenen Lock ueberein."""
    assert connection.vendor == "postgresql"
    calls: list = []
    original = chain_mod.advisory_lock_name

    def spy(fid):
        calls.append(fid)
        return original(fid)

    monkeypatch.setattr(chain_mod, "advisory_lock_name", spy)
    instance = AuditLog(facility=facility, user=staff_user, action=AuditLog.Action.LOGIN, detail={})
    with transaction.atomic():
        chain_mod.assign_chain_fields(instance)
    assert calls == [facility.pk]


def test_assign_chain_fields_bumpt_bei_exakter_kollision(facility, staff_user):
    """Killt drei Survivor-Mutanten der Kollisions-Monotonie in EINER exakten
    Assertion:

    - ``mutmut_23``: ``<=`` -> ``<`` — bei GLEICHEM Zeitstempel unterbliebe
      der Bump (zwei Zeilen mit identischem Zeitstempel waeren erlaubt).
    - ``mutmut_26``: ``+ timedelta(microseconds=1)`` -> ``- timedelta(...)`` —
      der Bump liefe RUECKWAERTS statt vorwaerts.
    - ``mutmut_29``: ``microseconds=1`` -> ``microseconds=2`` — der Bump-
      Betrag weicht vom dokumentierten Ein-Mikrosekunden-Vertrag ab.

    Zwei Zeilen derselben Facility mit BITGENAU demselben Zeitstempel muessen
    zu exakt ``timestamp + 1us`` fuer die zweite Zeile fuehren."""
    assert connection.vendor == "postgresql"
    erste = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
    kollidierend = AuditLog(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.LOGOUT,
        detail={},
        timestamp=erste.timestamp,
    )
    with transaction.atomic():
        chain_mod.assign_chain_fields(kollidierend)
    assert kollidierend.timestamp == erste.timestamp + timedelta(microseconds=1)


def test_assign_chain_fields_bumpt_bei_unverketteter_bestandszeile(facility, staff_user):
    """Killt ``x_assign_chain_fields__mutmut_22``: ``latest[0]`` -> ``latest[1]``
    in der Monotonie-Bedingung. Eine unverkettete Bestandszeile (vor
    ``backfill_audit_chain``, ``entry_hash=""``) hat einen falsy ``latest[1]``,
    aber einen gueltigen Zeitstempel ``latest[0]``. Die Original-Bedingung
    prueft ``latest[0]`` und bumpt trotzdem; der Mutant prueft ``latest[1]``
    und uebersieht die Kollision — die Monotonie-Garantie bricht genau an der
    Nahtstelle zu unmigrierten Altzeilen."""
    assert connection.vendor == "postgresql"
    bestand = audit_event(AuditLog.Action.LOGIN, user=staff_user, facility=facility)
    with transaction.atomic(), bypass_replication_triggers():
        AuditLog.objects.filter(pk=bestand.pk).update(entry_hash="")
    bestand.refresh_from_db()
    assert bestand.entry_hash == ""

    neu = AuditLog(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.LOGOUT,
        detail={},
        timestamp=bestand.timestamp,
    )
    with transaction.atomic():
        chain_mod.assign_chain_fields(neu)
    assert neu.timestamp == bestand.timestamp + timedelta(microseconds=1)


# ---------------------------------------------------------------------------
# _canonical_timestamp
# ---------------------------------------------------------------------------


def test_canonical_timestamp_falsy_leer_truthy_isoformat():
    """Killt ``x__canonical_timestamp__mutmut_1`` (``if not ts:`` -> ``if ts:``,
    vertauscht den Early-Return: ein ECHTER Zeitstempel liefert sofort ``""``
    statt des ISO-Strings, ein falsy Zeitstempel faellt bis ``ts.utcoffset()``
    durch und crasht) und ``x__canonical_timestamp__mutmut_2`` (``return ""``
    -> ``return "XXXX"`` fuer den Falsy-Fall — der Platzhalter ginge in den
    Kettenhash ein)."""
    assert _canonical_timestamp(None) == ""
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert _canonical_timestamp(aware) == aware.isoformat()


def test_canonical_timestamp_naive_wird_als_utc_interpretiert():
    """Killt drei Survivor-Mutanten der Naiv-zu-UTC-Normalisierung per
    deterministischem Spy (Systemzeitzone bleibt irrelevant):

    - ``mutmut_4``: ``ts = ts.replace(tzinfo=UTC)`` -> ``ts = None`` —
      ``.replace`` wird NIE aufgerufen.
    - ``mutmut_5``: ``tzinfo=UTC`` -> ``tzinfo=None`` beim ``.replace``-Call —
      der naive Zeitstempel bleibt naiv statt als UTC interpretiert.
    - ``mutmut_6``: finales ``ts.astimezone(UTC)`` -> ``ts.astimezone(None)`` —
      die letzte Normalisierung zielt auf die lokale statt die UTC-Zone.
    """
    ts = MagicMock(name="naive_ts")
    ts.utcoffset.return_value = None
    replaced = MagicMock(name="replaced_ts")
    ts.replace.return_value = replaced
    replaced.astimezone.return_value.isoformat.return_value = "SENTINEL-ISO"

    result = _canonical_timestamp(ts)

    ts.replace.assert_called_once_with(tzinfo=UTC)
    replaced.astimezone.assert_called_once_with(UTC)
    assert result == "SENTINEL-ISO"


# ---------------------------------------------------------------------------
# _latest_link
# ---------------------------------------------------------------------------


def test_latest_link_nimmt_raw_pfad_nur_bei_echtem_postgres_vendor(monkeypatch):
    """Killt drei Survivor-Mutanten des Vendor-Vergleichs in EINER Spy-
    Assertion — alle drei sorgen dafuer, dass der RLS-eng-gescopte
    System-Raw-Read auf echtem Postgres NIE genommen wird (der ORM-Fallback
    saehe die System-Kette unter RLS nicht, ``prev_hash`` wuerde faelschlich
    ``""`` — die Systemkette risse, siehe Moduldocstring):

    - ``mutmut_3``: ``==`` -> ``!=``.
    - ``mutmut_4``: Literal ``"postgresql"`` -> mutmut-Marker
      ``"XXpostgresqlXX"`` (matcht nie).
    - ``mutmut_5``: Literal ``"postgresql"`` -> ``"POSTGRESQL"`` (Grossschreibung,
      ``connection.vendor`` liefert immer Kleinschreibung).
    """
    assert connection.vendor == "postgresql"
    calls: list = []

    def spy():
        calls.append(True)
        return None

    monkeypatch.setattr(chain_mod, "_system_chain_latest_link_raw", spy)
    chain_mod._latest_link(None)
    assert calls == [True]


def test_latest_link_order_by_hat_id_tiebreak(monkeypatch, facility):
    """Killt ``x__latest_link__mutmut_13``: ``.order_by("-timestamp", "-id")``
    -> ``.order_by("-timestamp", )`` — der pk-Tiebreak fehlt. Per Spy auf die
    tatsaechlichen ``order_by``-Argumente deterministisch gepinnt statt ueber
    physische Zeilenreihenfolge bei einem Zeitstempel-Gleichstand (Standard-
    block-Lehre zu Sortier-/pk-Tiebreak-Mutanten: ohne den ``-id``-Tiebreak
    waere die 'juengste' Zeile bei zwei Zeilen mit identischem Zeitstempel
    nicht mehr deterministisch — ``assign_chain_fields`` koennte den falschen
    ``prev_hash`` uebernehmen)."""
    assert connection.vendor == "postgresql"
    calls: list = []
    real_filter = AuditLog.objects.filter

    class _OrderBySpy:
        def __init__(self, qs):
            self._qs = qs

        def order_by(self, *args):
            calls.append(args)
            return self._qs.order_by(*args)

    def spy_filter(*a, **kw):
        return _OrderBySpy(real_filter(*a, **kw))

    monkeypatch.setattr(AuditLog.objects, "filter", spy_filter)
    chain_mod._latest_link(facility.pk)
    assert calls == [("-timestamp", "-id")]


# ---------------------------------------------------------------------------
# compute_entry_hash
# ---------------------------------------------------------------------------


def test_compute_entry_hash_referenzwert_gegen_unabhaengige_rekonstruktion(facility, staff_user):
    """Killt ``x_compute_entry_hash__mutmut_5``: ``(prev_hash or "")`` ->
    ``(prev_hash or "XXXX")``. Der Referenzwert wird UNABHAENGIG von
    ``compute_entry_hash``s eigener Default-Logik gebaut — ``""`` wird explizit
    als Praefix VOR dem kanonischen Payload verwendet, nicht ueber
    ``prev_hash or ...``. Ein falscher Platzhalter-Default fuer den
    Kettenstart (leerer/``None`` prev_hash) faellt damit sofort auf."""
    row = AuditLog(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.LOGIN,
        target_type="Client",
        target_id="abc",
        detail={"a": 1},
        ip_address="10.0.0.1",
        timestamp=timezone.now(),
    )
    canonical = _canonical_payload(row)
    erwartet_kettenstart = hmac.new(_get_audit_hash_key(), ("" + canonical).encode("utf-8"), hashlib.sha256).hexdigest()

    assert compute_entry_hash(row, "") == erwartet_kettenstart
    assert compute_entry_hash(row, None) == erwartet_kettenstart


# ---------------------------------------------------------------------------
# advisory_lock_name / advisory_lock_id
# ---------------------------------------------------------------------------


def test_advisory_lock_name_facility_vs_system(facility):
    """Killt ``x_advisory_lock_name__mutmut_1`` (Bedingung ``is not None`` ->
    ``is None`` vertauscht — echte Facilities bekaemen den Systemketten-Namen
    und umgekehrt) und ``x_advisory_lock_name__mutmut_2`` (Default ``0`` ->
    ``1`` — der Systemketten-Lock-Name kollidiert mit dem einer echten
    Facility mit ``pk=1``)."""
    assert chain_mod.advisory_lock_name(None) == "audit_chain_0"
    assert chain_mod.advisory_lock_name(facility.pk) == f"audit_chain_{facility.pk}"


def test_advisory_lock_id_verwendet_facility_spezifischen_namen(monkeypatch, facility):
    """Killt ``x_advisory_lock_id__mutmut_11``: ``advisory_lock_name(facility_id)``
    -> ``advisory_lock_name(None)``. ``advisory_lock_id`` MUSS den
    facility-spezifischen Namen an ``advisory_lock_name`` uebergeben — sonst
    liefert die Funktion fuer JEDE Facility denselben ``hashtext``-Wert und
    der im Docstring zugesicherte Deadlock-Vermeidungs-Vertrag (sortierte
    Locks je Facility, #2019/#2023) ist wertlos."""
    assert connection.vendor == "postgresql"
    calls: list = []
    original = chain_mod.advisory_lock_name

    def spy(fid):
        calls.append(fid)
        return original(fid)

    monkeypatch.setattr(chain_mod, "advisory_lock_name", spy)
    chain_mod.advisory_lock_id(facility.pk)
    assert calls == [facility.pk]

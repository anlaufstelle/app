"""Pin-Tests der Welle L (0.99, Refs #2174, Refs #2193) — Testlücken in
``core.services.system.offline_revocation`` und ``core.services.system.ban_revision``,
Batch ``system-4``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-system-4.json`` (11 Survivor-
Mutanten über die Offline-Widerrufsliste und die Hausverbots-Revision;
Triage: ``welle-l-0.99/reports/m-scratch/pins-system-4/triage-099-system-4.json``).
9 der 11 Mutanten sind Klasse ``test`` (hier gepinnt), 2 sind nachweislich
``equivalent`` (NICHT gepinnt, Begründung siehe Triage-JSON).

* **``x_list_offline_client_revocations__mutmut_22/_26/_29/_30/_31``**: das
  ``%s``-Argument der Obergrenzen-Meldung (``facility.pk`` -> ``None``) bzw.
  reine XX-Marker-/Gross-Klein-Varianten desselben Wortlauts der beiden
  zusammengesetzten Log-Textliterale — von EINER exakten ``caplog``-Text-
  Assertion gemeinsam getötet.
* **``x_bump_ban_revision__mutmut_8``**: der UPDATE-zuerst-Zweig verliert
  seinen facility-Scope-Filter (``for_facility(facility)`` ->
  ``for_facility(None)``); der erste ``for_facility``-Aufruf muss
  nachweislich mit der übergebenen Einrichtung erfolgen.
* **``x_bump_ban_revision_for_event_change__mutmut_17/_19/_20``**: der
  Fallback-Default für eine bei ``SENSITIVITY_RANK`` unbekannte
  ``document_type.sensitivity`` kippt von ``0`` (dokumentiert konservativ:
  Stratum, das JEDE Rolle liest) auf ``None`` (Crash beim Indizieren von
  ``BAN_REVISION_STRATA``) bzw. ``1`` (falsches, für Rang-0-Rollen
  unsichtbares Stratum).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import logging

import pytest

from core.models import BanRevision, Client, DocumentType
from core.services.system.ban_revision import (
    bump_ban_revision,
    bump_ban_revision_for_event_change,
)
from core.services.system.offline_revocation import (
    list_offline_client_revocations,
    revoke_offline_client_bundles,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_OFFLINE_MODUL = "core.services.system.offline_revocation"


def _revoke_n(facility, staff_user, count):
    for index in range(count):
        client = Client.objects.create(
            facility=facility,
            pseudonym=f"Pin-099-System4-Revoke-{index}",
            created_by=staff_user,
        )
        revoke_offline_client_bundles(client)


def test_ueberlauf_loggt_exakten_meldungstext_mit_facility(facility, staff_user, monkeypatch, caplog):
    """Pins ``x_list_offline_client_revocations__mutmut_22`` (Log-Argument
    ``facility.pk`` -> ``None``); nimmt ``__mutmut_26``/``__mutmut_29``
    (XX-Marker um je eines der beiden Textliterale) sowie ``__mutmut_30``/
    ``__mutmut_31`` (Klein-/Grossschreibungs-Varianten des zweiten
    Textliterals) mit — jede Textabweichung bricht die exakte Assertion auf
    die VOLLE, zusammengesetzte Meldung. Deckel per monkeypatch auf 3
    verkleinert, 4 Widerrufe angelegt, damit der Überlauf-Zweig greift."""
    monkeypatch.setattr(f"{_OFFLINE_MODUL}.OFFLINE_REVOCATION_MAX_ENTRIES", 3)
    _revoke_n(facility, staff_user, 4)

    with caplog.at_level(logging.ERROR, logger=_OFFLINE_MODUL):
        entries = list_offline_client_revocations(facility)

    assert len(entries) == 3
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        f"Offline-Revoke-Liste: Obergrenze (3) fuer Facility {facility.pk} erreicht — "
        "aelteste Eintraege nicht ausgeliefert (Bundle-TTL bleibt Backstop)."
    )


def test_bump_ban_revision_scoped_update_traegt_die_gegebene_facility(facility, monkeypatch):
    """Pins ``x_bump_ban_revision__mutmut_8``: der UPDATE-zuerst-Zweig
    ``BanRevision.objects.for_facility(facility).update(...)`` verliert
    seinen facility-Scope-Filter (-> ``for_facility(None)``). Ein Spy auf
    ``for_facility`` zeichnet das jeweils übergebene Facility-Argument auf —
    der ERSTE Aufruf muss mit der echten Einrichtung erfolgen, nicht mit
    ``None``. Unter dem Original trifft der erste (einzige) Aufruf die
    bestehende Zeile direkt; unter dem Mutanten läuft der Aufruf zuerst ins
    Leere (0 Zeilen mit ``facility_id IS NULL``), was den INSERT-Zweig samt
    ``IntegrityError``-Retry auslöst — der End-Zustand bleibt zwar über den
    (unmutierten) Retry-``for_facility``-Aufruf korrekt, der erste
    aufgezeichnete Aufruf verrät den Scope-Verlust aber unmittelbar."""
    revision = BanRevision.objects.create(facility=facility, revision_normal=7)
    manager_cls = type(BanRevision.objects)
    real_for_facility = manager_cls.for_facility
    recorded = []

    def spy_for_facility(self, fac):
        recorded.append(fac)
        return real_for_facility(self, fac)

    monkeypatch.setattr(manager_cls, "for_facility", spy_for_facility)

    bump_ban_revision(facility, [0])

    assert recorded[0] is facility
    revision.refresh_from_db()
    assert revision.revision_normal == 8


def test_event_change_mit_unbekannter_sensitivity_bumpt_stratum_normal(facility):
    """Pins ``x_bump_ban_revision_for_event_change__mutmut_17``/``_19``
    (Fallback-Default ``0`` -> ``None`` bzw. syntaktisch entfernt: beide
    ergeben zur Laufzeit ``SENSITIVITY_RANK.get(..., None)`` und damit eine
    ``TypeError`` beim Indizieren von ``BAN_REVISION_STRATA``) sowie
    ``__mutmut_20`` (Default ``0`` -> ``1``: falsches, für Rang-0-Rollen
    unsichtbares Stratum). Eine bei ``SENSITIVITY_RANK`` unbekannte
    Sensitivity (``choices`` sind nur Formularvalidierung, keine
    DB-Beschränkung) muss konservativ Stratum 0 bumpen — dasselbe Stratum,
    das JEDE Rolle im Präfix liest."""
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Hausverbot (Pin-099-System4)",
        system_type=DocumentType.SystemType.BAN,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
    )
    DocumentType.objects.filter(pk=doc_type.pk).update(sensitivity="unbekannter-wert")
    doc_type.refresh_from_db()
    before = {"grund": "Alter Grund", "bis": "", "aktiv": True}
    after = {"grund": "Neuer Grund", "bis": "", "aktiv": True}

    bump_ban_revision_for_event_change(facility, doc_type, before, after)

    revision = BanRevision.objects.get(facility=facility)
    assert (revision.revision_normal, revision.revision_elevated, revision.revision_high) == (1, 0, 0)

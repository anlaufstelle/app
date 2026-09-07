"""Konfigurations-Pfade im Django-Admin hinterlassen dieselbe Spur wie die Fach-UI (Refs #2023).

Zwei Luecken:

* ``FieldTemplateAdmin.deactivate_selected``/``activate_selected`` schalteten
  ``is_active`` per rohem ``QuerySet.update()`` um — eine Feldvorlage
  verschwindet damit aus (oder erscheint in) allen Erfassungsformularen der
  Einrichtung, ohne dass eine Zeile im append-only AuditLog entsteht.
* ``DocumentTypeAdmin.save_model`` laeuft bewusst an
  ``core.services.config.save_document_type`` vorbei (technischer Fallback,
  Refs #1637) — genau dort entstuenden aber die ``CONFIG_UPDATE``-Zeilen des
  Config-UI-Pfads.

Beide Wege schreiben jetzt die Vokabel des Config-UI (``CONFIG_CREATE`` /
``CONFIG_UPDATE`` mit ``name`` + ``changed_fields``), damit unter /audit/ ein
Vorgang eine Zeile ist — egal ob er ueber die Fach-UI oder den technischen
Fallback lief.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.forms.models import model_to_dict
from django.test.utils import CaptureQueriesContext

from core.admin import documents as documents_admin
from core.admin.documents import DocumentTypeAdmin, FieldTemplateAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import AuditLog, DocumentType, Facility, FieldTemplate
from core.services.audit.chain import advisory_lock_id

pytestmark = pytest.mark.django_db

CONFIG_UPDATE = AuditLog.Action.CONFIG_UPDATE
CONFIG_CREATE = AuditLog.Action.CONFIG_CREATE


def _request(rf, user, method="post"):
    request = getattr(rf, method)("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.META["REMOTE_ADDR"] = "203.0.113.42"
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _field_template(facility, name, *, is_active=True):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.TEXT,
        is_active=is_active,
    )


def _config_entries(obj, action=CONFIG_UPDATE):
    return AuditLog.objects.filter(action=action, target_id=str(obj.pk))


class TestFieldTemplateBulkActionAudit:
    def test_deactivate_selected_writes_entry_per_template(self, rf, admin_user, facility):
        ft_a = _field_template(facility, "Bulk-A")
        ft_b = _field_template(facility, "Bulk-B")
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        request = _request(rf, admin_user)

        admin_cls.deactivate_selected(request, FieldTemplate.objects.filter(pk__in=[ft_a.pk, ft_b.pk]))

        for template in (ft_a, ft_b):
            template.refresh_from_db()
            assert template.is_active is False
            entry = _config_entries(template).get()
            assert entry.user_id == admin_user.pk
            assert entry.target_type == "FieldTemplate"
            assert entry.detail["name"] == template.name
            assert entry.detail["changed_fields"] == ["is_active"]
            assert entry.detail["bulk"] is True
            assert entry.facility_id == facility.pk

    def test_activate_selected_writes_entry_per_template(self, rf, admin_user, facility):
        ft = _field_template(facility, "Bulk-C", is_active=False)
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)

        admin_cls.activate_selected(_request(rf, admin_user), FieldTemplate.objects.filter(pk=ft.pk))

        ft.refresh_from_db()
        assert ft.is_active is True
        assert _config_entries(ft).count() == 1

    def test_no_entry_when_nothing_changes(self, rf, admin_user, facility):
        """Bereits deaktivierte Vorlagen erzeugen keine Zeile — sonst Audit-Rauschen."""
        ft = _field_template(facility, "Bulk-D", is_active=False)
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)

        admin_cls.deactivate_selected(_request(rf, admin_user), FieldTemplate.objects.filter(pk=ft.pk))

        assert _config_entries(ft).count() == 0


class TestDocumentTypeAdminFallbackAudit:
    """Der #1637-Fallback bleibt am Service vorbei — die Rechenschaftszeile nicht."""

    @staticmethod
    def _bound_form(admin_cls, request, obj, **overrides):
        form_cls = admin_cls.get_form(request, obj=obj, change=obj is not None)
        data = {}
        if obj is not None:
            raw = model_to_dict(obj, fields=list(form_cls.base_fields))
            data = {key: ("" if value is None else value) for key, value in raw.items()}
        data.update(overrides)
        form = form_cls(data, instance=obj)
        assert form.is_valid(), form.errors
        return form

    def test_change_writes_config_update(self, rf, admin_user, doc_type_contact):
        admin_cls = DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)
        request = _request(rf, admin_user)
        form = self._bound_form(admin_cls, request, doc_type_contact, name="Kontakt (neu)")

        admin_cls.save_model(request, form.save(commit=False), form, change=True)

        entry = _config_entries(doc_type_contact).get()
        assert entry.user_id == admin_user.pk
        assert entry.target_type == "DocumentType"
        assert entry.detail["name"] == "Kontakt (neu)"
        assert entry.detail["changed_fields"] == ["name"]
        assert entry.facility_id == doc_type_contact.facility_id

    def test_create_writes_config_create(self, rf, admin_user, facility):
        admin_cls = DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)
        request = _request(rf, admin_user)
        form = self._bound_form(
            admin_cls,
            request,
            None,
            facility=str(facility.pk),
            name="Neuer Typ",
            sensitivity=DocumentType.Sensitivity.NORMAL,
            entity_type=DocumentType.EntityType.EVENT,
            sort_order=0,
        )

        obj = form.save(commit=False)
        admin_cls.save_model(request, obj, form, change=False)

        entry = _config_entries(obj, action=CONFIG_CREATE).get()
        assert entry.detail["name"] == "Neuer Typ"
        assert entry.user_id == admin_user.pk


class TestFieldTemplateBulkAtomicity:
    """Fix-Runde 1 (Cross-Review): der Bulk-Schalter ist alles-oder-nichts.

    ``_bulk_set_active`` materialisierte erst die betroffenen Vorlagen, feuerte
    dann ein unbedingtes ``update()`` und schrieb danach die Auditzeilen —
    ohne Transaktion, ohne Zeilensperren und ohne die Rueckgabezahl des
    ``update()`` auch nur anzusehen. Moeglich waren dadurch: Zustandsaenderung
    ohne Audit bei Fehlern, doppelte Auditzeilen bei parallelen gleichgerichteten
    Aktionen und Auditzeilen fuer zwischenzeitlich geaenderte Vorlagen.
    """

    def test_audit_failure_rolls_back_the_whole_bulk(self, rf, admin_user, facility, monkeypatch):
        """Fail-closed: keine Audit-Zeile ⇒ auch keine Umschaltung."""
        ft_a = _field_template(facility, "Atomar-A")
        ft_b = _field_template(facility, "Atomar-B")
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)

        def _boom(*args, **kwargs):
            raise RuntimeError("Audit-Kette nicht schreibbar")

        # Modul-Referenz stubben (NICHT den connection-Proxy patchen).
        monkeypatch.setattr("core.admin.documents.log_audit_event", _boom)

        with pytest.raises(RuntimeError):
            admin_cls.deactivate_selected(
                _request(rf, admin_user), FieldTemplate.objects.filter(pk__in=[ft_a.pk, ft_b.pk])
            )

        for template in (ft_a, ft_b):
            template.refresh_from_db()
            assert template.is_active is True, (
                "Eine Umschaltung ohne Audit-Zeile ist genau die Luecke aus #2023 — sie muss zurueckgerollt werden"
            )
            assert _config_entries(template).count() == 0

    def test_selected_rows_are_locked_before_the_update(self, rf, admin_user, facility):
        """Die Vorlagen werden gesperrt — sonst schreiben zwei parallele
        gleichgerichtete Aktionen zwei Zeilen fuer eine Umschaltung."""
        ft = _field_template(facility, "Atomar-C")
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)

        with CaptureQueriesContext(connection) as ctx:
            admin_cls.deactivate_selected(_request(rf, admin_user), FieldTemplate.objects.filter(pk=ft.pk))

        sperren = [
            query["sql"]
            for query in ctx.captured_queries
            if "FOR UPDATE" in query["sql"].upper() and "core_fieldtemplate" in query["sql"].lower()
        ]
        assert sperren, "Der Vorfilter muss die betroffenen Vorlagen per select_for_update sperren"

    def test_mixed_selection_reports_and_audits_only_real_changes(self, rf, admin_user, facility):
        """Die gemeldete Zahl stammt aus der ``update()``-Rueckgabe, nicht aus
        der Laenge des Snapshots."""
        aktiv = _field_template(facility, "Atomar-D", is_active=True)
        schon_inaktiv = _field_template(facility, "Atomar-E", is_active=False)
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)

        geaendert = admin_cls._bulk_set_active(
            _request(rf, admin_user),
            FieldTemplate.objects.filter(pk__in=[aktiv.pk, schon_inaktiv.pk]),
            is_active=False,
        )

        assert geaendert == 1
        assert _config_entries(aktiv).count() == 1
        assert _config_entries(schon_inaktiv).count() == 0


# --- Fix-Runde 2: kanonische Lock-Reihenfolge ueber Facilities hinweg -------
#
# Begruendung wie bei der MFA-Sammelaktion: jeder Audit-Insert nimmt in
# ``services/audit/chain.py`` einen transaktionsweiten Advisory-Lock PRO
# FACILITY. Ein super_admin sieht einrichtungsuebergreifend, sein Bulk-Lauf
# haelt also potenziell mehrere Facility-Locks bis zum Commit. Ohne stabile
# Reihenfolge deadlocken zwei parallele Laeufe ueber disjunkte Vorlagenmengen.

_KLEINE_FACILITY_ID = uuid.UUID("00000000-0000-4000-8000-000000000011")
_GROSSE_FACILITY_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-fffffffffff1")
_KLEINE_TEMPLATE_ID = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
_GROSSE_TEMPLATE_ID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffaa")


@pytest.fixture
def gemischte_vorlagen(organization):
    """Zwei Vorlagen, deren PK-Ordnung der Facility-Ordnung WIDERSPRICHT.

    Die Vorlage in der Facility mit der GROSSEN UUID bekommt die KLEINE
    Vorlagen-UUID. Unter ``order_by("pk")`` liefe sie zuerst — die
    Facility-Locks fielen absteigend an. Genau diesen Konflikt braucht der
    Test, sonst waere er zufaellig gruen.
    """
    klein = Facility.objects.create(id=_KLEINE_FACILITY_ID, organization=organization, name="Erststelle")
    gross = Facility.objects.create(id=_GROSSE_FACILITY_ID, organization=organization, name="Zweitstelle")
    ft_in_gross = FieldTemplate.objects.create(
        id=_KLEINE_TEMPLATE_ID,
        facility=gross,
        name="Vorlage in grosser Facility",
        field_type=FieldTemplate.FieldType.TEXT,
        is_active=True,
    )
    ft_in_klein = FieldTemplate.objects.create(
        id=_GROSSE_TEMPLATE_ID,
        facility=klein,
        name="Vorlage in kleiner Facility",
        field_type=FieldTemplate.FieldType.TEXT,
        is_active=True,
    )
    return klein, gross, ft_in_klein, ft_in_gross


class TestFieldTemplateBulkFacilityLockOrder:
    """Ordnungsvertrag auf den TATSAECHLICHEN Advisory-Lock-Keys.

    Begruendung wie im MFA-Bulk: gesperrt wird ``hashtext(...)``, eine
    32-Bit- und damit nicht injektive Abbildung. Nach ``facility_id`` zu
    ordnen sagt ueber die Key-Reihenfolge nichts aus; bei einer Kollision
    bleibt der Deadlock-Zyklus offen.
    """

    @staticmethod
    def _lock_reihenfolge(monkeypatch, rf, actor, vorlagen):
        reihenfolge = []
        original = documents_admin.log_audit_event

        def _spy(*args, **kwargs):
            reihenfolge.append(kwargs["facility"].pk)
            return original(*args, **kwargs)

        monkeypatch.setattr("core.admin.documents.log_audit_event", _spy)
        admin_cls = FieldTemplateAdmin(FieldTemplate, anlaufstelle_admin_site)
        admin_cls.deactivate_selected(
            _request(rf, actor), FieldTemplate.objects.filter(pk__in=[v.pk for v in vorlagen])
        )
        return reihenfolge

    def test_processing_follows_the_actual_lock_key_order(self, rf, super_admin_user, gemischte_vorlagen, monkeypatch):
        """Die Key-Funktion wird gegenlaeufig zur facility_id-Ordnung gestubbt."""
        klein, gross, ft_in_klein, ft_in_gross = gemischte_vorlagen
        schluessel = {klein.pk: 4711, gross.pk: 42}
        monkeypatch.setattr("core.admin.documents.advisory_lock_id", lambda fid: schluessel[fid])

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [ft_in_gross, ft_in_klein])

        assert reihenfolge == [gross.pk, klein.pk], (
            "Verarbeitet werden muss nach dem tatsaechlichen Advisory-Lock-Key, nicht nach facility_id"
        )
        assert [schluessel[fid] for fid in reihenfolge] == sorted(schluessel.values())

    def test_key_collision_falls_back_to_a_deterministic_tie_break(
        self, rf, super_admin_user, gemischte_vorlagen, monkeypatch
    ):
        klein, gross, ft_in_klein, ft_in_gross = gemischte_vorlagen
        monkeypatch.setattr("core.admin.documents.advisory_lock_id", lambda fid: 99)

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [ft_in_gross, ft_in_klein])

        assert reihenfolge == sorted(reihenfolge, key=str), (
            "Bei Key-Kollision muss die facility_id als deterministischer Tie-Break greifen"
        )

    def test_real_lock_keys_are_acquired_in_ascending_order(
        self, rf, super_admin_user, gemischte_vorlagen, monkeypatch
    ):
        """Gegenprobe ohne Stub: mit den ECHTEN hashtext-Werten aus Postgres."""
        klein, gross, ft_in_klein, ft_in_gross = gemischte_vorlagen

        reihenfolge = self._lock_reihenfolge(monkeypatch, rf, super_admin_user, [ft_in_gross, ft_in_klein])

        keys = [advisory_lock_id(fid) for fid in reihenfolge]
        assert keys == sorted(keys), f"Advisory-Lock-Keys nicht aufsteigend erworben: {keys}"

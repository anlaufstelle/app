"""Pin-Tests der Welle L (0.99, Refs #2174, Refs #2193) — Testlücken in
``core.services.system.offline``, ``core.services.system.export`` und
``core.services.system.settings``, Batch ``system-3``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-system-3.json`` (70 Survivor-
Mutanten; Triage: ``welle-l-0.99/reports/m-scratch/pins-system-3/
triage-099-system-3.json``). 31 der 70 Mutanten sind Klasse ``test`` (hier
gepinnt), 39 sind nachweislich ``equivalent`` (NICHT gepinnt, Begründung je
Mutante in der Triage-JSON — SQL-Keyword-Case-Insensitivität,
``select_related()``-Praefetch-Hints ohne Dateneffekt, ``None``-Defaults in
bereits-falsy Bool-Kontexten und ein strukturell unerreichbarer
``source=``-Parameter in ``validate_audit_detail``).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

from datetime import time as dt_time
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db.models import QuerySet
from django.utils import timezone

from core.models import Case, DocumentType, TimeFilter, User
from core.services.system import (
    BUNDLE_TTL_SECONDS,
    _serialize_case,
    _serialize_event,
    _serialize_workitem,
    _visible_data_fields,
    build_facility_offline_bundle,
    export_events_csv,
    snapshot_settings,
)
from core.services.system.export import restricted_url_fetcher
from core.services.system.offline import _bundle_document_types, _field_gate_sensitivity, _serialize_assignable_users
from core.services.system.settings import _AUDIT_FIELDS, _jsonable
from tests._mutation_followup_offline_helpers import _make_event, _make_field_template

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _capture_order_by(model_name: str, call) -> list[tuple]:
    """Erfasst jeden ``QuerySet.order_by``-Aufruf des Modells *model_name*
    waehrend ``call()`` laeuft, als Liste der Argument-Tupel (Muster
    ``test_mutation_pins_099_audit_chain.py::_capture_order_by``, hier lokal
    kopiert — kein Import aus jener Datei noetig).

    Planunabhaengig: prueft das tatsaechlich an ``order_by()`` uebergebene
    Argument-Tupel, NICHT die von Postgres zurueckgelieferte Zeilenreihenfolge
    (die ist bei Gleichstand ohne vollstaendigen Tiebreak implementierungs-
    abhaengig und kein Aequivalenzbeweis, Standardblock Regel 3).
    """
    calls: list[tuple] = []
    original = QuerySet.order_by

    def _spy(self, *args, **kwargs):
        if self.model.__name__ == model_name:
            calls.append(args)
        return original(self, *args, **kwargs)

    with mock.patch.object(QuerySet, "order_by", _spy):
        call()
    return calls


# ---------------------------------------------------------------------------
# core.services.system.export
# ---------------------------------------------------------------------------


class TestExportEventsCsvSystemLabel:
    """Killt ``x_export_events_csv__mutmut_31/_32/_33``: das
    ``"System"``-Literal im Metadaten-Kopf der exportierten CSV (Fallback bei
    ``user=None``, Cron-/Systemmodus) — XX-Marker- und Schreibweisen-Varianten
    desselben Wortlauts werden vom exakten Text-Abgleich gemeinsam getoetet."""

    def test_metadata_row_shows_exact_system_label_for_missing_user(self, facility):
        from datetime import timedelta

        today = timezone.now().date()
        generator = export_events_csv(facility, today - timedelta(days=1), today, user=None)
        metadata_chunk = next(generator)
        assert metadata_chunk.startswith(f"# Export: {facility.name} | System | ")
        assert "XXSystemXX" not in metadata_chunk
        assert "| system |" not in metadata_chunk
        assert "| SYSTEM |" not in metadata_chunk


class TestRestrictedUrlFetcherMessage:
    """Killt ``x_restricted_url_fetcher__mutmut_5``: Meldungs-Verlust
    (``ValueError(f"... {url!r}")`` -> ``ValueError(None)``) — exakte
    Assertion auf ``exc.value.args``."""

    def test_raises_with_exact_message_including_the_url(self):
        with pytest.raises(ValueError) as exc_info:
            restricted_url_fetcher("https://evil.example/pfad")
        assert exc_info.value.args == (
            "Externe Ressourcen sind in PDF-Exporten nicht erlaubt: 'https://evil.example/pfad'",
        )


# ---------------------------------------------------------------------------
# core.services.system.offline — _field_gate_sensitivity / _visible_data_fields
# ---------------------------------------------------------------------------


class TestFieldGateSensitivityOrphanLookup:
    """Killt ``x__field_gate_sensitivity__mutmut_6/_7``: der Lookup eines
    aufgeloesten Waisen-Templates (``orphan_templates.get(slug)``) wird durch
    ``orphan_templates.get(None)`` bzw. ein hartes ``None`` ersetzt — die
    Funktion faellt dann IMMER auf den konservativen HIGH-Default zurueck,
    statt die echte (hier niedrigere) Sensitivity des gefundenen Templates zu
    liefern."""

    def test_resolved_orphan_template_sensitivity_is_used_not_forced_high(self):
        stub_template = SimpleNamespace(sensitivity="elevated")
        result = _field_gate_sensitivity({}, {"alt-slug": stub_template}, "alt-slug")
        assert result == "elevated"


class TestVisibleDataFieldsOrphanSlugFilter:
    """Killt ``x__visible_data_fields__mutmut_8``: der Waisen-Filter
    (``slug not in field_templates``) wird zu ``slug in field_templates``
    invertiert — ein ECHTER verwaister Slug (kein aktuelles Feld am Typ, aber
    facility-weit ein FieldTemplate mit niedrigerer Sensitivity) wird dann nie
    an ``resolve_orphan_field_templates`` uebergeben und faellt faelschlich
    auf die konservative HIGH-Gate zurueck (statt seiner echten, niedrigeren
    Sensitivity) — Fachkraft (STAFF, Max ELEVATED) sieht das Feld dann nicht
    mehr, obwohl es NORMAL-sensitiv ist."""

    def test_true_orphan_slug_keeps_its_real_lower_sensitivity(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        # Facility-weites Template MIT normaler (leerer) Sensitivity, aber
        # NICHT an doc_type_contact attached -> beim Event ist es ein echter
        # Waise-Slug.
        ft = _make_field_template(facility, name="Ehemals-Feld", sensitivity="")
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={ft.slug: "sichtbarer Wert"},
        )
        result = _visible_data_fields(staff_user, event)
        assert result.get(ft.slug) == "sichtbarer Wert", (
            "Ein verwaister NORMAL-Slug mit facility-weit aufloesbarem Template "
            "muss fuer STAFF sichtbar bleiben, statt (invertierter Filter) "
            "konservativ auf HIGH zu fallen und damit ausgeblendet zu werden."
        )


# ---------------------------------------------------------------------------
# core.services.system.offline — Serializer-Helfer (getattr-Default-Eskalation)
# ---------------------------------------------------------------------------


class TestSerializeEventCanEditWithoutStaffProperty:
    """Killt ``x__serialize_event__mutmut_44`` (``is_staff_or_above``-Default
    ``False`` -> ``True``: Berechtigungs-Eskalation, sobald das uebergebene
    user-Objekt kein solches Property traegt) UND ``x__serialize_event__
    mutmut_52`` (``getattr(user, "pk", None)`` verliert sein Default-Argument
    und wirft ``AttributeError`` statt ``None`` zu liefern, sobald
    ``event.created_by_id`` gesetzt ist und die rechte Seite des ``and``
    ausgewertet wird) — derselbe Aufruf deckt beide Mutanten."""

    def test_can_edit_stays_false_for_user_object_without_the_property(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, created_by=staff_user)
        stub_user = object()  # weder is_staff_or_above noch pk
        result = _serialize_event(stub_user, event)
        assert result["can_edit"] is False


class TestSerializeWorkitemCanEditWithoutStaffProperty:
    """Killt ``x__serialize_workitem__mutmut_12`` (``is_staff_or_above``-
    Default ``False`` -> ``True``): ohne den Kurzschluss wird
    ``can_user_mutate_workitem`` tatsaechlich aufgerufen — bei einer NICHT
    zugewiesenen WorkItem greift deren eigene Team-Aufgaben-Regel
    (``assigned_to_id is None``) und liefert ``True``, obwohl das user-Objekt
    gar kein Staff-Property hat."""

    def test_can_edit_stays_false_for_unassigned_workitem_without_staff_property(self, sample_workitem):
        assert sample_workitem.assigned_to_id is None

        class _StubNoStaffProperty:
            is_lead_or_admin = False

        result = _serialize_workitem(_StubNoStaffProperty(), sample_workitem)
        assert result["can_edit"] is False


class TestSerializeCaseDescriptionWithoutStaffProperty:
    """Killt ``x__serialize_case__mutmut_10`` (``is_staff_or_above``-Default
    ``False`` -> ``True``): die STAFF_PLUS-only ``description`` (HIGH-
    sensitiver Freitext It. Docstring) wuerde an ein user-Objekt ohne das
    Property leaken."""

    def test_description_stays_hidden_for_user_object_without_the_property(
        self, facility, client_identified, staff_user
    ):
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall",
            description="Vertrauliche Akteninformation",
            status=Case.Status.OPEN,
            created_by=staff_user,
        )
        result = _serialize_case(object(), case)
        assert result["description"] == ""


class TestSerializeAssignableUsersShapeAndName:
    """Killt ``x__serialize_assignable_users__mutmut_15/_16`` (Dict-Key
    ``"name"`` -> ``"XXnameXX"``/``"NAME"``: Bundle-Schema-Bruch) UND
    ``x__serialize_assignable_users__mutmut_17`` (``or`` -> ``and`` bei
    ``get_full_name() or username``: mit gesetztem vollem Namen liefert
    ``and`` faelschlich den ``username``) — ein einziger exakter
    Dict-Vergleich mit einem User, dessen voller Name vom Username abweicht,
    toetet alle drei."""

    def test_result_matches_exact_dict_with_full_name_priority(self, facility):
        user = User.objects.create_user(
            username="ehrenamt.7",
            role=User.Role.STAFF,
            facility=facility,
            is_active=True,
            first_name="Erika",
            last_name="Musterfrau",
        )
        result = _serialize_assignable_users(facility)
        assert result == [{"pk": str(user.pk), "name": "Erika Musterfrau"}]


# ---------------------------------------------------------------------------
# core.services.system.offline — order_by-Vertraege (planunabhaengig via Spy)
# ---------------------------------------------------------------------------


class TestBundleDocumentTypesOrderBy:
    """Killt ``x__bundle_document_types__mutmut_4/_5``: der ``order_by(
    "sort_order", "name")``-Aufruf verliert je Mutant eines der beiden
    Sortierkriterien. Spy auf ``QuerySet.order_by`` statt beobachteter
    Zeilenreihenfolge (Standardblock Regel 3 — Gleichstand ohne
    vollstaendigen Tiebreak ist implementierungsabhaengig)."""

    def test_order_by_call_carries_sort_order_and_name(self, facility, staff_user):
        calls = _capture_order_by("DocumentType", lambda: _bundle_document_types(staff_user, facility, []))
        assert ("sort_order", "name") in calls


class TestBuildFacilityOfflineBundleDocTypesOrderBy:
    """Killt ``x_build_facility_offline_bundle__mutmut_4/_5``: derselbe
    Defekt wie ``_bundle_document_types``, hier in der personenlosen
    Facility-Bundle-Query. Das 2-Tupel ``("sort_order", "name")`` ist vom
    3-Tupel der internen Tally-Kandidaten-Query (``"sort_order", "name",
    "pk"``) unterscheidbar, der Spy trennt beide sauber."""

    def test_order_by_call_carries_sort_order_and_name(self, facility, lead_user):
        calls = _capture_order_by("DocumentType", lambda: build_facility_offline_bundle(lead_user, facility))
        assert ("sort_order", "name") in calls


# ---------------------------------------------------------------------------
# core.services.system.offline — build_facility_offline_bundle (Roster/Tally)
# ---------------------------------------------------------------------------


class TestBuildFacilityOfflineBundleAssignableUsersWithoutStaffProperty:
    """Killt ``x_build_facility_offline_bundle__mutmut_27``
    (``is_staff_or_above``-Default ``False`` -> ``True``): der Staff-Roster
    (``assignable_users``) wuerde an ein user-Objekt ohne das Property
    leaken (Roster-Exposition It. Docstring)."""

    def test_assignable_users_stays_empty_for_user_object_without_the_property(self, facility, staff_user):
        # Mindestens ein zuweisbares Roster-Mitglied muss existieren, sonst
        # liefert ``_serialize_assignable_users`` ohnehin ``[]`` und die
        # Assertion unterscheidet orig/Mutant nicht (staff_user ist STAFF,
        # damit Teil der zuweisbaren Rollen).
        class _StubNoStaffProperty:
            role = User.Role.ASSISTANT

        bundle = build_facility_offline_bundle(_StubNoStaffProperty(), facility)
        assert bundle["assignable_users"] == []


class TestBuildFacilityOfflineBundleTallyEligibilityReceivesRealUser:
    """Killt ``x_build_facility_offline_bundle__mutmut_39`` (``user`` ->
    ``None`` im Defense-in-Depth-Re-Check ``evaluate_tally_eligibility(row[
    "document_type"], user)``) UND ``x_build_facility_offline_bundle__
    mutmut_41`` (Argument komplett entfernt, faellt auf denselben
    ``user=None``-Funktions-Default zurueck — identischer Effekt, derselbe
    Pin toetet beide). Verhaltens-Injektion per Capture-Spy auf
    ``core.services.events.evaluate_tally_eligibility`` (dem Symbol, das der
    lokale Import in ``build_facility_offline_bundle`` tatsaechlich
    aufloest) statt eines End-to-End-Leck-Szenarios: der Spy delegiert an
    die echte Funktion (``build_tally_summary`` bleibt unangetastet, dessen
    interner Aufruf laeuft ueber die modul-lokale Referenz in ``tally.py``
    und wird vom Patch NICHT beruehrt) und zeichnet nur auf, WELCHER
    ``user``-Wert beim Re-Check ankommt."""

    def test_second_eligibility_check_receives_the_real_user_not_none(self, facility, lead_user, monkeypatch):
        DocumentType.objects.create(
            facility=facility,
            name="Strichlisten-Typ",
            tally_enabled=True,
            entity_type=DocumentType.EntityType.EVENT,
        )
        from core.services.events.tally import evaluate_tally_eligibility as _real_eval

        received_users: list[object] = []

        def _spy(document_type, user=None):
            received_users.append(user)
            return _real_eval(document_type, user)

        monkeypatch.setattr("core.services.events.evaluate_tally_eligibility", _spy)
        build_facility_offline_bundle(lead_user, facility)

        assert received_users, "der Defense-in-Depth-Re-Check wurde nicht aufgerufen"
        assert all(u is lead_user for u in received_users), (
            f"Re-Check muss mit dem echten user aufgerufen werden, erhielt aber {received_users!r}"
        )


class TestBuildFacilityOfflineBundleSkipsOnlyTheIneligibleRow:
    """Killt ``x_build_facility_offline_bundle__mutmut_47`` (``continue`` ->
    ``break`` im Skip-Zweig der ``tally_rows``-Schleife): ``break`` wuerde
    ALLE nachfolgenden, eigentlich eligiblen Zeilen aus dem Tally-Snapshot
    verschwinden lassen, nicht nur die aktuell ineligible."""

    def test_a_later_eligible_row_survives_an_earlier_ineligible_one(self, facility, lead_user, monkeypatch):
        erste = DocumentType.objects.create(
            facility=facility,
            name="Erste",
            tally_enabled=True,
            entity_type=DocumentType.EntityType.EVENT,
            sort_order=0,
        )
        zweite = DocumentType.objects.create(
            facility=facility,
            name="Zweite",
            tally_enabled=True,
            entity_type=DocumentType.EntityType.EVENT,
            sort_order=1,
        )
        from core.services.events.tally import TallyEligibility
        from core.services.events.tally import evaluate_tally_eligibility as _real_eval

        def _spy(document_type, user=None):
            if document_type.pk == erste.pk:
                return TallyEligibility(
                    document_type=document_type,
                    eligible=False,
                    mode=None,
                    required_number_fields=(),
                    reasons=(),
                )
            return _real_eval(document_type, user)

        monkeypatch.setattr("core.services.events.evaluate_tally_eligibility", _spy)
        bundle = build_facility_offline_bundle(lead_user, facility)

        row_ids = {row["document_type_id"] for row in bundle["tally"]["rows"]}
        assert str(erste.pk) not in row_ids
        assert str(zweite.pk) in row_ids, (
            "eine spaetere eligible Zeile darf nicht mitgerissen werden, wenn eine "
            "fruehere Zeile ineligible ist (continue, nicht break)"
        )


class TestBuildFacilityOfflineBundleExpiresAt:
    """Killt ``x_build_facility_offline_bundle__mutmut_85``
    (``generated_at + timedelta(...)`` -> ``generated_at - timedelta(...)``):
    das TTL-Ablaufdatum wuerde in die Vergangenheit statt in die Zukunft
    berechnet."""

    def test_expires_at_is_generated_at_plus_ttl(self, facility, lead_user):
        from datetime import datetime

        bundle = build_facility_offline_bundle(lead_user, facility)
        generated = datetime.fromisoformat(bundle["generated_at"])
        expires = datetime.fromisoformat(bundle["expires_at"])
        delta = (expires - generated).total_seconds()
        assert expires > generated
        assert abs(delta - BUNDLE_TTL_SECONDS) < 1.0


class TestBuildFacilityOfflineBundleTimeFilterShape:
    """Killt ``x_build_facility_offline_bundle__mutmut_74`` (``time_filter``
    immer ``None`` statt ``tally_summary["time_filter"]``) UND
    ``x_build_facility_offline_bundle__mutmut_106/_107/_109/_110`` (Dict-Key
    ``"id"``/``"label"`` umbenannt) UND ``x_build_facility_offline_bundle__
    mutmut_108`` (``str(time_filter.pk)`` -> ``str(None)``): ein
    ganztaegiger ``TimeFilter`` deckt jeden Testlauf-Zeitpunkt ab, der exakte
    Dict-Vergleich toetet alle sechs Mutanten gemeinsam."""

    def test_time_filter_dict_has_exact_id_and_label(self, facility, lead_user):
        time_filter = TimeFilter.objects.create(
            facility=facility,
            label="Ganztagsdienst",
            start_time=dt_time(0, 0),
            end_time=dt_time(23, 59, 59, 999999),
            sort_order=0,
        )
        bundle = build_facility_offline_bundle(lead_user, facility)
        assert bundle["tally"]["time_filter"] == {"id": str(time_filter.pk), "label": "Ganztagsdienst"}


# ---------------------------------------------------------------------------
# core.services.system.settings
# ---------------------------------------------------------------------------


class TestJsonableNonPrimitiveFallback:
    """Killt ``x__jsonable__mutmut_3`` (``return str(value)`` -> ``return
    str(None)``): fuer jeden auditierten Wert, der kein bool/int/float/str/
    None ist, wuerde IMMER die literale Zeichenkette ``"None"`` statt der
    echten Repraesentation in den SETTINGS_CHANGE-Audit-Eintrag geschrieben."""

    def test_non_primitive_value_uses_its_own_string_form(self):
        class _Marker:
            def __str__(self):
                return "Marker(a=1)"

        value = _Marker()
        assert _jsonable(value) == "Marker(a=1)"
        assert _jsonable(value) != "None"


class TestSnapshotMissingAttributeFallback:
    """Killt ``x__snapshot__mutmut_5`` (``getattr(settings_obj, field, None)``
    verliert sein Default-Argument): statt ``None`` zurueckzugeben, wirft
    ``getattr()`` ohne Default ``AttributeError`` fuer ein settings-Objekt,
    dem eines der ``_AUDIT_FIELDS`` fehlt — der defensive Fallback-Vertrag
    der Funktion."""

    def test_missing_field_falls_back_to_none_instead_of_crashing(self):
        class _StubSettingsWithoutAnyAuditField:
            def __getattr__(self, name):
                raise AttributeError(name)

        result = snapshot_settings(_StubSettingsWithoutAnyAuditField())
        assert result == {field: None for field in _AUDIT_FIELDS}

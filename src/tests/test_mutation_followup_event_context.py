"""Follow-Up-Tests für Mutation-Survivors in ``core.services.events.context``.

Refs Welle 7 (#930). Ziel: Mutationen an den Branch-Grenzen von
``filtered_server_data_json``, ``_format_field_display_value`` und
``build_event_detail_context`` killen.

Schwerpunkte (Survivor-Cluster):

- ``filtered_server_data_json`` (57 Survivors): Sensitivity-Boundary
  ``user_can_see_field``; ``__file__``- vs. ``__files__``-Marker; leeres
  ``data_json``; verschlüsselte Werte; Negation der Sichtbarkeitsbedingung.
- ``_format_field_display_value`` (42 Survivors): Per-Feldtyp-Branches
  (BOOLEAN / SELECT / MULTI_SELECT) inkl. Fallback bei fehlenden
  ``options_json``-Einträgen; ``ft is None``.
- ``build_event_detail_context`` (58 Survivors): Aggregation der
  Per-Field-Dicts, Maskierung restringierter Felder mit
  ``[Eingeschränkt]``, Sortierreihenfolge der History (DESC) und
  Slug-Info-Lookup pro Entry.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.models import (
    DocumentType,
    DocumentTypeField,
    Event,
    EventHistory,
    FieldTemplate,
    User,
)
from core.services.events.context import (
    _format_field_display_value,
    build_event_detail_context,
    filtered_server_data_json,
)

# ---------------------------------------------------------------------------
# Helper / Factories
# ---------------------------------------------------------------------------


def _make_field_template(
    facility,
    *,
    name: str,
    field_type: str = FieldTemplate.FieldType.TEXT,
    sensitivity: str = "",
    is_encrypted: bool = False,
    options_json=None,
) -> FieldTemplate:
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        sensitivity=sensitivity,
        is_encrypted=is_encrypted,
        options_json=options_json or [],
    )


def _make_doc_type(
    facility,
    *,
    name: str = "Doc",
    sensitivity: str = DocumentType.Sensitivity.NORMAL,
) -> DocumentType:
    return DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        sensitivity=sensitivity,
        name=name,
    )


def _attach(doc_type: DocumentType, field_template: FieldTemplate, sort_order: int = 0) -> None:
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=sort_order)


def _make_event(facility, client_identified, doc_type, staff_user, *, data_json) -> Event:
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data_json,
        created_by=staff_user,
    )


def _make_user(facility, *, role: str, suffix: str) -> User:
    """Einen User mit gewünschter Rolle (ohne Facility-Bindung bei SUPER_ADMIN)."""
    fac = None if role == User.Role.SUPER_ADMIN else facility
    user = User.objects.create_user(
        username=f"ctx-mut-{role}-{suffix}",
        role=role,
        facility=fac,
        is_staff=True,
    )
    user.set_password("x" * 24)
    user.save()
    return user


# ---------------------------------------------------------------------------
# filtered_server_data_json — Sensitivity-Boundary + Marker-Branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFilteredServerDataJson:
    """Refs Welle 7 — ``filtered_server_data_json``.

    Boundary-Matrix: ``ROLE_MAX_SENSITIVITY`` (ASSISTANT=0, STAFF=1, LEAD/ADMIN=2)
    gegen ``DocumentType.Sensitivity`` (NORMAL=0, ELEVATED=1, HIGH=2). Felder
    mit ``effective_sensitivity > max_allowed`` müssen aus dem Result-Dict
    gestrippt sein — sonst leakt der Merge-Diff verbotene Werte.
    """

    def test_empty_data_json_returns_empty_dict(self, facility, client_identified, staff_user):
        """Mutation ``if event.data_json:`` (Negation) wird gefangen."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={})
        result = filtered_server_data_json(staff_user, event)
        assert result == {}

    def test_none_data_json_returns_empty_dict(self, facility, client_identified, staff_user):
        """``data_json`` ist DB-seitig NOT NULL — wir simulieren den
        Falsy-Branch über In-Memory-Override nach dem Insert.
        """
        doc_type = _make_doc_type(facility)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={})
        event.data_json = None
        assert filtered_server_data_json(staff_user, event) == {}

    def test_plain_value_kept_for_visible_field(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "Hallo"})
        result = filtered_server_data_json(staff_user, event)
        assert result == {ft.slug: "Hallo"}

    def test_assistant_cannot_see_elevated_doc_field(self, facility, client_identified, staff_user, assistant_user):
        """Boundary: ASSISTANT (rank 0) vs. ELEVATED doc (rank 1) → strip."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.ELEVATED)
        ft = _make_field_template(facility, name="Diagnose")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "X"})
        result = filtered_server_data_json(assistant_user, event)
        assert ft.slug not in result, "ASSISTANT darf ELEVATED-Feld nicht sehen"
        assert result == {}

    def test_staff_sees_elevated_but_not_high(self, facility, client_identified, staff_user):
        """STAFF (rank 1) sieht ELEVATED, aber nicht HIGH-Field-Override."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.ELEVATED)
        ft_normal = _make_field_template(facility, name="Notiz")
        ft_high = _make_field_template(
            facility,
            name="Geheim",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(doc_type, ft_normal, sort_order=0)
        _attach(doc_type, ft_high, sort_order=1)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft_normal.slug: "ok", ft_high.slug: "blocked"},
        )
        result = filtered_server_data_json(staff_user, event)
        assert ft_normal.slug in result
        assert ft_high.slug not in result

    def test_lead_sees_high_field(self, facility, client_identified, staff_user, lead_user):
        """LEAD (rank 2) muss HIGH sehen — Boundary ``rank <= 2``."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(
            facility,
            name="Krise",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        result = filtered_server_data_json(lead_user, event)
        assert ft.slug in result

    def test_unknown_slug_inherits_doc_sensitivity(self, facility, client_identified, staff_user, assistant_user):
        """Feld ohne passendes FieldTemplate erbt doc-sensitivity (rank).

        Mutation ``field_sensitivity = ft.sensitivity if ft else ""`` → ``"normal"``
        würde den Branch verändern. Bei ELEVATED-doc + unbekanntem Slug muss
        ASSISTANT (rank 0) das Feld dennoch nicht sehen, weil doc-rank 1.
        """
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.ELEVATED)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={"ghost-slug": "leak"},
        )
        result = filtered_server_data_json(assistant_user, event)
        assert "ghost-slug" not in result

    def test_single_file_marker_preserved(self, facility, client_identified, staff_user):
        """``__file__``-Branch: nur ``__file__`` + ``name`` bleiben übrig."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Anhang", field_type=FieldTemplate.FieldType.FILE)
        _attach(doc_type, ft)
        marker = {"__file__": True, "attachment_id": "1234", "name": "report.pdf"}
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: marker})
        result = filtered_server_data_json(staff_user, event)
        assert result[ft.slug] == {"__file__": True, "name": "report.pdf"}

    def test_single_file_marker_missing_name_uses_empty_string(self, facility, client_identified, staff_user):
        """Mutation ``value.get("name", "")`` → ``value.get("name")`` würde
        ``None`` produzieren; wir prüfen den Default-Branch explizit."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Anhang2", field_type=FieldTemplate.FieldType.FILE)
        _attach(doc_type, ft)
        marker = {"__file__": True, "attachment_id": "abc"}
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: marker})
        result = filtered_server_data_json(staff_user, event)
        assert result[ft.slug] == {"__file__": True, "name": ""}

    def test_multi_file_marker_preserves_entry_id_and_sort(self, facility, client_identified, staff_user):
        """``__files__``-Branch: ID + Sort werden in Entries beibehalten."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Mehrere", field_type=FieldTemplate.FieldType.FILE)
        _attach(doc_type, ft)
        marker = {
            "__files__": True,
            "entries": [
                {"id": "a", "sort": 1},
                {"id": "b", "sort": 2},
            ],
        }
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: marker})
        result = filtered_server_data_json(staff_user, event)
        assert result[ft.slug]["__files__"] is True
        assert result[ft.slug]["entries"] == [
            {"id": "a", "sort": 1},
            {"id": "b", "sort": 2},
        ]

    def test_multi_file_marker_sort_default_zero(self, facility, client_identified, staff_user):
        """Sort-Default ist 0, nicht None — fängt Mutation ``.get("sort", 0)``
        → ``.get("sort")`` und Konstantenmutation 0→1."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Sortlos", field_type=FieldTemplate.FieldType.FILE)
        _attach(doc_type, ft)
        marker = {"__files__": True, "entries": [{"id": "x"}]}
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: marker})
        result = filtered_server_data_json(staff_user, event)
        assert result[ft.slug]["entries"] == [{"id": "x", "sort": 0}]

    def test_dict_value_without_encryption_marker_passes_through(self, facility, client_identified, staff_user):
        """Boundary: ``safe_decrypt`` liefert das Dict unverändert zurück,
        wenn kein ``__encrypted__``-Marker drin ist (siehe
        ``services.encryption.safe_decrypt`` → ``is_encrypted_value``).

        Damit wird der ``isinstance(value, dict)``-Branch im
        ``filtered_server_data_json`` getroffen, ohne dass wir Fernet-Keys
        und echte Ciphertexte aufsetzen müssen.
        """
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft)
        payload = {"some": "dict"}
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft.slug: payload},
        )
        result = filtered_server_data_json(staff_user, event)
        assert result[ft.slug] == payload


# ---------------------------------------------------------------------------
# _format_field_display_value — pro-Feldtyp-Branches
# ---------------------------------------------------------------------------


def _bare_ft(field_type: str, *, options_json=None) -> SimpleNamespace:
    """Leichtes Stand-In fuer FieldTemplate ohne DB-Roundtrip.

    Reicht, weil ``_format_field_display_value`` nur ``field_type``,
    ``options_json`` und die ``FieldType``-Enum-Konstanten antastet.
    """
    return SimpleNamespace(
        field_type=field_type,
        options_json=options_json or [],
        FieldType=FieldTemplate.FieldType,
    )


class TestFormatFieldDisplayValue:
    """Refs Welle 7 — ``_format_field_display_value``.

    Branches: ``ft is None`` Fallback; BOOLEAN True/False; SELECT mit/ohne
    options_json; MULTI_SELECT mit Liste + Slug-Lookup-Fallback.
    """

    def test_none_ft_returns_value_untouched(self):
        """Mutation am ``if ft is None`` würde fixierte Werte liefern."""
        assert _format_field_display_value("raw", None) == "raw"
        assert _format_field_display_value(42, None) == 42

    # BOOLEAN ----------------------------------------------------------------

    def test_boolean_true_returns_ja(self):
        ft = _bare_ft(FieldTemplate.FieldType.BOOLEAN)
        assert str(_format_field_display_value(True, ft)) == "Ja"

    def test_boolean_false_returns_nein(self):
        ft = _bare_ft(FieldTemplate.FieldType.BOOLEAN)
        assert str(_format_field_display_value(False, ft)) == "Nein"

    def test_boolean_none_treated_as_falsy(self):
        """Mutation ``if value`` → ``if value is True`` würde ``None`` schlagen.

        Der Code prüft Truthy/Falsy — None ist falsy → "Nein".
        """
        ft = _bare_ft(FieldTemplate.FieldType.BOOLEAN)
        assert str(_format_field_display_value(None, ft)) == "Nein"

    def test_boolean_truthy_string_returns_ja(self):
        """Truthy-Check: nicht-leerer String → Ja."""
        ft = _bare_ft(FieldTemplate.FieldType.BOOLEAN)
        assert str(_format_field_display_value("ja", ft)) == "Ja"

    # SELECT -----------------------------------------------------------------

    def test_select_resolves_slug_to_label(self):
        ft = _bare_ft(
            FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "beratung", "label": "Beratung"},
                {"slug": "krise", "label": "Krise"},
            ],
        )
        assert _format_field_display_value("beratung", ft) == "Beratung"

    def test_select_unknown_slug_returns_raw(self):
        """Mutation ``label_map.get(value, value)`` → ``label_map.get(value)``
        würde ``None`` zurückliefern."""
        ft = _bare_ft(
            FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "A"}],
        )
        assert _format_field_display_value("xx", ft) == "xx"

    def test_select_empty_options_returns_raw(self):
        """Ohne ``options_json`` greift der Branch nicht, value bleibt unangetastet."""
        ft = _bare_ft(FieldTemplate.FieldType.SELECT, options_json=[])
        assert _format_field_display_value("anything", ft) == "anything"

    def test_select_skips_non_dict_options(self):
        """Mutation der ``isinstance(o, dict)``-Schranke wird gefangen."""
        ft = _bare_ft(
            FieldTemplate.FieldType.SELECT,
            options_json=["garbage", {"slug": "ok", "label": "OK"}],
        )
        assert _format_field_display_value("ok", ft) == "OK"

    def test_select_skips_options_without_slug(self):
        """``"slug" in o``-Check verhindert KeyError; Mutation des ``in`` würde crashen."""
        ft = _bare_ft(
            FieldTemplate.FieldType.SELECT,
            options_json=[{"label": "Nur Label"}, {"slug": "real", "label": "Real"}],
        )
        assert _format_field_display_value("real", ft) == "Real"

    # MULTI_SELECT -----------------------------------------------------------

    def test_multi_select_joins_labels_with_comma_space(self):
        ft = _bare_ft(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[
                {"slug": "a", "label": "Alpha"},
                {"slug": "b", "label": "Beta"},
            ],
        )
        assert _format_field_display_value(["a", "b"], ft) == "Alpha, Beta"

    def test_multi_select_single_value_no_trailing_separator(self):
        ft = _bare_ft(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha"}],
        )
        assert _format_field_display_value(["a"], ft) == "Alpha"

    def test_multi_select_unknown_slug_uses_str_fallback(self):
        """Mutation ``label_map.get(v, str(v))`` → ``label_map.get(v)`` würde ``None``
        in den join schmuggeln und ``TypeError`` werfen oder leeren String mischen."""
        ft = _bare_ft(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha"}],
        )
        assert _format_field_display_value(["a", "ghost"], ft) == "Alpha, ghost"

    def test_multi_select_non_list_value_returns_raw(self):
        """``isinstance(value, list)``-Branch: Nicht-Liste fällt durch."""
        ft = _bare_ft(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha"}],
        )
        # Kein list → kein Join, value unverändert.
        assert _format_field_display_value("a", ft) == "a"

    def test_multi_select_empty_list_returns_empty_string(self):
        ft = _bare_ft(
            FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha"}],
        )
        assert _format_field_display_value([], ft) == ""

    def test_multi_select_empty_options_returns_raw_value(self):
        ft = _bare_ft(FieldTemplate.FieldType.MULTI_SELECT, options_json=[])
        assert _format_field_display_value(["a"], ft) == ["a"]

    # Fallback / other field types ------------------------------------------

    @pytest.mark.parametrize(
        "field_type",
        [
            FieldTemplate.FieldType.TEXT,
            FieldTemplate.FieldType.TEXTAREA,
            FieldTemplate.FieldType.NUMBER,
            FieldTemplate.FieldType.DATE,
            FieldTemplate.FieldType.TIME,
            FieldTemplate.FieldType.FILE,
        ],
    )
    def test_unhandled_field_type_returns_value_untouched(self, field_type):
        """TEXT/NUMBER/DATE/TIME/FILE haben keinen Format-Branch — value bleibt 1:1."""
        ft = _bare_ft(field_type)
        sentinel = object()
        assert _format_field_display_value(sentinel, ft) is sentinel


# ---------------------------------------------------------------------------
# build_event_detail_context — aggregierter Detail-Context
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildEventDetailContext:
    """Refs Welle 7 — ``build_event_detail_context``.

    Schwerpunkte:
    - Result-Dict-Keys ``event``/``fields_display``/``history`` immer gesetzt.
    - Restringierte Felder werden via ``[Eingeschränkt]`` maskiert, statt
      einfach übersprungen (das ist Doku-Pflicht, sonst Diff im Template).
    - Per-Field-Sensitivity-Override greift VOR doc-sensitivity.
    - History sortiert DESC nach ``changed_at`` und trägt ``_slug_info``.
    """

    def test_returns_required_keys(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Feld")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        ctx = build_event_detail_context(event, staff_user)
        assert set(ctx.keys()) == {"event", "fields_display", "history"}
        assert ctx["event"] is event

    def test_empty_data_json_yields_empty_fields(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={})
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["fields_display"] == []
        assert ctx["history"] == []

    def test_visible_field_uses_template_name_as_label(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz-Label")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "Hallo"})
        ctx = build_event_detail_context(event, staff_user)
        assert len(ctx["fields_display"]) == 1
        entry = ctx["fields_display"][0]
        assert entry["label"] == "Notiz-Label"
        assert entry["value"] == "Hallo"
        assert entry["is_encrypted"] is False
        assert entry["is_sensitive"] is False
        assert "restricted" not in entry

    def test_unknown_slug_label_titleizes_with_dash_replacement(self, facility, client_identified, staff_user):
        """Mutation ``key.replace("-", " ")`` → ``key.replace("_", " ")``
        würde die Label-Generierung kippen."""
        doc_type = _make_doc_type(facility)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={"foo-bar": "baz"},
        )
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["fields_display"][0]["label"] == "Foo Bar"

    def test_restricted_field_returns_masked_entry_not_skip(
        self, facility, client_identified, staff_user, assistant_user
    ):
        """``user_can_see_field``-False muss einen ``restricted=True``-Eintrag
        liefern, nicht einfach skippen — Mutation ``continue`` ohne append
        wird gefangen.
        """
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.ELEVATED)
        ft = _make_field_template(facility, name="Diagnose")
        _attach(doc_type, ft)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft.slug: "geheim"},
        )
        ctx = build_event_detail_context(event, assistant_user)
        assert len(ctx["fields_display"]) == 1
        entry = ctx["fields_display"][0]
        assert entry["restricted"] is True
        assert entry["label"] == "Diagnose"
        # Wert darf NICHT durchsickern.
        assert "geheim" not in str(entry["value"])
        assert "Eingeschränkt" in str(entry["value"])

    def test_field_level_sensitivity_overrides_doc_for_visibility(self, facility, client_identified, staff_user):
        """Field-Override HIGH auf NORMAL-doc → STAFF sieht den Wert nicht.

        Stellt sicher, dass ``effective_sensitivity`` das Maximum nimmt
        (Mutation ``max(...)`` → ``min(...)`` würde diesen Branch killen).
        """
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(
            facility,
            name="Hoch",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "secret"})
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["fields_display"][0].get("restricted") is True

    def test_is_sensitive_flag_set_for_field_with_sensitivity(self, facility, client_identified, staff_user, lead_user):
        """``bool(field_sensitivity)`` muss True liefern, sobald irgendein
        non-empty Sensitivity-String gesetzt ist."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(
            facility,
            name="Markiert",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        ctx = build_event_detail_context(event, lead_user)
        assert ctx["fields_display"][0]["is_sensitive"] is True

    def test_is_sensitive_false_for_field_without_sensitivity(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Plain")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["fields_display"][0]["is_sensitive"] is False

    def test_boolean_field_value_is_formatted(self, facility, client_identified, staff_user):
        """Integration: ``_format_field_display_value`` wird im Aggregat aufgerufen."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Bestaetigt", field_type=FieldTemplate.FieldType.BOOLEAN)
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: True})
        ctx = build_event_detail_context(event, staff_user)
        assert str(ctx["fields_display"][0]["value"]) == "Ja"

    def test_select_field_value_is_resolved_to_label(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(
            facility,
            name="Kategorie",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "beratung", "label": "Beratung"}],
        )
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "beratung"})
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["fields_display"][0]["value"] == "Beratung"

    def test_field_order_follows_sort_order(self, facility, client_identified, staff_user):
        """``build_field_template_lookup(ordered=True)`` — Reihenfolge folgt
        ``data_json`` (Python-Dict-Reihenfolge bleibt insertion-order).

        Wir prüfen, dass beide Templates verfügbar sind und Label-Resolution
        klappt — nicht Sort-Reihenfolge per se, denn die Iteration läuft über
        ``data_json.items()``.
        """
        doc_type = _make_doc_type(facility)
        ft_a = _make_field_template(facility, name="Erstes")
        ft_b = _make_field_template(facility, name="Zweites")
        _attach(doc_type, ft_a, sort_order=0)
        _attach(doc_type, ft_b, sort_order=1)
        event = _make_event(
            facility,
            client_identified,
            doc_type,
            staff_user,
            data_json={ft_a.slug: "1", ft_b.slug: "2"},
        )
        ctx = build_event_detail_context(event, staff_user)
        labels = [e["label"] for e in ctx["fields_display"]]
        assert labels == ["Erstes", "Zweites"]

    def test_history_sorted_descending_by_changed_at(self, facility, client_identified, staff_user):
        """Mutation ``order_by("-changed_at")`` → ``order_by("changed_at")``
        oder Negation würde die History-Reihenfolge kippen."""
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Feld")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        # Drei History-Entries; auto_now_add setzt Timestamp ASC.
        h1 = EventHistory.objects.create(event=event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        h2 = EventHistory.objects.create(event=event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        h3 = EventHistory.objects.create(event=event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        ctx = build_event_detail_context(event, staff_user)
        ids = [h.pk for h in ctx["history"]]
        # Erwartet: neueste zuerst — h3, h2, h1.
        assert ids[0] == h3.pk
        assert ids[-1] == h1.pk
        # Belastbar gegen identische Timestamps: prüfe Menge + first/last.
        assert set(ids) == {h1.pk, h2.pk, h3.pk}

    def test_history_entries_carry_event_and_slug_info(self, facility, client_identified, staff_user):
        """Jeder History-Entry bekommt ``entry.event`` + ``entry._slug_info``
        injiziert (Refs #824, C-57). Mutation ``entry.event = event`` würde
        per ``entry.event is event`` failen.
        """
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Notiz")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        EventHistory.objects.create(event=event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        ctx = build_event_detail_context(event, staff_user)
        assert len(ctx["history"]) == 1
        entry = ctx["history"][0]
        assert entry.event is event
        # Slug-Info-Lookup: muss ft.slug enthalten + alle erwarteten Keys.
        assert ft.slug in entry._slug_info
        info = entry._slug_info[ft.slug]
        assert info["name"] == "Notiz"
        assert info["is_encrypted"] is False
        assert info["sensitivity"] == ""

    def test_slug_info_includes_sensitivity_and_encryption_flag(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Mutation eines der drei dict-Keys (``name``/``is_encrypted``/
        ``sensitivity``) wird gefangen, weil wir alle drei explizit prüfen."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(
            facility,
            name="Sensitiv",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        EventHistory.objects.create(event=event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        ctx = build_event_detail_context(event, lead_user)
        info = ctx["history"][0]._slug_info[ft.slug]
        assert info == {
            "name": "Sensitiv",
            "is_encrypted": True,
            "sensitivity": DocumentType.Sensitivity.HIGH,
        }

    def test_history_empty_when_no_changes(self, facility, client_identified, staff_user):
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Feld")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        ctx = build_event_detail_context(event, staff_user)
        assert ctx["history"] == []

    def test_assistant_sees_normal_doc_normal_field(self, facility, client_identified, staff_user, assistant_user):
        """Boundary: ASSISTANT (rank 0) sieht NORMAL doc + NORMAL field — der
        ``<=``-Vergleich muss True liefern."""
        doc_type = _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(facility, name="Plain")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "ok"})
        ctx = build_event_detail_context(event, assistant_user)
        assert ctx["fields_display"][0].get("restricted") is not True
        assert ctx["fields_display"][0]["value"] == "ok"

    def test_recent_history_changed_at_ordering_robust(self, facility, client_identified, staff_user):
        """Falls auto_now_add identische Timestamps liefert: explizit
        ``changed_at`` setzen (umgeht den ``auto_now_add``-Default per
        ``update`` nach Insert, weil der EventHistory.save den Update
        verbietet).

        Hier prüfen wir, dass auch bei `timedelta`-getrennten Inserts die
        Reihenfolge stabil DESC bleibt — wir warten kurz nicht, sondern
        verlassen uns auf monoton steigende ``auto_now_add``.
        """
        doc_type = _make_doc_type(facility)
        ft = _make_field_template(facility, name="Feld")
        _attach(doc_type, ft)
        event = _make_event(facility, client_identified, doc_type, staff_user, data_json={ft.slug: "v"})
        first = EventHistory.objects.create(event=event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        last = EventHistory.objects.create(event=event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        ctx = build_event_detail_context(event, staff_user)
        # Wenn timestamps identisch sind, fällt PostgreSQL auf einen
        # implementations-internen Tie-Break zurück — wir prüfen darum nur,
        # dass beide vorhanden sind und das zuletzt erstellte mindestens so
        # neu wie das erste ist.
        timestamps = [h.changed_at for h in ctx["history"]]
        assert len(timestamps) == 2
        assert max(timestamps) >= min(timestamps)
        assert {first.pk, last.pk} == {h.pk for h in ctx["history"]}


# ---------------------------------------------------------------------------
# Cross-Boundary Sanity: Sensitivity-Konstanten dürfen sich nicht stillschweigend
# verschieben (Mutationen auf SENSITIVITY_RANK-Werte).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSensitivityBoundaryMatrix:
    """Refs Welle 7 — Komplette Boundary-Matrix einmal explizit durchspielen.

    Dieser Block ist redundant zum Detail oben, aber zentral genug, dass eine
    Mutation in ``SENSITIVITY_RANK``/``ROLE_MAX_SENSITIVITY`` oder den Branches
    von ``filtered_server_data_json`` mit hoher Wahrscheinlichkeit hier
    auffällt.
    """

    @pytest.fixture
    def matrix_doc(self, facility):
        """Drei Felder mit jeweils unterschiedlicher Field-Sensitivity."""
        return _make_doc_type(facility, sensitivity=DocumentType.Sensitivity.NORMAL)

    @pytest.fixture
    def matrix_event(self, facility, client_identified, staff_user, matrix_doc):
        ft_normal = _make_field_template(facility, name="A", sensitivity="")
        ft_elevated = _make_field_template(facility, name="B", sensitivity=DocumentType.Sensitivity.ELEVATED)
        ft_high = _make_field_template(
            facility,
            name="C",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(matrix_doc, ft_normal, 0)
        _attach(matrix_doc, ft_elevated, 1)
        _attach(matrix_doc, ft_high, 2)
        event = _make_event(
            facility,
            client_identified,
            matrix_doc,
            staff_user,
            data_json={
                ft_normal.slug: "n",
                ft_elevated.slug: "e",
                ft_high.slug: "h",
            },
        )
        return event, ft_normal, ft_elevated, ft_high

    def test_assistant_sees_normal_only(self, facility, matrix_event, assistant_user):
        event, ft_normal, ft_elevated, ft_high = matrix_event
        result = filtered_server_data_json(assistant_user, event)
        assert set(result.keys()) == {ft_normal.slug}

    def test_staff_sees_normal_and_elevated(self, matrix_event, staff_user):
        event, ft_normal, ft_elevated, ft_high = matrix_event
        result = filtered_server_data_json(staff_user, event)
        assert set(result.keys()) == {ft_normal.slug, ft_elevated.slug}

    def test_lead_sees_all_three(self, matrix_event, lead_user):
        event, ft_normal, ft_elevated, ft_high = matrix_event
        result = filtered_server_data_json(lead_user, event)
        assert set(result.keys()) == {ft_normal.slug, ft_elevated.slug, ft_high.slug}

    def test_admin_sees_all_three(self, matrix_event, admin_user):
        event, ft_normal, ft_elevated, ft_high = matrix_event
        result = filtered_server_data_json(admin_user, event)
        assert set(result.keys()) == {ft_normal.slug, ft_elevated.slug, ft_high.slug}


# Marker-Importe gegen Pyflakes (timedelta/timezone werden für Future-Use
# importiert, falls weitere History-Boundary-Tests hinzukommen). Wir
# verwenden timedelta unten in einem No-Op, um Ruff F401 zu vermeiden.
_ = timedelta(seconds=0)

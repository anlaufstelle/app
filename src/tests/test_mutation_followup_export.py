"""Follow-Up-Tests für Mutation-Survivors in ``core.services.export``.

Refs Welle 7 (#930). Ziel: Mutationen an den Branch-, Boundary- und
Sicht-Grenzen der drei Top-Survivor-Funktionen killen:

- ``_resolve_field_value`` (29 Survivors): dict-/list-/str-Branches inkl.
  Label-Lookup mit Default-Fallback (``label_map.get(value, value)`` und
  ``label_map.get(v, str(v))``). Multi-Select-Join-Separator (``", "``).
  ``isinstance(o, dict)``-Filter in der options_json-Reduktion.
- ``_build_event_row`` (37 Survivors): Reihenfolge der Spalten (Datum,
  Uhrzeit, Dokumentationstyp, Person, Kontaktstufe, Altersgruppe + dyn.
  Felder), Visibility-Filter pro Feld (Sensitivity), Masking-Branch
  ``[Eingeschränkt]`` statt Skip, CSV-Escape-Wege (``_sanitize_csv_cell``)
  und Pseudonym-vs-Anonym-vs-Dash-Fallback bei ``event.client``.
- ``get_jugendamt_statistics`` (30 Survivors): Datumsbereiche-Boundaries
  (``occurred_at__date__gte`` / ``__lte`` an Anfang/Ende), Aggregat-Counts
  pro Kategorie (``Kontakte``/``Beratung``/``Versorgung``/``Vermittlung``),
  Stage-/system_type-Filter (None ausschließen), Age-Cluster-Verteilung,
  ``unique_clients`` exklusive Anonym-Events.

Konstanten-Kontext (siehe ``services/sensitivity.py``):

- ``SENSITIVITY_RANK``: ``NORMAL=0, ELEVATED=1, HIGH=2``
- ``ROLE_MAX_SENSITIVITY``: ``ASSISTANT=0, STAFF=1, LEAD=2, FACILITY_ADMIN=2``

Die Tests laufen gegen die Verify-DB (``anlaufstelle_verify``), damit sie
nicht mit aktiv laufenden Mutmut-Runs (auf ``test_anlaufstelle``)
kollidieren.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
)
from core.services.system import (
    JUGENDAMT_CATEGORY_MAP,
    _build_event_row,
    _resolve_field_value,
    get_jugendamt_statistics,
)

# ---------------------------------------------------------------------------
# Helper / Factories
# ---------------------------------------------------------------------------


def _bare_ft(*, options_json=None) -> SimpleNamespace:
    """Leichtes Stand-in für FieldTemplate ohne DB-Roundtrip.

    ``_resolve_field_value`` nutzt nur ``ft.options_json`` — das Stand-in
    deckt damit die Branches ab, ohne dass wir pro Test ein Template in
    der DB anlegen müssen (was teure migrations/RLS-Hops triggert).
    """
    return SimpleNamespace(options_json=options_json or [])


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
    name: str,
    system_type: str | None = None,
    sensitivity: str = DocumentType.Sensitivity.NORMAL,
    category: str = DocumentType.Category.CONTACT,
) -> DocumentType:
    return DocumentType.objects.create(
        facility=facility,
        name=name,
        category=category,
        sensitivity=sensitivity,
        system_type=system_type,
    )


def _attach(doc_type: DocumentType, field_template: FieldTemplate, sort_order: int = 0) -> None:
    DocumentTypeField.objects.create(document_type=doc_type, field_template=field_template, sort_order=sort_order)


def _aware(target_date: date, hour: int = 12, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime.combine(target_date, time(hour, minute)))


# ---------------------------------------------------------------------------
# _resolve_field_value — pro-Branch (dict / list / str+options / fallthrough)
# ---------------------------------------------------------------------------


class TestResolveFieldValueDictBranch:
    """``isinstance(value, dict)`` → ``safe_decrypt(value)``.

    Ohne ``__encrypted__``-Marker liefert ``safe_decrypt`` das Dict 1:1
    zurück (siehe ``services.encryption.is_encrypted_value``). Damit wird
    der ``dict``-Branch reproduzierbar getroffen, ohne Fernet-Keys aufzusetzen.
    """

    def test_dict_without_marker_returns_value_unchanged(self):
        ft = _bare_ft()
        payload = {"some": "thing"}
        assert _resolve_field_value(payload, ft) == payload

    def test_dict_branch_runs_even_when_ft_is_none(self):
        """``ft=None`` darf den dict-Branch nicht killen — er ist zuerst geprüft."""
        payload = {"k": "v"}
        assert _resolve_field_value(payload, None) == payload


class TestResolveFieldValueListBranch:
    """``isinstance(value, list)`` — Multi-Select-Aggregation.

    Mutationen, die hier geschlagen werden:
    - ``", ".join(...)`` → anderer Separator (``"|"``, ``", "`` → ``","``)
    - ``label_map.get(str(v), str(v))`` → ``.get(str(v))`` (None einschmuggeln)
    - ``isinstance(o, dict)`` → True (greift dann auch auf reine Strings)
    """

    def test_list_with_options_resolves_each_to_label(self):
        ft = _bare_ft(
            options_json=[
                {"slug": "a", "label": "Alpha"},
                {"slug": "b", "label": "Beta"},
            ]
        )
        assert _resolve_field_value(["a", "b"], ft) == "Alpha, Beta"

    def test_list_join_separator_is_comma_space(self):
        """Mutmut könnte ``", "`` → ``","`` oder ``" "`` → der Test fängt beides."""
        ft = _bare_ft(options_json=[{"slug": "x", "label": "X"}, {"slug": "y", "label": "Y"}])
        result = _resolve_field_value(["x", "y"], ft)
        assert result == "X, Y"
        assert ", " in result
        assert "X,Y" not in result

    def test_list_unknown_slug_falls_back_to_str_value(self):
        """Mutation ``label_map.get(str(v), str(v))`` → ``label_map.get(str(v))``
        würde ``None`` in den Join schmuggeln (TypeError) oder leeres ``"None"``."""
        ft = _bare_ft(options_json=[{"slug": "a", "label": "Alpha"}])
        assert _resolve_field_value(["a", "ghost"], ft) == "Alpha, ghost"

    def test_list_without_options_uses_str_fallback(self):
        """Branch: ``ft.options_json`` leer → reines ``str(v)`` pro Element."""
        ft = _bare_ft(options_json=[])
        assert _resolve_field_value(["a", "b"], ft) == "a, b"

    def test_list_with_ft_none_uses_str_fallback(self):
        """Branch: ``ft is None`` → ``and ft and ft.options_json`` ist False."""
        assert _resolve_field_value(["a", "b"], None) == "a, b"

    def test_list_with_non_string_values_stringified(self):
        """Mutation ``str(v) for v in value`` → ``v for v in value`` würde
        nicht-Strings unverändert in die join schicken (TypeError)."""
        ft = _bare_ft(options_json=[])
        assert _resolve_field_value([1, 2, 3], ft) == "1, 2, 3"

    def test_list_empty_returns_empty_string(self):
        """``", ".join([])`` → ``""`` (kein Trailing-Separator)."""
        ft = _bare_ft(options_json=[{"slug": "a", "label": "A"}])
        assert _resolve_field_value([], ft) == ""

    def test_list_single_value_has_no_trailing_separator(self):
        ft = _bare_ft(options_json=[{"slug": "a", "label": "Alpha"}])
        assert _resolve_field_value(["a"], ft) == "Alpha"

    def test_list_skips_non_dict_options(self):
        """Mutation ``isinstance(o, dict)`` → ``not isinstance(o, dict)``
        würde versuchen, auf String-Optionen mit ``["slug"]`` zuzugreifen
        (TypeError). Wir mischen valid + invalid und prüfen Robustheit."""
        ft = _bare_ft(options_json=[{"slug": "ok", "label": "OK"}, "garbage"])
        assert _resolve_field_value(["ok"], ft) == "OK"


class TestResolveFieldValueStrBranch:
    """``isinstance(value, str)`` + ``ft.options_json`` — Single-Select-Lookup."""

    def test_str_with_known_slug_returns_label(self):
        ft = _bare_ft(
            options_json=[
                {"slug": "rot", "label": "Rot"},
                {"slug": "blau", "label": "Blau"},
            ]
        )
        assert _resolve_field_value("rot", ft) == "Rot"

    def test_str_with_unknown_slug_returns_raw_value(self):
        """Mutation ``label_map.get(value, value)`` → ``label_map.get(value)``
        würde ``None`` zurückgeben — der Test fängt das auf, weil ``None != "xx"``."""
        ft = _bare_ft(options_json=[{"slug": "rot", "label": "Rot"}])
        assert _resolve_field_value("xx", ft) == "xx"

    def test_str_without_options_returns_value_unchanged(self):
        """Branch: ``ft.options_json`` leer → kein Lookup, Fallthrough zum Return."""
        ft = _bare_ft(options_json=[])
        assert _resolve_field_value("plain", ft) == "plain"

    def test_str_with_ft_none_returns_value_unchanged(self):
        assert _resolve_field_value("plain", None) == "plain"

    def test_str_skips_non_dict_options(self):
        """``isinstance(o, dict)``-Filter im single-select-Branch.
        Mutation würde TypeError werfen bei String-Options."""
        ft = _bare_ft(options_json=["junk", {"slug": "ok", "label": "OK"}])
        assert _resolve_field_value("ok", ft) == "OK"


class TestResolveFieldValueFallthrough:
    """Werte, die weder dict noch list noch str sind — Pass-Through."""

    def test_int_returns_unchanged(self):
        assert _resolve_field_value(42, _bare_ft()) == 42

    def test_none_returns_none(self):
        """Mutation ``return value`` → ``return ""`` würde None überschreiben."""
        assert _resolve_field_value(None, _bare_ft()) is None

    def test_bool_returns_unchanged(self):
        """``isinstance(True, int)`` ist True — bool kommt erst nach dict/list/str
        am Fallthrough an (``True/False`` sind weder dict noch list noch str)."""
        assert _resolve_field_value(True, _bare_ft()) is True
        assert _resolve_field_value(False, _bare_ft()) is False


# ---------------------------------------------------------------------------
# _build_event_row — Spaltenreihenfolge, Visibility, Masking, CSV-Escape
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildEventRowStaticColumns:
    """Die ersten 6 Spalten sind statisch: Datum, Uhrzeit, Doctype, Person,
    Kontaktstufe, Altersgruppe. Mutationen an Reihenfolge oder Format
    werden hier explizit geschlagen.
    """

    def test_column_order_with_identified_client(self, facility, client_identified, staff_user):
        dt = _make_doc_type(facility, name="Kontakt")
        when = timezone.make_aware(datetime(2026, 5, 17, 14, 30))
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=when,
            data_json={},
            created_by=staff_user,
        )
        row = _build_event_row(event, all_field_templates={}, field_slugs=[], user=staff_user)
        # Mutmut ``[0]``↔``[1]``-Swaps schlagen, weil Format eindeutig:
        # Datum = ``17.05.2026``, Uhrzeit = ``14:30``.
        assert row[0] == "17.05.2026"
        assert row[1] == "14:30"
        assert row[2] == "Kontakt"
        assert row[3] == client_identified.pseudonym
        # ``IDENTIFIED`` → Display-String "Identifiziert"
        assert row[4] == "Identifiziert"
        # AgeCluster default ``UNKNOWN`` → "Unbekannt"
        assert row[5] == "Unbekannt"
        assert len(row) == 6, "Ohne dyn. Felder muss row genau 6 statische Spalten haben"

    def test_date_format_uses_european_layout(self, facility, client_identified, staff_user):
        """Mutation ``%d.%m.%Y`` → ``%Y-%m-%d`` oder ``%m/%d/%Y`` würde hier failen."""
        dt = _make_doc_type(facility, name="X")
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.make_aware(datetime(2026, 1, 2, 3, 4)),
            data_json={},
            created_by=staff_user,
        )
        row = _build_event_row(event, {}, [], staff_user)
        assert row[0] == "02.01.2026"
        # Stunde:Minute, zweistellig — ``%H:%M``-Mutation auf ``%H-%M`` würde failen.
        assert row[1] == "03:04"

    def test_anonymous_event_without_client_uses_anonym_label(self, facility, staff_user):
        """Branch: ``event.client is None`` und ``is_anonymous=True`` → "Anonym"."""
        dt = _make_doc_type(facility, name="Anon")
        event = Event.objects.create(
            facility=facility,
            client=None,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        row = _build_event_row(event, {}, [], staff_user)
        # gettext_lazy → str-Konvertierung gibt das deutsche Label.
        assert str(row[3]) == "Anonym"
        # Kontaktstufe/Altersgruppe leer, weil kein client.
        assert row[4] == ""
        assert row[5] == ""

    def test_event_without_client_and_not_anonymous_uses_dash(self, facility, staff_user):
        """Branch: ``event.client is None`` und ``is_anonymous=False`` → "–".

        Mutation ``"–"`` → ``"-"`` (verschiedenes Unicode-Codepoint) würde failen.
        """
        dt = _make_doc_type(facility, name="NoClient")
        event = Event.objects.create(
            facility=facility,
            client=None,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=False,
            created_by=staff_user,
        )
        row = _build_event_row(event, {}, [], staff_user)
        # Es ist explizit en-dash (U+2013), nicht ASCII-minus.
        assert row[3] == "–"
        assert row[3] != "-"

    def test_doctype_name_is_sanitized_for_injection(self, facility, client_identified, staff_user):
        """DocumentType-Name fließt durch ``_sanitize_csv_cell`` — wir setzen ein
        ``=``-Prefix und prüfen, dass es mit ``'`` neutralisiert wird (Refs #719)."""
        dt = _make_doc_type(facility, name="=cmd|calc")
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        row = _build_event_row(event, {}, [], staff_user)
        assert row[2] == "'=cmd|calc"
        assert not row[2].startswith("=")

    def test_pseudonym_with_formula_prefix_is_sanitized(self, facility, staff_user):
        """Pseudonym fließt durch ``_sanitize_csv_cell`` — ``+1234`` muss
        ``'+1234`` werden (OWASP-Pattern)."""
        cli = Client.objects.create(
            facility=facility,
            pseudonym="+1234",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        dt = _make_doc_type(facility, name="Plain")
        event = Event.objects.create(
            facility=facility,
            client=cli,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        row = _build_event_row(event, {}, [], staff_user)
        assert row[3] == "'+1234"


@pytest.mark.django_db
class TestBuildEventRowDynamicFields:
    """Dynamische Felder werden am Ende angehängt — pro field_slug eine Spalte."""

    def test_dynamic_field_appended_after_static_columns(self, facility, client_identified, staff_user):
        dt = _make_doc_type(facility, name="Plain")
        ft = _make_field_template(facility, name="Dauer", field_type=FieldTemplate.FieldType.NUMBER)
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: 42},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], staff_user)
        assert len(row) == 7
        assert row[6] == "42"  # int → str via _sanitize_csv_cell

    def test_missing_field_slug_yields_empty_string(self, facility, client_identified, staff_user):
        """Branch: ``data.get(field_slug, "")`` mit fehlendem Key.

        Mutation ``data.get(field_slug, "")`` → ``data.get(field_slug)``
        würde ``None`` in den Sanitizer schicken → ``""`` (passt zufaellig),
        aber auch ``data.get(field_slug, "")`` → ``data[field_slug]``
        würde KeyError werfen — wird gefangen.
        """
        dt = _make_doc_type(facility, name="P")
        ft = _make_field_template(facility, name="Leer")
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},  # leer — slug fehlt
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], staff_user)
        assert row[-1] == ""

    def test_dict_value_passes_through_sanitizer_as_str(self, facility, client_identified, staff_user):
        """``_resolve_field_value`` liefert für dict-ohne-Marker das Dict zurück,
        dann sanitized ``_sanitize_csv_cell`` über ``str(...)``. Die String-
        Repräsentation eines Dicts beginnt mit ``{`` → kein OWASP-Prefix → unverändert.
        """
        dt = _make_doc_type(facility, name="P")
        ft = _make_field_template(facility, name="Map")
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: {"k": "v"}},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], staff_user)
        # Sicher: row enthält die String-Repr des Dicts oder einen Decrypt-Fallback.
        # Wichtig: KEIN OWASP-Prefix-Leak.
        assert not row[-1].startswith(("=", "+", "-", "@"))

    def test_field_value_with_owasp_prefix_is_sanitized(self, facility, client_identified, staff_user):
        """Field-Wert ``+SUM(A:A)`` muss als ``'+SUM(A:A)`` rausgehen."""
        dt = _make_doc_type(facility, name="P")
        ft = _make_field_template(facility, name="Inj")
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: "+SUM(A:A)"},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], staff_user)
        assert row[-1] == "'+SUM(A:A)"

    def test_dynamic_field_order_follows_field_slugs_list(self, facility, client_identified, staff_user):
        """Reihenfolge der dynamischen Spalten folgt ``field_slugs``, nicht der
        ``data_json``-Insertion-Order. Mutation ``for field_slug in field_slugs``
        → ``for field_slug in data`` würde diese Reihenfolge brechen."""
        dt = _make_doc_type(facility, name="P")
        ft_a = _make_field_template(facility, name="A")
        ft_b = _make_field_template(facility, name="B")
        _attach(dt, ft_a, sort_order=0)
        _attach(dt, ft_b, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            # Bewusst andere Reihenfolge im data_json
            data_json={ft_b.slug: "second", ft_a.slug: "first"},
            created_by=staff_user,
        )
        row = _build_event_row(
            event,
            {ft_a.slug: ft_a, ft_b.slug: ft_b},
            [ft_a.slug, ft_b.slug],
            staff_user,
        )
        assert row[6] == "first"
        assert row[7] == "second"


@pytest.mark.django_db
class TestBuildEventRowVisibility:
    """Per-Field-Visibility: HIGH-Felder werden für STAFF maskiert, nicht
    geskippt — sonst geriete die Spaltenanzahl pro Zeile aus dem Tritt.
    """

    def test_high_field_for_staff_is_masked_not_skipped(self, facility, client_identified, staff_user):
        dt = _make_doc_type(facility, name="P", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(
            facility,
            name="Hoch",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: "secret"},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], staff_user)
        # Spalte ist da, Inhalt maskiert — Wert darf NICHT durchsickern.
        assert len(row) == 7
        assert "secret" not in str(row[-1])
        assert "Eingeschränkt" in str(row[-1])

    def test_assistant_sees_normal_field_on_normal_doc(self, facility, client_identified, staff_user, assistant_user):
        """Boundary: ASSISTANT (rank 0) sieht NORMAL doc + NORMAL field."""
        dt = _make_doc_type(facility, name="P", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(facility, name="Plain")
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: "ok"},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], assistant_user)
        assert row[-1] == "ok"

    def test_user_none_system_mode_sees_high_field(self, facility, client_identified, staff_user):
        """``user is None`` → System-Mode, keine Visibility-Filter.

        Mutation ``if user is not None and not user_can_see_field(...)``
        → ``if not user_can_see_field(...)`` würde im System-Mode ``user.role``
        auf ``None`` lesen und AttributeError werfen — der Test fängt das.
        """
        dt = _make_doc_type(facility, name="P", sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(
            facility,
            name="Hoch",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: "ok"},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], user=None)
        assert row[-1] == "ok"

    def test_lead_sees_high_field(self, facility, client_identified, staff_user, lead_user):
        """LEAD (rank 2) darf HIGH sehen — Boundary ``<=``."""
        dt = _make_doc_type(facility, name="P", sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(
            facility,
            name="Hoch",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(dt, ft)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={ft.slug: "value"},
            created_by=staff_user,
        )
        row = _build_event_row(event, {ft.slug: ft}, [ft.slug], lead_user)
        # Keine Maskierung — der Wert kommt durch (ggf. ``[verschlüsselt]``
        # bei encryption-fehlern, aber sicher nicht ``[Eingeschränkt]``).
        assert "Eingeschränkt" not in str(row[-1])

    def test_unknown_slug_inherits_doc_sensitivity(self, facility, client_identified, staff_user, assistant_user):
        """``ft is None`` → ``field_sensitivity = ""`` → doc-sensitivity zählt.

        Mutation ``ft.sensitivity if ft else ""`` → ``ft.sensitivity if ft else "high"``
        würde STAFF unsichtbar machen, wo es sichtbar sein müsste.
        """
        dt = _make_doc_type(facility, name="P", sensitivity=DocumentType.Sensitivity.ELEVATED)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"ghost": "leak"},
            created_by=staff_user,
        )
        # ASSISTANT (rank 0) vs. doc ELEVATED (rank 1) → Maskierung erwartet.
        row_assistant = _build_event_row(event, {}, ["ghost"], assistant_user)
        assert "leak" not in str(row_assistant[-1])
        assert "Eingeschränkt" in str(row_assistant[-1])


# ---------------------------------------------------------------------------
# get_jugendamt_statistics — Boundaries, Kategorien, Age-Cluster, unique_clients
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestJugendamtStatisticsCategoryAggregation:
    """``JUGENDAMT_CATEGORY_MAP`` faltet system_types auf Kategorien.

    Erwartetes Mapping (siehe Source):
    - contact → Kontakte
    - crisis, counseling → Beratung
    - medical, needle_exchange → Versorgung
    - accompaniment, referral → Vermittlung
    - note, ban → ausgeschlossen
    """

    def _make_event(self, facility, client, when, system_type, staff_user):
        dt = _make_doc_type(facility, name=f"DT-{system_type}", system_type=system_type)
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=dt,
            occurred_at=when,
            data_json={},
            created_by=staff_user,
        )

    def test_total_excludes_unmapped_system_types(self, facility, client_identified, staff_user):
        """note + ban müssen aus ``total`` herausfallen.

        Mutation ``if cat is None: continue`` → ``pass`` würde unmapped
        system_types in total mitzählen.
        """
        today = date.today()
        when = _aware(today, 10)
        self._make_event(facility, client_identified, when, DocumentType.SystemType.CONTACT, staff_user)
        self._make_event(facility, client_identified, when, DocumentType.SystemType.NOTE, staff_user)
        self._make_event(facility, client_identified, when, DocumentType.SystemType.BAN, staff_user)

        stats = get_jugendamt_statistics(facility, today, today)
        assert stats["total"] == 1, "Nur CONTACT zaehlt, NOTE+BAN sind im MAP nicht gelistet"

    def test_categories_collapse_multiple_system_types_into_one_label(self, facility, client_identified, staff_user):
        """``medical`` und ``needle_exchange`` müssen beide in ``Versorgung`` zählen."""
        today = date.today()
        when = _aware(today, 10)
        self._make_event(facility, client_identified, when, DocumentType.SystemType.MEDICAL, staff_user)
        self._make_event(facility, client_identified, when, DocumentType.SystemType.NEEDLE_EXCHANGE, staff_user)

        stats = get_jugendamt_statistics(facility, today, today)
        cats = dict(stats["by_category"])
        assert cats.get("Versorgung") == 2, "Versorgung MUSS medical+needle_exchange aggregieren"
        assert stats["total"] == 2

    def test_each_mapped_category_appears_with_correct_label(self, facility, client_identified, staff_user):
        """Mutation an JUGENDAMT_CATEGORY_MAP (z.B. ``contact: "Beratung"``)
        würde hier auffallen."""
        today = date.today()
        when = _aware(today, 11)
        # Je ein Event pro Kategorie
        for st in [
            DocumentType.SystemType.CONTACT,
            DocumentType.SystemType.CRISIS,
            DocumentType.SystemType.COUNSELING,
            DocumentType.SystemType.MEDICAL,
            DocumentType.SystemType.ACCOMPANIMENT,
            DocumentType.SystemType.REFERRAL,
        ]:
            self._make_event(facility, client_identified, when, st, staff_user)

        stats = get_jugendamt_statistics(facility, today, today)
        cats = dict(stats["by_category"])
        # Kontakte = 1 (nur contact)
        assert cats["Kontakte"] == 1
        # Beratung = 2 (crisis + counseling)
        assert cats["Beratung"] == 2
        # Versorgung = 1 (medical)
        assert cats["Versorgung"] == 1
        # Vermittlung = 2 (accompaniment + referral)
        assert cats["Vermittlung"] == 2
        assert stats["total"] == 6

    def test_empty_period_yields_zero_total_and_empty_categories(self, facility, client_identified, staff_user):
        """Mutation ``total = 0`` → ``total = 1`` würde diesen Test failen."""
        today = date.today()
        stats = get_jugendamt_statistics(facility, today, today)
        assert stats["total"] == 0
        assert stats["by_category"] == []
        assert stats["unique_clients"] == 0


@pytest.mark.django_db
class TestJugendamtStatisticsDateBoundaries:
    """``occurred_at__date__gte=date_from`` / ``__lte=date_to``.

    Mutmut mutiert beide einzeln (``__gte`` → ``__gt``, ``__lte`` → ``__lt``).
    Boundary-Tests: Event exakt am Anfang/Ende muss enthalten sein, ein Tag
    davor/danach darf NICHT enthalten sein.
    """

    def _contact_event(self, facility, client, when, staff_user):
        dt = _make_doc_type(
            facility,
            name=f"DT-{when.isoformat()}",
            system_type=DocumentType.SystemType.CONTACT,
        )
        return Event.objects.create(
            facility=facility,
            client=client,
            document_type=dt,
            occurred_at=when,
            data_json={},
            created_by=staff_user,
        )

    def test_event_exactly_at_date_from_included(self, facility, client_identified, staff_user):
        target = date(2026, 4, 15)
        self._contact_event(facility, client_identified, _aware(target, 0, 0), staff_user)
        stats = get_jugendamt_statistics(facility, target, target + timedelta(days=5))
        assert stats["total"] == 1, "Event am Start-Tag muss enthalten sein (__gte)"

    def test_event_exactly_at_date_to_included(self, facility, client_identified, staff_user):
        target = date(2026, 4, 15)
        end = date(2026, 4, 20)
        self._contact_event(facility, client_identified, _aware(end, 23, 59), staff_user)
        stats = get_jugendamt_statistics(facility, target, end)
        assert stats["total"] == 1, "Event am End-Tag muss enthalten sein (__lte)"

    def test_event_one_day_before_from_excluded(self, facility, client_identified, staff_user):
        target = date(2026, 4, 15)
        self._contact_event(facility, client_identified, _aware(target - timedelta(days=1), 12), staff_user)
        stats = get_jugendamt_statistics(facility, target, target + timedelta(days=5))
        assert stats["total"] == 0, "Event einen Tag vor date_from darf NICHT enthalten sein"

    def test_event_one_day_after_to_excluded(self, facility, client_identified, staff_user):
        end = date(2026, 4, 20)
        self._contact_event(facility, client_identified, _aware(end + timedelta(days=1), 12), staff_user)
        stats = get_jugendamt_statistics(facility, end - timedelta(days=5), end)
        assert stats["total"] == 0, "Event einen Tag nach date_to darf NICHT enthalten sein"


@pytest.mark.django_db
class TestJugendamtStatisticsAgeClusters:
    """``by_age_cluster`` listet pro Cluster Anzahl + i18n-Label.

    Branches:
    - ``exclude(client__isnull=True)`` → anonyme Events fallen raus
    - ``order_by("-count")`` → DESC
    - ``age_labels.get(cluster, "")`` → leerer Default bei unbekanntem Cluster
    """

    def test_anonymous_events_excluded_from_age_clusters(self, facility, staff_user):
        today = date.today()
        when = _aware(today, 10)
        dt = _make_doc_type(facility, name="Anon", system_type=DocumentType.SystemType.CONTACT)
        Event.objects.create(
            facility=facility,
            client=None,  # anonym
            document_type=dt,
            occurred_at=when,
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        stats = get_jugendamt_statistics(facility, today, today)
        # Total zählt anonyme Events (count('id') ohne Client-Filter), aber
        # by_age_cluster muss leer sein.
        assert stats["by_age_cluster"] == []

    def test_age_clusters_aggregated_per_cluster_with_label(self, facility, staff_user):
        today = date.today()
        when = _aware(today, 10)
        # Zwei Klienten je Cluster
        for cluster, count in [
            (Client.AgeCluster.U18, 2),
            (Client.AgeCluster.AGE_18_26, 1),
        ]:
            for i in range(count):
                cli = Client.objects.create(
                    facility=facility,
                    pseudonym=f"AC-{cluster}-{i}",
                    contact_stage=Client.ContactStage.IDENTIFIED,
                    age_cluster=cluster,
                )
                dt = _make_doc_type(
                    facility,
                    name=f"AC-DT-{cluster}-{i}",
                    system_type=DocumentType.SystemType.CONTACT,
                )
                Event.objects.create(
                    facility=facility,
                    client=cli,
                    document_type=dt,
                    occurred_at=when,
                    data_json={},
                    created_by=staff_user,
                )

        stats = get_jugendamt_statistics(facility, today, today)
        rows = {row["cluster"]: row for row in stats["by_age_cluster"]}
        assert rows["u18"]["count"] == 2
        assert rows["18_26"]["count"] == 1
        # i18n-Labels — Mutation ``age_labels.get(cluster, "")`` → ``.get(cluster)``
        # würde bei unbekannten Clustern None liefern (kein leerer String).
        assert str(rows["u18"]["label"]) == "Unter 18"
        assert str(rows["18_26"]["label"]) == "18–26"

    def test_age_clusters_sorted_descending_by_count(self, facility, staff_user):
        """``order_by("-count")``. Mutation ``-count`` → ``count`` würde
        die Reihenfolge invertieren."""
        today = date.today()
        when = _aware(today, 10)
        # 1× u18, 3× 27+
        for cluster, count in [
            (Client.AgeCluster.U18, 1),
            (Client.AgeCluster.AGE_27_PLUS, 3),
        ]:
            for i in range(count):
                cli = Client.objects.create(
                    facility=facility,
                    pseudonym=f"SO-{cluster}-{i}",
                    contact_stage=Client.ContactStage.IDENTIFIED,
                    age_cluster=cluster,
                )
                dt = _make_doc_type(
                    facility,
                    name=f"SO-DT-{cluster}-{i}",
                    system_type=DocumentType.SystemType.CONTACT,
                )
                Event.objects.create(
                    facility=facility,
                    client=cli,
                    document_type=dt,
                    occurred_at=when,
                    data_json={},
                    created_by=staff_user,
                )

        stats = get_jugendamt_statistics(facility, today, today)
        counts = [row["count"] for row in stats["by_age_cluster"]]
        assert counts == sorted(counts, reverse=True), (
            f"by_age_cluster muss DESC nach count sortiert sein, bekam {counts}"
        )
        assert counts[0] == 3


@pytest.mark.django_db
class TestJugendamtStatisticsUniqueClients:
    """``unique_clients`` zählt distinct, anonyme Events fallen raus."""

    def test_unique_clients_deduplicates_repeat_visits(self, facility, client_identified, staff_user):
        """Ein Client mit 3 Events → unique_clients = 1.

        Mutation ``.distinct()`` weglassen würde 3 zaehlen.
        """
        today = date.today()
        when = _aware(today, 10)
        dt = _make_doc_type(facility, name="UC", system_type=DocumentType.SystemType.CONTACT)
        for _i in range(3):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=dt,
                occurred_at=when,
                data_json={},
                created_by=staff_user,
            )
        stats = get_jugendamt_statistics(facility, today, today)
        assert stats["unique_clients"] == 1

    def test_unique_clients_excludes_anonymous_events(self, facility, client_identified, staff_user):
        """``exclude(client__isnull=True)`` — Mutation Negation würde
        anonyme Events mitzählen."""
        today = date.today()
        when = _aware(today, 10)
        dt = _make_doc_type(facility, name="UC2", system_type=DocumentType.SystemType.CONTACT)
        # 1 identified
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=when,
            data_json={},
            created_by=staff_user,
        )
        # 1 anonym — darf NICHT in unique_clients zaehlen
        Event.objects.create(
            facility=facility,
            client=None,
            document_type=dt,
            occurred_at=when,
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        stats = get_jugendamt_statistics(facility, today, today)
        assert stats["unique_clients"] == 1

    def test_unique_clients_counts_distinct_clients(self, facility, staff_user):
        """Zwei verschiedene Clients → 2; jeder mit 2 Events."""
        today = date.today()
        when = _aware(today, 10)
        dt = _make_doc_type(facility, name="UC3", system_type=DocumentType.SystemType.CONTACT)
        cli_a = Client.objects.create(
            facility=facility,
            pseudonym="UC-A",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        cli_b = Client.objects.create(
            facility=facility,
            pseudonym="UC-B",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        for cli in (cli_a, cli_b, cli_a, cli_b):
            Event.objects.create(
                facility=facility,
                client=cli,
                document_type=dt,
                occurred_at=when,
                data_json={},
                created_by=staff_user,
            )
        stats = get_jugendamt_statistics(facility, today, today)
        assert stats["unique_clients"] == 2


# ---------------------------------------------------------------------------
# JUGENDAMT_CATEGORY_MAP — Konstanten-Sanity (verteidigt Map-Edits)
# ---------------------------------------------------------------------------


class TestJugendamtCategoryMapConstant:
    """Verteidigt die statische Kategorien-Map.

    Diese Map ist Teil des Reporting-Vertrags mit dem Jugendamt — eine
    versehentliche Umverteilung (z.B. ``crisis`` → ``Versorgung``) wäre
    fachlich falsch und würde silently rausgehen.
    """

    def test_map_includes_expected_keys(self):
        assert set(JUGENDAMT_CATEGORY_MAP.keys()) == {
            "contact",
            "crisis",
            "medical",
            "needle_exchange",
            "accompaniment",
            "counseling",
            "referral",
        }

    def test_map_excludes_note_and_ban(self):
        assert "note" not in JUGENDAMT_CATEGORY_MAP
        assert "ban" not in JUGENDAMT_CATEGORY_MAP

    def test_categories_are_german_labels(self):
        assert JUGENDAMT_CATEGORY_MAP["contact"] == "Kontakte"
        assert JUGENDAMT_CATEGORY_MAP["crisis"] == "Beratung"
        assert JUGENDAMT_CATEGORY_MAP["counseling"] == "Beratung"
        assert JUGENDAMT_CATEGORY_MAP["medical"] == "Versorgung"
        assert JUGENDAMT_CATEGORY_MAP["needle_exchange"] == "Versorgung"
        assert JUGENDAMT_CATEGORY_MAP["accompaniment"] == "Vermittlung"
        assert JUGENDAMT_CATEGORY_MAP["referral"] == "Vermittlung"

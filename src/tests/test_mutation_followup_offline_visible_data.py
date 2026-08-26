"""Mutation-Followup-Tests für ``core.services.offline`` — ``_visible_data_fields``.

Refs #930. Sub-File aus ``test_mutation_followup_offline``;
enthält die Test-Klasse ``TestVisibleDataFields`` — Marker-Branches
(``__file__``, ``__files__``), ``__encrypted__``-Branch via ``safe_decrypt``
und Sensitivity-Strip.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings
from django.utils import timezone

from core.models import (
    DocumentType,
    Event,
)
from core.services.compliance import SENSITIVITY_RANK
from core.services.file_vault import encrypt_field
from core.services.system import (
    _visible_data_fields,
)
from tests._mutation_followup_offline_helpers import (
    _attach,
    _make_doc_type,
    _make_event,
    _make_field_template,
)

# ---------------------------------------------------------------------------
# _visible_data_fields — Marker und Encrypt-Branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVisibleDataFields:
    """``_visible_data_fields`` ist der zentrale Filter-Helper.

    Schwerpunkte: leeres ``data_json``, sensitivity-Strip, single-/multi-file
    Marker, ``__encrypted__``-Branch via ``safe_decrypt``.
    """

    def test_empty_data_returns_empty_dict(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(facility, client_identified, doc_type_contact, staff_user, data_json={})
        assert _visible_data_fields(staff_user, event) == {}

    def test_plain_value_kept_for_visible_field(self, facility, client_identified, doc_type_contact, staff_user):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": "Hallo"},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == "Hallo"

    def test_field_stripped_when_user_cannot_see(self, facility, client_identified, staff_user, assistant_user):
        dt = _make_doc_type(facility, name="HD", sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(facility, name="X", sensitivity="high", is_encrypted=True)
        _attach(dt, ft)
        event = _make_event(
            facility,
            client_identified,
            dt,
            staff_user,
            data_json={ft.slug: "secret"},
        )
        # Re-fetch fuer Encryption-Marker
        event.refresh_from_db()
        result = _visible_data_fields(assistant_user, event)
        # Assistant sieht weder HIGH doc noch HIGH field — Strip greift.
        assert ft.slug not in result

    def test_orphan_high_field_gated_facility_wide(
        self, facility, client_identified, staff_user, assistant_user, lead_user
    ):
        """Refs #1725 (SICHERHEITSBEFUND): Ein einst HIGH-eingestuftes, nun
        verwaistes Feld darf im Offline-Bundle seine Sensitivity NICHT auf ``""``
        (=NORMAL) fallen lassen und entschlüsselt an unterberechtigte Rollen
        ausgeliefert werden.

        Vor dem Fix fiel ``ft=None`` → Sensitivity ``""`` → der HIGH-Klartext
        landete im Offline-Bundle (persistiert in IndexedDB) auch für Assistenz.
        Jetzt wird der Slug facility-weit aufgelöst — Assistant sieht ihn nicht,
        Leitung schon.
        """
        dt = _make_doc_type(facility, name="Waise", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft_high = _make_field_template(facility, name="Diagnose", sensitivity="high", is_encrypted=True)
        # ft_high NICHT attachen → sofort verwaist.
        event = _make_event(facility, client_identified, dt, staff_user, data_json={ft_high.slug: "streng geheim"})
        assert ft_high.slug not in _visible_data_fields(assistant_user, event), (
            "Assistant darf ein einst-HIGH Waisenfeld nicht im Offline-Bundle sehen"
        )
        assert _visible_data_fields(lead_user, event).get(ft_high.slug) == "streng geheim", (
            "Leitung sieht den Waisenwert weiterhin"
        )

    def test_orphan_without_template_gated_high_conservatively(
        self, facility, client_identified, doc_type_contact, staff_user, lead_user
    ):
        """Refs #1725: Ein verwaister Slug ohne auffindbares ``FieldTemplate``
        wird konservativ als HIGH gegatet — Fachkraft verbergen, Leitung zeigen."""
        event = _make_event(
            facility, client_identified, doc_type_contact, staff_user, data_json={"ghost-orphan": "rest"}
        )
        assert "ghost-orphan" not in _visible_data_fields(staff_user, event)
        assert _visible_data_fields(lead_user, event).get("ghost-orphan") == "rest"

    def test_single_file_marker_keeps_name_only(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``"name": value.get("name", "")`` → ``"id"`` wuerde id leaken."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__file__": True,
                    "name": "report.pdf",
                    "attachment_id": "secret-id",
                    "content_type": "application/pdf",
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__file__": True, "name": "report.pdf"}
        # Defensive: keine internen IDs leaken.
        import json

        assert "secret-id" not in json.dumps(result)

    def test_single_file_marker_missing_name_uses_empty_string(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``value.get("name", "")`` → ``value.get("name")``
        wuerde None liefern."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__file__": True}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__file__": True, "name": ""}

    def test_multi_file_marker_returns_count_only(self, facility, client_identified, doc_type_contact, staff_user):
        """Refs #786: ``__files__``-Branch reduziert auf count, KEINE entries/IDs."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__files__": True,
                    "entries": [
                        {"id": "aaa", "sort": 0},
                        {"id": "bbb", "sort": 1},
                    ],
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 2}
        import json

        # Keine internen IDs, kein "entries"-Key.
        body = json.dumps(result)
        assert "aaa" not in body
        assert "bbb" not in body
        assert "entries" not in body

    def test_multi_file_marker_counts_only_entries_with_id(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``sum(1 for e in entries if isinstance(e, dict) and e.get("id"))``
        → ``len(entries)`` wuerde auch Eintraege ohne ID mitzaehlen."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={
                "notiz": {
                    "__files__": True,
                    "entries": [
                        {"id": "a"},
                        {},  # ohne id
                        "garbage",  # nicht dict
                        {"id": "b"},
                    ],
                }
            },
        )
        result = _visible_data_fields(staff_user, event)
        # Nur 2 echte Entries mit id → count = 2.
        assert result["notiz"] == {"__files__": True, "count": 2}

    def test_multi_file_marker_empty_entries_count_zero(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__files__": True, "entries": []}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 0}

    def test_multi_file_marker_missing_entries_defaults_to_empty(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation ``value.get("entries") or []`` → ``value.get("entries")``
        wuerde None liefern, was beim sum() crashen würde."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": {"__files__": True}},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == {"__files__": True, "count": 0}

    def test_encrypted_value_is_decrypted(self, facility, client_identified, staff_user):
        """``__encrypted__``-Branch: ``safe_decrypt`` muss aufgerufen werden.

        Mit gueltigem ENCRYPTION_KEY laeuft decrypt durch — Klartext landet
        im Bundle. Mutation des Branchs wuerde stattdessen Marker-Dict oder
        Default leaken.
        """
        key = Fernet.generate_key().decode("utf-8")
        with override_settings(ENCRYPTION_KEY=key, ENCRYPTION_KEYS=""):
            dt = _make_doc_type(facility, name="Enc")
            ft = _make_field_template(
                facility,
                name="EncField",
                # Sensitivity leer lassen, damit STAFF sehen darf.
                is_encrypted=True,
            )
            _attach(dt, ft)
            encrypted_marker = encrypt_field("Geheim123")
            # Direkt das encrypted dict in data_json schreiben — Event.save()
            # encryptet nicht doppelt, weil is_encrypted_value() True ist.
            event = Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=dt,
                occurred_at=timezone.now(),
                data_json={ft.slug: encrypted_marker},
                created_by=staff_user,
            )
            event.refresh_from_db()
            result = _visible_data_fields(staff_user, event)
            assert result[ft.slug] == "Geheim123"

    def test_dict_value_without_encryption_marker_passes_through(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """``safe_decrypt`` liefert das Dict 1:1 zurueck, wenn kein
        ``__encrypted__``-Marker dran ist — Mutation der ``else``-Klausel
        wird gefangen."""
        payload = {"a": 1, "b": "x"}
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": payload},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == payload

    def test_non_dict_value_passes_through_for_visible_field(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """List/int/str/None werden 1:1 durchgereicht — keiner der dict-
        Branches greift, der ``else``-Pfad sichtbar."""
        event = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"notiz": ["a", "b", "c"]},
        )
        result = _visible_data_fields(staff_user, event)
        assert result["notiz"] == ["a", "b", "c"]


@pytest.mark.django_db
class TestVisibleDataFieldsGateMatrix:
    """Refs #1891: Gate-Matrix-Pins VOR dem Extract-Method-Umbau.

    ``_visible_data_fields`` ist die Leak-Grenze der Offline-Bundles — diese
    Pins nageln das Sensitivity-Gate (Feld-Override, DocType-Vererbung) und
    die Reihenfolge Gate-VOR-Wert-Transformation fest, damit der Umbau
    (Gate-Praedikat getrennt von den drei Wert-Transformationen)
    nachweisbar verhaltensneutral ist.
    """

    def test_field_override_escalates_above_doc_type(
        self, facility, client_identified, staff_user, assistant_user, lead_user
    ):
        """Feld-Override HIGH eskaliert ueber den NORMAL-DocType
        (effective = max(doc_type, field)): nur Lead+ sieht den Wert."""
        dt = _make_doc_type(facility, name="Gate Normal", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(facility, name="Heikles Feld", sensitivity=DocumentType.Sensitivity.HIGH)
        _attach(dt, ft)
        event = _make_event(facility, client_identified, dt, staff_user, data_json={ft.slug: "geheim"})

        assert ft.slug not in _visible_data_fields(assistant_user, event)
        assert ft.slug not in _visible_data_fields(staff_user, event)
        assert _visible_data_fields(lead_user, event)[ft.slug] == "geheim"

    def test_empty_field_sensitivity_inherits_doc_type(self, facility, client_identified, staff_user, assistant_user):
        """Leere Feld-Sensitivity erbt die DocType-Stufe (ELEVATED):
        Assistenz gestrippt, Staff sichtbar."""
        dt = _make_doc_type(facility, name="Gate Erhoeht", sensitivity=DocumentType.Sensitivity.ELEVATED)
        ft = _make_field_template(facility, name="Erbendes Feld", sensitivity="")
        _attach(dt, ft)
        event = _make_event(facility, client_identified, dt, staff_user, data_json={ft.slug: "wert"})

        assert _visible_data_fields(staff_user, event)[ft.slug] == "wert"
        assert ft.slug not in _visible_data_fields(assistant_user, event)

    def test_field_override_never_lowers_doc_type(
        self, facility, client_identified, staff_user, assistant_user, lead_user, admin_user
    ):
        """Refs #1998: Ein NIEDRIGERER Feld-Override senkt die DocType-Stufe
        NICHT — ``effective_sensitivity`` ist ``max(Typ, Feld)``.

        Die beiden Tests darueber decken nur die Eskalation (Feld > Typ) und
        die Vererbung (Feld leer) ab. Beide belegen, DASS beide Argumente bei
        ``user_can_see_field`` ankommen, nicht WIE sie verrechnet werden: die
        Mutante ``user_can_see_field(user, field_sensitivity or
        doc_type.sensitivity)`` ueberlebt sie und die gesamte Offline-Batterie.
        Sie ist hier schwerer als im Schema-Serializer — gestrippt wird nicht
        bloss die Feldbeschreibung, sondern der WERT: ein HIGH-Typ mit einem
        ausdruecklich auf "normal" gesetzten Feld gaebe seinen Klartext an
        STAFF und ASSISTENZ ins IndexedDB, wo er bis zum TTL-Ablauf liegt.
        """
        dt = _make_doc_type(facility, name="Gate Hoch", sensitivity=DocumentType.Sensitivity.HIGH)
        ft = _make_field_template(facility, name="Scheinbar harmlos", sensitivity=DocumentType.Sensitivity.NORMAL)
        _attach(dt, ft)
        event = _make_event(facility, client_identified, dt, staff_user, data_json={ft.slug: "geheim"})
        # Vorbedingung: der Override ist gesetzt UND echt niedriger als die
        # Typ-Stufe — nur dann unterscheidet sich der Fall von der Vererbung.
        assert ft.sensitivity == DocumentType.Sensitivity.NORMAL
        assert SENSITIVITY_RANK[ft.sensitivity] < SENSITIVITY_RANK[dt.sensitivity]

        assert ft.slug not in _visible_data_fields(assistant_user, event)
        assert ft.slug not in _visible_data_fields(staff_user, event)
        # Beide HIGH-faehigen Rollen sehen den Wert — haelt den Test nicht-vakuum.
        assert _visible_data_fields(lead_user, event)[ft.slug] == "geheim"
        assert _visible_data_fields(admin_user, event)[ft.slug] == "geheim"

    def test_encrypted_value_never_decrypted_for_stripped_field(
        self, facility, client_identified, staff_user, assistant_user, monkeypatch
    ):
        """Leak-Grenze: fuer ein gestripptes Feld laeuft ``safe_decrypt`` NIE
        (Gate VOR Wert-Transformation) — kein unnoetiges Entschluesseln von
        Werten, die das Bundle gar nicht verlassen duerfen."""
        dt = _make_doc_type(facility, name="Gate Krypt", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(
            facility,
            name="Kryptfeld",
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        _attach(dt, ft)
        event = _make_event(
            facility,
            client_identified,
            dt,
            staff_user,
            data_json={ft.slug: {"__encrypted__": True, "value": "token"}},
        )
        calls: list = []
        monkeypatch.setattr(
            "core.services.system.offline.safe_decrypt",
            lambda value: calls.append(value) or "KLARTEXT",
        )

        result = _visible_data_fields(assistant_user, event)

        assert ft.slug not in result
        assert calls == []

    def test_file_marker_on_stripped_field_not_included(self, facility, client_identified, staff_user, assistant_user):
        """Auch Datei-Marker (Metadaten) verlassen den Server nicht fuer
        Felder, die das Gate strippt."""
        dt = _make_doc_type(facility, name="Gate Datei", sensitivity=DocumentType.Sensitivity.NORMAL)
        ft = _make_field_template(facility, name="Dateifeld", sensitivity=DocumentType.Sensitivity.HIGH)
        _attach(dt, ft)
        event = _make_event(
            facility,
            client_identified,
            dt,
            staff_user,
            data_json={ft.slug: {"__file__": True, "name": "bericht.pdf"}},
        )

        assert ft.slug not in _visible_data_fields(assistant_user, event)

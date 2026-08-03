"""Mutation-Followup-Tests für ``core.services.client.export`` — Gather-Helfer.

Refs #930. Sub-File aus ``test_mutation_followup_client_export``;
enthält die Test-Klassen für die privaten ``_gather_*``-Helfer
(``_gather_client_fields``, ``_gather_events``, ``_gather_cases``,
``_gather_event_history``, ``_gather_deletion_requests``,
``_gather_workitems``).

Der Sollzustand der Art.-15-Auskunft ist am 2026-08-03 per Produktentscheid
gewachsen (Refs #1752, #1755):
Papierkorb-/Anonymisierungs-Metadaten, bislang fehlende Kleinfelder,
Historien-Inhalte, Personen-Löschanträge und der Papierkorb der Ereignisse
gehören hinein. Die Pins hier folgen dieser Anforderungsänderung — **ohne**
ihre Schärfe aufzugeben: die Schlüsselsätze bleiben exakt gepinnt (``==``,
nicht ``<=``) und jedes neue Feld hat seinen eigenen Boundary-Test.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from core.models import (
    Case as CaseModel,
)
from core.models import (
    DeletionRequest,
    DocumentType,
    Event,
    EventHistory,
    WorkItem,
)
from core.services.client import (
    _gather_cases,
    _gather_client_fields,
    _gather_deletion_requests,
    _gather_entity_dynamic_data,
    _gather_entity_orphan_data,
    _gather_event_history,
    _gather_events,
    _gather_workitems,
)
from tests._mutation_followup_client_export_helpers import (
    _make_doc_type,
    _make_event,
)

# ---------------------------------------------------------------------------
# _gather_client_fields — DSGVO-vollständige Master-Daten
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherClientFields:
    """Refs ``_gather_client_fields`` (Line 12).

    Mutation jedes einzelnen ``key`` würde das Result-Dict beschneiden.
    DSGVO Art. 15 verlangt aber Vollständigkeit der Stammdaten — wir
    pinnen jedes Feld explizit fest.
    """

    def test_contains_all_twelve_master_fields(self, client_identified):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher pinnte dieser Test sechs Felder.

        Neu dazu: ``notes`` (gewichtigster Freitext), ``k_anonymized``
        (erklärt generalisierte Werte), ``updated_at`` und die
        Papierkorb-Metadaten aus ``_soft_delete_meta``.
        """
        result = _gather_client_fields(client_identified)
        assert set(result.keys()) == {
            "pseudonym",
            "contact_stage",
            "age_cluster",
            "notes",
            "is_active",
            "k_anonymized",
            "created_at",
            "updated_at",
            "created_by",
            "is_deleted",
            "deleted_at",
            "deleted_by",
        }

    def test_pseudonym_is_preserved(self, client_identified):
        result = _gather_client_fields(client_identified)
        assert result["pseudonym"] == client_identified.pseudonym

    def test_contact_stage_uses_display_label_not_raw_value(self, client_identified):
        """Mutation ``get_contact_stage_display()`` → ``contact_stage``
        würde den Rohwert ``"identified"`` statt ``"Identifiziert"`` liefern.
        """
        result = _gather_client_fields(client_identified)
        # i18n-Label, nicht der Rohwert.
        assert result["contact_stage"] == "Identifiziert"
        assert result["contact_stage"] != "identified"

    def test_contact_stage_qualified(self, client_qualified):
        result = _gather_client_fields(client_qualified)
        assert result["contact_stage"] == "Qualifiziert"

    def test_age_cluster_uses_display_label(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="age-test",
            contact_stage=Client.ContactStage.IDENTIFIED,
            age_cluster=Client.AgeCluster.AGE_18_26,
            created_by=staff_user,
        )
        result = _gather_client_fields(c)
        assert result["age_cluster"] == "18–26"

    def test_is_active_true(self, client_identified):
        result = _gather_client_fields(client_identified)
        assert result["is_active"] is True

    def test_is_active_false_is_preserved(self, facility, staff_user):
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="inactive",
            contact_stage=Client.ContactStage.IDENTIFIED,
            is_active=False,
            created_by=staff_user,
        )
        result = _gather_client_fields(c)
        assert result["is_active"] is False, "Bool muss 1:1 durchgereicht werden, nicht negiert"

    def test_created_at_is_isoformat_string(self, client_identified):
        """Mutation ``.isoformat()`` → ``.strftime(...)`` oder Entfernen würde
        ein ``datetime``-Objekt liefern statt String. ISO-Format ist
        Pflicht für JSON-Serialisierung des Exports."""
        result = _gather_client_fields(client_identified)
        assert isinstance(result["created_at"], str)
        assert result["created_at"] == client_identified.created_at.isoformat()
        # ISO-Format hat ein "T" zwischen Datum und Uhrzeit.
        assert "T" in result["created_at"]

    def test_created_by_username_when_present(self, client_identified, staff_user):
        result = _gather_client_fields(client_identified)
        assert result["created_by"] == staff_user.username

    def test_created_by_none_when_user_missing(self, facility):
        """Mutation ``... if client.created_by else None`` → ``... if client.created_by``
        ohne else-Branch würde crashen. Wir simulieren den FK=NULL-Fall."""
        from core.models import Client

        c = Client.objects.create(
            facility=facility,
            pseudonym="no-creator",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=None,
        )
        result = _gather_client_fields(c)
        assert result["created_by"] is None

    # -- Refs #1752: Freitext, Anonymisierung, Papierkorb --------------------

    def test_notes_are_exported_verbatim(self, client_identified):
        """Mutation ``client.notes`` → Konstante/Leerstring würde ausgerechnet
        den Freitext unterschlagen, den ``docs/security-notes.md`` selbst als
        „hoch (Klarname-Risiko)" einstuft."""
        client_identified.notes = "Freitext-Vermerk der Fachkraft"
        client_identified.save(update_fields=["notes"])
        assert _gather_client_fields(client_identified)["notes"] == "Freitext-Vermerk der Fachkraft"

    def test_notes_empty_by_default(self, client_identified):
        """Der Key existiert auch ohne Inhalt — sonst wäre der Schlüsselsatz
        datenabhängig und die Auskunft nicht reproduzierbar."""
        assert _gather_client_fields(client_identified)["notes"] == ""

    def test_k_anonymized_false_for_untouched_record(self, client_identified):
        assert _gather_client_fields(client_identified)["k_anonymized"] is False

    def test_k_anonymized_true_is_preserved(self, client_identified):
        """Mutation ``client.k_anonymized`` → Negation/Konstante würde
        verschweigen, warum Felder generalisiert erscheinen."""
        client_identified.k_anonymized = True
        client_identified.save(update_fields=["k_anonymized"])
        result = _gather_client_fields(client_identified)
        assert result["k_anonymized"] is True, "Bool muss 1:1 durchgereicht werden, nicht negiert"

    def test_updated_at_is_isoformat_and_independent_of_created_at(self, client_identified):
        """Mutation ``client.updated_at`` → ``client.created_at`` würde die
        letzte Änderung verschweigen — beide Zeitstempel eigenständig pinnen."""
        client_identified.notes = "Änderung"
        client_identified.save(update_fields=["notes", "updated_at"])
        client_identified.refresh_from_db()
        result = _gather_client_fields(client_identified)
        assert isinstance(result["updated_at"], str)
        assert result["updated_at"] == client_identified.updated_at.isoformat()
        assert result["updated_at"] != result["created_at"]

    def test_trash_metadata_defaults_for_live_record(self, client_identified):
        """Mutation an ``_soft_delete_meta`` (Konstanten statt Feldwerten)
        würde einen lebenden Datensatz als gelöscht ausweisen."""
        result = _gather_client_fields(client_identified)
        assert result["is_deleted"] is False
        assert result["deleted_at"] is None
        assert result["deleted_by"] is None

    def test_trash_metadata_after_soft_delete(self, client_identified, staff_user):
        """Ein Datensatz im Papierkorb existiert bis zum Hard-Purge weiter —
        die Auskunft muss das ausweisen, sonst wirkt er „gelöscht"."""
        client_identified.soft_delete(user=staff_user)
        result = _gather_client_fields(client_identified)
        assert result["is_deleted"] is True
        assert result["deleted_at"] == client_identified.deleted_at.isoformat()
        assert result["deleted_by"] == staff_user.username

    def test_deleted_by_none_when_purge_orphaned_the_user(self, client_identified):
        """``deleted_by`` ist ``SET_NULL`` — Mutation ``_username`` → fester
        Wert würde bei FK=NULL crashen oder eine Person erfinden."""
        client_identified.soft_delete(user=None)
        result = _gather_client_fields(client_identified)
        assert result["is_deleted"] is True
        assert result["deleted_by"] is None


# ---------------------------------------------------------------------------
# _gather_entity_dynamic_data — entity-neutrale Zusammenführung (Refs #1730-4)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEntityDynamicData:
    """Refs #1730 (Punkt 4): ``_gather_client_dynamic_data`` und
    ``_gather_case_dynamic_data`` waren wörtliche Duplikate — jetzt EIN
    entity-neutraler Helfer über Duck-Typing (``document_type``/``data_json``).

    Verhalten muss exakt erhalten bleiben: ``None`` ohne Typ, sonst
    ``[{slug, label, value}, ...]`` in konfigurierter Feldreihenfolge, Werte
    entschlüsselt und über ``format_dynamic_display_value`` lesbar gemacht.
    """

    def test_returns_none_without_document_type(self, client_identified):
        """Ohne ``document_type`` gibt es keine Feld-Definitionen → ``None``
        (der Aufrufer lässt die Export-Keys dann ganz weg)."""
        assert _gather_entity_dynamic_data(client_identified) is None

    def test_client_entries_follow_configured_order_with_labels(self, facility, doc_type_client_profile, staff_user):
        from core.models import Client

        client = Client.objects.create(
            facility=facility,
            pseudonym="entity-dyn-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        entries = _gather_entity_dynamic_data(client)
        assert entries == [{"slug": "wohnsituation", "label": "Wohnsituation", "value": "WG"}]

    def test_case_entries_are_gathered_too(self, facility, client_identified, doc_type_case_form, staff_user):
        """Derselbe Helfer bedient auch das ``Case``-Aggregat (Duck-Typing)."""
        from core.models import Case

        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="entity-dyn-case",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden"},
        )
        entries = _gather_entity_dynamic_data(case)
        assert entries == [{"slug": "anlass", "label": "Anlass", "value": "Mietschulden"}]

    def test_encrypted_value_is_decrypted(self, facility, doc_type_client_profile, staff_user):
        """Der Art.-15-Auskunftsanspruch zielt auf den Inhalt — der Wert wird
        entschlüsselt ausgegeben, nicht als Fernet-Token."""
        from core.models import Client
        from core.services.file_vault import generate_key

        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
            client = Client.objects.create(
                facility=facility,
                pseudonym="entity-dyn-enc",
                created_by=staff_user,
                document_type=doc_type_client_profile,
                data_json={"diagnose": "Sehr sensibel"},
            )
            entries = _gather_entity_dynamic_data(client)
        by_slug = {e["slug"]: e for e in entries}
        assert by_slug["diagnose"]["value"] == "Sehr sensibel"


# ---------------------------------------------------------------------------
# _gather_entity_orphan_data — Waisen im Art.-15-Export (Refs #1725(a))
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEntityOrphanData:
    """Refs #1725(a): verwaiste ``data_json``-Keys (Slug nicht mehr am Typ)
    gehören separat in den Auskunfts-Export, damit ``dynamic_data`` stabil bleibt.

    Wie ``_gather_entity_dynamic_data`` **ohne** Feld-Gating (der Export hängt an
    ``LeadOrAdminRequiredMixin`` + SudoMode und entschlüsselt alles — eine
    Filterung wäre Scheinsicherheit mit unvollständiger Auskunft).
    """

    def _orphan_ft(self, facility, *, name="Ehemals-Feld"):
        from core.models import FieldTemplate

        return FieldTemplate.objects.create(
            facility=facility,
            name=name,
            field_type=FieldTemplate.FieldType.TEXT,
        )

    def test_returns_none_without_document_type(self, client_identified):
        assert _gather_entity_orphan_data(client_identified) is None

    def test_returns_empty_when_no_orphans(self, facility, doc_type_client_profile, staff_user):
        from core.models import Client

        client = Client.objects.create(
            facility=facility,
            pseudonym="orphan-export-empty",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        assert _gather_entity_orphan_data(client) == []

    def test_orphan_entry_uses_facility_label_and_decrypted_value(self, facility, doc_type_client_profile, staff_user):
        from core.models import Client

        orphan_ft = self._orphan_ft(facility)
        client = Client.objects.create(
            facility=facility,
            pseudonym="orphan-export-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", orphan_ft.slug: "Altwert"},
        )
        entries = _gather_entity_orphan_data(client)
        assert entries == [{"slug": orphan_ft.slug, "label": "Ehemals-Feld", "value": "Altwert"}]

    def test_client_fields_include_orphaned_key_only_when_present(self, facility, doc_type_client_profile, staff_user):
        from core.models import Client

        # Ohne Waisen: der Key fehlt (Bestandsformat bleibt stabil).
        clean = Client.objects.create(
            facility=facility,
            pseudonym="orphan-agg-clean",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG"},
        )
        assert "dynamic_data_orphaned" not in _gather_client_fields(clean)

        # Mit Waise: separater Key neben dem stabilen ``dynamic_data``.
        orphan_ft = self._orphan_ft(facility, name="Alt")
        dirty = Client.objects.create(
            facility=facility,
            pseudonym="orphan-agg-dirty",
            created_by=staff_user,
            document_type=doc_type_client_profile,
            data_json={"wohnsituation": "WG", orphan_ft.slug: "Rest"},
        )
        result = _gather_client_fields(dirty)
        assert result["dynamic_data"] == [{"slug": "wohnsituation", "label": "Wohnsituation", "value": "WG"}]
        assert result["dynamic_data_orphaned"] == [{"slug": orphan_ft.slug, "label": "Alt", "value": "Rest"}]

    def test_cases_include_orphaned_key_only_when_present(
        self, facility, client_identified, doc_type_case_form, staff_user
    ):
        from core.models import Case

        orphan_ft = self._orphan_ft(facility, name="Alt-Fall")
        Case.objects.create(
            facility=facility,
            client=client_identified,
            title="orphan-case",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Miete", orphan_ft.slug: "Rest"},
        )
        entry = _gather_cases(client_identified)[0]
        assert entry["dynamic_data_orphaned"] == [{"slug": orphan_ft.slug, "label": "Alt-Fall", "value": "Rest"}]


# ---------------------------------------------------------------------------
# _gather_events — Filter-Boundaries, Visibility, Sort, Return-Tuple
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEvents:
    """Refs ``_gather_events`` (Line 41).

    Schwerpunkte:
    - Tuple-Return ``(events_data, event_ids)``
    - Filter: ``client=client``, ``is_deleted=<Parameter>`` (Refs #1752)
    - Visibility-Filter: ``Event.objects.visible_to(user)``
    - Sort: ``-occurred_at``, ``pk`` (neueste zuerst, deterministisch)
    """

    def test_returns_tuple_of_list_and_id_list(self, facility, client_identified, doc_type_contact, staff_user):
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert isinstance(events_data, list)
        assert isinstance(event_ids, list)
        assert len(events_data) == 1
        assert ev.pk in event_ids

    def test_empty_when_no_events(self, client_identified, staff_user):
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert events_data == []
        assert event_ids == []

    def test_excludes_soft_deleted_events_by_default(self, facility, client_identified, doc_type_contact, staff_user):
        """Der Default-Zweig bleibt der Live-Bestand: Mutation am Default
        ``is_deleted=False`` → ``True`` würde den Papierkorb in den
        ``events``-Abschnitt kippen."""
        kept = _make_event(facility, client_identified, doc_type_contact, staff_user)
        deleted = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert kept.pk in event_ids
        assert deleted.pk not in event_ids
        assert len(events_data) == 1

    def test_is_deleted_true_returns_the_trash_only(self, facility, client_identified, doc_type_contact, staff_user):
        """Refs #1752: der Parameter wählt zwischen Live-Bestand und Papierkorb.

        Mutation ``filter(..., is_deleted=is_deleted)`` → fester Wert würde
        einen der beiden Abschnitte der Auskunft mit dem falschen Bestand
        füllen (und ``deleted_events`` damit zur Dublette von ``events``
        machen).
        """
        kept = _make_event(facility, client_identified, doc_type_contact, staff_user)
        deleted = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        events_data, event_ids = _gather_events(client_identified, staff_user, is_deleted=True)
        assert event_ids == [deleted.pk]
        assert kept.pk not in event_ids
        assert [e["is_deleted"] for e in events_data] == [True]

    def test_deleted_events_still_respect_the_visibility_filter(
        self, facility, client_identified, staff_user, assistant_user
    ):
        """Der Papierkorb-Abschnitt darf die Sensitivity-Matrix nicht umgehen —
        Mutation ``visible_to(user)`` → ``all()`` wäre sonst nur noch auf dem
        Live-Zweig gepinnt."""
        high_doc = _make_doc_type(facility, name="Krise-Papierkorb", sensitivity=DocumentType.Sensitivity.HIGH)
        _make_event(facility, client_identified, high_doc, staff_user, is_deleted=True)
        events_data, event_ids = _gather_events(client_identified, assistant_user, is_deleted=True)
        assert events_data == []
        assert event_ids == []

    def test_excludes_events_of_other_clients(
        self, facility, client_identified, client_qualified, doc_type_contact, staff_user
    ):
        """Mutation ``client=client`` → ``client=anything_else`` würde
        Cross-Klient-Daten leaken. Wir legen je einen Event auf zwei
        Klienten und prüfen Isolation."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_qualified, doc_type_contact, staff_user)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert ev_own.pk in event_ids
        assert ev_other.pk not in event_ids

    def test_assistant_cannot_see_elevated_events(
        self, facility, client_identified, doc_type_contact, staff_user, assistant_user
    ):
        """``visible_to(user)``-Filter: ASSISTANT (rank 0) darf ELEVATED
        nicht sehen. Mutation ``visible_to(user)`` → ``all()`` würde den
        Elevated-Event im Assistent:innen-Export auftauchen lassen.
        """
        elevated_doc = _make_doc_type(facility, name="Krise", sensitivity=DocumentType.Sensitivity.ELEVATED)
        normal_event = _make_event(facility, client_identified, doc_type_contact, staff_user)
        elevated_event = _make_event(facility, client_identified, elevated_doc, staff_user)

        events_data, event_ids = _gather_events(client_identified, assistant_user)
        assert normal_event.pk in event_ids
        assert elevated_event.pk not in event_ids, (
            "ASSISTANT darf ELEVATED-Events nicht im Export sehen — Sensitivity-Matrix verletzt"
        )

    def test_lead_sees_high_sensitivity_events(self, facility, client_identified, staff_user, lead_user):
        """LEAD (rank 2) sieht HIGH-Events. Boundary auf der oberen Seite
        der Sensitivity-Matrix."""
        high_doc = _make_doc_type(facility, name="Hoch", sensitivity=DocumentType.Sensitivity.HIGH)
        ev = _make_event(facility, client_identified, high_doc, staff_user)
        events_data, event_ids = _gather_events(client_identified, lead_user)
        assert ev.pk in event_ids

    def test_order_descending_by_occurred_at(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``order_by("-occurred_at")`` → ``order_by("occurred_at")``
        oder Entfernen würde die Sortierung kippen. Neueste Events
        müssen oben stehen."""
        now = timezone.now()
        ev_old = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=now - timedelta(days=5),
        )
        ev_mid = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            occurred_at=now - timedelta(days=1),
        )
        ev_new = _make_event(facility, client_identified, doc_type_contact, staff_user, occurred_at=now)
        events_data, event_ids = _gather_events(client_identified, staff_user)
        # Reihenfolge: neueste zuerst, dann mid, dann old.
        assert event_ids == [ev_new.pk, ev_mid.pk, ev_old.pk]

    def test_event_data_and_ids_have_same_length_and_order(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """Mutation an einer der zwei ``append``-Stellen würde Listen
        unterschiedlicher Länge produzieren — beides muss parallel
        wachsen und die Reihenfolge muss übereinstimmen."""
        now = timezone.now()
        evs = [
            _make_event(
                facility,
                client_identified,
                doc_type_contact,
                staff_user,
                occurred_at=now - timedelta(hours=i),
            )
            for i in range(3)
        ]
        events_data, event_ids = _gather_events(client_identified, staff_user)
        assert len(events_data) == len(event_ids) == 3
        # Parallel-Reihenfolge: events_data und event_ids referenzieren das
        # selbe Event an Position i (neueste zuerst → evs[0]).
        assert event_ids[0] == evs[0].pk

    def test_equal_timestamps_are_broken_by_pk(self, facility, client_identified, doc_type_contact, staff_user):
        """Refs #1752: ``order_by("-occurred_at", "pk")``.

        Mutation → ``order_by("-occurred_at")`` ließe die Reihenfolge bei
        zeitgleichen Ereignissen der Datenbank überlassen; zwei Auskünfte über
        denselben Bestand wären dann nicht mehr vergleichbar.

        Die Einfüge-Reihenfolge läuft bewusst **gegen** die pk-Reihenfolge:
        ohne Tiebreak liefert Postgres die Zeilen in Scan-Reihenfolge, der
        Test bliebe sonst auch ohne ihn zufällig grün.
        """
        moment = timezone.now()
        pks = [uuid.UUID(f"{n:08x}-1752-4000-8000-000000000000") for n in range(4)]
        for pk in reversed(pks):
            Event.objects.create(
                id=pk,
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=moment,
                data_json={},
                created_by=staff_user,
            )
        _events_data, event_ids = _gather_events(client_identified, staff_user)
        assert event_ids == pks


# ---------------------------------------------------------------------------
# _gather_cases — Field-Selection, Filter, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherCases:
    """Refs ``_gather_cases`` (Line 64).

    Mutationen im Dict-Literal entfernen einzelne Case-Felder. Wir
    pinnen jedes Feld einzeln + Sort + Filter-Boundary.
    """

    def test_empty_when_no_cases(self, client_identified):
        assert _gather_cases(client_identified) == []

    def test_contains_all_eleven_fields(self, facility, client_identified, staff_user):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher sieben Felder — ``updated_at`` und die Papierkorb-Metadaten
        fehlten."""
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="T",
            description="D",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        result = _gather_cases(client_identified)
        assert len(result) == 1
        entry = result[0]
        assert set(entry.keys()) == {
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
            "closed_at",
            "lead_user",
            "created_by",
            "is_deleted",
            "deleted_at",
            "deleted_by",
        }

    def test_title_preserved(self, facility, client_identified, staff_user):
        CaseModel.objects.create(facility=facility, client=client_identified, title="Mein Fall", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["title"] == "Mein Fall"

    def test_description_preserved(self, facility, client_identified, staff_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            description="Lange Beschreibung",
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["description"] == "Lange Beschreibung"

    def test_status_uses_display_label(self, facility, client_identified, staff_user):
        """Mutation ``get_status_display()`` → ``status`` würde
        ``"open"`` statt ``"Offen"`` liefern."""
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["status"] == "Offen"

    def test_status_closed_label(self, facility, client_identified, staff_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.CLOSED,
            closed_at=timezone.now(),
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["status"] == "Geschlossen"

    def test_created_at_isoformat(self, facility, client_identified, staff_user):
        c = CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        result = _gather_cases(client_identified)[0]
        assert isinstance(result["created_at"], str)
        assert result["created_at"] == c.created_at.isoformat()

    def test_closed_at_none_when_open(self, facility, client_identified, staff_user):
        """Mutation ``... if case.closed_at else None`` → fester Wert würde
        bei offenen Fällen einen falschen Timestamp liefern."""
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.OPEN,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["closed_at"] is None

    def test_closed_at_isoformat_when_set(self, facility, client_identified, staff_user):
        t = timezone.now()
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            status=CaseModel.Status.CLOSED,
            closed_at=t,
            created_by=staff_user,
        )
        result = _gather_cases(client_identified)[0]
        assert isinstance(result["closed_at"], str)
        assert result["closed_at"] == t.isoformat()

    def test_lead_user_username(self, facility, client_identified, staff_user, lead_user):
        CaseModel.objects.create(
            facility=facility,
            client=client_identified,
            title="X",
            lead_user=lead_user,
            created_by=staff_user,
        )
        assert _gather_cases(client_identified)[0]["lead_user"] == lead_user.username

    def test_lead_user_none_when_unset(self, facility, client_identified, staff_user):
        """Mutation ``... if case.lead_user else None`` → fester Wert würde
        bei fehlendem Lead crashen oder falsche Werte liefern."""
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["lead_user"] is None

    def test_created_by_username(self, facility, client_identified, staff_user):
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        assert _gather_cases(client_identified)[0]["created_by"] == staff_user.username

    def test_created_by_none_when_unset(self, facility, client_identified):
        """``created_by`` ist optional (SET_NULL) — None muss durchgereicht werden."""
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=None)
        assert _gather_cases(client_identified)[0]["created_by"] is None

    def test_excludes_cases_of_other_clients(self, facility, client_identified, client_qualified, staff_user):
        """``filter(client=client)``-Boundary: Cross-Klient-Daten müssen
        unsichtbar bleiben."""
        own = CaseModel.objects.create(facility=facility, client=client_identified, title="Own", created_by=staff_user)
        CaseModel.objects.create(facility=facility, client=client_qualified, title="Other", created_by=staff_user)
        results = _gather_cases(client_identified)
        assert len(results) == 1
        assert results[0]["title"] == "Own"
        assert own  # noqa: B015 — gegen Lint-Warning

    def test_order_descending_by_created_at(self, facility, client_identified, staff_user):
        """Mutation ``-created_at`` → ``created_at`` würde die Reihenfolge
        invertieren (ältester zuerst)."""
        c1 = CaseModel.objects.create(facility=facility, client=client_identified, title="Erst", created_by=staff_user)
        c2 = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Zweite", created_by=staff_user
        )
        c3 = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Dritte", created_by=staff_user
        )
        # auto_now_add → c3 > c2 > c1
        result = _gather_cases(client_identified)
        titles = [r["title"] for r in result]
        assert titles == ["Dritte", "Zweite", "Erst"]
        assert c1 and c2 and c3  # noqa: B015

    # -- Refs #1752: Änderungszeitpunkt + Papierkorb -------------------------

    def test_updated_at_is_isoformat_and_independent_of_created_at(self, facility, client_identified, staff_user):
        """Mutation ``case.updated_at`` → ``case.created_at`` würde die letzte
        Fall-Änderung verschweigen."""
        case = CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        case.description = "Nachtrag"
        case.save(update_fields=["description", "updated_at"])
        case.refresh_from_db()
        result = _gather_cases(client_identified)[0]
        assert isinstance(result["updated_at"], str)
        assert result["updated_at"] == case.updated_at.isoformat()
        assert result["updated_at"] != result["created_at"]

    def test_trash_metadata_defaults_for_live_case(self, facility, client_identified, staff_user):
        CaseModel.objects.create(facility=facility, client=client_identified, title="X", created_by=staff_user)
        result = _gather_cases(client_identified)[0]
        assert result["is_deleted"] is False
        assert result["deleted_at"] is None
        assert result["deleted_by"] is None

    def test_soft_deleted_case_is_exported_with_trash_metadata(self, facility, client_identified, staff_user):
        """``Case.objects`` filtert Soft-Deletes nicht heraus — der Fall bleibt
        bis zum Hard-Purge in der Auskunft und muss als Papierkorb-Eintrag
        erkennbar sein."""
        case = CaseModel.objects.create(
            facility=facility, client=client_identified, title="Papierkorb-Fall", created_by=staff_user
        )
        case.soft_delete(user=staff_user)
        result = _gather_cases(client_identified)
        assert [r["title"] for r in result] == ["Papierkorb-Fall"]
        assert result[0]["is_deleted"] is True
        assert result[0]["deleted_at"] == case.deleted_at.isoformat()
        assert result[0]["deleted_by"] == staff_user.username


# ---------------------------------------------------------------------------
# _gather_event_history — IDs, Field-Selection, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherEventHistory:
    """Refs ``_gather_event_history`` (Line 81).

    Mutationen:
    - ``filter(event_id__in=event_ids)`` → ohne Filter → cross-Event-Leak
    - ``order_by("-changed_at")`` → ASC würde Doku-Reihenfolge kippen
    - Einzelne Dict-Felder entfernt
    - Refs #1752: Inhalte (``data_before``/``data_after``), Ereignis-Bezug
      und ``redacted``-Flag — Mutationen dort machen die Auskunft wieder
      zur reinen Metadaten-Liste
    """

    @staticmethod
    def _metadata(**slugs):
        """Label-Snapshot, wie ihn ``EventHistory.field_metadata`` speichert."""
        return {slug: {"name": name, "sensitivity": "", "is_encrypted": False} for slug, name in slugs.items()}

    def test_empty_when_no_history(self):
        assert _gather_event_history([]) == []

    def test_empty_event_ids_returns_empty(self, sample_event, staff_user):
        """``event_ids=[]`` → ``__in=[]`` → leeres Queryset.

        Mutation ``event_id__in`` → ``event_id`` (without lookup) würde
        bei einer leeren Liste in einen TypeError laufen oder alle
        History-Entries laden. Wir legen einen History-Entry an, der
        existiert, aber bei leerer ``event_ids``-Liste NICHT auftauchen
        darf — so wird klar, dass der Filter greift, nicht nur ein
        leeres Result aus leerer Tabelle kommt.
        """
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([]) == []

    def test_contains_all_seven_fields(self, sample_event, staff_user):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher drei Felder (nur Metadaten).

        Neu: der Ereignis-Bezug (``event``), das ``redacted``-Flag und die
        eigentlich auskunftspflichtigen Inhalte ``data_before``/``data_after``.
        """
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert len(result) == 1
        assert set(result[0].keys()) == {
            "action",
            "changed_at",
            "changed_by",
            "event",
            "redacted",
            "data_before",
            "data_after",
        }

    def test_action_uses_display_label(self, sample_event, staff_user):
        """Mutation ``get_action_display()`` → ``action`` würde ``"update"``
        statt ``"Aktualisiert"`` liefern."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert result[0]["action"] == "Aktualisiert"

    def test_action_create_label(self, sample_event, staff_user):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([sample_event.pk])[0]["action"] == "Erstellt"

    def test_changed_at_isoformat(self, sample_event, staff_user):
        h = EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
        )
        result = _gather_event_history([sample_event.pk])
        assert isinstance(result[0]["changed_at"], str)
        assert result[0]["changed_at"] == h.changed_at.isoformat()

    def test_changed_by_username(self, sample_event, staff_user):
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
        )
        assert _gather_event_history([sample_event.pk])[0]["changed_by"] == staff_user.username

    def test_changed_by_none_when_user_missing(self, sample_event):
        """Mutation ``... if entry.changed_by else None`` → fester Wert würde
        bei FK=NULL crashen."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=None,
        )
        assert _gather_event_history([sample_event.pk])[0]["changed_by"] is None

    def test_order_descending_by_changed_at(self, sample_event, staff_user):
        """Mutation ``-changed_at`` → ``changed_at`` würde älteste zuerst
        zeigen. Wir verifizieren ID-Reihenfolge bei auto_now_add."""
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        h_last = EventHistory.objects.create(
            event=sample_event, action=EventHistory.Action.UPDATE, changed_by=staff_user
        )
        result = _gather_event_history([sample_event.pk])
        # auto_now_add → der zuletzt erstellte Entry ist neuester →
        # mit -changed_at-Sort steht er an Position 0.
        # Falls Timestamps gleich sind, bleibt zumindest die Menge identisch.
        assert len(result) == 3
        # Bei strikter Monotonie: h_last steht oben.
        assert result[0]["changed_at"] >= result[-1]["changed_at"]
        assert h_last  # noqa: B015

    def test_excludes_history_of_other_events(self, facility, client_identified, doc_type_contact, staff_user):
        """``filter(event_id__in=event_ids)``-Boundary: History eines
        anderen Events darf nicht im Export auftauchen."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_identified, doc_type_contact, staff_user)
        EventHistory.objects.create(event=ev_own, action=EventHistory.Action.CREATE, changed_by=staff_user)
        EventHistory.objects.create(event=ev_other, action=EventHistory.Action.UPDATE, changed_by=staff_user)
        result = _gather_event_history([ev_own.pk])
        assert len(result) == 1
        assert result[0]["action"] == "Erstellt"

    # -- Refs #1752: Ereignis-Bezug ------------------------------------------

    def test_event_reference_contains_exactly_three_keys(self, sample_event, staff_user):
        """Der Ereignis-Bezug ordnet die Änderung zu — bleibt aber bewusst
        schmal: Mutationen, die hier ``data_json`` oder Anhang-Metadaten
        nachziehen, brechen den exakten Schlüsselsatz."""
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        entry = _gather_event_history([sample_event.pk])[0]
        assert set(entry["event"].keys()) == {"document_type", "occurred_at", "is_deleted"}

    def test_event_reference_uses_document_type_name_and_iso_timestamp(self, sample_event, staff_user):
        """Mutation ``entry.event.document_type.name`` → ``document_type_id``
        würde eine UUID statt eines lesbaren Namens liefern."""
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        entry = _gather_event_history([sample_event.pk])[0]
        assert entry["event"]["document_type"] == "Kontakt"
        assert entry["event"]["occurred_at"] == sample_event.occurred_at.isoformat()
        assert entry["event"]["is_deleted"] is False

    def test_event_reference_marks_deleted_events(self, facility, client_identified, doc_type_contact, staff_user):
        """Die Historie eines gelöschten Ereignisses bleibt in der Auskunft —
        Mutation ``entry.event.is_deleted`` → Konstante würde sie als Historie
        eines lebenden Datensatzes ausgeben."""
        deleted = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        EventHistory.objects.create(event=deleted, action=EventHistory.Action.DELETE, changed_by=staff_user)
        entry = _gather_event_history([deleted.pk])[0]
        assert entry["event"]["is_deleted"] is True

    # -- Refs #1752: Inhalte statt reiner Metadaten --------------------------

    def test_payload_lists_are_empty_without_snapshots(self, sample_event, staff_user):
        """``data_before``/``data_after`` sind ``null``-fähig — der Falsy-Branch
        von ``_serialize_history_payload`` muss ``[]`` liefern, nicht ``None``
        (sonst bricht der JSON-Download-Pfad die Kategorie auf)."""
        EventHistory.objects.create(event=sample_event, action=EventHistory.Action.CREATE, changed_by=staff_user)
        entry = _gather_event_history([sample_event.pk])[0]
        assert entry["data_before"] == []
        assert entry["data_after"] == []
        assert entry["redacted"] is False

    def test_data_after_carries_values_with_labels_sorted_by_slug(self, sample_event, staff_user):
        """Mutation ``_serialize_history_payload`` → leere Liste würde exakt
        die auskunftspflichtigen Inhalte unterschlagen; die Slug-Sortierung
        hält die Auskunft reproduzierbar (``jsonb`` normalisiert Schlüssel)."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
            data_after={"notiz": "Erstkontakt", "dauer": 15},
            field_metadata=self._metadata(notiz="Notiz", dauer="Dauer"),
        )
        entry = _gather_event_history([sample_event.pk])[0]
        assert entry["data_after"] == [
            {"slug": "dauer", "label": "Dauer", "value": 15},
            {"slug": "notiz", "label": "Notiz", "value": "Erstkontakt"},
        ]

    def test_data_before_and_after_are_not_swapped(self, sample_event, staff_user):
        """Mutation, die beide Snapshots auf dieselbe Quelle zieht, würde den
        Änderungsverlauf verfälschen."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"notiz": "alt"},
            data_after={"notiz": "neu"},
            field_metadata=self._metadata(notiz="Notiz"),
        )
        entry = _gather_event_history([sample_event.pk])[0]
        assert [f["value"] for f in entry["data_before"]] == ["alt"]
        assert [f["value"] for f in entry["data_after"]] == ["neu"]

    def test_label_falls_back_to_slug_without_metadata(self, sample_event, staff_user):
        """Mutation am ``or slug``-Fallback würde ``None`` als Label liefern —
        für die betroffene Person unlesbar."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.CREATE,
            changed_by=staff_user,
            data_after={"unbekannt": "Wert"},
            field_metadata={},
        )
        entry = _gather_event_history([sample_event.pk])[0]
        assert entry["data_after"] == [{"slug": "unbekannt", "label": "unbekannt", "value": "Wert"}]

    def test_redacted_delete_lists_field_names_without_values(self, sample_event, staff_user):
        """Mutation am ``_redacted``-Zweig würde entweder das Flag verschweigen
        oder bereits gelöschte Werte zurückholen."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.DELETE,
            changed_by=staff_user,
            data_before={"_redacted": True, "fields": ["notiz", "dauer"]},
            field_metadata=self._metadata(notiz="Notiz", dauer="Dauer"),
        )
        entry = _gather_event_history([sample_event.pk])[0]
        assert entry["redacted"] is True
        assert entry["data_before"] == [
            {"slug": "dauer", "label": "Dauer", "value": None},
            {"slug": "notiz", "label": "Notiz", "value": None},
        ]

    def test_attachment_markers_stay_placeholders(self, sample_event, staff_user):
        """Attachment-Metadaten bleiben bis #1738 (AP3) draußen — Mutation am
        Marker-Check (``__file__``/``__files__``) würde Anhang-IDs über den
        Umweg des Historien-Snapshots doch in die Auskunft spülen."""
        EventHistory.objects.create(
            event=sample_event,
            action=EventHistory.Action.UPDATE,
            changed_by=staff_user,
            data_before={"datei": {"__file__": True, "id": "vorher-id"}},
            data_after={"datei": {"__files__": True, "entries": [{"id": "nachher-id", "sort": 0}]}},
            field_metadata=self._metadata(datei="Datei"),
        )
        entry = _gather_event_history([sample_event.pk])[0]
        assert [f["value"] for f in entry["data_before"]] == ["[Anhang]"]
        assert [f["value"] for f in entry["data_after"]] == ["[Anhang]"]
        assert "vorher-id" not in str(entry)
        assert "nachher-id" not in str(entry)

    def test_encrypted_history_value_is_decrypted(self, sample_event, staff_user):
        """Mutation ``safe_decrypt(raw)`` → ``raw`` würde ein Fernet-Token
        statt Klartext ausweisen — das wäre keine Auskunft."""
        from core.services.file_vault import encrypt_field, generate_key

        with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
            EventHistory.objects.create(
                event=sample_event,
                action=EventHistory.Action.CREATE,
                changed_by=staff_user,
                data_after={"diagnose": encrypt_field("Klartext-Befund")},
                field_metadata=self._metadata(diagnose="Diagnose"),
            )
            entry = _gather_event_history([sample_event.pk])[0]
        assert [f["value"] for f in entry["data_after"]] == ["Klartext-Befund"]


# ---------------------------------------------------------------------------
# _gather_deletion_requests — Filter + Field-Selection
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherDeletionRequests:
    """Refs ``_gather_deletion_requests`` (Line 96).

    Signatur geändert per Produktentscheid 2026-08-03 (Refs #1752):
    ``(facility, client, event_ids)`` statt ``(event_ids)``. Der Filter war
    hart auf ``target_type="Event"`` verdrahtet — der Vier-Augen-Antrag auf
    Löschung der eigenen Person (``target_type="Client"``) fehlte damit
    ausgerechnet in der Auskunft.

    Mutationen:
    - ``target_id__in=event_ids`` → falsche Boundary leaked
    - der ``Q(...) | Q(...)``-Zweig: fällt der Client-Zweig weg, fehlt der
      Personen-Löschantrag; fällt der Event-Zweig weg, fehlen die
      Ereignis-Anträge
    - ``target_id=client.pk`` im Client-Zweig → fremde Anträge leaken
    - ``for_facility(facility)`` → einrichtungsübergreifender Leak
    - Einzelne Felder entfernt
    - ``order_by("-created_at")``
    """

    def test_empty_when_no_requests(self, facility, client_identified):
        assert _gather_deletion_requests(facility, client_identified, []) == []

    def test_contains_all_seven_fields(self, facility, client_identified, sample_event, staff_user, lead_user):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher sechs Felder — ``target_type`` fehlte.

        Ohne ihn ließe sich seit der Client-Erweiterung nicht mehr
        unterscheiden, ob sich ein Antrag auf ein Ereignis oder auf die Person
        selbst bezieht.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="GDPR",
            requested_by=staff_user,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
            status=DeletionRequest.Status.APPROVED,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])
        assert len(result) == 1
        assert set(result[0].keys()) == {
            "target_type",
            "status",
            "reason",
            "requested_by",
            "reviewed_by",
            "created_at",
            "reviewed_at",
        }

    def test_status_uses_display_label(self, facility, client_identified, sample_event, staff_user):
        """Mutation ``get_status_display()`` → ``status`` würde Roh-Slug liefern."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
        assert _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]["status"] == "Ausstehend"

    def test_status_approved_label(self, facility, client_identified, sample_event, staff_user, lead_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            reviewed_by=lead_user,
            status=DeletionRequest.Status.APPROVED,
            reviewed_at=timezone.now(),
        )
        assert _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]["status"] == "Genehmigt"

    def test_target_type_uses_display_label(self, facility, client_identified, sample_event, staff_user):
        """Mutation ``get_target_type_display()`` → ``target_type`` würde das
        interne ``"Client"`` statt „Person" liefern — für die betroffene
        Person nicht lesbar."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]["target_type"] == "Event"

    def test_client_target_request_is_included_with_person_label(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Refs #1752: der Personen-Löschantrag fehlte komplett, während der
        PDF-Abschnitt „Löschanträge" Vollständigkeit suggerierte.

        Mutation, die den ``Q(target_type=CLIENT, ...)``-Zweig entfernt,
        bringt genau diese Lücke zurück.
        """
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_identified.pk,
            reason="Betroffene hat Löschung verlangt",
            requested_by=staff_user,
            reviewed_by=lead_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [])
        assert len(result) == 1
        assert result[0]["target_type"] == "Person"
        assert result[0]["reason"] == "Betroffene hat Löschung verlangt"

    def test_client_request_of_other_client_excluded(self, facility, client_identified, client_qualified, staff_user):
        """Mutation ``target_id=client.pk`` → weggelassen/andere Quelle würde
        fremde Personen-Löschanträge in die Auskunft spülen."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            reason="Fremder Antrag",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests(facility, client_identified, []) == []

    def test_reason_preserved(self, facility, client_identified, sample_event, staff_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Klient hat Auskunft beantragt",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])
        assert result[0]["reason"] == "Klient hat Auskunft beantragt"

    def test_requested_by_username(self, facility, client_identified, sample_event, staff_user):
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])
        assert result[0]["requested_by"] == staff_user.username

    def test_reviewed_by_none_when_pending(self, facility, client_identified, sample_event, staff_user):
        """Mutation ``... if dr.reviewed_by else None`` → fester Wert würde
        bei PENDING crashen."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            status=DeletionRequest.Status.PENDING,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]
        assert result["reviewed_by"] is None
        assert result["reviewed_at"] is None

    def test_reviewed_by_username_when_approved(self, facility, client_identified, sample_event, staff_user, lead_user):
        t = timezone.now()
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
            reviewed_by=lead_user,
            status=DeletionRequest.Status.APPROVED,
            reviewed_at=t,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]
        assert result["reviewed_by"] == lead_user.username
        assert result["reviewed_at"] == t.isoformat()

    def test_created_at_isoformat(self, facility, client_identified, sample_event, staff_user):
        dr = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="X",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])[0]
        assert result["created_at"] == dr.created_at.isoformat()

    def test_filter_target_id_in_event_ids(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``target_id__in=event_ids`` → ohne Filter würde alle
        DeletionRequests laden — auch von anderen Events."""
        ev_own = _make_event(facility, client_identified, doc_type_contact, staff_user)
        ev_other = _make_event(facility, client_identified, doc_type_contact, staff_user)
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=ev_own.pk,
            reason="Own",
            requested_by=staff_user,
        )
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=ev_other.pk,
            reason="Other",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [ev_own.pk])
        assert len(result) == 1
        assert result[0]["reason"] == "Own"

    def test_client_request_pointing_at_an_event_id_stays_out(
        self, facility, client_identified, sample_event, staff_user
    ):
        """Die beiden ``Q``-Zweige sind an ihren Zieltyp gebunden: ein
        CLIENT-Antrag auf eine Ereignis-UUID gehört in keine der beiden Mengen.

        Mutation, die ``target_type`` aus einem Zweig entfernt (oder die Zweige
        auf ``target_id__in`` verschmilzt), würde ihn durchlassen.
        """
        # CLIENT-DR mit IDENTISCHEM target_id wie sample_event.pk
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=sample_event.pk,  # bewusst gleicher UUID
            reason="Client-DR — soll im Event-Export NICHT auftauchen",
            requested_by=staff_user,
        )
        # EVENT-DR — soll auftauchen.
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Echter Event-DR",
            requested_by=staff_user,
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])
        assert len(result) == 1
        assert result[0]["reason"] == "Echter Event-DR"

    def test_event_request_pointing_at_the_client_id_stays_out(self, facility, client_identified, staff_user):
        """Gegenprobe zum Client-Zweig: Mutation ``Q(target_type=CLIENT,
        target_id=client.pk)`` → ``Q(target_id=client.pk)`` würde einen
        EVENT-Antrag auf die Personen-UUID durchlassen, obwohl das Ereignis
        gar nicht zur Auskunft gehört."""
        DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=client_identified.pk,  # bewusst die Personen-UUID
            reason="Event-DR — soll im Personen-Zweig NICHT auftauchen",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests(facility, client_identified, []) == []

    def test_requests_of_other_facility_excluded(
        self, facility, other_facility, client_identified, sample_event, staff_user
    ):
        """Mutation ``for_facility(facility)`` → ``all()`` würde den
        Einrichtungs-Scope (Defense-in-Depth zusätzlich zu RLS) aufheben —
        ``target_id`` allein trägt den Schutz nicht."""
        DeletionRequest.objects.create(
            facility=other_facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="Fremde Einrichtung",
            requested_by=staff_user,
        )
        assert _gather_deletion_requests(facility, client_identified, [sample_event.pk]) == []

    def test_order_descending_by_created_at(self, facility, client_identified, sample_event, staff_user, lead_user):
        """Mutation ``-created_at`` → ``created_at`` würde die Reihenfolge
        invertieren."""
        d1 = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="A",
            requested_by=staff_user,
            status=DeletionRequest.Status.REJECTED,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
        )
        # zweiter DR — partial unique blockiert nur PENDING, deshalb
        # beide REJECTED.
        d2 = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.EVENT,
            target_id=sample_event.pk,
            reason="B",
            requested_by=staff_user,
            status=DeletionRequest.Status.REJECTED,
            reviewed_by=lead_user,
            reviewed_at=timezone.now(),
        )
        result = _gather_deletion_requests(facility, client_identified, [sample_event.pk])
        # auto_now_add → d2 später als d1 → d2 zuerst.
        reasons = [r["reason"] for r in result]
        assert reasons[0] == "B"
        assert reasons[-1] == "A"
        assert d1 and d2  # noqa: B015


# ---------------------------------------------------------------------------
# _gather_workitems — Field-Selection, Filter, Sort
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGatherWorkItems:
    """Refs ``_gather_workitems`` (Line 116).

    Mutationen:
    - ``filter(client=client)`` → cross-Klient-Leak
    - ``order_by("-created_at")`` → ASC würde älteste zuerst zeigen
    - Einzelne Felder entfernt
    """

    def test_empty_when_no_workitems(self, client_identified):
        assert _gather_workitems(client_identified) == []

    def test_contains_all_sixteen_fields(self, sample_workitem):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher sechs Felder.

        Neu: Typ, handelnde Personen (``created_by``/``assigned_to``), die
        übrigen Fristen (``completed_at``/``remind_at``), ``recurrence``,
        ``updated_at`` und die Papierkorb-Metadaten.
        """
        result = _gather_workitems(sample_workitem.client)
        assert len(result) == 1
        assert set(result[0].keys()) == {
            "title",
            "description",
            "item_type",
            "status",
            "priority",
            "created_by",
            "assigned_to",
            "created_at",
            "updated_at",
            "completed_at",
            "due_date",
            "remind_at",
            "recurrence",
            "is_deleted",
            "deleted_at",
            "deleted_by",
        }

    def test_title_preserved(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)
        assert result[0]["title"] == "Test-Aufgabe"

    def test_description_preserved(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            description="Lange Workitem-Beschreibung",
        )
        assert _gather_workitems(client_identified)[0]["description"] == ("Lange Workitem-Beschreibung")

    def test_status_uses_display_label(self, sample_workitem):
        """Mutation ``get_status_display()`` → ``status`` würde Slug
        statt i18n-Label liefern."""
        result = _gather_workitems(sample_workitem.client)
        assert result[0]["status"] == "Offen"

    def test_status_done_label(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            status=WorkItem.Status.DONE,
        )
        assert _gather_workitems(client_identified)[0]["status"] == "Erledigt"

    def test_priority_uses_display_label(self, facility, client_identified, staff_user):
        """Mutation ``get_priority_display()`` → ``priority`` würde Slug
        liefern."""
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            priority=WorkItem.Priority.URGENT,
        )
        assert _gather_workitems(client_identified)[0]["priority"] == "Dringend"

    def test_priority_normal_label(self, sample_workitem):
        assert _gather_workitems(sample_workitem.client)[0]["priority"] == "Normal"

    def test_created_at_isoformat(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)
        assert isinstance(result[0]["created_at"], str)
        assert result[0]["created_at"] == sample_workitem.created_at.isoformat()

    def test_due_date_none_when_unset(self, sample_workitem):
        """Mutation ``... if wi.due_date else None`` → fester Wert würde
        bei fehlendem due_date crashen."""
        assert _gather_workitems(sample_workitem.client)[0]["due_date"] is None

    def test_due_date_isoformat_when_set(self, facility, client_identified, staff_user):
        due = timezone.now().date() + timedelta(days=7)
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            due_date=due,
        )
        result = _gather_workitems(client_identified)[0]
        assert result["due_date"] == due.isoformat()

    def test_excludes_workitems_of_other_clients(self, facility, client_identified, client_qualified, staff_user):
        """``filter(client=client)``-Boundary: Cross-Klient-Daten dürfen
        nicht in den Export leaken."""
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="Own",
        )
        WorkItem.objects.create(
            facility=facility,
            client=client_qualified,
            created_by=staff_user,
            title="Other",
        )
        result = _gather_workitems(client_identified)
        assert len(result) == 1
        assert result[0]["title"] == "Own"

    def test_order_descending_by_created_at(self, facility, client_identified, staff_user):
        """Mutation ``-created_at`` → ``created_at`` würde älteste zuerst
        liefern."""
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Erst")
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Zweite")
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=staff_user, title="Dritte")
        result = _gather_workitems(client_identified)
        titles = [r["title"] for r in result]
        assert titles == ["Dritte", "Zweite", "Erst"]

    # -- Refs #1752: Typ, handelnde Personen, Fristen, Papierkorb ------------

    def test_item_type_uses_display_label(self, sample_workitem):
        """Mutation ``get_item_type_display()`` → ``item_type`` würde den
        Roh-Slug ``"task"`` statt „Aufgabe" liefern."""
        assert _gather_workitems(sample_workitem.client)[0]["item_type"] == "Aufgabe"

    def test_item_type_hint_label(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            item_type=WorkItem.ItemType.HINT,
        )
        assert _gather_workitems(client_identified)[0]["item_type"] == "Hinweis"

    def test_created_by_username(self, sample_workitem, staff_user):
        assert _gather_workitems(sample_workitem.client)[0]["created_by"] == staff_user.username

    def test_created_by_none_when_user_missing(self, facility, client_identified):
        """``created_by`` ist ``SET_NULL`` — Mutation ``_username`` → fester
        Wert würde bei FK=NULL crashen."""
        WorkItem.objects.create(facility=facility, client=client_identified, created_by=None, title="X")
        assert _gather_workitems(client_identified)[0]["created_by"] is None

    def test_assigned_to_username(self, facility, client_identified, staff_user, lead_user):
        """Mutation ``wi.assigned_to`` → ``wi.created_by`` würde die zuständige
        Person mit der erstellenden verwechseln."""
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            assigned_to=lead_user,
            title="X",
        )
        result = _gather_workitems(client_identified)[0]
        assert result["assigned_to"] == lead_user.username
        assert result["created_by"] == staff_user.username

    def test_assigned_to_none_when_unassigned(self, sample_workitem):
        assert _gather_workitems(sample_workitem.client)[0]["assigned_to"] is None

    def test_updated_at_is_isoformat_and_independent_of_created_at(self, sample_workitem):
        """Mutation ``wi.updated_at`` → ``wi.created_at`` würde die letzte
        Änderung verschweigen."""
        sample_workitem.description = "Nachtrag"
        sample_workitem.save(update_fields=["description", "updated_at"])
        sample_workitem.refresh_from_db()
        result = _gather_workitems(sample_workitem.client)[0]
        assert isinstance(result["updated_at"], str)
        assert result["updated_at"] == sample_workitem.updated_at.isoformat()
        assert result["updated_at"] != result["created_at"]

    def test_completed_at_none_when_open(self, sample_workitem):
        assert _gather_workitems(sample_workitem.client)[0]["completed_at"] is None

    def test_completed_at_isoformat_when_set(self, facility, client_identified, staff_user):
        t = timezone.now()
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            status=WorkItem.Status.DONE,
            completed_at=t,
        )
        assert _gather_workitems(client_identified)[0]["completed_at"] == t.isoformat()

    def test_remind_at_none_when_unset(self, sample_workitem):
        assert _gather_workitems(sample_workitem.client)[0]["remind_at"] is None

    def test_remind_at_isoformat_when_set(self, facility, client_identified, staff_user):
        """Mutation ``wi.remind_at`` → ``wi.due_date`` würde Wiedervorlage und
        Frist vermischen — zwei verschiedene gespeicherte Daten."""
        remind = timezone.now().date() + timedelta(days=3)
        due = timezone.now().date() + timedelta(days=10)
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            remind_at=remind,
            due_date=due,
        )
        result = _gather_workitems(client_identified)[0]
        assert result["remind_at"] == remind.isoformat()
        assert result["due_date"] == due.isoformat()

    def test_recurrence_uses_display_label(self, sample_workitem):
        """Mutation ``get_recurrence_display()`` → ``recurrence`` würde
        ``"none"`` statt „Keine" liefern."""
        assert _gather_workitems(sample_workitem.client)[0]["recurrence"] == "Keine"

    def test_recurrence_weekly_label(self, facility, client_identified, staff_user):
        WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            title="X",
            recurrence=WorkItem.Recurrence.WEEKLY,
        )
        assert _gather_workitems(client_identified)[0]["recurrence"] == "Wöchentlich"

    def test_trash_metadata_defaults_for_live_workitem(self, sample_workitem):
        result = _gather_workitems(sample_workitem.client)[0]
        assert result["is_deleted"] is False
        assert result["deleted_at"] is None
        assert result["deleted_by"] is None

    def test_soft_deleted_workitem_is_exported_with_trash_metadata(self, sample_workitem, staff_user):
        """``WorkItem.objects`` filtert Soft-Deletes nicht heraus — die Aufgabe
        bleibt bis zum Hard-Purge in der Auskunft und muss als
        Papierkorb-Eintrag erkennbar sein."""
        sample_workitem.soft_delete(user=staff_user)
        result = _gather_workitems(sample_workitem.client)
        assert len(result) == 1
        assert result[0]["is_deleted"] is True
        assert result[0]["deleted_at"] == sample_workitem.deleted_at.isoformat()
        assert result[0]["deleted_by"] == staff_user.username

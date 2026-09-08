"""Mutation-Followup-Tests für ``core.services.client.export`` — Serializer.

Refs #930. Sub-File aus ``test_mutation_followup_client_export``;
enthält die Test-Klasse ``TestSerializeEvent`` (Event-pro-Eintrag-
Serialisierung inkl. Decryption-Branches).

Der Sollzustand der Auskunft ist am 2026-08-03 per Produktentscheid
gewachsen (Refs #1752, #1755):
jeder Ereignis-Eintrag trägt zusätzlich Fall-/Episoden-Bezug, Anonym-Flag,
Papierkorb-Status und beide Zeitstempel. Die Pins hier folgen dieser
Anforderungsänderung — **ohne** ihre Schärfe aufzugeben: der Schlüsselsatz
bleibt exakt gepinnt und jedes neue Feld hat seinen eigenen Boundary-Test.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import (
    Case as CaseModel,
)
from core.models import (
    DocumentType,
    Episode,
    Event,
)
from core.services.client import (
    _serialize_event,
)
from core.services.file_vault import encrypt_field
from tests._mutation_followup_client_export_helpers import (
    _make_doc_type,
    _make_event,
)


@pytest.fixture
def case_with_episode(facility, client_identified, staff_user):
    """Fall + Episode zum Anhängen an ein Ereignis (Refs #1752)."""
    case = CaseModel.objects.create(
        facility=facility,
        client=client_identified,
        title="Wohnungsnotfall",
        created_by=staff_user,
    )
    episode = Episode.objects.create(
        case=case,
        title="Clearing-Phase",
        started_at=timezone.now().date(),
        created_by=staff_user,
    )
    return case, episode


# ---------------------------------------------------------------------------
# _serialize_event — pro-Event-Serialisierung inkl. Decryption
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializeEvent:
    """Refs ``_serialize_event`` (Line 24).

    Branches:
    - ``if event.data_json:`` (truthy)
    - ``isinstance(value, dict)`` → safe_decrypt
    - else-Branch → 1:1 passthrough
    - ``event.created_by`` ``None``-Fallback
    - ``event.case`` / ``event.episode`` ``None``-Fallback (Refs #1752)
    """

    BASE_KEYS = {
        "document_type",
        "occurred_at",
        "created_by",
        "data",
        "case",
        "episode",
        "is_anonymous",
        "is_deleted",
        # Refs #2157: Papierkorb-Metadaten wie bei Person und Fall — der Pin
        # zieht die neuen Keys nach (Ticket-Auftrag „anpassen, nicht
        # abschwächen"), der Vergleich bleibt exakt.
        "deleted_at",
        "deleted_by",
        "created_at",
        "updated_at",
    }

    def test_contains_all_ten_keys(self, sample_event):
        """Anforderung geändert per Produktentscheid 2026-08-03 (Refs #1752):
        vorher pinnte dieser Test vier Keys.

        Anforderung erneut geändert mit M24 (Refs #1738, ADR-039):
        Attachment-Metadaten sind jetzt Teil der Auskunft — vorher pinnte der
        Test hier ihre Abwesenheit. Der Schlüsselsatz eines Ereignisses **ohne**
        Dateien bleibt aber exakt derselbe: ``attachments`` kommt nur dazu, wenn
        es Anhänge gibt (``Key nur wenn nicht leer``), damit das Bestandsformat
        bitgleich bleibt.

        Anforderung erneut geändert mit #2157: ``deleted_at``/``deleted_by``
        kommen dazu, damit die Art.-15-Auskunft bei Ereignissen im Papierkorb
        denselben Löschzeitpunkt und -auslöser ausweist wie bei Fällen und
        Personen. Der Test heißt weiter ``…_all_ten_keys``, weil sein
        Bezeichner in Pin-Protokollen früherer Wellen steht; gepinnt ist
        ``BASE_KEYS``, nicht die Zahl im Namen (heute zwölf).

        Der Pin bleibt exakt (``==``, nicht ``<=``): fällt ein Key weg, bricht
        er — kommt ein ungewollter dazu, ebenfalls.
        """
        result = _serialize_event(sample_event)
        assert set(result.keys()) == self.BASE_KEYS

    def test_attachments_key_appears_only_with_attachments(self, sample_event, facility, staff_user):
        """Gegenstück zum Pin oben: mit Anhang genau EIN zusätzlicher Key.

        Ohne diesen Test bliebe der exakte Schlüsselsatz oben grün, wenn der
        Anhang-Zweig ganz verschwände — der Ereignis-Anhang fiele dann
        unbemerkt wieder aus der Auskunft (die Lücke, die M24 geschlossen hat).
        """
        from core.models import Attachment, FieldTemplate
        from core.services.file_vault import encrypt_field

        Attachment.objects.create(
            facility=facility,
            event=sample_event,
            field_template=FieldTemplate.objects.create(
                facility=facility,
                name="Serializer-Anlage",
                field_type=FieldTemplate.FieldType.FILE,
            ),
            storage_filename="s.enc",
            original_filename_encrypted=encrypt_field("Anlage.pdf"),
            file_size=7,
            mime_type="application/pdf",
            created_by=staff_user,
        )
        result = _serialize_event(sample_event)
        assert set(result.keys()) == self.BASE_KEYS | {"attachments"}
        assert [a["filename"] for a in result["attachments"]] == ["Anlage.pdf"]

    def test_document_type_uses_name_not_pk(self, sample_event):
        """Mutation ``event.document_type.name`` → ``event.document_type_id``
        würde eine UUID statt einem lesbaren Namen liefern."""
        result = _serialize_event(sample_event)
        assert result["document_type"] == sample_event.document_type.name
        assert result["document_type"] == "Kontakt"

    def test_occurred_at_is_isoformat(self, sample_event):
        result = _serialize_event(sample_event)
        assert isinstance(result["occurred_at"], str)
        assert result["occurred_at"] == sample_event.occurred_at.isoformat()
        assert "T" in result["occurred_at"]

    def test_created_by_username(self, sample_event, staff_user):
        result = _serialize_event(sample_event)
        assert result["created_by"] == staff_user.username

    def test_created_by_none_when_user_missing(self, facility, client_identified, doc_type_contact):
        """Mutation ``if event.created_by else None`` → fester Wert würde
        ``None``-Branch verändern."""
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=None,
        )
        result = _serialize_event(ev)
        assert result["created_by"] is None

    def test_empty_data_json_yields_empty_data_dict(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``if event.data_json:`` (Negation) würde non-empty
        Dicts überspringen oder leere durchwinken — hier prüfen wir den
        Falsy-Branch explizit."""
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user, data_json={})
        result = _serialize_event(ev)
        assert result["data"] == {}

    def test_plain_value_passes_through_untouched(self, facility, client_identified, doc_type_contact, staff_user):
        """Non-dict-Werte landen 1:1 in ``data`` — der ``isinstance(value, dict)``-
        Check ist die Boundary."""
        ev = _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            data_json={"dauer": 42, "notiz": "hello"},
        )
        result = _serialize_event(ev)
        assert result["data"]["dauer"] == 42
        assert result["data"]["notiz"] == "hello"

    def test_dict_value_without_encryption_marker_passes_through(
        self, facility, client_identified, doc_type_contact, staff_user
    ):
        """``safe_decrypt`` liefert das Dict unverändert zurück, wenn kein
        ``__encrypted__``-Marker drin ist. So fangen wir den
        ``isinstance(value, dict)``-Branch, ohne Fernet-Keys aufsetzen
        zu müssen."""
        payload = {"some": "nested", "n": 1}
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": payload},
            created_by=staff_user,
        )
        result = _serialize_event(ev)
        assert result["data"]["notiz"] == payload

    def test_encrypted_dict_value_is_decrypted(self, facility, client_identified, staff_user):
        """Mutation ``safe_decrypt(value)`` → ``value`` würde den Ciphertext
        statt Klartext im Export liefern — kritisch für DSGVO Art. 15."""
        doc_type = _make_doc_type(facility, name="Krise", sensitivity=DocumentType.Sensitivity.HIGH)
        # Verschlüsselter Marker; safe_decrypt → Klartext
        encrypted_value = encrypt_field("geheimer-text")
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"krise-notiz": encrypted_value},
            created_by=staff_user,
        )
        result = _serialize_event(ev)
        assert result["data"]["krise-notiz"] == "geheimer-text"

    # -- Refs #1752: Fall-/Episoden-Bezug -----------------------------------

    def test_case_none_when_event_has_no_case(self, sample_event):
        """Mutation ``event.case.title if event.case else None`` → fester Wert
        würde bei fallfreien Ereignissen einen Fall behaupten, den es nicht
        gibt."""
        assert _serialize_event(sample_event)["case"] is None

    def test_case_uses_title_not_pk(self, facility, client_identified, doc_type_contact, staff_user, case_with_episode):
        """Mutation ``event.case.title`` → ``event.case_id`` würde eine UUID
        statt eines lesbaren Falltitels liefern."""
        case, _episode = case_with_episode
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            case=case,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        assert _serialize_event(ev)["case"] == "Wohnungsnotfall"

    def test_episode_none_when_event_has_no_episode(self, sample_event):
        """Mutation ``event.episode.title if event.episode else None`` → fester
        Wert würde bei episodenfreien Ereignissen crashen oder falsch melden."""
        assert _serialize_event(sample_event)["episode"] is None

    def test_episode_uses_title_not_pk(
        self, facility, client_identified, doc_type_contact, staff_user, case_with_episode
    ):
        case, episode = case_with_episode
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            case=case,
            episode=episode,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        assert _serialize_event(ev)["episode"] == "Clearing-Phase"

    # -- Refs #1752: Anonym-Flag, Papierkorb-Status, Zeitstempel -------------

    def test_is_anonymous_false_for_personalised_event(self, sample_event):
        assert _serialize_event(sample_event)["is_anonymous"] is False

    def test_is_anonymous_true_is_preserved(self, facility, client_identified, doc_type_contact, staff_user):
        """Mutation ``event.is_anonymous`` → Negation/Konstante würde ein
        anonym erfasstes Ereignis als personenbezogen ausweisen."""
        ev = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=staff_user,
        )
        assert _serialize_event(ev)["is_anonymous"] is True, "Bool muss 1:1 durchgereicht werden, nicht negiert"

    def test_is_deleted_false_for_live_event(self, sample_event):
        assert _serialize_event(sample_event)["is_deleted"] is False

    def test_is_deleted_true_for_trashed_event(self, facility, client_identified, doc_type_contact, staff_user):
        """Der Papierkorb-Abschnitt der Auskunft ist nur dann ehrlich, wenn das
        Flag am Eintrag hängt — Mutation zur Konstante ``False`` ließe einen
        gelöschten Datensatz als lebend erscheinen."""
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user, is_deleted=True)
        assert _serialize_event(ev)["is_deleted"] is True

    def test_created_at_is_isoformat(self, sample_event):
        result = _serialize_event(sample_event)
        assert isinstance(result["created_at"], str)
        assert result["created_at"] == sample_event.created_at.isoformat()
        assert "T" in result["created_at"]

    def test_updated_at_is_isoformat(self, sample_event):
        """Mutation ``event.updated_at`` → ``event.created_at`` würde die
        letzte Änderung verschweigen — beide Zeitstempel müssen eigenständig
        gepinnt sein."""
        sample_event.data_json = {"dauer": 99}
        sample_event.save()
        sample_event.refresh_from_db()
        result = _serialize_event(sample_event)
        assert isinstance(result["updated_at"], str)
        assert result["updated_at"] == sample_event.updated_at.isoformat()
        assert result["updated_at"] != result["created_at"]

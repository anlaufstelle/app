"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Batch ``events-3``
(Vollmutation-Survivor-Triage), Modul ``core.services.events.crud``
(Ereignis-CRUD: ``create_event``, ``update_event``, ``soft_delete_event``,
Fall-/Kontaktstufen-Validierung, Klartext-Entschlüsselung, Löschgates).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-events-3.json`` (Triage
``triage-099-events-3.json``). Von den 40 Survivor-Mutanten dieses Batches
sind 25 Klasse ``test`` (dieser Datei gepinnt) und 15 nachweislich
``equivalent`` (NICHT gepinnt, Begründung siehe Triage-JSON):

* ``xǁcreate_eventǁmutmut_76/88/95/96`` und ``xǁupdate_eventǁmutmut_28/29/30``
  (Mutationen an der ``search_text=compute_event_search_text(...)``-Zeile):
  ``core/signals/event_search.py::_refresh_event_search_text`` ist ein
  ``pre_save``-Signal auf ``Event``, das ``instance.search_text`` bei JEDEM
  ``.save()`` unbedingt aus ``instance.data_json``/``instance.document_type``
  NEU berechnet — genau als Sicherheitsnetz für Aufrufer, die das Feld nicht
  selbst pflegen (Refs #827 C-60). Der in ``create_event``/``update_event``
  inline berechnete bzw. mutierte Wert wird dadurch beim ``event.save()``
  IMMER überschrieben; kein Aufrufer kann den Zwischenwert beobachten (per
  vollem Pin-Datei-Lauf unter jeder dieser 7 Mutanten empirisch bestätigt:
  ``kill-batch-final.log``, alle 7 grün).
* ``xǁcreate_eventǁmutmut_112`` und ``xǁsoft_delete_eventǁmutmut_21``: das
  vierte Argument von ``bump_ban_revision_for_event_change`` wird von
  ``{}`` auf ``None`` mutiert. ``_banner_projection()``
  (``core/services/system/ban_revision.py``) normalisiert als ERSTE
  Anweisung per ``data_json or {}`` — ``None`` und ``{}`` erzeugen für
  JEDEN ``document_type`` bitidentische Ergebnisse, kein Aufrufer kann sie
  unterscheiden.
* ``xǁupdate_eventǁmutmut_33`` bis ``_38``: die Schleife
  ``for k, v in kwargs.items(): setattr(event, k, v)`` (mit
  ``# pragma: no cover`` im Original markiert) ist toter Code — die
  vorgelagerte ``allowed: set[str] = set()`` ist ein hartkodiertes leeres
  Set, jeder nicht-leere ``kwargs``-Schlüssel wirft deshalb VOR Erreichen
  dieser Zeile eine ``ValueError``; bei leerem ``kwargs`` iteriert die
  Schleife über nichts. Die mutierte Zeile läuft in keinem Fall.

Mehrere Survivor-Mutanten sind reine XX-Marker-/Gross-Klein-Varianten
derselben Meldung (kein Meldungsverlust). Sie wären nach der
Meldungs-Klassifikationsregel eigentlich ``skip`` — die hier ohnehin
nötigen Prio-1/2/3-Verhaltens-Pins mit exakter Text-Assertion töten sie
aber MIT, darum stehen sie in der Triage-JSON als Klasse ``test`` mit
Verweis auf den jeweiligen Testnamen.

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import Activity, Event, LegalHold
from core.services.events.crud import (
    CLIENT_BINDING_CHANGED_MESSAGE,
    LEGAL_HOLD_BLOCKS_DELETE_MESSAGE,
    _assert_current_client_binding,
    _assert_no_active_hold,
    _validate_case_assignment,
    _validate_contact_stage,
    create_event,
    decrypt_event_text_data,
    soft_delete_event,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _validate_case_assignment (Refs #1160)
# ---------------------------------------------------------------------------


def test_validate_case_assignment_meldet_exakten_text_bei_facility_mismatch(second_facility, case_open):
    """Pins ``xǁ_validate_case_assignmentǁmutmut_5``: ein Fall aus einer
    ANDEREN Einrichtung als das Ereignis muss GENAU diese deutsche Meldung
    auslösen (der Survivor war ein reiner XX-Marker derselben Meldung)."""
    with pytest.raises(ValidationError) as exc_info:
        _validate_case_assignment(second_facility, case_open, None, False)

    assert exc_info.value.messages == ["Fall gehört nicht zur selben Einrichtung wie das Ereignis."]


def test_validate_case_assignment_meldet_exakten_text_bei_person_mismatch(facility, case_open, client_qualified):
    """Pins ``xǁ_validate_case_assignmentǁmutmut_15``: hängt eine Person an
    das Ereignis, die nicht der Person des Falls entspricht, muss GENAU
    diese deutsche Meldung auslösen."""
    with pytest.raises(ValidationError) as exc_info:
        _validate_case_assignment(facility, case_open, client_qualified, False)

    assert exc_info.value.messages == ["Person des Ereignisses passt nicht zur Person des Falls."]


# ---------------------------------------------------------------------------
# _validate_contact_stage (Refs #1160)
# ---------------------------------------------------------------------------


def test_validate_contact_stage_meldet_exakten_text_bei_anonymem_kontakt(doc_type_contact):
    """Pins ``xǁ_validate_contact_stageǁmutmut_4/7/8/9``: ein anonymer
    Kontakt an einem Dokumentationstyp mit Mindest-Kontaktstufe muss die
    GESAMTE (implizit verkettete) deutsche Meldung exakt tragen — die
    Survivor waren XX-Marker- bzw. Gross-/Kleinschreibungs-Varianten je
    einer der beiden verketteten Teilstrings."""
    doc_type_contact.min_contact_stage = "identified"
    doc_type_contact.save(update_fields=["min_contact_stage"])

    with pytest.raises(ValidationError) as exc_info:
        _validate_contact_stage(doc_type_contact, None, True)

    assert exc_info.value.messages == [
        "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt, "
        "da eine Mindest-Kontaktstufe vorausgesetzt wird."
    ]


def test_validate_contact_stage_meldet_exakten_text_bei_fehlender_person(doc_type_contact):
    """Pins ``xǁ_validate_contact_stageǁmutmut_13/16/17/18``: fehlt bei einem
    Dokumentationstyp mit Mindest-Kontaktstufe die Person komplett, muss die
    GESAMTE verkettete deutsche Meldung exakt geprüft werden."""
    doc_type_contact.min_contact_stage = "identified"
    doc_type_contact.save(update_fields=["min_contact_stage"])

    with pytest.raises(ValidationError) as exc_info:
        _validate_contact_stage(doc_type_contact, None, False)

    assert exc_info.value.messages == [
        "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
        "da eine Mindest-Kontaktstufe vorausgesetzt wird."
    ]


# ---------------------------------------------------------------------------
# create_event (Refs #777, #2045, #1909)
# ---------------------------------------------------------------------------


def test_create_event_valueerror_bei_facility_fremder_person_ist_exakt(
    facility, staff_user, doc_type_contact, foreign_client
):
    """Pins ``xǁcreate_eventǁmutmut_28/29/30/31``: eine Person aus einer
    ANDEREN Einrichtung darf nicht an ein Ereignis gehängt werden — die
    ``ValueError``-Meldung wird exakt geprüft, nicht nur ihr Typ (die
    Survivor waren Meldungsverlust bzw. XX-Marker-/Gross-Klein-Varianten)."""
    with pytest.raises(ValueError) as exc_info:
        create_event(facility, staff_user, doc_type_contact, timezone.now(), {}, client=foreign_client)

    assert str(exc_info.value) == "Client gehört nicht zur Facility"


def test_create_event_permission_denied_meldung_ist_exakt(facility, assistant_user, doc_type_crisis):
    """Pins ``xǁcreate_eventǁmutmut_47/49/50/51``: eine Rolle ohne
    ausreichende Sensitivity-Berechtigung (``assistant`` sieht nur
    ``normal``, ``doc_type_crisis`` ist ``elevated``) muss GENAU diese
    deutsche ``PermissionDenied``-Meldung erhalten."""
    with pytest.raises(PermissionDenied) as exc_info:
        create_event(facility, assistant_user, doc_type_crisis, timezone.now(), {}, is_anonymous=True)

    assert str(exc_info.value) == "Diese Dokumentation darf von Ihrer Rolle nicht erstellt werden."


# ---------------------------------------------------------------------------
# update_event (Refs #734, #1338, #1789)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# decrypt_event_text_data (Refs #1160)
# ---------------------------------------------------------------------------


def test_decrypt_event_text_data_liefert_leeren_string_als_fallback_bei_kaputtem_token(sample_event):
    """Pins ``xǁdecrypt_event_text_dataǁmutmut_14/16/17``: ist ein
    ``data_json``-Wert als verschlüsselt markiert, aber das Token defekt
    (``EncryptionError``), muss die Form-Vorbelegung den LEEREN String
    liefern — nicht ``None`` (14), nicht das ``safe_decrypt``-Moduldefault
    ``'[verschlüsselt]'`` weil das ``default``-Kwarg fehlt (16), und nicht
    den XX-markierten leeren String (17)."""
    sample_event.data_json = {"notiz": {"__encrypted__": True, "value": "invalid-token"}}
    sample_event.save(update_fields=["data_json"])

    result = decrypt_event_text_data(sample_event)

    assert result["notiz"] == ""


# ---------------------------------------------------------------------------
# _assert_current_client_binding (ADR-050 D2, Refs #2197)
# ---------------------------------------------------------------------------


def test_assert_current_client_binding_meldet_exakten_text_bei_geaenderter_person(sample_event, client_qualified):
    """Pins ``xǁ_assert_current_client_bindingǁmutmut_15``: hat ein
    paralleler Merge die Personenzuordnung des Ereignisses in der DB
    geändert, während die aufrufende Instanz noch die alte Bindung im
    Speicher hält, muss GENAU die deutsche Meldung ``CLIENT_BINDING_CHANGED_MESSAGE``
    ausgelöst werden — der Survivor war ein Meldungsverlust (Text -> None)."""
    Event.objects.filter(pk=sample_event.pk).update(client=client_qualified)

    with pytest.raises(ValidationError) as exc_info:
        _assert_current_client_binding(sample_event)

    assert exc_info.value.messages == [str(CLIENT_BINDING_CHANGED_MESSAGE)]


# ---------------------------------------------------------------------------
# _assert_no_active_hold (ADR-050 D2, Refs #1066, #1376, #2197)
# ---------------------------------------------------------------------------


def test_assert_no_active_hold_meldet_exakten_text_bei_ereignis_hold(sample_event, admin_user):
    """Pins ``xǁ_assert_no_active_holdǁmutmut_10``: ein Legal Hold auf das
    EREIGNIS selbst muss GENAU ``LEGAL_HOLD_BLOCKS_DELETE_MESSAGE`` tragen
    — der bestehende ``test_event_deletion_hold.py::
    test_event_hold_stops_the_approved_deletion`` prüft nur den
    Exception-Typ, nicht den Wortlaut, darum überlebte der Meldungsverlust."""
    LegalHold.objects.create(
        facility=sample_event.facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="Ermittlungsverfahren",
        created_by=admin_user,
    )

    with pytest.raises(ValidationError) as exc_info:
        _assert_no_active_hold(sample_event, client_id=sample_event.client_id, case_id=None)

    assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]


def test_assert_no_active_hold_meldet_exakten_text_bei_personen_hold(sample_event, client_identified, admin_user):
    """Pins ``xǁ_assert_no_active_holdǁmutmut_21``: ein Legal Hold auf die
    PERSON (Refs #1376 I10) blockiert denselben manuellen Löschpfad wie ein
    Fall- oder Ereignis-Hold — bisher gab es dafür GAR KEINEN Test (nur
    Fall- und Ereignis-Hold waren in ``test_event_deletion_hold.py``
    belegt), darum überlebte sowohl die Meldung als auch der Zweig
    selbst unbemerkt bis zur exakten Text-Prüfung."""
    LegalHold.objects.create(
        facility=sample_event.facility,
        target_type="Client",
        target_id=client_identified.pk,
        reason="Ermittlungsverfahren",
        created_by=admin_user,
    )

    with pytest.raises(ValidationError) as exc_info:
        _assert_no_active_hold(sample_event, client_id=client_identified.pk, case_id=None)

    assert exc_info.value.messages == [str(LEGAL_HOLD_BLOCKS_DELETE_MESSAGE)]


# ---------------------------------------------------------------------------
# soft_delete_event (ADR-050 D2, Refs #2157)
# ---------------------------------------------------------------------------


def test_soft_delete_event_activity_traegt_die_loeschende_person(sample_event, staff_user):
    """Pins ``xǁsoft_delete_eventǁmutmut_80``: die Activity-Zeile des
    Löschvorgangs muss den HANDELNDEN User als ``actor`` tragen.
    ``Activity.actor`` ist nullable (``null=True``) — der Mutant
    (``actor=None`` statt ``actor=user``) schlägt deshalb NICHT laut fehl,
    sondern verliert die Zurechenbarkeit im Dashboard-Aktivitäts-Feed
    lautlos."""
    soft_delete_event(sample_event, staff_user)

    activity = Activity.objects.get(
        target_type=ContentType.objects.get_for_model(Event),
        target_id=sample_event.pk,
        verb=Activity.Verb.DELETED,
    )
    assert activity.actor == staff_user

"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.client.main``,
Batch ``main-a`` (Funktionen ``approve_client_deletion``, ``update_client``,
``anonymize_client``).

Quelle: Vollmutation #2174, Batch ``main-a``
(``welle-l-0.99/reports/m-scratch/batch-main-a.json``,
Triage ``welle-l-0.99/reports/m-scratch/pins-main-a/triage-099-main-a.json``). Die
Batch hat 60 Survivor-Mutanten in genau diesen drei Funktionen — dem DSGVO-
Loeschpfad (Vier-Augen-Genehmigung) und der Aggregat-Anonymisierung einer
Person. 59 sind Testlücken (``test``), EINE (``x_anonymize_client__mutmut_29``)
ist ``equivalent`` (siehe Begründung an ``TestAnonymizeClientAttachmentDeletion``
unten).

Gruppierung der gepinnten Tests:

* **Meldungs-VERLUST an sieben Guard-Stellen von ``approve_client_deletion``
  und ``update_client`` (24 Mutanten, sieben Tests unten in
  ``TestApproveClientDeletionGuardMessages``/``TestUpdateClientGuardMessage``).**
  ``raise ValidationError(text)``/``raise ValidationError(_(text))`` wird zu
  ``ValidationError(None)`` (die Exception traegt dann buchstaeblich die
  Meldung ``"None"``) bzw. zu ``ValidationError(_(None))`` (``gettext_lazy(None)``
  — jede Stringifizierung des Objekts bricht mit ``AttributeError`` ab) bzw. zu
  einer reinen Wortlaut-Variante (``XX``-Marker, Klein-/Grossschreibung).
  Beides ist an der Service-API beobachtbares Verhalten — anders als reine
  Wortlaut-Varianten *desselben Textes ohne begleitenden None-Verlust*, die
  aber hier IMMER gemeinsam mit einem None-Verlust an derselben Stelle
  auftreten und daher vom selben exakten ``exc.value.messages``-Vergleich
  mitgetoetet werden (deshalb ``test``, nicht ``skip`` — jede Variante wird
  unten explizit beim zugehoerigen Mutanten-Namen genannt).

* **TOCTOU-Reload-Guards von ``approve_client_deletion`` (2 Mutanten, Prio 1,
  ``TestApproveClientDeletionReloadTiesToItsOwnFacility``).** Zwei
  ``select_for_update().get(pk=..., facility=...)``-Aufrufe verlieren ihren
  ``facility``-Filter. Muster identisch zu Batch ``merge-b``
  (``x_approve_client_merge__mutmut_17``): eine zwischenzeitlich (im Speicher
  bzw. per Direct-Update) veraltete Facility-Annahme wird beim Original mit
  ``DoesNotExist`` verworfen, beim Mutanten still ignoriert.

* **Audit-/Aktivitaets-Zurechnung (5 Mutanten, Prio 1/2).** ``reviewed_at``,
  der ``AuditLog``-Akteur von ``CLIENT_SOFT_DELETED``/``STAGE_CHANGE`` und der
  ``Activity``-Akteur mehrerer Zweige verlieren ihren Wert bzw. ihre Person.

* **Dynamische Feldkomponente von ``update_client`` (7 Mutanten, Prio 1,
  ``TestUpdateClientDynamicDataGuards``).** Ein verstuemmelter
  Membership-Check umgeht die ``clean_dynamic_data``-Whitelist, wenn NUR
  ``data_json`` (kein ``document_type``) mitgeschickt wird (Mass-Assignment-
  Bypass); ein vertauschter Default loescht bestehende dynamische Feldwerte,
  wenn NUR ``document_type`` gewechselt wird (Datenverlust).

* **Art.-18-Sperrenverlust von ``update_client`` (3 Mutanten, Prio 1,
  ``TestUpdateClientLocksTheRow``).** ``assert_processing_allowed(client,
  lock=True)`` verliert die Zeilensperre — das TOCTOU-Fenster zwischen Pruefung
  und Write ist wieder offen. Nachweis ueber ``CaptureQueriesContext``
  (``FOR UPDATE`` in der ersten Query gegen die Client-Tabelle).

* **Stufenwechsel-Logik von ``update_client`` (3 Mutanten, Prio 2,
  ``TestUpdateClientContactStageDefault``).** Ein vertauschter Default lässt
  den Herabstufungs-Guard faelschlich auch bei UNVERAENDERTEM ``contact_stage``
  greifen; eine ``and``/``or``-Verwechslung loest eine QUALIFIED-Activity auch
  ohne echten Stufenwechsel aus.

* **Anonymisierungs-Kaskade (2 Mutanten, Prio 1,
  ``TestAnonymizeClientCascadeArguments``).** ``_redact_case_goals``/
  ``_redact_retention_proposals`` bekommen ``None`` statt der echten ID-Liste
  — beide Helfer haben ein ``if not ids: return``-Fruehende und redigieren
  dadurch NICHTS: Klartext-Wirkungsziele/-Meilensteine bzw. das
  Klartext-Pseudonym in ``RetentionProposal.details`` ueberleben die
  Anonymisierung.

* **Nachweis-Logzeile der Anonymisierung (13 Mutanten, Prio 1,
  ``TestAnonymizeClientLogRecord``).** ``logger.info("client_anonymized",
  extra={"client_id": ..., "user_id": ...})`` ist das EINZIGE Log-Ereignis
  dieser irreversiblen Operation und wird per ``caplog`` exakt gepinnt (Text
  UND beide ``extra``-Felder) — analog dem bereits etablierten Muster in
  ``test_mutation_pins_099_workitems_b.py``
  (``TestLockedWorkitemsSkipLogging``).

``_encryption_key`` liegt modulweit an: der Mutantenbaum (``/work/anlaufstelle/mutants``)
hat keine ``.env`` und damit keinen ``ENCRYPTION_KEY``; ohne die Fixture waeren
die Pins dort aus dem FALSCHEN Grund rot.
"""

from __future__ import annotations

import logging

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DeletionRequest, Event, RetentionProposal
from core.retention.proposals import build_proposal_details, create_proposal
from core.services.client.main import (
    anonymize_client,
    approve_client_deletion,
    request_client_deletion,
    update_client,
)
from core.services.client.merge import request_client_merge

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# approve_client_deletion — exakte Guard-Meldungen
# ---------------------------------------------------------------------------


class TestApproveClientDeletionGuardMessages:
    def test_reviewer_ist_antragsteller_meldet_den_exakten_wortlaut(self, client_identified, lead_user):
        """Pins ``x_approve_client_deletion__mutmut_2`` (``ValidationError(None)``),
        ``…_3`` (XX-Marker), ``…_4`` (kleingeschrieben), ``…_5`` (grossgeschrieben)."""
        dr = request_client_deletion(client_identified, lead_user, "Pin099-MainA-Grund")

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)

        assert exc.value.messages == ["Reviewer darf nicht der Antragsteller sein (Vier-Augen-Prinzip)."]

    def test_reviewer_ohne_loeschbestaetigungsrecht_meldet_den_exakten_wortlaut(
        self, client_identified, staff_user, assistant_user
    ):
        """Pins ``x_approve_client_deletion__mutmut_7`` (``ValidationError(None)``),
        ``…_8`` (XX-Marker), ``…_9`` (kleingeschrieben), ``…_10`` (grossgeschrieben)."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, assistant_user)

        assert exc.value.messages == ["Reviewer benötigt das Recht 'Löschbestätigung' (Vier-Augen-Prinzip)."]

    def test_merge_antrag_kann_nicht_ueber_diesen_weg_genehmigt_werden(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x_approve_client_deletion__mutmut_17`` (``ValidationError(None)``),
        ``…_18`` (``ValidationError(_(None))``), ``…_19`` (XX-Marker), ``…_20``
        (kleingeschrieben), ``…_21`` (grossgeschrieben). Ein Merge-Antrag
        (``merge_into`` gesetzt) darf nicht ueber den blossen Loeschweg
        genehmigt werden."""
        ziel = Client.objects.create(facility=facility, pseudonym="Pin099-MainA-Mergeziel", created_by=staff_user)
        dr = request_client_merge(client_identified, ziel, staff_user, "Pin099-MainA-Doppelt")

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)

        assert exc.value.messages == [
            "Dies ist ein Zusammenführungs-Antrag — er wird über den Zusammenführungs-Weg entschieden."
        ]

    def test_bereits_bearbeiteter_antrag_meldet_den_exakten_wortlaut(self, client_identified, staff_user, lead_user):
        """Pins ``x_approve_client_deletion__mutmut_23`` (``ValidationError(None)``),
        ``…_24`` (XX-Marker), ``…_25`` (kleingeschrieben), ``…_26`` (grossgeschrieben)."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")
        approve_client_deletion(dr, lead_user)
        dr.refresh_from_db()

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)

        assert exc.value.messages == ["Löschantrag wurde bereits bearbeitet (nicht mehr ausstehend)."]

    def test_bereits_zusammengefuehrtes_ziel_meldet_den_exakten_wortlaut(
        self, facility, client_identified, staff_user, lead_user
    ):
        """Pins ``x_approve_client_deletion__mutmut_35`` (XX-Marker), ``…_36``
        (kleingeschrieben). Das Zielobjekt wird per Direct-Update als bereits
        zusammengefuehrt markiert (Simulation eines Merge zwischen Antrag und
        Genehmigung)."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")
        anderer = Client.objects.create(facility=facility, pseudonym="Pin099-MainA-Mergeziel2", created_by=staff_user)
        Client.objects.filter(pk=client_identified.pk).update(merged_into=anderer)

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)

        assert exc.value.messages == ["Diese Person ist bereits zusammengeführt."]

    def test_bereits_im_papierkorb_meldet_den_exakten_wortlaut(self, client_identified, staff_user, lead_user):
        """Pins ``x_approve_client_deletion__mutmut_40`` (XX-Marker)."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")
        client_identified.soft_delete(user=staff_user)

        with pytest.raises(ValidationError) as exc:
            approve_client_deletion(dr, lead_user)

        assert exc.value.messages == ["Diese Person liegt bereits im Papierkorb."]


# ---------------------------------------------------------------------------
# approve_client_deletion — TOCTOU-Reload-Guards (facility-Filter)
# ---------------------------------------------------------------------------


class TestApproveClientDeletionReloadTiesToItsOwnFacility:
    def test_antrags_reload_verwirft_eine_veraltete_facility_annahme(
        self, facility, second_facility, client_identified, staff_user, lead_user
    ):
        """Pin: ``x_approve_client_deletion__mutmut_15`` — ``select_for_update().get(
        pk=deletion_request.pk, facility=deletion_request.facility)`` -> ``get(
        pk=deletion_request.pk, )`` (facility-Filter entfernt). Identisches
        Muster zu ``x_approve_client_merge__mutmut_17`` (Batch ``merge-b``):
        ``deletion_request.facility`` wird NACH dem Laden manuell auf eine
        ANDERE Einrichtung gesetzt (nicht gespeichert). Original: der Reload
        filtert erneut nach dieser (jetzt falschen) Annahme und findet die
        Zeile nicht. Mutant: der Reload ignoriert die Annahme und laedt die
        echte Zeile trotzdem.

        Die pruefende Person gehoert zur ANGENOMMENEN Einrichtung: die
        Einrichtungsgrenze vor der Antragssperre vergleicht mit der Annahme und
        laesst den Aufruf bis zum gesperrten Reload durch, der die Annahme
        verwirft (Mutant: Reload gelingt, die Grenze unter der Sperre meldet
        die fremde Einrichtung statt ``DoesNotExist``)."""
        from core.models import User

        reviewer = User.objects.create_user(
            username="pin099_leitung_angenommen",
            role=User.Role.LEAD,
            facility=second_facility,
            can_confirm_deletion=True,
        )
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")
        dr.facility = second_facility

        with pytest.raises(DeletionRequest.DoesNotExist):
            approve_client_deletion(dr, reviewer)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING
        client_identified.refresh_from_db()
        assert client_identified.is_deleted is False

    def test_client_reload_verwirft_eine_geaenderte_facility_zuordnung(
        self, facility, second_facility, client_identified, staff_user, lead_user
    ):
        """Pin: ``x_approve_client_deletion__mutmut_31`` — ``Client.objects.
        select_for_update().get(pk=deletion_request.target_id, facility=
        deletion_request.facility)`` -> ``get(pk=deletion_request.target_id, )``
        (facility-Filter entfernt). Die Ziel-Person wird per Direct-Update
        (Bypass des Service) einer ANDEREN Einrichtung zugeordnet, NACHDEM der
        Antrag gestellt wurde. Original: der Reload findet die Zeile nicht
        mehr (facility passt nicht mehr zum bereits neu geladenen Antrag) —
        ``DoesNotExist``. Mutant: der Reload ignoriert die Divergenz und
        genehmigt den Soft-Delete ueber die Einrichtungsgrenze hinweg."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")
        Client.objects.filter(pk=client_identified.pk).update(facility=second_facility)

        with pytest.raises(Client.DoesNotExist):
            approve_client_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING


# ---------------------------------------------------------------------------
# approve_client_deletion — Audit-/Aktivitaets-Zurechnung
# ---------------------------------------------------------------------------


class TestApproveClientDeletionAudit:
    def test_genehmigung_setzt_den_pruefzeitpunkt(self, client_identified, staff_user, lead_user):
        """Pin: ``x_approve_client_deletion__mutmut_46`` — ``deletion_request.
        reviewed_at = timezone.now()`` -> ``= None``. Nachweisluecke im
        Vier-Augen-Loeschpfad: der Antrag traegt reviewed_by/status, aber
        keinen Pruefzeitpunkt mehr."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")

        approve_client_deletion(dr, lead_user)

        dr.refresh_from_db()
        assert dr.reviewed_at is not None

    def test_soft_delete_audit_traegt_die_genehmigende_person(self, facility, client_identified, staff_user, lead_user):
        """Pin: ``x_approve_client_deletion__mutmut_48`` — ``audit_client_event(
        client, reviewer, ...)`` -> ``audit_client_event(client, None, ...)``.
        Der AuditLog-Eintrag CLIENT_SOFT_DELETED verliert sonst die
        genehmigende Person — Kernzusage des Vier-Augen-Loeschpfads."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")

        approve_client_deletion(dr, lead_user)

        entry = AuditLog.objects.get(
            action=AuditLog.Action.CLIENT_SOFT_DELETED,
            target_id=str(client_identified.pk),
        )
        assert entry.user_id == lead_user.pk
        assert entry.facility_id == facility.pk

    def test_papierkorb_aktivitaet_traegt_die_genehmigende_person(self, client_identified, staff_user, lead_user):
        """Pin: ``x_approve_client_deletion__mutmut_57`` — ``log_activity(...,
        actor=reviewer, ...)`` -> ``actor=None``."""
        dr = request_client_deletion(client_identified, staff_user, "Pin099-MainA-Grund")

        approve_client_deletion(dr, lead_user)

        activity = Activity.objects.get(target_id=client_identified.pk, verb=Activity.Verb.DELETED)
        assert activity.actor_id == lead_user.pk


# ---------------------------------------------------------------------------
# update_client — Art.-18-Gate sperrt die Zeile
# ---------------------------------------------------------------------------


class TestUpdateClientLocksTheRow:
    def test_das_art_18_gate_sperrt_die_zeile_beim_pruefen(self, client_identified, staff_user):
        """Pins ``x_update_client__mutmut_2`` (``lock=None``), ``…_4``
        (Argument entfernt, Default ``False``), ``…_5`` (``lock=False``) an
        ``assert_processing_allowed(client, lock=True)``. Original: die ERSTE
        Query gegen die Client-Tabelle (der Einschraenkungs-Check) traegt
        ``FOR UPDATE`` — das TOCTOU-Fenster zwischen Pruefung und dem
        spaeteren Write ist damit geschlossen. Mutant: dieselbe Query ohne
        Zeilensperre."""
        with CaptureQueriesContext(connection) as ctx:
            update_client(client_identified, staff_user, notes="Pin099-MainA-Sperrpruefung")

        table = Client._meta.db_table
        client_queries = [q["sql"] for q in ctx.captured_queries if table in q["sql"]]
        assert client_queries, "keine Query gegen die Client-Tabelle erfasst"
        assert "FOR UPDATE" in client_queries[0]


# ---------------------------------------------------------------------------
# update_client — Stufenwechsel-Default und and/or-Logik
# ---------------------------------------------------------------------------


class TestUpdateClientContactStageDefault:
    def test_unveraenderter_stufenwechsel_bleibt_folgenlos(self, client_qualified, staff_user):
        """Pins ``x_update_client__mutmut_14``/``…_16`` (``fields.get(
        "contact_stage", old_stage)`` faellt auf ``None`` statt ``old_stage``
        zurueck — der Herabstufungs-Guard griffe dann faelschlich auch bei
        einer Aktualisierung, die ``contact_stage`` gar nicht anfasst) UND
        ``x_update_client__mutmut_98`` (``and``->``or`` in der
        QUALIFIED-Aktivitaets-Bedingung — loest ohne echten Stufenwechsel
        trotzdem eine CLIENT_QUALIFIED-Activity aus). ``client_qualified``
        bleibt QUALIFIZIERT, nur ``notes`` wird von einer NICHT-leitenden
        Rolle aktualisiert."""
        update_client(client_qualified, staff_user, notes="Pin099-MainA-KeineStufenaenderung")

        client_qualified.refresh_from_db()
        assert client_qualified.notes == "Pin099-MainA-KeineStufenaenderung"
        assert client_qualified.contact_stage == Client.ContactStage.QUALIFIED
        assert not Activity.objects.filter(target_id=client_qualified.pk, verb=Activity.Verb.QUALIFIED).exists()


class TestUpdateClientGuardMessage:
    def test_herabstufung_ohne_leitungsrecht_meldet_den_exakten_wortlaut(self, client_qualified, staff_user):
        """Pins ``x_update_client__mutmut_24`` (``ValidationError(None)``),
        ``…_25`` (``ValidationError(_(None))``), ``…_26`` (XX-Marker), ``…_27``
        (kleingeschrieben), ``…_28`` (grossgeschrieben)."""
        with pytest.raises(ValidationError) as exc:
            update_client(client_qualified, staff_user, contact_stage=Client.ContactStage.IDENTIFIED)

        assert exc.value.messages == ["Nur die Leitung darf eine qualifizierte Person herabstufen."]


# ---------------------------------------------------------------------------
# update_client — dynamische Feldkomponente (Whitelist + Datenerhalt)
# ---------------------------------------------------------------------------


class TestUpdateClientDynamicDataGuards:
    def test_reine_data_json_uebergabe_durchlaeuft_weiterhin_die_whitelist(
        self, client_identified, staff_user, doc_type_client_profile
    ):
        """Pins ``x_update_client__mutmut_50`` (``"data_json"`` ->
        ``"XXdata_jsonXX"``), ``…_51`` (``"DATA_JSON"``), ``…_52`` (``in``
        -> ``not in``) an ``if "document_type" in fields or "data_json" in
        fields:``. Alle drei Varianten ueberspringen den ``clean_dynamic_data``-
        Aufruf, sobald NUR ``data_json`` (ohne ``document_type``) mitgeschickt
        wird — ein unbekannter Slug landet dann ungefiltert im gespeicherten
        ``data_json`` (Mass-Assignment-Bypass an der dynamischen
        Formularkomponente)."""
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type"])
        wohn_key = doc_type_client_profile.fields.get(field_template__name="Wohnsituation").field_template.slug

        update_client(
            client_identified,
            staff_user,
            data_json={wohn_key: "Pin099-MainA-Miete", "pin099-main-unbekanntes-feld": "sollte-verschwinden"},
        )

        client_identified.refresh_from_db()
        assert "pin099-main-unbekanntes-feld" not in client_identified.data_json
        assert client_identified.data_json[wohn_key] == "Pin099-MainA-Miete"

    def test_reiner_dokumenttyp_wechsel_ohne_data_json_loescht_vorhandene_werte_nicht(
        self, client_identified, staff_user, doc_type_client_profile
    ):
        """Pins ``x_update_client__mutmut_62`` (``client.data_json`` ->
        ``None``), ``…_64`` (Default-Argument entfernt, ebenfalls ``None``) an
        ``raw_data = fields.get("data_json", client.data_json)``. Original:
        wird NUR ``document_type`` geaendert, dient das BESTEHENDE
        ``client.data_json`` als Grundlage fuer die erneute Validierung gegen
        den (neuen) Typ. Mutant: die Grundlage ist ``None`` ->
        ``clean_dynamic_data`` liefert ``{}`` -> die vorhandenen dynamischen
        Feldwerte werden geloescht, obwohl nur der Typ neu zugewiesen wurde."""
        client_identified.document_type = doc_type_client_profile
        wohn_key = doc_type_client_profile.fields.get(field_template__name="Wohnsituation").field_template.slug
        client_identified.data_json = {wohn_key: "Pin099-MainA-Bestandswert"}
        client_identified.save(update_fields=["document_type", "data_json"])

        update_client(client_identified, staff_user, document_type=doc_type_client_profile)

        client_identified.refresh_from_db()
        assert client_identified.data_json[wohn_key] == "Pin099-MainA-Bestandswert"


# ---------------------------------------------------------------------------
# update_client — Audit-/Aktivitaets-Zurechnung
# ---------------------------------------------------------------------------


class TestUpdateClientAudit:
    def test_stufenwechsel_und_qualifizierung_werden_der_handelnden_person_zugerechnet(
        self, client_identified, staff_user
    ):
        """Pins ``x_update_client__mutmut_93`` (``update_client_stage(client,
        old_stage, new_stage, user)`` -> ``..., None)`` — der STAGE_CHANGE-
        AuditLog-Eintrag verliert die handelnde Person) UND ``…_102``
        (``log_activity(..., actor=user, ...)`` -> ``actor=None`` im
        QUALIFIED-Zweig). ``client_identified`` startet IDENTIFIED, der
        Aufruf qualifiziert sie."""
        update_client(client_identified, staff_user, contact_stage=Client.ContactStage.QUALIFIED)

        stage_entry = AuditLog.objects.get(action=AuditLog.Action.STAGE_CHANGE, target_id=str(client_identified.pk))
        assert stage_entry.user_id == staff_user.pk

        activity = Activity.objects.get(target_id=client_identified.pk, verb=Activity.Verb.QUALIFIED)
        assert activity.actor_id == staff_user.pk

    def test_abschliessende_updated_aktivitaet_wird_der_handelnden_person_zugerechnet(
        self, client_identified, staff_user
    ):
        """Pin: ``x_update_client__mutmut_120`` — ``log_activity(...,
        actor=user, ...)`` -> ``actor=None`` im abschliessenden UPDATED-Zweig
        (laeuft bei JEDEM erfolgreichen Aufruf)."""
        update_client(client_identified, staff_user, notes="Pin099-MainA-AutorenNachweis")

        activity = Activity.objects.get(target_id=client_identified.pk, verb=Activity.Verb.UPDATED)
        assert activity.actor_id == staff_user.pk


# ---------------------------------------------------------------------------
# anonymize_client — Kaskaden-Argumente (Datenverlust)
# ---------------------------------------------------------------------------


class TestAnonymizeClientCascadeArguments:
    def test_anonymisierung_redigiert_wirkungsziele_und_meilensteine(self, client_identified, outcome_goal, milestone):
        """Pin: ``x_anonymize_client__mutmut_6`` — ``_redact_case_goals(case_ids)``
        -> ``_redact_case_goals(None)``. ``redact_case_goals`` (core.services.
        case.deletion) hat ein ``if not case_ids: return`` — mit ``None``
        bricht die Funktion sofort ab, OHNE etwas zu redigieren.
        Klartext-Wirkungsziele/-Meilensteine der Faelle dieser Person
        ueberleben dann die Anonymisierung."""
        assert outcome_goal.case.client_id == client_identified.pk

        anonymize_client(client_identified)

        outcome_goal.refresh_from_db()
        milestone.refresh_from_db()
        assert outcome_goal.title == "Wirkungsziel (anonymisiert)"
        assert outcome_goal.description == ""
        assert milestone.title == "Meilenstein (anonymisiert)"

    def test_anonymisierung_redigiert_das_klartext_pseudonym_im_loeschvorschlag(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Pin: ``x_anonymize_client__mutmut_17`` — ``_redact_retention_proposals(
        event_ids)`` -> ``_redact_retention_proposals(None)``. Die Funktion hat
        ein ``if not event_ids: return`` — mit ``None`` bricht sie sofort ab,
        das Klartext-Pseudonym in ``RetentionProposal.details`` ueberlebt die
        Anonymisierung."""
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        proposal, _created = create_proposal(
            facility,
            RetentionProposal.TargetType.EVENT,
            event.pk,
            timezone.localdate(),
            build_proposal_details(event),
            "identified",
        )
        assert proposal.details["pseudonym"] == client_identified.pseudonym

        anonymize_client(client_identified)

        proposal.refresh_from_db()
        assert proposal.details["pseudonym"] == "[Anonymisiert]"


# ---------------------------------------------------------------------------
# anonymize_client — unbenutzter Parameter (equivalent)
# ---------------------------------------------------------------------------


class TestAnonymizeClientAttachmentDeletion:
    """``x_anonymize_client__mutmut_29`` (``_delete_event_attachments_for_client(
    client, event_ids)`` -> ``..., (None, event_ids)``) ist EQUIVALENT: der
    Parameter ``client`` wird im Funktionskoerper von
    ``_delete_event_attachments_for_client`` (src/core/services/client/main.py)
    an KEINER Stelle gelesen — die Funktion filtert ausschliesslich ueber
    ``event_ids`` (``Event.objects.filter(pk__in=event_ids)``) und ruft
    ``delete_attachments_for(event)`` je Ereignis auf, niemals mit ``client``.
    Kein Aufrufer kann den Unterschied zwischen dem echten Client-Objekt und
    ``None`` an dieser Stelle beobachten. Kein Pin (siehe Triage,
    ``pins-main-a/triage-099-main-a.json``)."""


# ---------------------------------------------------------------------------
# anonymize_client — Nachweis-Logzeile
# ---------------------------------------------------------------------------


class TestAnonymizeClientLogRecord:
    def test_anonymisierung_protokolliert_akteur_und_ziel_exakt(self, caplog, client_identified, staff_user):
        """Pins ``x_anonymize_client__mutmut_41`` (Meldungstext -> ``None``),
        ``…_42``/``…_44`` (``extra``-Dict -> ``None`` bzw. ganz entfernt),
        ``…_45``/``…_46`` (Meldungstext klein-/grossgeschrieben), ``…_47``/
        ``…_48`` (Key ``client_id`` verstuemmelt), ``…_49`` (Wert von
        ``client_id`` -> ``str(None)``), ``…_50``/``…_51`` (Key ``user_id``
        verstuemmelt), ``…_52``/``…_57``/``…_58`` (Wert von ``user_id`` ->
        ``None`` ueber verstuemmelten ``getattr``-Zugriff). ``logger.info(
        "client_anonymized", extra={"client_id": ..., "user_id": ...})`` ist
        das EINZIGE Log-Ereignis dieser irreversiblen DSGVO-Operation (Muster
        analog ``TestLockedWorkitemsSkipLogging`` in
        ``test_mutation_pins_099_workitems_b.py``)."""
        logger_name = "core.services.client.main"

        with caplog.at_level(logging.INFO, logger=logger_name):
            anonymize_client(client_identified, user=staff_user)

        records = [record for record in caplog.records if record.name == logger_name]
        assert len(records) == 1
        record = records[0]
        assert record.getMessage() == "client_anonymized"
        assert record.client_id == str(client_identified.pk)
        assert record.user_id == staff_user.pk

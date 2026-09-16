"""Pin-Tests Welle L (release-0.99) — Vollmutation #2174/#2193, Batch **compliance-1**.

Modul ``core.services.compliance.sensitivity`` (46 Survivors, gesamtes
Batch). Liste: ``/work/wellen/welle-l-0.99/reports/m-scratch/
batch-compliance-1.json``. Quelle der Mutanten-Rümpfe: Mutantenbaum
``mutants/src/core/services/compliance/sensitivity.py``. Vollständige
Triage (alle 46 Mutanten, inkl. ``skip``/``equivalent``-Begründungen):
``reports/m-scratch/pins-compliance-1/triage-099-compliance-1.json``.

Dieses Modul ist die zentrale Sensitivity-/Sichtbarkeits-Instanz: Rollen vs.
Dokumenttyp-/Feld-Sensitivität (``effective_sensitivity``,
``user_can_see_field``, ``user_can_see_document_type``,
``allowed_sensitivities_for_user``), die facility-gescopte
Anhang-Übersichts-Query (``visible_attachments_for_user``) und das
fünfstufige Download-Gate (``get_visible_event_or_404``,
``get_visible_attachment_for_owner_or_404``,
``get_visible_attachment_or_404``). Die Bestandsdeckung
(``test_field_sensitivity.py``, ``test_compliance_attachment_gate.py``,
``test_attachment_reachability.py``) prüft die Haupt-Branches mit explizit
übergebenen Argumenten; die 46 Survivors liegen an drei Stellen, die dort
NICHT abgedeckt sind:

1. **Default-Argumente**, die kein aktueller Aufrufer (bzw. nur ein
   Ein-Argument-Aufruf in ``core.packages.loader``) auslässt.
2. **``ROLE_MAX_SENSITIVITY``-Default für eine Rolle ohne Eintrag**
   (``super_admin`` — die Rolle ist bewusst NICHT in der Tabelle, siehe
   ``User.Role``).
3. **``select_related``-Vollständigkeit und eine Q-Filter-Bedingung** in
   ``visible_attachments_for_user`` (Löschgrenze über den Event-Client).

Alle Prio-1-Einstufungen unten wurden gegen echte Daten verifiziert (Test
lief unter der jeweiligen Mutante rot, ohne Mutante grün) — auch die als
``equivalent`` eingestuften Coalesce-/output_field-Survivors wurden unter
der Mutante ausgeführt und blieben nachweislich unverändert (kein Diff in
Ergebnis, Query-Zahl oder Exception).
"""

from __future__ import annotations

import pytest

from core.models import Attachment, Client, FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _ft(facility, name, sensitivity=""):
    return FieldTemplate.objects.create(
        facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE, sensitivity=sensitivity
    )


def _attach(facility, user, ft, *, event=None, client=None, case=None, is_current=True, deleted_at=None):
    return Attachment.objects.create(
        facility=facility,
        event=event,
        client=client,
        case=case,
        field_template=ft,
        storage_filename="pin.enc",
        original_filename_encrypted={"v": "pin"},
        file_size=10,
        mime_type="application/pdf",
        created_by=user,
        is_current=is_current,
        deleted_at=deleted_at,
    )


class TestSensitivityDefaultArguments:
    """Default-Werte der ``field_sensitivity``-Parameter — die dokumentierte
    Bedeutung „leer = erbt vom Dokumentationstyp" darf nicht am Default
    selbst kippen."""

    def test_effective_sensitivity_ohne_feld_argument_bleibt_dokumenttyp_rang(self):
        """Pin: ``x_effective_sensitivity__mutmut_1`` (Default
        ``field_sensitivity=""`` -> ``"XXXX"``).

        ``core.packages.loader.py:868`` ruft ``effective_sensitivity(new_type)``
        mit NUR einem Argument auf (Paket-Downgrade-Erkennung beim
        Paket-Update). Der mutierte, truthy Default hebt das Ergebnis IMMER
        auf ``UNKNOWN_SENSITIVITY_RANK`` — die Downgrade-Prüfung
        ``effective_sensitivity(new_type) < effective_sensitivity(old_type)``
        vergleicht dann zwei gleiche, maximale Werte und ist IMMER ``False``:
        eine echte Sensitivitäts-Herabstufung beim Paket-Update würde nie
        mehr erkannt.
        """
        from core.services.compliance.sensitivity import effective_sensitivity

        assert effective_sensitivity("normal") == 0
        assert effective_sensitivity("high") == 2

    def test_user_can_see_field_ohne_feld_argument_erbt_dokumenttyp_rang(self, assistant_user):
        """Pin: ``x_user_can_see_field__mutmut_1`` (Default
        ``field_sensitivity=""`` -> ``"XXXX"``).

        Kein Produktions-Aufrufer lässt das 3. Argument aus, aber die
        Funktion ist Teil der öffentlichen Sensitivity-API mit derselben
        Vererbungssemantik wie ``effective_sensitivity``. Mit dem mutierten
        Default würde ein Direktaufruf ohne 3. Argument fälschlich IMMER
        ``False`` liefern (zu restriktiv) statt die Dokumenttyp-Sensitivität
        zu erben.
        """
        from core.services.compliance.sensitivity import user_can_see_field

        assert user_can_see_field(assistant_user, "normal") is True


class TestRoleMaxSensitivityFailClosedDefault:
    """``ROLE_MAX_SENSITIVITY`` enthält bewusst NICHT ``SUPER_ADMIN`` (nur
    ASSISTANT/STAFF/LEAD/FACILITY_ADMIN) — jede der drei Sensitivity-Prüf-
    funktionen muss diese Rolle über den ``.get(..., 0)``-Default fail-closed
    auf NORMAL beschränken."""

    def test_rolle_ohne_eintrag_bleibt_bei_normal_gedeckelt(self, super_admin_user):
        """Pin: ``x_user_can_see_field__mutmut_4``/``_6`` (Default ``0`` ->
        ``None``, effektiv gleich: expliziter ``None``-Default bzw. Default-
        Argument ganz entfernt — beide lösen ohne Fix ein ungefangenes
        ``TypeError`` aus, sobald ``effective_sensitivity(...) <= None``
        ausgewertet wird), ``x_user_can_see_field__mutmut_7`` (Default ``0``
        -> ``1``), ``x_user_can_see_document_type__mutmut_5`` (Default ``0``
        -> ``1``), ``x_allowed_sensitivities_for_user__mutmut_6`` (Default
        ``0`` -> ``1``).

        Eine Rolle ohne Tabelleneintrag (hier: ``super_admin``) muss auf
        JEDEM der drei Pfade fail-closed wie ``ASSISTANT`` (NORMAL only)
        behandelt werden — nicht mit einem stillen Absturz (``None``-Default)
        und nicht mit einer stillschweigend geöffneten ELEVATED-Sicht
        (Default ``1``).
        """
        from core.models import DocumentType
        from core.services.compliance.sensitivity import (
            allowed_sensitivities_for_user,
            user_can_see_document_type,
            user_can_see_field,
        )

        assert user_can_see_field(super_admin_user, "normal", "") is True
        assert user_can_see_field(super_admin_user, "elevated", "") is False

        class _StubDocumentType:
            sensitivity = "elevated"

        assert user_can_see_document_type(super_admin_user, _StubDocumentType()) is False
        assert allowed_sensitivities_for_user(super_admin_user) == [DocumentType.Sensitivity.NORMAL]


class TestChainVersionCountsDefaultRemoved:
    """Private Hilfsfunktion der Versionsketten-Zählung — über die
    öffentliche API immer mit explizitem ``removed=`` aufgerufen, aber der
    Default selbst muss bei künftiger Wiederverwendung stimmen (Prio 3:
    aktuell unerreichbar, siehe Triage-Begründung)."""

    def test_chain_version_counts_ohne_removed_zaehlt_aktive_versionen(self, facility, staff_user, sample_event):
        """Pin: ``x__chain_version_counts__mutmut_1`` (Default
        ``removed=False`` -> ``True``).

        Zwei Versionen derselben Kette (``entry_id`` geteilt), beide NICHT
        soft-gelöscht. Ein Direktaufruf ohne ``removed`` muss die AKTIVEN
        (nicht gelöschten) Versionen zählen, nicht die gelöschten.
        """
        from django.db.models import IntegerField, Subquery

        from core.services.compliance.sensitivity import _chain_version_counts

        ft = _ft(facility, "Beleg-Chain")
        att_prior = _attach(facility, staff_user, ft, event=sample_event, is_current=False)
        att_current = Attachment.objects.create(
            facility=facility,
            event=sample_event,
            entry_id=att_prior.entry_id,
            field_template=ft,
            storage_filename="pin-chain.enc",
            original_filename_encrypted={"v": "pin-chain"},
            file_size=10,
            mime_type="application/pdf",
            created_by=staff_user,
            is_current=True,
        )

        result = (
            Attachment.objects.filter(pk=att_current.pk)
            .annotate(cnt=Subquery(_chain_version_counts(facility), output_field=IntegerField()))
            .first()
        )
        assert result.cnt == 2


class TestVisibleAttachmentsSelectRelatedVollstaendig:
    """``select_related``-Liste von ``visible_attachments_for_user`` deckt
    ALLE drei Owner-Typen (Event/Client/Case) samt ihren Nested-Pfaden
    sowie ``field_template``/``created_by`` ab — jede echte Lücke löst eine
    N+1-Zusatzquery aus."""

    def test_alle_relationspfade_ohne_zusatzquery_erreichbar(
        self,
        facility,
        lead_user,
        staff_user,
        sample_event,
        doc_type_client_profile,
        doc_type_case_form,
        case_open,
        django_assert_num_queries,
    ):
        """Pin: ``x_visible_attachments_for_user__mutmut_25``
        (``"event__document_type"`` entfernt), ``_26``
        (``"event__client"`` entfernt), ``_28``
        (``"client__document_type"`` entfernt), ``_30``
        (``"case__document_type"`` entfernt), ``_31``
        (``"case__client"`` entfernt), ``_32`` (``"field_template"``
        entfernt), ``_33`` (``"created_by"`` entfernt).

        Je ein Anhang an einem Event- (mit Doc-Type + Client), Client- (mit
        Doc-Type) und Case-Owner (mit Doc-Type + Client) — Zugriff auf jeden
        select_related-Pfad nach der Query-Auswertung darf KEINE
        Zusatzquery auslösen. (``mutmut_24``/``_27``/``_29`` — die bare
        ``"event"``/``"client"``/``"case"``-Einträge — sind äquivalent, weil
        die jeweils verbleibenden Nested-Pfade denselben Join erzwingen;
        siehe Triage-Datei.)
        """
        from core.services.compliance.sensitivity import visible_attachments_for_user

        client_with_type = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-Client-01",
            created_by=staff_user,
            document_type=doc_type_client_profile,
        )
        case_open.document_type = doc_type_case_form
        case_open.save(update_fields=["document_type"])

        ft_event = _ft(facility, "Beleg-Event")
        ft_client = _ft(facility, "Beleg-Client")
        ft_case = _ft(facility, "Beleg-Case")

        att_event = _attach(facility, staff_user, ft_event, event=sample_event)
        att_client = _attach(facility, staff_user, ft_client, client=client_with_type)
        att_case = _attach(facility, staff_user, ft_case, case=case_open)

        qs = visible_attachments_for_user(facility, lead_user)
        with django_assert_num_queries(1):
            rows = list(qs)
            for row in rows:
                _ = row.event
                if row.event_id:
                    _ = row.event.document_type
                    _ = row.event.client
                _ = row.client
                if row.client_id:
                    _ = row.client.document_type
                _ = row.case
                if row.case_id:
                    _ = row.case.document_type
                    _ = row.case.client
                _ = row.field_template
                _ = row.created_by
                _ = row.version_count

        pks = {r.pk for r in rows}
        assert {att_event.pk, att_client.pk, att_case.pk} <= pks


class TestVisibleAttachmentsLoeschgrenzeUeberEventClient:
    """Refs #1777: Die Löschgrenze für Event-Anhänge verläuft auch über die
    PERSON (den Event-Client), nicht nur über ``event.is_deleted`` — ein
    getrashter ODER anonymisierter Client muss die Event-Anhänge verstecken."""

    def test_getrashter_und_anonymisierter_client_versteckt_event_anhaenge(
        self, facility, lead_user, staff_user, doc_type_contact
    ):
        """Pin: ``x_visible_attachments_for_user__mutmut_50`` (die
        ``event__client__is_deleted=False``-Bedingung wird aus der
        Client-Q-Klausel entfernt), ``x_visible_attachments_for_user__mutmut_51``
        (die ``event__client__is_active=True``-Bedingung wird entfernt).

        Zwei reale Zwischenzustände (Docstring-Zitat: „eine anonymisierte
        Person (is_active=False) liegt nicht zwingend im Papierkorb"):
        ein getrashter, aber noch nicht anonymisierter Client
        (``is_deleted=True, is_active=True``) UND ein anonymisierter, aber
        nicht (mehr) getrashter Client (``is_deleted=False,
        is_active=False``, K-Anon-Bridge). Beide müssen ihre Event-Anhänge
        aus der Übersicht nehmen.
        """
        from django.utils import timezone

        from core.models import Event

        ft = _ft(facility, "Beleg-Loeschgrenze")

        client_trashed = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-Trash-01",
            created_by=staff_user,
        )
        Client.objects.filter(pk=client_trashed.pk).update(is_deleted=True, is_active=True)

        client_anonymized = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Pin-Anon-01",
            created_by=staff_user,
        )
        Client.objects.filter(pk=client_anonymized.pk).update(is_deleted=False, is_active=False)

        event_trashed_client = Event.objects.create(
            facility=facility,
            client=client_trashed,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        event_anonymized_client = Event.objects.create(
            facility=facility,
            client=client_anonymized,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        att_trashed = _attach(facility, staff_user, ft, event=event_trashed_client)
        att_anonymized = _attach(facility, staff_user, ft, event=event_anonymized_client)

        from core.services.compliance.sensitivity import visible_attachments_for_user

        pks = set(visible_attachments_for_user(facility, lead_user).values_list("pk", flat=True))
        assert att_trashed.pk not in pks
        assert att_anonymized.pk not in pks


class TestOwnerGateSelectRelatedUndFailClosedAttribut:
    """``get_visible_attachment_for_owner_or_404`` — Stufe 3/4 des
    fünfstufigen Download-Gates."""

    def test_field_template_select_related_vermeidet_zusatzquery(
        self, facility, lead_user, staff_user, client_identified, doc_type_client_profile, django_assert_num_queries
    ):
        """Pin: ``x_get_visible_attachment_for_owner_or_404__mutmut_6``
        (``.select_related("field_template")`` -> ``.select_related(None)``).

        Die Funktion liest ``attachment.field_template.sensitivity`` in der
        UNMITTELBAR nächsten Zeile (Stufe-4-Gate) — ohne select_related
        löst dieser Zugriff eine N+1-Zusatzquery aus.
        """
        from core.services.compliance.sensitivity import get_visible_attachment_for_owner_or_404

        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type"])
        ft = _ft(facility, "Beleg-Gate-SR")
        att = _attach(facility, staff_user, ft, client=client_identified)

        with django_assert_num_queries(1):
            result = get_visible_attachment_for_owner_or_404(lead_user, client_identified, att.pk)
            _ = result.field_template.sensitivity

    def test_owner_ohne_document_type_attribut_bleibt_fail_closed_404(self, staff_user, lead_user, facility):
        """Pin: ``x_get_visible_attachment_for_owner_or_404__mutmut_20``
        (``getattr(owner, "document_type", None)`` -> Default-Argument
        entfernt).

        ``test_owner_ohne_dokumentationstyp_ist_404`` (Bestand) deckt nur den
        Fall ab, dass der Owner das Attribut BESITZT mit Wert ``None`` — das
        wirft nie ein ``AttributeError``. Der Docstring verspricht aber
        Fail-Closed „Hat der Owner keinen document_type" auch für einen
        Owner, der das Attribut überhaupt nicht besitzt (verteidigende
        Codierung für künftige Owner-Typen). Ohne den ``getattr``-Default
        wird aus dem dokumentierten ``Http404`` ein unbehandeltes
        ``AttributeError`` — eine kontrollierte Ablehnung wird zu einem
        unbehandelten Serverfehler.
        """
        from django.http import Http404

        from core.services.compliance.sensitivity import get_visible_attachment_for_owner_or_404

        ft = _ft(facility, "Beleg-Gate-Stub")
        att = _attach(
            facility,
            staff_user,
            ft,
            client=Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.IDENTIFIED,
                pseudonym="Pin-Stub-01",
                created_by=staff_user,
            ),
        )

        class _OwnerWithoutDocumentTypeAttribute:
            """Owner-Stub OHNE ``document_type``-Attribut überhaupt (nicht
            nur mit Wert ``None``) — trennt den getattr-Default explizit vom
            bereits bestehenden Nullwert-Fall."""

            def __init__(self, attachments_qs):
                self.attachments = attachments_qs

        stub_owner = _OwnerWithoutDocumentTypeAttribute(Attachment.objects.filter(pk=att.pk))
        with pytest.raises(Http404):
            get_visible_attachment_for_owner_or_404(lead_user, stub_owner, att.pk)


class TestEventAttachmentGateSelectRelatedDurchreichen:
    """``get_visible_attachment_or_404`` reicht ``select_related=
    ("document_type",)`` an ``get_visible_event_or_404`` durch."""

    def test_document_type_select_related_wird_durchgereicht(
        self, facility, lead_user, staff_user, sample_event, django_assert_num_queries
    ):
        """Pin: ``x_get_visible_attachment_or_404__mutmut_5``
        (``select_related=("document_type",)`` -> ``select_related=None``),
        ``x_get_visible_attachment_or_404__mutmut_9`` (das
        ``select_related``-Keyword ganz entfernt, gleicher Effekt über den
        Default).

        ``get_visible_attachment_for_owner_or_404`` liest direkt danach
        ``getattr(owner, "document_type", None)`` — ohne den
        durchgereichten select_related-Pfad löst der Zugriff auf
        ``event.document_type`` eine N+1-Zusatzquery aus.
        """
        from core.services.compliance.sensitivity import get_visible_attachment_or_404

        ft = _ft(facility, "Beleg-EventGate-SR")
        att = _attach(facility, staff_user, ft, event=sample_event)

        with django_assert_num_queries(2):
            found_event, found_attachment = get_visible_attachment_or_404(lead_user, facility, sample_event.pk, att.pk)
            _ = found_event.document_type.sensitivity

        assert found_attachment == att


class TestHttp404MeldungenSindExakt:
    """Die drei Fail-Closed-``Http404``-Stellen des Download-Gates tragen
    einen festen Meldungstext. Bestand prüfte bisher nur den Exception-Typ
    (``pytest.raises(Http404)``), nie den Wortlaut — ein Vergleich per
    ``==`` (nie ``match=``, das ist Teilstring-Suche und übersieht sowohl
    den ``None``-Verlust als auch mutmuts ``XX…XX``-Marker) deckt den
    kompletten Wortlaut inklusive Groß-/Kleinschreibung ab."""

    def test_event_not_found_meldung_ist_exakt(self, facility, lead_user):
        """Pin: ``x_get_visible_event_or_404__mutmut_13`` (Meldung ->
        ``None``), ``_14`` (``"XXEvent not foundXX"``), ``_15``
        (``"event not found"``), ``_16`` (``"EVENT NOT FOUND"``).

        ``str(exc.value)`` ist bei einer mit einem einzelnen String-Argument
        erzeugten Exception exakt dieser String; bei ``Http404(None)`` ist es
        die Zeichenkette ``"None"``. Nur ein exakter Vergleich unterscheidet
        alle vier Varianten vom Original.
        """
        import uuid

        from django.http import Http404

        from core.services.compliance.sensitivity import get_visible_event_or_404

        with pytest.raises(Http404) as exc_info:
            get_visible_event_or_404(lead_user, facility, uuid.uuid4())

        assert str(exc_info.value) == "Event not found"

    def test_attachment_not_found_meldung_ist_exakt_bei_fehlendem_anhang(
        self, facility, lead_user, staff_user, client_identified, doc_type_client_profile
    ):
        """Pin: ``x_get_visible_attachment_for_owner_or_404__mutmut_11``
        (Meldung -> ``None``), ``_12`` (``"XXAttachment not foundXX"``),
        ``_13`` (``"attachment not found"``), ``_14``
        (``"ATTACHMENT NOT FOUND"``) — alle im Zweig ``attachment is None``.
        """
        import uuid

        from django.http import Http404

        from core.services.compliance.sensitivity import get_visible_attachment_for_owner_or_404

        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type"])

        with pytest.raises(Http404) as exc_info:
            get_visible_attachment_for_owner_or_404(lead_user, client_identified, uuid.uuid4())

        assert str(exc_info.value) == "Attachment not found"

    def test_attachment_not_found_meldung_ist_exakt_bei_fehlendem_dokumenttyp(
        self, facility, lead_user, staff_user, client_identified
    ):
        """Pin: ``x_get_visible_attachment_for_owner_or_404__mutmut_24``
        (Meldung -> ``None``), ``_25`` (``"XXAttachment not foundXX"``),
        ``_26`` (``"attachment not found"``), ``_27``
        (``"ATTACHMENT NOT FOUND"``) — alle im Zweig ``document_type is
        None`` (Owner ohne Dokumentationstyp, Fail-Closed nach ADR-039 §12).
        """
        from django.http import Http404

        from core.services.compliance.sensitivity import get_visible_attachment_for_owner_or_404

        assert client_identified.document_type_id is None
        ft = _ft(facility, "Beleg-Meldung-Doctype")
        att = _attach(facility, staff_user, ft, client=client_identified)

        with pytest.raises(Http404) as exc_info:
            get_visible_attachment_for_owner_or_404(lead_user, client_identified, att.pk)

        assert str(exc_info.value) == "Attachment not found"

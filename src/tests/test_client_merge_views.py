"""Duplikat-Merge fuer Personen — View-Schicht (Refs #1876, ADR-045).

Geprueft werden die Oberflaechen ueber dem Merge-Service
(:mod:`core.services.client.merge`):

* Einstieg + Zielsuche + Bestaetigungsseite (``ClientMergeView``) — die Seite
  traegt die Beweislast (Lehre #1874): zwei aehnliche Personen muessen auf ihr
  unterscheidbar sein (Stufen, Anlagedatum, letzter Kontakt, Bestandszahlen).
* Der gestaffelte POST (Lehre #1818): der PENDING-Check steht VOR der Weiche —
  eine Kontaktstufen-Herabstufung waehrend eines offenen Antrags darf den
  Vier-Augen-Weg nicht umgehen.
* Genehmigen/Ablehnen ueber die bestehende Review-Flaeche
  (``DeletionRequestReviewView``) inkl. fail-closed-Schranke fuer Rollen ohne
  Fall-Domaene (Muster ``may_see_case``, Refs #1818).
* Papierkorb: der Tombstone weist aus, WOHIN die Akte ging, und bietet das
  Aufheben statt eines Wiederherstellens an, das der Service abweist.
* Praeventiver Duplikat-Hinweis im Anlagepfad (``ClientSimilarHintView``).
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, Client, DocumentType, Event, LegalHold
from core.models.workitem import DeletionRequest
from core.services.client.merge import merge_clients, request_client_merge

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Merge-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Merge-Ziel",
        created_by=staff_user,
    )


def _merge_url(client_obj, target_obj=None):
    url = reverse("core:client_merge", kwargs={"pk": client_obj.pk})
    if target_obj is not None:
        url = f"{url}?target={target_obj.pk}"
    return url


class TestMergeEntryAndSearch:
    def test_search_step_lists_candidates_with_target_links(self, client, staff_user, source, target):
        client.force_login(staff_user)
        response = client.get(f"{_merge_url(source)}?q=Merge")
        html = response.content.decode()
        assert response.status_code == 200
        assert "Merge-Ziel" in html
        assert f"?target={target.pk}" in html

    def test_search_excludes_self_and_trashed(self, client, staff_user, facility, source):
        Client.objects.create(
            facility=facility,
            pseudonym="Merge-Papierkorb",
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        client.force_login(staff_user)
        html = client.get(f"{_merge_url(source)}?q=Merge").content.decode()
        assert "Merge-Papierkorb" not in html
        assert f"?target={source.pk}" not in html

    def test_assistant_cannot_open(self, client, assistant_user, source):
        client.force_login(assistant_user)
        assert client.get(_merge_url(source)).status_code == 403


class TestMergeConfirmStep:
    def test_confirm_shows_both_persons_counts_and_warnings(
        self, client, lead_user, staff_user, facility, source, target, doc_type_contact
    ):
        case = Case.objects.create(facility=facility, client=source, title="Fall", created_by=staff_user)
        for _i in range(2):
            Event.objects.create(
                facility=facility,
                client=source,
                case=case,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
        client.force_login(lead_user)
        response = client.get(_merge_url(source, target))
        html = response.content.decode()
        assert "Merge-Quelle" in html
        assert "Merge-Ziel" in html
        # Bestandszahlen der Quelle (2 Ereignisse, 1 Fall) stehen auf der Seite.
        assert 'data-testid="merge-source-events">2<' in html
        assert 'data-testid="merge-source-cases">1<' in html
        # Statistik-Warnung und Fristablauf-Hinweis tragen die Beweislast.
        assert 'data-testid="merge-statistics-warning"' in html
        assert 'data-testid="merge-deadline-hint"' in html

    def test_lead_sees_direct_submit_for_identified_pair(self, client, lead_user, source, target):
        client.force_login(lead_user)
        html = client.get(_merge_url(source, target)).content.decode()
        assert 'data-testid="merge-direct-submit"' in html
        assert 'name="reason"' not in html

    def test_staff_sees_request_form_even_for_identified_pair(self, client, staff_user, source, target):
        client.force_login(staff_user)
        html = client.get(_merge_url(source, target)).content.decode()
        assert 'data-testid="merge-request-submit"' in html
        assert 'name="reason"' in html

    def test_qualified_pair_shows_four_eyes_form_with_stage_upgrade_hint(
        self, client, lead_user, client_qualified, target
    ):
        client.force_login(lead_user)
        html = client.get(_merge_url(client_qualified, target)).content.decode()
        assert 'data-testid="merge-request-submit"' in html
        assert 'name="reason"' in html
        assert 'data-testid="merge-stage-upgrade-hint"' in html

    def test_pending_request_replaces_form(self, client, lead_user, staff_user, source, target):
        request_client_merge(source, target, staff_user, reason="Doppelt angelegt")
        client.force_login(lead_user)
        html = client.get(_merge_url(source, target)).content.decode()
        assert 'data-testid="merge-already-pending"' in html
        assert 'data-testid="merge-direct-submit"' not in html
        assert 'data-testid="merge-request-submit"' not in html


class TestMergePost:
    def test_lead_direct_post_merges(self, client, lead_user, source, target):
        client.force_login(lead_user)
        response = client.post(_merge_url(source), {"target": str(target.pk)})
        assert response.status_code == 302
        assert response.url == reverse("core:client_detail", kwargs={"pk": target.pk})
        source.refresh_from_db()
        assert source.is_deleted is True
        assert source.merged_into_id == target.pk

    def test_staff_post_creates_request(self, client, staff_user, source, target):
        client.force_login(staff_user)
        response = client.post(_merge_url(source), {"target": str(target.pk), "reason": "Doppelt angelegt"})
        assert response.status_code == 302
        source.refresh_from_db()
        assert source.is_deleted is False
        dr = DeletionRequest.objects.get(target_id=source.pk)
        assert dr.merge_into_id == target.pk
        assert dr.status == DeletionRequest.Status.PENDING

    def test_four_eyes_post_without_reason_creates_nothing(self, client, lead_user, client_qualified, target):
        client.force_login(lead_user)
        response = client.post(_merge_url(client_qualified), {"target": str(target.pk)})
        assert response.status_code == 302
        assert not DeletionRequest.objects.filter(target_id=client_qualified.pk).exists()
        target.refresh_from_db()
        assert target.is_deleted is False

    def test_pending_request_blocks_direct_path_after_downgrade(
        self, client, lead_user, staff_user, client_qualified, target
    ):
        """Race aus #1818: Herabstufung waehrend eines offenen Antrags darf den
        Vier-Augen-Weg nicht auf den Direktweg umschalten."""
        request_client_merge(client_qualified, target, staff_user, reason="Doppelt")
        Client.objects.filter(pk=client_qualified.pk).update(contact_stage=Client.ContactStage.IDENTIFIED)
        client.force_login(lead_user)
        response = client.post(_merge_url(client_qualified), {"target": str(target.pk)})
        assert response.status_code == 302
        client_qualified.refresh_from_db()
        assert client_qualified.is_deleted is False
        assert (
            DeletionRequest.objects.filter(target_id=client_qualified.pk, status=DeletionRequest.Status.PENDING).count()
            == 1
        )

    def test_post_without_target_redirects_without_merge(self, client, lead_user, source):
        client.force_login(lead_user)
        response = client.post(_merge_url(source), {})
        assert response.status_code == 302
        source.refresh_from_db()
        assert source.is_deleted is False

    def test_service_validation_error_becomes_message_not_500(self, client, lead_user, source, target):
        LegalHold.objects.create(
            facility=source.facility,
            target_type="Client",
            target_id=target.pk,
            reason="Gerichtsverfahren",
            created_by=lead_user,
        )
        client.force_login(lead_user)
        response = client.post(_merge_url(source), {"target": str(target.pk)})
        assert response.status_code == 302
        source.refresh_from_db()
        assert source.is_deleted is False


class TestMergeReview:
    @pytest.fixture
    def merge_request(self, source, target, staff_user):
        return request_client_merge(source, target, staff_user, reason="Doppelt angelegt")

    def test_review_page_shows_merge_context(self, client, admin_user, merge_request, source, target):
        client.force_login(admin_user)
        response = client.get(reverse("core:deletion_review", kwargs={"pk": merge_request.pk}))
        html = response.content.decode()
        assert response.status_code == 200
        assert 'data-testid="merge-review-page"' in html
        assert "Merge-Quelle" in html
        assert "Merge-Ziel" in html

    def test_review_approve_executes_merge(self, client, admin_user, merge_request, source, target):
        client.force_login(admin_user)
        response = client.post(reverse("core:deletion_review", kwargs={"pk": merge_request.pk}), {"action": "approve"})
        assert response.status_code == 302
        source.refresh_from_db()
        merge_request.refresh_from_db()
        assert source.merged_into_id == target.pk
        assert merge_request.status == DeletionRequest.Status.APPROVED

    def test_review_reject_moves_nothing(self, client, admin_user, merge_request, source):
        client.force_login(admin_user)
        client.post(reverse("core:deletion_review", kwargs={"pk": merge_request.pk}), {"action": "reject"})
        source.refresh_from_db()
        merge_request.refresh_from_db()
        assert source.is_deleted is False
        assert merge_request.status == DeletionRequest.Status.REJECTED

    def test_requester_cannot_approve_own_merge(self, client, lead_user, source, target):
        dr = request_client_merge(source, target, lead_user, reason="Doppelt")
        client.force_login(lead_user)
        response = client.post(reverse("core:deletion_review", kwargs={"pk": dr.pk}), {"action": "approve"})
        assert response.status_code == 302
        dr.refresh_from_db()
        assert dr.status == DeletionRequest.Status.PENDING

    def test_assistant_confirmer_sees_decision_but_no_inventory(self, client, assistant_user, merge_request):
        """Fail-closed wie beim Fall-Loeschantrag (Refs #1818): die Assistenz
        traegt ``can_confirm_deletion``, aber die Fall-Domaene ist zu — die
        Bestandszahlen (Faelle/Ereignisse) bleiben ihr verborgen, die
        Entscheidung selbst nicht."""
        assistant_user.can_confirm_deletion = True
        assistant_user.save(update_fields=["can_confirm_deletion"])
        client.force_login(assistant_user)
        html = client.get(reverse("core:deletion_review", kwargs={"pk": merge_request.pk})).content.decode()
        assert 'data-testid="merge-review-page"' in html
        assert 'data-testid="merge-review-not-visible"' in html
        assert 'data-testid="merge-source-events"' not in html
        assert 'value="approve"' in html

    def test_list_marks_merge_requests(self, client, admin_user, merge_request, target):
        client.force_login(admin_user)
        html = client.get(reverse("core:deletion_request_list")).content.decode()
        assert 'data-testid="deletion-merge-chip"' in html
        assert "Merge-Ziel" in html


class TestDetailSurfaces:
    def test_detail_banner_names_merge(self, client, staff_user, source, target):
        request_client_merge(source, target, staff_user, reason="Doppelt")
        client.force_login(staff_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": source.pk})).content.decode()
        assert 'data-testid="client-merge-pending-banner"' in html
        assert 'data-testid="client-deletion-pending-banner"' not in html

    def test_detail_action_links_to_merge_in_overflow(self, client, admin_user, source):
        client.force_login(admin_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": source.pk})).content.decode()
        assert f'href="{reverse("core:client_merge", kwargs={"pk": source.pk})}"' in html

    def test_assistant_sees_no_merge_action(self, client, assistant_user, source):
        client.force_login(assistant_user)
        html = client.get(reverse("core:client_detail", kwargs={"pk": source.pk})).content.decode()
        assert reverse("core:client_merge", kwargs={"pk": source.pk}) not in html


class TestTrashAndUnmerge:
    def test_trash_names_merge_target_and_offers_unmerge(self, client, admin_user, lead_user, source, target):
        merge_clients(source, target, lead_user)
        client.force_login(admin_user)
        html = client.get(reverse("core:client_trash")).content.decode()
        assert 'data-testid="client-trash-merged-into"' in html
        assert "Merge-Ziel" in html
        assert f'action="{reverse("core:client_unmerge", kwargs={"pk": source.pk})}"' in html
        # Kein wirkungsloses „Wiederherstellen" fuer den Tombstone — der
        # Service weist es ab, der konsistente Rueckweg ist das Aufheben.
        assert f'action="{reverse("core:client_restore", kwargs={"pk": source.pk})}"' not in html

    def test_unmerge_post_restores_source(self, client, admin_user, lead_user, source, target):
        merge_clients(source, target, lead_user)
        client.force_login(admin_user)
        response = client.post(reverse("core:client_unmerge", kwargs={"pk": source.pk}))
        assert response.status_code == 302
        source.refresh_from_db()
        assert source.is_deleted is False
        assert source.merged_into_id is None

    def test_unmerge_view_gate_is_admin_only(self, client, lead_user, source, target):
        merge_clients(source, target, lead_user)
        client.force_login(lead_user)
        assert client.post(reverse("core:client_unmerge", kwargs={"pk": source.pk})).status_code == 403

    def test_unmerge_on_plain_trashed_client_shows_error(self, client, admin_user, client_trashed):
        client.force_login(admin_user)
        response = client.post(reverse("core:client_unmerge", kwargs={"pk": client_trashed.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:client_trash")
        client_trashed.refresh_from_db()
        assert client_trashed.is_deleted is True

    def test_restore_post_on_tombstone_errors_instead_of_500(self, client, admin_user, lead_user, source, target):
        merge_clients(source, target, lead_user)
        client.force_login(admin_user)
        response = client.post(reverse("core:client_restore", kwargs={"pk": source.pk}))
        assert response.status_code == 302
        source.refresh_from_db()
        assert source.is_deleted is True


class TestSimilarHintInCreatePath:
    def test_create_form_wires_the_hint(self, client, staff_user):
        client.force_login(staff_user)
        html = client.get(reverse("core:client_create")).content.decode()
        assert reverse("core:client_similar_hint") in html

    def test_hint_lists_similar_clients_with_link(self, client, staff_user, facility):
        existing = Client.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        client.force_login(staff_user)
        response = client.get(f"{reverse('core:client_similar_hint')}?q=Schmitt")
        html = response.content.decode()
        assert response.status_code == 200
        assert "Schmidt" in html
        assert reverse("core:client_detail", kwargs={"pk": existing.pk}) in html

    def test_hint_is_empty_for_short_query(self, client, staff_user, facility):
        Client.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        client.force_login(staff_user)
        html = client.get(f"{reverse('core:client_similar_hint')}?q=S").content.decode()
        assert "Schmidt" not in html

    def test_hint_excludes_trashed(self, client, staff_user, facility):
        Client.objects.create(
            facility=facility,
            pseudonym="Schmidt",
            is_deleted=True,
            deleted_at=timezone.now(),
        )
        client.force_login(staff_user)
        html = client.get(f"{reverse('core:client_similar_hint')}?q=Schmidt").content.decode()
        assert "Schmidt" not in html

    def test_hint_requires_staff(self, client, assistant_user):
        client.force_login(assistant_user)
        assert client.get(f"{reverse('core:client_similar_hint')}?q=Schmidt").status_code == 403


class TestListBadge:
    def test_list_names_merge_request_not_deletion(self, client, staff_user, source, target):
        request_client_merge(source, target, staff_user, reason="Doppelt")
        client.force_login(staff_user)
        html = client.get(reverse("core:client_list")).content.decode()
        assert 'data-testid="client-row-merge-pending-badge"' in html
        assert 'data-testid="client-row-deletion-pending-badge"' not in html


class TestMalformedTarget:
    """`?target=`/POST-``target`` sind rohe Query-Werte ohne URL-Konverter —
    ein manipulierter Nicht-UUID-Wert darf kein 500 ausloesen."""

    def test_malformed_target_on_get_is_a_404_not_a_500(self, client, staff_user, source):
        client.force_login(staff_user)
        response = client.get(f"{_merge_url(source)}?target=kein-uuid")
        assert response.status_code in (302, 404)

    def test_malformed_target_on_post_is_a_404_not_a_500(self, client, lead_user, source):
        client.force_login(lead_user)
        response = client.post(_merge_url(source), {"target": "kein-uuid"})
        assert response.status_code in (302, 404)
        source.refresh_from_db()
        assert source.is_deleted is False


class TestTombstoneInvisibility:
    """Der Tombstone verhaelt sich wie jeder Papierkorb-Client (ADR-045):
    die bestehenden ``is_deleted``-Gates fassen ihn auf allen Suchflaechen."""

    @pytest.fixture
    def tombstone(self, source, target, lead_user):
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        return source

    def test_tombstone_is_gone_from_detail_list_search_and_autocomplete(self, client, staff_user, tombstone):
        client.force_login(staff_user)
        detail_url = reverse("core:client_detail", kwargs={"pk": tombstone.pk})
        assert client.get(detail_url).status_code == 404
        assert "Merge-Quelle" not in client.get(reverse("core:client_list")).content.decode()
        # Die Suchseite echot den Suchbegriff — geprueft wird der Treffer-LINK.
        assert detail_url not in client.get(f"{reverse('core:search')}?q=Merge-Quelle").content.decode()
        assert "Merge-Quelle" not in client.get(f"{reverse('core:client_autocomplete')}?q=Merge-Qu").content.decode()
        assert (
            "Merge-Quelle" not in client.get(f"{reverse('core:client_similar_hint')}?q=Merge-Quelle").content.decode()
        )


class TestMergeFactsSensitivity:
    """Refs #2112 (Red-Team B-1): Die Bestandszahlen enden an der Sichtbarkeitsgrenze.

    ``merge_facts`` zaehlte Ereignisse, Anhaenge und den letzten Kontakt ohne
    ``visible_to(user)`` — anders als Personenakte, Zeitstrom und
    Anhang-Uebersicht. Eine Fachkraft (Hoechstsensitivitaet ``erhoeht``) las auf
    der Bestaetigungsseite die Gesamtzahl inklusive ``hochvertraulich``-Doku
    **und** den Zeitpunkt des letzten Kontakts, obwohl das ausloesende Ereignis
    fuer sie hinter 404 liegt (``get_visible_event_or_404``).
    """

    @pytest.fixture
    def profil_typ(self, facility):
        """Personen-Dokumentationstyp ``erhoeht`` — traegt die Owner-Sensitivitaet
        der personeneigenen Anhaenge (``owner_sensitivity``, ADR-039 §12)."""
        return DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            name="Personenprofil (erhoeht)",
        )

    @pytest.fixture
    def typ_erhoeht(self, facility):
        return DocumentType.objects.create(
            facility=facility,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            name="Kontakt (erhoeht)",
        )

    @pytest.fixture
    def typ_hoch(self, facility):
        return DocumentType.objects.create(
            facility=facility,
            sensitivity=DocumentType.Sensitivity.HIGH,
            name="Kontakt (hochvertraulich)",
        )

    @pytest.fixture
    def person(self, facility, staff_user, profil_typ, typ_erhoeht, typ_hoch):
        """Person mit einem sichtbaren und einem unsichtbaren Ereignis + Anhang.

        Das HIGH-Ereignis ist bewusst das **juengere**: sonst bliebe der Fehler
        am ``last_contact`` unsichtbar.
        """
        p = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Sensitiv-Quelle",
            document_type=profil_typ,
            created_by=staff_user,
        )
        p.erhoeht_am = timezone.now() - timedelta(days=5)
        Event.objects.create(
            facility=facility,
            client=p,
            document_type=typ_erhoeht,
            occurred_at=p.erhoeht_am,
            data_json={},
            created_by=staff_user,
        )
        p.hoch_am = timezone.now() - timedelta(days=1)
        Event.objects.create(
            facility=facility,
            client=p,
            document_type=typ_hoch,
            occurred_at=p.hoch_am,
            data_json={},
            created_by=staff_user,
        )
        return p

    @pytest.fixture
    def person_mit_anhaengen(self, facility, staff_user, person):
        """Zwei personeneigene Anhaenge: einer ueber eine normale, einer ueber
        eine ``hochvertraulich``-Feldvorlage (``field_template__sensitivity``)."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from core.models import FieldTemplate
        from core.services.file_vault import store_encrypted_file
        from tests.conftest import PDF_MINIMAL

        for name, sensitivity in (("Ausweis (normal)", ""), ("Gutachten (hoch)", "high")):
            ft = FieldTemplate.objects.create(
                facility=facility,
                name=name,
                field_type=FieldTemplate.FieldType.FILE,
                sensitivity=sensitivity,
            )
            store_encrypted_file(
                facility,
                SimpleUploadedFile("probe.pdf", PDF_MINIMAL, content_type="application/pdf"),
                ft,
                person,
                staff_user,
            )
        return person

    def test_merge_facts_respect_sensitivity(self, person_mit_anhaengen, staff_user, lead_user):
        from core.views.client_merge import merge_facts

        person = person_mit_anhaengen

        fuer_fachkraft = merge_facts(person, staff_user)
        assert fuer_fachkraft["events"] == 1, "hochvertrauliches Ereignis zaehlt fuer die Fachkraft mit"
        assert fuer_fachkraft["attachments"] == 1, "hochvertraulicher Anhang zaehlt fuer die Fachkraft mit"
        assert fuer_fachkraft["last_contact"] == person.erhoeht_am, (
            "der letzte Kontakt verraet den Zeitpunkt eines fuer die Rolle unsichtbaren Ereignisses"
        )

        fuer_leitung = merge_facts(person, lead_user)
        assert fuer_leitung["events"] == 2
        assert fuer_leitung["attachments"] == 2
        assert fuer_leitung["last_contact"] == person.hoch_am

    def test_cases_stay_unfiltered_because_there_is_no_case_sensitivity(
        self, person_mit_anhaengen, facility, staff_user, lead_user
    ):
        """Bewusste Nicht-Filterung (ADR-039 §12): Faelle sind rollen-, nicht
        sensitivitaetsgegatet, und diese Seite erreicht nur ``is_staff_or_above``."""
        from core.views.client_merge import merge_facts

        Case.objects.create(
            facility=facility,
            client=person_mit_anhaengen,
            title="Fall",
            created_by=staff_user,
        )
        assert merge_facts(person_mit_anhaengen, staff_user)["cases"] == 1
        assert merge_facts(person_mit_anhaengen, lead_user)["cases"] == 1

    def test_confirmation_page_shows_the_filtered_numbers(self, client, staff_user, lead_user, person, target):
        """Die Seite selbst — nicht nur der Helfer — muss die gefilterte Zahl zeigen."""
        client.force_login(staff_user)
        body = client.get(_merge_url(person, target)).content.decode()
        assert '<td class="py-1.5 pr-2" data-testid="merge-source-events">1</td>' in body, body[:0] or "Fachkraft"

        client.force_login(lead_user)
        body = client.get(_merge_url(person, target)).content.decode()
        assert '<td class="py-1.5 pr-2" data-testid="merge-source-events">2</td>' in body

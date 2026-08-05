"""E2E: Art.-16-Berichtigungsworkflow (ADR-041, M4, Refs #1060).

Manuell verifiziert am 2026-08-05 (Antrag → Prüfung → Genehmigung, Ablehnung
mit Pflicht-Prüfvermerk, Selbst-Review-Sperre, AuthZ-Proben); die Tests hier
sind aus den beobachteten Schritten abgeleitet.

**Wegwerf-Daten-Strategie** (Lehre aus #1787/#1788): Jeder Test arbeitet auf
einer *eigenen* Person mit dem Präfix ``E2E-RECT-<hex8>``. Das ist hier
zwingend, nicht kosmetisch — der Workflow ändert Stammdaten (``notes``,
``data_json``) dauerhaft und irreversibel. Ein Antrag auf eine Seed-Person
verschöbe die Erwartungen von Export-, Statistik- und Zeitstrom-Tests im
gemeinsamen Voll-Lauf, und ein liegengebliebener PENDING-Antrag könnte von
einer anderen Datei genehmigt werden (genau das Muster, an dem
``test_client_deletion_workflow`` schon einmal gescheitert ist).

Der ``data_json``-Fall braucht einen Personen-Dokumentationstyp mit einem
Nicht-FILE-Feld. Der Seed liefert nur ``Personenakte`` mit einem einzigen
FILE-Feld (``anhang``) — FILE-Felder sind vom Berichtigungsworkflow bewusst
ausgeschlossen. Deshalb legt ``rect_run`` per ORM einen eigenen Wegwerf-Typ mit
einem unverschlüsselten TEXT-Feld an und räumt ihn am Modulende wieder ab.
"""

import json
import re
import subprocess
import sys
import uuid
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

_CLIENT_DETAIL_RE = re.compile(r"/clients/[0-9a-f-]{36}/$")
_REVIEW_RE = re.compile(r"/rectification/[0-9a-f-]{36}/review/$")
_LIST_RE = re.compile(r"/rectification/$")


def _shell(e2e_env, code):
    """``manage.py shell -c`` auf der Worker-DB; liefert stdout."""
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"shell fehlgeschlagen: {result.stderr}\n{result.stdout}"
    return result.stdout


def _json_line(out, key):
    """Eine mit ``key=<json>`` markierte Zeile aus der Shell-Ausgabe lesen."""
    line = next(ln for ln in out.splitlines() if ln.startswith(f"{key}="))
    return json.loads(line.removeprefix(f"{key}="))


@pytest.fixture(scope="module")
def rect_run(e2e_env):
    """Wegwerf-Namensraum + Wegwerf-Dokumentationstyp; räumt am Modulende ab.

    Der Typ trägt genau ein TEXT-Feld, unverschlüsselt und ohne eigene
    Sensitivity (erbt NORMAL vom Typ) — die schmalste Konfiguration, mit der
    ein ``data_json.<slug>``-Pfad überhaupt berichtigungsfähig ist. Für die
    v1-Abdeckung reicht das; die Verschlüsselungs-Erhaltung ist im
    Service-Unittest gepinnt.
    """
    run_id = uuid.uuid4().hex[:8]
    prefix = f"E2E-RECT-{run_id}"
    slug = f"e2e-rect-{run_id}"
    out = _shell(
        e2e_env,
        "from core.models import DocumentType, DocumentTypeField, Facility, FieldTemplate; "
        "import json; "
        "f = Facility.objects.first(); "
        f"dt = DocumentType.objects.create(facility=f, name='E2E-Rect-Profil {run_id}', "
        "  entity_type=DocumentType.EntityType.CLIENT, sensitivity=DocumentType.Sensitivity.NORMAL); "
        f"ft = FieldTemplate.objects.create(facility=f, name='E2E Merkmal {run_id}', slug='{slug}', "
        "  field_type=FieldTemplate.FieldType.TEXT, is_encrypted=False, sensitivity=''); "
        "DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0); "
        "print('setup=' + json.dumps({'doc_type': str(dt.pk), 'field_template': str(ft.pk), 'name': ft.name}))",
    )
    setup = _json_line(out, "setup")
    state = {
        "prefix": prefix,
        "slug": slug,
        "doc_type_pk": setup["doc_type"],
        "field_label": setup["name"],
    }
    yield state

    # Reihenfolge: Personen zuerst (``Client.document_type`` ist PROTECT),
    # danach Zuordnung, Typ und Vorlage. Berichtigungsanträge hängen per
    # CASCADE an der Person und verschwinden mit ihr.
    _shell(
        e2e_env,
        "from core.models import Client, DocumentType, DocumentTypeField, FieldTemplate; "
        f"Client.objects.filter(pseudonym__startswith='{prefix}').delete(); "
        f"DocumentTypeField.objects.filter(document_type_id='{setup['doc_type']}').delete(); "
        f"DocumentType.objects.filter(pk='{setup['doc_type']}').delete(); "
        f"FieldTemplate.objects.filter(pk='{setup['field_template']}').delete(); "
        "print('cleanup ok')",
    )


def _make_client(e2e_env, rect_run, label, *, notes="", with_doc_type=False, data_value=None):
    """Wegwerf-Person per ORM anlegen; liefert ``(pseudonym, pk)``.

    Bewusst per ORM statt über das Personen-Formular: der Ausgangswert von
    ``notes``/``data_json`` ist damit exakt festgelegt (der Konflikt-Guard der
    Genehmigung vergleicht gegen genau diesen Wert), und der Test hängt nicht
    daran, welche Felder eine Rolle im Formular sieht.
    """
    pseudonym = f"{rect_run['prefix']}-{label}-{uuid.uuid4().hex[:4]}"
    doc_type = f"'{rect_run['doc_type_pk']}'" if with_doc_type else "None"
    data_json = json.dumps({rect_run["slug"]: data_value}) if data_value is not None else "{}"
    out = _shell(
        e2e_env,
        "from core.models import Client, Facility; "
        "import json; "
        "f = Facility.objects.first(); "
        f"c = Client.objects.create(facility=f, pseudonym='{pseudonym}', contact_stage='identified', "
        f"  age_cluster='18_26', notes={notes!r}, document_type_id={doc_type}, "
        f"  data_json=json.loads({data_json!r})); "
        "print('client=' + json.dumps({'pk': str(c.pk)}))",
    )
    return pseudonym, _json_line(out, "client")["pk"]


def _request_rectification(page, base_url, client_pk, *, field_path, new_value, reason):
    """Antrag über die UI stellen (Einstieg vom Personen-Detail)."""
    page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
    page.locator("[data-testid='client-rectification-create-link']").click()
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]{36}/rectification/new/$"))

    page.select_option("select[name='field_path']", value=field_path)
    page.fill("textarea[name='new_value']", new_value)
    page.fill("textarea[name='reason']", reason)
    page.locator("[data-testid='rectification-submit']").click()
    page.wait_for_url(_CLIENT_DETAIL_RE)
    expect(page.locator("text=Berichtigungsantrag gestellt").first).to_be_visible()


def _pending_card(page, base_url, pseudonym):
    """Die Antragskarte zu genau diesem Pseudonym in der Prüfliste."""
    page.goto(f"{base_url}/rectification/", wait_until="domcontentloaded")
    card = page.locator(f"[data-testid='rectification-request-card']:has-text('{pseudonym}')").first
    card.wait_for(state="visible", timeout=10000)
    return card


def _open_review(page, base_url, pseudonym):
    """Prüfseite zu diesem Pseudonym öffnen; liefert die Antrags-PK."""
    card = _pending_card(page, base_url, pseudonym)
    rr_pk = card.get_attribute("data-rr-pk")
    card.locator("[data-testid='rectification-review-link']").click()
    page.wait_for_url(_REVIEW_RE)
    return rr_pk


def _csrf_token(page):
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    token = cookies.get("csrftoken")
    assert token, "csrftoken-Cookie fehlt — Page muss vorher eine GET-Seite besucht haben."
    return token


class TestRectificationApprovalWorkflow:
    """miriam beantragt, emma prüft und genehmigt — Vier-Augen-Prinzip."""

    def test_staff_request_is_approved_by_lead_and_applied(self, staff_page, lead_page, base_url, e2e_env, rect_run):
        old_note = "Alter Vermerk aus der Erstaufnahme"
        new_note = "Korrigierter Vermerk nach Rücksprache"
        pseudonym, client_pk = _make_client(e2e_env, rect_run, "Notes", notes=old_note)

        # 1. Fachkraft stellt den Antrag.
        _request_rectification(
            staff_page,
            base_url,
            client_pk,
            field_path="notes",
            new_value=new_note,
            reason="Eintrag beruhte auf einer Verwechslung.",
        )
        # Am Datensatz ist der offene Antrag sichtbar (Rückkanal für die
        # antragstellende Fachkraft).
        expect(staff_page.locator("[data-testid='client-rectification-pending-badge']").first).to_be_visible()

        # 2. Leitung prüft — alter und neuer Wert stehen zur Abwägung nebeneinander.
        lead = lead_page
        rr_pk = _open_review(lead, base_url, pseudonym)
        assert lead.locator("[data-testid='rectification-old-value']").inner_text().strip() == old_note
        assert lead.locator("[data-testid='rectification-new-value']").inner_text().strip() == new_note
        assert lead.locator("[data-testid='rectification-self-review-blocked']").count() == 0

        lead.fill("textarea[name='review_note']", "Sachverhalt mit der Fachkraft geklärt.")
        lead.locator("[data-testid='rectification-approve']").click()
        lead.wait_for_url(_LIST_RE)
        expect(lead.locator("text=Berichtigung genehmigt und angewendet").first).to_be_visible()

        # 3. Der korrigierte Wert steht am Datensatz.
        lead.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
        expect(lead.locator(f"text={new_note}").first).to_be_visible()
        assert old_note not in lead.content()

        # 4. Audit-Nachweis: beide Schritte protokolliert — und in ``detail``
        #    steht KEIN Feldwert (die Werte leben verschlüsselt am Antrag).
        out = _shell(
            e2e_env,
            "from core.models import AuditLog, RectificationRequest; "
            "import json; "
            f"rr = RectificationRequest.objects.get(pk='{rr_pk}'); "
            f"logs = list(AuditLog.objects.filter(target_id='{rr_pk}').values('action', 'detail')); "
            "print('audit=' + json.dumps({'status': rr.status, 'logs': logs}))",
        )
        payload = _json_line(out, "audit")
        assert payload["status"] == "approved"
        actions = {entry["action"] for entry in payload["logs"]}
        assert {"rectification_requested", "rectification_approved"} <= actions
        for entry in payload["logs"]:
            serialized = json.dumps(entry["detail"], ensure_ascii=False)
            assert old_note not in serialized
            assert new_note not in serialized
            assert pseudonym not in serialized


class TestRectificationDataJsonField:
    """``data_json.<slug>``: dynamisches Zusatzfeld über denselben Weg berichtigen."""

    def test_dynamic_text_field_is_updated_after_approval(self, staff_page, lead_page, base_url, e2e_env, rect_run):
        slug = rect_run["slug"]
        old_value = "Wert vor der Korrektur"
        new_value = "Wert nach der Korrektur"
        pseudonym, client_pk = _make_client(e2e_env, rect_run, "Json", with_doc_type=True, data_value=old_value)

        _request_rectification(
            staff_page,
            base_url,
            client_pk,
            field_path=f"data_json.{slug}",
            new_value=new_value,
            reason="Zusatzfeld wurde beim Anlegen falsch übertragen.",
        )

        lead = lead_page
        _open_review(lead, base_url, pseudonym)
        # Das Feld erscheint mit seinem Label aus der Vorlage, nicht nur als Pfad.
        expect(lead.locator(f"text={rect_run['field_label']}").first).to_be_visible()
        assert lead.locator("[data-testid='rectification-old-value']").inner_text().strip() == old_value
        assert lead.locator("[data-testid='rectification-new-value']").inner_text().strip() == new_value

        lead.locator("[data-testid='rectification-approve']").click()
        lead.wait_for_url(_LIST_RE)

        out = _shell(
            e2e_env,
            "from core.models import Client; "
            "import json; "
            f"c = Client.objects.get(pk='{client_pk}'); "
            f"print('data=' + json.dumps({{'value': (c.data_json or {{}}).get('{slug}')}}))",
        )
        assert _json_line(out, "data")["value"] == new_value


class TestSelfReviewIsBlocked:
    """Wer selbst beantragt, darf nicht selbst genehmigen — UI **und** POST."""

    def test_own_request_offers_no_review_and_post_is_refused(self, lead_page, base_url, e2e_env, rect_run):
        lead = lead_page
        pseudonym, client_pk = _make_client(e2e_env, rect_run, "Self", notes="Ausgangswert Selbstpruefung")

        _request_rectification(
            lead,
            base_url,
            client_pk,
            field_path="notes",
            new_value="Von der Leitung selbst beantragt",
            reason="Prüfung der Selbst-Review-Sperre.",
        )

        # In der Liste trägt der eigene Antrag statt des Prüfen-Links den Hinweis.
        card = _pending_card(lead, base_url, pseudonym)
        rr_pk = card.get_attribute("data-rr-pk")
        assert card.locator("[data-testid='rectification-review-link']").count() == 0
        expect(card.locator("[data-testid='rectification-own-request-hint']").first).to_be_visible()

        # Direktaufruf der Prüfseite: Hinweis statt Entscheidungsformular.
        review_url = f"{base_url}/rectification/{rr_pk}/review/"
        lead.goto(review_url, wait_until="domcontentloaded")
        expect(lead.locator("[data-testid='rectification-self-review-blocked']").first).to_be_visible()
        assert lead.locator("[data-testid='rectification-approve']").count() == 0
        assert lead.locator("[data-testid='rectification-reject']").count() == 0

        # Die Sperre hängt nicht am fehlenden Formular: ein direkt abgesetztes
        # POST (Formular nachgebaut, CSRF aus der Session) wird ebenfalls
        # abgewiesen — der Guard sitzt im Service, nicht im Template.
        response = lead.request.post(
            review_url,
            data=urlencode({"action": "approve", "review_note": "Umgehungsversuch"}),
            headers={
                "X-CSRFToken": _csrf_token(lead),
                "Referer": review_url,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        # Exakt 400 (nicht nur „ungleich 200"): 400 ist die Fachfehler-Antwort
        # der View. Ein 403 hier hiesse, der POST waere schon am CSRF-Schutz
        # gescheitert — der Test haette dann gruen gemeldet, ohne den
        # Selbst-Review-Guard je erreicht zu haben.
        assert response.status == 400, f"Selbst-Genehmigung darf nicht durchgehen (Status {response.status})"

        out = _shell(
            e2e_env,
            "from core.models import Client, RectificationRequest; "
            "import json; "
            f"rr = RectificationRequest.objects.get(pk='{rr_pk}'); "
            f"c = Client.objects.get(pk='{client_pk}'); "
            "print('state=' + json.dumps({'status': rr.status, 'notes': c.notes}))",
        )
        state = _json_line(out, "state")
        assert state["status"] == "pending", "Antrag darf nach dem Umgehungsversuch nicht genehmigt sein"
        assert state["notes"] == "Ausgangswert Selbstpruefung", "Der Wert wurde trotz Sperre geändert"


class TestRejectionRequiresReviewNote:
    """Ablehnung ohne Prüfvermerk wird abgewiesen; mit Vermerk landet sie in der Liste."""

    def test_reject_without_note_fails_and_with_note_succeeds(self, staff_page, lead_page, base_url, e2e_env, rect_run):
        pseudonym, client_pk = _make_client(e2e_env, rect_run, "Reject", notes="Unveraenderter Ausgangswert")

        _request_rectification(
            staff_page,
            base_url,
            client_pk,
            field_path="notes",
            new_value="Abzulehnender Wunschwert",
            reason="Strittige Wahrnehmung — bewusst zur Ablehnung.",
        )

        lead = lead_page
        rr_pk = _open_review(lead, base_url, pseudonym)

        # Ohne Vermerk: Fehlermeldung auf derselben Seite, kein Statuswechsel.
        lead.locator("[data-testid='rectification-reject']").click()
        expect(lead.locator("text=Für eine Ablehnung ist ein Prüfvermerk erforderlich").first).to_be_visible()
        assert re.search(_REVIEW_RE, lead.url), f"Seite muss auf der Prüfansicht bleiben, ist {lead.url}"

        # Mit Vermerk: Ablehnung greift.
        lead.fill("textarea[name='review_note']", "Der bisherige Eintrag ist durch die Akte gedeckt.")
        lead.locator("[data-testid='rectification-reject']").click()
        lead.wait_for_url(_LIST_RE)
        expect(lead.locator("text=Berichtigungsantrag abgelehnt").first).to_be_visible()

        # Der Antrag steht im Abschnitt „Abgelehnt" und nicht mehr unter „Ausstehend".
        assert lead.locator(f"[data-testid='rectification-request-card']:has-text('{pseudonym}')").count() == 0
        expect(lead.locator(f"text={pseudonym}").first).to_be_visible()

        out = _shell(
            e2e_env,
            "from core.models import Client, RectificationRequest; "
            "import json; "
            f"rr = RectificationRequest.objects.get(pk='{rr_pk}'); "
            f"c = Client.objects.get(pk='{client_pk}'); "
            "print('state=' + json.dumps({'status': rr.status, 'notes': c.notes}))",
        )
        state = _json_line(out, "state")
        assert state["status"] == "rejected"
        assert state["notes"] == "Unveraenderter Ausgangswert", "Eine Ablehnung darf den Wert nicht anfassen"


class TestRectificationAccessControl:
    """Antragstellung ist staff+, die Prüfliste lead+."""

    def test_staff_gets_403_on_rectification_list(self, staff_page, base_url):
        resp = staff_page.goto(f"{base_url}/rectification/")
        assert resp.status == 403

    def test_assistant_gets_403_on_rectification_list(self, assistant_page, base_url):
        resp = assistant_page.goto(f"{base_url}/rectification/")
        assert resp.status == 403

    def test_assistant_cannot_open_request_form(self, assistant_page, base_url, e2e_env, rect_run):
        """Assistenz sieht am Datensatz weder Einstieg noch Formular (403)."""
        _pseudonym, client_pk = _make_client(e2e_env, rect_run, "Assist", notes="Assistenz-Probe")
        page = assistant_page
        page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
        assert page.locator("[data-testid='client-rectification-create-link']").count() == 0

        resp = page.goto(f"{base_url}/clients/{client_pk}/rectification/new/")
        assert resp.status == 403

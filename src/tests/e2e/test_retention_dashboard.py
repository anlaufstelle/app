"""E2E-Tests: Retention Dashboard — Löschfristen-Management."""

import re
import subprocess
import sys
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from ._selectors import sidebar_item, subnav_tab

pytestmark = pytest.mark.e2e

#: Both seeded accounts hold the "Löschbestätigung" right and a lead/admin role.
_APPROVER_A = "admin"
_APPROVER_B = "emma"


def _ensure_proposals(e2e_env, count=3):
    """Legt ``count`` pending-RetentionProposals über **eigene** Wegwerf-Daten an.

    Liefert die PKs der angelegten Proposals zurück; die Tests adressieren
    ihre Karten damit über ``#proposal-<pk>`` statt über ``.first``.

    Zwei Richtungen von Test-Isolation, beide hier nötig:

    1. *Fremde Side-Effects auf diesen Test* — andere Dateien approven oder
       deferren Proposals; deshalb verlässt sich dieser Helfer nicht auf
       Bestands-Proposals, sondern legt pro Aufruf frische an. (Die frühere
       ``get_or_create(... status__in=[...])``-Variante war zusätzlich falsch:
       ``__in`` überträgt Django in den ``GET``-Filter, nicht in den
       ``CREATE``-Pfad, und unique-Constraints verhinderten Re-Creates für
       bereits-approved Targets — isoliert grün, im geteilten Lauf rot.)
    2. *Dieser Test auf fremde Daten* — die Gegenrichtung, und die teurere:
       bis dahin band der Helfer seine Proposals an **beliebige** vorhandene
       Events (``Event.objects.filter(...)[:need]``), also im Voll-Lauf an
       Seed-Personen wie ``Blitz-08`` und an Events, die frühere Dateien
       angelegt hatten. Die Freigabe-/Hold-Tests haben damit fremden
       Lösch-/Hold-Zustand umgeschaltet: ein aufgehobener Legal Hold gibt ein
       fremdes Event für ``enforce_retention`` frei (``test_statistics_snapshot``
       fährt den Lauf im selben Suite-Durchgang), und sind danach *alle* Events
       einer Person soft-gelöscht, anonymisiert die K-Anon-Bridge sie
       (``is_active=False``) — nachfolgende Dateien finden die Person dann nicht
       mehr bzw. laufen ins Widerrufs-Gate (Refs #1771,
       ``resolve_writable_client``). Deshalb: eigener Wegwerf-Klient
       ``RET-E2E-<uuid8>``, eigene Events, Proposals **nur** darüber.

    ``manage.py shell -c "..."`` akzeptiert keine if/for-Statements, nur
    ``;``-getrennte einfache Statements — die Schleifen bleiben deshalb
    List-Comprehensions.
    """
    code = (
        "from core.models import Client, DocumentType, Event, Facility, RetentionProposal; "
        "from datetime import date, timedelta; "
        "from django.utils import timezone; "
        "import uuid; "
        "f = Facility.objects.get(name='Hauptstelle'); "
        "dt = DocumentType.objects.filter(facility=f, entity_type='event').first(); "
        "c = Client.objects.create(facility=f, contact_stage='identified', "
        "  pseudonym='RET-E2E-' + uuid.uuid4().hex[:8]); "
        "events = [Event.objects.create(facility=f, client=c, document_type=dt, "
        "  occurred_at=timezone.now(), data_json={}) "
        f" for _ in range({count})]; "
        "cats = ['anonymous', 'identified', 'qualified']; "
        "props = [RetentionProposal.objects.create("
        "  facility=f, target_type='Event', target_id=e.pk, "
        "  deletion_due_at=date.today() + timedelta(days=i*10+5), status='pending', "
        "  retention_category=cats[i % len(cats)], "
        "  details={'pseudonym': c.pseudonym, "
        "           'document_type': dt.name if dt else None, "
        "           'occurred_at': str(e.occurred_at)}) for i, e in enumerate(events)]; "
        "[print('proposal=' + str(p.pk)) for p in props]"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    pks = [ln.removeprefix("proposal=") for ln in result.stdout.splitlines() if ln.startswith("proposal=")]
    assert len(pks) == count, f"Seed failed: {result.stderr}\n{result.stdout}"
    return pks


def _set_approver_pool(e2e_env, eligible):
    """Make exactly the ``eligible`` usernames manual approvers of every facility's lead/admin pool.

    Returns ``{username: previous_flag}`` for :func:`_restore_approver_pool`. The
    flag is the right "Löschbestätigung"; other roles are left untouched.
    """
    script = (
        "from core.models import User; "
        "users = list(User.objects.filter(role__in=['lead', 'facility_admin'], is_active=True)); "
        f"keep = {sorted(eligible)!r}; "
        "[print('prev=' + u.username + ':' + str(u.can_confirm_deletion)) for u in users]; "
        "[User.objects.filter(pk=u.pk).update(can_confirm_deletion=(u.username in keep)) for u in users]"
    )
    previous = {}
    for line in django_shell(e2e_env, script):
        if line.startswith("prev="):
            name, flag = line.removeprefix("prev=").rsplit(":", 1)
            previous[name] = flag == "True"
    assert _APPROVER_A in previous and _APPROVER_B in previous, f"seed accounts missing: {previous}"
    return previous


def _restore_approver_pool(e2e_env, previous):
    script = (
        "from core.models import User; "
        f"prev = {previous!r}; "
        "[User.objects.filter(username=n).update(can_confirm_deletion=f) for n, f in prev.items()]"
    )
    django_shell(e2e_env, script)


def _set_language(e2e_env, usernames, code):
    """Set ``preferred_language`` (the only thing that picks the app locale); returns previous values."""
    script = (
        "from core.models import User; "
        f"names = {list(usernames)!r}; "
        "[print('lang=' + u.username + ':' + u.preferred_language) for u in User.objects.filter(username__in=names)]; "
        f"User.objects.filter(username__in=names).update(preferred_language={code!r})"
    )
    previous = {}
    for line in django_shell(e2e_env, script):
        if line.startswith("lang="):
            name, value = line.removeprefix("lang=").rsplit(":", 1)
            previous[name] = value
    return previous


def _restore_language(e2e_env, previous):
    script = (
        "from core.models import User; "
        f"prev = {previous!r}; "
        "[User.objects.filter(username=n).update(preferred_language=v) for n, v in prev.items()]"
    )
    django_shell(e2e_env, script)


def _proposal_state(e2e_env, pk):
    """``(status, first_approved_by username or '')`` straight from the database."""
    script = (
        "from core.models import RetentionProposal; "
        f"p = RetentionProposal.objects.select_related('first_approved_by').get(pk='{pk}'); "
        "print('state=' + p.status + ':' + (p.first_approved_by.username if p.first_approved_by else ''))"
    )
    line = next(ln for ln in django_shell(e2e_env, script) if ln.startswith("state="))
    status, first = line.removeprefix("state=").split(":", 1)
    return status, first


def _one_approver_per_facility(e2e_env):
    """Keep exactly one eligible lead/admin per facility; returns what :func:`_restore_one_approver` needs.

    The card is installation-wide: a facility without any lead/admin (other specs
    create facilities that cannot be deleted again) would make it critical. Such a
    facility gets a temporary approver that is deactivated on restore.
    """
    marker = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Facility, User; "
        "users = list(User.objects.filter(role__in=['lead', 'facility_admin'], is_active=True)"
        ".order_by('facility_id', 'username')); "
        "[print('prev=' + u.username + ':' + str(u.can_confirm_deletion)) for u in users]; "
        "seen = set(); "
        "keep = [u.pk for u in users if not (u.facility_id in seen or seen.add(u.facility_id))]; "
        "[User.objects.filter(pk=u.pk).update(can_confirm_deletion=(u.pk in keep)) for u in users]; "
        f"temps = [User.objects.create(username='e2e-approver-{marker}-' + str(i), role='lead', facility=f, "
        "  can_confirm_deletion=True) for i, f in enumerate(Facility.objects.exclude(pk__in=seen))]; "
        "[print('temp=' + u.username) for u in temps]"
    )
    previous = {}
    temporary = []
    for line in django_shell(e2e_env, script):
        if line.startswith("prev="):
            name, flag = line.removeprefix("prev=").rsplit(":", 1)
            previous[name] = flag == "True"
        elif line.startswith("temp="):
            temporary.append(line.removeprefix("temp="))
    return previous, temporary


def _restore_one_approver(e2e_env, state):
    previous, temporary = state
    _restore_approver_pool(e2e_env, previous)
    django_shell(
        e2e_env,
        "from core.models import User; "
        f"User.objects.filter(username__in={temporary!r}).update(is_active=False, can_confirm_deletion=False)",
    )


@pytest.fixture
def approver_pool(e2e_env):
    """Both seed approvers eligible; flags restored afterwards."""
    previous = _set_approver_pool(e2e_env, {_APPROVER_A, _APPROVER_B})
    try:
        yield
    finally:
        _restore_approver_pool(e2e_env, previous)


@pytest.fixture
def single_approver_pool(e2e_env):
    """Only the admin account is eligible; flags restored afterwards."""
    previous = _set_approver_pool(e2e_env, {_APPROVER_A})
    try:
        yield
    finally:
        _restore_approver_pool(e2e_env, previous)


class TestRetentionDashboardAccess:
    """Dashboard-Zugriff und Inhalt."""

    def test_lead_can_access_dashboard(self, lead_page, base_url, e2e_env):
        """Lead kann das Retention Dashboard aufrufen."""
        _ensure_proposals(e2e_env)
        page = lead_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1:has-text('Löschfristen')").is_visible()
        # Scoped to the summary card: the collapsed help text above it also
        # contains the word "ausstehend" and used to win ``text=Ausstehend``.
        expect(page.locator("#summary-pending")).to_contain_text("Ausstehend")
        expect(page.locator("#summary-pending")).to_be_visible()
        assert page.locator("h3:has-text('Aufbewahrungsfristen')").is_visible()

    def test_admin_can_access_dashboard(self, authenticated_page, base_url, e2e_env):
        """Admin kann das Retention Dashboard aufrufen."""
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1:has-text('Löschfristen')").is_visible()

    def test_staff_redirected_from_dashboard(self, staff_page, base_url):
        """Staff-User wird vom Dashboard abgewiesen."""
        page = staff_page
        resp = page.goto(f"{base_url}/retention/")
        # Should get 403 or redirect to login
        assert page.url != f"{base_url}/retention/" or resp.status == 403


_DE = {
    "first_button": "Erste Freigabe erteilen",
    "second_button": "Zweite Freigabe erteilen",
    "pending": "Ausstehend",
    "released": "Freigegeben",
    "progress": "1 von 2 Freigaben",
    "first_by": "Erste Freigabe durch:",
    "different_person": "Die zweite Freigabe muss eine andere berechtigte Person erteilen.",
    "will_delete": "Wird beim nächsten Lauf gelöscht",
}
_EN = {
    "first_button": "Grant first release",
    "second_button": "Grant second release",
    "pending": "Pending",
    "released": "Released",
    "progress": "1 of 2 releases",
    "first_by": "First released by:",
    "different_person": "The second release must be granted by a different authorised person.",
    "will_delete": "Will be deleted on next run",
}


def _badge(card, text):
    """The status badge span of a card with exactly this text."""
    return card.locator("span", has_text=re.compile(rf"^\s*{re.escape(text)}\s*$"))


def _open_dashboard(page, base_url):
    page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")
    page.on("dialog", lambda dialog: dialog.accept())


def _two_person_flow(page_a, page_b, base_url, pk, labels, e2e_env):
    """Person A approves twice (second try rejected), person B finalises; returns visible evidence."""
    evidence = {}
    _open_dashboard(page_a, base_url)
    card = page_a.locator(f"#proposal-{pk}")
    expect(card).to_be_visible(timeout=10000)
    expect(_badge(card, labels["pending"])).to_have_count(1)
    expect(card.get_by_text(labels["progress"])).to_have_count(0)

    # --- A: first release keeps the card pending and shows "1 of 2".
    card.get_by_role("button", name=labels["first_button"]).click()
    expect(card.get_by_text(labels["progress"])).to_be_visible(timeout=10000)
    expect(card.get_by_text(labels["first_by"])).to_be_visible()
    expect(_badge(card, labels["pending"])).to_have_count(1)
    expect(_badge(card, labels["released"])).to_have_count(0)
    evidence["progress_line"] = card.locator("p", has_text=labels["progress"]).first.inner_text()
    assert _proposal_state(e2e_env, pk) == ("pending", _APPROVER_A)

    # --- A again: rejected with a visible message, no status change, no error page.
    with page_a.expect_response(lambda r: f"/retention/{pk}/approve/" in r.url) as response_info:
        card.get_by_role("button", name=labels["second_button"]).click()
    assert response_info.value.status == 409
    expect(page_a.get_by_text(labels["different_person"])).to_be_visible(timeout=10000)
    evidence["rejection"] = labels["different_person"]
    assert page_a.locator("h1").first.is_visible()
    assert "/retention/" in page_a.url
    assert _proposal_state(e2e_env, pk) == ("pending", _APPROVER_A)
    page_a.reload(wait_until="domcontentloaded")
    card = page_a.locator(f"#proposal-{pk}")
    expect(card.get_by_text(labels["progress"])).to_be_visible(timeout=10000)
    expect(_badge(card, labels["released"])).to_have_count(0)

    # --- B: the second, different person finalises.
    _open_dashboard(page_b, base_url)
    card_b = page_b.locator(f"#proposal-{pk}")
    expect(card_b).to_be_visible(timeout=10000)
    expect(card_b.get_by_text(labels["progress"])).to_be_visible()
    expect(_badge(card_b, labels["pending"])).to_have_count(1)
    card_b.get_by_role("button", name=labels["second_button"]).click()
    expect(_badge(card_b, labels["released"])).to_have_count(1, timeout=10000)
    expect(card_b.get_by_text(labels["will_delete"])).to_be_visible()
    evidence["final_line"] = card_b.get_by_text(labels["will_delete"]).inner_text()
    assert _proposal_state(e2e_env, pk)[0] == "approved"
    return evidence


class TestRetentionApproveFlow:
    """Approve a proposal via HTMX — manual deletion approval by two different persons."""

    @pytest.mark.smoke
    def test_approve_proposal(self, authenticated_page, lead_page, base_url, e2e_env, approver_pool):
        """Admin approves first, the repetition is rejected, only the lead approves for good (DE)."""
        pks = _ensure_proposals(e2e_env)
        evidence = _two_person_flow(authenticated_page, lead_page, base_url, pks[0], _DE, e2e_env)
        assert "1 von 2 Freigaben" in evidence["progress_line"]
        assert "Erste Freigabe durch:" in evidence["progress_line"]

    def test_approve_proposal_english(
        self, browser, base_url, e2e_env, approver_pool, _login_storage_state, _lead_storage_state
    ):
        """The same path in English (browser locale and app language)."""
        pks = _ensure_proposals(e2e_env)
        previous = _set_language(e2e_env, [_APPROVER_A, _APPROVER_B], "en")
        context_a = browser.new_context(storage_state=_login_storage_state, locale="en-US")
        context_b = browser.new_context(storage_state=_lead_storage_state, locale="en-US")
        try:
            page_a, page_b = context_a.new_page(), context_b.new_page()
            page_a.set_default_timeout(30000)
            page_b.set_default_timeout(30000)
            evidence = _two_person_flow(page_a, page_b, base_url, pks[0], _EN, e2e_env)
            assert "1 of 2 releases" in evidence["progress_line"]
            assert "First released by:" in evidence["progress_line"]
            assert evidence["final_line"] == "Will be deleted on next run"
            # No German card text is left on the finished card.
            card = page_b.locator(f"#proposal-{pks[0]}")
            for german in ("Freigegeben", "Freigabe", "Ausstehend"):
                assert german not in card.inner_text()
        finally:
            context_a.close()
            context_b.close()
            _restore_language(e2e_env, previous)

    def test_single_approver_first_release_warns(self, authenticated_page, base_url, e2e_env, single_approver_pool):
        """With only one authorised account it stays at 1 of 2; the card names the pool that is too small."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        _open_dashboard(page, base_url)
        expect(page.get_by_text("Aktuell ist keine manuelle Löschfreigabe", exact=False)).to_be_visible(timeout=10000)
        card = page.locator(f"#proposal-{pks[0]}")
        card.get_by_role("button", name="Erste Freigabe erteilen").click()
        expect(card.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)
        expect(card.get_by_text("Weniger als zwei berechtigte Konten", exact=False)).to_be_visible()
        expect(_badge(card, "Freigegeben")).to_have_count(0)


class TestRetentionApproverComplianceCard:
    """System administration sees the yellow card when a facility has only one authorised account."""

    _CASES = {
        "de": (
            "Genehmiger für den Aufbewahrungslauf",
            "Nur ein Genehmiger für den Aufbewahrungslauf, keine manuelle Löschfreigabe möglich",
        ),
        "en": (
            "Approvers for the retention run",
            "Only one approver for the retention run, no manual deletion release possible",
        ),
    }

    @pytest.mark.parametrize("language", ["de", "en"])
    def test_single_approver_shows_yellow_card(self, browser, base_url, e2e_env, _super_admin_storage_state, language):
        previous_pool = _one_approver_per_facility(e2e_env)
        previous_lang = _set_language(e2e_env, ["superadmin"], language)
        locale = "de-DE" if language == "de" else "en-US"
        context = browser.new_context(storage_state=_super_admin_storage_state, locale=locale)
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(f"{base_url}/system/compliance/", wait_until="domcontentloaded")
            card = page.locator("[data-testid='compliance-check-retention_approvers']")
            expect(card).to_be_visible(timeout=10000)
            label, message = self._CASES[language]
            expect(card).to_contain_text(label)
            expect(card).to_contain_text(message)
            assert card.get_attribute("data-status") == "warning"
            # The status label carries the warning colour class (yellow), not green/red.
            expect(card.locator(".text-yellow-700")).to_have_count(1)
        finally:
            context.close()
            _restore_language(e2e_env, previous_lang)
            _restore_one_approver(e2e_env, previous_pool)


class TestRetentionFirstReleaseReset:
    """Hold, defer and reject discard the first approval."""

    def test_hold_clears_first_release(self, authenticated_page, base_url, e2e_env, approver_pool):
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        _open_dashboard(page, base_url)
        card = page.locator(f"#proposal-{pks[0]}")
        card.get_by_role("button", name="Erste Freigabe erteilen").click()
        expect(card.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)

        TestRetentionHoldFlow._set_hold(card, "E2E: Hold nach erster Freigabe")
        expect(_badge(card, "Legal Hold")).to_have_count(1, timeout=10000)
        assert _proposal_state(e2e_env, pks[0]) == ("held", "")

        card.get_by_role("button", name="Hold aufheben").click()
        # Back to a fresh start: first button again, no progress line.
        expect(card.get_by_role("button", name="Erste Freigabe erteilen")).to_be_visible(timeout=10000)
        expect(card.get_by_text("1 von 2 Freigaben")).to_have_count(0)
        assert _proposal_state(e2e_env, pks[0]) == ("pending", "")

    @pytest.mark.parametrize(
        ("button_testid", "expected_status"),
        [("retention-bulk-defer", "deferred"), ("retention-bulk-reject", "rejected")],
    )
    def test_defer_and_reject_clear_first_release(
        self, authenticated_page, base_url, e2e_env, approver_pool, button_testid, expected_status
    ):
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        _open_dashboard(page, base_url)
        card = page.locator(f"#proposal-{pks[0]}")
        card.get_by_role("button", name="Erste Freigabe erteilen").click()
        expect(card.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)
        assert _proposal_state(e2e_env, pks[0]) == ("pending", _APPROVER_A)

        card.locator("[data-bulk-proposal]").check()
        page.locator(f"[data-testid='{button_testid}']").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("[data-testid='retention-bulk-count']")).to_be_hidden(timeout=10000)
        status, first = _proposal_state(e2e_env, pks[0])
        assert (status, first) == (expected_status, "")


class TestRetentionBulkFlow:
    """Bulk-Actions: Mehrere Löschvorschläge in einem Rutsch bearbeiten."""

    def test_bulk_approve_counts_first_releases_only(self, authenticated_page, base_url, e2e_env, approver_pool):
        """Bulk approval of two fresh proposals: two first approvals, no final one."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        _open_dashboard(page, base_url)

        # Gezielt die beiden EIGENEN Vorschläge auswählen (Refs #1787) — ``nth(0/1)``
        # über alle Checkboxen hätte fremde Bestands-Vorschläge erwischt.
        for pk in pks[:2]:
            page.locator(f"#proposal-{pk} [data-bulk-proposal]").check()

        counter = page.locator("[data-testid='retention-bulk-count']")
        counter.wait_for(state="visible", timeout=10000)
        assert "2" in counter.inner_text()

        page.locator("[data-testid='retention-bulk-approve']").click()

        # HX-Redirect triggers a full page reload; the message separates first from final approvals.
        expect(page.get_by_text("2 erste Freigaben erteilt (1 von 2 Freigaben).")).to_be_visible(timeout=10000)
        expect(page.get_by_text("endgültig freigegeben", exact=False)).to_have_count(0)
        for pk in pks[:2]:
            card = page.locator(f"#proposal-{pk}")
            expect(card.get_by_text("1 von 2 Freigaben")).to_be_visible()
            expect(_badge(card, "Ausstehend")).to_have_count(1)
            assert _proposal_state(e2e_env, pk) == ("pending", _APPROVER_A)

    def test_bulk_mixed_selection_and_same_person_skipped(
        self, authenticated_page, lead_page, base_url, e2e_env, approver_pool
    ):
        """Mixed selection: first vote for the fresh proposal, final one for the pre-approved proposal."""
        pks = _ensure_proposals(e2e_env)
        fresh, half = pks[0], pks[1]

        # The lead approves the second proposal first (individually).
        _open_dashboard(lead_page, base_url)
        half_card = lead_page.locator(f"#proposal-{half}")
        half_card.get_by_role("button", name="Erste Freigabe erteilen").click()
        expect(half_card.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)

        # Admin selects both.
        page = authenticated_page
        _open_dashboard(page, base_url)
        for pk in (fresh, half):
            page.locator(f"#proposal-{pk} [data-bulk-proposal]").check()
        page.locator("[data-testid='retention-bulk-approve']").click()
        expect(page.get_by_text("1 erste Freigabe erteilt (1 von 2 Freigaben).")).to_be_visible(timeout=10000)
        expect(page.get_by_text("1 Löschvorschlag endgültig freigegeben.")).to_be_visible()
        assert _proposal_state(e2e_env, fresh) == ("pending", _APPROVER_A)
        assert _proposal_state(e2e_env, half)[0] == "approved"
        expect(_badge(page.locator(f"#proposal-{half}"), "Freigegeben")).to_have_count(1)

        # The same person on the half-approved proposal again: skipped, status unchanged.
        page.locator(f"#proposal-{fresh} [data-bulk-proposal]").check()
        page.locator("[data-testid='retention-bulk-approve']").click()
        expect(page.get_by_text("1 Löschvorschlag übersprungen", exact=False)).to_be_visible(timeout=10000)
        assert _proposal_state(e2e_env, fresh) == ("pending", _APPROVER_A)

    def test_select_all_toggles_every_checkbox(self, authenticated_page, base_url, e2e_env):
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        total = page.locator("[data-bulk-proposal]").count()
        assert total >= 1

        page.locator("[data-testid='retention-select-all']").check()
        counter = page.locator("[data-testid='retention-bulk-count']")
        counter.wait_for(state="visible", timeout=10000)
        assert str(total) in counter.inner_text()

        page.locator("[data-testid='retention-select-all']").uncheck()
        # Nach dem Abwählen muss die Toolbar wieder verschwinden
        page.locator("[data-testid='retention-bulk-count']").wait_for(state="hidden", timeout=10000)


class TestRetentionHoldFlow:
    """Legal Hold setzen und aufheben via HTMX."""

    @staticmethod
    def _set_hold(card, reason):
        """Hold über die eigene Karte setzen und auf den HTMX-Swap warten."""
        card.locator("button:has-text('Hold setzen')").click()
        reason_field = card.locator("textarea[name='reason']")
        reason_field.wait_for(state="visible", timeout=5000)
        reason_field.fill(reason)
        card.locator("button:has-text('Hold erstellen')").click()
        card.locator("button:has-text('Hold aufheben')").wait_for(state="visible", timeout=5000)

    def test_set_hold_on_proposal(self, authenticated_page, base_url, e2e_env):
        """Hold setzen — Badge wechselt zu 'Legal Hold' (Refs #1478 COD-03)."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Nur die EIGENE Karte anfassen (Refs #1787).
        card = page.locator(f"#proposal-{pks[0]}")
        card.wait_for(state="visible", timeout=5000)
        self._set_hold(card, "E2E-Test: Gerichtsverfahren")

        # The card should now show "Legal Hold" (get_status_display, seit
        # #1478/COD-03 statt des irrefuehrenden "Aufgeschoben") — warten bis
        # Badge UND der eingegebene Reason-Text gerendert sind (nach HTMX-Swap).
        card.locator("span:has-text('Legal Hold')").wait_for(state="visible", timeout=5000)
        card.locator("text=E2E-Test: Gerichtsverfahren").wait_for(state="visible", timeout=5000)
        assert card.locator("span:has-text('Legal Hold')").count() >= 1

    def test_dismiss_hold(self, authenticated_page, base_url, e2e_env):
        """Hold aufheben — Badge zurück zu 'Ausstehend'."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Refs #1787: Hold IMMER erst auf der eigenen Karte anlegen und dann
        # genau diesen wieder aufheben. Die frühere ``.first``-Variante hob den
        # erstbesten vorhandenen Hold auf — im Voll-Lauf also einen Seed-Hold
        # auf einem fremden Ereignis, das damit für ``enforce_retention``
        # freigegeben wurde (Spoliations-Schutz weg, Löschkaskade an).
        card = page.locator(f"#proposal-{pks[0]}")
        card.wait_for(state="visible", timeout=5000)
        self._set_hold(card, "Temporärer Hold")

        page.on("dialog", lambda dialog: dialog.accept())
        card.locator("button:has-text('Hold aufheben')").click()

        # After dismissal the card reverts: wait for the approve button to reappear
        # (signals the return to pending; since the two-person rule it is labelled
        # "Erste Freigabe erteilen").
        page.wait_for_load_state("domcontentloaded")
        card_btn = card.get_by_role("button", name="Erste Freigabe erteilen")
        card_btn.wait_for(state="visible", timeout=5000)
        assert card_btn.count() >= 1


class TestRetentionNavigation:
    """Navigation zum Dashboard.

    Refs #1813 (Konzept #1801 §4): „Löschfristen" hat keinen eigenen
    Sidebar-Eintrag mehr — der Einstieg laeuft ueber den Bereichs-Hub
    „Datenschutz" und dessen Reiterzeile. Die Aussage bleibt: die Leitung
    findet die Seite ueber die Navigation und landet per Klick dort.
    """

    def test_nav_link_visible_for_lead(self, lead_page, base_url):
        """Der Löschfristen-Reiter ist für Lead über den Hub sichtbar."""
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        assert sidebar_item(page, "datenschutz").count() >= 1

        page.goto(f"{base_url}/datenschutz/")
        page.wait_for_load_state("domcontentloaded")
        assert subnav_tab(page, "retention").count() >= 1

    def test_nav_link_navigates_to_dashboard(self, lead_page, base_url):
        """Klick über Hub → Reiter 'Löschfristen' navigiert zum Dashboard."""
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        sidebar_item(page, "datenschutz").click()
        page.wait_for_url(re.compile(r"/datenschutz/"))
        subnav_tab(page, "retention").click()
        page.wait_for_url(re.compile(r"/retention/"))
        assert page.locator("h1:has-text('Löschfristen')").is_visible()

"""E2E: Application-language dates and chart texts (#2603, #2536, #2548).

Date fields show the format of the *application* language (DE ``TT.MM.JJJJ``, EN
``YYYY-MM-DD``) regardless of the browser locale; the ISO transport value stays the
same. Chart titles, axes and legends follow the application language.
"""

from __future__ import annotations

import re
import uuid

import pytest

from ._date_helpers import expect_date_value, fill_date
from ._helpers import django_shell

pytestmark = pytest.mark.e2e

GERMAN_CHART_WORDS = {"Gesamt", "Anonym", "Nur Pseudonym", "Mit Zusatzangaben", "Kontakte", "Anzahl", "Zeitraum"}


def _set_language(e2e_env, username: str, code: str) -> str:
    script = (
        "from core.models import User; "
        f"u = User.objects.get(username='{username}'); print('prev=' + u.preferred_language); "
        f"User.objects.filter(pk=u.pk).update(preferred_language='{code}')"
    )
    return next(line for line in django_shell(e2e_env, script) if line.startswith("prev=")).removeprefix("prev=")


@pytest.fixture
def staff_language(e2e_env):
    """Switch the staff account to a language for one test and restore it afterwards."""
    previous = _set_language(e2e_env, "miriam", "de")

    def switch(code):
        _set_language(e2e_env, "miriam", code)

    yield switch
    _set_language(e2e_env, "miriam", previous)


@pytest.fixture
def admin_language(e2e_env):
    previous = _set_language(e2e_env, "admin", "de")

    def switch(code):
        _set_language(e2e_env, "admin", code)

    yield switch
    _set_language(e2e_env, "admin", previous)


class TestDateFieldsInAppLanguage:
    @pytest.mark.parametrize(
        ("app_language", "browser_locale"),
        [("de", "de-DE"), ("de", "en-US"), ("en", "en-US"), ("en", "de-DE")],
    )
    def test_statistics_and_events_filters_keep_format_and_values_after_back(
        self, browser, base_url, _lead_storage_state, e2e_env, app_language, browser_locale
    ):
        _set_language(e2e_env, "emma", app_language)  # lead account of the seed
        context = browser.new_context(storage_state=_lead_storage_state, locale=browser_locale)
        try:
            page = context.new_page()
            page.set_default_timeout(20000)
            statistics = f"{base_url}/statistics/?zeitraum=frei&date_from=2026-03-05&date_to=2026-03-20"
            page.goto(statistics, wait_until="domcontentloaded")
            expect_date_value(page, "#statistics-date-from", "2026-03-05")
            expect_date_value(page, "#statistics-date-to", "2026-03-20")

            # Filter: change the end of the period and apply (HTMX swap, history entry).
            fill_date(page, "#statistics-date-to", "2026-04-12")
            with page.expect_response(lambda r: "statistics" in r.url and r.request.method == "GET"):
                page.get_by_role("button", name=re.compile("Anwenden|Apply")).first.click()
            expect_date_value(page, "#statistics-date-to", "2026-04-12")

            # Leave and come back: format and values stay.
            page.goto(f"{base_url}/events/?date_from=2026-03-05&date_to=2026-03-20", wait_until="domcontentloaded")
            expect_date_value(page, "#event-date_from", "2026-03-05")
            expect_date_value(page, "#event-date_to", "2026-03-20")
            page.go_back(wait_until="domcontentloaded")
            expect_date_value(page, "#statistics-date-from", "2026-03-05")
            expect_date_value(page, "#statistics-date-to", "2026-04-12")
            shown = page.locator("#statistics-date-to").evaluate(
                "e => (e.dataset.appDateVisible === 'true' ? e : e.previousElementSibling).value"
            )
            expected = "12.04.2026" if app_language == "de" else "2026-04-12"
            assert shown == expected, f"app={app_language} browser={browser_locale}: {shown!r}"
        finally:
            context.close()
            _set_language(e2e_env, "emma", "de")

    @pytest.mark.parametrize("app_language", ["de", "en"])
    def test_audit_filter_dates_follow_app_language(self, authenticated_page, base_url, admin_language, app_language):
        admin_language(app_language)
        page = authenticated_page
        page.goto(f"{base_url}/audit/?date_from=2026-03-05&date_to=2026-03-20", wait_until="domcontentloaded")
        expect_date_value(page, "#filter-date-from", "2026-03-05")
        expect_date_value(page, "#filter-date-to", "2026-03-20")


class TestEpisodeEditEnglish:
    def test_edit_form_of_active_and_closed_episode_in_english(self, browser, base_url, _staff_storage_state, e2e_env):
        marker = uuid.uuid4().hex[:6]
        script = (
            "from core.models import Case, Client, Episode, Facility, User; "
            "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
            f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='I18N-{marker}', "
            "created_by=u); "
            f"ca = Case.objects.create(facility=f, client=c, title='I18N-Fall-{marker}', created_by=u, lead_user=u); "
            f"a = Episode.objects.create(case=ca, title='Aktiv-{marker}', started_at='2026-02-03', created_by=u); "
            f"z = Episode.objects.create(case=ca, title='Zu-{marker}', started_at='2026-01-10', "
            "ended_at='2026-02-01', created_by=u); "
            "print('case=' + str(ca.pk)); print('a=' + str(a.pk)); print('z=' + str(z.pk))"
        )
        values = {}
        for line in django_shell(e2e_env, script):
            key, _, value = line.partition("=")
            values[key] = value
        previous = _set_language(e2e_env, "miriam", "en")
        context = browser.new_context(storage_state=_staff_storage_state, locale="en-US")
        try:
            page = context.new_page()
            page.set_default_timeout(20000)
            for pk, expect_end in ((values["a"], ""), (values["z"], "2026-02-01")):
                page.goto(f"{base_url}/cases/{values['case']}/episodes/{pk}/edit/", wait_until="domcontentloaded")
                expect_date_value(page, "input[name='started_at']", "2026-02-03" if pk == values["a"] else "2026-01-10")
                expect_date_value(page, "input[name='ended_at']", expect_end)
                labels = page.locator("#main-content label").all_inner_texts()
                german = {"Beginn", "Ende", "Titel", "Beschreibung"}
                assert not german & {text.strip(" *") for text in labels}, f"German labels in EN form: {labels}"
                page.fill("input[name='title']", f"Title only {pk[:4]}")
                page.locator("#main-content button[type='submit']").last.click()
                page.wait_for_url(re.compile(r"/cases/[0-9a-f-]{36}/$"), timeout=10000)
            stored = django_shell(
                e2e_env,
                "from core.models import Episode; "
                f"print(';'.join(str(e.started_at) + '/' + str(e.ended_at) "
                f"for e in Episode.objects.filter(pk__in=['{values['a']}', '{values['z']}']).order_by('started_at')))",
            )[-1]
            assert stored == "2026-01-10/2026-02-01;2026-02-03/None", stored
        finally:
            context.close()
            _set_language(e2e_env, "miriam", previous)


class TestChartsInAppLanguage:
    def _chart_texts(self, page):
        page.goto(f"{page.url.split('/statistics')[0]}/statistics/?zeitraum=jahr", wait_until="domcontentloaded")
        page.locator("#chart-contacts").wait_for(state="visible", timeout=15000)
        page.wait_for_function("() => window.Chart && window.Chart.getChart('chart-contacts')", timeout=15000)
        return page.evaluate(
            """() => ['chart-contacts', 'chart-doc-types', 'chart-age-clusters'].map((id) => {
                const c = window.Chart.getChart(id);
                const scales = Object.values((c.options && c.options.scales) || {});
                return {
                    id,
                    datasets: c.data.datasets.map((d) => d.label),
                    axisTitles: scales.map((s) => s.title && s.title.display ? s.title.text : null).filter(Boolean),
                    title: c.options.plugins && c.options.plugins.title && c.options.plugins.title.display
                        ? c.options.plugins.title.text : null,
                };
            })"""
        )

    def test_charts_english(self, browser, base_url, _login_storage_state, e2e_env):
        previous = _set_language(e2e_env, "admin", "en")
        context = browser.new_context(storage_state=_login_storage_state, locale="en-US")
        try:
            page = context.new_page()
            page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")
            charts = self._chart_texts(page)
            labels = [text for chart in charts for text in chart["datasets"] + chart["axisTitles"] if text]
            assert labels, charts
            assert not GERMAN_CHART_WORDS & set(labels), f"German chart texts in EN: {charts}"
            headings = page.locator("h2").all_inner_texts()
            assert "Kontakte im Zeitverlauf" not in headings, headings
        finally:
            context.close()
            _set_language(e2e_env, "admin", previous)

    def test_charts_german_counter_check(self, authenticated_page, base_url, admin_language):
        admin_language("de")
        page = authenticated_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")
        charts = self._chart_texts(page)
        labels = {text for chart in charts for text in chart["datasets"] + chart["axisTitles"] if text}
        assert labels & GERMAN_CHART_WORDS, f"no German chart text in DE: {charts}"

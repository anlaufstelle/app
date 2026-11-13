"""Statistics charts tolerate either arrival order of data and Chart.js."""

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("arrival_order", ["data-first", "library-first"])
def test_statistics_charts_render_once(base_url, browser, _lead_storage_state, arrival_order):
    """Hold the library or data response and check the real chart instances."""
    context = browser.new_context(storage_state=_lead_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    held_library = []
    held_data = []
    data_requests = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on(
        "request",
        lambda request: data_requests.append(request.url) if "/statistics/chart-data/" in request.url else None,
    )
    page.route("**/static/js/chart.min.js*", lambda route: held_library.append(route))
    if arrival_order == "library-first":
        page.route("**/statistics/chart-data/**", lambda route: held_data.append(route))
    try:
        if arrival_order == "data-first":
            with page.expect_response(lambda response: "/statistics/chart-data/" in response.url) as response:
                page.goto(f"{base_url}/statistics/?zeitraum=jahr", wait_until="commit")
            assert response.value.status == 200
            response.value.finished()
            # Allow the response body to reach the component while Chart.js is held.
            page.wait_for_function(
                "() => Alpine.$data(document.getElementById('statistics-charts')).sources.length > 0"
            )
        else:
            with page.expect_request("**/statistics/chart-data/**"):
                page.goto(f"{base_url}/statistics/?zeitraum=jahr", wait_until="commit")
        assert held_library, "The library request must be held to control arrival order."
        held_library[0].continue_()
        page.wait_for_function("() => typeof window.Chart === 'function'")
        if arrival_order == "library-first":
            assert held_data, "The data response must remain pending until Chart.js is available."
            held_data[0].continue_()
        assert errors == []
        expect(page.locator("body")).to_have_attribute("data-charts-ready", "true")
        assert page.evaluate("Object.values(Chart.instances).map(chart => chart.id).sort()") == [0, 1, 2]
        expect(page.locator('#statistics-charts [x-text="errorText"]')).to_have_count(0)
        expect(page.locator('#statistics-charts [x-show="loading"]')).to_be_hidden()
        assert len(data_requests) == 1
        assert errors == []
    finally:
        context.close()


def test_statistics_charts_report_library_failure(base_url, browser, _lead_storage_state):
    """A failed library ends loading with the existing localized error."""
    context = browser.new_context(storage_state=_lead_storage_state, locale="de-DE", service_workers="block")
    page = context.new_page()
    held_library = []
    data_requests = []
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "request",
        lambda request: data_requests.append(request.url) if "/statistics/chart-data/" in request.url else None,
    )
    page.route("**/static/js/chart.min.js*", lambda route: held_library.append(route))
    try:
        with page.expect_response(lambda response: "/statistics/chart-data/" in response.url) as response:
            page.goto(f"{base_url}/statistics/?zeitraum=jahr", wait_until="commit")
        assert response.value.status == 200
        response.value.finished()
        page.wait_for_function("() => Alpine.$data(document.getElementById('statistics-charts')).sources.length > 0")
        assert held_library
        held_library[0].abort()
        expect(page.locator('#statistics-charts [x-text="errorText"]')).to_contain_text(
            "Die Diagramme konnten nicht geladen werden."
        )
        expect(page.locator('#statistics-charts [x-show="loading"]')).to_be_hidden()
        expect(page.locator("body")).not_to_have_attribute("data-charts-ready", "true")
        assert len(data_requests) == 1
        assert page_errors == []
    finally:
        context.close()

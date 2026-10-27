"""E2E acceptance: the English interface after the catalog update, per role (Refs #2609, #2665-#2667, #2669, #2011).

Staff, lead and facility admin each open the surfaces changed in 0.99.9 with the account
language set to English: person, case, episode and task forms with their document types,
the configuration of document types and field templates (admin), the free-text review,
the 2FA settings, statistics (lead, admin), the offline fallback page and the help pages.

Per page:

* no visible text or attribute equals a German message id whose English translation differs
  (an untranslated catalog entry or a string rendered outside gettext); names and slugs of
  document types and field templates are configuration data and exempt,
* no template placeholder (``%(name)s``, ``{{``, ``{%``) is visible,
* no serious or critical axe violation at 1280 px,
* at 320 px (a 400 % zoom equivalent) the page does not scroll sideways and no visible
  label, button, navigation link or table header is clipped (user data may be truncated),
* the browser console stays free of errors (CSP included).

Records are read-only except one throw-away person and case per role, prefixed ``EN-``.
"""

from __future__ import annotations

import gettext
import json
import re
import uuid
from pathlib import Path

import pytest

from ._axe import run_axe, serious_or_critical
from ._helpers import django_shell, track_console_errors

pytestmark = pytest.mark.e2e

CATALOG = Path(__file__).resolve().parents[2] / "locale" / "en" / "LC_MESSAGES" / "django.mo"
PLACEHOLDER_LEAK = re.compile(r"%\([a-z_]+\)[sd]|\{\{|\}\}|\{%|%\}")

COLLECT = """() => {
  const strings = [];
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const el = node.parentElement;
    if (!el || skip.has(el.tagName) || !shown(el)) continue;
    const text = node.textContent.replace(/\\s+/g, ' ').trim();
    if (text) strings.push(text);
  }
  for (const el of document.querySelectorAll('[aria-label],[placeholder],[title],[alt]')) {
    for (const name of ['aria-label', 'placeholder', 'title', 'alt']) {
      const value = (el.getAttribute(name) || '').replace(/\\s+/g, ' ').trim();
      if (value) strings.push(value);
    }
  }
  const clipped = [];
  for (const el of document.querySelectorAll('label, button, nav a, legend, th, summary')) {
    if (!shown(el) || el.closest('.sr-only, .skip-link')) continue;
    const style = getComputedStyle(el);
    const cuts = style.textOverflow === 'ellipsis' || style.overflow === 'hidden';
    if (cuts && el.scrollWidth > el.clientWidth + 1 && el.textContent.trim()) {
      clipped.push(el.textContent.replace(/\\s+/g, ' ').trim().slice(0, 80));
    }
  }
  return {
    strings,
    clipped,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    visibleText: document.body.innerText,
  };
}"""


def _translated_message_ids() -> set[str]:
    """German message ids that must never reach an English page unchanged."""
    with CATALOG.open("rb") as handle:
        catalog = gettext.GNUTranslations(handle)._catalog
    return {
        msgid
        for msgid, msgstr in catalog.items()
        if isinstance(msgid, str) and len(msgid) >= 4 and msgstr != msgid and re.search(r"[A-Za-zÄÖÜäöü]{3}", msgid)
    }


def _configuration_names(e2e_env) -> set[str]:
    """Names of document types and names/slugs of field templates (configuration data, German in the seed)."""
    lines = django_shell(
        e2e_env,
        "import json; from core.models import DocumentType, FieldTemplate; "
        "print('JSON|' + json.dumps(sorted(set(DocumentType.objects.values_list('name', flat=True)) | "
        "{v for row in FieldTemplate.objects.values_list('name', 'slug') for v in row})))",
    )
    return set(json.loads([line for line in lines if line.startswith("JSON|")][-1][5:]))


def _set_language(e2e_env, username: str, code: str) -> str:
    script = (
        "from core.models import User; "
        f"u = User.objects.get(username='{username}'); print('prev=' + u.preferred_language); "
        f"User.objects.filter(pk=u.pk).update(preferred_language='{code}')"
    )
    return next(line for line in django_shell(e2e_env, script) if line.startswith("prev=")).removeprefix("prev=")


def _throwaway_records(e2e_env) -> dict:
    suffix = uuid.uuid4().hex[:6]
    lines = django_shell(
        e2e_env,
        "from core.models import Case, Client, Facility, User; "
        "f = Facility.objects.get(name='Hauptstelle'); u = User.objects.get(username='miriam'); "
        f"c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='EN-P-{suffix}', created_by=u); "
        f"ca = Case.objects.create(facility=f, client=c, title='EN-Case-{suffix}', created_by=u, lead_user=u); "
        "print('IDS|%s|%s' % (c.pk, ca.pk))",
    )
    client_pk, case_pk = [line for line in lines if line.startswith("IDS|")][-1].split("|")[1:]
    return {"client": client_pk, "case": case_pk}


def _paths(who: str, ids: dict) -> list[str]:
    paths = [
        "/",
        "/clients/",
        "/clients/new/",
        f"/clients/{ids['client']}/",
        f"/clients/{ids['client']}/edit/",
        "/cases/",
        "/cases/new/",
        f"/cases/{ids['case']}/",
        f"/cases/{ids['case']}/edit/",
        f"/cases/{ids['case']}/episodes/new/",
        "/workitems/",
        "/workitems/new/",
        "/free-text-review/",
        "/mfa/settings/",
        "/offline/",
        "/hilfe/",
        "/hilfe/personen/",
        "/hilfe/faelle/",
        "/hilfe/statistik/",
        "/hilfe/datenschutz/",
    ]
    if who in {"lead", "admin"}:
        paths.append("/statistics/")
    if who == "admin":
        paths += [
            "/config/document-types/",
            "/config/document-types/new/",
            "/config/field-templates/",
            "/config/field-templates/new/",
            "/dsgvo/",
        ]
    return paths


@pytest.mark.parametrize("who", ["staff", "lead", "admin"])
def test_english_surfaces_are_translated_complete_and_fit(
    who, browser, base_url, e2e_env, _staff_storage_state, _lead_storage_state, _login_storage_state
):
    username, state = {
        "staff": ("miriam", _staff_storage_state),
        "lead": ("emma", _lead_storage_state),
        "admin": ("admin", _login_storage_state),
    }[who]
    german_ids = _translated_message_ids() - _configuration_names(e2e_env)
    ids = _throwaway_records(e2e_env)
    previous = _set_language(e2e_env, username, "en")
    problems: list[str] = []
    try:
        for width, height in ((1280, 900), (320, 640)):
            with browser.new_context(
                storage_state=state, locale="en-US", viewport={"width": width, "height": height}
            ) as context:
                page = context.new_page()
                page.set_default_timeout(20000)
                errors = track_console_errors(page)
                for path in _paths(who, ids):
                    response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
                    page.wait_for_load_state("load")
                    where = f"[{who} {width}px {path.split('/', 3)[1:3]}]"
                    if response is None or response.status != 200:
                        problems.append(f"{where} status {response and response.status}")
                        continue
                    result = page.evaluate(COLLECT)
                    problems += [f"{where} German text: {s!r}" for s in sorted(set(result["strings"]) & german_ids)]
                    problems += [f"{where} placeholder: {m}" for m in PLACEHOLDER_LEAK.findall(result["visibleText"])]
                    if width == 320:
                        if result["overflow"] > 0:
                            problems.append(f"{where} scrolls sideways by {result['overflow']}px")
                        problems += [f"{where} clipped: {text!r}" for text in result["clipped"]]
                    else:
                        found = serious_or_critical(run_axe(page, base_url).violations)
                        problems += [f"{where} axe {v['id']} ({v['impact']})" for v in found]
                problems += [f"[{who} {width}px] console: {e}" for e in errors]
    finally:
        _set_language(e2e_env, username, previous)
    assert not problems, "\n".join(problems)

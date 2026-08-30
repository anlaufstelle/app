"""Oeffentliche Rechtstexte fuer Demo und selbst gehostete Instanzen."""

import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.http import Http404
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")
_LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _render_inline(text):
    """Escape text and render only links with explicitly allowed schemes."""
    pieces = []
    cursor = 0
    for match in _LINK_RE.finditer(text):
        pieces.append(format_html("{}", text[cursor : match.start()]))
        label, url = match.groups()
        if urlsplit(url).scheme.lower() in _ALLOWED_LINK_SCHEMES:
            pieces.append(
                format_html(
                    '<a href="{}" class="text-accent underline break-words">{}</a>',
                    url,
                    label,
                )
            )
        else:
            pieces.append(format_html("{}", match.group(0)))
        cursor = match.end()
    pieces.append(format_html("{}", text[cursor:]))
    return format_html_join("", "{}", ((piece,) for piece in pieces))


def render_legal_markdown(source):
    """Render a deliberately small, escaping Markdown subset.

    Supported are headings up to level three, paragraphs, blank lines,
    unordered lists and links using http, https or mailto. All source text is
    escaped before the small set of generated tags is assembled.
    """
    blocks = []
    paragraph_lines = []
    list_items = []

    def flush_paragraph():
        if paragraph_lines:
            blocks.append(
                format_html(
                    '<p class="text-sm leading-6 text-ink">{}</p>',
                    _render_inline(" ".join(paragraph_lines)),
                )
            )
            paragraph_lines.clear()

    def flush_list():
        if list_items:
            items = format_html_join(
                "",
                '<li class="ml-5 list-disc">{}</li>',
                ((_render_inline(item),) for item in list_items),
            )
            blocks.append(format_html('<ul class="space-y-1 text-sm leading-6 text-ink">{}</ul>', items))
            list_items.clear()

    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            flush_list()
            level = len(heading_match.group(1))
            heading_templates = {
                1: '<h1 class="text-2xl font-bold text-ink">{}</h1>',
                2: '<h2 class="text-xl font-semibold text-ink">{}</h2>',
                3: '<h3 class="text-lg font-semibold text-ink">{}</h3>',
            }
            blocks.append(format_html(heading_templates[level], _render_inline(heading_match.group(2))))
            continue

        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            flush_paragraph()
            list_items.append(list_match.group(1))
            continue

        flush_list()
        paragraph_lines.append(line)

    flush_paragraph()
    flush_list()
    return format_html_join("\n", "{}", ((block,) for block in blocks))


def read_configured_legal_file(setting_name, *, warn=False):
    """Return configured UTF-8 content or ``None`` when unavailable."""
    configured_path = getattr(settings, setting_name, None)
    if not configured_path:
        return None
    try:
        return Path(configured_path).read_text(encoding="utf-8")
    except (OSError, TypeError, UnicodeError):
        if warn:
            logger.warning("Configured legal file for %s could not be read", setting_name)
        return None


class ConfigurableLegalView(TemplateView):
    """Render the unchanged demo template or an operator-provided text file."""

    demo_template_name = None
    setting_name = None
    page_title = None
    operator_template_name = "core/legal/operator_text.html"

    def dispatch(self, request, *args, **kwargs):
        self.operator_content = None
        if not getattr(settings, "DEMO_MODE", False):
            self.operator_content = read_configured_legal_file(self.setting_name, warn=True)
            if self.operator_content is None:
                raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_template_names(self):
        if getattr(settings, "DEMO_MODE", False):
            return [self.demo_template_name]
        return [self.operator_template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.operator_content is not None:
            context.update(
                {
                    "legal_page_title": self.page_title,
                    "legal_content": render_legal_markdown(self.operator_content),
                }
            )
        return context


class DemoImprintView(ConfigurableLegalView):
    """GET /impressum/ — Demo- oder Betreiber-Impressum, oeffentlich."""

    demo_template_name = "core/legal/imprint.html"
    setting_name = "LEGAL_IMPRINT_FILE"
    page_title = _("Impressum")


class DemoPrivacyView(ConfigurableLegalView):
    """GET /datenschutzerklaerung/ — Demo- oder Betreiber-Datenschutztext."""

    demo_template_name = "core/legal/privacy.html"
    setting_name = "LEGAL_PRIVACY_FILE"
    page_title = _("Datenschutzerklärung")

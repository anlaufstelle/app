"""Angemeldete In-App-Hilfe mit fest verdrahteter Slug-Whitelist."""

from dataclasses import dataclass

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from core import nav


@dataclass(frozen=True)
class HelpPage:
    slug: str
    nav_key: str
    intro: str | Promise
    topics: tuple[str | Promise, ...]


HELP_PAGES = {
    page.slug: page
    for page in (
        HelpPage(
            slug="arbeitszentrale",
            nav_key="dashboard",
            intro=_("Die Arbeitszentrale bündelt die wichtigsten offenen Punkte für die Leitung."),
            topics=(
                _("Karten führen direkt zu Aufgaben, Löschanträgen und fälligen Prüfungen."),
                _("Die Zahlen sind Einstiege in die jeweilige Arbeitsliste, keine eigene Ablage."),
            ),
        ),
        HelpPage(
            slug="zeitstrom",
            nav_key="zeitstrom",
            intro=_("Der Zeitstrom zeigt dokumentierte Kontakte in zeitlicher Reihenfolge."),
            topics=(
                _("Filter grenzen Zeitraum, Dokumentationstyp und Darstellungsform ein."),
                _("Neue Kontakte werden über „Neu“ angelegt und erscheinen danach im Zeitstrom."),
            ),
        ),
        HelpPage(
            slug="aufgaben",
            nav_key="workitems",
            intro=_("Aufgaben halten nächste Schritte, Zuständigkeiten und Fristen fest."),
            topics=(
                _("Die Inbox trennt offene, laufende und erledigte Aufgaben."),
                _("Überfällige Aufgaben werden in der Navigation zusätzlich hervorgehoben."),
            ),
        ),
        HelpPage(
            slug="personen",
            nav_key="clients",
            intro=_("Personenakten verbinden Pseudonym, Kontakte, Fälle, Aufgaben und Dateien."),
            topics=(
                _("Die Kontaktstufe zeigt, ob nur ein Pseudonym oder zusätzliche Angaben gespeichert sind."),
                _("Ausgewählte Personen können verschlüsselt für die befristete Offline-Arbeit mitgenommen werden."),
            ),
        ),
        HelpPage(
            slug="faelle",
            nav_key="cases",
            intro=_("Fälle bündeln länger laufende Begleitungen und die zugehörigen Kontakte."),
            topics=(
                _("Ziele, Meilensteine und Episoden strukturieren den Verlauf eines Falls."),
                _("Ein Fall kann geschlossen und bei Bedarf wieder geöffnet werden."),
            ),
        ),
        HelpPage(
            slug="dateien",
            nav_key="attachments",
            intro=_("Die Dateiübersicht führt Anhänge aus Kontakten, Personenakten und Fällen zusammen."),
            topics=(
                _("Filter helfen, Dateien nach ihrem fachlichen Bezug zu finden."),
                _("Downloads und Löschungen bleiben an die Rechte des jeweiligen Bezugsobjekts gebunden."),
            ),
        ),
        HelpPage(
            slug="statistik",
            nav_key="statistics",
            intro=_("Die Statistik verdichtet dokumentierte Arbeit für Auswertung und Berichte."),
            topics=(
                _("Der gewählte Zeitraum gilt beim Wechsel zwischen den Statistik-Reitern weiter."),
                _("Freigaben halten fest, welche Berichtsstände tatsächlich herausgegeben wurden."),
            ),
        ),
        HelpPage(
            slug="datenschutz",
            nav_key="datenschutz",
            intro=_("Der Datenschutzbereich bündelt Betroffenenrechte, Löschfristen und Nachweise."),
            topics=(
                _("Lösch- und Berichtigungsanträge werden nachvollziehbar geprüft."),
                _("Löschvorschläge, Audit-Log und DSGVO-Paket unterstützen die regelmäßige Kontrolle."),
            ),
        ),
        HelpPage(
            slug="verwaltung",
            nav_key="verwaltung",
            intro=_("Die Verwaltung passt die Anwendung an die Abläufe der Einrichtung an."),
            topics=(
                _("Hier werden Zugänge, Einstellungen, Dokumentationstypen und Feldvorlagen gepflegt."),
                _("Bedingungen können Felder abhängig von bereits erfassten Angaben ein- oder ausblenden."),
            ),
        ),
        HelpPage(
            slug="system",
            nav_key="system",
            intro=_("Der Systembereich zeigt installationsweite Betriebs- und Compliance-Informationen."),
            topics=(
                _("Er ist ausschließlich für die Systemadministration bestimmt."),
                _("Einrichtungen, Wartungszustand und technische Nachweise werden hier übergreifend geprüft."),
            ),
        ),
    )
}


class HelpPageView(LoginRequiredMixin, TemplateView):
    """Render overview or one whitelisted help page without dynamic templates."""

    def get_template_names(self):
        if "slug" in self.kwargs:
            return ["core/help/page.html"]
        return ["core/help/overview.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs.get("slug")
        if slug is None:
            context["visible_help_pages"] = [
                {"page": page, "nav_item": nav.item(page.nav_key)}
                for page in HELP_PAGES.values()
                if nav.is_visible(nav.item(page.nav_key), self.request.user)
            ]
            return context

        page = HELP_PAGES.get(slug)
        if page is None:
            raise Http404
        nav_item = nav.item(page.nav_key)
        context.update(
            {
                "help_page": page,
                "nav_item": nav_item,
                "show_area_link": nav.is_visible(nav_item, self.request.user),
            }
        )
        return context

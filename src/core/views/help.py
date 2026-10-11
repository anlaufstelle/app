"""Angemeldete In-App-Hilfe mit fest verdrahteter Slug-Whitelist."""

from dataclasses import dataclass

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _
from django.views.generic import TemplateView

from core import nav


@dataclass(frozen=True)
class HelpSection:
    """A deep-linkable section of a help page; ``anchor`` becomes the element id."""

    anchor: str
    heading: str | Promise
    paragraphs: tuple[str | Promise, ...]
    items: tuple[str | Promise, ...] = ()


@dataclass(frozen=True)
class HelpPage:
    slug: str
    nav_key: str
    intro: str | Promise
    topics: tuple[str | Promise, ...]
    sections: tuple[HelpSection, ...] = ()


HELP_PAGES = {
    page.slug: page
    for page in (
        HelpPage(
            slug="arbeitszentrale",
            nav_key="dashboard",
            intro=_("Die Arbeitszentrale bündelt die wichtigsten offenen Punkte für die Leitung."),
            topics=(
                _(
                    "Die Karten führen zu Löschanträgen, Löschvorschlägen und Statistik; "
                    "die Zeile „Speicher“ zur Seite „Dateien“."
                ),
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
            sections=(
                HelpSection(
                    anchor="kontakte-liste",
                    heading=_("Kontakte-Liste"),
                    paragraphs=(
                        _(
                            "„Kontakte“ steht direkt unter „Zeitstrom“ in der Seitenleiste, mobil unter "
                            "„Mehr → Kontakte“. Der lokale Kontakte-Link im Zeitstrom übernimmt Tag, "
                            "Dokumentationstyp und Schicht."
                        ),
                        _(
                            "Die Liste filtert Kontakte über mehrere Tage, auch Erfassungen anderer Mitarbeitender. "
                            "Ab Assistenz gelten dieselben Sichtbarkeitsgrenzen wie im Zeitstrom; "
                            "Fallfilter und Fallanzeige gibt es erst ab Fachkraft. "
                            "Die Filter bleiben in der Adresse, beim Seitenwechsel und über Zurück/Vorwärts erhalten."
                        ),
                    ),
                    items=(
                        _(
                            "Die Suche umfasst nur Pseudonym und Ereignis-Suchtext ab zwei Zeichen, keine "
                            "verschlüsselten oder wirksam erhöht/hoch eingestuften Feldwerte, "
                            "Falltitel oder Dateiinhalte."
                        ),
                        _(
                            "Person und Fall werden ausgewählt; "
                            "es gibt keine Autocomplete-Suche und keine Suchvorschläge."
                        ),
                        _("Die Liste zeigt keine Feldwerte und bietet keinen Export und keine Massenaktionen."),
                        _("Listen- und Filteraufrufe erzeugen keinen Audit-Eintrag."),
                    ),
                ),
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
                _(
                    "„Übersicht“ ist der interne Blick: Kennzahlen, Diagramme und Tabellen, "
                    "ohne Kleinstfall-Unterdrückung, mit Pseudonymen bei den Top-Personen."
                ),
                _(
                    "„Datensatz-Berichte“ bietet Auswertungen wie „Allgemeine Kennzahlen“ und "
                    "„Summe von Zahlenfeldern“ mit Vorschau und CSV-, JSON-, XML- oder PDF-Download; "
                    "Kleinstfallzahlen werden unterdrückt, Personen-Pseudonyme fehlen."
                ),
                _(
                    "Die Ereignisliste (intern) in der Übersicht enthält Einzelereignisse mit "
                    "Pseudonymen; diese bleiben personenbezogen. Unterdrückte Berichtszahlen "
                    "allein garantieren ebenfalls keine Anonymität."
                ),
                _(
                    "„Freigaben“ listet markierte Datei-Herausgaben mit Berichtszeitraum und warnt "
                    "bei nicht identischen, überlappenden Zeiträumen desselben Berichts; "
                    "Vorschauen und Weitergaben außerhalb der Anwendung fehlen dort."
                ),
                _(
                    "Der gewählte Zeitraum gilt beim Wechsel zwischen den Statistik-Reitern weiter; "
                    "Freigaben zeigt Herausgaben unabhängig davon."
                ),
                _(
                    "Zahlen können wegen Unterdrückung, Grundgesamtheit und Datenquelle abweichen. "
                    "Beachten Sie die Angaben je Bericht und Format: Der Beispiel-Sachbericht "
                    "liest nur im PDF aus Monats-Aggregaten mit Live-Fallback, sonst live."
                ),
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
            sections=(
                HelpSection(
                    anchor="vier-augen-prinzip",
                    heading=_("Vier-Augen-Prinzip bei Löschungen"),
                    paragraphs=(
                        _(
                            "Beim Vier-Augen-Prinzip führt ein Löschwunsch zunächst zu einem Antrag. "
                            "Gelöscht wird erst, nachdem eine zweite berechtigte Person den Antrag "
                            "geprüft und genehmigt hat."
                        ),
                    ),
                    items=(
                        _("Wer den Antrag gestellt hat, kann ihn nicht selbst genehmigen."),
                        _("Die prüfende Person braucht das Recht „Löschbestätigung“."),
                        _(
                            "Auch die Herabstufung von „Mit Zusatzangaben“ auf „Nur Pseudonym“ braucht bei "
                            "vorhandenen Ereignissen Antrag und Freigabe."
                        ),
                        _("Wer den Antrag gestellt hat, kann ihn zurückziehen, solange er aussteht."),
                        _("Ein zurückgezogener oder abgelehnter Antrag löscht nichts; die Daten bleiben unverändert."),
                    ),
                ),
                HelpSection(
                    anchor="loeschfristen",
                    heading=_("Löschfristen und Löschvorschläge"),
                    paragraphs=(
                        _(
                            "Ereignisse haben eine Aufbewahrungsfrist. Sie ergibt sich aus der Kontaktstufe "
                            "und einer Höchstfrist (verkürzt nur) am Dokumentationstyp; der früheste Stichtag gewinnt."
                        ),
                        _(
                            "Der Standardlauf erzeugt für „Mit Zusatzangaben“ einen Löschvorschlag und löscht erst "
                            "nach Freigabe durch eine Person mit „Löschbestätigung“ (Ausnahme: die standardmäßig "
                            "ausgeschaltete Auto-Freigabe nach mehrfacher Zurückstellung). Anonym und „Nur "
                            "Pseudonym“ werden automatisch gelöscht; Legal Hold und Art. 18 stellen den Lauf "
                            "zurück. Den Vorschlag legt der Lauf an. Die Einrichtung benennt mindestens ein aktives "
                            "Konto der eigenen Einrichtung mit Rolle Leitung oder Anwendungsbetreuung und dem Recht "
                            "„Löschbestätigung“; ohne besetzte Freigabe wird bei ausgeschalteter Auto-Freigabe "
                            "nicht gelöscht. Das Recht wird rollenunabhängig vergeben, die Aufbewahrungsoberfläche "
                            "ist jedoch auf Leitung und Anwendungsbetreuung beschränkt. Die Compliance-Karte "
                            "„Genehmiger“ zählt aktive Löschantragsberechtigte unabhängig von ihrer Rolle; sie "
                            "belegt nicht, dass ein Konto den Aufbewahrungslauf bedienen kann."
                        ),
                    ),
                    items=(
                        _(
                            "Ohne Fall beginnt die Frist am Ereignisdatum, im offenen Fall bei der letzten fachlichen "
                            "Fallaktivität (ersatzweise Anlage), im geschlossenen Fall beim Abschluss."
                        ),
                        _(
                            "Zurückstellen schützt bis zum Datum; Ablehnen erlaubt eine neue Vorlage erst nach "
                            "der Wiedervorlagefrist."
                        ),
                        _(
                            "Legal Hold setzen und aufheben: direkt in Personen-, Fall- und Ereignisakte, mit "
                            "Grund und optionalem Ablaufdatum."
                        ),
                    ),
                ),
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
            sections=(
                HelpSection(
                    anchor="feldbedingungen",
                    heading=_("Feldbedingungen (bedingte Sichtbarkeit)"),
                    paragraphs=(
                        _(
                            "Feldbedingungen steuern, wann ein Feld im Formular sichtbar ist. Das Zielfeld erscheint "
                            "nur, wenn ein anderes Feld desselben Dokumentationstyps, das Quellfeld, "
                            "die Bedingung erfüllt."
                        ),
                        _(
                            "Beispiel: Das Feld „Notiz zur Weitervermittlung“ soll nur erscheinen, wenn im Feld "
                            "„Weitervermittlung“ der Wert „Ja“ ausgewählt wurde."
                        ),
                    ),
                    items=(
                        _("Bedingungsgruppe: Die Bedingungen eines Zielfelds sind in Gruppen geordnet."),
                        _(
                            "UND innerhalb einer Gruppe: Mehrere Bedingungen in derselben Gruppe müssen "
                            "gemeinsam erfüllt sein."
                        ),
                        _(
                            "ODER über Gruppen hinweg: Mehrere Gruppen sind alternative Wege. "
                            "Es reicht, wenn eine Gruppe erfüllt ist."
                        ),
                        _(
                            "Operatoren: ist gleich, ist ungleich, enthält, ist größer als, ist kleiner als, "
                            "ist leer und ist nicht leer. Welche zur Wahl stehen, hängt vom Feldtyp des Quellfelds ab; "
                            "„ist leer“ und „ist nicht leer“ brauchen keinen Vergleichswert."
                        ),
                        _(
                            "Ausgeblendete Felder erscheinen im Formular nicht. "
                            "Ist das Quellfeld selbst ausgeblendet, gilt es als leer."
                        ),
                        _(
                            "Pflichtfeld: Ein ausgeblendetes Pflichtfeld muss nicht ausgefüllt werden. "
                            "Sobald es sichtbar ist, gilt die Pflicht wieder."
                        ),
                        _(
                            "Ausprobieren: Öffnen Sie nach dem Speichern der Bedingung das Erfassungsformular "
                            "dieses Dokumentationstyps und ändern Sie den Wert im Quellfeld — "
                            "das Zielfeld muss erscheinen und wieder verschwinden."
                        ),
                    ),
                ),
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
        # Detailtexte bleiben bewusst fuer alle angemeldeten Rollen direkt
        # erreichbar. Deshalb duerfen sie keine rolleninternen Informationen
        # enthalten; nur der Sprung in einen Bereich folgt dessen Nav-Gate.
        nav_item = nav.item(page.nav_key)
        context.update(
            {
                "help_page": page,
                "nav_item": nav_item,
                "show_area_link": nav.is_visible(nav_item, self.request.user),
            }
        )
        return context

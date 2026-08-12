"""Geteilte Form-Mixins (Refs #1834)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.forms.widgets import INPUT_CSS


class TailwindFormMixin:
    """Trägt die Tailwind-Eingabeklasse einmal über ``self.fields``.

    Refs #1834: ``INPUT_CSS`` ist seit #733 zentral definiert, stand aber
    51-mal von Hand als ``attrs={"class": INPUT_CSS}`` in acht Modulen — jede
    neue Widget-Definition musste daran denken, und ``Meta.widgets`` existierte
    an vielen Feldern **nur** wegen dieser einen Zeile. Der passende
    ``setdefault``-Helfer gab es bis dahin nur im dynamischen Zweig
    (``core/forms/dynamic.py``); hier ist er für die statischen Formulare.

    ``setdefault`` statt Zuweisung: ein Feld, das bewusst eine andere Klasse
    trägt, behält sie. ``Meta.widgets``-Einträge bleiben nur noch dort, wo es
    echte Zusatz-Attribute gibt (``rows``, ``hx-*``, ``min``/``max``, …).

    **Nicht gestylt** werden Widgets ohne sichtbares Eingabefeld — Hidden-Felder
    (die Klasse landete auf einem unsichtbaren ``<input>``) und Checkboxen (die
    Vollbreiten-Klasse gehört nicht an ein Ankreuzfeld; das Muster spiegelt den
    Checkbox-Zweig in ``dynamic.py``). Alles darüber hinaus ist ein
    **dokumentierter Opt-out** je Formular über :attr:`tailwind_opt_out`.
    """

    if TYPE_CHECKING:
        # Kommt von der Form, neben der das Mixin steht — nur fuer mypy.
        fields: dict[str, forms.Field]

    #: Feldnamen, die die Eingabe-Klasse bewusst nicht bekommen. Jeder Eintrag
    #: braucht am Formular einen Kommentar mit der Begründung.
    tailwind_opt_out: tuple[str, ...] = ()

    _UNSTYLED_WIDGETS = (forms.HiddenInput, forms.CheckboxInput, forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_tailwind_classes()

    def apply_tailwind_classes(self):
        for name, field in self.fields.items():
            if name in self.tailwind_opt_out:
                continue
            if isinstance(field.widget, self._UNSTYLED_WIDGETS):
                continue
            field.widget.attrs.setdefault("class", INPUT_CSS)


class PinnedClientMixin:
    """Pin-Guard und Widerrufs-Gate für Formulare mit einem ``client``-Feld.

    Refs #1834: ``CaseForm``, ``WorkItemForm`` und ``EventMetaForm`` trugen
    dieselbe Personen-Auflösung je einmal. Die Driftkosten sind belegt — die
    Fix-Kette [#1771], [#1786], [#1791] musste jedes Mal an drei Kopien
    nachgezogen werden, und der #1791-Befund (das *Entfernen* der Person
    rutschte durch das ``if client_id``-Short-Circuit) entstand genau dort.

    Zwei Bausteine, die die Formulare in ihrem eigenen ``clean_client()``
    zusammensetzen — die Methode bleibt bewusst pro Formular, weil sich die
    Ränder unterscheiden (Pflichtfeld beim Fall, ``None`` bei Aufgabe und
    Ereignis, eigene „existiert nicht"-Meldung):

    * :meth:`apply_client_pin` — ist eine Person gepinnt, gewinnt IMMER der
      gespeicherte Wert; abweichende oder leere POST-Werte sind Manipulation.
    * :meth:`resolve_client` — Facility-Scope + Widerrufs-Gate
      (``resolve_writable_client``).

    ``EventMetaForm`` kennt kein Pinning (dort sitzt das Zuordnungs-Gate
    view-seitig) und nutzt nur den Resolver; ``pinned_client`` bleibt für sie
    auf dem Klassendefault ``None``, damit :meth:`apply_client_pin` auch dort
    ein gefahrloser No-op wäre.
    """

    if TYPE_CHECKING:
        # Kommen von der Form, neben der das Mixin steht — nur fuer mypy.
        data: Any
        facility: Any

    #: Von der View durchgereichte Bestands-Person (``None`` = freie Auswahl).
    pinned_client = None

    def get_client_facility(self):
        """Facility, gegen die die Person aufgelöst wird.

        Default ist ``self.facility``; ``EventMetaForm`` hält sie unter
        ``_facility`` und überschreibt diesen Hook. Bewusst ein Hook statt
        einer Vereinheitlichung der Attributnamen — der Unterschied ist real
        und ein Rename zöge Views und Tests mit, ohne etwas zu gewinnen.
        """
        return self.facility

    def apply_client_pin(self, client_id):
        """Gibt die effektive Personen-ID zurück; wehrt Pin-Manipulation ab.

        Refs #1786/#1791: Ein FEHLENDER ``client``-Key ist der legitime
        Normalfall — das Read-only-Formular rendert kein
        ``<input name="client">``. Ein VORHANDENER Key mit leerem oder
        abweichendem Wert ist ein manipulierter POST; Wechsel und Entfernen
        werden symmetrisch als Formularfehler abgewiesen, nicht still
        ignoriert. Die Unterscheidung braucht das rohe ``self.data``: nach
        ``UUIDField.to_python("")`` sind beide Fälle in ``cleaned_data``
        identisch ``None``.

        Bekannter Grenzfall (Review #1791): Wurde der Datensatz ZWISCHEN
        Render (personenlos → Hidden-Feld sendet ``client=""``) und Submit
        von jemand anderem zugeordnet, greift die Prüfung mit derselben
        Meldung — inhaltlich zutreffend (die Zuordnung ist jetzt fix), auch
        wenn der Auslöser Nebenläufigkeit statt Manipulation ist; offline
        endet so ein Replay als 422 statt im 409-Konfliktfluss. Fail-closed
        ist hier bewusst in Kauf genommen.
        """
        if self.pinned_client is None:
            return client_id
        raw = self.data.get("client")
        if raw is not None and str(raw) != str(self.pinned_client.pk):
            raise ValidationError(_("Die Personenzuordnung kann nach dem Anlegen nicht geändert werden."))
        # Der gepinnte Wert geht trotzdem durch ``resolve_client``: das
        # Pinning ersetzt das Widerrufs-Gate (#1771) nicht, es kommt dazu.
        # Sonst schriebe ein Edit an einer inzwischen anonymisierten Person
        # wieder frischen Klartext.
        return self.pinned_client.pk

    def resolve_client(self, client_id, *, missing_error):
        """Facility-Scope (Refs #819/#1423) + Widerrufs-Gate (Refs #1771).

        Der Scope liefert für eine fremde Facility direkt „existiert nicht"
        (Defense-in-Depth gegen ID-Erraten); das Gate hält Personen im
        Papierkorb (``is_deleted``) und anonymisierte (``is_active=False``)
        heraus, an denen ein Schreibzugriff frischen Klartext hinterließe —
        seit M24 (#1738) inklusive Datei-Anhängen.
        """
        # Lokaler Import: ``core.services.client`` zieht Modelle und weitere
        # Services nach, ein Modul-Top-Level-Import riskierte einen Zirkel
        # ueber ``core.forms``.
        from core.services.client import resolve_writable_client

        return resolve_writable_client(self.get_client_facility(), client_id, missing_error=missing_error)

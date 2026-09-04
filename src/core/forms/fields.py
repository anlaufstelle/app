"""Wiederverwendbare Formularfelder mit projekteigener Fehlersemantik.

Aktuell genau eines: :class:`DstAwareDateTimeField` (Refs #2117). Es liegt
bewusst in einem eigenen Modul und nicht in :mod:`core.forms.events` — das
Problem ist keines des Ereignis-Formulars, sondern jedes ``DateTimeField`` mit
Wanduhrzeit-Eingabe in einer Zeitzone mit Sommerzeit. Ein zweiter Verbraucher
soll die Klasse importieren, nicht nachbauen.
"""

import datetime as dt

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _


class DstAwareDateTimeField(forms.DateTimeField):
    """``DateTimeField``, das die beiden Zeitumstellungs-Grenzfaelle benennt.

    Django lehnt beide Faelle bereits ab (``from_current_timezone`` in
    ``django/forms/utils.py`` wirft eine ``ValidationError`` mit dem Code
    ``ambiguous_timezone``) — es wird also **nichts still verschoben**. Die
    Meldung ist aber fuer beide Faelle dieselbe, nennt die Zeitzone technisch
    und sagt nicht, was stattdessen einzutragen ist:

    * **Rueckfalltag** (Europe/Berlin: letzter Sonntag im Oktober, 03:00 →
      02:00): die Wanduhrzeit 02:00–02:59 gibt es **zweimal**. Welche der
      beiden Stunden gemeint ist, traegt die Eingabe nicht — das native
      ``<input type="datetime-local">`` kennt keinen ``fold``-Transport.
    * **Vorstelltag** (letzter Sonntag im Maerz, 02:00 → 03:00): die
      Wanduhrzeit 02:00–02:59 gibt es **nicht**.

    Die Unterscheidung passiert mit oeffentlichen ``zoneinfo``-Mitteln (kein
    Zugriff auf Djangos privates ``timezone._datetime_ambiguous_or_imaginary``)
    und ausschliesslich auf dem Fehlerpfad: der Gutfall laeuft unveraendert
    durch ``super().to_python``. Laesst sich der Fall nicht eindeutig
    klassifizieren (fremde Zeitzonen-Implementierung, nicht rekonstruierbarer
    Eingabewert), bleibt Djangos Originalfehler stehen — fail-honest statt
    einer moeglicherweise falschen Erklaerung.
    """

    default_error_messages = {
        "dst_ambiguous": _(
            "Diese Uhrzeit gibt es am Tag der Zeitumstellung zweimal (Sommer- und Winterzeit) und kann "
            "nicht eindeutig gespeichert werden. Bitte eine Uhrzeit vor 02:00 oder ab 03:00 Uhr eintragen "
            "und die genaue Zeit bei Bedarf im Text vermerken."
        ),
        "dst_nonexistent": _(
            "Diese Uhrzeit gibt es am Tag der Zeitumstellung nicht — die Uhr springt von 02:00 direkt auf "
            "03:00 Uhr. Bitte eine Uhrzeit ab 03:00 Uhr eintragen."
        ),
    }

    def to_python(self, value):
        try:
            return super().to_python(value)
        except ValidationError as exc:
            if getattr(exc, "code", None) != "ambiguous_timezone":
                raise
            code = self._dst_code(self._naive_value(value))
            if code is None:
                raise
            raise ValidationError(self.error_messages[code], code=code) from exc

    def _naive_value(self, value):
        """Den naiven Eingabewert erneut ermitteln — ohne ``from_current_timezone``.

        Djangos ``to_python`` verwirft den geparsten Wert, wenn die
        Zeitzonen-Umrechnung wirft; ``super()`` laesst sich nicht auf einem
        ``USE_TZ``-freien Pfad erneut aufrufen. Der Parse-Weg spiegelt deshalb
        ``DateTimeField.to_python``/``BaseTemporalField.to_python``:
        ``parse_datetime`` zuerst, danach die ``input_formats``.
        """
        if isinstance(value, dt.datetime):
            return value if timezone.is_naive(value) else None
        if isinstance(value, dt.date):
            return dt.datetime(value.year, value.month, value.day)
        if not isinstance(value, str):
            return None
        text = value.strip()
        try:
            parsed = parse_datetime(text)
        except ValueError:
            return None
        if parsed is None:
            for input_format in self.input_formats:
                try:
                    parsed = self.strptime(text, input_format)
                    break
                except (ValueError, TypeError):
                    continue
        if parsed is None or timezone.is_aware(parsed):
            return None
        return parsed

    @staticmethod
    def _dst_code(naive):
        """``"dst_nonexistent"`` / ``"dst_ambiguous"`` / ``None`` fuer die aktive Zeitzone.

        *Nicht existent* heisst: der Hin- und Rueckweg ueber UTC landet nicht
        wieder auf derselben Wanduhrzeit (``zoneinfo`` rechnet die Luecke mit
        dem Pre-Fold-Offset um). *Mehrdeutig* heisst: die beiden ``fold``-
        Lesarten derselben Wanduhrzeit haben unterschiedliche UTC-Offsets.
        Die Reihenfolge ist bewusst so: die Luecke ist der schaerfere Befund.
        """
        if naive is None:
            return None
        tz = timezone.get_current_timezone()
        try:
            roundtrip = naive.replace(tzinfo=tz).astimezone(dt.UTC).astimezone(tz).replace(tzinfo=None)
            offsets_differ = (
                naive.replace(tzinfo=tz, fold=0).utcoffset() != naive.replace(tzinfo=tz, fold=1).utcoffset()
            )
        except (TypeError, ValueError, OverflowError):  # pragma: no cover — fremde tzinfo-Implementierung
            return None
        if roundtrip != naive:
            return "dst_nonexistent"
        if offsets_differ:
            return "dst_ambiguous"
        return None

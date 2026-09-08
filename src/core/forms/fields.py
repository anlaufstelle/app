"""Wiederverwendbare Formularfelder mit projekteigener Fehlersemantik.

Aktuell genau eines: :class:`DstAwareDateTimeField` (Refs #2117) samt seinem
Zwischenwert :class:`AmbiguousWallClock` (Refs #2127). Es liegt bewusst in
einem eigenen Modul und nicht in :mod:`core.forms.events` — das Problem ist
keines des Ereignis-Formulars, sondern jedes ``DateTimeField`` mit
Wanduhrzeit-Eingabe in einer Zeitzone mit Sommerzeit. Ein zweiter Verbraucher
soll die Klasse importieren, nicht nachbauen.
"""

import dataclasses
import datetime as dt

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _


@dataclasses.dataclass(frozen=True)
class AmbiguousWallClock:
    """Eine Wanduhrzeit, die es am Rueckfalltag zweimal gibt (Refs #2127).

    Bewusst **kein** ``datetime``-Subtyp: ein Wert, der sich wie ein
    ``datetime`` anfuehlt, aber noch keinen Zeitpunkt bezeichnet, wuerde
    frueher oder spaeter still gespeichert. So ist jede Verwendung ohne
    vorherige Aufloesung ein lauter ``TypeError``.

    Der Zwischenwert entsteht nur, wenn ein Formular ihn ausdruecklich
    anfordert (:class:`DstAwareDateTimeField` mit ``resolve_ambiguous=True``)
    — es muss dann selbst entscheiden, welche der beiden Stunden gemeint ist,
    und :meth:`resolve` damit aufrufen.
    """

    naive: dt.datetime

    def resolve(self, fold: int) -> dt.datetime:
        """Die gewaehlte Lesart als aware ``datetime`` der aktiven Zeitzone.

        ``fold=0`` ist die erste (Sommerzeit-)Stunde, ``fold=1`` die zweite
        (Winterzeit-)Stunde; die beiden Ergebnisse unterscheiden sich in UTC
        um genau eine Stunde. ``make_aware`` haengt seit Django 5 nur die
        ``tzinfo`` an und laesst ``fold`` unangetastet — genau das wird hier
        gebraucht (Djangos eigener Eingabepfad lehnt mehrdeutige Zeiten davor
        ab, statt sie aufzuloesen).
        """
        return timezone.make_aware(self.naive.replace(fold=fold))


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

    **Opt-in ``resolve_ambiguous`` (Refs #2127).** Der Rueckfalltag ist der
    einzige der beiden Faelle, der sich ueberhaupt aufloesen laesst — aber nur
    mit einer zusaetzlichen Angabe, die das native
    ``<input type="datetime-local">`` nicht transportiert. Ein Formular, das
    diese Angabe erhebt (``EventMetaForm.occurred_at_fold``), schaltet mit
    ``resolve_ambiguous=True`` von "ablehnen" auf "zurueckfragen": ``to_python``
    liefert dann statt des Feldfehlers einen :class:`AmbiguousWallClock`, den
    das Formular in seinem ``clean()`` aufloest. Der Default bleibt bewusst
    ``False`` — ein Verbraucher ohne zweites Eingabefeld hat keine Moeglichkeit,
    die Rueckfrage zu stellen, und bekommt weiterhin die #2117-Meldung statt
    eines Zwischenwerts, mit dem er nicht rechnet. Der Vorstelltag bleibt in
    beiden Betriebsarten ein harter Fehler: eine Uhrzeit, die es nicht gibt,
    wird durch keine Auswahl real.
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

    def __init__(self, *args, resolve_ambiguous: bool = False, **kwargs):
        self.resolve_ambiguous = resolve_ambiguous
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        try:
            return super().to_python(value)
        except ValidationError as exc:
            if getattr(exc, "code", None) != "ambiguous_timezone":
                raise
            naive = self._naive_value(value)
            code = self._dst_code(naive)
            if code is None:
                raise
            if code == "dst_ambiguous" and self.resolve_ambiguous:
                return AmbiguousWallClock(naive)
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
            for input_format in self.input_formats or ():
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

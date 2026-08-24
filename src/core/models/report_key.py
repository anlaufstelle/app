"""Berichtsschluessel der Dokumentationstyp-Familie (E3, Refs #1849).

Eigenes Modul aus demselben Grund wie ``slug.py`` (Refs #1834):
``DocumentType`` und ``FieldTemplate`` teilen sich Feld, Wertebereichs-Sentinel
und Aenderbarkeits-Regel — bei einem der beiden Models zu wohnen waere
willkuerlich.

**Warum ein eigenes Feld** (Konzept #1847 §1):
``system_type`` macht zwei unvereinbare Jobs — App-Verhalten (``ban``,
``crisis``) und Berichtszuordnung. Weil beides im eingefrorenen Kern-Enum
steckt, erzwaenge jede neue Berichtskategorie eine Datenmodell-Migration. Der
``slug`` kann den Job nicht uebernehmen: er ist je Einrichtung **eindeutig**,
Berichtskategorien sind es nicht (drei Beratungstypen, eine Berichtszeile).

**Woher der Werteraum kommt.** Nicht von hier. ``report_key`` traegt bewusst
**kein** ``choices`` — der Wertebereich ist die Vereinigung der
Backend-Deklarationen (``core.reporting.registry.get_report_key_vocabulary``
bzw. ``get_field_key_vocabulary``) plus :data:`EXCLUDED`. Ein ``choices`` am
Modell machte ``full_clean()`` bei jedem Fremdwert hart — und damit genau die
Toleranz kaputt, die das Paketformat (Fremdpaket mit anderem Vokabular) und die
Konfigurations-UI (Bearbeitung eines Typs, dessen Backend entfernt wurde)
brauchen. Unbekannte Werte sind sichtbar, aber nicht blockierend: der
Vollstaendigkeitshinweis meldet sie (``unknown_report_keys``, Entscheid E-H).
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

# Reservierter Sentinel: „gehoert ausdruecklich in keinen Bericht" (Konzept
# #1847 §2). Ohne ihn kaeme der Dauerhinweis aus
# #1819 zurueck —
# ``ban``-Typen traegen keinen Berichtsschluessel und fielen wieder unter
# „nicht zugeordnet".
#
# Der Wert ist bewusst **hier** definiert und nicht aus
# ``core.reporting.base.EXCLUDED_KEY`` importiert: die Modellschicht darf nicht
# auf die Reporting-Schicht zeigen. Dass beide Konstanten denselben String
# tragen, pinnt ``test_report_key_model.py`` — eine Abweichung waere sonst erst
# an falschen Berichtszahlen sichtbar.
EXCLUDED = "excluded"


class ReportKeyMixin(models.Model):
    """Abstrakte Basis fuer Modelle mit einem ``report_key`` (Muster ``SluggedModel``).

    **Aenderbarkeits-Regel (Konzept #1847 §2.1, Entscheid E-K):** leer→gesetzt
    ist erlaubt, gesetzt→anders und gesetzt→leer sind verboten;
    :data:`EXCLUDED` zaehlt als gesetzt. Leitbild ist die E13-Ausnahme des
    Paket-Loaders und der ``system_type``-Guard — mit einem Unterschied, der
    hier der Zweck der Uebung ist: das Nachtragen bleibt **auch in der
    Konfigurations-UI** moeglich. Es ist der Reparaturpfad fuer die haeufigste
    Ueberraschung (stille Nicht-Zuordnung); die Berichtskontinuitaet sichert die
    zweite Haelfte der Regel.

    Der Guard sitzt in ``save()`` und **nicht** als DB-Trigger — Paritaet zu
    ``system_type``, dessen Trigger es ebenfalls nicht gibt (Migration ``0116``
    deckt nur Slugs ab).

    **Vertrag der Subklassen:** :meth:`_check_report_key_immutable` aus dem
    eigenen ``_check_immutable_fields``-Hook aufrufen (den
    :class:`~core.models.mixins.SluggedModel` beim Update aufruft). Bewusst
    explizit statt ueber ein ``super()``-Kettenglied im Mixin: die
    Guard-Reihenfolge steht damit sichtbar im Modell, und das Mixin muss keine
    Methode einer Klasse ueberschreiben, von der es gar nicht erbt.
    ``test_report_key_model.py`` prueft die Regel an BEIDEN Traegern — ein
    vergessener Aufruf faellt dort auf.
    """

    class Meta:
        abstract = True

    report_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Berichtsschlüssel"),
        help_text=_(
            "Ordnet diesen Eintrag einer Berichtszeile zu; derselbe Schlüssel kann in mehreren Berichten erscheinen. "
            "Leer = erscheint in keinem Bericht. "
            "Ein leerer Schlüssel kann nachgetragen werden; ein gesetzter ist danach unveränderbar."
        ),
    )

    def _check_report_key_immutable(self, orig) -> None:
        """Gesetzt→anders/weg ist ein Fehler; leer→gesetzt ist erlaubt.

        ``orig.report_key`` wird auf **Truthiness** geprueft, nicht auf ``is not
        None``: ``""`` ist wie NULL „keine Aussage getroffen" — ein
        Formular-POST mit leerer Auswahl darf keine dauerhafte Sperre erzeugen.
        """
        if orig.report_key and orig.report_key != self.report_key:
            raise ValidationError(_("Der Berichtsschlüssel kann nach dem Setzen nicht mehr geändert werden."))

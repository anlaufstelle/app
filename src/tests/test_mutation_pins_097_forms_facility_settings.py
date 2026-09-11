"""Pin-Tests der Welle J (0.97) — Checkpoint-Delta der Familie ``forms``.

Refs https://github.com/anlaufstelle/app/issues/2149,
Refs https://github.com/anlaufstelle/app/issues/2042.

Anlass (R-CP-1): Das Mutations-Delta BASE=v0.96.0 auf ``bb2673e1`` weist der
Familie ``forms`` 26/41 = 63,4 % aus und reisst damit die Delta-Policy
(§ 0.5, Refs https://github.com/anlaufstelle/app/issues/1953) von 85 %.
Einziges Delta-Modul der Familie ist ``core.forms.facility_settings`` — Lane E
hat dort das Feld „Aufbewahrung Uebergabenotizen (Tage)" in die Gruppe
„Datenschutz und Fristen" eingehaengt. Alle 41 Mutanten sitzen in
``FacilitySettingsForm.__init__``; die 15 Survivors betreffen ausschliesslich
die Aufbereitung des Feldes ``default_document_type``.

Gepinnt werden die 13 nicht-aequivalenten Survivors in fuenf Familien:

* **Der aktuelle Wert bleibt waehlbar** (``__init___mutmut_3``, ``_4``, ``_9``,
  ``_10``, ``_20``) — jede dieser Mutanten laesst ``current_id`` auf ``None``
  laufen (Zuweisung genullt, ``getattr(None, …)``, verfaelschter
  Attributname in beiden mutmut-Schreibweisen, bzw. ``Q(pk=None)`` direkt im
  Filter). ``Q(pk=None)`` uebersetzt Django zu ``pk IS NULL`` und trifft nie —
  ein bereits gesetzter, inzwischen deaktivierter Standardtyp faellt damit aus
  dem Queryset. Das Formular wuerde den gespeicherten Wert beim naechsten
  Speichern still verwerfen (``ModelChoiceField`` validiert gegen sein
  Queryset). Bestandstests arbeiten nur mit AKTIVEN Typen und sehen den
  Unterschied nicht.
* **Inaktive Fremdtypen bleiben aussen vor** (``_14``) — der Mutant streicht das
  positionale ``Q``-Argument komplett; ohne ``is_active``-Zweig stehen
  deaktivierte Dokumentationstypen wieder zur Auswahl.
* **Nur Ereignis-Typen** (``_15``) — der Mutant streicht
  ``entity_type=EVENT``; Personen- und Fall-Typen (M2, Refs
  https://github.com/anlaufstelle/app/issues/1058) landen dann in einem
  Feld, das ausschliesslich Ereignisse vorbelegt.
* **Sortierung ``sort_order`` vor ``name``** (``_31``, ``_32``) — der Mutant
  streicht je einen der beiden Schluessel. ``_31`` ist am Ergebnis beobachtbar
  (Betreiber-Sortierung wird zur Alphabetliste). ``_32`` verliert nur den
  Tie-Break; bei gleichem ``sort_order`` ist die Reihenfolge dann
  DB-willkuerlich und damit nicht stabil beobachtbar — deshalb prueft der Pin
  zusaetzlich die Ordnungsspezifikation des Querysets exakt
  (``query.order_by``). Beides steht im selben Test, damit die Zusicherung
  „Betreiber-Sortierung, dann Name" nicht in zwei Haelften zerfaellt.
* **Die Leer-Option traegt den Fachbegriff** (``_37``, ``_39``, ``_40``,
  ``_41``) — ``empty_label = None`` entfernt die Leer-Option bei
  ``ModelChoiceField`` vollstaendig (kein „kein Standard" mehr waehlbar, der
  Standard liesse sich nicht zuruecknehmen); ``_39``/``_40``/``_41`` tauschen
  den Text gegen die mutmut-Varianten ``"XX…XX"``, Klein- und Grossschreibung.
  Der Pin vergleicht deshalb EXAKT per ``==``, nie per Teilstring.

Aequivalente Mutanten (nachweislich verhaltensgleich, bewusst NICHT gepinnt):
``_8`` (``getattr(self.instance, "default_document_type_id")`` ohne Default —
``self.instance`` ist bei einer ``ModelForm`` immer eine ``Settings``-Instanz,
das FK-Attribut existiert also stets, der Default greift nie) und ``_22``
(``cast(None, …)`` — ``typing.cast`` gibt zur Laufzeit unveraendert sein
zweites Argument zurueck und prueft das erste nie).

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren die Pins dort aus dem FALSCHEN
Grund rot und der Kill-Nachweis wertlos.
"""

from __future__ import annotations

import pytest

from core.forms.facility_settings import FacilitySettingsForm
from core.models import DocumentType

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

#: Wortlaut der Leer-Option — exakt, damit die mutmut-Textvarianten
#: (``"XX…XX"``, Klein-/Grossschreibung) auffallen.
EMPTY_LABEL = "Kein Standard-Dokumentationstyp"


def _choice_queryset(settings_obj, facility):
    """Das Queryset, das ``default_document_type`` nach ``__init__`` traegt."""
    form = FacilitySettingsForm(instance=settings_obj, facility=facility)
    return form.fields["default_document_type"].queryset


class TestDefaultDocumentTypeAuswahl:
    """``FacilitySettingsForm.__init__`` — Queryset des Standard-Dokumentationstyps."""

    def test_gesetzter_aber_deaktivierter_standardtyp_bleibt_waehlbar(self, facility, settings_obj, doc_type_contact):
        """Pinnt ``_3``/``_4``/``_9``/``_10``/``_20`` (``current_id`` bzw. ``Q(pk=…)`` -> ``None``).

        Der gespeicherte Standardtyp ist deaktiviert: nur der ``Q(pk=current_id)``-
        Zweig haelt ihn im Queryset. Bei allen fuenf Mutanten ist das Queryset leer.
        """
        settings_obj.default_document_type = doc_type_contact
        settings_obj.save(update_fields=["default_document_type"])
        doc_type_contact.is_active = False
        doc_type_contact.save(update_fields=["is_active"])

        assert list(_choice_queryset(settings_obj, facility)) == [doc_type_contact]

    def test_deaktivierte_fremdtypen_stehen_nicht_zur_auswahl(self, facility, settings_obj, doc_type_contact):
        """Pinnt ``_14`` (positionales ``Q``-Argument des Filters gestrichen)."""
        DocumentType.objects.create(
            facility=facility,
            name="Altlast",
            entity_type=DocumentType.EntityType.EVENT,
            is_active=False,
        )

        assert list(_choice_queryset(settings_obj, facility)) == [doc_type_contact]

    def test_nur_ereignis_typen_stehen_zur_auswahl(
        self, facility, settings_obj, doc_type_contact, doc_type_client_profile
    ):
        """Pinnt ``_15`` (``entity_type=EVENT`` aus dem Filter gestrichen)."""
        assert doc_type_client_profile.entity_type == DocumentType.EntityType.CLIENT
        assert doc_type_client_profile.is_active is True

        assert list(_choice_queryset(settings_obj, facility)) == [doc_type_contact]

    def test_sortierung_sort_order_vor_name(self, facility, settings_obj):
        """Pinnt ``_31`` (``sort_order`` gestrichen) und ``_32`` (``name`` gestrichen).

        ``_31`` faellt am Ergebnis auf: die beiden Typen sind so angelegt, dass
        Betreiber-Sortierung und Alphabet gegenlaeufig sind. ``_32`` verliert nur
        den Tie-Break und ist am Ergebnis nicht stabil beobachtbar — dafuer die
        exakte Ordnungsspezifikation des Querysets.
        """
        zuerst = DocumentType.objects.create(
            facility=facility,
            name="Zugang",
            entity_type=DocumentType.EntityType.EVENT,
            sort_order=1,
        )
        danach = DocumentType.objects.create(
            facility=facility,
            name="Beratung",
            entity_type=DocumentType.EntityType.EVENT,
            sort_order=2,
        )

        queryset = _choice_queryset(settings_obj, facility)

        assert list(queryset) == [zuerst, danach]
        assert tuple(queryset.query.order_by) == ("sort_order", "name")

    def test_leer_option_traegt_den_fachbegriff(self, facility, settings_obj, doc_type_contact):
        """Pinnt ``_37`` (``empty_label = None``) und ``_39``/``_40``/``_41`` (Textvarianten).

        ``empty_label = None`` entfernt die Leer-Option bei ``ModelChoiceField``
        ganz — deshalb zusaetzlich die erste gerenderte Auswahl.
        """
        form = FacilitySettingsForm(instance=settings_obj, facility=facility)
        field = form.fields["default_document_type"]

        assert str(field.empty_label) == EMPTY_LABEL
        first_value, first_label = next(iter(field.choices))
        assert first_value == ""
        assert str(first_label) == EMPTY_LABEL

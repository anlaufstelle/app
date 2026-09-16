"""Mutation-Pins Welle L (0.99) — ``core.forms.import_data`` (Vollmutation #2174).

Batch ``forms-1`` (Lane M, Refs #2174, Refs #2193). 29 Survivors in
``ImportUploadForm.__init__`` (4) und ``ImportUploadForm.clean`` (25). Alle
29 sind echte Testluecken (``test``, 19× Prio 2 fachliches Verhalten, 10×
Prio 3 Darstellung/Text) — die fuenf Pins unten killen sie vollstaendig.
21 Mutanten sind strukturelle Bruecke (kaputte ``cleaned.get``-Quelle,
geflippte Bedingung, verstuemmelter Feldname/fehlendes Argument in
``add_error``, entfernter Filter-/Sortierschluessel); 8 weitere waeren fuer
sich genommen reine Fehlermeldungs-Textmutationen (vgl.
``docs/testing/mutation-survivors-baseline.md`` Zeile 969) — sie sterben
aber als Nebeneffekt derselben Pins, weil die beiden ``clean()``-Pins
``form.errors["document_type"]`` per EXAKTER Listengleichheit statt nur auf
Feldnamen pruefen (empirisch gegen den Mutantenbaum verifiziert, siehe
Bericht Abschnitt 3 fuer die Einzelnachweise ``_15``/``_21``/``_22``/``_23``/
``_29``/``_35``/``_36``/``_37``).

**__init__ (Facility-Scope des ``document_type``-Dropdowns):**

* ``_2`` — ``super().__init__(*args, )`` reicht ``**kwargs`` nicht mehr an
  ``forms.Form.__init__`` durch. Jedes zusaetzliche Kwarg (``initial=``,
  ``prefix=``, ``data=`` als Keyword statt positional) geht damit
  stillschweigend verloren; aktuelle Call-Sites uebergeben ``data``/``files``
  zwar positional, das Formular ist aber oeffentlich mit ``**kwargs``
  dokumentiert — ``test_kwargs_reach_the_underlying_form_via_super_init``
  pinnt das ueber ``initial=``.
* ``_10``/``_11`` — ``.order_by("entity_type", "name")`` verliert je einen
  Sortierschluessel. Beobachtbar nur an ``queryset.query.order_by`` (bei
  Gleichstand im verbleibenden Schluessel ist die DB-Reihenfolge nicht
  deterministisch) — der Pin prueft deshalb sowohl die konkrete PK-Liste als
  auch das Order-Tupel exakt (Muster aus
  ``test_mutation_pins_097_forms_facility_settings.py``, dort ``_31``/``_32``).
* ``_14`` — ``.filter(is_active=True)`` ohne
  ``entity_type__in=[CLIENT, EVENT]`` liesse Fall-Dokumentationstypen (CASE)
  im Import-Mapping-Dropdown erscheinen, obwohl der Import-Wizard nur
  Personen- und Ereignis-Ziele kennt (``ImportJob.TargetType`` hat kein
  ``CASE``).

Ein Test (``test_document_type_field_is_scoped_and_ordered_by_entity_type_then_name``)
deckt ``_10``/``_11``/``_14`` gemeinsam ab (dieselbe Queryset-Konstruktion).

**clean() (Zieltyp/Dokumentationstyp-Konsistenz):**

``clean()`` traegt zwei Zweige: (1) Ereignis-Import ohne Dokumentationstyp
ist ein Pflichtfeldfehler, (2) ein gesetzter Dokumentationstyp, dessen
``entity_type`` nicht zum gewaehlten Zieltyp passt, ist ein Mismatch-Fehler.
Beide Zweige rufen ``self.add_error("document_type", <Text>)`` explizit auf
(anders als ``core.forms.user_access``, wo ``clean_role``/``clean_is_active``
nur ``raise ValidationError(...)`` nutzen und Django das Feld implizit
zuordnet) — das gibt mutmut eine zusaetzliche Mutationsflaeche: das Feld
selbst kann auf ``None`` (NON_FIELD_ERRORS statt ``document_type``) fallen,
das Argument kann fehlen (``TypeError``) oder der Feldname kann verstuemmelt
werden (``ValueError: '...' is not a valid field``). Drei Pins:

* ``test_event_import_without_document_type_is_rejected`` (target_type=EVENT,
  document_type fehlt) killt ``_3``/``_4``/``_5``/``_6`` (Quelle von
  ``target_type`` kaputt — die Bedingung wird nie wahr, kein Fehler mehr),
  ``_13`` (Bedingung auf ``is not None`` geflippt — dieselbe Wirkung) und
  ``_14`` (``add_error(None, ...)`` haengt den Fehler an ``__all__`` statt an
  ``document_type``).
* ``test_event_import_with_matching_document_type_is_valid`` (target_type=
  EVENT, document_type mit passendem ``entity_type``) killt ``_7``/``_8``/
  ``_9``/``_10`` (Quelle von ``document_type`` erzwungen ``None`` — die
  Bedingung wird faelschlich wahr, obwohl ein gueltiger Typ gesetzt ist).
* ``test_event_import_with_mismatched_document_type_is_rejected`` (target_type
  =EVENT, document_type mit ``entity_type=CLIENT``) killt ``_27``
  (Vergleichsoperator auf ``==`` geflippt — der Mismatch wird nicht mehr
  erkannt), ``_28`` (Feld auf ``None``), ``_30``/``_31`` (``add_error`` ohne
  Nachrichtenargument — ``TypeError: add_error() missing 1 required
  positional argument: 'error'``, empirisch verifiziert gegen den
  Mutantenbaum), ``_32``/``_33`` (Feldname verstuemmelt —
  ``ValueError: 'XXdocument_typeXX' is not a valid field or
  NON_FIELD_ERRORS.``) und ``_34`` (``_(None)`` — ``gettext_lazy(None)`` wirft
  beim Erzwingen zu ``str()`` ``AttributeError: 'NoneType' object has no
  attribute 'replace'`` in ``django.utils.translation.trans_real.gettext``,
  empirisch verifiziert; die exakte Text-Assertion erzwingt genau diesen
  ``str()``-Aufruf ueber ``form.errors["document_type"]``).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env``/``ENCRYPTION_KEY``, ohne die Fixture waeren die Pins dort aus
dem falschen Grund rot.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.forms.import_data import ImportUploadForm
from core.models import DocumentType, ImportJob

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

_CSV_BYTES = b"spalte_a,spalte_b\nwert1,wert2\n"


def _upload_form(facility, *, target_type, document_type=None):
    data = {"target_type": target_type, "delimiter": "", "encoding": ""}
    if document_type is not None:
        data["document_type"] = str(document_type.pk)
    files = {"source_file": SimpleUploadedFile("import.csv", _CSV_BYTES, content_type="text/csv")}
    return ImportUploadForm(data, files, facility=facility)


class TestImportUploadFormInitScopeAndOrder:
    def test_kwargs_reach_the_underlying_form_via_super_init(self, facility):
        """Pins ``xǁImportUploadFormǁ__init____mutmut_2``: ``super().__init__(*args, )``
        laesst ``**kwargs`` (hier ``initial=``) fallen — ``forms.Form`` bekommt
        keine Vorbelegung mehr und ``form.initial`` bleibt faelschlich leer."""
        form = ImportUploadForm(facility=facility, initial={"delimiter": "comma"})

        assert form.initial == {"delimiter": "comma"}

    def test_document_type_field_is_scoped_and_ordered_by_entity_type_then_name(self, facility):
        """Pins ``xǁImportUploadFormǁ__init____mutmut_10``/``_11`` (Sortierschluessel
        ``entity_type``/``name`` je einzeln entfernt) und ``_mutmut_14``
        (``entity_type__in=[CLIENT, EVENT]``-Filter entfernt, Fall-Typen
        erscheinen dann faelschlich im Import-Mapping-Dropdown)."""
        case_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CASE,
            name="Nur fuer Faelle",
        )
        client_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="A Personenprofil",
        )
        event_b = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="B Ereignis",
        )
        event_a = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="A Ereignis",
        )

        form = ImportUploadForm(facility=facility)
        qs = form.fields["document_type"].queryset

        assert list(qs.values_list("pk", flat=True)) == [client_type.pk, event_a.pk, event_b.pk]
        assert case_type.pk not in qs.values_list("pk", flat=True)
        assert tuple(qs.query.order_by) == ("entity_type", "name")


class TestImportUploadFormCleanTargetDocumentTypeConsistency:
    def test_event_import_without_document_type_is_rejected(self, facility):
        """Pins ``xǁImportUploadFormǁclean__mutmut_3``/``_4``/``_5``/``_6``
        (``target_type`` wird ueber eine kaputte ``cleaned.get(...)``-Quelle
        immer ``None`` — die Ereignis-Bedingung wird nie wahr), ``_13``
        (Bedingung auf ``document_type is not None`` geflippt — bei fehlendem
        Dokumentationstyp ebenfalls nie wahr) und ``_14`` (``add_error(None,
        ...)`` haengt den Fehler an ``__all__`` statt an ``document_type``).
        Killt als Nebeneffekt der exakten Text-Assertion zusaetzlich die
        reinen Nachrichtentext-Mutanten ``_15`` (Text wird ``"None"``) sowie
        ``_21``/``_22``/``_23`` (Leerstring-Marker, Klein-/Grossschreibung)."""
        form = _upload_form(facility, target_type=ImportJob.TargetType.EVENT)

        assert not form.is_valid()
        assert form.errors["document_type"] == ["Für den Ereignis-Import ist ein Dokumentationstyp erforderlich."]

    def test_event_import_with_matching_document_type_is_valid(self, facility):
        """Pins ``xǁImportUploadFormǁclean__mutmut_7``/``_8``/``_9``/``_10``:
        eine kaputte ``cleaned.get(...)``-Quelle erzwingt ``document_type ==
        None`` selbst wenn ein passender Typ gesetzt ist — die
        Pflichtfeld-Bedingung wird dann faelschlich wahr und lehnt eine
        gueltige Ereignis-Zuordnung ab."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.EVENT,
            name="Krisengespräch",
        )
        form = _upload_form(facility, target_type=ImportJob.TargetType.EVENT, document_type=doc_type)

        assert form.is_valid(), form.errors
        assert "document_type" not in form.errors

    def test_event_import_with_mismatched_document_type_is_rejected(self, facility):
        """Pins ``xǁImportUploadFormǁclean__mutmut_27`` (Vergleich auf ``==``
        geflippt — der Mismatch bleibt unentdeckt), ``_28`` (Feld auf
        ``None``), ``_30``/``_31`` (``add_error`` ohne Nachrichtenargument,
        ``TypeError``), ``_32``/``_33`` (Feldname verstuemmelt, ``ValueError``)
        und ``_34`` (``_(None)`` — ``gettext_lazy(None)`` wirft beim
        Erzwingen zu ``str()`` in ``form.errors`` einen ``AttributeError``).
        Killt als Nebeneffekt der exakten Text-Assertion zusaetzlich ``_29``
        (Text wird ``"None"``) sowie ``_35``/``_36``/``_37`` (Leerstring-
        Marker, Klein-/Grossschreibung)."""
        doc_type = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Personenprofil",
        )
        form = _upload_form(facility, target_type=ImportJob.TargetType.EVENT, document_type=doc_type)

        assert not form.is_valid()
        assert form.errors["document_type"] == ["Der Dokumentationstyp passt nicht zum gewählten Zieltyp."]

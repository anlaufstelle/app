"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.config.delete_blockers`` (Batch ``delete_blockers-a``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@ebb52409 (frischer Mutantenbaum), Batch
``delete_blockers-a`` (63 Survivor-Mutanten in drei Funktionen:
``x_delete_document_type`` 26, ``x_field_template_delete_blockers`` 23,
``x__trashed_client_ids_with_attachment`` 14). Batch ``delete_blockers-b``
(``field_template_trashed_*_count``, ``_trashed_client_ids_with_slug``,
``_trashed_case_ids_with_*``, ``delete_field_template``, ``delete_field_group``)
gehoert einem anderen Batch-Agenten und wird hier NICHT angefasst.

Drei wiederkehrende Mutationsachsen in diesem Modul:

* **i18n-Textvarianten** (``"XX...XX"``-Wrap, Gross-/Kleinschreibung) auf
  ``_()``/``ngettext()``-Literalen — laut ``docs/testing/mutation-testing.md``
  ``skip``: der Text ist eine uebersetzte UI-Meldung ohne Hash-/Schluessel-/
  Vergleichsrolle, kein Aufrufer prueft ihren genauen Wortlaut ausserhalb
  dieser Pins.
* **Plural-msgid → ``None``** in ``ngettext(singular, None, n)``: mit dem in
  Tests standardmaessig AKTIVEN de-Katalog (``_reset_active_language``,
  ``conftest.py``) schlaegt ``ngettext`` ueber das Singular-``msgid`` im
  Katalog nach und liefert dessen Plural — das kaputte Argument bliebe dabei
  unsichtbar (Muster
  ``TestDocumentTypeCaseTrashShareWording.test_plural_fallback_carries_the_passed_msgid``
  in ``test_config_delete_blockers_trash.py``). Erst OHNE aktive Sprache
  (``translation.override(None)``) ist das uebergebene Plural-``msgid`` der
  einzige Fallback: dort liefert ``ngettext`` bei ``n != 1`` direkt ``None``
  zurueck, und die anschliessende ``% {...}``-Formatierung wirft
  ``TypeError`` statt des Blocker-Satzes. Gepinnt ueber Szenarien mit
  **zwei** referenzierenden Objekten UND ``translation.override(None)``.
* **facility_id-/Filter-Verlust** in den drei ``data_json__has_key``-Abfragen
  sowie im ``document_type``-Filter des Personen-Papierkorb-Anteils: zaehlt
  Abhaengigkeiten einer ANDEREN Einrichtung bzw. eines ANDEREN Dokumentations-
  typs mit — Papierkorb-/Blocker-Zustand falsch bewertet (Prio 1).

``_trashed_client_ids_with_attachment`` mutiert zusaetzlich die drei
``values_list(..., flat=True)``-Aufrufe: ohne Feldname faellt Django auf das
erste konkrete Modellfeld zurueck (``Attachment.id``, NICHT die Client-PK),
ohne ``flat=True`` liefert die Abfrage 1-Tupel statt PKs — beides bricht die
Mengen-Vereinigung der drei Owner-Pfade (Person/Ereignis/Fall). Gepinnt ueber
den exakten Rueckgabewert (Menge == ``{client.pk}``) je Owner-Pfad.

Konventionen wie ``test_mutation_pins_098_client_export.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Substring-Match uebersehen wuerde);
Fixtures kommen aus ``src/tests/conftest.py`` bzw. sind echte Model-Objekte.
Jeder Test nennt seine Mutanten in der Docstring. ``_encryption_key`` liegt
modulweit an (Lehre 112) — der Mutantenbaum hat keine ``.env`` und damit
keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import translation

from core.models import Attachment, Case, Client, DocumentType, Event, FieldTemplate, ImportJob, QuickTemplate
from core.services.config.delete_blockers import (
    ConfigDeleteBlocked,
    _trashed_client_ids_with_attachment,
    delete_document_type,
    field_template_delete_blockers,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _blocked_request(rf, admin_user, facility):
    request = rf.post("/")
    request.user = admin_user
    request.current_facility = facility
    return request


def _attachment(facility, field_template, *, event=None, client=None, case=None):
    return Attachment.objects.create(
        facility=facility,
        field_template=field_template,
        event=event,
        client=client,
        case=case,
        storage_filename=f"{uuid.uuid4().hex}.enc",
        original_filename_encrypted={"v": 1, "data": "x"},
        file_size=1,
        mime_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# x_delete_document_type — 10 der 26 Survivor sind Testluecken (16 skip)
# ---------------------------------------------------------------------------


class TestDeleteDocumentTypeSurvivors:
    def test_client_reason_plural_text_with_two_references(self, rf, facility, admin_user):
        """Mutant ``x_delete_document_type__mutmut_21``: das Plural-``msgid``
        des Personen-Satzes (``ngettext(singular, "...Personen...", n)``) wird
        durch ``None`` ersetzt. Mit AKTIVEM de-Katalog (Testdefault) schlaegt
        ``ngettext`` ueber das Singular-``msgid`` im Katalog nach und liefert
        dessen Plural — das kaputte Argument bliebe so unsichtbar (Muster
        ``TestDocumentTypeCaseTrashShareWording.test_plural_fallback_carries_the_passed_msgid``
        in ``test_config_delete_blockers_trash.py``). Nur OHNE aktive Sprache
        (``translation.override(None)``) ist das uebergebene Plural-``msgid``
        der einzige Fallback: dort liefert ``ngettext`` bei ``n != 1`` direkt
        ``None`` zurueck, und die ``% {"n": ...}``-Formatierung wirft
        ``TypeError`` statt den Blocker sauber zu werfen.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Typ Personen-Plural")
        Client.objects.create(facility=facility, pseudonym="P-Plural-1", document_type=doc_type)
        Client.objects.create(facility=facility, pseudonym="P-Plural-2", document_type=doc_type)

        with translation.override(None), pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(_blocked_request(rf, admin_user, facility), doc_type)

        assert exc.value.reasons == [
            "2 Personen sind diesem Dokumentationstyp zugeordnet und müssten zuerst geändert werden."
        ]

    def test_case_reason_plural_text_with_two_references(self, rf, facility, admin_user):
        """Mutant ``x_delete_document_type__mutmut_50``: das Plural-``msgid``
        des Fall-Satzes wird durch ``None`` ersetzt — gleicher Mechanismus wie
        mutmut_21, hier ueber zwei referenzierende Faelle OHNE aktive Sprache
        (``translation.override(None)``) erzwungen.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Typ Faelle-Plural")
        Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Fall-Plural-1"),
            title="Fall Plural 1",
            document_type=doc_type,
        )
        Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Fall-Plural-2"),
            title="Fall Plural 2",
            document_type=doc_type,
        )

        with translation.override(None), pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(_blocked_request(rf, admin_user, facility), doc_type)

        assert exc.value.reasons == [
            "2 Fälle sind diesem Dokumentationstyp zugeordnet und müssten zuerst geändert werden."
        ]

    def test_quick_template_reason_plural_text_with_two_references(self, rf, facility, admin_user):
        """Mutant ``x_delete_document_type__mutmut_74``: das Plural-``msgid``
        des Schnellvorlagen-Satzes wird durch ``None`` ersetzt — gleicher
        Mechanismus, hier ueber zwei referenzierende Schnellvorlagen OHNE
        aktive Sprache (``translation.override(None)``) erzwungen.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Typ Vorlagen-Plural")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type, name="Vorlage 1")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type, name="Vorlage 2")

        with translation.override(None), pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(_blocked_request(rf, admin_user, facility), doc_type)

        assert exc.value.reasons == [
            "2 Schnellvorlagen verweisen auf diesen Dokumentationstyp und müssten zuerst gelöscht werden."
        ]

    def test_client_trash_share_counts_only_clients_of_this_document_type(self, rf, facility, admin_user):
        """Mutant ``x_delete_document_type__mutmut_36``: der Papierkorb-Anteil-
        Zaehler des Personen-Satzes (``Client.objects.filter(document_type=doc_type,
        is_deleted=True).count()``) verliert den ``document_type``-Filter und
        zaehlt danach ALLE geloeschten Personen der Einrichtung — auch die
        eines anderen Dokumentationstyps. Papierkorb-Zustand falsch bewertet
        (Prio 1).

        Szenario: zwei Personen an ``doc_type`` (eine im Papierkorb) plus eine
        dritte, im Papierkorb liegende Person an einem ANDEREN Dokumentations-
        typ derselben Einrichtung — die darf nicht mitgezaehlt werden.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Typ Papierkorb-Anteil")
        other_doc_type = DocumentType.objects.create(facility=facility, name="Typ Papierkorb-Anteil (fremd)")
        Client.objects.create(facility=facility, pseudonym="P-Anteil-Aktiv", document_type=doc_type)
        trashed = Client.objects.create(facility=facility, pseudonym="P-Anteil-Korb", document_type=doc_type)
        trashed.soft_delete()
        foreign_trashed = Client.objects.create(
            facility=facility, pseudonym="P-Anteil-Korb-Fremd", document_type=other_doc_type
        )
        foreign_trashed.soft_delete()

        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(_blocked_request(rf, admin_user, facility), doc_type)

        assert exc.value.reasons == [
            "2 Personen sind diesem Dokumentationstyp zugeordnet und müssten zuerst geändert werden. "
            "Davon liegt 1 Person im Papierkorb."
        ]

    def test_generic_protect_fallback_reports_single_referencing_object(self, rf, facility, admin_user):
        """Der generische PROTECT-Fallback (``except ProtectedError``) ist ueber
        eine FUENFTE, nicht explizit gezaehlte PROTECT-Quelle erreichbar:
        ``ImportJob.document_type`` (Refs ``core/models/import_job.py``,
        PROTECT wie ``Client.document_type``) ist in den vier expliziten
        Zaehlern (Event/Client/Case/QuickTemplate) NICHT enthalten, sodass
        ``doc_type.delete()`` bei einem referenzierenden ImportJob eine
        ``ProtectedError`` wirft und den Fallback ausloest.

        Toetet gemeinsam sechs Mutanten desselben Fallback-Blocks:
        ``x_delete_document_type__mutmut_90`` (``blocked = None`` statt des
        formatierten Satzes), ``_91`` (``%`` → ``/``-Operator, TypeError),
        ``_92`` (``_("...")`` → ``_(None)``, AttributeError), ``_96``/``_97``
        (Formatstring-Schluessel ``"n"`` → ``"XXnXX"``/``"N"``, KeyError beim
        ``%``-Format) und ``_98`` (``ConfigDeleteBlocked([blocked])`` →
        ``ConfigDeleteBlocked(None)``, TypeError in ``"; ".join(None)`` beim
        Exception-``__init__``). Jeder dieser sechs Mutanten laesst die
        Blockierung mit einer ANDEREN Exception als ``ConfigDeleteBlocked``
        crashen oder veraendert den Satztext — beides bricht die Zusicherung.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Typ Generik-Fallback")
        ImportJob.objects.create(facility=facility, target_type=ImportJob.TargetType.CLIENT, document_type=doc_type)

        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(_blocked_request(rf, admin_user, facility), doc_type)

        assert exc.value.reasons == ["Der Dokumentationstyp wird noch von 1 Objekt(en) referenziert."]


# ---------------------------------------------------------------------------
# x_field_template_delete_blockers — 12 der 23 Survivor sind Testluecken
# (11 skip)
# ---------------------------------------------------------------------------


class TestFieldTemplateDeleteBlockersSurvivors:
    def test_attachment_trash_share_names_attachments_not_persons(self, facility, staff_user):
        """Toetet ``x_field_template_delete_blockers__mutmut_17``
        (``of_attachments=True`` → ``of_attachments=None``), ``_20``
        (``of_attachments=True`` komplett entfernt, Default ``False``) und
        ``_29`` (``of_attachments=True`` → ``of_attachments=False``): alle drei
        machen ``of_attachments`` in ``_append_trash_share`` falsy, wodurch der
        Personen- statt der Anhang-Zweig gewaehlt wird — der Satz nennt dann
        "Person" statt "Anhang" und benennt damit die FALSCHE Objektart im
        Papierkorb-Hinweis (Prio 1).
        """
        ft = FieldTemplate.objects.create(
            facility=facility, name="Datei-Anteil", slug="datei-anteil", field_type=FieldTemplate.FieldType.FILE
        )
        person = Client.objects.create(facility=facility, pseudonym="P-Anhang-Korb")
        _attachment(facility, ft, client=person)
        person.soft_delete()

        reasons = field_template_delete_blockers(ft)

        assert reasons == [
            "1 Dateianhang/-anhänge sind an diese Feldvorlage gebunden. "
            "Davon gehört 1 Anhang zu einer Person im Papierkorb."
        ]

    def test_event_slug_count_scoped_to_facility(self, facility, other_facility, staff_user):
        """Mutant ``x_field_template_delete_blockers__mutmut_33``: der
        ``facility_id``-Filter der Ereignis-Slug-Abfrage entfaellt — Ereignisse
        MIT demselben Slugwert in einer ANDEREN Einrichtung zaehlen mit.
        Zaehlt Abhaengigkeiten einer anderen Einrichtung (Prio 1).

        ``facility`` hat KEIN Ereignis mit diesem Slug, ``other_facility``
        SCHON — unter dem Original bleibt ``field_template_delete_blockers``
        also leer.
        """
        from django.utils import timezone

        ft = FieldTemplate.objects.create(
            facility=facility, name="Event-Slug", slug="event-slug-scope", field_type=FieldTemplate.FieldType.TEXT
        )
        foreign_doc_type = DocumentType.objects.create(facility=other_facility, name="Fremd-Kontakt")
        foreign_client = Client.objects.create(facility=other_facility, pseudonym="P-Fremd-Event")
        Event.objects.create(
            facility=other_facility,
            client=foreign_client,
            document_type=foreign_doc_type,
            occurred_at=timezone.now(),
            data_json={"event-slug-scope": "Wert"},
            created_by=staff_user,
        )

        assert field_template_delete_blockers(ft) == []

    def test_case_slug_count_scoped_to_facility(self, facility, other_facility):
        """Mutant ``x_field_template_delete_blockers__mutmut_75``: der
        ``facility_id``-Filter der Fall-Slug-Abfrage entfaellt — Faelle MIT
        demselben Slugwert in einer ANDEREN Einrichtung zaehlen mit. Zaehlt
        Abhaengigkeiten einer anderen Einrichtung (Prio 1).
        """
        ft = FieldTemplate.objects.create(
            facility=facility, name="Fall-Slug-Scope", slug="fall-slug-scope", field_type=FieldTemplate.FieldType.TEXT
        )
        foreign_client = Client.objects.create(facility=other_facility, pseudonym="P-Fremd-Fall")
        Case.objects.create(
            facility=other_facility,
            client=foreign_client,
            title="Fremder Fall",
            data_json={"fall-slug-scope": "Wert"},
        )

        assert field_template_delete_blockers(ft) == []

    def test_case_slug_count_requires_slug_key(self, facility):
        """Mutant ``x_field_template_delete_blockers__mutmut_76``: der
        ``data_json__has_key``-Filter der Fall-Slug-Abfrage entfaellt — die
        Abfrage zaehlt danach JEDEN Fall der Einrichtung, unabhaengig vom Slug.
        Falscher (ueberzaehliger) Fall-Blockergrund ohne Slugbezug.

        Ein Fall existiert in derselben Einrichtung, aber OHNE diesen Slug in
        ``data_json`` — unter dem Original bleibt die Vorlage loeschbar.
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Fall-Slug-Key",
            slug="fall-slug-key",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Ohne-Slug"),
            title="Fall ohne Slugdaten",
            data_json={"anderer-slug": "Wert"},
        )

        assert field_template_delete_blockers(ft) == []

    def test_case_slug_trash_share_present_and_names_faelle(self, facility):
        """Toetet ``x_field_template_delete_blockers__mutmut_79``
        (``len(_trashed_case_ids_with_slug(...))`` → ``None``: Papierkorb-
        Anteil wird stillschweigend NICHT angehaengt, obwohl ein Fall im
        Papierkorb liegt — Papierkorb-Zustand verschwiegen), ``_80``
        (``of_cases=True`` → ``of_cases=None``), ``_83`` (``of_cases=True``
        entfernt) und ``_101`` (``of_cases=True`` → ``of_cases=False``): alle
        drei letzteren machen ``of_cases`` falsy, wodurch der Personen- statt
        der Fall-Zweig gewaehlt wird ("Person" statt "Fall" im Papierkorb-
        Hinweis). Alle vier Mutanten sind Prio 1 (Papierkorb-Zustand falsch
        bewertet).
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Fall-Slug-Papierkorb",
            slug="fall-slug-papierkorb",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        case = Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Fall-Papierkorb"),
            title="Fall im Papierkorb",
            data_json={"fall-slug-papierkorb": "Wert"},
        )
        case.soft_delete()

        reasons = field_template_delete_blockers(ft)

        assert reasons == [
            "1 Fall enthält Daten unter dem Slug „fall-slug-papierkorb“. Davon liegt 1 Fall im Papierkorb."
        ]

    def test_client_slug_reason_plural_text_with_two_matches(self, facility):
        """Mutant ``x_field_template_delete_blockers__mutmut_57``: das
        Plural-``msgid`` des Personen-Slug-Satzes wird durch ``None`` ersetzt —
        derselbe Mechanismus wie mutmut_21 in ``delete_document_type`` (siehe
        dort): mit aktivem de-Katalog schlaegt ``ngettext`` ueber das
        Singular-``msgid`` nach und das kaputte Argument bliebe unsichtbar,
        daher hier ueber zwei Personen MIT Slugdaten UND
        ``translation.override(None)`` erzwungen.
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Person-Slug-Plural",
            slug="person-slug-plural",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        Client.objects.create(facility=facility, pseudonym="P-Slug-1", data_json={"person-slug-plural": "A"})
        Client.objects.create(facility=facility, pseudonym="P-Slug-2", data_json={"person-slug-plural": "B"})

        with translation.override(None):
            reasons = field_template_delete_blockers(ft)

        assert reasons == ["2 Personen enthalten Daten unter dem Slug „person-slug-plural“."]

    def test_case_slug_reason_plural_text_with_two_matches(self, facility):
        """Mutant ``x_field_template_delete_blockers__mutmut_86``: das
        Plural-``msgid`` des Fall-Slug-Satzes wird durch ``None`` ersetzt —
        derselbe Mechanismus, hier ueber zwei Faelle MIT Slugdaten UND
        ``translation.override(None)`` erzwungen.
        """
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Fall-Slug-Plural",
            slug="fall-slug-plural",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Fall-Slug-1"),
            title="Fall Slug 1",
            data_json={"fall-slug-plural": "A"},
        )
        Case.objects.create(
            facility=facility,
            client=Client.objects.create(facility=facility, pseudonym="P-Fall-Slug-2"),
            title="Fall Slug 2",
            data_json={"fall-slug-plural": "B"},
        )

        with translation.override(None):
            reasons = field_template_delete_blockers(ft)

        assert reasons == ["2 Fälle enthalten Daten unter dem Slug „fall-slug-plural“."]


# ---------------------------------------------------------------------------
# x__trashed_client_ids_with_attachment — alle 14 Survivor sind Testluecken
# ---------------------------------------------------------------------------


class TestTrashedClientIdsWithAttachmentSurvivors:
    def test_direct_client_attachment_path_returns_exact_pk_set(self, facility):
        """Toetet ``x__trashed_client_ids_with_attachment__mutmut_6``
        (``values_list("client_id", flat=True)`` → ``flat=None``: liefert
        1-Tupel statt PKs), ``_7`` (Feldname ``"client_id"`` entfernt — Django
        faellt auf das erste konkrete Modellfeld, ``Attachment.id``, zurueck —
        FALSCHE PKs) und ``_8``/``_13`` (``flat=True`` entfernt bzw. zu
        ``flat=False``: ebenfalls 1-Tupel statt PKs). Alle vier verfaelschen
        die Mengen-Vereinigung der Owner-Pfade — Papierkorb-Zustand falsch
        bewertet (Prio 1). Exakter Soll-Rueckgabewert: die Menge enthaelt
        GENAU die PK der geloeschten Person, mit korrektem Elementtyp.
        """
        ft = FieldTemplate.objects.create(
            facility=facility, name="Client-Pfad", slug="client-pfad", field_type=FieldTemplate.FieldType.FILE
        )
        person = Client.objects.create(facility=facility, pseudonym="P-Client-Pfad")
        _attachment(facility, ft, client=person)
        person.soft_delete()

        assert _trashed_client_ids_with_attachment(ft) == {person.pk}

    def test_event_owned_attachment_path_returns_exact_pk_set(self, facility, staff_user, doc_type_contact):
        """Toetet ``x__trashed_client_ids_with_attachment__mutmut_18``
        (``flat=None``), ``_19`` (Feldname ``"event__client_id"`` entfernt →
        ``Attachment.id`` statt Client-PK), ``_20``/``_25`` (``flat=True``
        entfernt bzw. ``flat=False``) sowie ``_22``
        (``event__client__is_deleted=True`` → ``=False``: invertierte
        Bedingung, findet Anhaenge an Ereignissen NICHT geloeschter Personen —
        verschweigt die geloeschte Person komplett). Alle fuenf verfaelschen
        den Ereignis-Pfad — Papierkorb-Zustand falsch bewertet (Prio 1).
        """
        from django.utils import timezone

        ft = FieldTemplate.objects.create(
            facility=facility, name="Event-Pfad", slug="event-pfad", field_type=FieldTemplate.FieldType.FILE
        )
        person = Client.objects.create(facility=facility, pseudonym="P-Event-Pfad")
        event = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        _attachment(facility, ft, event=event)
        person.soft_delete()

        assert _trashed_client_ids_with_attachment(ft) == {person.pk}

    def test_case_owned_attachment_path_returns_exact_pk_set(self, facility):
        """Toetet ``x__trashed_client_ids_with_attachment__mutmut_30``
        (``flat=None``), ``_31`` (Feldname ``"case__client_id"`` entfernt →
        ``Attachment.id`` statt Client-PK), ``_32``/``_37`` (``flat=True``
        entfernt bzw. ``flat=False``) sowie ``_33``
        (``case__client__is_deleted=True`` → ``=None``: ein Filter auf ein
        NOT-NULL-Boolean-Feld mit ``None`` uebersetzt sich als ``IS NULL`` —
        liefert ueber diesen Pfad IMMER ein leeres Ergebnis). Alle fuenf
        verfaelschen den Fall-Pfad — Papierkorb-Zustand falsch bewertet
        (Prio 1).
        """
        ft = FieldTemplate.objects.create(
            facility=facility, name="Case-Pfad", slug="case-pfad", field_type=FieldTemplate.FieldType.FILE
        )
        person = Client.objects.create(facility=facility, pseudonym="P-Case-Pfad")
        case = Case.objects.create(facility=facility, client=person, title="Fall Case-Pfad")
        _attachment(facility, ft, case=case)
        person.soft_delete()

        assert _trashed_client_ids_with_attachment(ft) == {person.pk}

"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — Batch ``config-1``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-config-1.json`` (50 Survivor-
Mutanten, Triage ``welle-l-0.99/reports/m-scratch/pins-config-1/triage-099-config-1.json``).
Zwei Module: ``core.services.config.mutations`` (41 Survivors — die Save-/
Zuordnungs-Services von ``DocumentType``/``FieldTemplate``/``FieldGroup``/
``DocumentTypeField``) und ``core.services.config.conditions`` (9 Survivors —
ausschliesslich ``update_field_condition``).

**Fünf Äquivalenz-Urteile (``save_document_type``/``save_field_template``/
``save_field_group``, kein Pin):**

* ``x_save_document_type__mutmut_{5,7,10}``: ``previous_sensitivity =
  form.initial.get("sensitivity", DEFAULT)`` mit ``DEFAULT`` ∈ ``{None,
  <fehlt>, "XXXX"}`` statt ``""``. ``previous_sensitivity`` hat GENAU einen
  Verbraucher — ``SENSITIVITY_RANK.get(previous_sensitivity, 0)`` in
  ``bump_ban_revision_for_document_type_change``. ``SENSITIVITY_RANK`` kennt
  ausschliesslich die drei ``DocumentType.Sensitivity``-Werte (direkt an
  ``core/services/compliance/sensitivity.py`` verifiziert); weder ``""``
  noch ``None`` noch ``"XXXX"`` ist ein Schluessel, alle drei Defaults liefern
  ``old_rank=0``. Kein Aufrufer kann die drei Varianten unterscheiden.
* ``x_save_field_template__mutmut_4`` / ``x_save_field_group__mutmut_4``:
  ``form.save(commit=False)`` -> ``form.save(commit=None)``. Djangos
  ``ModelForm.save()`` prueft nur ``if commit:`` (Truthiness,
  ``django/forms/models.py:572``) — ``None`` ist wie ``False`` falsy, beide
  Male wird derselbe ``else``-Zweig (``save_m2m`` deferred, KEIN sofortiges
  ``instance.save()``) genommen. Direkt am installierten Django verifiziert.

**Meldungsfamilien (bindend, exakte statt Teilstring-Vergleiche):** je
betroffener Meldung EIN Pin mit exaktem ``exc.value.messages``/``.args``-
Vergleich — dieser eine Vergleich schlaegt gleichermassen auf den
Meldungsverlust-Mutanten (``ValidationError(None)``/``ValueError(None)``,
Klasse ``test``/Prio 3) WIE auf dessen ``XX``-Marker-/Klein-/
Grossschreibungs-Varianten desselben Wortlauts an — verifiziert per Kill-Lauf
unter jeder einzelnen Variante (auch dort, wo kein Meldungsverlust-Mutant im
Batch liegt, z. B. ``add_field_to_doctype``s eigener Berichtsschluessel-
Konflikt-Meldung ``_39``/``_42``/``_43``/``_45``/``_46``/``_47`` und der
Fremdgruppen-Meldung ``_14``). Zwei dieser Familien schaerfen bestehende,
zu lasche Tests aus ``test_config_service.py`` NACH (Teilstring-``match=``
bzw. ``pytest.raises`` ohne Textpruefung) — diese Datei fasst
``test_config_service.py`` NICHT an, sondern deckt dieselben Szenarien mit
einem zusaetzlichen, eigenen Pin ab.

**Schreib-Scope-Regressionen (Klasse ``test``, Klassifikationsregel):**

* ``x_set_field_template_active__mutmut_2``: ``save(update_fields=["is_active"])``
  -> ``update_fields=None``. Der Aufrufer (View) laedt ``field_template``
  ohne ``select_for_update`` und ruft direkt ``save()`` — ein voller Save
  ueberschreibt zwischenzeitlich geaenderte Spalten (Lost Update).
* ``x_add_field_to_doctype__mutmut_{21,22}``: der Vorab-``exists()``-Check
  verliert seinen ``document_type``- bzw. ``field_template``-Anteil auf
  ``None`` — matcht dann NIE eine reale Zeile (beide FKs sind ``NOT NULL``),
  der Fruehausstieg entfaellt, das nachfolgende ``.create()`` scheitert
  stattdessen an der DB-``UniqueConstraint`` (zusaetzliche fehlschlagende
  INSERT-Query, die das Original NIE absetzt).
* ``x_add_field_to_doctype__mutmut_23``: derselbe Filter verliert das
  ``document_type``-Kwarg komplett (nicht nur auf ``None`` gesetzt) — eine
  an einem ANDEREN DocumentType gebundene ``field_template`` wird dadurch
  faelschlich als „hier bereits zugeordnet" abgewiesen (echte
  Scope-Regression, kein reiner Query-Unterschied).
* ``x_add_field_to_doctype__mutmut_59``: das ``current_max``-Aggregat fuer
  die Sortierung INNERHALB einer Gruppe verliert sein ``document_type=doc_type``
  — eine per ``.create()`` (das NIE ``full_clean()`` aufruft, siehe
  Docstring von ``add_field_to_doctype``) erzeugte „Rogue"-Zuordnung mit
  ANDEREM ``document_type``, aber derselben ``field_group`` blaeht das
  Maximum auf und verschiebt ``next_order``.
* ``x_conflicting_report_key_assignment__mutmut_7``: ``.select_related("document_type")``
  -> ``.select_related(None)``. Das loescht die GESAMTE select_related-Liste
  (Django-Doku), nicht nur ein Feld — der Zugriff auf
  ``zuordnung.document_type`` in der Schleife loest dann je Iteration eine
  zusaetzliche Query aus (N+1, beobachtbar ueber die Abfragezahl).

**Datenverlust (Prio 1):** ``x_add_field_to_doctype__mutmut_80`` — ``**kwargs``
entfaellt beim ``.create()``-Aufruf; jeder vom Aufrufer explizit gesetzte
Zusatzwert (z. B. eine vorgegebene ``id`` fuer reproduzierbare Importe) geht
still verloren.

Alle Exakt-Vergleiche (nie Teilstring/``pytest.raises(match=…)``) — sonst
uebersieht der Test den mutmut-Marker ``XX…XX``, weil der Original-Wortlaut
als Teilstring in der markierten Variante weiterhin vorkommt.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import modelform_factory
from django.test.utils import CaptureQueriesContext

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    FieldGroup,
    FieldTemplate,
)
from core.services.config import (
    add_field_to_doctype,
    conflicting_report_key_assignment,
    save_field_group,
    save_field_template,
    set_field_template_active,
    update_field_condition,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (Muster ``test_config_service.py``)."""
    from django.test import RequestFactory

    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


# ---------------------------------------------------------------------------
# save_field_template / save_field_group — changed_fields-Audit (Prio 1)
# ---------------------------------------------------------------------------


class TestSaveFieldTemplateChangedFieldsAudit:
    def test_update_audits_changed_fields_exactly(self, req, facility):
        """Pinnt ``xǁsave_field_templateǁmutmut_1``: ``changed_fields = list(form.changed_data)``
        -> ``changed_fields = None`` verliert die geaenderten Feldnamen im
        CONFIG_UPDATE-Audit-Detail komplett."""
        ft = FieldTemplate.objects.create(facility=facility, name="Alt", field_type=FieldTemplate.FieldType.TEXT)
        Form = modelform_factory(FieldTemplate, fields=["name", "field_type"])
        form = Form(instance=ft, data={"name": "Neu", "field_type": FieldTemplate.FieldType.TEXT})
        assert form.is_valid(), form.errors
        save_field_template(req, form, creating=False, facility=facility)
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(ft.pk)).latest("timestamp")
        assert entry.detail["changed_fields"] == ["name"]


class TestSaveFieldGroupChangedFieldsAudit:
    def test_update_audits_changed_fields_exactly(self, req, doc_type_contact):
        """Pinnt ``xǁsave_field_groupǁmutmut_1``: identischer Mechanismus wie
        ``save_field_template._1`` — ``changed_fields = None`` statt der
        Liste im CONFIG_UPDATE-Audit-Detail."""
        group = FieldGroup.objects.create(document_type=doc_type_contact, name="Alt")
        Form = modelform_factory(FieldGroup, fields=["name"])
        form = Form(instance=group, data={"name": "Neu"})
        assert form.is_valid(), form.errors
        save_field_group(req, form, doc_type=doc_type_contact, creating=False)
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(group.pk)).latest(
            "timestamp"
        )
        assert entry.detail["changed_fields"] == ["name"]


# ---------------------------------------------------------------------------
# save_field_template — Berichtsschluessel-Konflikt-Meldung (Prio 3)
# ---------------------------------------------------------------------------


class TestSaveFieldTemplateReportKeyConflictMessage:
    def test_message_is_exact(self, req, facility, doc_type_contact):
        """Pinnt ``xǁsave_field_templateǁmutmut_{16,19,20,22,23,25,26,27}``:
        Meldungsverlust (``ValidationError(None)``) bzw. ``XX``-Marker-/
        Klein-/Grossschreibungs-Varianten der drei zusammengesetzten
        Textteile — EIN exakter Vergleich toetet alle acht."""
        ft_dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert ft_dauer.report_key == "dauer_minuten"
        assert ft_dauer.is_active is True
        ft_neu = FieldTemplate.objects.create(
            facility=facility, name="Zweite Dauer", field_type=FieldTemplate.FieldType.NUMBER
        )
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft_neu, sort_order=5)
        Form = modelform_factory(FieldTemplate, fields=["name", "field_type", "report_key"])
        form = Form(
            instance=ft_neu,
            data={
                "name": ft_neu.name,
                "field_type": FieldTemplate.FieldType.NUMBER,
                "report_key": "dauer_minuten",
            },
        )
        assert form.is_valid(), form.errors
        with pytest.raises(ValidationError) as excinfo:
            save_field_template(req, form, creating=False, facility=facility)
        assert excinfo.value.messages == [
            "„Zweite Dauer“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
            "„Dauer“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
            "zulässig — sonst zählt der Bericht keine von beiden."
        ]


# ---------------------------------------------------------------------------
# set_field_template_active — Schreib-Scope (Prio 2)
# ---------------------------------------------------------------------------


class TestSetFieldTemplateActiveWriteScope:
    def test_toggle_only_touches_is_active_column(self, req, facility):
        """Pinnt ``xǁset_field_template_activeǁmutmut_2``:
        ``save(update_fields=["is_active"])`` -> ``update_fields=None``. Der
        Aufrufer laedt ``field_template`` ohne ``select_for_update`` (View:
        ``get_object_or_404``) — ein voller Save ueberschreibt eine
        zwischenzeitliche Fremdaenderung an einer ANDEREN Spalte (Lost
        Update). Nachweis ueber das UPDATE-SQL (nur ``is_active``) UND den
        DB-Endzustand nach dem Toggle."""
        ft = FieldTemplate.objects.create(
            facility=facility, name="Scope-Test", field_type=FieldTemplate.FieldType.TEXT, is_active=True
        )
        # Fremdaenderung NACH dem (impliziten) Laden von ft, simuliert per
        # direktem .update() — kein select_for_update sichert das In-Memory-
        # Objekt gegen diese Aenderung ab.
        FieldTemplate.objects.filter(pk=ft.pk).update(name="Von woanders geaendert")
        with CaptureQueriesContext(connection) as ctx:
            set_field_template_active(req, ft, active=False)
        update_sql = [q["sql"] for q in ctx.captured_queries if q["sql"].upper().startswith("UPDATE")]
        assert len(update_sql) == 1
        assert '"name"' not in update_sql[0]
        assert '"is_active"' in update_sql[0]
        ft.refresh_from_db()
        assert ft.name == "Von woanders geaendert"
        assert ft.is_active is False


# ---------------------------------------------------------------------------
# conflicting_report_key_assignment — select_related-Abfragezahl (Prio 2)
# ---------------------------------------------------------------------------


class TestConflictingReportKeyAssignmentQueryCount:
    def test_select_related_avoids_n_plus_one(self, req, facility, doc_type_contact):
        """Pinnt ``xǁconflicting_report_key_assignmentǁmutmut_7``:
        ``.select_related("document_type")`` -> ``.select_related(None)``
        loescht die GESAMTE select_related-Liste (Django-Doku), nicht nur
        ein Feld — der Zugriff auf ``zuordnung.document_type`` in der
        Schleife loest dann je gebundener Zuordnung eine zusaetzliche Query
        aus. ``ft_dauer`` haengt genau EINMAL an ``doc_type_contact``."""
        ft_dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert DocumentTypeField.objects.filter(field_template=ft_dauer).count() == 1
        with CaptureQueriesContext(connection) as ctx:
            result = conflicting_report_key_assignment(ft_dauer, "unbenutzter-schluessel-ohne-konflikt", is_active=True)
        assert result is None
        # Original: 1 Query fuer die (gejointe) zuordnungen-Liste + 1 Query
        # fuer die bestand-Pruefung der einzigen Zuordnung = 2. Die Mutante
        # braucht eine zusaetzliche Query, um zuordnung.document_type ohne
        # Join nachzuladen.
        assert len(ctx.captured_queries) <= 2


# ---------------------------------------------------------------------------
# add_field_to_doctype — Fremdgruppen-Meldung (Prio 3)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeForeignGroupMessage:
    def test_message_is_exact(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_{4,6,7,8}``: Meldungsverlust
        (``ValueError(None)``) bzw. ``XX``-Marker-/Klein-/Grossschreibungs-
        Varianten — EIN exakter ``args``-Vergleich toetet alle vier."""
        other_dt = DocumentType.objects.create(facility=facility, name="Fremd-Typ fuer Meldungstext")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremde Gruppe")
        ft = FieldTemplate.objects.create(
            facility=facility, name="Neu-Fremd-Text", field_type=FieldTemplate.FieldType.TEXT
        )
        with pytest.raises(ValueError) as excinfo:
            add_field_to_doctype(req, doc_type_contact, ft, field_group=foreign_grp)
        assert excinfo.value.args == ("Die Feldgruppe gehört nicht zu diesem Dokumentationstyp.",)


# ---------------------------------------------------------------------------
# add_field_to_doctype — Reservierter-Slug-Meldung (Prio 3)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeReservedSlugMessage:
    def test_message_is_exact(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_14``: die erste Haelfte der
        Meldung wird kleingeschrieben. Der Bestandstest
        (``test_config_service.py::test_add_reserved_slug_template_raises_and_creates_nothing``)
        prueft nur ``pytest.raises(ValidationError, match="festen Formularfeld")``
        — dieses Teilstring-Match bleibt bei der Kleinschreibung der ERSTEN
        Zeile wahr, weil nur die zweite Haelfte gesucht wird. Ein exakter
        Volltext-Vergleich toetet die Mutante."""
        (legacy,) = FieldTemplate.objects.bulk_create(
            [
                FieldTemplate(
                    facility=facility,
                    name="Alt-Reserviert",
                    slug="pseudonym",
                    field_type=FieldTemplate.FieldType.TEXT,
                )
            ]
        )
        with pytest.raises(ValidationError) as excinfo:
            add_field_to_doctype(req, doc_type_contact, legacy)
        assert excinfo.value.messages == [
            "Der Slug „pseudonym“ dieser Feldvorlage kollidiert mit einem festen "
            "Formularfeld und kann nicht zugeordnet werden."
        ]


# ---------------------------------------------------------------------------
# add_field_to_doctype — Dubletten-Vorab-Check: Schreib-Scope (Prio 2)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeDuplicateScopeWithoutInsertAttempt:
    def test_early_exit_never_attempts_an_insert(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_{21,22}``: der Vorab-``exists()``-
        Check verliert ``document_type`` bzw. ``field_template`` auf ``None``
        — matcht dann NIE eine reale Zeile (beide FKs sind ``NOT NULL``), der
        Fruehausstieg entfaellt, das nachfolgende ``.create()`` scheitert
        stattdessen an der DB-``UniqueConstraint`` (zusaetzliche, fehlschlagende
        INSERT-Query, die das Original NIE absetzt). Derselbe Pin bestaetigt
        per exaktem Meldungsvergleich zugleich ``_25``/``_27``/``_28``/``_29``
        (Meldungsverlust-Familie derselben Dublettenmeldung, exists()-Zweig)."""
        existing_ft = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=existing_ft).exists()
        with CaptureQueriesContext(connection) as ctx:
            with pytest.raises(ValidationError) as excinfo:
                add_field_to_doctype(req, doc_type_contact, existing_ft)
        assert excinfo.value.messages == ["Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet."]
        assert not any(q["sql"].upper().startswith("INSERT") for q in ctx.captured_queries)


class TestAddFieldToDoctypeDuplicateScopeAcrossDoctype:
    def test_same_field_template_allowed_on_a_different_doctype(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_23``: der Vorab-``exists()``-
        Check verliert das ``document_type``-Kwarg KOMPLETT (nicht nur auf
        ``None`` gesetzt) — eine an einem ANDEREN DocumentType gebundene
        ``field_template`` wird dadurch faelschlich als „hier bereits
        zugeordnet" abgewiesen, obwohl die UniqueConstraint nur das Paar
        (document_type, field_template) schuetzt und Mehrfachbindung an
        verschiedene Typen legitim ist."""
        existing_ft = FieldTemplate.objects.get(facility=facility, name="Dauer")
        andere_doc_type = DocumentType.objects.create(facility=facility, name="Anderer Typ fuer Scope-Test")
        dtf = add_field_to_doctype(req, andere_doc_type, existing_ft)
        assert dtf.document_type_id == andere_doc_type.id
        assert DocumentTypeField.objects.filter(document_type=andere_doc_type, field_template=existing_ft).exists()


# ---------------------------------------------------------------------------
# add_field_to_doctype — eigene Berichtsschluessel-Konflikt-Meldung (Prio 3)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeReportKeyConflictMessage:
    def test_message_is_exact(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_{39,42,43,45,46,47}`` — die
        Berichtsschluessel-Konflikt-Meldung am ZWEITEN Vorkommen im Modul
        (``conflicting_report_key_field``, eigener Code-Pfad zu
        ``save_field_template``s Meldung). Kein Meldungsverlust-Mutant
        dieser Stelle liegt im Batch, aber der exakte Vergleich toetet alle
        sechs Text-Varianten gleichermassen."""
        ft_dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert ft_dauer.report_key == "dauer_minuten"
        ft_neu = FieldTemplate.objects.create(
            facility=facility,
            name="Doppel-Dauer",
            field_type=FieldTemplate.FieldType.NUMBER,
            report_key="dauer_minuten",
        )
        with pytest.raises(ValidationError) as excinfo:
            add_field_to_doctype(req, doc_type_contact, ft_neu)
        assert excinfo.value.messages == [
            "„Doppel-Dauer“ trägt denselben Berichtsschlüssel wie die bereits zugeordnete Feldvorlage "
            "„Dauer“. Je Dokumentationstyp ist höchstens eine Feldvorlage je Berichtsschlüssel "
            "zulässig — sonst zählt der Bericht keine von beiden."
        ]


# ---------------------------------------------------------------------------
# add_field_to_doctype — Gruppen-Sortierung: Schreib-Scope (Prio 2)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeGroupSortOrderScope:
    def test_current_max_scoped_to_doctype(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_59``: das ``current_max``-
        Aggregat fuer die Sortierung INNERHALB einer Gruppe verliert
        ``document_type=doc_type``. ``DocumentTypeField.objects.create()``
        ruft NIE ``full_clean()`` auf (Docstring von
        ``add_field_to_doctype``) — eine so erzeugte „Rogue"-Zuordnung mit
        ANDEREM ``document_type``, aber DERSELBEN ``field_group`` blaeht das
        Maximum unter der Mutante auf."""
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Scope-Gruppe")
        andere_doc_type = DocumentType.objects.create(facility=facility, name="Fremd fuer Gruppen-Scope")
        rogue_ft = FieldTemplate.objects.create(
            facility=facility, name="Rogue", field_type=FieldTemplate.FieldType.TEXT
        )
        DocumentTypeField.objects.create(
            document_type=andere_doc_type, field_template=rogue_ft, field_group=grp, sort_order=99
        )
        ft = FieldTemplate.objects.create(
            facility=facility, name="Neu in Gruppe", field_type=FieldTemplate.FieldType.TEXT
        )
        dtf = add_field_to_doctype(req, doc_type_contact, ft, field_group=grp)
        assert dtf.sort_order == 0


# ---------------------------------------------------------------------------
# add_field_to_doctype — **kwargs-Durchreichung (Prio 1, Datenverlust)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeKwargsPassthrough:
    def test_extra_kwargs_reach_the_created_row(self, req, facility, doc_type_contact):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_80``: ``**kwargs`` entfaellt
        beim ``.create()``-Aufruf. Ein vom Aufrufer explizit gesetzter
        Zusatzwert (hier: eine vorgegebene ``id``, wie sie ein Paket-/
        Migrations-Import braeuchte) geht unter der Mutante still verloren —
        das Modell nimmt stattdessen seinen ``uuid4``-Default."""
        ft = FieldTemplate.objects.create(
            facility=facility, name="Mit fester ID", field_type=FieldTemplate.FieldType.TEXT
        )
        custom_id = uuid4()
        dtf = add_field_to_doctype(req, doc_type_contact, ft, id=custom_id)
        assert dtf.pk == custom_id
        dtf.refresh_from_db()
        assert dtf.pk == custom_id


# ---------------------------------------------------------------------------
# add_field_to_doctype — Dubletten-Meldung ueber den IntegrityError-Zweig (Prio 3)
# ---------------------------------------------------------------------------


class TestAddFieldToDoctypeIntegrityErrorMessage:
    def test_message_is_exact_after_forced_race(self, req, facility, doc_type_contact, monkeypatch):
        """Pinnt ``xǁadd_field_to_doctypeǁmutmut_{81,83,84,85}`` — dieselbe
        Dublettenmeldung, aber am IntegrityError->ValidationError-
        Uebersetzungs-Zweig (nur ueber eine erzwungene Race erreichbar). Der
        Bestandstest
        (``test_config_service.py::test_add_duplicate_translates_integrity_error``)
        nutzt dieselbe Verhaltens-Injektion (``QuerySet.exists`` auf
        ``False`` gezwungen), prueft aber nur ``pytest.raises(ValidationError)``
        OHNE Text — deshalb ueberlebten alle vier Textvarianten. Dieser Pin
        ergaenzt den exakten Meldungsvergleich."""
        from django.db.models.query import QuerySet

        existing_ft = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=existing_ft).exists()
        monkeypatch.setattr(QuerySet, "exists", lambda self: False)
        with pytest.raises(ValidationError) as excinfo:
            add_field_to_doctype(req, doc_type_contact, existing_ft)
        assert excinfo.value.messages == ["Diese Feldvorlage ist dem Dokumentationstyp bereits zugeordnet."]


# ---------------------------------------------------------------------------
# conditions.update_field_condition — changed_fields-Audit (Prio 1)
# ---------------------------------------------------------------------------


def _make_dtf(doc_type, facility, name, field_type, *, sort_order=5):
    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=field_type)
    return DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=sort_order)


class TestUpdateFieldConditionChangedFieldsAudit:
    """``changed_fields.append("target_field"/"depends_on_field"/"group_index")``
    -> ``None``/``XX``-Marker/GROSSSCHREIBUNG. Je Feld EIN Pin mit exaktem
    Listenvergleich, der alle drei Varianten (Meldungsverlust + zwei
    Textvarianten) desselben Feldnamens toetet — die anderen Parameter
    bleiben in jedem Test unveraendert, damit ``changed_fields`` GENAU ein
    Element enthaelt."""

    def test_target_field_change_is_audited_exactly(self, req, facility, doc_type_contact, field_condition):
        """Pinnt ``xǁupdate_field_conditionǁmutmut_{3,4,5}``."""
        dauer = doc_type_contact.fields.get(field_template__slug="dauer")
        neues_ziel = _make_dtf(doc_type_contact, facility, "Neues Ziel", FieldTemplate.FieldType.TEXT)
        update_field_condition(
            req,
            field_condition,
            target_field=neues_ziel,
            depends_on_field=dauer,
            operator=field_condition.operator,
            value=field_condition.value,
            group_index=field_condition.group_index,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_condition.pk)).latest(
            "timestamp"
        )
        assert entry.detail["changed_fields"] == ["target_field"]

    def test_depends_on_field_change_is_audited_exactly(self, req, facility, doc_type_contact, field_condition):
        """Pinnt ``xǁupdate_field_conditionǁmutmut_{7,8,9}``."""
        notiz = doc_type_contact.fields.get(field_template__slug="notiz")
        neue_quelle = _make_dtf(doc_type_contact, facility, "Neue Quelle", FieldTemplate.FieldType.NUMBER)
        update_field_condition(
            req,
            field_condition,
            target_field=notiz,
            depends_on_field=neue_quelle,
            operator=field_condition.operator,
            value=field_condition.value,
            group_index=field_condition.group_index,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_condition.pk)).latest(
            "timestamp"
        )
        assert entry.detail["changed_fields"] == ["depends_on_field"]

    def test_group_index_change_is_audited_exactly(self, req, doc_type_contact, field_condition):
        """Pinnt ``xǁupdate_field_conditionǁmutmut_{19,20,21}``."""
        dauer = doc_type_contact.fields.get(field_template__slug="dauer")
        notiz = doc_type_contact.fields.get(field_template__slug="notiz")
        update_field_condition(
            req,
            field_condition,
            target_field=notiz,
            depends_on_field=dauer,
            operator=field_condition.operator,
            value=field_condition.value,
            group_index=field_condition.group_index + 1,
        )
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_condition.pk)).latest(
            "timestamp"
        )
        assert entry.detail["changed_fields"] == ["group_index"]

"""Unit-Tests für ``ImportUploadForm`` (M4, Refs #1060; Sicherheits-Nachzug #1811).

#1811: Mutation-Survivor-Triage 2026-08-10 fand drei sicherheitsrelevante
Testlücken in ``ImportUploadForm.__init__`` (mutmut_3/4/16) — die
``document_type``-Queryset-Scopeung auf die übergebene Facility war durch
keinen Test abgesichert, obwohl das Dropdown strukturell auf die eigene
Einrichtung begrenzt sein muss (Docstring der Klasse: "Die Facility kommt als
Keyword-Argument aus der View (nie aus dem POST)"). Die View-Ebene
(``test_import_views.py::test_upload_rejects_a_foreign_document_type``) prüft
nur den POST-Roundtrip; hier wird das Formular direkt instanziiert, um genau
die mutierten Bedingungen zu treffen.
"""

from __future__ import annotations

import pytest

from core.forms.import_data import ImportUploadForm
from core.models import DocumentType
from tests._form_helpers import queryset_pks


@pytest.mark.django_db
class TestImportUploadFormFacilityScoping:
    def test_facility_kwarg_is_stored_on_the_form(self, facility):
        """Refs #1811 (mutmut_3, ``self.facility = None``): das ``facility``-Kwarg
        muss unverändert als Instanzattribut vorliegen — sonst könnten spätere
        Codepfade, die sich auf ``form.facility`` verlassen, stillschweigend auf
        „keine Einrichtung" zurückfallen."""
        form = ImportUploadForm(facility=facility)
        assert form.facility == facility

    def test_document_type_queryset_contains_only_own_active_types(self, facility, foreign_doc_type):
        """Refs #1811 (mutmut_4, invertierte ``facility is not None``-Bedingung,
        und mutmut_16, ``for_facility(None)`` statt ``for_facility(facility)``):
        beide Mutationen leeren oder entgrenzen das ``document_type``-Dropdown.
        Ein Typ der eigenen Einrichtung muss in der Queryset stehen (widerlegt
        mutmut_4/mutmut_16, die die Queryset leer lassen würden), ein Typ einer
        fremden Einrichtung darf es nie (widerlegt eine Entgrenzung)."""
        own = DocumentType.objects.create(
            facility=facility,
            name="Eigener Import-Typ",
            entity_type=DocumentType.EntityType.EVENT,
            is_active=True,
        )
        form = ImportUploadForm(facility=facility)
        pks = queryset_pks(form, "document_type")
        assert own.pk in pks
        assert foreign_doc_type.pk not in pks

    def test_inactive_own_type_is_excluded(self, facility):
        """Gegenprobe zur Scoping-Prüfung: ``is_active=False`` bleibt weiterhin
        ausgefiltert, das Facility-Scoping darf diesen bestehenden Filter nicht
        verdrängen."""
        DocumentType.objects.create(
            facility=facility,
            name="Deaktivierter Import-Typ",
            entity_type=DocumentType.EntityType.EVENT,
            is_active=False,
        )
        form = ImportUploadForm(facility=facility)
        assert "Deaktivierter Import-Typ" not in [dt.name for dt in form.fields["document_type"].queryset]

    def test_without_facility_kwarg_queryset_stays_empty(self):
        """Sanity-Gegenprobe: ohne ``facility``-Kwarg (z. B. defensiver
        Formularaufbau) bleibt die Queryset beim ``.none()``-Default — kein
        versehentliches Leerlassen der Bedingung darf stattdessen ALLE Typen
        zeigen."""
        form = ImportUploadForm()
        assert form.fields["document_type"].queryset.count() == 0

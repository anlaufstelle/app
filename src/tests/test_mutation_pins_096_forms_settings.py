"""Mutation-Pins v0.96.0 — ``FacilitySettingsForm`` bindet die uebergebene Instanz.

``core/views/facility_settings.py`` reicht das ``Settings``-Objekt der
Einrichtung als ``instance=`` durch — in GET wie in POST. Geht dieses kwarg
verloren, baut ``ModelForm`` ein frisches ``Settings()``: die Seite rendert
dann nicht die gespeicherten Werte, sondern die Modell-Vorgaben aller
sicherheitsrelevanten Felder (Aufbewahrungsfristen, MFA-Pflicht, Fluchtseite,
Speicherkontingent). Wer sie so speichert, setzt die Einstellungen der
Einrichtung still auf Vorgabewerte zurueck.
``test_facility_settings_hub.py`` prueft am GET nur Ueberschriften und
``name=``-Attribute, nie Werte.
"""

from __future__ import annotations

import pytest

from core.forms.facility_settings import FacilitySettingsForm
from core.models import Settings

pytestmark = pytest.mark.django_db


class TestFacilitySettingsFormInstanceBinding:
    def test_form_renders_the_stored_values_of_the_passed_instance(self, facility, settings_obj, doc_type_contact):
        """Pins xǁFacilitySettingsFormǁ__init____mutmut_2: ohne ``**kwargs`` an
        ``super().__init__`` verliert das Formular die ``instance`` und zeigt
        Modell-Vorgaben statt der gespeicherten Werte der Einrichtung."""
        settings_obj.retention_identified_days = 400
        settings_obj.quick_exit_url = "https://www.wetter.example/"
        settings_obj.mfa_enforced_facility_wide = True
        settings_obj.default_document_type = doc_type_contact
        settings_obj.save()

        form = FacilitySettingsForm(instance=settings_obj, facility=facility)

        assert form.instance.pk == settings_obj.pk
        assert form.initial["retention_identified_days"] == 400
        assert form.initial["quick_exit_url"] == "https://www.wetter.example/"
        assert form.initial["mfa_enforced_facility_wide"] is True
        assert form.initial["default_document_type"] == doc_type_contact.pk
        # Gegenprobe: die gepinnten Werte weichen von den Modell-Vorgaben ab —
        # ein frisches ``Settings()`` wuerde die Assertions oben nicht erfuellen.
        fresh = Settings()
        assert fresh.retention_identified_days != 400
        assert fresh.mfa_enforced_facility_wide is not True

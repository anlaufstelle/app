"""Refs #1142: Die beworbenen verschlüsselten Anhänge müssen im Default-Flow
erreichbar sein.

Hintergrund: Der File-Vault verschlüsselt *jeden* Upload at-rest, unabhängig
vom ``is_encrypted``-Flag des Feldes (siehe ``store_encrypted_file``) — das
Flag steuert nur die data_json-Verschlüsselung von Textwerten. Die
*Sichtbarkeit* eines Feldes hängt allein an ``FieldTemplate.sensitivity``
(siehe ``test_field_sensitivity.py``).

Bis #1142 trug die einzige FILE-Feldvorlage des Seeds (``Scan/Bescheid`` auf
``Beratungsgespräch``) wegen ``encrypted=True`` automatisch
``sensitivity="high"`` — damit strich ``remove_restricted_fields`` sie für
Fachkraft (max. ELEVATED) und Assistenz (max. NORMAL) weg. Es gab also keinen
einzigen für Fachkraft/Assistenz erstellbaren Doku-Typ mit erreichbarem
Datei-Upload, obwohl die Produktseite „Verschlüsselte Anhänge" bewirbt.

Der Seed muss daher mindestens ein FILE-Feld auf NORMAL-Sichtbarkeit an einem
für Fachkraft *und* Assistenz erstellbaren Doku-Typ bereitstellen.
"""

import pytest
from django import forms

from core.forms.events import DynamicEventDataForm
from core.models import DocumentType
from core.seed.doc_types import seed_document_types
from core.services.compliance import allowed_sensitivities_for_user
from core.services.events.fields import remove_restricted_fields


def _doc_types_with_reachable_file_field(user, facility):
    """Namen der für *user* erstellbaren Doku-Typen, die nach der
    Sensitivity-Filterung ein Datei-Upload-Feld zeigen."""
    creatable = DocumentType.objects.for_facility(facility).filter(
        is_active=True,
        sensitivity__in=allowed_sensitivities_for_user(user),
    )
    names = []
    for dt in creatable:
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        remove_restricted_fields(user, dt, form)
        if any(isinstance(f, forms.FileField) for f in form.fields.values()):
            names.append(dt.name)
    return names


@pytest.mark.django_db
class TestSeededAttachmentReachability:
    def test_staff_can_reach_a_file_upload_field(self, facility, staff_user):
        seed_document_types(facility)
        reachable = _doc_types_with_reachable_file_field(staff_user, facility)
        assert reachable, "Kein für Fachkraft erstellbarer Seed-Doku-Typ zeigt ein Datei-Upload-Feld"

    def test_assistant_can_reach_a_file_upload_field(self, facility, assistant_user):
        seed_document_types(facility)
        reachable = _doc_types_with_reachable_file_field(assistant_user, facility)
        assert reachable, "Kein für Assistenz erstellbarer Seed-Doku-Typ zeigt ein Datei-Upload-Feld"

"""Pins fuer den Klarnamenhinweis dynamischer Textfelder (``core.forms.dynamic``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 1 Mutant in
``build_dynamic_form_field``: ohne konfigurierten Hilfetext ist der
Hilfetext exakt der Hinweis — ohne Praefix aus einem Ersatzwert.
"""

from core.forms.dynamic import DYNAMIC_FIELD_PLAINTEXT_HINT, build_dynamic_form_field
from core.models import FieldTemplate


class TestBuildDynamicFormField:
    def test_textfeld_ohne_hilfetext_bekommt_genau_den_hinweis(self):
        """x_build_dynamic_form_field__mutmut_47: der Default ``"XXXX"`` statt
        ``""`` ergaebe ``"XXXX <Hinweis>"``."""
        ft = FieldTemplate(name="Notiz", slug="notiz", field_type=FieldTemplate.FieldType.TEXT, help_text="")
        feld = build_dynamic_form_field(ft, {})
        assert feld.help_text == str(DYNAMIC_FIELD_PLAINTEXT_HINT)

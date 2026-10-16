"""Date and time widget HTML pins through the public field-type registry.

Source: mutation delta of the release-0.99.8 checkpoint, 4 mutants.
Groups: valid date input attributes (2), valid second-resolution time attributes (2).
Related pattern: test_mutation_pins_0997_forms_config.py.
"""

from html.parser import HTMLParser

from core.services.system.field_types import get_spec


class _InputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(attrs)


def _assert_input_type(widget, kind):
    context = widget.get_context("value", None, {})["widget"]
    assert not any("type" in name.lower() for name in context["attrs"])
    parser = _InputParser()
    parser.feed(widget.render("value", None))
    assert len(parser.inputs) == 1
    attributes = parser.inputs[0]
    assert [(key, value) for key, value in attributes if "type" in key.lower()] == [("type", kind)]
    return dict(attributes)


def test_date_widget_renders_one_valid_type_attribute():
    """The date registry produces a single type attribute without stray machine keys.

    x__widget_date__mutmut_2
    x__widget_date__mutmut_3
    """
    _assert_input_type(get_spec("date").widget_factory(), "date")


def test_time_widget_renders_one_valid_type_and_seconds_step():
    """The time registry produces a single type attribute and preserves second resolution.

    x__widget_time__mutmut_2
    x__widget_time__mutmut_3
    """
    attrs = _assert_input_type(get_spec("time").widget_factory(), "time")
    assert attrs["step"] == "1"

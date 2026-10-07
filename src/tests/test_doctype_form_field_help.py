"""Document type form: short inline help plus expandable deep help (Refs #2460).

The form used to print the full explanation under every field. Now each
critical field keeps one short orientation sentence directly below it and
carries the consequences, changeability and a worked example in a native
``<details>`` block inside the same field wrapper (so the entity-type
show/hide script hides both together).
"""

from html.parser import HTMLParser

import pytest
from django.urls import reverse

VOID_ELEMENTS = {"input", "br", "img", "hr", "meta", "link", "area", "base", "col", "source", "wbr"}

DEEP_HELP_FIELDS = (
    "entity_type",
    "sensitivity",
    "system_type",
    "report_key",
    "min_contact_stage",
    "retention_days",
    "tally_enabled",
)

#: Hard ceiling for the one-sentence inline orientation (characters).
INLINE_MAX_LENGTH = 130


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[_Node] = []
        self.text_parts: list[str] = []

    def text(self) -> str:
        parts = list(self.text_parts)
        for child in self.children:
            parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find_by_id(self, element_id):
        return next((node for node in self.walk() if node.attrs.get("id") == element_id), None)

    def find_all(self, tag, **attrs):
        return [
            node
            for node in self.walk()
            if node.tag == tag and all(node.attrs.get(key.replace("_", "-")) == value for key, value in attrs.items())
        ]


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", [], None)
        self._current = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self._current)
        self._current.children.append(node)
        if tag not in VOID_ELEMENTS:
            self._current = node

    def handle_endtag(self, tag):
        node = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data):
        self._current.text_parts.append(data)


def _parse(body: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(body)
    return builder.root


def _closest_div(node: _Node) -> _Node:
    current = node.parent
    while current is not None and current.tag != "div":
        current = current.parent
    assert current is not None
    return current


@pytest.fixture
def create_page(client, admin_user):
    client.force_login(admin_user)
    return _parse(client.get(reverse("core:config_doctype_create")).content.decode())


@pytest.mark.django_db
class TestInlineHelpIsShort:
    @pytest.mark.parametrize("field", DEEP_HELP_FIELDS)
    def test_inline_help_is_one_short_sentence_and_stays_linked_to_the_input(self, create_page, field):
        inline = create_page.find_by_id(f"id_{field}-help")

        assert inline is not None, field
        assert 0 < len(inline.text()) <= INLINE_MAX_LENGTH, inline.text()

    @pytest.mark.parametrize(
        ("field", "expected"),
        (
            (
                "system_type",
                "Steuert besondere App-Funktionen wie Hausverbot oder Krisen-Hervorhebung. "
                "Nach dem Anlegen nicht mehr änderbar.",
            ),
            ("report_key", "Ordnet diesen Typ Berichten zu. Leer bedeutet: noch nicht zugeordnet."),
            (
                "min_contact_stage",
                "Legt fest, ob dieser Typ anonym nutzbar ist oder eine gespeicherte Person braucht.",
            ),
            (
                "tally_enabled",
                "Macht diesen Ereignistyp für die schnelle anonyme Zählung verfügbar, "
                "wenn er die Voraussetzungen erfüllt.",
            ),
        ),
    )
    def test_inline_help_uses_the_agreed_orientation_wording(self, create_page, field, expected):
        assert create_page.find_by_id(f"id_{field}-help").text() == expected

    @pytest.mark.parametrize("field", ("entity_type", "system_type"))
    def test_unchangeable_settings_are_named_inline(self, create_page, field):
        assert "nicht" in create_page.find_by_id(f"id_{field}-help").text()
        assert "änderbar" in create_page.find_by_id(f"id_{field}-help").text()

    def test_fields_without_deep_help_keep_no_details_block(self, create_page):
        names = {
            node.attrs["data-field-help"] for node in create_page.find_all("details") if "data-field-help" in node.attrs
        }

        assert names == set(DEEP_HELP_FIELDS)


@pytest.mark.django_db
class TestDeepHelp:
    @pytest.mark.parametrize("field", DEEP_HELP_FIELDS)
    def test_deep_help_sits_in_the_field_wrapper_next_to_the_input(self, create_page, field):
        details = create_page.find_all("details", data_field_help=field)

        assert len(details) == 1, field
        # The entity-type script hides ``input.closest("div")``: the deep help must
        # share exactly that wrapper, or it would stay visible for hidden fields.
        input_wrapper = _closest_div(create_page.find_by_id(f"id_{field}"))
        assert _closest_div(details[0]) is input_wrapper, field

    @pytest.mark.parametrize("field", DEEP_HELP_FIELDS)
    def test_deep_help_has_a_named_summary_and_the_three_explanations(self, create_page, field):
        details = create_page.find_all("details", data_field_help=field)[0]
        summary = details.find_all("summary")[0].text()
        body = details.text()

        assert summary.startswith("Mehr zu „")
        for heading in ("Folgen:", "Änderbar:", "Beispiel:"):
            assert heading in body, (field, heading)

    @pytest.mark.parametrize(
        ("field", "needles"),
        (
            ("entity_type", ("Ereignis", "Person", "Fall", "Nach dem Anlegen unveränderbar")),
            ("sensitivity", ("Normal", "Erhöht", "Hoch", "verschlüsselt", "Assistenz")),
            ("system_type", ("Hausverbot", "Krisengespräch", "Berichtsschlüssel", "unveränderbar")),
            ("report_key", ("Gehört ausdrücklich in keinen Bericht", "Lücke", "unveränderbar")),
            ("min_contact_stage", ("anonym", "Nur Pseudonym", "Mit Zusatzangaben")),
            ("retention_days", ("Tage", "Ereignisse", "Kontaktstufe")),
            ("tally_enabled", ("Strichliste", "Zahlen-Pflichtfelder", "Mindest-Kontaktstufe")),
        ),
    )
    def test_deep_help_explains_the_field_in_practical_terms(self, create_page, field, needles):
        body = create_page.find_all("details", data_field_help=field)[0].text()

        for needle in needles:
            assert needle in body, (field, needle)

    @pytest.mark.parametrize("field", ("entity_type", "system_type"))
    def test_deep_help_keeps_the_full_rule_text_of_the_field(self, create_page, field):
        """Nothing is lost: the former inline text moved into the expandable block."""
        body = create_page.find_all("details", data_field_help=field)[0].text()

        assert "Nach Erstellung unveränderbar." in body

    def test_report_key_edit_hint_survives_in_the_deep_help(self, client, admin_user, facility):
        from core.models import DocumentType

        client.force_login(admin_user)
        doc_type = DocumentType.objects.create(
            facility=facility, name="Beratung Probe", report_key="counseling", sort_order=1
        )

        page = _parse(client.get(reverse("core:config_doctype_update", kwargs={"pk": doc_type.pk})).content.decode())
        body = page.find_all("details", data_field_help="report_key")[0].text()

        assert "Falsch gesetzt?" in body
        assert page.find_by_id("id_report_key-help").text() == (
            "Ordnet diesen Typ Berichten zu. Leer bedeutet: noch nicht zugeordnet."
        )

    def test_deep_help_has_no_active_content(self, create_page):
        for details in create_page.find_all("details"):
            tags = {node.tag for node in details.walk()}
            assert not tags & {"script", "style"}, details.attrs
            assert "style" not in {key for node in details.walk() for key in node.attrs}

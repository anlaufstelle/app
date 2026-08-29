"""Ueberschriften-Hierarchie der Aufgaben-Detailseite (Refs #2007).

``core/workitems/detail.html`` setzte seine Abschnittsmarken als gestylte
``<span>``\\ s zwischen ``h1`` und Inhalt. Optisch sind sie Ueberschriften,
im Accessibility-Tree sind sie nichts: die Ueberschriften-Navigation
(Rotor/„H"-Taste) endet auf dieser Seite beim ``h1``, waehrend die
Nachbar-Detailseiten (``clients/detail.html:185,198,226,242``,
``cases/detail.html:103``) echte ``h2`` mit derselben Pixelform tragen.

Der Guard prueft beide Richtungen: die Marken sind ``h2`` — und keine von
ihnen ist als ``span`` zurueckgefallen.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse

#: Die Abschnittsmarken der Detailseite in Renderreihenfolge.
SECTION_LABELS = [
    "Typ",
    "Status",
    "Priorität",
    "Zu erledigen bis",
    "Wiedervorlage am",
    "Erstellt am",
    "Erstellt von",
    "Zugewiesen an",
    "Beschreibung",
]

#: Die Pixelform, die die Marken tragen — identisch vor und nach dem Umbau.
LABEL_CLASSES = "text-[12px] font-bold uppercase tracking-wider text-ink-muted"


@pytest.fixture
def assigned_workitem(sample_workitem, staff_user):
    """Eine Aufgabe mit Beschreibung und Zuweisung — sonst fehlen zwei Marken."""
    sample_workitem.description = "Rueckruf vereinbaren"
    sample_workitem.assigned_to = staff_user
    sample_workitem.save(update_fields=["description", "assigned_to"])
    return sample_workitem


@pytest.mark.django_db
class TestWorkitemDetailHeadings:
    def _content(self, client, staff_user, workitem) -> str:
        client.force_login(staff_user)
        return client.get(reverse("core:workitem_detail", args=[workitem.pk])).content.decode()

    @pytest.mark.parametrize("label", SECTION_LABELS)
    def test_section_label_is_a_real_h2(self, client, staff_user, assigned_workitem, label):
        content = self._content(client, staff_user, assigned_workitem)

        assert re.search(rf"<h2[^>]*>\s*{re.escape(label)}\s*</h2>", content), (
            f"Abschnittsmarke {label!r} ist keine echte <h2>"
        )

    def test_no_section_label_stays_a_styled_span(self, client, staff_user, assigned_workitem):
        content = self._content(client, staff_user, assigned_workitem)
        start = content.index("<h1")

        assert f'<span class="{LABEL_CLASSES}"' not in content[start:], (
            "Eine Abschnittsmarke ist als gestyltes <span> zurueckgefallen"
        )

    def test_the_headings_keep_their_pixel_shape(self, client, staff_user, assigned_workitem):
        """„Visuelles Erscheinungsbild unveraendert" ist Teil der Anforderung."""
        content = self._content(client, staff_user, assigned_workitem)

        assert f'<h2 class="{LABEL_CLASSES}">Typ</h2>' in content

    def test_exactly_one_h1_remains(self, client, staff_user, assigned_workitem):
        content = self._content(client, staff_user, assigned_workitem)

        assert content.count("<h1") == 1

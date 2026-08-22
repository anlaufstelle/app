"""Erklaerflaechen fuer die Kontaktstufen (Refs #1805, Konzept §3.2).

Der Persona-Walk hat "Begriffe dort erklaeren, wo sie benutzt werden"
validiert — kein Glossar-Link, kein Tooltip-Framework (M3 gesperrt). Drei
Bausteine, die hier festgehalten werden:

* **A** — ``help_text`` an den Eingabeflaechen (Personenformular,
  Dokumentationstyp-Formular).
* **C1** — eine permanente Legendenzeile je Liste ohne Platz fuer
  Untertitel (Personenliste, Offline-Personenliste, Loeschfristen).
* **C2/C3** — ``title`` am Personen-Badge; die Loeschantrag-Review benutzt
  denselben Badge statt einer eigenen ``<dt>``-Zeile.

Baustein B (Untertitel an Leseflaechen) ist mit der Umbenennung der Labels
gegenstandslos geworden — das Primaerlabel traegt die Aussage selbst.
"""

from __future__ import annotations

import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import Client, DeletionRequest

LEGEND_TESTID = 'data-testid="contact-stage-legend"'

#: Refs #1862: Die pauschale Zusage "jeder Zugriff wird protokolliert" ist am
#: Code falsch. ``AuditLog.Action.VIEW_QUALIFIED`` entsteht projektweit nur im
#: GET der Personen-Detailseite (``core/views/clients.py``); das
#: Bearbeiten-Formular derselben Person zeigt ihre Zusatzangaben
#: entschluesselt im Klartext und schreibt **keinen** Eintrag (Kommentar
#: "Update-GET zeigt Klartext"), Liste, Ereignis, Fall und Suchtreffer
#: ebenfalls nicht. Diese beiden Bausteine sind ausgeliefertes UI — sie geben
#: der Nutzerin die Zusage direkt, deshalb haengt der Guard hier.
BLANKET_ACCESS_CLAIM_RE = re.compile(
    r"jede[rn]?\s+Zugriff\w*(\s+darauf)?\s+wird\s+(automatisch\s+)?protokolliert",
    re.IGNORECASE,
)

#: Die Flaeche, auf die die Zusage tatsaechlich zutrifft — die Detailseite der
#: Person (``/clients/<id>/``), und nur sie.
SCOPED_ACCESS_CLAIM_RE = re.compile(r"Detailseite[^.;]{0,30}wird protokolliert")


@pytest.mark.django_db
class TestContactStageHelpTexts:
    """Baustein A — die Eingabeflaechen erklaeren die Stufen."""

    def test_contact_stage_help_text_names_both_stages_and_the_consequence(self):
        help_text = str(Client._meta.get_field("contact_stage").help_text)
        assert "Nur Pseudonym" in help_text
        assert "Mit Zusatzangaben" in help_text
        # Die Konsequenz der Auswahl, nicht nur die Definition (user-guide.md:315).
        assert "protokolliert" in help_text

    def test_contact_stage_help_text_scopes_the_audit_claim(self):
        """Refs #1862: nicht "jeder Zugriff", sondern der Aufruf des Profils."""
        help_text = str(Client._meta.get_field("contact_stage").help_text)
        assert not BLANKET_ACCESS_CLAIM_RE.search(help_text), (
            f"``contact_stage.help_text`` sagt eine flaechendeckende Zugriffs-"
            f"Protokollierung zu ({help_text!r}) — geschrieben wird nur beim "
            "Aufruf der Personen-Detailseite (VIEW_QUALIFIED)."
        )
        assert SCOPED_ACCESS_CLAIM_RE.search(help_text), help_text

    def test_contact_stage_help_text_carries_no_markdown(self):
        """T3: ``_form_field.html`` rendert ``help_text`` ohne ``|safe`` —
        ``**fett**`` erzeugt literale Sternchen im Formular."""
        assert "**" not in str(Client._meta.get_field("contact_stage").help_text)

    def test_min_contact_stage_help_text_is_not_circular(self):
        from core.models import DocumentType

        help_text = str(DocumentType._meta.get_field("min_contact_stage").help_text)
        # Alt: "Mindest-Kontaktstufe der Person fuer diesen Dokumentationstyp".
        assert help_text != "Mindest-Kontaktstufe der Person für diesen Dokumentationstyp"
        # Die eigentlich fehlende Information: was "leer" bedeutet.
        assert "leer" in help_text


@pytest.mark.django_db
class TestContactStageLegend:
    """Baustein C1 — eine Legendenzeile je Liste ohne Untertitel-Platz."""

    def test_client_list_shows_legend(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:client_list")).content.decode()
        assert LEGEND_TESTID in content

    def test_offline_client_list_shell_shows_legend(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:offline_client_list_shell")).content.decode()
        assert LEGEND_TESTID in content

    def test_retention_dashboard_shows_legend(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:retention_dashboard")).content.decode()
        assert LEGEND_TESTID in content

    def test_legend_scopes_the_audit_claim_to_the_person_profile(self):
        """Refs #1862: die Legende steht auf drei Listen — und die Liste selbst
        schreibt keinen Eintrag."""
        html = render_to_string("components/_contact_stage_legend.html")
        assert not BLANKET_ACCESS_CLAIM_RE.search(html), (
            "Die Legendenzeile sagt eine flaechendeckende Zugriffs-"
            "Protokollierung zu — geschrieben wird nur beim Aufruf der "
            "Personen-Detailseite (VIEW_QUALIFIED)."
        )
        assert SCOPED_ACCESS_CLAIM_RE.search(html), html


@pytest.mark.django_db
class TestClientBadgeTitle:
    """Baustein C2/C3 — Badge traegt die Erklaerung als ``title``."""

    def _render_badge(self, client_obj):
        from django.template.loader import render_to_string

        return render_to_string("components/_client_badge.html", {"client": client_obj})

    def test_identified_badge_has_explaining_title(self, client_identified):
        html = self._render_badge(client_identified)
        assert 'data-testid="client-stage-badge"' in html
        assert "title=" in html
        assert "Pseudonym" in html

    def test_qualified_badge_title_names_the_audit_consequence(self, client_qualified):
        html = self._render_badge(client_qualified)
        assert "title=" in html
        assert "protokolliert" in html

    def test_qualified_badge_title_scopes_the_audit_claim(self, client_qualified):
        """Refs #1862: der Badge haengt auch an Liste, Suchtreffer und
        Loeschantrag-Review — alles Flaechen ohne ``VIEW_QUALIFIED``."""
        html = self._render_badge(client_qualified)
        assert not BLANKET_ACCESS_CLAIM_RE.search(html), (
            "Der Badge-``title`` sagt eine flaechendeckende Zugriffs-"
            "Protokollierung zu — geschrieben wird nur beim Aufruf der "
            "Personen-Detailseite (VIEW_QUALIFIED)."
        )
        assert SCOPED_ACCESS_CLAIM_RE.search(html), html

    def test_badge_keeps_its_visible_label(self, client_qualified):
        """``title`` ersetzt keine a11y-Arbeit — der sichtbare Text bleibt."""
        assert "Mit Zusatzangaben" in self._render_badge(client_qualified)

    def test_client_deletion_review_uses_the_badge(self, client, lead_user, client_qualified):
        """C3: statt einer eigenen ``<dt>``-Zeile erbt die Review den Badge."""
        dr = DeletionRequest.objects.create(
            facility=client_qualified.facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=client_qualified.pk,
            requested_by=lead_user,
            reason="Test",
        )
        client.force_login(lead_user)
        content = client.get(reverse("core:deletion_review", args=[dr.pk])).content.decode()
        assert 'data-testid="client-stage-badge"' in content
        # Erbt damit den erklaerenden ``title`` aus C2.
        assert "protokolliert" in content


@pytest.mark.django_db
class TestMinStageHintParity:
    """Baustein A3 — Online- und Offline-Erfassung sagen denselben Satz."""

    def test_event_create_reuses_the_offline_hint_msgid(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:event_create")).content.decode()
        assert "Dieser Dokumentationstyp erfordert eine zugeordnete Person." in content
        assert "erfordert eine Mindest-Kontaktstufe" not in content

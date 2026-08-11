"""Kontext-Badges am Objekt-Kopf (Refs #1802, Konzept #1801 §3.5).

Zusätzliche Eltern eines Objekts erscheinen als verlinkte Pille neben dem
Titel, nicht als Segment eines Breadcrumb-Trails (Regel R4). Zwei Zusagen sind
hier festgeschrieben:

1. **Höchstens zwei, nur wenn die Beziehung gesetzt ist.** Ein Ereignis hat
   null bis drei Eltern — alle drei Felder sind nullable
   (``core/models/event.py``). Ein Trail müsste sich einen erfinden; Badges
   lassen jeden einzeln weg.
2. **Regel R8, fail-closed.** ``event_detail`` ist ``ASSISTANT_PLUS``,
   ``case_detail`` ist ``STAFF_PLUS``. Für die Assistenz erscheint deshalb
   **weder Badge noch Fall-Titel** — Fall-Titel sind Freitext, ein unverlinkter
   Text wäre ein Existenz- und Inhalts-Leak (Maintainer-Entscheid 2 zu §6.2).

Dieser Test muss eigenständig existieren, weil ``test_authz_template_links``
den Zweig fixture-bedingt nicht erreichte: seine ``guard_content``-Fixture legte
kein Ereignis MIT Fall an — genau die im Guard-Docstring dokumentierte Grenze
„ein Link in einem Zweig ohne Testdaten fällt durch das Raster".
"""

from __future__ import annotations

import pytest
from django.urls import reverse

BADGE_TESTIDS = ("context-badge-client", "context-badge-case")


def _get(client, user, url_name, obj):
    client.force_login(user)
    response = client.get(reverse(url_name, kwargs={"pk": obj.pk}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.mark.django_db
class TestEventContextBadges:
    def test_staff_sees_client_and_case_badge(self, client, staff_user, case_event):
        html = _get(client, staff_user, "core:event_detail", case_event)

        assert 'data-testid="context-badge-client"' in html
        assert 'data-testid="context-badge-case"' in html
        assert reverse("core:case_detail", kwargs={"pk": case_event.case.pk}) in html

    def test_assistant_sees_neither_case_badge_nor_case_title(self, client, assistant_user, case_event):
        """Fail-closed: kein Link, kein Text, keine Existenz-Aussage.

        ``case_detail`` ist für die Assistenz ein 403 — ein Badge wäre ein
        Link ins Nichts (Refs #1735), der blosse Titel ein Leak: Fall-Titel
        sind Freitext und können den Anlass benennen.
        """
        html = _get(client, assistant_user, "core:event_detail", case_event)

        assert 'data-testid="context-badge-case"' not in html
        assert case_event.case.title not in html
        assert reverse("core:case_detail", kwargs={"pk": case_event.case.pk}) not in html

    def test_event_without_parents_shows_no_badges(self, client, staff_user, sample_event):
        sample_event.client = None
        sample_event.is_anonymous = True
        sample_event.save(update_fields=["client", "is_anonymous"])

        html = _get(client, staff_user, "core:event_detail", sample_event)

        assert not any(f'data-testid="{testid}"' in html for testid in BADGE_TESTIDS)

    def test_event_with_client_only_shows_one_badge(self, client, staff_user, sample_event):
        html = _get(client, staff_user, "core:event_detail", sample_event)

        assert 'data-testid="context-badge-client"' in html
        assert 'data-testid="context-badge-case"' not in html


@pytest.mark.django_db
class TestWorkItemContextBadge:
    def test_client_badge_appears_in_the_head(self, client, staff_user, sample_workitem):
        """Die Person steht im Kopf, nicht als Zeile 7 der Detailtabelle.

        Beziehungen gehören nach oben, Metadaten (Typ, Status, Frist) bleiben
        im Block — sonst doppelt der Kopf, was die Tabelle schon sagt.
        """
        html = _get(client, staff_user, "core:workitem_detail", sample_workitem)

        assert 'data-testid="context-badge-client"' in html
        kopf_ende = html.index('data-testid="workitem-actions-desktop"')
        assert html.index('data-testid="context-badge-client"') < kopf_ende

    def test_workitem_without_client_shows_no_badge(self, client, staff_user, sample_workitem):
        sample_workitem.client = None
        sample_workitem.save(update_fields=["client"])

        html = _get(client, staff_user, "core:workitem_detail", sample_workitem)

        assert 'data-testid="context-badge-client"' not in html


@pytest.mark.django_db
class TestEpisodeFormContextBadge:
    """Konzept §3.5-Tabelle, Zeile „Episode-Formular": ← Fallname + Person des Falls.

    Der Rücklink der Episodenseiten nennt den **Fall** (Objekt-Eltern nach
    §3.4). Ohne Badge sagt der Kopf damit nicht, um wessen Akte es geht — die
    Episode liegt unter einem Fall, der Fall unter einer Person, und genau
    diese zweite Beziehung ist der Grund, warum §3.5 Badges statt eines
    zweiten Rücklinks vorsieht (R4).

    Kein Rollengefälle (R8): ``episode_create``/``episode_update`` sind
    ``STAFF_PLUS``, ``client_detail`` ist ``ASSISTANT_PLUS`` — wer das
    Formular sieht, darf die Person auch öffnen.
    """

    def _form_html(self, client, user, url_name, **kwargs):
        client.force_login(user)
        response = client.get(reverse(url_name, kwargs=kwargs))
        assert response.status_code == 200
        return response.content.decode()

    def test_create_form_shows_the_person_of_the_case(self, client, staff_user, case_open):
        html = self._form_html(client, staff_user, "core:episode_create", case_pk=case_open.pk)

        assert 'data-testid="context-badge-client"' in html
        assert reverse("core:client_detail", kwargs={"pk": case_open.client.pk}) in html
        assert case_open.client.pseudonym in html

    def test_edit_form_shows_the_person_of_the_case(self, client, staff_user, case_open, episode):
        html = self._form_html(client, staff_user, "core:episode_update", case_pk=case_open.pk, pk=episode.pk)

        assert 'data-testid="context-badge-client"' in html
        assert reverse("core:client_detail", kwargs={"pk": case_open.client.pk}) in html

    def test_badge_stands_after_the_h1_not_before_the_back_link(self, client, staff_user, case_open):
        """Reihenfolge des Kontext-Kopfes: Rücklink → H1 → Badges (§3.1/§3.5).

        Ein Badge über dem Rücklink läse sich als zweiter Rückweg; die
        Zugehörigkeit gehört unter den Titel, den sie qualifiziert.
        """
        html = self._form_html(client, staff_user, "core:episode_create", case_pk=case_open.pk)

        rueck = html.index('data-testid="back-link"')
        h1 = html.index("<h1")
        badge = html.index('data-testid="context-badge-client"')
        assert rueck < h1 < badge

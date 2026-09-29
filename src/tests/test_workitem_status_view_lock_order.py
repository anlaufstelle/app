"""Der Status-POST einer Aufgabe sperrt Person vor Aufgabe — ueber den ganzen Request.

Beobachtet wird die Folge der gesperrten Tabellen im Mitschnitt des ganzen
Requests, nicht der Wortlaut der Anweisungen: eine Aufgabensperre vor der
Personensperre (Aufgabe -> Person -> Aufgabe) stuende gegen jeden Pfad, der
Person -> Aufgabe sperrt, im Zyklus.

Die Berechtigung wird dabei weiterhin am gesperrten Stand entschieden: wird
eine Teamaufgabe nach der ungesperrten Vorpruefung einer anderen Person
zugewiesen, endet der POST mit derselben 403-Antwort wie die Vorpruefung, der
Status bleibt und es entsteht keine Audit-Zeile.
"""

import pytest
from django.urls import reverse

from core.models import AuditLog, WorkItem
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_KEINE_BERECHTIGUNG = "Keine Berechtigung für diese Aufgabe."


def _url(workitem):
    return reverse("core:workitem_status_update", args=[workitem.pk])


def _personen_und_aufgaben(folge):
    return [tabelle for tabelle in folge if tabelle in ("core_client", "core_workitem")]


@pytest.mark.parametrize("htmx", [False, True], ids=["formular", "htmx"])
def test_person_vor_aufgabe(client, facility, client_identified, staff_user, htmx):
    workitem = WorkItem.objects.create(
        facility=facility, client=client_identified, created_by=staff_user, title="Aufgabe mit Person"
    )
    client.force_login(staff_user)
    header = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    with mitschnitt() as m:
        response = client.post(_url(workitem), {"status": WorkItem.Status.IN_PROGRESS}, **header)
    assert response.status_code == (200 if htmx else 302)
    assert WorkItem.objects.get(pk=workitem.pk).status == WorkItem.Status.IN_PROGRESS
    assert _personen_und_aufgaben(m.sperrfolge()) == ["core_client", "core_workitem"], m.sperrfolge()


def test_berechtigung_faellt_am_gesperrten_stand(client, facility, client_identified, staff_user, lead_user):
    workitem = WorkItem.objects.create(
        facility=facility, client=client_identified, created_by=lead_user, title="Teamaufgabe"
    )
    client.force_login(staff_user)
    audit_vorher = AuditLog.objects.count()
    with mitschnitt() as m:
        m.nach_sperre("core_client", lambda: WorkItem.objects.filter(pk=workitem.pk).update(assigned_to=lead_user))
        response = client.post(_url(workitem), {"status": WorkItem.Status.DONE})
    assert m.offene_schranken() == [], "das Rennen wurde nicht ausgeloest"
    assert response.status_code == 403
    assert response.content.decode() == _KEINE_BERECHTIGUNG
    assert WorkItem.objects.get(pk=workitem.pk).status == WorkItem.Status.OPEN
    assert AuditLog.objects.count() == audit_vorher

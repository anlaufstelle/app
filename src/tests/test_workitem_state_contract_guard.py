"""Aufgabendienste entscheiden am frischen, gesperrten Stand.

Eine Person, die nach dem Laden in den Papierkorb gelegt, deaktiviert oder
zusammengefuehrt wurde, nimmt keine neuen oder geaenderten Aufgabendaten mehr
an; eine inzwischen geloeschte Aufgabe wird nicht mehr geaendert. Der veraltete
Zustand wird dafuer in der Datenbank hergestellt (``update()`` bzw. eine zweite
Instanz), nie nur im Speicher. Beobachtet werden Meldung, geschriebene Zeilen,
Audit- und Zeitstrom-Eintraege und die Reihenfolge der gesperrten Tabellen
(Person vor Aufgabe).

Die Views bilden eine solche Absage des Dienstes ab, statt mit HTTP 500 zu
enden.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import Activity, AuditLog, Client, WorkItem
from core.services.client.main import CLIENT_WRITE_REVOKED_MESSAGE
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

WIDERRUFE = ["papierkorb", "inaktiv", "zusammengefuehrt"]


def _widerrufen(person, art):
    """Stellt den Widerruf in der Datenbank her — die geladene Instanz bleibt veraltet."""
    zeilen = Client.objects.filter(pk=person.pk)
    if art == "papierkorb":
        zeilen.update(is_deleted=True, deleted_at=timezone.now())
    elif art == "inaktiv":
        zeilen.update(is_active=False)
    else:
        ziel = Client.objects.create(facility_id=person.facility_id, pseudonym="Ziel der Zusammenführung")
        zeilen.update(merged_into=ziel, is_deleted=True, deleted_at=timezone.now())


def _zaehler():
    return WorkItem.objects.count(), AuditLog.objects.count(), Activity.objects.count()


def _assert_refused(aufruf, meldung):
    vorher = _zaehler()
    with mitschnitt() as m, pytest.raises(ValidationError) as excinfo:
        aufruf()
    assert excinfo.value.messages == [str(meldung)]
    assert _zaehler() == vorher
    return m


def _person_vor_aufgabe(m):
    folge = m.sperrfolge()
    if "core_workitem" in folge and "core_client" in folge:
        assert folge.index("core_client") < folge.index("core_workitem"), folge
    return folge


class TestAnlegen:
    @pytest.mark.parametrize("art", WIDERRUFE)
    def test_widerrufene_person(self, facility, client_identified, staff_user, art):
        from core.services.case import create_workitem

        _widerrufen(client_identified, art)
        _assert_refused(
            lambda: create_workitem(facility, staff_user, client=client_identified, title="Neu"),
            CLIENT_WRITE_REVOKED_MESSAGE,
        )
        assert not WorkItem.objects.filter(title="Neu").exists()


class TestAnlegenUeberDieView:
    """Die Person verschwindet zwischen Formularpruefung und Dienst."""

    def _post(self, client, person, **header):
        return client.post(
            reverse("core:workitem_create"),
            {"item_type": "task", "title": "Neu im Rennen", "priority": "normal", "client": str(person.pk)},
            **header,
        )

    def test_formular_zeigt_die_meldung(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        vorher = AuditLog.objects.count()
        with mitschnitt() as m:
            m.nach_sperre("core_client", lambda: _widerrufen(client_identified, "papierkorb"))
            response = self._post(client, client_identified)
        assert m.offene_schranken() == [], "das Rennen wurde nicht ausgeloest"
        assert response.status_code == 200
        assert str(CLIENT_WRITE_REVOKED_MESSAGE) in response.content.decode()
        assert not WorkItem.objects.filter(title="Neu im Rennen").exists()
        assert AuditLog.objects.count() == vorher

    def test_offline_replay_bekommt_422(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        with mitschnitt() as m:
            m.nach_sperre("core_client", lambda: _widerrufen(client_identified, "papierkorb"))
            response = self._post(client, client_identified, HTTP_ACCEPT="application/json")
        assert m.offene_schranken() == [], "das Rennen wurde nicht ausgeloest"
        assert response.status_code == 422
        assert response.json()["errors"]["__all__"][0]["message"] == str(CLIENT_WRITE_REVOKED_MESSAGE)
        assert not WorkItem.objects.filter(title="Neu im Rennen").exists()


SAMMELDIENSTE = ["status", "prioritaet", "zuweisung"]


def _sammelaufruf(dienst, items, user, zustaendig):
    from core.services.case import bulk_assign_workitems, bulk_update_workitem_priority, bulk_update_workitem_status

    if dienst == "status":
        return bulk_update_workitem_status(items, user, WorkItem.Status.DONE)
    if dienst == "prioritaet":
        return bulk_update_workitem_priority(items, user, WorkItem.Priority.URGENT)
    return bulk_assign_workitems(items, user, zustaendig)


def _stand(items):
    return list(
        WorkItem.objects.filter(pk__in=[w.pk for w in items])
        .order_by("pk")
        .values_list("pk", "status", "priority", "assigned_to_id", "updated_at")
    )


def _stapel(facility, person, user):
    return [
        WorkItem.objects.create(facility=facility, client=person, created_by=user, title="Mit Person"),
        WorkItem.objects.create(facility=facility, created_by=user, title="Ohne Person"),
    ]


class TestSammeldienste:
    """Ein widerrufener oder geloeschter Teil weist den ganzen Stapel ab — kein Teilerfolg."""

    @pytest.mark.parametrize("art", WIDERRUFE)
    @pytest.mark.parametrize("dienst", SAMMELDIENSTE)
    def test_widerrufene_person_im_stapel(self, facility, client_identified, staff_user, lead_user, dienst, art):
        items = _stapel(facility, client_identified, staff_user)
        vorher = _stand(items)
        _widerrufen(client_identified, art)
        m = _assert_refused(lambda: _sammelaufruf(dienst, items, staff_user, lead_user), CLIENT_WRITE_REVOKED_MESSAGE)
        assert _stand(items) == vorher
        assert _person_vor_aufgabe(m)[:1] == ["core_client"]

    @pytest.mark.parametrize("dienst", SAMMELDIENSTE)
    def test_geloeschte_aufgabe_im_stapel(self, facility, client_identified, staff_user, lead_user, dienst):
        from core.services.case.workitems import WORKITEM_DELETED_MESSAGE

        items = _stapel(facility, client_identified, staff_user)
        WorkItem.objects.filter(pk=items[1].pk).update(is_deleted=True, deleted_at=timezone.now())
        vorher = _stand(items)
        m = _assert_refused(lambda: _sammelaufruf(dienst, items, staff_user, lead_user), WORKITEM_DELETED_MESSAGE)
        assert _stand(items) == vorher
        assert _person_vor_aufgabe(m) == ["core_client", "core_workitem"]


class TestSammelaktionUeberDieView:
    """Die Sammel-Views melden die Absage des Dienstes in der Inbox statt mit HTTP 500."""

    @pytest.mark.parametrize("htmx", [False, True], ids=["formular", "htmx"])
    @pytest.mark.parametrize(
        ("url", "daten"),
        [
            ("core:workitem_bulk_status", {"status": "done"}),
            ("core:workitem_bulk_priority", {"priority": "urgent"}),
            ("core:workitem_bulk_assign", {"assigned_to": ""}),
        ],
        ids=["status", "prioritaet", "zuweisung"],
    )
    def test_geloeschte_aufgabe_in_der_auswahl(
        self, client, facility, client_identified, staff_user, lead_user, url, daten, htmx
    ):
        from django.contrib.messages import get_messages

        from core.services.case.workitems import WORKITEM_DELETED_MESSAGE

        items = _stapel(facility, client_identified, staff_user)
        WorkItem.objects.filter(pk__in=[w.pk for w in items]).update(assigned_to=lead_user)
        WorkItem.objects.filter(pk=items[1].pk).update(is_deleted=True, deleted_at=timezone.now())
        client.force_login(lead_user)
        vorher = _stand(items)
        audit_vorher = AuditLog.objects.count()
        header = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        response = client.post(reverse(url), {"workitem_ids": [str(w.pk) for w in items], **daten}, **header)
        assert response.status_code == 302
        assert response.url.startswith(reverse("core:workitem_inbox"))
        if htmx:
            assert response["HX-Redirect"] == response.url
        meldungen = [str(msg) for msg in get_messages(response.wsgi_request)]
        assert meldungen == [str(WORKITEM_DELETED_MESSAGE)]
        assert _stand(items) == vorher
        assert AuditLog.objects.count() == audit_vorher


def _geladen(facility, user, *, person=None, title="Alt", **extra):
    """Eine Aufgabe, wie ein Formular sie laedt — danach aendert sich die Datenbank."""
    workitem = WorkItem.objects.create(facility=facility, client=person, created_by=user, title=title, **extra)
    return WorkItem.objects.get(pk=workitem.pk)


class TestBearbeiten:
    @pytest.mark.parametrize("art", WIDERRUFE)
    def test_widerrufene_person(self, facility, client_identified, staff_user, art):
        from core.services.case import update_workitem

        workitem = _geladen(facility, staff_user, person=client_identified)
        _widerrufen(client_identified, art)
        m = _assert_refused(lambda: update_workitem(workitem, staff_user, title="Neu"), CLIENT_WRITE_REVOKED_MESSAGE)
        assert WorkItem.objects.get(pk=workitem.pk).title == "Alt"
        assert _person_vor_aufgabe(m)[:1] == ["core_client"]

    @pytest.mark.parametrize("art", WIDERRUFE)
    def test_zuordnung_zu_widerrufener_person(self, facility, client_identified, staff_user, art):
        from core.services.case import update_workitem

        workitem = _geladen(facility, staff_user)
        _widerrufen(client_identified, art)
        m = _assert_refused(
            lambda: update_workitem(workitem, staff_user, client=client_identified, title="Neu"),
            CLIENT_WRITE_REVOKED_MESSAGE,
        )
        zeile = WorkItem.objects.get(pk=workitem.pk)
        assert (zeile.client_id, zeile.title) == (None, "Alt")
        assert _person_vor_aufgabe(m)[:1] == ["core_client"]

    @pytest.mark.parametrize("mit_person", [True, False], ids=["mit_person", "ohne_person"])
    def test_geloeschte_aufgabe(self, facility, client_identified, staff_user, mit_person):
        from core.services.case import update_workitem
        from core.services.case.workitems import WORKITEM_DELETED_MESSAGE

        workitem = _geladen(facility, staff_user, person=client_identified if mit_person else None)
        WorkItem.objects.filter(pk=workitem.pk).update(is_deleted=True, deleted_at=timezone.now())
        m = _assert_refused(lambda: update_workitem(workitem, staff_user, title="Neu"), WORKITEM_DELETED_MESSAGE)
        assert WorkItem.objects.get(pk=workitem.pk).title == "Alt"
        erwartet = ["core_client", "core_workitem"] if mit_person else ["core_workitem"]
        assert _person_vor_aufgabe(m) == erwartet

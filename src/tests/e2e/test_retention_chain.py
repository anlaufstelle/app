"""E2E-Test: Retention-Kette Vorschlag → Freigabe → ``enforce_retention`` → sichtbare Anonymisierung (Refs #2032).

Scope ist Teil (b) des Tickets. Teil (a) — der Konflikt-Resolver-Dialog — ist
seit Refs #1327/#1426 in ``test_offline_edit_conflict.py`` als vollstaendige
Browser-Journey abgedeckt und faellt hier weg.

Bis hierher endete ``test_retention_dashboard.py`` am „Freigegeben"-Badge, und
``test_statistics_snapshot.py`` fuhr zwar ``enforce_retention``, prueft aber nur
die Stabilitaet der Statistik-Zahlen. Die Kette dazwischen — dass eine Freigabe
im naechtlichen Lauf wirklich zu einer sichtbaren Redaktion fuehrt — hatte
niemand.

**Zwei Praezisierungen gegenueber dem Ticket-Text** (beide am Code verifiziert):

1. Es gibt **kein** Management-Command ``create_retention_proposals``. Vorschlaege
   entstehen ueber ``enforce_retention --propose`` →
   ``create_proposals_for_facility``.
2. Die „sichtbare Anonymisierung" ist **nicht** ein redigiertes Ereignis-Detail:
   ``get_visible_event_or_404`` filtert ``is_deleted=False``, die frueher
   erreichbare Detailseite liefert danach schlicht **404**. Sichtbar wird die
   Redaktion an der PERSON — ``anonymize_client`` setzt
   ``pseudonym = "Gelöscht-<pk8>"`` und ``is_active=False``. Die Personenliste
   filtert auf ``is_active=True`` (``views/clients.py``), die Person
   verschwindet dort also; ihre Akte antwortet seit der Vereinheitlichung der
   Lesepfade widerrufener Personen ebenfalls mit **404** (``is_active=True`` im
   Detail-Loader). Das neue Pseudonym prueft die Datenbank-Gegenprobe. Genau
   diese Beobachtungen sind der Nachweis.

**Warnung zum geteilten Lauf** (Muster ``test_statistics_snapshot.py``): Der
``enforce_retention``-Lauf ist installationsweit — das Command kennt keinen
Test-Scope. Dieser Test bringt deshalb ausschliesslich EIGENE Wegwerf-Objekte
mit (``E2E-RET-<uuid8>``, genau ein Ereignis) und trifft keine Annahme ueber
fremde Vorschlaege, Fristen oder Personen. Er hebt insbesondere keinen fremden
Legal Hold auf.
"""

import subprocess
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell, python_bin
from ._selectors import find_client_link

pytestmark = pytest.mark.e2e

#: Obergrenze fuer die beiden Management-Command-Laeufe. Ein haengender Lauf
#: soll fehlschlagen, nicht den globalen CI-Lock blockieren.
_COMMAND_TIMEOUT_S = 300


def _marker_values(zeilen, marker):
    """Die mit ``marker`` praefixierten Werte aus EINER ``django_shell``-Ausgabe.

    Bewusst auf bereits gelesenen Zeilen: ein Helfer, der je Marker erneut
    ``django_shell`` faehrt, wuerde schreibende Skripte mehrfach ausfuehren
    (erster Lauf dieser Datei ist genau daran gescheitert — die zweite
    Ausfuehrung lief in die Pseudonym-Eindeutigkeit).
    """
    return [line.removeprefix(marker) for line in zeilen if line.startswith(marker)]


def _einen_wert(zeilen, marker):
    treffer = _marker_values(zeilen, marker)
    assert len(treffer) == 1, f"Erwarte genau eine Zeile mit {marker!r}, sah: {zeilen}"
    return treffer[0]


def _run_enforce_retention(e2e_env, *args):
    """``manage.py enforce_retention`` als Subprozess gegen die E2E-Datenbank.

    Anders als ``test_statistics_snapshot.py`` (dort ``sys.executable``) laeuft
    der Aufruf ueber ``_helpers.python_bin()`` — die Repo-Konvention aus
    Refs #1829, die ``.venv/bin/python`` bevorzugt.
    """
    result = subprocess.run(
        [python_bin(), "src/manage.py", "enforce_retention", *args],
        capture_output=True,
        text=True,
        env=e2e_env,
        timeout=_COMMAND_TIMEOUT_S,
    )
    assert result.returncode == 0, f"enforce_retention {args} scheiterte:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _create_doomed_event(e2e_env, pseudonym):
    """Eigene Person mit GENAU EINEM ueberfaelligen Ereignis anlegen.

    ``contact_stage='identified'`` + ``occurred_at`` aelter als
    ``Settings.retention_identified_days`` trifft die ``identified``-Strategie
    (``core/retention/strategies.py``). Weil die Person nur dieses eine Ereignis
    hat, macht sie der Lauf danach zum Kandidaten fuer ``anonymize_clients``.
    Die Frist wird aus der Einrichtung GELESEN, nicht angenommen — ein
    veraenderter Seed-Wert soll den Test nicht still entkoppeln.
    """
    script = f"""
from datetime import timedelta
from django.utils import timezone
from core.models import Client, DocumentType, Event, Facility, User

facility = User.objects.get(username="admin").facility  # nicht first(): UUID-PK, Ordnung zufaellig (E2E-Harness)
settings_obj = facility.settings
doc_type = DocumentType.objects.filter(facility=facility, entity_type="event").order_by("pk").first()
actor = User.objects.get(username="admin")
client = Client.objects.create(
    facility=facility,
    contact_stage=Client.ContactStage.IDENTIFIED,
    pseudonym="{pseudonym}",
)
event = Event.objects.create(
    facility=facility,
    client=client,
    document_type=doc_type,
    created_by=actor,
    occurred_at=timezone.now() - timedelta(days=settings_obj.retention_identified_days + 30),
    data_json={{}},
)
print("E2E_CLIENT=" + str(client.pk))
print("E2E_EVENT=" + str(event.pk))
"""
    zeilen = django_shell(e2e_env, script)
    return _einen_wert(zeilen, "E2E_CLIENT="), _einen_wert(zeilen, "E2E_EVENT=")


def _proposal_for_event(e2e_env, event_pk):
    """PK und Status des Vorschlags zum eigenen Ereignis (leer, wenn keiner existiert)."""
    script = f"""
from core.models import RetentionProposal
proposal = RetentionProposal.objects.filter(target_type="Event", target_id="{event_pk}").first()
print("E2E_PROPOSAL=" + (str(proposal.pk) if proposal else ""))
print("E2E_STATUS=" + (proposal.status if proposal else ""))
"""
    zeilen = django_shell(e2e_env, script)
    return _einen_wert(zeilen, "E2E_PROPOSAL="), _einen_wert(zeilen, "E2E_STATUS=")


def _endzustand(e2e_env, client_pk, event_pk, proposal_pk):
    """Datenbank-Sicht nach dem Enforcement-Lauf als Dict."""
    script = f"""
from core.models import Client, Event, RetentionProposal
event = Event.objects.filter(pk="{event_pk}").first()
client = Client.objects.filter(pk="{client_pk}").first()
print("E2E_EVENT_DELETED=" + str(bool(event and event.is_deleted)))
print("E2E_PSEUDONYM=" + (client.pseudonym if client else ""))
print("E2E_CLIENT_ACTIVE=" + str(bool(client and client.is_active)))
print("E2E_PROPOSALS_LEFT=" + str(RetentionProposal.objects.filter(pk="{proposal_pk}").count()))
"""
    zeilen = django_shell(e2e_env, script)
    return {
        schluessel.rstrip("="): _einen_wert(zeilen, schluessel)
        for schluessel in ("E2E_EVENT_DELETED=", "E2E_PSEUDONYM=", "E2E_CLIENT_ACTIVE=", "E2E_PROPOSALS_LEFT=")
    }


class TestRetentionChain:
    """Vom Vorschlag bis zur sichtbaren Redaktion — in einem Durchgang."""

    def test_approved_proposal_leads_to_visible_anonymization(self, authenticated_page, lead_page, base_url, e2e_env):
        """Zwei Freigaben im Dashboard → ``enforce_retention`` → 404 + „Gelöscht-"-Pseudonym."""
        page = authenticated_page
        pseudonym = f"E2E-RET-{uuid.uuid4().hex[:8]}"
        client_pk, event_pk = _create_doomed_event(e2e_env, pseudonym)

        # --- Ausgangslage sichtbar machen: Person in der Liste, Ereignis erreichbar.
        page.goto(f"{base_url}/clients/?q={pseudonym}", wait_until="domcontentloaded")
        expect(find_client_link(page, pseudonym)).to_be_visible(timeout=10000)

        antwort = page.goto(f"{base_url}/events/{event_pk}/", wait_until="domcontentloaded")
        assert antwort is not None and antwort.status == 200, (
            f"Ereignis-Detail sollte vor dem Lauf erreichbar sein, war: {antwort and antwort.status}"
        )

        # --- Vorschlag ueber den echten Pfad erzeugen (kein Direkt-ORM-Insert).
        _run_enforce_retention(e2e_env, "--propose")
        proposal_pk, status = _proposal_for_event(e2e_env, event_pk)
        assert proposal_pk, "enforce_retention --propose hat fuer das eigene Ereignis keinen Vorschlag erzeugt"
        assert status == "pending", f"Frischer Vorschlag sollte 'pending' sein, ist: {status!r}"

        # --- Freigabe ueber die Oberflaeche (nur die EIGENE Karte).
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")
        karte = page.locator(f"#proposal-{proposal_pk}")
        expect(karte).to_be_visible(timeout=10000)
        expect(karte.locator("span:has-text('Ausstehend')")).to_have_count(1, timeout=10000)
        karte.get_by_role("button", name="Erste Freigabe erteilen").click()
        # Seit der Zwei-Personen-Regel genuegt eine Freigabe nicht: Karte bleibt
        # ausstehend, und der Lauf loescht noch nichts.
        expect(karte.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)
        expect(karte.locator("span:has-text('Freigegeben')")).to_have_count(0)

        # --- Zweite, andere berechtigte Person (Leitung) gibt endgueltig frei.
        lead_page.on("dialog", lambda dialog: dialog.accept())
        lead_page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")
        karte_leitung = lead_page.locator(f"#proposal-{proposal_pk}")
        expect(karte_leitung.get_by_text("1 von 2 Freigaben")).to_be_visible(timeout=10000)
        karte_leitung.get_by_role("button", name="Zweite Freigabe erteilen").click()
        expect(karte_leitung.locator("span:has-text('Freigegeben')")).to_be_visible(timeout=10000)

        # --- Der naechtliche Lauf.
        _run_enforce_retention(e2e_env)

        # --- 1. Nachweis: das Ereignis ist aus der Oberflaeche verschwunden (404).
        antwort = page.goto(f"{base_url}/events/{event_pk}/", wait_until="domcontentloaded")
        assert antwort is not None and antwort.status == 404, (
            f"Getilgtes Ereignis muss 404 liefern, war: {antwort and antwort.status}"
        )

        # --- 2. Nachweis: die anonymisierte Person ist inaktiv; ihre Akte antwortet
        #        seit der Vereinheitlichung der Lesepfade widerrufener Personen mit
        #        404 statt mit dem neuen Pseudonym (das Pseudonym prueft Nachweis 4
        #        an der Datenbank).
        antwort = page.goto(f"{base_url}/clients/{client_pk}/", wait_until="domcontentloaded")
        assert antwort is not None and antwort.status == 404, (
            f"Akte einer anonymisierten Person muss 404 liefern, war: {antwort and antwort.status}"
        )

        # --- 3. Nachweis: unter dem alten Pseudonym ist sie nicht mehr auffindbar.
        page.goto(f"{base_url}/clients/?q={pseudonym}", wait_until="domcontentloaded")
        expect(find_client_link(page, pseudonym)).to_have_count(0, timeout=10000)

        # --- 4. Datenbank-Gegenprobe inkl. Endzustand des Vorschlags: die
        #        freigegebene Zeile wird nach dem Tilgen aufgeraeumt
        #        (``_soft_delete_events`` loescht APPROVED-Vorschlaege).
        zustand = _endzustand(e2e_env, client_pk, event_pk, proposal_pk)
        assert zustand["E2E_EVENT_DELETED"] == "True", "Ereignis wurde nicht soft-geloescht"
        assert zustand["E2E_PSEUDONYM"].startswith("Gelöscht-"), (
            f"Pseudonym wurde nicht redigiert: {zustand['E2E_PSEUDONYM']!r}"
        )
        assert zustand["E2E_CLIENT_ACTIVE"] == "False", "Anonymisierte Person muss inaktiv sein"
        assert zustand["E2E_PROPOSALS_LEFT"] == "0", "Freigegebener Vorschlag muss nach dem Lauf aufgeraeumt sein"

        # --- 5. …und die Karte ist im Dashboard weg.
        page.goto(f"{base_url}/retention/", wait_until="domcontentloaded")
        expect(page.locator(f"#proposal-{proposal_pk}")).to_have_count(0, timeout=10000)

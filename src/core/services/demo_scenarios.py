"""Facility-/Konto-Auflösung für die Demo-Bereichsauswahl (Refs #2285).

Ausgelagert aus ``core.views.demo_scenarios`` (Fix-Runde 1, beide
Zweitgutachten BLOCKER): ``TestTenantModelScopeGuard`` verlangt für
``core/views/**`` einen erklärten Facility-Scope an jedem
``<Model>.objects``-Zugriff auf ein facility-tragendes Modell — der
Einrichtungs-Lookup hier hat strukturell KEINEN Scope, weil er selbst die
Facility erst auflöst (pre-auth, kein ``request.current_facility``,
identisch zur bereits dokumentierten Ausnahmeklasse „Pre-Auth-
Identitätslookups" in ``_VIEWS_ALLOWLIST``/``_SERVICES_ALLOWLIST`` von
``test_architecture_guards_models.py``, z. B.
``security/lockout_recovery.py:verify_recovery_token:User``).

Der Services-Layer ist laut Guard-Doktrin der vorgesehene Ort für genau
diese Klasse von Zugriff (vgl. ``compliance/app_superuser.py``,
deploymentweiter User-Zugriff ohne Facility-Bezug) — die View bleibt damit
frei von direktem Tenant-Modellzugriff. Beide Funktionen bekommen trotzdem
einen begründeten Eintrag in ``_SERVICES_ALLOWLIST`` (der Guard schützt den
Services-Layer identisch zum View-Layer, das Verschieben allein hebt die
Pflicht zur Begründung nicht auf).

``facility_for_scenario``/``account_for`` bekommen NIE Werte aus
Request-Parametern — der Name kommt ausschließlich aus dem serverseitigen
Szenariokatalog (``core.seed.scenarios``, Lane F-2/#2279), Rolle und
Facility-Objekt sind zum Aufrufzeitpunkt bereits serverseitig validiert.
"""

from __future__ import annotations

from core.models import Facility, User


def facility_for_scenario(scenario) -> tuple[Facility | None, bool]:
    """``(facility, available)`` für ein Szenario aus dem Katalog.

    ``available`` deckt sowohl „noch nicht geseedet"/"Reset läuft gerade"
    (kein passender Facility-Datensatz) als auch „archiviert"
    (``is_active=False``) ab — beide Zustände bekommen auf der
    Auswahlseite dieselbe, verständliche Behandlung (kein stiller Einstieg
    in einen nicht bereitgestellten Bereich, Ticket-Punkt 3 #2285).
    """
    try:
        facility = Facility.objects.get(name=scenario.facility_name)
    except Facility.DoesNotExist:
        return None, False
    return facility, facility.is_active


def account_for(facility: Facility, role: str) -> User | None:
    """Deterministisches, fest zugeordnetes Demo-Konto für Facility+Rolle.

    ``order_by("pk")`` macht die Auswahl deterministisch, wenn ein
    Team-Szenario mehrere Konten derselben Rolle seedet (z. B. zwei
    ``staff``-Konten) — stabil über die Seed-Erstellungsreihenfolge.

    ``.exclude(is_superuser=True)`` (Zweitgutachten Opus, Hinweis H-2):
    Verteidigung in der Tiefe. Der Seed setzt ``is_superuser`` bewusst nie
    (Refs #1271), der installationsweite ``super_admin`` trägt zusätzlich
    ``facility=None`` und wird vom ``facility=facility``-Filter bereits
    strukturell nie getroffen — der explizite Ausschluss macht das
    zusätzlich unabhängig von dieser zweiten Eigenschaft robust.
    """
    return (
        User.objects.filter(facility=facility, role=role, is_active=True)
        .exclude(is_superuser=True)
        .order_by("pk")
        .first()
    )

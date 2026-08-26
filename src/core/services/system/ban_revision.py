"""Write-Boundaries und Poll-Projektion der Hausverbots-Revision (Refs #1917).

Gegenstück zu :class:`core.models.ban_revision.BanRevision`. Zwei Seiten:

**Schreibseite** — die Bump-Helfer laufen in derselben DB-Transaktion wie der
fachliche Write (Event-CRUD, Retention, Dokumenttyp-Konfiguration,
Personen-Pfade). Nur so ist ausgeschlossen, dass ein Poll eine neue Revision
vor den zugehörigen Daten sieht: das Follow-up würde sonst alten Inhalt mit
frischem Validator ausliefern und der Client bliebe dauerhaft stale. Die
harmlose Gegenrichtung (Daten committed, Poll las noch die alte Revision)
konvergiert im nächsten Poll-Zyklus.

**Leseseite** — :func:`ban_revision_projection` ist die Projektion der
Watch-Komponente ``"bans"`` (:mod:`core.services.zeitstrom_watch`): eine
einzige Single-Row-SELECT über den Unique-Index auf ``facility_id``, ohne
``core_event``-Zugriff und ohne Entschlüsselung. Sie liefert nur das
Zähler-**Präfix** bis zum Rollenrang — Bumps in höheren Strata sind für
niedrigere Rollen funktional unbeobachtbar (kein Validator-, ETag- oder
Arbeitsunterschied). Zusätzlich enthält die Projektion ``localdate``: ein
Hausverbot mit ``bis < heute`` fällt ohne jeden Write aus dem Banner; der
Datumsanteil kippt den Validator um Mitternacht für alle Rollen gleichermaßen
und datenunabhängig (auch bei 0 Hausverboten) — uniform und damit kanalfrei.
"""

from __future__ import annotations

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from core.models import BanRevision, DocumentType
from core.services.compliance.sensitivity import ROLE_MAX_SENSITIVITY, SENSITIVITY_RANK

#: Spalten je Sichtbarkeits-Stratum; Index == Rollenrang aus ROLE_MAX_SENSITIVITY.
BAN_REVISION_STRATA = ("revision_normal", "revision_elevated", "revision_high")

# Refs #1991 (bewusste Abweichung): Dieses Modul benutzt weiter
# ``SENSITIVITY_RANK.get(..., 0)`` statt der fail-closed
# ``sensitivity_rank``. Der Rang ist hier kein Lese-Gate, sondern ein INDEX in
# ``BAN_REVISION_STRATA`` — der Ersatzrang fuer Unbekanntes laege ausserhalb
# des Tupels (IndexError). Stratum 0 ist zugleich die konservative Richtung:
# es bumpt den Zaehler, den JEDE Rolle im Praefix liest, also refresht bei
# einem unbekannten Wert lieber jeder zu viel als einer zu wenig. Verborgen
# wird dadurch nichts — was eine Rolle sehen darf, entscheidet weiterhin das
# Gate in ``core.services.system.bans``.


def bump_ban_revision(facility, ranks) -> None:
    """Erhöht die Zähler der Strata *ranks* (Rollenränge 0/1/2) atomar.

    ``F()``-Inkrement + Row-Lock serialisieren konkurrierende Writes (monoton,
    kein Lost Update). Die Zeile entsteht lazy: UPDATE-first, INSERT im
    Savepoint; verliert der INSERT das Rennen (``IntegrityError`` am
    OneToOne-Unique), gewinnt der Retry-UPDATE — die äußere Transaktion des
    fachlichen Writes überlebt den Konflikt.
    """
    fields = sorted({BAN_REVISION_STRATA[rank] for rank in ranks})
    if not fields:
        return
    increments = {name: models.F(name) + 1 for name in fields}
    if BanRevision.objects.for_facility(facility).update(**increments):
        return
    try:
        with transaction.atomic():
            BanRevision.objects.create(facility=facility, **{name: 1 for name in fields})
    except IntegrityError:
        BanRevision.objects.for_facility(facility).update(**increments)


def ban_revision_projection(facility, user):
    """Rollen-sichtbares Zähler-Präfix + lokales Datum — feste kleine Arbeit."""
    rank = ROLE_MAX_SENSITIVITY.get(user.role, 0)
    row = (BanRevision.objects.for_facility(facility).values_list(*BAN_REVISION_STRATA).first()) or (0, 0, 0)
    return (rank, tuple(row[: rank + 1]), str(timezone.localdate()))


def _banner_projection(data_json):
    """Banner-Sicht eines einzelnen Ban-Events: ``(aktiv, bis, grund)``.

    Delegiert an :func:`core.services.system.bans._project_ban_data` — exakt
    die Logik, mit der das autoritative Banner rendert (``safe_decrypt``
    verkraftet Klartext wie gespeicherte Fernet-Token gleichermaßen; auf dem
    Schreibpfad ist die Entschlüsselung einzelner Felder EINES Events erlaubt,
    der Poll-Pfad bleibt entschlüsselungsfrei). Inaktive Bans projizieren auf
    ``(False, None, "")`` — Edits an durchgehend schlafenden Hausverboten
    erzeugen dadurch kein Delta und bleiben für alle Rollen unbeobachtbar.
    """
    from core.services.system.bans import _project_ban_data

    return _project_ban_data(data_json or {}, today=timezone.localdate(), may_see_grund=True)


def bump_ban_revision_for_event_change(facility, document_type, data_before, data_after) -> None:
    """Write-Boundary für Event-Create/-Update/-Soft-Delete (Refs #1917).

    - Existenz-Delta (Aktiv-Status oder Bis-Datum der Banner-Sicht ändert
      sich) → Stratum 0: das Banner zeigt diese Fakten allen Rollen.
    - Grund-Delta → Stratum des Dokumenttyp-Rangs: exakt die Gate-Logik von
      ``_user_may_see_grund`` (Doc-Type-Sensitivity, Feld-Override irrelevant).
    - Kein Delta → kein Bump: Änderungen banner-fremder Felder eines
      Ban-Events dürfen keinen Refresh und keinen Beobachtungsunterschied
      erzeugen (Seitenkanal-Auflage).
    """
    if document_type.system_type != DocumentType.SystemType.BAN:
        return
    before = _banner_projection(data_before)
    after = _banner_projection(data_after)
    ranks = []
    if before[:2] != after[:2]:
        ranks.append(0)
    if before[2] != after[2]:
        # Default 0 statt fail-closed ``sensitivity_rank`` — Begruendung bei
        # BAN_REVISION_STRATA oben (Index, kein Lese-Gate; Refs #1991).
        ranks.append(SENSITIVITY_RANK.get(document_type.sensitivity, 0))
    bump_ban_revision(facility, ranks)


def bump_ban_revision_for_document_type_change(document_type, changed_fields, *, previous_sensitivity) -> None:
    """Write-Boundary für Konfigurations-Updates eines Ban-Dokumenttyps.

    - ``is_active`` gated die Banner-Query komplett → Stratum 0.
    - Ein Sensitivity-Wechsel kippt die Grund-Sichtbarkeit exakt für die
      Ränge zwischen alt und neu → Stratum ``min(rank_alt, rank_neu)`` (der
      niedrigste betroffene Rang); Rollen darunter sehen keine Differenz.
    """
    if document_type.system_type != DocumentType.SystemType.BAN:
        return
    ranks = []
    if "is_active" in changed_fields:
        ranks.append(0)
    if "sensitivity" in changed_fields:
        # Default 0 statt fail-closed ``sensitivity_rank`` — Begruendung bei
        # BAN_REVISION_STRATA oben (Index, kein Lese-Gate; Refs #1991).
        old_rank = SENSITIVITY_RANK.get(previous_sensitivity, 0)
        new_rank = SENSITIVITY_RANK.get(document_type.sensitivity, 0)
        if old_rank != new_rank:
            ranks.append(min(old_rank, new_rank))
    bump_ban_revision(document_type.facility, ranks)


def client_has_ban_banner_entries(client) -> bool:
    """Steht die Person JETZT im Hausverbots-Banner?

    Nutzt das autoritative :func:`core.services.system.bans.
    get_active_bans_for_client` mit ``user=None`` (Grund bleibt redigiert und
    unentschlüsselt; nur ``aktiv``/``bis`` der Ban-Events EINER Person werden
    auf dem Schreibpfad projiziert — nie im Poll). Ein Roh-EXISTS auf
    Ban-Events wäre ein Trigger-Seitenkanal: die sichtbare Umbenennung einer
    Person mit ausschließlich schlafenden/abgelaufenen Bans (oder deaktiviertem
    Ban-Typ) änderte das Banner für keine Rolle, der No-op-Refresh verriete
    aber „Person hat verborgene Ban-Events".
    """
    from core.services.system.bans import get_active_bans_for_client

    return bool(get_active_bans_for_client(client, user=None))


def bump_ban_revision_for_client_change(client, *, had_banner_entries=None) -> None:
    """Write-Boundary für Personen-Pfade (Pseudonym-Änderung, Anonymisierung).

    Bumpt Stratum 0 nur bei echter Banner-Wirkung (die Person steht im
    Banner) — kanalfrei, weil Pseudonym und Banner-Eintrag Rang-0-sichtbare
    Fakten sind. ``had_banner_entries`` erlaubt Aufrufern, deren Write die
    Banner-Wirkung selbst zerstört (Anonymisierungs-Kaskade leert die
    Event-``data_json``), den Zustand VOR dem Write zu übergeben.
    """
    if had_banner_entries is None:
        had_banner_entries = client_has_ban_banner_entries(client)
    if had_banner_entries:
        bump_ban_revision(client.facility, [0])

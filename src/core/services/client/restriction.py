"""Art.-18-Sperrvermerk — Einschraenkung der Verarbeitung (Refs #2045).

**Art. 18 ist nicht der Legal Hold.** Der Hold blockiert die *Loeschung*
(Art. 17 Abs. 3), dieser Vermerk blockiert die *Nutzung*: nach Art. 18 Abs. 2
duerfen eingeschraenkte Daten weiterhin **gespeichert** werden, aber — von
engen Ausnahmen abgesehen — nicht mehr verarbeitet. Die Umsetzung ist deshalb
ein Schreibgate, kein Loeschgate, und es ist bewusst als Spiegel von
:func:`~core.services.client.main.resolve_writable_client` (Refs #1771) gebaut:
dieselbe Doppelung Service-Guard + Formular-Resolver, damit auch
Nicht-Formular-Aufrufer (Offline-Replay, CSV-Import, kuenftige Commands) die
Grenze passieren muessen.

Die **eine** Ausnahme ist der Berichtigungsworkflow (ADR-041): Art. 18 Abs. 1
lit. a knuepft die Einschraenkung gerade an das Bestreiten der Richtigkeit, und
Art. 16 ist der Weg, auf dem dieses Bestreiten geklaert wird. Ein Gate, das
auch die Berichtigung blockierte, machte die Einschraenkung zur Sackgasse. Der
Ausstieg ist ein **modul-privater Kontext** (:func:`_rectification_write`), den
ausschliesslich ``_apply_rectification`` oeffnet — bewusst kein Argument der
generischen Schreib-Services, das jeder Aufrufer setzen koennte.

**Aufhebung.** Art. 18 Abs. 3 verlangt, dass die betroffene Person vor dem
Aufheben unterrichtet wird — die Aufhebung ist damit keine Nebenlaeufigkeit
einer Bearbeitung, sondern ein eigener, belegter Vorgang. Sie ist deshalb
vier-aeugig (eine andere Fachkraft als die setzende) **oder** durch die Leitung
moeglich, und in beiden Faellen auditiert.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import AuditLog, Client
from core.services.audit import audit_client_event

#: Ablehnungsgrund fuer inhaltliche Schreibzugriffe auf eine eingeschraenkte
#: Person. Getrennt von ``CLIENT_WRITE_REVOKED_MESSAGE`` gehalten, weil der
#: Zustand hier — anders als Papierkorb/Anonymisierung — **reversibel** ist und
#: die Meldung deshalb den Weg hinaus nennen muss.
PROCESSING_RESTRICTED_MESSAGE = _(
    "Für diese Person ist die Verarbeitung nach Art. 18 DSGVO eingeschränkt — "
    "die Daten bleiben gespeichert, dürfen aber außerhalb des "
    "Berichtigungsverfahrens nicht geändert oder weiterverwendet werden. "
    "Aufheben kann das die Leitung oder eine zweite berechtigte Person."
)

#: Fields, die :func:`restrict_processing` und :func:`lift_processing_restriction`
#: schreiben — als SSOT fuer beide ``update_fields``-Listen.
_RESTRICTION_FIELDS = (
    "processing_restricted",
    "processing_restriction_reason",
    "processing_restricted_at",
    "processing_restricted_by",
)


#: Der Berichtigungs-Ausstieg aus dem Gate — **kein** Parameter des generischen
#: Client-Service (Cross-Review-Befund 4). Ein oeffentliches
#: ``allow_restricted=True`` waere ein Generalschluessel gewesen: jeder interne
#: Aufrufer haette das Gate vollstaendig abschalten koennen, und der Vertrag
#: „nur Berichtigungs-Flow" haette technisch nichts bedeutet. Stattdessen ein
#: modul-privater Kontext, den ausschliesslich
#: :func:`core.services.client.rectification._apply_rectification` oeffnet; der
#: Quelltext-Guard ``test_restriction_bypass_is_module_private`` pinnt, dass
#: keine weitere Stelle ihn referenziert.
_ACTIVE_WORKFLOW: ContextVar[str | None] = ContextVar(
    "anlaufstelle_restriction_exempt_workflow",
    default=None,
)


@contextmanager
def _workflow_write(name: str) -> Iterator[None]:
    """Oeffnet einen Betroffenenrechte-Workflow fuer die Dauer EINES Blocks.

    Bewusst ein Kontext und kein Argument: der Ausstieg gilt fuer den
    umschlossenen Block, nicht fuer einen beliebigen Aufrufer, und er raeumt
    sich im ``finally`` selbst wieder ab. ``ContextVar`` (statt eines
    Modul-Globals) haelt ihn thread- und async-lokal — zwei gleichzeitige
    Requests koennen sich den Ausstieg nicht gegenseitig unterschieben.

    Der Name wandert mit, damit ein offener Kontext im Debugging benennbar ist
    und der Quelltext-Guard je Workflow pinnen kann, wer ihn oeffnen darf.
    """
    token = _ACTIVE_WORKFLOW.set(name)
    try:
        yield
    finally:
        _ACTIVE_WORKFLOW.reset(token)


@contextmanager
def _rectification_write() -> Iterator[None]:
    """Art.-16-Ausstieg — geoeffnet ausschliesslich von ``_apply_rectification``."""
    with _workflow_write("rectification"):
        yield


@contextmanager
def _deletion_workflow_write() -> Iterator[None]:
    """Art.-17-Ausstieg fuer den GEPRUEFTEN Vier-Augen-Loeschweg.

    Die destruktiven Services (``soft_delete_event``, ``soft_delete_case``,
    ``purge_attachment_chain``) gaten seit dem Re-Review unter ihrer eigenen
    Zeilensperre — der Guard sitzt also im Service, nicht mehr ungesperrt in
    der View davor (TOCTOU-Befund). Damit braucht der ERLAUBTE Weg einen
    benannten Ausstieg: die Genehmigung eines Vier-Augen-Loeschantrags ruft
    dieselben Services.

    Die Abgrenzung bleibt die des Orchestrator-Rulings: Art. 17 gilt waehrend
    der Einschraenkung weiter, die Einzelentscheidung ohne zweite Person nicht.
    """
    with _workflow_write("deletion_request"):
        yield


def assert_processing_allowed(client: Any, *, lock: bool = False) -> None:
    """Wirft ``ValidationError``, wenn an *client* eingeschraenkt verarbeitet wird.

    Das gemeinsame Gate ALLER fachlichen Schreibpfade. Erlaubt ist der Aufruf
    aus dem Berichtigungs-Kontext (:func:`_rectification_write`).

    **Fail-closed gegen den frisch gelesenen Stand** (Cross-Review-Befund 3):
    Geprueft wird NICHT das mitgegebene Instanz-Attribut — das stammt
    regelmaessig aus einem Formular-GET von vor Minuten und weiss von einer
    zwischenzeitlich gesetzten Einschraenkung nichts. Gelesen wird stattdessen
    die eine Spalte direkt aus der Datenbank.

    ``lock=True`` liest sie unter ``select_for_update``. Das ist der
    Unterschied zwischen „war beim Pruefen nicht eingeschraenkt" und „kann bis
    zum Commit nicht eingeschraenkt werden": die Zeilensperre haelt bis zum
    Ende der umschliessenden Transaktion, ein paralleles
    :func:`restrict_processing` wartet also, statt sich zwischen Pruefung und
    Write zu draengen. Jeder schreibende Service nutzt ``lock=True``; die
    View-seitigen Vorabpruefungen (freundliche Meldung statt 500) begnuegen
    sich mit dem ungesperrten Lesen, weil hinter ihnen ohnehin das gesperrte
    Gate des Service steht.

    *client* darf eine ``Client``-Instanz, eine PK oder ``None`` sein —
    ``None`` (anonymes Ereignis, Aufgabe ohne Personenbezug) ist kein
    Personenbezug und damit nie eingeschraenkt.
    """
    if _ACTIVE_WORKFLOW.get() is not None:
        return
    if _restricted_flag(client, lock=lock):
        raise ValidationError(PROCESSING_RESTRICTED_MESSAGE)


def _restricted_flag(client: Any, *, lock: bool = False) -> bool:
    """Liest die eine Spalte frisch aus der Datenbank (siehe Aufrufer)."""
    client_id = getattr(client, "pk", client)
    if client_id is None:
        return False
    rows = Client.objects.filter(pk=client_id)
    if lock:
        rows = rows.select_for_update()
    return bool(rows.values_list("processing_restricted", flat=True).first())


def is_processing_restricted(client: Any) -> bool:
    """Praedikat fuer ANZEIGE-Zwecke — nie als Gate verwenden.

    Bestaetigungsseiten sollen den Blocker nennen, bevor jemand den roten
    Knopf sucht (Muster ``owner_has_active_hold``). Die verbindliche Grenze
    zieht immer :func:`assert_processing_allowed` im Schreibpfad.
    """
    return _restricted_flag(client)


def _require_staff(user: Any, message: Any) -> None:
    """Rollen-Guard im SERVICE (nicht nur in der View) — Muster ADR-041."""
    if not getattr(user, "is_staff_or_above", False):
        raise ValidationError(message)


@transaction.atomic
def restrict_processing(client: Client, user: Any, *, reason: str) -> Client:
    """Setzt den Sperrvermerk (idempotent).

    **Idempotent statt Fehler:** der Auto-Vorschlag am Berichtigungsantrag darf
    nicht daran scheitern, dass jemand die Einschraenkung schon von Hand gesetzt
    hat — das Ziel ist identisch (Muster ``request_deletion``, Refs #1834). Eine
    zweite Audit-Zeile entstuende dabei ohne neuen Sachverhalt und verwaesserte
    den Nachweis, wann die Einschraenkung tatsaechlich begann; der frueheste
    Zeitpunkt ist der rechtlich relevante.
    """
    _require_staff(user, _("Nur Fachkräfte dürfen die Verarbeitung einschränken."))
    if reason not in Client.RestrictionReason.values:
        raise ValidationError(_("Ungültiger Grund für die Einschränkung der Verarbeitung."))
    # Cross-Review-Befund 3: Zustand unter der Zeilensperre lesen, nicht am
    # (moeglicherweise Minuten alten) Formular-Objekt. Ohne den Lock setzen
    # zwei gleichzeitige Vorgaenge beide, ueberschreiben gegenseitig
    # Provenienz und Zeitpunkt und schreiben ZWEI Audit-Zeilen — die
    # dokumentierte Idempotenz waere eine blosse Absichtserklaerung.
    client = Client.objects.select_for_update().get(pk=client.pk)
    if client.processing_restricted:
        return client

    client.processing_restricted = True
    client.processing_restriction_reason = reason
    client.processing_restricted_at = timezone.now()
    client.processing_restricted_by = user
    # ``update_fields`` haelt den Schreibvorgang auf die Sperr-Spalten begrenzt:
    # ein volles ``save()`` liefe durch ``_encrypt_sensitive_fields`` und
    # schriebe die uebrigen (moeglicherweise stale geladenen) Felder mit —
    # genau das, was Art. 18 hier verbietet.
    client.save(update_fields=[*_RESTRICTION_FIELDS, "updated_at"])

    audit_client_event(
        client,
        user,
        AuditLog.Action.PROCESSING_RESTRICTED,
        # Nur der Rechtsgrund (lit. a–d), nie der strittige Sachverhalt — der
        # lebt verschluesselt am Berichtigungsantrag (Muster #1093).
        reason=reason,
    )
    return client


@transaction.atomic
def lift_processing_restriction(client: Client, user: Any) -> Client:
    """Hebt den Sperrvermerk auf — vier-aeugig ODER durch die Leitung.

    Guard-Reihenfolge (jeder Schritt einzeln getestet):

    1. **Rolle** — Fachkraft und hoeher; unter ``staff`` gibt es keinen
       Schreibpfad an der Person, also auch keinen an ihrer Einschraenkung.
    2. **Zustand** — ohne gesetzten Vermerk gibt es nichts aufzuheben; ein
       stiller No-op erzeugte sonst eine Audit-Zeile ohne Vorgang.
    3. **Vier-Augen** — wer eingeschraenkt hat, hebt nicht allein wieder auf.
       Die Leitung ist davon ausgenommen: sie ist im Rollenmodell die Instanz,
       die Betroffenenrechte-Entscheidungen allein verantworten darf (Muster
       ``update_client``-Herabstufung, ``_guard_reviewable``).
    """
    _require_staff(user, _("Nur Fachkräfte dürfen die Einschränkung aufheben."))
    # Cross-Review-Befund 3: siehe :func:`restrict_processing`. Zusaetzlich
    # haengt hier die Vier-Augen-Regel am frisch gelesenen
    # ``processing_restricted_by`` — auf einem stalen Objekt entschiede sie
    # anhand einer laengst ueberholten Provenienz.
    client = Client.objects.select_for_update().get(pk=client.pk)
    if not client.processing_restricted:
        raise ValidationError(_("Für diese Person ist die Verarbeitung nicht eingeschränkt."))
    if not user.is_lead_or_admin and client.processing_restricted_by_id == user.pk:
        raise ValidationError(
            _(
                "Die Einschränkung kann nicht von derselben Person aufgehoben werden, "
                "die sie gesetzt hat (Vier-Augen-Prinzip) — bitte die Leitung oder eine "
                "zweite berechtigte Person hinzuziehen."
            )
        )

    previous_reason = client.processing_restriction_reason
    client.processing_restricted = False
    client.processing_restriction_reason = ""
    client.processing_restricted_at = None
    client.processing_restricted_by = None
    client.save(update_fields=[*_RESTRICTION_FIELDS, "updated_at"])

    audit_client_event(
        client,
        user,
        AuditLog.Action.PROCESSING_RESTRICTION_LIFTED,
        reason=previous_reason,
    )
    return client

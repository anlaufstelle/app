"""Speicherkontingent je Einrichtung (Refs #2129, Befund N-5).

Warum ueberhaupt
================

Die Groessenbegrenzung des File-Vaults galt bis hierher ausschliesslich **pro
Datei** (:func:`~core.services.file_vault.policy.enforce_upload_size`:
``min(Settings.max_file_size_mb, FILE_VAULT_MAX_UPLOAD_BYTES)``). Eine Summe je
Einrichtung wurde nirgends gebildet. Betriebsseitig gab es nur den globalen
Disk-Check in ``/health/`` (``HEALTH_DISK_REQUIRED``) — der **meldet**, wenn
die Platte volllaeuft, verhindert nichts und kennt keine Einrichtung.

Im Mehr-Mandanten-Betrieb (eine Instanz, mehrere Einrichtungen — das
RLS-Zielbild) konnte damit **eine** Einrichtung mit lauter legitimen 10-MB-
Uploads den gemeinsamen ``MEDIA_ROOT`` fuellen; die Folge (Upload-Fehler,
Backup-Fehler, ggf. Postgres-WAL auf gemeinsamem Volume) traf **alle**
Mandanten. Verfuegbarkeits- und Isolationsthema nach Art. 32 DSGVO, kein
Vertraulichkeitsleck.

Ein Rechner fuer zwei Fragen
============================

:func:`facility_storage_usage` beantwortet die Durchsetzungsfrage („passt diese
Datei noch?") **und** die Anzeigefrage („wie voll ist die Einrichtung?"). Das
ist Absicht: eine Kachel, die eine andere Zahl nennt als die Regel dahinter,
ist schlimmer als keine Kachel (dieselbe Regel wie in
:mod:`core.services.hubs`).

Was zaehlt
==========

**Alle** ``Attachment``-Zeilen der Einrichtung, **inklusive Papierkorb** und
inklusive abgeloester Vorversionen: soft-geloescht heisst nur „aus der Sicht
der Fachkraft entfernt", die ``.enc`` liegt bis zur physischen Tilgung
(Owner-Loeschung/Anonymisierung bzw. ``purge_attachment_chain``) weiter auf der
Platte und belegt Platz. Die Anzeige weist den Papierkorb-Anteil deshalb
gesondert aus — er ist der Hebel, mit dem eine volle Einrichtung wieder Luft
bekommt. Die **Anzeige** aus :func:`facility_storage_usage` bleibt
Anhang-basiert. Offene ``ImportJob``-Quelldateien zaehlen bei der
**Durchsetzung** mit, bis der Lauf ``COMMITTED`` oder ``ABORTED`` ist; sie
sind temporaer und haengen an einem eigenen Modell. Beide Speicherpfade der
Einrichtung — Anhang und CSV-Import — rechnen dafuer ueber DENSELBEN Zaehler
(:func:`_enforcement_totals`, Refs #2259): zaehlte jeder nur seine eigene
Sorte, liessen sich beide Kontingente nebeneinander ausschoepfen.

Zwei Stufen, zwei Aufgaben
==========================

Die Durchsetzung haengt an zwei Stellen (Refs #2178):

1. :func:`~core.services.file_vault.policy.enforce_storage_quota` **vor** dem
   Virenscan — lockfrei, damit fuer einen ohnehin abgelehnten Upload kein
   synchroner ClamAV-Lauf und keine Verschluesselung anfaellt. Diese Stufe
   **spart Arbeit**, sie entscheidet nicht endgueltig: zwischen ihr und der
   Attachment-Zeile liegt die gesamte teure Pipeline.
2. :func:`facility_storage_overrun` im Commit — unter der Sperre der
   Einrichtung und unmittelbar vor dem Insert. Diese Stufe **entscheidet**:
   ohne sie ueberziehen gleichzeitige Uploads das Kontingent gemeinsam um
   ``Nebenlaeufigkeit x max_file_size_mb`` (dynamisch belegt, Red-Team B-13).
   Serialisiert wird nur der kurze DB-Teil — Scan und Verschluesselung laufen
   weiterhin ohne jede Sperre.

Der CSV-Import haengt seit Refs #2259 an genau denselben zwei Stufen und an
derselben Sperre: sein ``job.save()`` laeuft im Transaktionsrahmen hinter
:func:`~core.services.file_vault.policy.check_import_storage_quota_at_commit`.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings as django_settings
from django.db import connection
from django.db.models import Q, Sum
from django.utils.translation import gettext as _

from core.models.attachment import Attachment
from core.models.settings import Settings

_BYTES_PER_MB = 1024 * 1024

#: Ab diesem Fuellstand (Prozent) warnen Verwaltungs-Hub und Leitungs-Dashboard.
WARN_THRESHOLD_PERCENT = 90


@dataclass(frozen=True)
class StorageUsage:
    """Belegung und wirksames Limit einer Einrichtung — in Bytes.

    Die ``*_mb``-Eigenschaften runden kaufmaennisch auf ganze Megabyte; sie
    sind fuer Meldungstexte und Kacheln gedacht (dasselbe Format wie in
    ``enforce_upload_size``). Wer exakt rechnen will, nimmt die Byte-Werte.
    ``limit_bytes is None`` heisst **unbegrenzt** — dann gibt es weder eine
    Prozentzahl noch eine Warnschwelle.
    """

    used_bytes: int
    trash_bytes: int
    limit_bytes: int | None

    @property
    def used_mb(self) -> int:
        return round(self.used_bytes / _BYTES_PER_MB)

    @property
    def trash_mb(self) -> int:
        return round(self.trash_bytes / _BYTES_PER_MB)

    @property
    def limit_mb(self) -> int | None:
        return None if self.limit_bytes is None else round(self.limit_bytes / _BYTES_PER_MB)

    @property
    def percent_used(self) -> int | None:
        """Fuellstand in Prozent fuer die **Anzeige** — ``None`` bei
        unbegrenztem Kontingent.

        Bewusst gerundet: eine Kachel „89,63 %" liest niemand. Fuer die
        Schwellenfrage ist dieser Wert NICHT massgeblich, siehe
        :attr:`is_near_limit`.
        """
        if not self.limit_bytes:
            return None
        return round(self.used_bytes * 100 / self.limit_bytes)

    @property
    def is_near_limit(self) -> bool:
        """Ist die Warnschwelle erreicht? Vergleich auf dem **ungerundeten**
        Byte-Verhaeltnis (Refs #2129).

        Vorher lief der Vergleich ueber :attr:`percent_used` — also ueber eine
        bereits kaufmaennisch gerundete Ganzzahl. 89,6 % wurden damit zu 90 und
        loesten die Warnung aus, obwohl die Schwelle nicht erreicht war. Eine
        Warnung, die zu frueh kommt, wird weggeklickt; genau das soll sie nicht.
        Ganzzahl-Arithmetik statt Division, damit kein Gleitkomma-Rand entsteht.
        """
        if not self.limit_bytes:
            return False
        return self.used_bytes * 100 >= self.limit_bytes * WARN_THRESHOLD_PERCENT

    def fits(self, additional_bytes: int) -> bool:
        """Passt eine weitere Datei dieser Groesse noch hinein?

        Der Grenzwert ist **inklusiv**: genau aufgehen darf noch.
        """
        if self.limit_bytes is None:
            return True
        return self.used_bytes + additional_bytes <= self.limit_bytes


def storage_warning(usage: StorageUsage) -> str | None:
    """Der Warnsatz ab :data:`WARN_THRESHOLD_PERCENT` — ``None`` darunter.

    **Ein** msgid fuer beide Rollen (Refs #2129): Die
    Anwendungsbetreuung sieht ihn in der ``settings_warnings``-Liste des
    Verwaltungs-Hubs, die Leitung als Hinweistext in ihrer Arbeitszentrale.
    Zwei getrennte Formulierungen wuerden auseinanderdriften — und im
    Lead-Dashboard fehlte der Text bisher ganz: dort war der Warnzustand
    ausschliesslich ueber die Textfarbe codiert (WCAG 1.4.1).
    """
    if not usage.is_near_limit:
        return None
    return _(
        "Speicherkontingent zu %(percent)s %% belegt (%(used)s von %(limit)s MB, "
        "davon %(trash)s MB im Papierkorb) — Papierkorb leeren oder Kontingent erhöhen."
    ) % {
        "percent": usage.percent_used,
        "used": usage.used_mb,
        "limit": usage.limit_mb,
        "trash": usage.trash_mb,
    }


def _configured_limit_bytes(facility) -> int | None:
    """Das wirksame Limit: das **striktere** von Einrichtungs-Setting und
    Betreiber-Backstop; ``None``, wenn beide fehlen.

    Fail-closed wie ``enforce_allowed_file_types``: fehlt die ``Settings``-Row
    ganz, gilt nicht „unbegrenzt", sondern der Backstop. ``0`` ist an beiden
    Stellen die ausdrueckliche Abschaltung („kein Limit an dieser Stelle") —
    ein Kontingent von 0 MB waere sinnlos, es liesse gar keinen Upload mehr zu.

    ``objects.get(facility=…)`` statt ``for_facility``: dasselbe Muster wie
    ``enforce_upload_size``/``enforce_allowed_file_types`` — ``Settings`` ist
    ein 1:1-Satz je Einrichtung, kein zu scopender Bestand (Refs #1834-Guard
    zielt auf ``objects.filter(facility=…)``).
    """
    try:
        facility_mb = Settings.objects.get(facility=facility).max_storage_mb or None
    except Settings.DoesNotExist:
        facility_mb = None
    backstop_mb = getattr(django_settings, "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB", 0) or None

    candidates = [mb for mb in (facility_mb, backstop_mb) if mb]
    if not candidates:
        return None
    return min(candidates) * _BYTES_PER_MB


def _facility_totals(facility) -> tuple[int, int]:
    """``(belegt, davon Papierkorb)`` in Bytes — EINE bedingte Aggregat-Query."""
    totals = Attachment.objects.for_facility(facility).aggregate(
        used=Sum("file_size"),
        trash=Sum("file_size", filter=Q(deleted_at__isnull=False)),
    )
    return totals["used"] or 0, totals["trash"] or 0


def _enforcement_totals(facility) -> tuple[int, int]:
    """``(belegt, davon Papierkorb)`` fuer die **Durchsetzung** (Refs #2259).

    Der EINE Verbrauchsrechner, an dem beide Backstops haengen: der der
    Anhaenge (:func:`~core.services.file_vault.policy.check_storage_quota_at_commit`)
    und der des CSV-Imports
    (:func:`~core.services.file_vault.policy.check_import_storage_quota_at_commit`).
    Belegt ist, was auf der Platte liegt — Anhangszeilen **und** die
    Quelldateien der noch offenen Importlaeufe. Solange nur die Anhaenge
    zaehlten, nahm jeder der beiden Pfade den jeweils anderen nicht wahr: ein
    Anhang wurde angenommen, waehrend eine offene Importdatei zusaetzlich Platz
    belegte, und umgekehrt (Befund #2259, Mischfall).

    Die **Anzeige** bleibt anhangsbasiert (:func:`facility_storage_usage`):
    offene Importdateien sind temporaer, haengen an einem eigenen Modell und
    verschwinden mit der Uebernahme oder dem Abbruch des Laufs; eine Kachel,
    die sie mitzaehlte, schwankte ohne Zutun der Fachkraft.

    Der Zaehler der Importseite behaelt bewusst seinen Modulnamen
    (``_pending_import_upload_bytes``) — die Mutations-Pins adressieren ihn
    darueber. Der Import ist lokal, weil ``import_engine`` seinerseits ueber die
    Upload-Policy in dieses Modul zeigt.
    """
    from core.services import import_engine  # noqa: PLC0415 — Zyklus vermeiden

    used, trash = _facility_totals(facility)
    return used + import_engine._pending_import_upload_bytes(facility), trash


def facility_storage_usage(facility) -> StorageUsage:
    """Belegung und wirksames Kontingent einer Einrichtung (2 Queries).

    Eine Aggregat-Query fuer Gesamt- und Papierkorb-Summe (bedingtes ``Sum``),
    eine fuer das Einrichtungs-Setting — die Kachel darf die Uebersichtsseiten
    nicht teurer machen.
    """
    used, trash = _facility_totals(facility)
    return StorageUsage(
        used_bytes=used,
        trash_bytes=trash,
        limit_bytes=_configured_limit_bytes(facility),
    )


def storage_lock_name(facility_id) -> str:
    """Der Ressourcen-Name der Kontingent-Sperre einer Einrichtung (Refs #2178).

    **Bewusst derselbe Name wie die AuditLog-Hashkette** — die Sperre ist
    dieselbe, nicht nur eine gleichnamige. Ein eigener Namensraum waere hier
    ein Deadlock-Risiko und kein Gewinn:

    * **Ein einziger Lock je Einrichtung kennt keine Reihenfolge, die sich
      verdrehen koennte.** Das ist der tragende Grund — er haengt an keiner
      Annahme darueber, was Aufrufer vorher tun.
    * Mit einem zweiten Namensraum gaebe es dagegen zwei Sperren und damit eine
      Reihenfolge: Eine Transaktion, die ihre AuditLog-Zeile schon geschrieben
      hat (der Regelfall: ``create_event``/``update_event`` laufen vor
      ``finalize_staged_files``/``apply_attachment_changes``), haelt den
      Ketten-Lock und braucht danach das Kontingent; ein Aufrufer ohne
      vorherige Audit-Zeile (``store_encrypted_file``, Seed, und ein
      ``update_event`` ohne geaenderte Felder, das gar keine Zeile schreibt)
      naehme sie umgekehrt. Zwei solche Transaktionen derselben Einrichtung
      bildeten einen Deadlock-Zyklus, den Postgres nur durch Abbruch aufloest.
    * Der geteilte Key kostet nichts zusaetzlich: dieselbe Transaktion nimmt ihn
      wenige Anweisungen spaeter fuer die Anhang-Audit-Zeile ohnehin und haelt
      ihn bis zum Commit.
    * Zeilensperren sind davon unberuehrt — fuer sie gilt die Reihenfolge
      „Zeile vor Advisory-Lock", die ``storage._lock_superseded_row`` im
      Replace-Pfad herstellt (Refs #2178).

    Abgeleitet wird der Name ausschliesslich ueber
    :func:`~core.services.audit.chain.advisory_lock_name` — eine zweite,
    kopierte Formatierung waere genau die stille Drift, vor der dessen
    Docstring warnt.
    """
    from core.services.audit.chain import advisory_lock_name  # noqa: PLC0415 — Zyklus vermeiden

    return advisory_lock_name(facility_id)


def lock_facility_storage(facility_id) -> None:
    """Sperrt die Einrichtung fuer die Dauer der laufenden Transaktion.

    ``pg_advisory_xact_lock`` wird beim COMMIT/ROLLBACK automatisch
    freigegeben — nur so ueberspannt die Sperre „Summe lesen + Zeile
    schreiben" und serialisiert genau das. Auf Nicht-PostgreSQL (SQLite)
    entfaellt sie, wie in :func:`~core.services.audit.chain.assign_chain_fields`.

    Fail-loud ausserhalb einer Transaktion: im Autocommit-Modus liefe die
    Sperre in ihrer eigenen impliziten Transaktion und waere mit dem Ende des
    ``SELECT`` schon wieder weg — ein lautloser No-op, der genau die Zusage
    dieses Moduls aushebelte.
    """
    if connection.vendor != "postgresql":
        return
    if not connection.in_atomic_block:
        raise RuntimeError(
            "lock_facility_storage() ausserhalb einer Transaktion — die Xact-Sperre "
            "waere sofort wieder frei und wuerde nichts serialisieren."
        )
    from core.services.dynamic_protection import lock_dynamic_protection_gate

    # The quota key is also the audit-chain key. Acquire protection before it
    # so a batch cannot request configuration locks after its first audit write.
    lock_dynamic_protection_gate(facility_id)
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [storage_lock_name(facility_id)])


def facility_storage_overrun(facility, additional_bytes: int) -> StorageUsage | None:
    """Passt ``additional_bytes`` noch — **unter der Sperre** gepruefte Antwort.

    Liefert ``None``, wenn die Datei hineinpasst, sonst die
    :class:`StorageUsage`, die sie sprengt (der Aufrufer auditiert damit NACH
    dem Rollback, siehe ``storage.commit_staged_upload``).

    Die Konfigurationsfrage laeuft VOR der Sperre: eine Einrichtung ohne
    wirksames Kontingent zahlt weder Sperre noch Summen-Query. Das Limit ist
    Konfiguration und nicht umkaempft; umkaempft ist allein die Summe, und die
    wird erst nach dem Sperren gelesen (Refs #2178).

    Gerechnet wird ueber :func:`_enforcement_totals` — Anhaenge **und** offene
    Importdateien (Refs #2259). Beide Speicherpfade der Einrichtung entscheiden
    damit auf derselben Summe und unter derselben Sperre.
    """
    limit_bytes = _configured_limit_bytes(facility)
    if limit_bytes is None:
        return None

    lock_facility_storage(getattr(facility, "pk", facility))
    used, trash = _enforcement_totals(facility)
    usage = StorageUsage(used_bytes=used, trash_bytes=trash, limit_bytes=limit_bytes)
    return None if usage.fits(additional_bytes) else usage

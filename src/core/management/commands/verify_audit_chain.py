"""CLI-Tool: Verifiziert die HMAC-Integritaetskette des AuditLog (Refs #1070).

Fuer den Compliance-Cron: geht jede Facility-Kette (inkl. der System-Kette mit
``facility=NULL``) in ``(timestamp, id)``-Reihenfolge durch und prueft pro
Zeile (a) ``entry_hash`` == ``HMAC(key, prev_hash || canonical(row))`` —
erkennt In-Place-Manipulation — und (b) die ``prev_hash``-Verkettung auf die
vorherige ueberlebende Zeile (MITTIGE Loeschung/Einschub), wobei nur von einem
*authentifizierten* ``AUDIT_PRUNE_CHECKPOINT`` protokollierte Prune-Grenzen
toleriert werden (ein gefaelschter Checkpoint ohne gueltigen ``entry_hash``
legitimiert keine Luecke).

Gibt den ersten Bruch mit Zeilen-ID aus und beendet sich mit Exit-Code != 0,
sobald irgendeine Kette gebrochen ist — damit ein Cron-Job das Finding
eskalieren kann.

**Bekannte Grenze (Tail-Truncation):** Erkannt werden In-Place-Manipulation und
MITTIGE Loeschung/Einschuebe. Das Abschneiden der *juengsten* Zeile(n) am
Ketten-Ende ist NICHT erkennbar — die Restkette bleibt in sich konsistent und es
fehlt der danglende Nachfolger. Dafuer braeuchte es einen extern verankerten
High-Water-Mark (juengster ``entry_hash`` + Count ausserhalb der DB); ein bewusst
offener Folge-Schritt.

Ein fehlender externer Anker gilt standardmaessig als Erstlauf und wird nach
gruenem Lauf still gesetzt. Wo ein Anker nachweislich vorhanden sein MUSS —
etwa nach dem HWM-Import einer Wiederherstellung — erzwingt ``--require-anchor``
stattdessen einen Abbruch (Refs #2242). Ketten ohne Zeilen sind davon
ausgenommen: sie haben keinen Tail, den ein Lauf verankern koennte, und gelten
als trivial verankert (Refs #2317).

Beispiel::

    python manage.py verify_audit_chain
    python manage.py verify_audit_chain --require-anchor
"""

from __future__ import annotations

import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import AuditLog, Facility
from core.services.audit import audit_event
from core.services.audit.chain import verify_chain
from core.services.audit.hwm import HighWaterMarkStore, HwmIntegrityError, _chain_key

# Refs #1070: zentrale Fail-Loud-Pruefung in services/system/_db_admin — als
# Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


class Command(BaseCommand):
    help = "Verifiziert die HMAC-Integritätskette des AuditLog (Refs #1070)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--require-anchor",
            action="store_true",
            help=(
                "Bricht ab, sobald auch nur eine der geprüften Ketten MIT INHALT keinen "
                "externen High-Water-Mark-Anker hat, statt sie als Erstlauf zu behandeln und neu "
                "zu verankern. Eine Kette ohne Zeilen hat keinen verankerbaren Tail und gilt als "
                "trivial verankert (Refs #2317). Für Abläufe, in "
                "denen ein Anker nachweislich vorhanden sein MUSS (Wiederherstellung, Drill) "
                "(Refs #2242)."
            ),
        )
        parser.add_argument(
            "--retire-chain",
            action="append",
            default=[],
            metavar="KENNNUMMER",
            dest="retire_chain",
            help=(
                "Legt die genannte Einrichtungskette im externen Anker ausdrücklich still, statt "
                "ihren verwaisten Anker als Inventarabweichung zu melden. Nur zulässig, wenn die "
                "Einrichtung tatsächlich nicht mehr im Bestand ist und ihre Kette verankert war; "
                "verlangt --retire-reason. Mehrfach angebbar (Refs #2296)."
            ),
        )
        parser.add_argument(
            "--retire-reason",
            default="",
            metavar="TEXT",
            help="Begründung des Stilllegungsnachweises zu --retire-chain (Refs #2296).",
        )
        parser.add_argument(
            "--reactivate-chain",
            action="append",
            default=[],
            metavar="KENNNUMMER",
            dest="reactivate_chain",
            help=(
                "Nimmt den Stilllegungsnachweis der genannten Kette zurück, weil ihr Bestand "
                "bewusst zurückkehrt. Nur zulässig, wenn die Einrichtung wieder im Bestand ist "
                "und ein Nachweis vorliegt; verlangt --reactivate-reason. Mehrfach angebbar "
                "(Refs #2296)."
            ),
        )
        parser.add_argument(
            "--reactivate-reason",
            default="",
            metavar="TEXT",
            help="Begründung der Rückname zu --reactivate-chain (Refs #2296).",
        )

    def _chains_with_anchorable_tail(self) -> set:
        """Ketten-IDs, die ueberhaupt einen verankerbaren Tail besitzen koennen.

        Refs #2317: Eine Kette ohne verkettete Zeile hat keinen Tail — es gibt
        nichts zu verankern, und :meth:`HighWaterMarkStore.record` entfernt
        einen Eintrag ohne Tail ausdruecklich wieder. Eine angelegte, noch
        unbenutzte Einrichtung kann deshalb NIE einen Anker bekommen; ohne
        diese Unterscheidung bliebe ``--require-anchor`` genau nach der
        Erstinbetriebnahme dauerhaft rot.

        ``None`` steht wie ueberall fuer die System-Kette (``facility=NULL``).
        """
        return set(
            AuditLog.objects.exclude(entry_hash="")
            .exclude(entry_hash__isnull=True)
            .values_list("facility_id", flat=True)
            .distinct()
        )

    def _save_hwm(self, hwm) -> None:
        """Persist the external anchor with an actionable operator error."""
        try:
            hwm.save()
        except ValueError as exc:
            raise CommandError(
                f"AUDIT_HWM_PATH is invalid: {exc} Configure a path without '..' components "
                "and run verify_audit_chain again."
            ) from exc
        except OSError as exc:
            raise CommandError(
                f"AUDIT_HWM_PATH is not writable: {hwm.path!r} ({exc}). "
                "Run verify_audit_chain in cron or maintenance with a writable audit-anchor volume."
            ) from exc

    def _require_anchor(self, hwm_path: str, hwm, targets: list) -> None:
        """Fail-loud, wenn ``--require-anchor`` gesetzt ist, aber ein Anker fehlt.

        Ohne Anker verankert ein gruener Lauf die Kette so, wie sie gerade
        vorliegt. Nach einem fehlgeschlagenen HWM-Import auf einem
        wiederhergestellten Host waere das ein abgeschnittener, in sich
        konsistenter Bestand — gruen verankert, ohne je gegen das gesicherte
        Original geprueft worden zu sein.

        Verlangt wird ein Anker fuer JEDE tatsaechlich geprueffte Kette mit
        Inhalt: ein Anker fuer eine andere Einrichtung bewahrt die ankerlose
        Kette nicht vor dem Erstlauf-Pfad. **Leere Ketten gelten als trivial
        verankert** (Refs #2317) — nicht als Aufweichung, sondern weil sie
        keinen Tail haben, den irgendein Lauf verankern koennte. Die Haertung
        bleibt dabei vollstaendig: Ankerpfad und Ankerdatei muessen weiterhin
        vorhanden sein, und eine Kette, die einen Anker HAT und trotzdem leer
        ist, faellt in der anschliessenden Pruefung als Tail-Truncation /
        whole-chain wipe auf.

        Die Entscheidung faellt alles-oder-nichts VOR der Pruefung — so
        entsteht fuer keine Kette ein neuer Anker und auch kein
        ``AUDIT_CHAIN_VERIFIED``-Marker. Die Meldung benennt die betroffenen
        Einrichtungen datenminimiert per Primaerschluessel.
        """
        prefix = "--require-anchor verlangt einen externen Audit-Anker, aber "
        if not hwm_path:
            raise CommandError(
                prefix + "AUDIT_HWM_PATH ist nicht gesetzt — ohne Ankerpfad lässt sich ein "
                "abgeschnittener Bestand nicht von einem Erstlauf unterscheiden."
            )
        if not os.path.exists(hwm_path):
            raise CommandError(
                prefix + f"die HWM-Datei {hwm_path!r} fehlt — Abbruch statt stillem Erstlauf "
                "(ein unvollständig wiederhergestellter Audit-Bestand würde sonst neu verankert)."
            )
        anchorable = self._chains_with_anchorable_tail()
        relevant = [None if facility is None else facility.pk for facility in targets]
        relevant = [facility_id for facility_id in relevant if facility_id in anchorable]
        missing = hwm.missing_anchors(relevant)
        if missing:
            labels = ", ".join("System (facility=NULL)" if pk is None else f"Einrichtung #{pk}" for pk in missing)
            raise CommandError(
                prefix + f"die HWM-Datei {hwm_path!r} enthält für {len(missing)} der {len(relevant)} geprüften "
                f"Kette(n) mit Inhalt keinen verankerten Tail ({labels}) — Abbruch statt stillem Erstlauf."
            )

    def _retire_chains(self, hwm_path: str, hwm, targets: list, keys: list, reason: str) -> None:
        """Schreibt den authentifizierten Stilllegungsnachweis (Refs #2296).

        Eine endgueltig entfernte Einrichtung ist fachlich moeglich — aber die
        Abwesenheit ihrer DB-Zeilen ist selbst KEINE Autorisierung dafuer:
        genau so sieht auch der Angriff aus. Der Nachweis muss deshalb
        ausdruecklich (die Kennnummer wird genannt), begruendet und
        authentifiziert sein: er landet in der mit ``AUDIT_HASH_KEY``
        signierten Ankerdatei, die ohne diesen Schluessel nicht schreibbar ist.

        Abgewiesen wird die Stilllegung
        * der System-Kette (``facility=NULL``) — sie gehoert zu JEDER
          Installation, hat keine Einrichtung im Inventar und passierte die
          Bestandspruefung deshalb immer; stillgelegt haette sie keinen
          Tail-Anker mehr und damit keine Truncation-Erkennung;
        * solange die Einrichtung noch im Bestand ist (sie waere sonst ein Weg,
          eine lebende Kette aus der Pruefung zu nehmen);
        * solange die Kette ueberhaupt noch Audit-Zeilen hat — das ist die
          eigentliche Zusage, und sie darf sich nicht darauf verlassen, dass
          die Fremdschluessel-Kaskade die Zeilen mitnimmt;
        * wenn die Kette gar keinen Anker hat (dann gibt es nichts
          stillzulegen).
        """
        if not hwm_path or hwm is None:
            raise CommandError(
                "--retire-chain braucht den externen Anker: AUDIT_HWM_PATH ist nicht gesetzt — "
                "ohne signierte Ankerdatei lässt sich kein Stilllegungsnachweis hinterlegen."
            )
        if not reason.strip():
            raise CommandError("--retire-chain verlangt eine Begründung per --retire-reason.")
        present = {str(facility.pk) for facility in targets if facility is not None}
        anchored = set(hwm.anchored_chain_keys())
        populated = {_chain_key(facility_id) for facility_id in self._chains_with_anchorable_tail()}
        for key in keys:
            if key == _chain_key(None):
                raise CommandError(
                    f"--retire-chain {key}: die System-Kette lässt sich nicht stilllegen — sie "
                    "gehört zu jeder Installation, und ohne ihren Tail-Anker gäbe es für den "
                    "installationsweiten Bestand keine Truncation-Erkennung mehr."
                )
            if key in present:
                raise CommandError(
                    f"--retire-chain {key}: die Einrichtung existiert weiterhin im Bestand — eine "
                    "lebende Kette wird nicht stillgelegt."
                )
            if key in populated:
                raise CommandError(
                    f"--retire-chain {key}: die Kette enthält weiterhin Audit-Zeilen — stillgelegt "
                    "wird nur ein Bestand, der nachweislich nicht mehr da ist."
                )
            if key not in anchored:
                raise CommandError(
                    f"--retire-chain {key}: die HWM-Datei {hwm_path!r} enthält für diese Kette "
                    "keinen verankerten Tail — es gibt nichts stillzulegen."
                )
        at = timezone.now().isoformat()
        for key in keys:
            hwm.retire(key, reason=reason.strip(), at=at)
        # Sofort persistieren: der Nachweis soll auch dann stehen, wenn die
        # anschliessende Kettenpruefung rot endet.
        self._save_hwm(hwm)
        for key in keys:
            self.stdout.write(self.style.WARNING(f"Kette {key} stillgelegt ({reason.strip()})."))

    def _reactivate_chains(self, hwm_path: str, hwm, targets: list, keys: list, reason: str) -> None:
        """Nimmt Stilllegungsnachweise zurueck (Refs #2296).

        Der Gegenweg zu :meth:`_retire_chains`, damit ein fail-loud
        gewordener Widerspruch (:meth:`HighWaterMarkStore.record`) aufloesbar
        bleibt, ohne die signierte Datei von Hand zu bearbeiten. Er ist
        genauso ausdruecklich und begruendet: die Kette wird benannt, der
        Grund protokolliert, und die Datei bleibt signiert.

        Abgewiesen wird die Ruecknahme, wenn die Einrichtung NICHT wieder im
        Bestand ist (sonst entstuende genau der verwaiste Anker aus dem
        Inventarabgleich) und wenn die Kette gar keinen Nachweis traegt.
        """
        if not hwm_path or hwm is None:
            raise CommandError("--reactivate-chain braucht den externen Anker: AUDIT_HWM_PATH ist nicht gesetzt.")
        if not reason.strip():
            raise CommandError("--reactivate-chain verlangt eine Begründung per --reactivate-reason.")
        present = {str(facility.pk) for facility in targets if facility is not None}
        for key in keys:
            if key not in present:
                raise CommandError(
                    f"--reactivate-chain {key}: die Einrichtung ist nicht im Bestand — eine Kette ohne "
                    "Bestand zurückzuholen erzeugt genau den verwaisten Anker, den die Prüfung meldet."
                )
            entry = hwm.chains.get(key)
            if not isinstance(entry, dict) or not entry.get("retired"):
                raise CommandError(
                    f"--reactivate-chain {key}: die HWM-Datei {hwm_path!r} enthält für diese Kette "
                    "keinen Stilllegungsnachweis — es gibt nichts zurückzunehmen."
                )
        for key in keys:
            previous = hwm.reactivate(key)
            self.stdout.write(
                self.style.WARNING(
                    f"Stilllegung der Kette {key} zurückgenommen ({reason.strip()}); "
                    f"vorheriger Nachweis: seit {previous.get('retired')}, "
                    f"Begründung: {previous.get('reason') or 'keine'}."
                )
            )
        self._save_hwm(hwm)

    def _check_inventory(self, hwm_path: str, hwm, targets: list) -> None:
        """Gleicht die extern verankerten Ketten gegen den DB-Bestand ab (Refs #2296).

        Die Prueflist entstand bisher ausschliesslich aus System-Kette und
        vorhandenen Einrichtungen. Eine Einrichtung, die aus der Datenbank
        verschwindet, verschwindet damit auch aus der Pruefung — ihr gueltiger
        externer Anker blieb unbeachtet, und der Lauf meldete vollstaendige
        Integritaet samt Erfolgsmarker. Genau das ist das Bild nach einer
        unvollstaendigen Wiederherstellung oder einem DB-Owner-Eingriff.

        Die Pruefung laeuft unabhaengig von ``--require-anchor`` und VOR jedem
        Schreibvorgang: kein Erfolgsmarker, keine Fortschreibung der
        Ankerdatei. Sie meldet datenminimiert die Kennnummern.
        """
        if hwm is None:
            return
        known = {_chain_key(None)} | {_chain_key(facility.pk) for facility in targets if facility is not None}
        orphans = [key for key in hwm.anchored_chain_keys() if key not in known]
        if orphans:
            labels = ", ".join(f"Einrichtung #{key}" for key in orphans)
            raise CommandError(
                f"Die HWM-Datei {hwm_path!r} verankert {len(orphans)} Kette(n), die im aktuellen "
                f"Datenbankbestand fehlen ({labels}). Abbruch statt Vollständigkeitsmeldung — ein "
                "verankerter Bestand, der nicht mehr da ist, ist ein Integritätsbefund und keine "
                "Autorisierung. War die Entfernung beabsichtigt, den Lauf einmalig mit "
                "--retire-chain <Kennnummer> --retire-reason <Text> fahren; das hinterlegt den "
                "authentifizierten Stilllegungsnachweis in der signierten Ankerdatei."
            )

    def handle(self, *args, **options):
        # Refs #1070: Wie enforce_retention (Refs #1016 A1.1) fail-loud, statt
        # RLS-blind 0 Zeilen zu pruefen und faelschlich „Alle Audit-Ketten intakt"
        # zu melden. Der Compliance-Cron MUSS als Rolle mit BYPASSRLS (Admin) laufen.
        if not _has_rls_bypass_context():
            raise CommandError(
                "verify_audit_chain läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — sonst "
                "sähe der Lauf 0 Zeilen und meldete fälschlich intakte Ketten (Refs #1070)."
            )
        # R16 (Refs #1476/#1665): optionaler externer High-Water-Mark. Ist
        # ``AUDIT_HWM_PATH`` gesetzt, prueft der Lauf zusaetzlich gegen den zuletzt
        # verankerten Tail (erkennt Tail-Truncation / whole-chain wipe) und
        # ruckt den Anker NUR nach vollstaendig gruenem Lauf monoton vor. Eine
        # ungueltige HWM-Signatur ist selbst ein Tamper-Verdacht → fail-loud.
        hwm_path = getattr(settings, "AUDIT_HWM_PATH", "") or ""
        hwm = None
        if hwm_path:
            try:
                hwm = HighWaterMarkStore.load(hwm_path)
            except HwmIntegrityError as exc:
                raise CommandError(str(exc)) from exc

        # System-Kette (facility=NULL) zuerst, dann jede Facility.
        targets: list = [None, *Facility.objects.all().order_by("name")]

        # Refs #2296: Erst den ausdruecklichen Stilllegungsnachweis verbuchen,
        # dann Anker gegen DB-Bestand abgleichen — beides VOR jeder Pruefung
        # und jedem Schreibvorgang.
        retire_keys = [str(value).strip() for value in options.get("retire_chain") or [] if str(value).strip()]
        if retire_keys:
            self._retire_chains(hwm_path, hwm, targets, retire_keys, options.get("retire_reason") or "")
        back_keys = [str(value).strip() for value in options.get("reactivate_chain") or [] if str(value).strip()]
        if back_keys:
            self._reactivate_chains(hwm_path, hwm, targets, back_keys, options.get("reactivate_reason") or "")
        self._check_inventory(hwm_path, hwm, targets)

        # Refs #2242: Opt-in-Haertung. Der Default bleibt unveraendert (fehlender
        # Anker = Erstlauf); nur mit ``--require-anchor`` ist ein fehlender Anker
        # selbst schon ein Abbruchgrund — und zwar fuer jede der oben ermittelten
        # Ketten, bevor der erste Schreibvorgang stattfindet.
        if options.get("require_anchor"):
            self._require_anchor(hwm_path, hwm, targets)

        broken = 0
        results: list = []
        for facility in targets:
            label = "System (facility=NULL)" if facility is None else f"Facility '{facility.name}'"
            result = verify_chain(facility, hwm=hwm)
            results.append((facility, result))
            if result.ok:
                self.stdout.write(self.style.SUCCESS(f"OK   {label}: {result.rows_checked} Zeile(n) intakt."))
            else:
                broken += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"FAIL {label}: Bruch bei Zeile {result.first_break_id} — {result.reason} "
                        f"(nach {result.rows_checked} geprüften Zeilen)."
                    )
                )

        if broken:
            self.stderr.write(self.style.ERROR(f"Audit-Kette gebrochen in {broken} Kette(n) — Tamper-Verdacht."))
            sys.exit(1)

        # Nur nach durchgehend gruenem Lauf den Anker fortschreiben.
        if hwm is not None:
            try:
                for facility, result in results:
                    facility_id = None if facility is None else facility.pk
                    hwm.record(facility_id, result.rows_checked, result.last_entry_hash)
            except HwmIntegrityError as exc:
                # Refs #2296: ein zurueckgekehrter, zuvor stillgelegter Bestand.
                # Abbruch VOR ``save`` und vor dem Erfolgsmarker — die Datei
                # bleibt unveraendert, der Widerspruch bleibt sichtbar.
                raise CommandError(str(exc)) from exc
            self._save_hwm(hwm)

        # Refs #1872: Last-Run-Marker fuer die Betriebsansicht. Bewusst erst
        # HIER — nach dem ``sys.exit(1)``-Zweig und nach dem HWM-Fortschreiben:
        # der Marker steht ausschliesslich fuer einen vollstaendig gruenen
        # Lauf. Ein Tamper-Fund darf die Ampel nicht auf gruen setzen.
        # ``facility=None`` wie bei allen installationsweiten Cron-Markern; die
        # Zeile haengt sich selbst an die System-Kette und wird beim naechsten
        # Lauf mitgeprueft.
        audit_event(
            AuditLog.Action.AUDIT_CHAIN_VERIFIED,
            user=None,
            facility=None,
            target_type="AuditChainVerification",
            detail={
                "chains": len(results),
                "rows_checked": sum(result.rows_checked for _, result in results),
            },
        )

        self.stdout.write(self.style.SUCCESS("Alle Audit-Ketten intakt."))

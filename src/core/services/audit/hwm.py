"""Externer High-Water-Mark (HWM) fuer die Audit-HMAC-Kette (R16, Refs #1476/#1665).

``verify_chain`` prueft ausschliesslich die *ueberlebenden* Zeilen einer Kette
gegeneinander. Zwei Angriffe bleiben damit prinzipiell unerkannt:

* **Whole-chain TRUNCATE** — eine komplett geleerte Kette hat ``rows=[]`` und
  gilt trivial als „intakt" (nichts zu widerlegen).
* **Tail-Truncation** — schneidet ein Angreifer die *juengste(n)* Zeile(n) am
  Ketten-Ende ab, bleibt die Restkette in sich konsistent; es fehlt der
  danglende Nachfolger, an dem eine mittige Loeschung auffiele.

Beides ist per Definition nur mit einem **ausserhalb der DB** verankerten
High-Water-Mark erkennbar: dem juengsten ``entry_hash`` (Tail-Anker) plus
Zaehlstand je Kette. Dieser Store haelt genau das in einer signierten
JSON-Datei (Pfad aus ``settings.AUDIT_HWM_PATH``; leer ⇒ Feature aus,
Verhalten unveraendert).

**Threat-Model / Grenzen.** Die Datei ist mit ``AUDIT_HASH_KEY`` HMAC-signiert:
ein Angreifer mit reinem DB-Zugriff (Owner/``replica``) — der klassische
Vektor der Kette — kann sie weder faelschen noch den Anker verschieben. Wer
zusaetzlich Dateisystem-Zugriff hat, kann die Datei loeschen (das Feature
degradiert dann still auf den Ist-Zustand vor R16) — nicht aber unbemerkt
umschreiben, ohne den Schluessel zu kennen. Die **betriebliche Verankerung**
(Offsite-Kopie/Backup des HWM, Cron-Frequenz des ``verify_audit_chain``-Laufs)
ist bewusst eine Deployment-Entscheidung und nicht Teil dieses Moduls.

Der Anker wird nur nach einem **vollstaendig gruenen** ``verify_audit_chain``-Lauf
monoton vorgeruckt (siehe Management-Command). Da der Anker damit stets sehr
jung bleibt, entfernt ihn das retention-basierte Pruning (Monate spaeter) nicht,
bevor er laengst weitergesetzt wurde — legitimes Wachstum + Pruning erzeugen
keine False-Positives.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass

from core.services.audit.hash import _get_audit_hash_key

_VERSION = 1


class HwmIntegrityError(Exception):
    """Die HWM-Datei existiert, ihre HMAC-Signatur ist aber ungueltig —
    Manipulationsverdacht. Fail-loud statt stiller Ignoranz."""


def _chain_key(facility_id) -> str:
    """Stabiler Store-Schluessel je Kette (``system`` fuer ``facility=NULL``)."""
    return "system" if facility_id is None else str(facility_id)


def _sign(chains: object) -> str:
    """HMAC-SHA256 ueber die kanonische Serialisierung des ``chains``-Dicts."""
    canonical = json.dumps(chains, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_get_audit_hash_key(), canonical, "sha256").hexdigest()


def _validate_chains(chains: object, path: str) -> dict:
    """Prueft das Store-Schema nach der Signatur — fail-closed statt Traceback.

    Die Signatur bindet nur den *Inhalt*, nicht dessen Form: ein korrekt
    signierter Store kann strukturell unbrauchbar sein (Skalar statt
    Eintrags-Dict, Tail kein String). Ohne diese Pruefung schlaegt die
    Auswertung erst weiter unten als ``AttributeError``/``TypeError`` auf —
    fail-closed, aber ohne verwertbare Meldung fuer den Betrieb.
    """
    prefix = f"HWM-Datei {path!r}: Struktur ungueltig — "
    if not isinstance(chains, dict):
        raise HwmIntegrityError(prefix + "'chains' ist kein Objekt (Ketten-Schluessel -> Anker-Eintrag).")
    for key, entry in chains.items():
        # Ein leerer Eintrag (``null``, ``{}``) ist der bekannte Erstlauf-Fall
        # und wurde immer schon toleriert — ebenso ein fehlender oder leerer
        # Tail und ein Zaehlstand in fremdem Format (er geht nur in die
        # Bruchmeldung ein). Nur die Formen, die die Auswertung frueher zum
        # Absturz brachten, werden hier abgewiesen.
        if not entry:
            continue
        if not isinstance(entry, dict):
            raise HwmIntegrityError(prefix + f"Eintrag {key!r} ist kein Objekt.")
        anchor = entry.get("last_entry_hash")
        if anchor and not isinstance(anchor, str):
            raise HwmIntegrityError(prefix + f"Eintrag {key!r} hat keinen Tail-Anker als Zeichenkette.")
    return chains


@dataclass
class HighWaterMarkStore:
    """Signierter, datei-basierter HWM-Store fuer die Audit-Ketten.

    ``chains`` bildet ``chain_key -> {"count": int, "last_entry_hash": str}`` ab;
    eine per :meth:`retire` stillgelegte Kette traegt stattdessen
    ``{"retired": <ISO-Zeitpunkt>, "reason": str, "count": int}`` und damit
    keinen Tail-Anker mehr (Refs #2296).
    In-Memory geladen; Aenderungen (:meth:`record`) werden erst mit
    :meth:`save` atomar persistiert.
    """

    path: str
    chains: dict = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chains is None:
            self.chains = {}

    # -- Laden / Persistieren -------------------------------------------------

    @classmethod
    def load(cls, path: str) -> HighWaterMarkStore:
        """Laedt den Store aus ``path``.

        Fehlende Datei ⇒ leerer Store (Erst-Lauf). Vorhandene Datei mit
        ungueltiger Signatur, unlesbarem Inhalt oder unbrauchbarer Struktur ⇒
        :class:`HwmIntegrityError` (fail-loud). Die Signatur wird zuerst
        geprueft: ein Manipulationsverdacht schlaegt jeden Strukturbefund.
        """
        store = cls(path=path, chains={})
        if not path or not os.path.exists(path):
            return store
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise HwmIntegrityError(
                f"HWM-Datei {path!r}: nicht lesbar ({type(exc).__name__}) — eine leere, abgeschnittene "
                "oder nicht zugaengliche Datei belegt keinen externen Audit-High-Water-Mark."
            ) from exc
        if not isinstance(data, dict):
            raise HwmIntegrityError(
                f"HWM-Datei {path!r}: Struktur ungueltig — die Datei enthaelt kein signiertes Objekt."
            )
        chains = data.get("chains", {})
        signature = data.get("sig", "")
        expected = _sign(chains)
        # ``compare_digest`` verlangt ASCII-Strings: eine Signatur mit
        # Nicht-ASCII-Zeichen wirft dort sonst einen ungefangenen ``TypeError``.
        if not isinstance(signature, str) or not signature.isascii() or not hmac.compare_digest(expected, signature):
            raise HwmIntegrityError(
                f"HWM-Datei {path!r}: Signatur ungueltig — Manipulationsverdacht am externen Audit-High-Water-Mark."
            )
        store.chains = _validate_chains(chains, path)
        return store

    def save(self) -> None:
        """Schreibt den Store signiert und atomar (temp + ``os.replace``)."""
        if not self.path:
            return
        payload = {"version": _VERSION, "chains": self.chains, "sig": _sign(self.chains)}
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hwm-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # -- Abfrage / Fortschreibung --------------------------------------------

    def get(self, facility_id):
        """Gespeicherter ``{"count", "last_entry_hash"}``-Eintrag oder ``None``."""
        return self.chains.get(_chain_key(facility_id))

    def missing_anchors(self, facility_ids: list) -> list:
        """Alle uebergebenen Ketten OHNE verwendbaren Tail-Anker, Reihenfolge erhalten.

        Bewusst **je Kette** statt store-weit: fuer jede Kette ohne eigenen
        Anker greift der in :meth:`load` beschriebene Erstlauf-Fall — es gibt
        nichts, wogegen sie geprueft werden koennte, und der naechste gruene
        Lauf verankert sie so, wie sie gerade vorliegt. Ein Anker fuer eine
        *andere* Kette aendert daran nichts. Betriebs-Ablaeufe, die einen
        bestehenden Anker voraussetzen — etwa der Restore-/Drill-Ablauf nach
        dem HWM-Import — erkennen hieran, welche Ketten ungeprueft neu
        verankert wuerden, und koennen abbrechen (Refs #2242).

        ``None`` steht wie ueberall fuer die System-Kette (``facility=NULL``).
        """
        missing = []
        for facility_id in facility_ids:
            entry = self.chains.get(_chain_key(facility_id))
            if not isinstance(entry, dict) or not entry.get("last_entry_hash"):
                missing.append(facility_id)
        return missing

    def anchored_chain_keys(self) -> list[str]:
        """Alle Ketten-Schluessel mit **aktivem** externen Tail-Anker.

        Refs #2296: Der Store ist die einzige Instanz, die eine verankerte
        Kette ueberdauert, wenn ihr Bestand aus der Datenbank verschwindet.
        ``verify_audit_chain`` gleicht diese Liste gegen das DB-Inventar ab —
        ein Anker ohne zugehoerige Einrichtung ist eine Inventarabweichung,
        kein stiller Normalfall.

        Nicht gelistet werden Eintraege ohne verwendbaren Tail (Erstlauf-Fall,
        siehe :meth:`missing_anchors`) und Ketten mit
        :meth:`retire`-Stilllegungsnachweis: deren Bestand DARF fehlen, weil
        die Entfernung ausdruecklich und — ueber die Signatur der Datei —
        authentifiziert festgehalten wurde.
        """
        keys = []
        for key, entry in self.chains.items():
            if not isinstance(entry, dict) or entry.get("retired"):
                continue
            if entry.get("last_entry_hash"):
                keys.append(key)
        return keys

    def retire(self, facility_id, *, reason: str, at: str) -> None:
        """Ersetzt den Anker einer Kette durch einen Stilllegungsnachweis.

        Der Nachweis ist die einzige Form, in der ein fehlender Bestand
        legitim sein kann: er nennt Zeitpunkt und Begruendung, und er wird mit
        :meth:`save` unter demselben ``AUDIT_HASH_KEY`` signiert wie der Anker
        selbst. Wer ihn schreibt, muss den Schluessel besitzen — fehlende
        DB-Zeilen allein autorisieren nichts. Der Tail-Anker faellt dabei weg:
        die Kette ist abgeschlossen, nicht fortgeschrieben.
        """
        entry = self.chains.get(_chain_key(facility_id))
        previous_count = entry.get("count") if isinstance(entry, dict) else None
        record: dict = {"retired": at, "reason": reason}
        if previous_count is not None:
            record["count"] = previous_count
        self.chains[_chain_key(facility_id)] = record

    def check(self, facility_id, present_hashes: set[str]) -> str | None:
        """Prueft die aktuelle Kette gegen den gespeicherten Tail-Anker.

        ``present_hashes`` ist die Menge aller aktuell in der Kette physisch
        vorhandenen ``entry_hash``-Werte. Fehlt der zuletzt verankerte Tail
        darin, wurden die juengste(n) Zeile(n) abgeschnitten oder die Kette
        geleert. Ohne vorherigen Anker (Erst-Lauf / neue Facility) ⇒ ``None``.

        Returns: sprechender Bruch-Reason oder ``None`` (unauffaellig).
        """
        entry = self.get(facility_id)
        if not entry:
            return None
        anchor = entry.get("last_entry_hash") or ""
        if not anchor:
            return None
        if anchor not in present_hashes:
            prior = entry.get("count")
            return (
                "tail truncation / whole-chain wipe: verankerter High-Water-Mark "
                f"(count={prior}, tail={anchor[:12]}…) fehlt in der aktuellen Kette "
                "(juengste Zeile[n] abgeschnitten oder Kette geleert)"
            )
        return None

    def reactivate(self, facility_id) -> dict:
        """Nimmt den Stilllegungsnachweis einer Kette zurueck (Refs #2296).

        Der Gegenweg zu :meth:`retire` fuer den Fall, dass ein stillgelegter
        Bestand bewusst zurueckkehrt. Der Eintrag faellt weg, die Kette ist
        danach wieder ein regulaerer Erstlauf-Fall und wird vom naechsten
        gruenen Lauf neu verankert. Der zurueckgenommene Nachweis wird
        zurueckgegeben, damit der Aufrufer ihn protokollieren kann — er
        verschwindet nicht lautlos.
        """
        return self.chains.pop(_chain_key(facility_id), {}) or {}

    def record(self, facility_id, count: int, last_entry_hash: str | None) -> None:
        """Setzt den Anker der Kette auf den aktuellen Tail (In-Memory).

        Nur nach einem vollstaendig gruenen Verify aufrufen — dann ist der neue
        Tail legitim. Eine leere Kette ohne Tail (``last_entry_hash`` falsy)
        wird nicht verankert, damit ein spaeterer echter Tail nicht als „fehlend"
        gemeldet wird.

        Ein vorhandener Stilllegungsnachweis (:meth:`retire`) ist fuer beide
        Schreibpfade gesperrt (Refs #2296): Kehrt eine Kette mit derselben
        Kennnummer zurueck, wuerde der neue Tail den signierten Nachweis
        ueberschreiben und die leere Kette ihn sogar loeschen — die
        dokumentierte Entfernung waere spurlos weg, und genau dieser Widerspruch
        ist das Signal (unvollstaendige Wiederherstellung, alter Dump,
        untergeschobener Bestand). Fail-loud statt stiller Ueberschreibung; der
        ausdrueckliche Weg zurueck ist :meth:`reactivate`.
        """
        key = _chain_key(facility_id)
        existing = self.chains.get(key)
        if isinstance(existing, dict) and existing.get("retired"):
            raise HwmIntegrityError(
                f"HWM-Datei {self.path!r}: Die Kette {key} traegt einen Stilllegungsnachweis "
                f"(seit {existing.get('retired')}, Begruendung: {existing.get('reason') or 'keine'}), "
                "ihr Bestand ist aber wieder da. Ein gruener Lauf wuerde den signierten Nachweis "
                "stillschweigend ersetzen. Entweder ist die Wiederherstellung unvollstaendig oder aus "
                "einem falschen Stand, oder die Kette kehrt bewusst zurueck — dann einmalig mit "
                f"--reactivate-chain {key} --reactivate-reason <Text> fahren."
            )
        if not last_entry_hash:
            self.chains.pop(key, None)
            return
        self.chains[key] = {
            "count": int(count),
            "last_entry_hash": last_entry_hash,
        }

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


def _sign(chains: dict) -> str:
    """HMAC-SHA256 ueber die kanonische Serialisierung des ``chains``-Dicts."""
    canonical = json.dumps(chains, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_get_audit_hash_key(), canonical, "sha256").hexdigest()


@dataclass
class HighWaterMarkStore:
    """Signierter, datei-basierter HWM-Store fuer die Audit-Ketten.

    ``chains`` bildet ``chain_key -> {"count": int, "last_entry_hash": str}`` ab.
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
        ungueltiger Signatur ⇒ :class:`HwmIntegrityError` (fail-loud).
        """
        store = cls(path=path, chains={})
        if not path or not os.path.exists(path):
            return store
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        chains = data.get("chains", {})
        expected = _sign(chains)
        if not hmac.compare_digest(expected, data.get("sig", "")):
            raise HwmIntegrityError(
                f"HWM-Datei {path!r}: Signatur ungueltig — Manipulationsverdacht am externen Audit-High-Water-Mark."
            )
        store.chains = chains
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

    def record(self, facility_id, count: int, last_entry_hash: str | None) -> None:
        """Setzt den Anker der Kette auf den aktuellen Tail (In-Memory).

        Nur nach einem vollstaendig gruenen Verify aufrufen — dann ist der neue
        Tail legitim. Eine leere Kette ohne Tail (``last_entry_hash`` falsy)
        wird nicht verankert, damit ein spaeterer echter Tail nicht als „fehlend"
        gemeldet wird.
        """
        if not last_entry_hash:
            self.chains.pop(_chain_key(facility_id), None)
            return
        self.chains[_chain_key(facility_id)] = {
            "count": int(count),
            "last_entry_hash": last_entry_hash,
        }

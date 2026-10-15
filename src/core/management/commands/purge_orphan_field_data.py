"""Management command: verwaiste ``data_json``-Keys aus Client/Case/Event entfernen.

Refs #1725(a): Wird eine Feldzuordnung von einem ``DocumentType`` entfernt,
bleiben bereits erfasste Werte unter ihrem Slug in ``data_json`` gespeichert —
der Slug faellt erst beim naechsten Speichern der Entitaet durch
``validate_data_json``. Bis dahin sind sie „verwaist": in der Detailansicht in
der Sektion „Nicht mehr zugeordnete Felder" sichtbar (Refs #1725), aber aus
Sicht der Datenminimierung (Art. 5 Abs. 1 lit. c/e DSGVO) Reste, die entfernt
werden sollen, sobald sie fachlich nicht mehr gebraucht werden.

Dieser Command entfernt bei Personen, Faellen und Ereignissen jene
``data_json``-Schluessel, die **nicht (mehr)** zur aktuellen Feldzuordnung ihres
Typs gehoeren. Zugeordnete Werte — auch verschluesselte — bleiben unangetastet:
es werden nur Keys entfernt, keine Werte umgeschrieben.

**Verwaiste FILE-Marker werden bewusst UEBERSPRUNGEN** und separat
gezaehlt/gemeldet, statt geloescht: der ``data_json``-Marker ist die einzige
Bruecke zur verschluesselten ``Attachment``-Zeile.

M24 (Refs #1738, ADR-039 §7): Die Skip-Regel galt frueher faktisch nur fuer
Ereignisse (Datei-Felder waren an Personen-/Fall-Typen gesperrt, E8). Seit der
Owner-Generik gilt sie fuer **alle drei** Entitaeten — Personen und Faelle
koennen ebenfalls FILE-Waisen tragen und werden genauso verschont.
Loeschte man ihn hier, verschonte ``cleanup_orphan_storage_files`` die Datei (die
Attachment-Zeile existiert weiter) und sie waere danach UI-unsichtbar und nur
noch ueber die Klient-Hardloeschung erreichbar — schlechter minimiert statt
besser. Die eigentliche Datei-Bereinigung laeuft ueber die Attachment-
Loeschpfade / ``cleanup_orphan_storage_files``, nicht ueber diesen Command.

    python manage.py purge_orphan_field_data --dry-run
    python manage.py purge_orphan_field_data

Wie die uebrigen Wartungskommandos MUSS der Lauf mit der Admin-DB-Rolle
(BYPASSRLS) erfolgen — siehe docs/ops-runbook.md § 3.5 / § 9.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Case, Client, Event

# Refs #1725: FILE-Marker-Erkennung (Stufe A/B) — verwaiste Datei-Marker werden
# uebersprungen, nicht geloescht (s. Modul-Docstring). Refs #1738: Der Helfer
# ist mit der Owner-Generik nach ``services/dynamic_data`` gezogen (SSOT), weil
# Marker nicht mehr event-gebunden sind.
from core.services.dynamic_data import is_file_marker

# Refs #1016/#1554: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


def _purge_entity_data_json(entity):
    """Berechne die bereinigte ``data_json`` einer Entitaet.

    Rueckgabe ``(new_data_or_None, removed_keys, skipped_file_keys)``.
    ``new_data`` ist ``None``, wenn nichts zu SCHREIBEN ist:

    * **kein ``document_type``** — ohne Typ gibt es keine massgebliche
      Feld-Whitelist; welche Keys „richtig" waeren, ist nicht entscheidbar.
      Solche Datensaetze werden nur gezaehlt, NICHT angefasst (kein Raten).
    * **keine loeschbaren Waisen** — alle Keys gehoeren zum Typ (oder sind
      uebersprungene FILE-Waisen, die erhalten bleiben).

    ``skipped_file_keys`` sind verwaiste FILE-Marker (Refs #1725): sie werden
    bewusst NICHT entfernt (der Marker ist die einzige Bruecke zur
    verschluesselten ``Attachment``-Zeile — s. Modul-Docstring) und bleiben
    daher in ``new_data`` erhalten; sie werden nur gezaehlt/gemeldet. ``new_data``
    enthaelt also genau die zugeordneten Keys **plus** die uebersprungenen
    FILE-Waisen — es fallen nur die verwaisten Nicht-Datei-Keys weg.
    """
    if entity.document_type_id is None:
        return None, [], []
    data = entity.data_json or {}
    if not data:
        return None, [], []
    allowed = set(entity.document_type.fields.values_list("field_template__slug", flat=True))
    orphans = [k for k in data if k not in allowed]
    # FILE-Waisen bewusst behalten (Refs #1725) — s. Modul-Docstring.
    skipped_files = sorted(k for k in orphans if is_file_marker(data[k]))
    removed = sorted(k for k in orphans if k not in set(skipped_files))
    if not removed:
        # Nichts zu schreiben; uebersprungene FILE-Waisen trotzdem melden.
        return None, [], skipped_files
    new_data = {k: v for k, v in data.items() if k not in set(removed)}
    return new_data, removed, skipped_files


class Command(BaseCommand):
    help = (
        "Entferne verwaiste data_json-Keys (nicht mehr am Dokumentationstyp) aus "
        "Personen/Fällen/Ereignissen (Refs #1725). Zugeordnete (auch verschlüsselte) "
        "Werte bleiben unangetastet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Zeige nur, was bereinigt würde — ohne zu schreiben.",
        )

    def handle(self, *args, **options):
        # Orphan-Cleanup-RLS-Falle (Refs #1554 / #1016 A1.1): Client/Case/Event
        # stehen unter FORCE RLS. Als App-Rolle (NOBYPASSRLS, kein
        # app.current_facility_id-GUC) saehe der Lauf 0 Zeilen und meldete
        # trotzdem Erfolg — die verwaisten Reste blieben liegen, obwohl der
        # Operator ein Aufraeumen protokolliert glaubt. Fail-loud wie
        # reencrypt_fields / cleanup_orphan_storage_files.
        if not _has_rls_bypass_context():
            raise CommandError(
                "Orphan-Purge läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst sähe der Lauf 0 Zeilen und meldete fälschlich Erfolg, während die "
                "verwaisten Daten liegen blieben. Command mit der Admin-Rolle aufrufen "
                "(docs/ops-runbook.md § 3.5 / § 9 DB-Rollenmodell)."
            )
        dry_run = options["dry_run"]
        results = {
            "Personen": self._purge_model(Client, dry_run),
            "Fälle": self._purge_model(Case, dry_run),
            "Ereignisse": self._purge_model(Event, dry_run),
        }

        verb = "Würde bereinigen" if dry_run else "Bereinigt"
        parts = [
            f"{label}: {cleaned}/{checked} bereinigt, {keys} Key(s) entfernt, {files} Datei-Waise(n) übersprungen"
            for label, (checked, cleaned, keys, files) in results.items()
        ]
        self.stdout.write(self.style.SUCCESS(f"{verb} — " + "; ".join(parts) + "."))

    def _purge_model(self, model, dry_run: bool):
        """Iteriere ``model`` und bereinige verwaiste Keys.

        Rueckgabe ``(checked, cleaned, keys_removed, files_skipped)``.
        ``model.objects`` deckt auch soft-geloeschte Zeilen ab
        (``FacilityScopedManager``/``EventManager`` filtern ``is_deleted`` nicht)
        — anders als reencrypt_fields (Event: ``is_deleted=False`` als bekannte
        Krypto-Luecke) raeumt der Purge bewusst auch dort, damit keine verwaisten
        Reste im Papierkorb ueberdauern. ``files_skipped`` zaehlt verwaiste
        FILE-Marker, die bewusst NICHT geloescht werden (Refs #1725, s. Modul-
        Docstring) — auch bei Datensaetzen, bei denen sonst nichts zu bereinigen
        ist.
        """
        checked = cleaned = keys_removed = files_skipped = 0
        for entity in model.objects.iterator(chunk_size=500):
            checked += 1
            new_data, removed, skipped_files = _purge_entity_data_json(entity)
            files_skipped += len(skipped_files)
            if not removed:
                continue
            cleaned += 1
            keys_removed += len(removed)
            if dry_run:
                continue
            entity.data_json = new_data
            # ``update_fields`` bewusst nur ``data_json``/``updated_at`` (Muster
            # reencrypt_fields): Fuer Event pflegt das pre_save-Signal
            # ``search_text`` im Speicher nach, persistiert es hier aber nicht —
            # verwaiste Werte standen ohnehin nie im Suchindex
            # (``compute_event_search_text`` nimmt nur Slugs des aktuellen Typs),
            # ein Resync waere ein unerwuenschter Seiteneffekt auf sonst
            # unberuehrte Ereignisse.
            entity.save(update_fields=["data_json", "updated_at"])
        return checked, cleaned, keys_removed, files_skipped

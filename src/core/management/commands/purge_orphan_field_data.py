"""Remove orphan dynamic keys from every registered carrier.

Assigned fields and orphan file markers remain intact. Run with an RLS bypass
context to include all facilities, live records and deleted records.
"""

from django.core.management.base import BaseCommand, CommandError

# Refs #1725: FILE-Marker-Erkennung (Stufe A/B) — verwaiste Datei-Marker werden
# uebersprungen, nicht geloescht (s. Modul-Docstring). Refs #1738: Der Helfer
# ist mit der Owner-Generik nach ``services/dynamic_data`` gezogen (SSOT), weil
# Marker nicht mehr event-gebunden sind.
from core.services.dynamic_data import is_file_marker
from core.services.dynamic_records import DYNAMIC_DATA_MODELS

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
            str(model._meta.verbose_name_plural): self._purge_model(model, dry_run) for model in DYNAMIC_DATA_MODELS
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
            fields = ["data_json"]
            if hasattr(entity, "updated_at"):
                fields.append("updated_at")
            entity.save(update_fields=fields)
        return checked, cleaned, keys_removed, files_skipped

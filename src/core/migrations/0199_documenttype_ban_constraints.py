"""Database backstop: a ban document type names a person and lives on events.

No data step: there is no existing data to repair, and an install that still
holds a ban type without a stage fails here on purpose instead of being
rewritten silently. Rolling back only drops the constraints.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    # After the hvkpi merge this dependency moves to 0198_user_ban_details_fingerprint.
    dependencies = [("core", "0197_drop_statistics_event_flat_view")]

    operations = [
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.CheckConstraint(
                condition=models.Q(("system_type", "ban"), _negated=True)
                | models.Q(("min_contact_stage__in", ["identified", "qualified"])),
                name="documenttype_ban_requires_contact_stage",
            ),
        ),
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.CheckConstraint(
                condition=models.Q(("system_type", "ban"), _negated=True) | models.Q(("entity_type", "event")),
                name="documenttype_ban_only_on_events",
            ),
        ),
    ]

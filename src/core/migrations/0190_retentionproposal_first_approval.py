"""Persist the first manual retention approval and require distinct reviewers."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0189_settings_auditlog_retention_help_text")]

    operations = [
        migrations.AddField(
            model_name="retentionproposal",
            name="first_approved_by",
            field=models.ForeignKey(
                to=settings.AUTH_USER_MODEL,
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="first_approved_retention_proposals",
                verbose_name="Erste Freigabe durch",
            ),
        ),
        migrations.AddField(
            model_name="retentionproposal",
            name="first_approved_at",
            field=models.DateTimeField(null=True, blank=True, verbose_name="Erste Freigabe am"),
        ),
        migrations.AddField(
            model_name="retentionproposal",
            name="auto_approved_at",
            field=models.DateTimeField(
                null=True, blank=True, editable=False, verbose_name="Automatisch freigegeben am"
            ),
        ),
        migrations.AddConstraint(
            model_name="retentionproposal",
            constraint=models.CheckConstraint(
                condition=~models.Q(first_approved_by=models.F("approved_by")),
                name="retention_proposal_distinct_approvers",
            ),
        ),
    ]

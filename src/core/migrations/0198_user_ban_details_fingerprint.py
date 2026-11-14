"""Keyed fingerprint of the ban list a user hid in the Zeitstrom (Refs #2809).

Empty while the details are shown. The value is a salted hash of the ban list the
role saw, never ids, names or reasons. It lives on the user row, which every
request has loaded already, and is removed with the account.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0197_drop_statistics_event_flat_view"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="ban_details_fingerprint",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
    ]

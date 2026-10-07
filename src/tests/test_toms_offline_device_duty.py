"""The TOM template names the device duties that offline use relies on.

The offline session key is a non-extractable ``CryptoKey`` in IndexedDB: its
raw bytes cannot be read, but the key stays usable in the browser profile
until it is deleted. Whoever can use an unlocked device or a shared account
can therefore decrypt offline data, so device encryption, a screen lock and
personal accounts are mandatory organisational measures, not optional ones.
"""

import pytest

from core.services.client.dsgvo_package import render_document

DEVICE_DUTY = (
    "Geräteverschlüsselung und Bildschirmsperre auf allen Geräten mit Offline-Nutzung; "
    "keine geteilten Konten auf solchen Geräten"
)


def _section(content: str, start: str, end: str) -> str:
    head, _, rest = content.partition(start)
    assert rest, f"section {start!r} missing"
    body, _, _ = rest.partition(end)
    return body


@pytest.mark.django_db
def test_toms_office_section_lists_offline_device_duty(facility):
    content, _ = render_document("toms", facility)

    access_control = _section(content, "### 1.1 Zutrittskontrolle", "### 1.2 ")
    office = _section(access_control, "**Büro/Einrichtung:**", "\n\n")

    assert f"- [ ] {DEVICE_DUTY}\n" in f"{office}\n"

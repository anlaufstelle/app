"""Pin-Tests der Welle K (0.98, Refs #2193) — Prio-2-Testlücken in
``core.services.case.episodes``, Batch ``case-client``.

Quelle: ``welle-k-0.98/reports/c-scratch/batch-case-client.json``
(``triage-097-case-rest.json``). Drei Survivors, keine Überschneidung mit den
in ``test_mutation_pins_097_case_rest.py`` bereits gepinnten Mutanten (dort:
Zeilensperre der Personenzeile beim Anlegen — hier: Default-Parameterwerte
und die Weitergabe des Startdatums).

* **``x_create_episode__mutmut_1``**: der Default-Wert von ``description``
  ändert sich von ``""`` auf ``"XXXX"`` — ruft ein Aufrufer
  ``create_episode()`` ohne ``description`` auf, landet der Literaltext in
  der Etappe statt eines leeren Felds.
* **``x_create_episode__mutmut_10``/``…_14``**: der Service reicht
  ``started_at`` nicht mehr an ``Case.create_episode()`` durch (Default
  ``None`` bzw. Kwarg entfällt). ``Case.create_episode()`` macht
  ``started_at or timezone.now().date()`` — ein vom Aufrufer explizit
  gesetztes Startdatum ginge verloren und würde stillschweigend durch das
  heutige Datum ersetzt.
"""

from __future__ import annotations

from datetime import date

import pytest

from core.services.case.episodes import create_episode

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def test_create_episode_ohne_description_speichert_leerstring(staff_user, case_open):
    """Pins ``x_create_episode__mutmut_1``: der Default-Parameterwert von
    ``description`` wird zum Literal ``"XXXX"``."""
    episode = create_episode(case_open, staff_user, title="Pin098-Etappe")

    assert episode.description == ""


def test_create_episode_uebernimmt_das_angegebene_startdatum(staff_user, case_open):
    """Pins ``x_create_episode__mutmut_10`` UND ``…_14``: das explizit
    übergebene ``started_at`` muss unverändert an die Episode durchgereicht
    werden. Unter beiden Mutanten fällt der Service auf
    ``timezone.now().date()`` zurück (``Case.create_episode``s eigener
    Default), weil ``started_at`` nicht mehr ankommt."""
    startdatum = date(2024, 1, 1)

    episode = create_episode(case_open, staff_user, title="Pin098-Etappe-Start", started_at=startdatum)

    assert episode.started_at == startdatum

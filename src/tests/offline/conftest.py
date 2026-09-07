"""Familien-conftest der Offline-Tests (Refs #1839).

Ort fuer Fixtures, die mehrere Dateien dieser Familie teilen. Beim Umzug in
das Unterpaket (0.95) gab es keine: die dateilokalen ``body``-, ``doc_type_*``-
und Settings-Fixtures unterscheiden sich je Datei in Literalen oder
Feldsaetzen (z. B. ``help_text`` als Leak-Sonde, ``Suizidgefaehrdung-GEHEIM``
vs. ``Hochsensibel``) und bleiben bewusst lokal. Suiteweite Fixtures liegen in
``src/tests/conftest.py``.
"""

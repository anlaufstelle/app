"""Projekt-App-Konfigurationen."""

from django.contrib.staticfiles.apps import StaticFilesConfig


class ProjectStaticFilesConfig(StaticFilesConfig):
    """collectstatic ohne die Tailwind-Build-Quelle (Refs #1480).

    ``css/input.css`` ist die Tailwind-QUELLE; ausgeliefert wird nur das
    kompilierte ``css/styles.css``. Seit v4 beginnt die Quelle mit
    ``@import "tailwindcss"`` — wird sie mit eingesammelt, versucht das
    Manifest-Post-Processing (HashedFilesMixin) den Bare-Import
    ``tailwindcss`` als Static-Asset aufzuloesen und collectstatic bricht
    mit ``MissingFileError`` ab (Prod-/Docker-Build, E2E-Workflow).
    """

    ignore_patterns = [*StaticFilesConfig.ignore_patterns, "css/input.css"]

"""Resource path metadata for the shipped default catalog.

Catalog readers live in core.packages.catalog. Management commands use this
path in their output without coupling the value to a filesystem location.
"""

DEFAULT_PACKAGE_PATH = "core/packages/default.yaml"

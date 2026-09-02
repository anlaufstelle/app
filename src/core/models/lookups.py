"""Custom Postgres-only lookup: ``__ilike``.

``__ilike`` behaves like ``__icontains`` (case-insensitive substring match),
but compiles to ``ILIKE '%...%'`` instead of ``UPPER(col) LIKE UPPER('%...%')``.
This lets Postgres use trigram GIN indexes on the raw column.

Refs https://github.com/anlaufstelle/app/issues/2027
"""

from django.db.models import CharField, TextField
from django.db.models.lookups import IContains, PatternLookup


class ILikeLookup(PatternLookup):
    lookup_name = "ilike"
    param_pattern = "%%%s%%"

    def get_rhs_op(self, connection, rhs):
        # Direct Python value: process_rhs already wraps with % and escapes,
        # so the operator is a plain ILIKE placeholder.
        if self.rhs_is_direct_value() and not self.bilateral_transforms:
            return "ILIKE %s"
        # Expression / column reference: build the pattern on the SQL side,
        # mirroring the contains pattern_ops shape, but case-insensitive via
        # the ILIKE operator itself.
        return f"ILIKE '%%' || {rhs} || '%%'"

    def as_sql(self, compiler, connection):
        if connection.vendor == "postgresql":
            return super().as_sql(compiler, connection)
        # Non-Postgres fallback: behave exactly like Django's IContains.
        return IContains(self.lhs, self.rhs).as_sql(compiler, connection)


CharField.register_lookup(ILikeLookup)
TextField.register_lookup(ILikeLookup)

"""Custom Postgres-only lookup: ``__ilike``.

``__ilike`` behaves like ``__icontains`` (case-insensitive substring match),
but compiles to ``ILIKE '%...%'`` instead of ``UPPER(col) LIKE UPPER('%...%')``.
This lets Postgres use trigram GIN indexes on the raw column.

Refs https://github.com/anlaufstelle/app/issues/2027
"""

from django.db.models import Field
from django.db.models.lookups import PatternLookup


class ILikeLookup(PatternLookup):
    lookup_name = "ilike"
    param_pattern = "%%%s%%"

    def as_sql(self, compiler, connection):
        # Postgres-specific ILIKE. Other backends fall back via lookup_name.
        if connection.vendor != "postgresql":
            return super().as_sql(compiler, connection)
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        params = list(lhs_params) + list(rhs_params)
        return f"{lhs} ILIKE {rhs}", params


Field.register_lookup(ILikeLookup)

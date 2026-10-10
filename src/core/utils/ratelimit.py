"""Rate limits with a verifiable runtime contract for registered views (Refs #2529)."""

from dataclasses import dataclass
from typing import Any

from django_ratelimit import ALL
from django_ratelimit.core import _split_rate
from django_ratelimit.decorators import ratelimit as library_ratelimit

RATE_LIMIT_WRAPPER_CODE = library_ratelimit()(lambda request: None).__code__


@dataclass(frozen=True, slots=True)
class RateLimit:
    """Keep the exact options used by the library decorator."""

    group: Any = None
    key: Any = None
    rate: Any = None
    method: Any = ALL
    block: bool = True

    def covers(self, method: str) -> bool:
        """Only finite, blocking, method-matching limits provide evidence."""
        if self.block is not True or not self.key:
            return False
        try:
            count, seconds = _split_rate(self.rate)
        except (TypeError, ValueError, AttributeError):
            return False
        if type(count) is not int or type(seconds) is not int or count < 0 or seconds <= 0:
            return False
        methods = self.method
        if methods == ALL:
            return True
        if isinstance(methods, str):
            methods = [methods]
        return isinstance(methods, (list, tuple)) and any(
            isinstance(item, str) and item.upper() == method.upper() for item in methods
        )

    def __call__(self, handler):
        wrapped = library_ratelimit(
            group=self.group, key=self.key, rate=self.rate, method=self.method, block=self.block
        )(handler)
        wrapped._blocking_rate_limit = AppliedRateLimit(wrapped, wrapped.__code__, self)
        return wrapped


@dataclass(frozen=True, slots=True)
class AppliedRateLimit:
    """Bind evidence to its actual callable; functools.wraps copies are insufficient."""

    handler: Any
    code: Any
    limit: RateLimit


def ratelimit(group=None, key=None, rate=None, method=ALL, block=True):
    """Apply django-ratelimit without changing its keys, counters or responses."""
    return RateLimit(group, key, rate, method, block)

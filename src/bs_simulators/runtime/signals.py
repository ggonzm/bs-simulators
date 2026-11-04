"""Transport-independent signal descriptions and command metadata validation."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class SignalDescriptor:
    """Describe a signal from the simulator's perspective.

    ``direction='input'`` permits writes; all signals can be read. Optional
    bounds are inclusive. None means no configured bound, not a physical
    guarantee. Transport adapters map their own addresses to these names.
    """

    name: str
    unit: str
    dtype: str
    direction: str
    lower: float | None = None
    upper: float | None = None

    def as_dict(self):
        return asdict(self)



def controller_identifier(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise ValueError('controller_id must be a non-empty identifier of at most 128 characters')
    if any(ord(character) < 32 for character in value):
        raise ValueError('controller_id must not contain control characters')
    return value


def validate_source_timestamp(source_timestamp):
    """Normalize an optional diagnostic UTC timestamp without scheduling input."""
    if source_timestamp is None:
        return None
    if not isinstance(source_timestamp, str):
        raise ValueError('source_timestamp must be an ISO 8601 UTC timestamp')
    try:
        timestamp = datetime.fromisoformat(source_timestamp)
    except ValueError as exc:
        raise ValueError('source_timestamp must be an ISO 8601 UTC timestamp') from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError('source_timestamp must include a UTC offset (Z or +00:00)')
    return timestamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

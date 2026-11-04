"""Small numeric validators shared by the simulation runtime."""

import numpy as np


def _finite(name, value, *, positive=False, nonnegative=False):
    """Return a finite real scalar, optionally requiring a positive sign."""
    if isinstance(value, (bool, np.bool_, str, bytes)):
        raise ValueError(f'{name} must be a finite number')
    try:
        raw = np.asarray(value)
        if raw.ndim != 0 or raw.dtype.kind in 'bcSU':
            raise ValueError
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{name} must be a finite number') from exc
    if not np.isfinite(result):
        raise ValueError(f'{name} must be finite')
    if positive and result <= 0:
        raise ValueError(f'{name} must be positive')
    if nonnegative and result < 0:
        raise ValueError(f'{name} must be nonnegative')
    return result


def _vector(name, values, size=None, *, allow_empty=False):
    """Return a detached finite real vector with an optional exact size.

    Empty vectors are accepted only with ``allow_empty=True``. This permits
    zero-input plants without allowing an empty state vector by default.
    ``size=0`` is a valid exact-size requirement when empty values are allowed.
    Boolean, complex, string and object arrays are rejected.
    """
    if size is not None:
        if isinstance(size, (bool, np.bool_)) or not isinstance(size, (int, np.integer)) or size < 0:
            raise ValueError('size must be a nonnegative integer or None')
    try:
        raw = np.asarray(values)
        if raw.dtype.kind not in 'iuf':
            raise ValueError
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{name} must be a finite numeric vector') from exc
    if result.ndim != 1 or (not allow_empty and result.size == 0):
        qualifier = '' if allow_empty else 'non-empty '
        raise ValueError(f'{name} must be a {qualifier}one-dimensional vector')
    if not np.isfinite(result).all():
        raise ValueError(f'{name} values must be finite')
    if size is not None and result.size != size:
        raise ValueError(f'{name} must contain {size} values')
    return result.copy()

"""Standard-library validation for catalogue data, without runtime imports."""

from collections.abc import Mapping
import copy
import json
import math


_FIELDS = {
    'name', 'model', 'parameters', 'x0', 'u0', 'dt', 't0',
    'active_controller', 'viewer', 'state_signals', 'input_signals', 'stepper',
}
_STEPPER_FIELDS = {'kind', 'method', 'rtol', 'atol', 'xdot0', 'fixed_x'}
_SIGNAL_FIELDS = {'name', 'unit', 'dtype', 'direction', 'lower', 'upper'}


def _text(name, value):
    """Return a nonempty label without surrounding whitespace or control characters."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 for character in value)):
        raise ValueError(f'{name} must be a non-empty string without surrounding whitespace')
    return value


def _number(name, value, *, positive=False):
    """Return a finite real scalar, optionally requiring a strictly positive value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(value) or (positive and value <= 0):
        qualifier = 'positive and finite' if positive else 'finite'
        raise ValueError(f'{name} must be {qualifier}')
    return value


def _numbers(name, values, *, empty=False):
    """Normalize a numeric list or tuple; allow empty inputs only when requested."""
    if not isinstance(values, (list, tuple)) or (not empty and not values):
        raise ValueError(f'{name} must be a {"possibly empty" if empty else "non-empty"} numeric array')
    return [_number(name, value) for value in values]


def _parameters(values):
    """Return detached, finite JSON-compatible model parameters with string keys."""
    if not isinstance(values, Mapping) or any(not isinstance(key, str) for key in values):
        raise ValueError('parameters must be a table with string keys')
    try:
        return json.loads(json.dumps(dict(values), allow_nan=False))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('parameters must contain finite JSON-compatible values') from exc


def _signals(name, values, count, direction):
    """Validate ordered signal tables, their dimensions, direction and inclusive bounds."""
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise ValueError(f'{name} must contain {count} signal descriptions')
    result = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError(f'{name} entries must be signal tables')
        unknown = set(value) - _SIGNAL_FIELDS
        missing = {'name', 'unit', 'dtype', 'direction'} - set(value)
        if unknown or missing:
            raise ValueError(f'invalid {name} fields: unknown={sorted(unknown)}, missing={sorted(missing)}')
        signal = dict(value)
        _text('signal name', signal['name'])
        if signal['name'] in ('time', 'status'):
            raise ValueError('signal names cannot be time or status')
        if not isinstance(signal['unit'], str) or not isinstance(signal['dtype'], str):
            raise ValueError('signal unit and dtype must be strings')
        if signal['direction'] != direction:
            raise ValueError(f'{name} direction must be {direction!r}')
        for bound in ('lower', 'upper'):
            if signal.get(bound) is not None:
                signal[bound] = _number(f'signal {bound}', signal[bound])
        if (signal.get('lower') is not None and signal.get('upper') is not None
                and signal['lower'] > signal['upper']):
            raise ValueError('signal lower bound must not exceed its upper bound')
        result.append(copy.deepcopy(signal))
    return result


def _stepper_spec(value, state_size):
    """Copy and validate integrator options against the state dimension, without importing solvers."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError('stepper configuration must be a table')
    unknown = set(value) - _STEPPER_FIELDS
    if unknown:
        raise ValueError(f'unknown stepper fields: {sorted(unknown)}')
    value = copy.deepcopy(dict(value))
    kind = value.setdefault('kind', 'ode')
    if kind not in ('ode', 'dae'):
        raise ValueError("stepper kind must be 'ode' or 'dae'")
    if 'method' in value:
        _text('stepper method', value['method'])
    for tolerance in ('rtol', 'atol'):
        if tolerance in value:
            value[tolerance] = _number(tolerance, value[tolerance], positive=True)
    if kind == 'ode':
        if {'xdot0', 'fixed_x'} & value.keys():
            raise ValueError('an ODE stepper does not accept xdot0 or fixed_x')
    else:
        if 'xdot0' not in value:
            raise ValueError('a DAE stepper requires xdot0')
        value['xdot0'] = _numbers('xdot0', value['xdot0'])
        if len(value['xdot0']) != state_size:
            raise ValueError('xdot0 must have the same length as x0')
        fixed = value.get('fixed_x')
        if fixed is not None:
            if (not isinstance(fixed, (list, tuple))
                    or any(isinstance(index, bool) or not isinstance(index, int) for index in fixed)
                    or any(index < 0 or index >= state_size for index in fixed)
                    or len(set(fixed)) != len(fixed)):
                raise ValueError('fixed_x must contain unique state indices within x0')
            value['fixed_x'] = list(fixed)
    return value


def _configuration_values(configuration):
    """Validate configuration fields and return normalized, detached values for dataclass initialization."""
    _text('configuration name', configuration.name)
    _text('model', configuration.model)
    controller = _text('active_controller', configuration.active_controller)
    if len(controller) > 128:
        raise ValueError('active_controller must contain at most 128 characters')
    if configuration.viewer is not None:
        _text('viewer', configuration.viewer)
    states = _numbers('x0', configuration.x0)
    inputs = _numbers('u0', configuration.u0, empty=True)
    state_signals = _signals('state_signals', configuration.state_signals, len(states), 'output')
    input_signals = _signals('input_signals', configuration.input_signals, len(inputs), 'input')
    names = [signal['name'] for signal in state_signals] if state_signals is not None else [f'x{i}' for i in range(len(states))]
    names += [signal['name'] for signal in input_signals] if input_signals is not None else [f'u{i}' for i in range(len(inputs))]
    if len(set(names)) != len(names):
        raise ValueError('signal names must be unique across states and inputs')
    return {
        'parameters': _parameters(configuration.parameters), 'x0': states, 'u0': inputs,
        'dt': _number('dt', configuration.dt, positive=True), 't0': _number('t0', configuration.t0),
        'state_signals': state_signals, 'input_signals': input_signals,
        'stepper': _stepper_spec(configuration.stepper, len(states)),
    }

def _document_fields(data, default_name):
    """Validate top-level TOML fields and supply the configuration name when omitted."""
    unknown = set(data) - _FIELDS
    if unknown:
        raise ValueError(f'unknown configuration fields: {sorted(unknown)}')
    missing = {'model', 'parameters', 'x0', 'u0'} - set(data)
    if missing:
        raise ValueError(f'missing configuration fields: {sorted(missing)}')
    data.setdefault('name', default_name)
    return data

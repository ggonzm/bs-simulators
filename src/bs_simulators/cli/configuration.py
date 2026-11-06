"""CLI literal parsing and lazy public-model inspection."""

import inspect
import tomllib


def _toml_value(literal):
    """Parse one CLI value with TOML's scalar, array and inline-table syntax."""
    parsed = tomllib.loads(f'value = {literal}')
    if set(parsed) != {'value'}:
        raise ValueError('expected a single TOML value')
    return parsed['value']


def _toml_vector(literal, option):
    """Parse a complete state or input array; reject scalar CLI values."""
    value = _toml_value(literal)
    if not isinstance(value, list):
        raise ValueError(f'{option} expects a TOML array')
    return value


def _model_function(name):
    """Resolve a public benchmark-systems model name without importing the runtime."""
    from benchmark_systems import models

    if name not in models.__all__:
        raise ValueError(f'unknown public benchmark model: {name!r}; use --list-models')
    return getattr(models, name)


def _model_kind(model):
    """Describe published numerical signatures; never select a DAE solver implicitly."""
    positional = [parameter for parameter in inspect.signature(model).parameters.values()
                  if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                        inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    return 'dae' if len(positional) == 3 else 'ode'


def _parameter_values(items):
    """Parse repeated physical-parameter overrides, with the last value winning."""
    values = {}
    for item in items or []:
        key, separator, literal = item.partition('=')
        if not separator or not key.strip():
            raise ValueError('--parameter expects NAME=TOML_VALUE')
        values[key.strip()] = _toml_value(literal)
    return values



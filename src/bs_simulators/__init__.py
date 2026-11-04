"""Live simulators, transports and viewers for benchmark-systems models.

Subpackages are loaded on demand, so runtime imports do not initialize HTTP
or viewer components. The numerical model library remains an independent
dependency.
"""

from importlib import import_module

__version__ = '0.1.0'

__all__ = ['PendulumSimulation', 'InMemoryAdapter', 'SimulationPort',
           'SignalDescriptor', '__version__']

_EXPORTS = {
    'PendulumSimulation': '.runtime.core',
    'InMemoryAdapter': '.comms.adapters',
    'SimulationPort': '.runtime.protocols',
    'SignalDescriptor': '.runtime.signals',
}


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))

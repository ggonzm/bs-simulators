"""Live simulators, transports and viewers for benchmark-systems models.

Subpackages are loaded on demand, so runtime imports do not initialize HTTP
or viewer components. The numerical model library remains an independent
dependency.
"""

from importlib import import_module

__version__ = '0.1.0'

__all__ = ['Simulation', 'Stepper', 'ODEStepper', 'DAEStepper',
           'InMemoryAdapter', 'SimulationPort',
           'SignalDescriptor', 'SimulationConfiguration', 'list_configurations',
           'load_configuration', 'load_configuration_file', '__version__']

_EXPORTS = {
    'SimulationConfiguration': '.catalogue',
    'list_configurations': '.catalogue',
    'load_configuration': '.catalogue',
    'load_configuration_file': '.catalogue',
    'Simulation': '.runtime.core',
    'Stepper': '.runtime.protocols',
    'ODEStepper': '.runtime.steppers',
    'DAEStepper': '.runtime.steppers',
    'InMemoryAdapter': '.comms.adapters',
    'SimulationPort': '.runtime.protocols',
    'SignalDescriptor': '.runtime.signals',
}


def __getattr__(name):
    """Load and cache a public export on first access, keeping imports lazy."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    """Include lazy public exports in interactive attribute discovery."""
    return sorted(set(globals()) | set(__all__))

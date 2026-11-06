"""Optional data-only configurations for benchmark-systems models.

Listing and loading configurations use only the Python standard library.
Numerical dependencies are imported when create() constructs a simulation.
TOML model names are restricted to benchmark_systems.models public exports;
arbitrary numerical callables can still be supplied directly to Simulation.
"""

from __future__ import annotations

from collections.abc import Mapping
import copy
from dataclasses import dataclass, replace
from importlib import resources
import inspect
from pathlib import Path
import tomllib
from typing import Any

from ._validation import _configuration_values, _document_fields, _stepper_spec, _text


__all__ = ['SimulationConfiguration', 'list_configurations',
           'load_configuration', 'load_configuration_file']

@dataclass(frozen=True)
class SimulationConfiguration:
    """Data for constructing fresh simulations without copying model equations.

    Parameters are shallow-merged by key when overridden, with nested values
    copied recursively. State/input overrides replace entire vectors. Every
    create() call using a stepper specification constructs a new integrator;
    an explicitly supplied Stepper object instead follows its usual single-use
    ownership rules. This frozen dataclass contains user-editable nested data,
    but creating a simulation never mutates that data. Loading and construction
    operate in memory and never create or overwrite configuration files.
    """

    name: str
    model: str
    parameters: dict[str, Any]
    x0: list[float]
    u0: list[float]
    dt: float = 0.01
    active_controller: str = 'python'
    viewer: str | None = None
    state_signals: list[dict[str, Any]] | None = None
    input_signals: list[dict[str, Any]] | None = None
    stepper: dict[str, Any] | None = None
    t0: float = 0.0

    def __post_init__(self):
        """Store validated copies of configuration data without importing the simulation runtime."""
        for key, value in _configuration_values(self).items():
            object.__setattr__(self, key, value)

    def create(self, *, parameters=None, x0=None, u0=None, dt=None,
               active_controller=None, stepper=None):
        """Return a new Simulation with optional, detached user overrides.

        Only public benchmark_systems.models functions are selected by name.
        Unknown or missing physical parameter names raise ValueError before
        the runtime starts. A stepper override may be a complete specification
        mapping or an explicitly supplied fresh Stepper instance. t0, signal
        descriptions and viewer settings come from the configuration. This
        method never saves a TOML file or modifies the configuration source.
        """
        configuration = replace(self)
        # Numerical imports happen only when a simulation is requested.
        from benchmark_systems import models
        from ..runtime.core import Simulation
        from ..runtime.signals import SignalDescriptor
        from ..runtime.steppers import ODEStepper, DAEStepper

        if configuration.model not in models.__all__:
            raise ValueError(f'unknown public benchmark model: {configuration.model!r}')
        model = getattr(models, configuration.model)
        model_parameters = copy.deepcopy(configuration.parameters)
        if parameters is not None:
            if not isinstance(parameters, Mapping) or any(not isinstance(key, str) for key in parameters):
                raise ValueError('parameter overrides must be a mapping with string keys')
            model_parameters.update(copy.deepcopy(dict(parameters)))
        signature = inspect.signature(model)
        physical = {name: parameter for name, parameter in signature.parameters.items()
                    if parameter.kind is inspect.Parameter.KEYWORD_ONLY and name != 'u'}
        unknown = model_parameters.keys() - physical.keys()
        if unknown:
            raise ValueError(f'unknown parameters for {configuration.model}: {sorted(unknown)}')
        missing = {name for name, parameter in physical.items()
                   if parameter.default is inspect.Parameter.empty and name not in model_parameters}
        if missing:
            raise ValueError(f'missing parameters for {configuration.model}: {sorted(missing)}')

        states = copy.deepcopy(configuration.x0 if x0 is None else x0)
        inputs = copy.deepcopy(configuration.u0 if u0 is None else u0)
        try:
            state_size = len(states)
        except TypeError as exc:
            raise ValueError('x0 must be a non-empty numeric vector') from exc
        selected_stepper = stepper
        if selected_stepper is None or isinstance(selected_stepper, Mapping):
            specification = configuration.stepper if selected_stepper is None else selected_stepper
            specification = _stepper_spec(specification, state_size) or {'kind': 'ode'}
            options = dict(specification)
            kind = options.pop('kind')
            selected_stepper = ODEStepper(**options) if kind == 'ode' else DAEStepper(**options)

        def descriptions(values):
            """Convert optional signal tables into independent runtime descriptors."""
            return None if values is None else [SignalDescriptor(**copy.deepcopy(value)) for value in values]

        try:
            return Simulation(
                model, stepper=selected_stepper, x0=states, u0=inputs,
                parameters=model_parameters,
                dt=configuration.dt if dt is None else dt,
                t0=configuration.t0,
                active_controller=configuration.active_controller if active_controller is None else active_controller,
                viewer=configuration.viewer,
                state_signals=descriptions(configuration.state_signals),
                input_signals=descriptions(configuration.input_signals),
            )
        except (TypeError, ValueError, IndexError, KeyError, ArithmeticError, RuntimeError) as exc:
            raise ValueError(f'invalid configuration {configuration.name!r}: {exc}') from exc


def list_configurations() -> list[str]:
    """List bundled configuration names without importing numerical modules."""
    return sorted(item.name[:-5] for item in resources.files(__package__).iterdir()
                  if item.is_file() and item.name.endswith('.toml'))


def _load_document(text, default_name):
    """Parse TOML text and construct a validated configuration using the supplied fallback name."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f'invalid configuration TOML: {exc}') from exc
    return SimulationConfiguration(**_document_fields(data, default_name))


def load_configuration(name: str) -> SimulationConfiguration:
    """Read a bundled TOML configuration using package resources."""
    _text('configuration name', name)
    available = list_configurations()
    if name not in available:
        raise ValueError(f'unknown configuration {name!r}; available: {", ".join(available)}')
    resource = resources.files(__package__).joinpath(name + '.toml')
    return _load_document(resource.read_text(encoding='utf-8'), name)


def load_configuration_file(path) -> SimulationConfiguration:
    """Read a complete custom TOML file without inheritance or arbitrary imports.

    Required fields are model, parameters, x0 and u0. Missing optional name
    defaults to the file stem. Parsing/schema errors raise ValueError; file
    access errors retain their usual OSError subclasses.
    """
    path = Path(path)
    return _load_document(path.read_text(encoding='utf-8'), path.stem)

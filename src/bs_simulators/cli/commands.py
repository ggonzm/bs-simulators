"""Typer options and routing between inspection and execution."""

from pathlib import Path
from typing import Annotated

import typer

from . import presentation
from .configuration import (_model_function, _model_kind, _parameter_values,
                            _toml_value, _toml_vector)


app = typer.Typer(
    add_completion=False,
    pretty_exceptions_enable=False,
    context_settings={'help_option_names': ['-h', '--help']},
    help='Run an explicitly selected model or inspect its parameters without starting a simulation.',
)



@app.command()
def run(
    port: Annotated[int, typer.Option(
        '--port', min=0, max=65535, help='Optional local HTTP port; 0 selects an available port.',
        rich_help_panel='Execution (optional)',
    )] = 8765,
    dt: Annotated[float | None, typer.Option(
        '--dt', help='Fixed interval in simulated seconds; inherits configuration, or 0.01 for --model.',
        rich_help_panel='Integration',
    )] = None,
    dt_real: Annotated[float | None, typer.Option(
        '--dt-real', help='Optional interval between step deadlines in wall-clock seconds; defaults to dt.',
        rich_help_panel='Execution (optional)',
    )] = None,
    duration: Annotated[float | None, typer.Option(
        '--duration', help='Optional simulated seconds, a multiple of dt; omit to run until stopped.',
        rich_help_panel='Execution (optional)',
    )] = None,
    record: Annotated[Path | None, typer.Option(
        '--record', dir_okay=False, help='Optional new JSONL file; omit to disable recording. Existing files are preserved.',
        rich_help_panel='Execution (optional)',
    )] = None,
    active_controller: Annotated[str | None, typer.Option(
        '--controller', '--active-controller',
        help='Optional controller identity; inherits configuration, or python for --model.',
        rich_help_panel='Execution (optional)',
    )] = None,
    catalogue: Annotated[str | None, typer.Option(
        '--catalogue', help='Explicit bundled configuration name; use --list-catalogue.',
        rich_help_panel='Model selection',
    )] = None,
    config: Annotated[Path | None, typer.Option(
        '--config', exists=True, dir_okay=False, readable=True,
        help='Complete custom TOML file; alternative to --catalogue or --model.',
        rich_help_panel='Model selection',
    )] = None,
    model: Annotated[str | None, typer.Option(
        '--model', help='Public benchmark-systems model; requires --x0, --u0 and physical parameters.',
        rich_help_panel='Model selection',
    )] = None,
    parameter: Annotated[list[str] | None, typer.Option(
        '--parameter', help='Override NAME=TOML_VALUE; repeat for multiple parameters.',
        rich_help_panel='Initial data and parameters',
    )] = None,
    x0: Annotated[str | None, typer.Option(
        '--x0', help='Initial state as a TOML array, e.g. [0.0, 0.0].',
        rich_help_panel='Initial data and parameters',
    )] = None,
    u0: Annotated[str | None, typer.Option(
        '--u0', help='Initial held input as a TOML array, e.g. [1.0].',
        rich_help_panel='Initial data and parameters',
    )] = None,
    list_catalogue: Annotated[bool, typer.Option(
        '--list-catalogue', help='List bundled configurations and exit.',
        rich_help_panel='Inspection',
    )] = False,
    list_models: Annotated[bool, typer.Option(
        '--list-models', help='List public benchmark-systems models and exit.',
        rich_help_panel='Inspection',
    )] = False,
    describe: Annotated[bool, typer.Option(
        '--describe', help='Show selected model parameters, defaults and documentation without running.',
        rich_help_panel='Inspection',
    )] = False,
    stepper: Annotated[str | None, typer.Option(
        '--stepper', help='Integrator as a TOML inline table, e.g. {kind="ode", method="Radau"}.',
        rich_help_panel='Integration',
    )] = None,
) -> None:
    """Select exactly one of --catalogue NAME, --config FILE or --model NAME.

    Inspect parameters with --model pendulum --describe before constructing a run.
    Execution options are optional; their help explains defaults and inherited settings.
    Catalogue and file overrides affect this run only; no configuration is saved.
    """
    # Help and argument parsing stay independent of scientific dependencies.
    from ..catalogue import (list_configurations, load_configuration,
                            load_configuration_file)

    if sum((list_catalogue, list_models, describe)) > 1:
        raise typer.BadParameter('choose only one inspection action: --list-catalogue, --list-models or --describe')
    if list_catalogue:
        presentation.show_catalogue(list_configurations())
        return
    if list_models:
        presentation.show_models()
        return
    if sum(value is not None for value in (catalogue, config, model)) != 1:
        raise typer.BadParameter('select exactly one of --catalogue NAME, --config FILE or --model NAME; use --help for options')

    try:
        configuration = (load_configuration_file(config) if config is not None
                         else load_configuration(catalogue) if catalogue is not None else None)
        parameters = _parameter_values(parameter)
        initial = _toml_vector(x0, '--x0') if x0 is not None else None
        held = _toml_vector(u0, '--u0') if u0 is not None else None
        specification = _toml_value(stepper) if stepper is not None else None
        if stepper is not None and not isinstance(specification, dict):
            raise ValueError('--stepper expects a TOML inline table')
        if describe:
            selected = _model_function(model if model is not None else configuration.model)
            defaults = vars(configuration) if configuration is not None else {
                'parameters': {}, 'x0': None, 'u0': None, 'dt': 0.01, 't0': 0.0,
                'active_controller': 'python', 'stepper': None, 'viewer': None,
            }
            values = dict(defaults['parameters'])
            values.update(parameters)
            presentation.show_description(selected, values,
                      initial=initial if initial is not None else defaults['x0'],
                      held=held if held is not None else defaults['u0'],
                      dt=dt if dt is not None else defaults['dt'],
                      t0=defaults['t0'],
                      controller=active_controller if active_controller is not None else defaults['active_controller'],
                      stepper=specification if specification is not None else defaults['stepper'],
                      viewer=defaults['viewer'])
            return
        if model is not None:
            selected = _model_function(model)
            if initial is None or held is None:
                raise ValueError('--model execution requires both --x0 and --u0; use --describe for model details')
            if _model_kind(selected) == 'dae' and (specification is None or specification.get('kind') != 'dae'):
                raise ValueError('a DAE model requires an explicit --stepper table with kind="dae" and xdot0')
            from ..catalogue import SimulationConfiguration
            configuration = SimulationConfiguration(
                name=model, model=model, parameters=parameters, x0=initial, u0=held,
            )
    except (ValueError, OSError, TypeError, IndexError, ArithmeticError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    from .execution import run_simulation
    run_simulation(configuration, parameters=parameters, initial=initial, held=held,
                   dt=dt, active_controller=active_controller, stepper=specification,
                   port=port, duration=duration, record=record, dt_real=dt_real)

"""Consistent Rich tables and panels for human-facing CLI output."""

import inspect

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .configuration import _model_kind


def _table(title, columns, *, expand=True, min_width=None):
    """Create the shared table style without creating global console state."""
    table = Table(title=title, box=box.ROUNDED, header_style='bold',
                  row_styles=['', 'dim'], expand=expand, min_width=min_width)
    for index, column in enumerate(columns):
        table.add_column(column, style='cyan' if index == 0 else None)
    return table


def _row(table, *values):
    """Add literal cell content, preserving brackets and avoiding markup interpretation."""
    table.add_row(*(Text(str(value)) for value in values))


def show_catalogue(names):
    """Display bundled names without importing numerical dependencies."""
    console = Console(highlight=False)
    table = _table('Available configurations', ['Configuration'], expand=False, min_width=28)
    table.columns[0].no_wrap = True
    for name in names:
        _row(table, name)
    console.print(table)
    console.print(Text('Inspect parameters: --catalogue NAME --describe', style='dim'))


def show_models():
    """Display published numerical model names, equation types and descriptions."""
    from benchmark_systems import models

    console = Console(highlight=False)
    table = _table('Available benchmark models', ['Model', 'Type', 'Description'])
    table.columns[0].no_wrap = True
    table.columns[1].no_wrap = True
    table.columns[2].ratio = 1
    for name in models.__all__:
        model = getattr(models, name)
        kind = _model_kind(model).upper()
        summary = (inspect.getdoc(model) or 'No description available.').splitlines()[0]
        table.add_row(Text(name), Text(kind, style='green' if kind == 'ODE' else 'yellow'),
                      Text(summary))
    console.print(table)
    console.print(Text('Inspect parameters: --model NAME --describe', style='dim'))


def _documentation(model):
    """Style NumPy docstring headings and parameter declarations while retaining all text."""
    lines = (inspect.getdoc(model) or 'No documentation provided.').splitlines()
    text = Text()
    for index, line in enumerate(lines):
        if line and set(line.strip()) == {'-'}:
            continue
        heading = index + 1 < len(lines) and lines[index + 1] and set(lines[index + 1].strip()) == {'-'}
        declaration = line and not line.startswith(' ') and ' : ' in line
        style = 'bold blue' if heading else 'bold cyan' if declaration else ''
        text.append(line + '\n', style=style)
    return text


def show_description(model, parameters, *, initial, held, dt, t0, controller, stepper, viewer):
    """Render model signature, parameter choices, run settings and complete documentation."""
    physical = {name: parameter for name, parameter in inspect.signature(model).parameters.items()
                if parameter.kind is inspect.Parameter.KEYWORD_ONLY and name != 'u'}
    unknown = parameters.keys() - physical.keys()
    if unknown:
        raise ValueError(f'unknown parameters for {model.__name__}: {sorted(unknown)}')
    console = Console(highlight=False)
    signature = Syntax(f'{model.__name__}{inspect.signature(model)}', 'python',
                       theme='ansi_dark', background_color='default', word_wrap=True)
    console.print(Panel(signature, title=f'Model: {model.__name__} ({_model_kind(model).upper()})',
                        border_style='cyan'))
    table = _table('Physical parameters · --parameter NAME=VALUE',
                   ['Parameter', 'Requirement', 'Function default', 'Selected value'])
    for name, parameter in physical.items():
        required = parameter.default is inspect.Parameter.empty
        default = '—' if required else repr(parameter.default)
        selected = repr(parameters[name]) if name in parameters else 'not supplied' if required else default
        _row(table, name, 'Required' if required else 'Optional', default, selected)
    console.print(table)

    settings = _table('Simulation settings', ['Setting', 'Value'])
    default_stepper = ('explicit DAE stepper required' if _model_kind(model) == 'dae' else 'ODE RK45')
    integrator = ('\n'.join(f'{key}: {value}' for key, value in stepper.items())
                  if stepper else default_stepper)
    values = [
        ('Initial state (--x0)', initial if initial is not None else 'required for execution'),
        ('Initial input (--u0)', held if held is not None else 'required for execution'),
        ('Integration interval (--dt)', f'{dt} s'), ('Initial time', f'{t0} s'),
        ('Controller', controller), ('Stepper', integrator),
        ('Viewer', viewer or 'none'),
    ]
    for label, value in values:
        _row(settings, label, value)
    console.print(settings)
    console.print(Panel(_documentation(model), title='Model documentation', border_style='blue'))


def show_started(base, viewer):
    """Show local observation endpoints and the interruption hint in a startup panel."""
    links = Table.grid(padding=(0, 2))
    _row(links, 'Simulation API', f'{base}/api/state')
    if viewer is not None:
        _row(links, 'Viewer', f'{base}/')
    _row(links, 'Stop', 'Ctrl+C')
    Console(highlight=False).print(Panel(links, title='Simulation running', border_style='cyan'))


def show_finished(snapshot, record):
    """Report the terminal state and optional recording after resources have closed."""
    result = Table.grid(padding=(0, 2))
    _row(result, 'Status', snapshot['status'])
    _row(result, 'Simulated time', f"{snapshot['t']:.6g} s")
    if record is not None:
        _row(result, 'Recording', record)
    Console(highlight=False).print(Panel(result, title='Simulation complete', border_style='green'))


def show_error(message):
    """Report a worker failure on stderr without interpreting message markup."""
    Console(stderr=True, highlight=False).print(
        Panel(Text(message), title='Simulation error', border_style='red'))

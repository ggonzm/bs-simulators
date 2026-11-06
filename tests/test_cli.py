"""CLI contracts: real bounded runs, invalid requests and resource cleanup."""

import json
import math
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading

import pytest
from typer.testing import CliRunner

from bs_simulators.cli import app


runner = CliRunner()


def assert_listener_closed(output):
    match = re.search(r'http://127\.0\.0\.1:(\d+)/', output)
    assert match, output
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', int(match.group(1))))


def test_selected_catalogue_records_defaults_and_releases_resources(tmp_path):
    record = tmp_path / 'default.jsonl'
    before = set(threading.enumerate())
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02', '--record', str(record)])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['x'] == pytest.approx([math.pi - 0.1, 0.0])
    assert rows[0]['u'] == [0.0]
    assert rows[0]['parameters'] == {'m': 1.0, 'L': 2.0, 'drag': 0.0}
    assert rows[0]['active_controller'] == 'python'
    assert rows[-1]['status'] == 'finished'
    assert rows[-1]['t'] == pytest.approx(0.02)
    assert rows[-1]['seq'] == 2
    assert_listener_closed(result.output)
    assert not [thread for thread in threading.enumerate()
                if thread not in before and thread.name in ('simulation', 'simulation-http')]


@pytest.mark.parametrize('controller_option', ['--controller', '--active-controller'])
def test_explicit_flags_and_controller_aliases_reach_simulation(tmp_path, monkeypatch, controller_option):
    from bs_simulators import Simulation

    start = Simulation.start
    wall_steps = []

    def capture_start(simulation, **kwargs):
        wall_steps.append(kwargs.get('dt_real'))
        return start(simulation, **kwargs)

    monkeypatch.setattr(Simulation, 'start', capture_start)
    record = tmp_path / 'explicit.jsonl'
    result = runner.invoke(app, [
        '--catalogue', 'pendulum', '--port', '0', '--dt', '0.02', '--duration', '0.04', '--record', str(record),
        '--dt-real', '0.005',
        '--x0', '[-0.3, 0.2]', '--u0', '[0.4]',
        '--parameter', 'm=2', '--parameter', 'L=1.5',
        '--parameter', 'drag=0.25', controller_option, 'python',
    ])
    assert result.exit_code == 0, result.output
    assert wall_steps == [0.005]
    rows = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['x'] == [-0.3, 0.2]
    assert rows[0]['u'] == [0.4]
    assert rows[0]['parameters'] == {'m': 2.0, 'L': 1.5, 'drag': 0.25}
    assert rows[0]['active_controller'] == 'python'
    assert rows[-1]['status'] == 'finished'
    assert rows[-1]['seq'] == 2
    assert rows[-1]['t'] == pytest.approx(0.04)
    assert_listener_closed(result.output)


@pytest.mark.parametrize('help_option', ['--help', '-h'])
def test_help_is_flat_and_has_no_filesystem_side_effects(tmp_path, monkeypatch, help_option):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app, [help_option], terminal_width=150,
        env={'COLUMNS': '150', 'FORCE_COLOR': '1', 'TERM': 'xterm-256color'}, color=True,
    )
    assert result.exit_code == 0, result.output
    output = re.sub(r'\x1b\[[0-9;]*m', '', result.output)
    assert '--duration' in output
    assert '--dt-real' in output
    assert '--controller' in output
    assert '--active-controller' in output
    assert '--x0' in output and '--u0' in output
    for removed in ('--theta0', '--omega0', '--m ', '--length', '--drag', '--inputs'):
        assert removed not in output
    assert 'Commands' not in output
    assert list(tmp_path.iterdir()) == []
    unknown = runner.invoke(app, ['unknown-command'])
    assert unknown.exit_code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('arguments', [
    ['--catalogue', 'pendulum', '--port', '-1'],
    ['--catalogue', 'pendulum', '--port', '65536'],
    ['--catalogue', 'pendulum', '--port', 'not-an-integer'],
    ['--parameter', 'm=nan'],
    ['--parameter', 'L=inf'],
    ['--x0', '[nan, 0]'],
    ['--x0', '[0, -inf]'],
    ['--u0', '[nan]'],
    ['--u0', '0.5'],
    ['--dt', '0'],
    ['--dt', 'nan'],
    ['--dt-real', '0'],
    ['--dt-real', '-1'],
    ['--dt-real', 'nan'],
    ['--dt-real', 'inf'],
    ['--duration', '-1'],
    ['--duration', 'nan'],
    ['--dt', '0.01', '--duration', '0.015'],
])
def test_invalid_options_fail_without_creating_recording(tmp_path, arguments):
    record = tmp_path / 'invalid.jsonl'
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02',
                                 '--record', str(record), *arguments])
    assert result.exit_code == 2, result.output
    assert not record.exists()


def test_existing_recording_is_preserved(tmp_path):
    record = tmp_path / 'existing.jsonl'
    original = b'preserve the existing experiment\n'
    record.write_bytes(original)
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02', '--record', str(record)])
    assert result.exit_code == 2, result.output
    assert record.read_bytes() == original


def test_occupied_port_does_not_start_a_worker_or_create_recording(tmp_path):
    record = tmp_path / 'occupied.jsonl'
    before = set(threading.enumerate())
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', str(occupied.getsockname()[1]),
                                     '--duration', '0.02', '--record', str(record)])
        assert result.exit_code == 2, result.output
    assert not record.exists()
    assert not [thread for thread in threading.enumerate()
                if thread not in before and not thread.daemon]


def test_worker_error_returns_failure_and_still_releases_http_listener(monkeypatch):
    from bs_simulators import catalogue

    class FailedSimulation:
        def __init__(self):
            self.started = False
            self.stopped = False

        def start(self, **kwargs):
            self.started = True

        def wait(self, timeout=None):
            return True

        def stop(self):
            self.stopped = True

        def snapshot(self):
            return {'status': 'error', 'error': 'deliberate worker failure', 'viewer': None}

    simulation = FailedSimulation()
    class FailedConfiguration:
        def create(self, **kwargs):
            return simulation

    monkeypatch.setattr(catalogue, 'load_configuration', lambda name: FailedConfiguration())
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02'])
    assert result.exit_code == 1, result.output
    assert 'deliberate worker failure' in result.output
    assert simulation.started
    assert simulation.stopped
    assert_listener_closed(result.output)


def test_import_and_help_do_not_load_simulation_dependencies():
    script = '''
import sys
from typer.testing import CliRunner
from bs_simulators.cli import app
prefixes = ('bs_simulators.runtime', 'bs_simulators.comms',
            'benchmark_systems', 'scipy', 'numpy')
def assert_lightweight():
    unexpected = [name for name in sys.modules
                  if any(name == prefix or name.startswith(prefix + '.') for prefix in prefixes)]
    assert not unexpected, unexpected
assert_lightweight()
result = CliRunner().invoke(app, ['--help'])
assert result.exit_code == 0, result.output
assert_lightweight()
listed = CliRunner().invoke(app, ['--list-catalogue'])
assert listed.exit_code == 0, listed.output
assert 'pendulum' in listed.output and 'dc-motor' in listed.output
assert_lightweight()
'''
    result = subprocess.run([sys.executable, '-B', '-c', script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_catalogue_listing_does_not_start_an_experiment(tmp_path):
    record = tmp_path / 'not-created.jsonl'
    before = set(threading.enumerate())
    result = runner.invoke(app, ['--list-catalogue', '--record', str(record)])
    assert result.exit_code == 0, result.output
    assert 'pendulum' in result.output
    assert 'dc-motor' in result.output
    assert not record.exists()
    assert not [thread for thread in threading.enumerate() if thread not in before]


def test_cli_motor_catalogue_uses_generic_parameter_and_vector_overrides(tmp_path):
    record = tmp_path / 'motor.jsonl'
    result = runner.invoke(app, [
        '--catalogue', 'dc-motor', '--port', '0', '--duration', '0.02',
        '--record', str(record), '--parameter', 'R=3.0', '--parameter', 'L=0.7',
        '--x0', '[0.1, -0.2, 0.3]', '--u0', '[1.4]',
    ])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
    assert rows[0]['x'] == [0.1, -0.2, 0.3]
    assert rows[0]['u'] == [1.4]
    assert rows[0]['parameters']['R'] == 3
    assert rows[0]['parameters']['L'] == 0.7
    assert rows[0]['viewer'] == 'dc-motor'
    assert rows[-1]['status'] == 'finished'
    assert len(rows[-1]['x']) == 3
    assert rows[-1]['x'] != rows[0]['x']
    assert_listener_closed(result.output)


def write_user_configuration(tmp_path):
    path = tmp_path / 'cart.toml'
    path.write_text('''
model = 'cart_pendulum'
x0 = [0.0, 0.1, 0.0, 0.0]
u0 = [0.2]
dt = 0.02
[parameters]
m = 0.2
M = 1.0
L = 0.5
drag = 0.1
''', encoding='utf-8')
    return path


def test_cli_user_toml_inherits_dt_and_accepts_explicit_overrides(tmp_path):
    path = write_user_configuration(tmp_path)
    original = path.read_bytes()
    record = tmp_path / 'cart.jsonl'
    result = runner.invoke(app, [
        '--config', str(path), '--port', '0', '--duration', '0.04',
        '--record', str(record), '--parameter', 'M=1.7', '--u0', '[0.8]',
        '--controller', 'custom-controller',
    ])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
    assert path.read_bytes() == original
    assert rows[0]['x'] == [0, 0.1, 0, 0]
    assert rows[0]['u'] == [0.8]
    assert rows[0]['parameters']['M'] == 1.7
    assert rows[0]['active_controller'] == 'custom-controller'
    assert rows[-1]['t'] == pytest.approx(0.04)
    assert rows[-1]['seq'] == 2
    assert_listener_closed(result.output)


@pytest.mark.parametrize('arguments', [
    ['--catalogue', 'unknown-configuration'],
    ['--parameter', 'missing-equals'],
    ['--parameter', 'm=not-a-toml-value'],
    ['--parameter', 'invented=1'],
    ['--parameter', "m='not-a-number'"],
    ['--x0', 'not-an-array'],
    ['--x0', '1'],
    ['--x0', '[0]'],
    ['--u0', '[nan]'],
    ['--u0', '[0, 1]'],
    ['--catalogue', 'dc-motor', '--u0', '1'],
])
def test_catalogue_override_errors_do_not_create_recording(tmp_path, arguments):
    record = tmp_path / 'invalid-override.jsonl'
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02',
                                 '--record', str(record), *arguments])
    assert result.exit_code == 2, (result.output, result.exception)
    assert not record.exists()


def test_catalogue_and_user_file_are_mutually_exclusive(tmp_path):
    path = write_user_configuration(tmp_path)
    record = tmp_path / 'conflict.jsonl'
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--config', str(path),
                                 '--port', '0', '--duration', '0.02', '--record', str(record)])
    assert result.exit_code == 2, (result.output, result.exception)
    assert not record.exists()


@pytest.mark.parametrize('option', ['--theta0', '--omega0', '--m', '--length', '--drag'])
def test_model_specific_flags_are_not_part_of_the_cli(tmp_path, option):
    record = tmp_path / 'removed-option.jsonl'
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--port', '0', '--duration', '0.02',
                                 '--record', str(record), option, '1.0'])
    assert result.exit_code == 2, result.output
    assert 'No such option' in result.output
    assert not record.exists()


@pytest.mark.parametrize('arguments', [[], ['--parameter', 'm=2'], ['--x0', '[0,0]']])
def test_execution_requires_explicit_selection_without_starting_worker(tmp_path, arguments):
    record = tmp_path / 'not-started.jsonl'
    before = set(threading.enumerate())
    result = runner.invoke(app, [*arguments, '--record', str(record)])
    assert result.exit_code == 2, result.output
    assert 'select exactly one' in result.output
    assert not record.exists()
    assert set(threading.enumerate()) == before


def test_model_and_other_sources_cannot_be_combined(tmp_path):
    config = write_user_configuration(tmp_path)
    for other in (['--catalogue', 'pendulum'], ['--config', str(config)]):
        result = runner.invoke(app, ['--model', 'pendulum', *other])
        assert result.exit_code == 2, result.output
        assert 'select exactly one' in result.output


def test_model_listing_is_informative_and_does_not_start_an_experiment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(threading.enumerate())
    result = runner.invoke(app, ['--list-models'])
    assert result.exit_code == 0, result.output
    assert 'pendulum' in result.output and 'ODE' in result.output
    assert 'quadrotor_dae' in result.output and 'DAE' in result.output
    assert 'dc_motor' in result.output
    assert set(threading.enumerate()) == before
    assert list(tmp_path.iterdir()) == []


def test_raw_model_help_works_before_parameters_are_supplied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['--model', 'pendulum', '--describe'])
    assert result.exit_code == 0, result.output
    assert re.search(r'\bm\b.*Required.*not supplied', result.output)
    assert re.search(r'\bL\b.*Required.*not supplied', result.output)
    assert re.search(r'\bdrag\b.*Optional.*0\.0', result.output)
    assert 'Mass of the pendulum' in result.output
    assert 'State variables [theta, omega]' in result.output
    assert 'required for execution' in result.output
    assert list(tmp_path.iterdir()) == []


def test_catalogue_help_exposes_selected_values_without_creating_simulation(monkeypatch, tmp_path):
    from bs_simulators.catalogue import SimulationConfiguration

    def unexpected_creation(*args, **kwargs):
        pytest.fail('inspection must not construct a simulation')

    monkeypatch.setattr(SimulationConfiguration, 'create', unexpected_creation)
    record = tmp_path / 'not-created.jsonl'
    result = runner.invoke(app, ['--catalogue', 'pendulum', '--describe',
                                 '--parameter', 'm=2', '--record', str(record)])
    assert result.exit_code == 0, result.output
    assert re.search(r'\bm\b.*Required.*2', result.output)
    assert re.search(r'\bdrag\b.*Optional.*0\.0.*0\.0', result.output)
    assert repr([math.pi - 0.1, 0.0]) in result.output
    assert re.search(r'Viewer.*pendulum', result.output)
    assert not record.exists()


@pytest.mark.parametrize('arguments', [
    ['--model', 'unknown', '--describe'],
    ['--model', 'pendulum'],
    ['--model', 'pendulum', '--x0', '[0,0]'],
    ['--model', 'pendulum', '--x0', '[0,0]', '--u0', '[0]'],
    ['--model', 'pendulum', '--x0', '[0,0]', '--u0', '[0]', '--parameter', 'invented=1'],
    ['--model', 'quadrotor_dae', '--x0', '[0]', '--u0', '[0]'],
    ['--catalogue', 'pendulum', '--stepper', '1'],
    ['--catalogue', 'pendulum', '--stepper', '{kind="ode", method="unknown"}'],
    ['--list-models', '--list-catalogue'],
])
def test_new_selection_and_stepper_errors_preserve_files_and_threads(tmp_path, arguments):
    record = tmp_path / 'invalid-selection.jsonl'
    before = set(threading.enumerate())
    result = runner.invoke(app, [*arguments, '--record', str(record), '--port', '0', '--duration', '0.02'])
    assert result.exit_code == 2, (result.output, result.exception)
    assert not record.exists()
    assert set(threading.enumerate()) == before


def test_raw_model_uses_function_defaults_without_writing_catalogue(tmp_path):
    from importlib.resources import files
    resource = files('bs_simulators.catalogue').joinpath('pendulum.toml')
    original = resource.read_bytes()
    record = tmp_path / 'raw-pendulum.jsonl'
    result = runner.invoke(app, [
        '--model', 'pendulum', '--x0', '[0.2,0]', '--u0', '[0.4]',
        '--parameter', 'm=2', '--parameter', 'L=1.5',
        '--stepper', '{kind="ode", method="DOP853"}',
        '--duration', '0.02', '--port', '0', '--record', str(record),
    ])
    assert result.exit_code == 0, (result.output, result.exception)
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert rows[0]['parameters'] == {'m': 2, 'L': 1.5}
    assert rows[0]['x'] == [0.2, 0]
    assert rows[0]['u'] == [0.4]
    assert rows[0]['viewer'] is None
    assert rows[-1]['status'] == 'finished'
    assert rows[-1]['x'] != rows[0]['x']
    assert resource.read_bytes() == original
    assert_listener_closed(result.output)


def test_raw_dae_model_uses_explicit_stepper(tmp_path):
    record = tmp_path / 'raw-dae.jsonl'
    zeros = '[' + ','.join(['0'] * 12) + ']'
    parameters = {'Ixx': 1.2, 'Iyy': 1.3, 'Izz': 2.1, 'k': 0.9,
                  'L': 0.25, 'm': 2, 'drag': 0}
    arguments = ['--model', 'quadrotor_dae', '--x0', zeros, '--u0', '[5.2,5.2,5.2,5.2]',
                 '--stepper', '{kind="dae", xdot0=' + zeros + '}',
                 '--duration', '0.02', '--port', '0', '--record', str(record)]
    for key, value in parameters.items():
        arguments += ['--parameter', f'{key}={value}']
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0, (result.output, result.exception)
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert rows[-1]['status'] == 'finished'
    assert len(rows[-1]['xdot']) == 12
    assert rows[-1]['t'] == pytest.approx(0.02)
    assert_listener_closed(result.output)

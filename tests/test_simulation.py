"""Acceptance tests for incremental simulation and the standalone SIL demo."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from benchmark_systems.models import pendulum
from bs_simulators import create_pendulum, ODEStepper
from bs_simulators.runtime import core
from bs_simulators.comms.http import create_server



def _ode_stepper(model, x0, *, t0=0.0):
    stepper = ODEStepper()
    stepper.initialize(model, x0, t0=t0, u0=[0.0])
    return stepper


def test_incremental_solver_preserves_absolute_time():
    solver = _ode_stepper(lambda t, x, u: np.array([t]), [2.0], t0=3.0)
    for dt in (0.1, 0.35, 0.2, 0.65):
        solver.step(dt, [0.0])
    assert solver.t == pytest.approx(4.3)
    np.testing.assert_allclose(solver.x, [2.0 + (4.3**2 - 3.0**2) / 2], atol=1e-8)


def test_incremental_solver_integrates_piecewise_constant_inputs():
    solver = _ode_stepper(lambda t, x, u: u, [0.0])
    solver.step(0.25, [2.0])
    solver.step(0.25, [2.0])
    np.testing.assert_allclose(solver.x, [1.0], atol=1e-9)
    solver.step(0.5, [-3.0])
    solver.step(0.25, [-3.0])
    np.testing.assert_allclose(solver.x, [-1.25], atol=1e-9)


def test_incremental_pendulum_matches_piecewise_offline_solution():
    def rhs(t, x, u):
        return pendulum(t, x, m=0.8, L=1.2, drag=0.05, u=u[0])

    initial = np.array([0.4, -0.1])
    solver = _ode_stepper(rhs, initial)
    expected = initial.copy()
    for left, right, torque in ((0, 0.4, 0.0), (0.4, 0.8, 0.7), (0.8, 1.2, -0.2)):
        offline = solve_ivp(
            lambda t, x: rhs(t, x, np.array([torque])),
            (left, right), expected, rtol=1e-10, atol=1e-12,
        )
        assert offline.success
        expected = offline.y[:, -1]
        for _ in range(20):
            solver.step(0.02, [torque])
        np.testing.assert_allclose(solver.x, expected, rtol=2e-6, atol=2e-8)


@pytest.mark.parametrize("dt", [0, -0.1, np.nan, np.inf])
def test_invalid_step_does_not_advance_state(dt):
    solver = _ode_stepper(lambda t, x, u: u, [1.0])
    with pytest.raises(ValueError):
        solver.step(dt, [2.0])
    assert solver.t == 0
    np.testing.assert_array_equal(solver.x, [1.0])


@pytest.mark.parametrize("control", [[1, 2], [np.nan], [np.inf]])
def test_invalid_control_does_not_advance_state(control):
    solver = _ode_stepper(lambda t, x, u: u, [1.0])
    with pytest.raises(ValueError):
        solver.step(0.1, control)
    assert solver.t == 0
    np.testing.assert_array_equal(solver.x, [1.0])


def test_invalid_model_output_does_not_commit_a_partial_step():
    solver = _ode_stepper(lambda t, x, u: [np.nan] if u[0] else [0.0], [1.0])
    with pytest.raises(ValueError):
        solver.step(0.1, [2.0])
    assert solver.t == 0
    np.testing.assert_array_equal(solver.x, [1.0])


def test_stopped_simulation_rejects_commands_and_restart():
    simulation = create_pendulum()
    simulation.stop()
    assert simulation.snapshot()['status'] == 'stopped'
    with pytest.raises(RuntimeError):
        simulation.set_input([0.4])
    with pytest.raises(RuntimeError):
        simulation.start()


def test_recording_preserves_existing_file(tmp_path):
    record = tmp_path / 'existing.jsonl'
    record.write_text('existing evidence\n')
    simulation = create_pendulum()
    with pytest.raises(FileExistsError):
        simulation.start(record=record)
    assert record.read_text() == 'existing evidence\n'
    assert simulation.snapshot()['status'] == 'ready'
    simulation.stop()


def test_snapshots_are_detached_and_acknowledgement_is_not_application():
    simulation = create_pendulum(u0=0.2)
    ack = simulation.set_input([0.8])
    snapshot = simulation.snapshot()
    assert snapshot['u'] == [0.2]
    assert snapshot['input_seq'] == 0
    assert snapshot['pending_input_seq'] == ack['input_seq']
    snapshot['x'][0] = 999
    snapshot['u'][0] = 999
    snapshot['parameters']['L'] = 999
    clean = simulation.snapshot()
    assert clean['x'][0] != 999
    assert clean['u'] == [0.2]
    assert clean['parameters']['L'] == 1.0
    simulation.stop()


def _wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError('condition did not become true before timeout')


def test_zoh_and_recorded_trajectory_match_offline_without_a_viewer(tmp_path):
    record = tmp_path / 'trajectory.jsonl'
    simulation = create_pendulum(dt=0.01, theta0=0.2, u0=0.1)
    simulation.start(duration=0.3, record=record)
    try:
        _wait_for(lambda: simulation.snapshot()['seq'] >= 5)
        command = simulation.set_input([0.6])
        _wait_for(lambda: simulation.snapshot()['status'] == 'finished')
    finally:
        simulation.stop()
    states = [json.loads(line) for line in record.read_text().splitlines()]
    assert states[-1]['status'] == 'finished'
    assert states[-1]['seq'] == states[-2]['seq'] == 30
    assert states[-1]['t'] == states[-2]['t']
    assert [state['seq'] for state in states[:-1]] == list(range(31))
    np.testing.assert_allclose([state['t'] for state in states[:-1]], np.arange(31) * 0.01)
    applied = [state for state in states if state['input_seq'] == command['input_seq']]
    assert len(applied) >= 10
    assert all(state['u'] == [0.6] for state in applied)
    assert all(state['applied_at_sim_time'] == applied[0]['applied_at_sim_time'] for state in applied)
    switch_time = applied[0]['applied_at_sim_time']
    expected = np.array(states[0]['x'])
    for left, right, torque in ((0, switch_time, 0.1), (switch_time, 0.3, 0.6)):
        solution = solve_ivp(
            lambda t, x: pendulum(t, x, m=1, L=1, drag=0.1, u=torque),
            (left, right), expected, rtol=1e-10, atol=1e-12,
        )
        assert solution.success
        expected = solution.y[:, -1]
    np.testing.assert_allclose(states[-1]['x'], expected, rtol=1e-6, atol=1e-8)


def test_command_arriving_during_an_interval_applies_only_to_the_next(monkeypatch, tmp_path):
    # Drive the actual worker with a controlled clock and a command delivered
    # inside its first wait. No scheduling or elapsed-time threshold is used.
    clock = [0.0]
    monkeypatch.setattr(core, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    simulation = create_pendulum(dt=0.1, u0=0.2)
    intervals = []

    def controlled_wait(timeout):
        intervals.append(simulation.snapshot())
        if len(intervals) == 1:
            simulation.set_input([0.8])
        clock[0] += timeout
        return False

    monkeypatch.setattr(simulation._stop_event, 'wait', controlled_wait)
    record = tmp_path / 'boundary.jsonl'
    simulation.start(duration=0.2, record=record)
    try:
        simulation._thread.join(timeout=5)
        assert not simulation._thread.is_alive()
    finally:
        simulation.stop()
    states = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(intervals) == 2
    assert states[1]['seq'] == 1
    assert states[1]['u'] == [0.2]
    assert states[1]['input_seq'] == 0
    assert states[2]['seq'] == 2
    assert states[2]['u'] == [0.8]
    assert states[2]['input_seq'] == 1
    assert states[2]['applied_at_sim_time'] == pytest.approx(0.1)
    assert states[-1]['status'] == 'finished'


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://unrelated.example'},
    {'Host': 'unrelated.example'},
])
def test_http_rejects_nonlocal_writers_without_accepting_input(headers):
    simulation = create_pendulum()
    server = create_server(simulation, port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}'
        request = Request(base + '/api/input', data=b'{"u":[2.0]}',
                          headers={'Content-Type': 'application/json', **headers})
        with pytest.raises(HTTPError) as exc:
            urlopen(request, timeout=2)
        assert exc.value.code == 403
        exc.value.close()
        assert simulation.snapshot()['pending_input_seq'] is None
        assert simulation.snapshot()['u'] == [0.0]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        simulation.stop()


def _get_state(base):
    with urlopen(base + '/api/state', timeout=2) as response:
        return json.load(response)


def test_real_server_accepts_external_controller_and_runs_without_viewer(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    record = tmp_path / 'server.jsonl'
    process = subprocess.Popen(
        [sys.executable, '-m', 'bs_simulators', '--port', str(port),
         '--dt', '0.01', '--duration', '2', '--record', str(record)],
        cwd=Path(__file__).parents[1], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    slow_client = None
    try:
        def ready():
            if process.poll() is not None:
                raise AssertionError(f'simulator exited early: {process.communicate()}')
            try:
                return _get_state(base)
            except (URLError, TimeoutError):
                return False

        initial = _wait_for(ready)
        # A separate process acts as controller, then exits immediately.
        controller = subprocess.run(
            [sys.executable, '-c',
             'import json,sys; from urllib.request import Request,urlopen; '
             'r=urlopen(Request(sys.argv[1]+"/api/input",data=b\'{"u":[0.75]}\','
             'headers={"Content-Type":"application/json"}),timeout=2); '
             'print(r.read().decode())', base],
            capture_output=True, text=True, timeout=10, check=True,
        )
        ack = json.loads(controller.stdout)
        assert ack['input_seq'] >= 1

        def applied():
            state = _get_state(base)
            return state if state['input_seq'] == ack['input_seq'] else False

        first_applied = _wait_for(applied)
        assert first_applied['u'] == [0.75]
        assert first_applied['t'] > initial['t']

        for invalid in ({'u': [1, 2]}, {'u': [float('nan')]}, {'u': 'bad'}):
            request = Request(base + '/api/input', data=json.dumps(invalid).encode(),
                              headers={'Content-Type': 'application/json'})
            with pytest.raises(HTTPError) as exc:
                urlopen(request, timeout=2)
            assert exc.value.code == 400
            exc.value.close()

        # An unfinished HTTP request must not block integration or other readers.
        slow_client = socket.create_connection(('127.0.0.1', port), timeout=2)
        slow_client.sendall(b'GET /api/state HTTP/1.1\r\nHost: localhost\r\n')
        time.sleep(0.15)
        later = _get_state(base)
        assert later['seq'] > first_applied['seq']
        assert later['input_seq'] == ack['input_seq']
        assert later['u'] == [0.75]
        assert np.linalg.norm(np.array(later['x']) - first_applied['x']) > 1e-5
        slow_client.close()
        slow_client = None
        # Finish with neither controller nor viewer connected.
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
    finally:
        if slow_client is not None:
            slow_client.close()
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
    states = [json.loads(line) for line in record.read_text().splitlines()]
    assert states[-1]['seq'] == 200
    assert states[-1]['t'] == pytest.approx(2.0)
    assert states[-1]['input_seq'] == ack['input_seq']
    assert states[-1]['u'] == [0.75]
    assert states[-1]['status'] == 'finished'
    assert all(state['lag'] >= 0 for state in states)

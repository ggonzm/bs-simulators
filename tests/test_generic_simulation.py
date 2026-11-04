"""Model-independent contracts, checked against analytic and offline solutions."""

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from benchmark_systems import models
from bs_simulators.comms.http import create_server
from bs_simulators.runtime.core import Simulation
from bs_simulators.runtime.signals import SignalDescriptor
from bs_simulators.runtime.steppers import ODEStepper, DAEStepper


def finish(simulation, duration, **kwargs):
    """Bound worker lifetime without requiring a particular execution speed."""
    simulation.start(duration=duration, **kwargs)
    if not simulation.wait(timeout=5):
        simulation.stop()
        pytest.fail('worker did not finish its bounded experiment')
    state = simulation.snapshot()
    assert state['status'] == 'finished', state
    return state


def test_real_three_state_motor_matches_offline_solution():
    parameters = dict(J=0.02, b=0.1, Ke=0.1, Kt=0.1, R=2.0, L=0.5)
    x0 = [0.2, -0.1, 0.3]
    expected = solve_ivp(
        lambda t, x: models.dc_motor(t, x, u=1.4, **parameters),
        (0, 0.04), x0, rtol=1e-11, atol=1e-13,
    )
    simulation = Simulation(models.dc_motor, stepper=ODEStepper(), x0=x0,
                            u0=[1.4], parameters=parameters)
    state = finish(simulation, 0.04)
    np.testing.assert_allclose(state['x'], expected.y[:, -1], rtol=2e-6, atol=2e-9)
    assert state['seq'] == 4
    assert set(simulation.read_measurements()) >= {'x0', 'x1', 'x2', 'u0'}


def test_nonzero_initial_time_uses_absolute_rhs_and_relative_duration():
    simulation = Simulation(lambda t, x: [t], stepper=ODEStepper(),
                            x0=[2.0], u0=[], t0=13.0, dt=0.01)
    assert simulation.snapshot()['applied_at_sim_time'] == 13.0
    state = finish(simulation, 0.02)
    assert state['t'] == pytest.approx(13.02)
    assert state['x'] == pytest.approx([2 + (13.02**2 - 13**2) / 2])
    assert state['seq'] == 2


def test_multi_input_model_receives_complete_vector():
    simulation = Simulation(lambda t, x, *, u: [u[0] - 2*u[1]],
                            stepper=ODEStepper(), x0=[4], u0=[3, -2])
    state = finish(simulation, 0.03)
    assert state['x'] == pytest.approx([4.21])
    assert state['u'] == [3, -2]


def test_named_input_mapping_is_not_restricted_to_u_keyword():
    simulation = Simulation(lambda t, x, *, force, bias: [force + bias],
                            stepper=ODEStepper(), x0=[0], u0=[2, 3],
                            input_map=lambda u: {'force': u[0], 'bias': u[1]})
    assert finish(simulation, 0.02)['x'] == pytest.approx([0.1])


def test_nested_parameter_values_are_copied_before_model_execution():
    parameters = {'configuration': {'gains': np.array([2.0, 3.0])}}
    simulation = Simulation(lambda t, x, *, configuration, u: [sum(configuration['gains'])*u],
                            stepper=ODEStepper(), x0=[0], u0=[1], parameters=parameters)
    parameters['configuration']['gains'][:] = 100
    exposed = simulation.snapshot()
    exposed['parameters']['configuration']['gains'][0] = 100
    state = finish(simulation, 0.02)
    assert state['x'] == pytest.approx([0.1])
    assert state['parameters'] == {'configuration': {'gains': [2.0, 3.0]}}


def test_initial_data_metadata_and_snapshots_are_detached():
    x0, u0 = np.array([1.0]), np.array([2.0])
    parameters = {'gain': 3.0}
    state_signals = [SignalDescriptor('level', 'm', 'float64', 'output')]
    input_signals = [SignalDescriptor('flow', 'm3/s', 'float64', 'input')]
    simulation = Simulation(lambda t, x, *, gain, u: [gain*u],
                            stepper=ODEStepper(), x0=x0, u0=u0, parameters=parameters,
                            state_signals=state_signals, input_signals=input_signals)
    x0[:] = 100
    u0[:] = 100
    parameters['gain'] = 100
    state_signals.clear()
    input_signals.clear()
    snapshot = simulation.snapshot()
    snapshot['x'][0] = -100
    snapshot['u'][0] = -100
    snapshot['parameters']['gain'] = -100
    descriptions = simulation.describe_signals()
    descriptions[0]['name'] = 'corrupted'
    state = finish(simulation, 0.02)
    assert state['x'] == pytest.approx([1.12])
    assert state['parameters']['gain'] == 3
    measurements = simulation.read_measurements()
    assert measurements['level'] == pytest.approx(1.12)
    assert measurements['flow'] == 2
    assert 'corrupted' not in {item['name'] for item in simulation.describe_signals()}


@pytest.mark.parametrize('state_signals,input_signals', [
    ([SignalDescriptor('a', '', 'float64', 'input')], None),
    (None, [SignalDescriptor('b', '', 'float64', 'output')]),
    ([SignalDescriptor('a', '', 'float64', 'output'),
      SignalDescriptor('b', '', 'float64', 'output')], None),
])
def test_signal_directions_and_lengths_are_validated(state_signals, input_signals):
    with pytest.raises(ValueError):
        Simulation(lambda t, x, *, u: [u], stepper=ODEStepper(), x0=[0], u0=[0],
                   state_signals=state_signals, input_signals=input_signals)


def test_complete_multi_input_command_rejects_invalid_component_atomically():
    simulation = Simulation(lambda t, x, *, u: [sum(u)], stepper=ODEStepper(),
                            x0=[0], u0=[0, 0], input_signals=[
                                SignalDescriptor('a', '', 'float64', 'input', -1, 1),
                                SignalDescriptor('b', '', 'float64', 'input', -2, 2),
                            ])
    simulation.submit_input([0.5, 1.0], controller_id='python')
    before = simulation.snapshot()
    with pytest.raises(ValueError):
        simulation.submit_input([-0.5, 3.0], controller_id='python')
    after = simulation.snapshot()
    assert after == before
    state = finish(simulation, 0.01)
    assert state['u'] == [0.5, 1.0]
    assert state['x'] == pytest.approx([0.015])


def test_generic_http_serves_model_data_without_guessing_pendulum_viewer():
    simulation = Simulation(lambda t, x, *, u: [u[0], u[1], u[0] - u[1]],
                            stepper=ODEStepper(), x0=[1, 2, 3], u0=[0, 0],
                            active_controller='http')
    server = create_server(simulation, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/api/state', timeout=2) as response:
            state = json.load(response)
        assert state['x'] == [1, 2, 3]
        assert state['u'] == [0, 0]
        assert state['viewer'] is None
        with urlopen(base + '/api/signals', timeout=2) as response:
            signals = json.load(response)
        assert {signal['name'] for signal in signals} >= {'x0', 'x1', 'x2', 'u0', 'u1'}
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/', timeout=2)
        assert error.value.code == 404
        error.value.close()
        request = Request(base + '/api/input', data=json.dumps({'u': [2, 5]}).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=2) as response:
            assert response.status == 202
            ack = json.load(response)
        state = finish(simulation, 0.01)
        assert state['input_seq'] == ack['accepted_input_seq']
        assert state['u'] == [2, 5]
        assert state['x'] == pytest.approx([1.02, 2.05, 2.97])
    finally:
        simulation.stop()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class CustomStepper:
    """Structural implementation deliberately unrelated to built-in steppers."""
    def __init__(self):
        self.t, self.x, self.xdot = 0.0, np.array([]), None

    def initialize(self, model, x0, *, t0=0, u0):
        self.model = model
        self.t, self.x, self.xdot = t0, np.array(x0, dtype=float), None
        self.calls = []

    def step(self, dt, u):
        self.calls.append((self.t, list(u)))
        new_x = self.x + dt*np.asarray(self.model(self.t, self.x, u))
        self.t, self.x = self.t + dt, new_x
        return new_x.copy()


def test_structural_custom_stepper_is_actually_used():
    stepper = CustomStepper()
    simulation = Simulation(lambda t, x, *, u: [u], stepper=stepper,
                            x0=[8], u0=[2], t0=1)
    state = finish(simulation, 0.02)
    assert state['x'] == pytest.approx([8.04])
    assert stepper.calls == [(1, [2]), (1.01, [2])]


def test_builtin_ode_step_failure_and_external_reads_preserve_committed_state():
    fail = False

    def model(t, x, u):
        if fail:
            raise RuntimeError('deliberate second-interval failure')
        return np.array([u[0]])

    stepper = ODEStepper()
    stepper.initialize(model, [1], t0=2, u0=[3])
    stepper.step(0.1, [3])
    before = stepper.x.copy()
    exposed = stepper.x
    exposed[:] = 100
    np.testing.assert_array_equal(stepper.x, before)
    fail = True
    with pytest.raises(RuntimeError, match='deliberate'):
        stepper.step(0.1, [9])
    assert stepper.t == pytest.approx(2.1)
    np.testing.assert_array_equal(stepper.x, before)
    assert stepper.xdot is None


@pytest.mark.parametrize('factory', [ODEStepper, lambda: DAEStepper([0])])
def test_stepper_cannot_be_shared_by_two_simulations(factory):
    stepper = factory()
    ode = lambda t, x, *, u: [u]
    dae = lambda t, x, xdot, *, u: [xdot[0] - u]
    model = dae if isinstance(stepper, DAEStepper) else ode
    Simulation(model, stepper=stepper, x0=[0], u0=[0])
    with pytest.raises((ValueError, RuntimeError)):
        Simulation(model, stepper=stepper, x0=[0], u0=[0])


def test_failed_interval_does_not_commit_state_or_command():
    def broken(t, x, *, u):
        if u != 0:
            raise RuntimeError('deliberate integration failure')
        return [0]
    simulation = Simulation(broken, stepper=ODEStepper(), x0=[4], u0=[0])
    before = simulation.snapshot()
    simulation.submit_input([2], controller_id='python')
    simulation.start(duration=0.01)
    completed = simulation.wait(timeout=5)
    try:
        assert completed
        state = simulation.snapshot()
        assert state['status'] == 'error'
        for key in ('t', 'x', 'u', 'seq', 'input_seq'):
            assert state[key] == before[key]
    finally:
        simulation.stop()


def index_one(t, x, xdot, u):
    return np.array([xdot[0] + x[0] - u[0], x[1] - x[0] - u[0]])


def test_index_one_dae_reconciles_algebraic_state_at_input_boundary():
    stepper = DAEStepper([0, 0], fixed_x=[0], rtol=1e-8, atol=1e-10)
    stepper.initialize(index_one, [1, 1], u0=[0])
    stepper.step(0.01, [0])
    before = stepper.x.copy()
    stepper.step(0.01, [2])
    expected = 2 + (before[0] - 2)*np.exp(-0.01)
    assert stepper.x[0] == pytest.approx(expected, abs=2e-7)
    assert stepper.x[1] == pytest.approx(expected + 2, abs=2e-7)
    assert abs(stepper.x[0] - before[0]) < 0.02
    assert stepper.x[1] - before[1] > 1.9
    assert np.linalg.norm(index_one(stepper.t, stepper.x, stepper.xdot, [2]), np.inf) < 1e-6


def test_dae_failed_consistency_is_atomic():
    def residual(t, x, xdot, u):
        return index_one(t, x, xdot, u) if u[0] == 0 else np.ones(2)
    stepper = DAEStepper([0, 0], fixed_x=[0])
    stepper.initialize(residual, [1, 1], u0=[0])
    before = (stepper.t, stepper.x.copy(), stepper.xdot.copy())
    with pytest.raises((ValueError, RuntimeError)):
        stepper.step(0.01, [1])
    assert stepper.t == before[0]
    np.testing.assert_array_equal(stepper.x, before[1])
    np.testing.assert_array_equal(stepper.xdot, before[2])


def test_runtime_dae_reports_derivatives_and_generic_measurements():
    def residual(t, x, xdot, *, u):
        return index_one(t, x, xdot, [u])
    simulation = Simulation(residual, stepper=DAEStepper([0, 0], fixed_x=[0]),
                            x0=[1, 3], u0=[2])
    state = finish(simulation, 0.02)
    expected = 2 - np.exp(-0.02)
    assert state['x'] == pytest.approx([expected, expected + 2], abs=1e-6)
    assert np.linalg.norm(residual(state['t'], state['x'], state['xdot'], u=2), np.inf) < 1e-6
    measurements = simulation.read_measurements()
    assert measurements['x0'] == pytest.approx(expected, abs=1e-6)
    assert measurements['x1'] == pytest.approx(expected + 2, abs=1e-6)


def test_real_quadrotor_dae_matches_ode_away_from_hover():
    parameters = dict(Ixx=1.2, Iyy=1.3, Izz=2.1, k=0.9, L=0.25, m=2.0, drag=0.0)
    x0 = np.zeros(12)
    x0[2] = 1
    u = np.array([5.2, 5.2, 5.2, 5.2])
    # Zero attitude and equal rotors avoid different Euler/body-rate conventions.
    expected = solve_ivp(lambda t, x: models.quadrotor_ode(t, x, u=u, **parameters),
                         (0, 0.02), x0, rtol=1e-10, atol=1e-12)
    simulation = Simulation(models.quadrotor_dae, stepper=DAEStepper(np.zeros(12)),
                            x0=x0, u0=u, parameters=parameters)
    state = finish(simulation, 0.02)
    np.testing.assert_allclose(state['x'], expected.y[:, -1], rtol=1e-5, atol=1e-7)
    assert np.linalg.norm(models.quadrotor_dae(state['t'], state['x'], state['xdot'],
                                              u=u, **parameters), np.inf) < 1e-6


def test_default_rk45_steppers_are_independent_per_simulation(monkeypatch):
    from bs_simulators.runtime import steppers

    methods = []
    solve = steppers.solve_ivp

    def capture_method(*args, **kwargs):
        methods.append(kwargs['method'])
        return solve(*args, **kwargs)

    monkeypatch.setattr(steppers, 'solve_ivp', capture_method)
    model = lambda t, x, *, u: [u]
    first = Simulation(model, x0=[0], u0=[1])
    second = Simulation(model, x0=[10], u0=[2], stepper=None)
    assert finish(first, 0.01)['x'] == pytest.approx([0.01])
    assert second.snapshot()['x'] == [10]
    assert finish(second, 0.01)['x'] == pytest.approx([10.02])
    assert first.snapshot()['x'] == pytest.approx([0.01])
    assert methods == ['RK45', 'RK45']

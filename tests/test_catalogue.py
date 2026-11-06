"""Optional configurations preserve the model-independent simulation API."""

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from benchmark_systems import models
from bs_simulators.catalogue import (
    list_configurations, load_configuration, load_configuration_file,
)


CART_CONFIGURATION = '''
name = 'custom-cart'
model = 'cart_pendulum'
x0 = [0.0, 0.15, 0.0, 0.0]
u0 = [0.4]
dt = 0.01
active_controller = 'python'
[parameters]
m = 0.2
M = 1.0
L = 0.5
drag = 0.1
[stepper]
kind = 'ode'
method = 'DOP853'
rtol = 1e-9
atol = 1e-11
'''


def write_configuration(tmp_path, content=CART_CONFIGURATION):
    path = tmp_path / 'experiment.toml'
    path.write_text(content, encoding='utf-8')
    return path


def finish(simulation, duration=0.02):
    simulation.start(duration=duration)
    if not simulation.wait(timeout=5):
        simulation.stop()
        pytest.fail('bounded configured simulation did not finish')
    state = simulation.snapshot()
    assert state['status'] == 'finished', state
    return state


def test_packaged_catalogue_loads_independently_of_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert {'pendulum', 'dc-motor'} <= set(list_configurations())
    pendulum = load_configuration('pendulum').create()
    motor = load_configuration('dc-motor').create()
    assert len(pendulum.snapshot()['x']) == 2
    assert len(motor.snapshot()['x']) == 3
    assert motor.snapshot()['active_controller'] == 'python'
    assert motor.snapshot()['viewer'] == 'dc-motor'
    assert list(tmp_path.iterdir()) == []


def test_configuration_overrides_and_returned_data_do_not_mutate_defaults():
    configuration = load_configuration('pendulum')
    baseline = configuration.create().snapshot()
    overrides, x0, u0 = {'m': 2.5}, [0.2, -0.1], [0.4]
    first = configuration.create(parameters=overrides, x0=x0, u0=u0,
                                 dt=0.02, active_controller='custom-controller')
    overrides['m'], x0[0], u0[0] = 100, 100, 100
    state = first.snapshot()
    assert state['parameters'] == {**baseline['parameters'], 'm': 2.5}
    assert state['x'] == [0.2, -0.1]
    assert state['u'] == [0.4]
    assert state['active_controller'] == 'custom-controller'
    state['parameters']['m'] = -1
    second = configuration.create()
    assert second is not first
    assert second.snapshot()['parameters'] == baseline['parameters']
    assert second.snapshot()['x'] == baseline['x']
    assert second.snapshot()['u'] == baseline['u']
    assert load_configuration('pendulum').create().snapshot()['parameters'] == baseline['parameters']
    assert finish(first, 0.02)['seq'] == 1
    assert finish(second, 0.02)['seq'] == 2


def test_motor_configuration_matches_public_three_state_model():
    simulation = load_configuration('dc-motor').create(
        parameters={'R': 3.0}, x0=[0.1, -0.2, 0.3], u0=[1.2],
    )
    initial = simulation.snapshot()
    expected = solve_ivp(
        lambda t, x: models.dc_motor(t, x, u=1.2, **initial['parameters']),
        (0, 0.02), initial['x'], rtol=1e-11, atol=1e-13,
    )
    state = finish(simulation)
    np.testing.assert_allclose(state['x'], expected.y[:, -1], rtol=2e-6, atol=2e-9)
    assert state['parameters']['R'] == 3


def test_unknown_catalogue_entry_reports_a_validation_error():
    with pytest.raises(ValueError, match='unknown|Unknown|configuration|catalogue'):
        load_configuration('not-a-packaged-model')


def test_unknown_model_parameter_is_rejected_before_running():
    with pytest.raises(ValueError, match='invented|parameter|keyword'):
        load_configuration('pendulum').create(parameters={'invented': 1})


def test_user_toml_can_select_a_public_model_outside_packaged_catalogue(tmp_path):
    configuration = load_configuration_file(write_configuration(tmp_path))
    assert configuration.name == 'custom-cart'
    assert configuration.model == 'cart_pendulum'
    simulation = configuration.create()
    initial = simulation.snapshot()
    expected = solve_ivp(
        lambda t, x: models.cart_pendulum(t, x, u=0.4, **initial['parameters']),
        (0, 0.02), initial['x'], rtol=1e-11, atol=1e-13,
    )
    state = finish(simulation)
    assert state['viewer'] is None
    np.testing.assert_allclose(state['x'], expected.y[:, -1], rtol=2e-7, atol=2e-10)


def test_configured_dae_gets_a_fresh_integrator_for_each_simulation(tmp_path):
    path = write_configuration(tmp_path, '''
model = 'double_cart_pendulum'
x0 = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
u0 = [0.2]
[parameters]
M = 0.7
m1 = 0.2
m2 = 0.15
L1 = 0.5
L2 = 0.4
[stepper]
kind = 'dae'
method = 'Radau'
xdot0 = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
''')
    configuration = load_configuration_file(path)
    first, second = configuration.create(), configuration.create()
    before = second.snapshot()
    assert 'xdot' in before
    final_first = finish(first)
    assert second.snapshot() == before
    final_second = finish(second)
    assert final_second['t'] == pytest.approx(0.02)
    np.testing.assert_allclose(final_first['x'], final_second['x'], rtol=1e-10, atol=1e-12)
    residual = models.double_cart_pendulum(
        final_second['t'], final_second['x'], final_second['xdot'],
        u=0.2, **final_second['parameters'],
    )
    assert np.linalg.norm(residual, np.inf) < 1e-5


@pytest.mark.parametrize('content', [
    CART_CONFIGURATION.replace("model = 'cart_pendulum'", "model = 'builtins.eval'"),
    CART_CONFIGURATION.replace('drag = 0.1', 'invented = 0.1'),
    CART_CONFIGURATION.replace('x0 = [0.0, 0.15, 0.0, 0.0]', 'x0 = [0.0]'),
    CART_CONFIGURATION.replace("kind = 'ode'", "kind = 'not-an-integrator'"),
    CART_CONFIGURATION + '\n[[input_signals]]\nname="force"\nunit="N"\ndtype="float64"\ndirection="output"\n',
])
def test_invalid_user_configuration_is_rejected_before_running(tmp_path, content):
    with pytest.raises(ValueError):
        load_configuration_file(write_configuration(tmp_path, content)).create()


def test_user_signal_metadata_maps_by_position(tmp_path):
    content = CART_CONFIGURATION + '''
[[input_signals]]
name = 'force'
unit = 'N'
dtype = 'float64'
direction = 'input'
lower = -2.0
upper = 2.0
'''
    simulation = load_configuration_file(write_configuration(tmp_path, content)).create()
    assert simulation.read_measurements()['force'] == 0.4
    descriptor = next(item for item in simulation.describe_signals() if item['name'] == 'force')
    assert descriptor['lower'] == -2
    assert descriptor['upper'] == 2
    simulation.set_input([3])
    simulation.start(duration=simulation.dt)
    assert simulation.wait(timeout=5)
    assert simulation.snapshot()['input_saturated'] == [True]
    assert simulation.read_measurements()['force'] == 2

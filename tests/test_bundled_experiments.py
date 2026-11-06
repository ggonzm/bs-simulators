"""Three complete model/viewer paths and nonlinear neutralization consistency."""

import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from benchmark_systems.models import neutralization
from bs_simulators import load_configuration, DAEStepper
from bs_simulators.comms.http import create_server


def test_neutralization_matches_reduced_ode_and_independent_ph_root():
    configuration = load_configuration('neutralization')
    simulation = configuration.create()
    initial = simulation.snapshot()
    assert initial['x'][:3] == [-0.000432, 0.000528, 14.0]
    assert initial['x'][3] == pytest.approx(7.00188107, abs=1e-7)
    parameters = simulation.parameters
    simulation.stop()
    options = dict(configuration.stepper)
    options.pop('kind')
    stepper = DAEStepper(**options)
    stepper.initialize(lambda t, x, dx, u: neutralization(t, x, dx, u=u[0], **parameters),
                       initial['x'], u0=[15.6])
    expected = np.array(initial['x'][:3])
    for left, right, flow in [(0, 10, 15.6), (10, 20, 17.0)]:
        reference = solve_ivp(
            lambda t, x: -neutralization(t, [*x, 7.0], np.zeros(4),
                                         u=flow, **parameters)[:3],
            (left, right), expected, rtol=1e-11, atol=1e-13,
        )
        assert reference.success
        expected = reference.y[:, -1]
        for _ in range(10):
            stepper.step(1.0, [flow])
            residual = neutralization(stepper.t, stepper.x, stepper.xdot,
                                      u=flow, **parameters)
            assert np.max(np.abs(residual)) < 1e-8
        ph = brentq(lambda value: neutralization(right, [*expected, value], np.zeros(4),
                                                u=flow, **parameters)[3], -2, 16)
        np.testing.assert_allclose(stepper.x[:3], expected, rtol=2e-6, atol=2e-10)
        assert stepper.x[3] == pytest.approx(ph, abs=2e-5)
    assert abs(stepper.x[3] - initial['x'][3]) > 0.01
    simulation.stop()


@pytest.mark.parametrize('name,size,command', [
    ('pendulum', 2, [0.75]), ('dc-motor', 3, [2.0]), ('neutralization', 4, [17.0]),
])
def test_three_scenes_serve_assets_and_apply_external_inputs(name, size, command):
    simulation = load_configuration(name).create(active_controller='http')
    server = create_server(simulation, port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    simulation.start()
    worker.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        with urlopen(base + '/', timeout=3) as response:
            assert f'data-viewer="{name}"'.encode() in response.read()
        for asset, mime in [('viewer.js', 'text/javascript'), ('scenes.js', 'text/javascript'),
                            ('viewer.css', 'text/css')]:
            with urlopen(base + '/assets/' + asset, timeout=3) as response:
                assert response.headers.get_content_type() == mime
                assert response.read()
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/assets/../catalogue/pendulum.toml', timeout=3)
        assert error.value.code == 404
        error.value.close()
        request = Request(base + '/api/input', data=json.dumps({'u': command}).encode(),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=3) as response:
            assert response.status == 202
            ack = json.load(response)
        deadline = time.monotonic() + 5
        while True:
            with urlopen(base + '/api/state', timeout=3) as response:
                state = json.load(response)
            if state['input_seq'] == ack['accepted_input_seq']:
                break
            assert state['status'] == 'running', state
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert len(state['x']) == size
        assert state['u'] == command
        assert state['viewer'] == name
        if name == 'neutralization':
            assert len(state['xdot']) == 4
            residual = neutralization(state['t'], state['x'], state['xdot'],
                                      u=state['u'][0], **state['parameters'])
            assert abs(residual[3]) < 1e-8
    finally:
        simulation.stop()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

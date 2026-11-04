"""Transport-independent controller protocols and measurement acceptance tests."""

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest

import bs_simulators as simulation_api
from bs_simulators import InMemoryAdapter, create_pendulum, Simulation, SimulationPort
from bs_simulators.runtime import core
from bs_simulators.comms.http import create_server


@pytest.fixture
def clock(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(core, 'time', SimpleNamespace(monotonic=lambda: current[0]))
    return current


def test_public_interface_has_no_unpaced_stepper():
    assert 'Simulation' in simulation_api.__all__
    assert 'ODEStepper' in simulation_api.__all__
    assert isinstance(create_pendulum(), Simulation)
    assert not hasattr(simulation_api, 'PendulumSimulation')
    assert 'step' not in dir(create_pendulum())


def test_signal_metadata_and_named_measurements():
    simulation = create_pendulum(theta0=0.2, omega0=-0.3, u0=0.4,
                                    active_controller='python')
    adapter = InMemoryAdapter(simulation, controller_id='python')
    metadata = {signal['name']: signal for signal in adapter.describe_signals()}
    assert set(metadata) == {'theta', 'omega', 'torque', 'time', 'status'}
    assert metadata['theta']['unit'] == 'rad'
    assert metadata['omega']['unit'] == 'rad/s'
    assert metadata['time']['unit'] == 's'
    for signal in metadata.values():
        assert isinstance(signal['dtype'], str) and signal['dtype']
        assert 'lower' in signal and 'upper' in signal
        assert signal['direction'] in ('input', 'output')
    assert metadata['torque']['direction'] == 'input'
    measured = adapter.read_measurements()
    assert measured['theta'] == pytest.approx(0.2)
    assert measured['omega'] == pytest.approx(-0.3)
    assert measured['torque'] == pytest.approx(0.4)
    assert measured['time'] == 0
    assert measured['status'] == 'ready'
    ack = adapter.write_signals({'torque': -0.6})
    snapshot = adapter.read_snapshot()
    assert snapshot['pending_input_seq'] == ack['accepted_input_seq']
    assert snapshot['u'] == [0.4]
    simulation.stop()


@pytest.mark.parametrize('mapping', [{}, {'theta': 1}, {'torque': 1, 'theta': 2},
                                     {'torque': np.nan}, {'torque': '0.1'}])
def test_named_mapping_requires_complete_valid_input_frame(mapping):
    simulation = create_pendulum(active_controller='python')
    adapter = InMemoryAdapter(simulation, controller_id='python')
    accepted = adapter.write_signals({'torque': 0.5})
    before = simulation.snapshot()
    with pytest.raises(ValueError):
        adapter.write_signals(mapping)
    after = simulation.snapshot()
    assert after['received_input_seq'] == before['received_input_seq'] == accepted['accepted_input_seq']
    assert after['pending_input_seq'] == before['pending_input_seq']
    assert after['u'] == before['u']
    assert after['seq'] == before['seq']
    simulation.stop()


def test_active_controller_is_explicit_and_other_adapter_cannot_override(clock):
    simulation = create_pendulum(active_controller='plc')
    owner = InMemoryAdapter(simulation, controller_id='plc')
    stranger = InMemoryAdapter(simulation, controller_id='http')
    accepted = owner.write_inputs([0.5])
    clock[0] += 100.0
    before = simulation.snapshot()
    with pytest.raises(PermissionError):
        stranger.write_inputs([-9.0])
    after = simulation.snapshot()
    assert after['active_controller'] == 'plc'
    assert after['received_input_seq'] == accepted['accepted_input_seq']
    assert after['pending_input_seq'] == before['pending_input_seq']
    assert after['u'] == before['u']
    assert after['received_at_monotonic'] == before['received_at_monotonic']
    simulation.stop()


def test_explicit_controller_selection_clears_prior_queued_input_and_receipt_metadata():
    simulation = create_pendulum(active_controller='http', u0=0.2)
    previous = InMemoryAdapter(simulation, controller_id='http')
    previous.write_inputs([0.9])
    simulation.configure_controller('plc')
    switched = simulation.snapshot()
    assert switched['active_controller'] == 'plc'
    assert switched['pending_input_seq'] is None
    assert switched['received_input_seq'] == 0
    assert switched['u'] == [0.2]
    with pytest.raises(PermissionError):
        previous.write_inputs([0.3])
    simulation.stop()
    with pytest.raises(RuntimeError):
        simulation.configure_controller('http')


@pytest.mark.parametrize('kwargs', [
    {'source_timestamp': '2020-01-01T12:00:00'},
    {'source_timestamp': '2020-01-01T12:00:00+02:00'},
    {'source_timestamp': 'nonsense'},
])
def test_invalid_source_time_leaves_received_and_held_input_unchanged(kwargs):
    simulation = create_pendulum(active_controller='python', u0=0.2)
    adapter = InMemoryAdapter(simulation, controller_id='python')
    adapter.write_inputs([0.6], source_timestamp='2020-01-01T00:00:00Z')
    before = adapter.read_snapshot()
    with pytest.raises(ValueError):
        adapter.write_inputs([-4.0], **kwargs)
    after = adapter.read_snapshot()
    for key in ('received_input_seq', 'received_at_monotonic', 'pending_input_seq', 'u', 'seq'):
        assert after[key] == before[key]
    simulation.stop()


def test_same_input_is_a_new_receipt_without_expiration(clock):
    simulation = create_pendulum(active_controller='python', u0=0.2)
    adapter = InMemoryAdapter(simulation, controller_id='python')
    first = adapter.write_inputs([0.4], source_timestamp='2001-01-01T00:00:00Z')
    clock[0] += 100000.0
    pending = adapter.read_snapshot()
    assert pending['received_input_seq'] == first['accepted_input_seq']
    assert pending['pending_input_seq'] == first['accepted_input_seq']
    assert pending['received_at_monotonic'] == 100.0
    assert pending['u'] == [0.2]
    second = adapter.write_inputs([0.4], source_timestamp='2001-01-01T00:00:00+00:00')
    renewed = adapter.read_snapshot()
    assert second['accepted_input_seq'] > first['accepted_input_seq']
    assert renewed['received_at_monotonic'] == clock[0]
    assert renewed['received_source_timestamp'] == '2001-01-01T00:00:00Z'
    assert renewed['source_timestamp'] is None
    simulation.stop()


def test_in_memory_controller_roundtrip_needs_no_http(monkeypatch, tmp_path, clock):
    simulation = create_pendulum(active_controller='python', theta0=0.4, dt=0.1)
    adapter = InMemoryAdapter(simulation, controller_id='python')
    ack = adapter.write_signals({'torque': 0.8}, source_timestamp='2020-01-01T00:00:00Z')
    monkeypatch.setattr(simulation._stop_event, 'wait', lambda timeout: clock.__setitem__(0, clock[0] + timeout) or False)
    record = tmp_path / 'adapter.jsonl'
    before = adapter.read_measurements()
    simulation.start(duration=0.2, record=record)
    try:
        simulation._thread.join(timeout=5)
        assert not simulation._thread.is_alive()
    finally:
        simulation.stop()
    snapshot = adapter.read_snapshot()
    measured = adapter.read_measurements()
    assert snapshot['input_seq'] == ack['accepted_input_seq']
    assert snapshot['controller_id'] == 'python'
    assert snapshot['u'] == [0.8]
    assert measured['torque'] == 0.8
    assert measured['time'] == pytest.approx(0.2)
    assert measured['theta'] != before['theta']
    assert measured['status'] == 'finished'
    assert snapshot['applied_at_sim_time'] == 0
    assert snapshot['source_timestamp'] == '2020-01-01T00:00:00Z'


def test_applied_input_is_held_across_arbitrary_clock_gaps(monkeypatch, tmp_path, clock):
    simulation = create_pendulum(active_controller='python', dt=0.1)
    adapter = InMemoryAdapter(simulation, controller_id='python')
    receipt = adapter.write_inputs([0.8])

    def wait_with_long_gap(timeout):
        clock[0] += 100000.0
        return False

    monkeypatch.setattr(simulation._stop_event, 'wait', wait_with_long_gap)
    record = tmp_path / 'held-input.jsonl'
    simulation.start(duration=0.3, record=record)
    try:
        simulation._thread.join(timeout=5)
        assert not simulation._thread.is_alive()
    finally:
        simulation.stop()
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    committed = [row for row in rows if row['seq'] > 0]
    assert len(committed) >= 3
    assert all(row['u'] == [0.8] for row in committed)
    assert all(row['input_seq'] == receipt['accepted_input_seq'] for row in committed)
    assert all(row['received_at_monotonic'] == 100.0 for row in committed)
    assert committed[-1]['status'] == 'finished'


def test_rejected_sample_does_not_increment_next_accepted_sequence():
    simulation = create_pendulum(active_controller='python')
    adapter = InMemoryAdapter(simulation, controller_id='python')
    first = adapter.write_inputs([0.3])
    with pytest.raises(ValueError):
        adapter.write_inputs([float('nan')])
    with pytest.raises(ValueError):
        adapter.write_inputs([1, 2])
    second = adapter.write_inputs([0.3])
    assert second['accepted_input_seq'] == first['accepted_input_seq'] + 1
    simulation.stop()


class _StructuralPlant:
    """Independent implementation, with no inheritance from package classes."""

    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {'x': [0.2, -0.3], 'u': [0.4], 't': 1.0, 'status': 'running'}

    def read_measurements(self):
        return {'theta': 0.2, 'omega': -0.3, 'torque': 0.4, 'time': 1.0, 'status': 'running'}

    def describe_signals(self):
        return [{'name': 'torque', 'unit': 'N m', 'dtype': 'float',
                 'direction': 'input', 'lower': None, 'upper': None}]

    def submit_input(self, u, *, controller_id, source_timestamp=None):
        self.calls.append({'u': list(u), 'controller_id': controller_id,
                           'source_timestamp': source_timestamp})
        return {'accepted_input_seq': len(self.calls), 'applied_input_seq': 0}


def test_adapter_accepts_structural_protocol_implementation():
    plant = _StructuralPlant()
    assert isinstance(plant, SimulationPort)
    adapter = InMemoryAdapter(plant, controller_id='plc')
    assert adapter.read_snapshot() == plant.snapshot()
    adapter.write_signals({'torque': 0.7})
    assert plant.calls[-1]['u'] == [0.7]
    assert plant.calls[-1]['controller_id'] == 'plc'
    assert adapter.describe_signals() == plant.describe_signals()
    assert adapter.read_measurements() == plant.read_measurements()


def test_adapter_reads_other_models_without_assuming_pendulum_state_layout():
    class TankPlant(_StructuralPlant):
        def snapshot(self):
            return {'levels': {'main': 1.7}, 'sample': 6}

        def read_measurements(self):
            return {'level': 1.7, 'outflow': 0.02}

        def describe_signals(self):
            return [{'name': 'level', 'unit': 'm', 'dtype': 'float64',
                     'direction': 'output', 'lower': 0.0, 'upper': 3.0}]

    plant = TankPlant()
    adapter = InMemoryAdapter(plant, controller_id='tank_bridge')
    assert isinstance(plant, SimulationPort)
    assert adapter.read_measurements() == {'level': 1.7, 'outflow': 0.02}
    assert adapter.read_snapshot() == {'levels': {'main': 1.7}, 'sample': 6}
    assert adapter.describe_signals()[0]['upper'] == 3.0


def test_named_mapping_orders_a_complete_multi_input_frame_before_submission():
    class MultiInputPlant(_StructuralPlant):
        def describe_signals(self):
            return [
                {'name': name, 'direction': 'input', 'unit': '', 'dtype': 'float64',
                 'lower': None, 'upper': None}
                for name in ('first', 'second')
            ]

    plant = MultiInputPlant()
    adapter = InMemoryAdapter(plant, controller_id='bridge')
    adapter.write_signals({'second': 2.0, 'first': 1.0})
    assert len(plant.calls) == 1
    assert plant.calls[0]['u'] == [1.0, 2.0]
    with pytest.raises(ValueError):
        adapter.write_signals({'first': 3.0})
    assert len(plant.calls) == 1


def test_http_uses_structural_port_instead_of_concrete_pendulum():
    plant = _StructuralPlant()
    server = create_server(plant, port=0, viewer='pendulum')
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}'
        with urlopen(base + '/api/state', timeout=2) as response:
            assert json.load(response) == plant.snapshot()
        with urlopen(base + '/api/signals', timeout=2) as response:
            assert json.load(response) == plant.describe_signals()
        with urlopen(base + '/', timeout=2) as response:
            assert response.headers.get_content_type() == 'text/html'
            assert b'/api/state' in response.read()
        request = Request(base + '/api/input', data=b'{"u":[0.9]}',
                          headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=2) as response:
            assert response.status == 202
        assert plant.calls[-1]['controller_id'] == 'http'
        assert plant.calls[-1]['u'] == [0.9]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_http_cannot_spoof_the_active_controller():
    simulation = create_pendulum(active_controller='plc')
    server = create_server(simulation, port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    worker.start()
    try:
        base = f'http://127.0.0.1:{server.server_address[1]}'
        for payload in ({'u': [0.8]}, {'u': [0.8], 'controller_id': 'plc'}):
            request = Request(base + '/api/input', data=json.dumps(payload).encode(),
                              headers={'Content-Type': 'application/json'})
            with pytest.raises(HTTPError) as exc:
                urlopen(request, timeout=2)
            assert exc.value.code in (400, 403)
            exc.value.close()
        snapshot = simulation.snapshot()
        assert snapshot['received_input_seq'] == 0
        assert snapshot['pending_input_seq'] is None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        simulation.stop()

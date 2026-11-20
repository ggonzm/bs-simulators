"""Capture lifecycle, relative clocks and atomic changes during integration."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import numpy as np
import pytest

from bs_simulators import DAEStepper, Simulation
from bs_simulators.runtime import _recording


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def blocked_run(monkeypatch):
    """Pause after solving each interval, before publishing its new state."""
    simulation = Simulation(lambda t, x, *, u: [t + u], x0=[1], u0=[2],
                            t0=10, dt=0.5)
    reached = [threading.Event() for _ in range(4)]
    proceed = [threading.Event() for _ in range(4)]
    integrate = simulation._stepper.step
    calls = 0

    def blocked_step(dt, u):
        nonlocal calls
        index = calls
        calls += 1
        result = integrate(dt, u)
        reached[index].set()
        if not proceed[index].wait(10):
            raise RuntimeError('test did not release the integration barrier')
        return result

    monkeypatch.setattr(simulation._stepper, 'step', blocked_step)
    simulation.start(duration=2, dt_real=0.001)
    assert reached[0].wait(5)
    try:
        yield simulation, reached, proceed
    finally:
        for event in proceed:
            event.set()
        if simulation._recorder is not None and simulation._recorder.status()['error'] is not None:
            with pytest.raises(OSError):
                simulation.stop()
        else:
            simulation.stop()


def test_capture_restart_gap_and_rotation_preserve_physics(blocked_run, tmp_path):
    simulation, reached, proceed = blocked_run
    first, second, third = [tmp_path / f'{name}.jsonl' for name in ('first', 'second', 'third')]
    simulation.start_recording(first)
    assert read_rows(first)[0]['t'] == 0  # The initial row is already flushed.
    proceed[0].set()
    assert reached[1].wait(5)
    simulation.stop_recording()
    closed_first = first.read_bytes()

    proceed[1].set()  # The second interval runs without a capture.
    assert reached[2].wait(5)
    simulation.start_recording(second)
    proceed[2].set()
    assert reached[3].wait(5)
    simulation.start_recording(third)  # Rotate directly at global t=11.5.
    closed_second = second.read_bytes()
    proceed[3].set()
    assert simulation.wait(5)

    assert first.read_bytes() == closed_first
    assert second.read_bytes() == closed_second
    for path, sequences in [(first, [0, 1, 1]), (second, [2, 3, 3]), (third, [3, 4, 4])]:
        rows = read_rows(path)
        assert [row['seq'] for row in rows] == sequences
        assert [row['t'] for row in rows] == [0, 0.5, 0.5]
        assert rows[0]['wall_elapsed'] == 0
        assert all(row['wall_elapsed'] >= 0 for row in rows)
        for row in rows:
            absolute_time = 10 + row['seq'] * 0.5
            assert row['x'] == pytest.approx([1 + 2*(absolute_time - 10) + (absolute_time**2 - 100)/2])
            assert row['u'] == [2]
            assert row['input_saturated'] == [False]
    assert read_rows(second)[-1]['x'] == read_rows(third)[0]['x']
    assert read_rows(third)[0]['applied_at_sim_time'] == -1.5
    assert read_rows(third)[-1]['status'] == 'finished'
    assert read_rows(first)[-1]['status'] == 'running'
    state = simulation.snapshot()
    assert state['t'] == 12
    assert state['seq'] == 4
    assert state['applied_at_sim_time'] == 10
    simulation.stop_recording()  # Automatic completion already closed the file.


@pytest.mark.parametrize('failure', ['existing', 'missing-parent', 'initial-write'])
def test_failed_rotation_keeps_previous_capture(blocked_run, tmp_path, monkeypatch, failure):
    simulation, reached, proceed = blocked_run
    previous = tmp_path / 'previous.jsonl'
    simulation.start_recording(previous)
    destination = tmp_path / 'new.jsonl'
    if failure == 'existing':
        destination.write_text('preserved\n')
    elif failure == 'missing-parent':
        destination = tmp_path / 'missing' / 'new.jsonl'
    else:
        active = simulation._recorder._active
        write = _recording._Recording.write

        def fail_initial(recording, state):
            if recording is not active:
                raise OSError('initial write failed')
            write(recording, state)

        monkeypatch.setattr(_recording._Recording, 'write', fail_initial)
    with pytest.raises(OSError):
        simulation.start_recording(destination)
    if failure == 'existing':
        assert destination.read_text() == 'preserved\n'
    proceed[0].set()
    assert reached[1].wait(5)
    simulation.stop_recording()
    assert [row['seq'] for row in read_rows(previous)] == [0, 1, 1]
    assert simulation.snapshot()['status'] == 'running'


def test_concurrent_capture_changes_are_serialized(blocked_run, tmp_path):
    simulation, _, _ = blocked_run
    paths = [tmp_path / 'one.jsonl', tmp_path / 'two.jsonl']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(simulation.start_recording, paths))
    assert results == [None, None]
    simulation.stop_recording()
    for path in paths:
        assert [row['t'] for row in read_rows(path)] == [0, 0]
        assert [row['seq'] for row in read_rows(path)] == [0, 0]


def test_failed_old_capture_close_keeps_new_capture_active(blocked_run, tmp_path, monkeypatch):
    simulation, reached, proceed = blocked_run
    previous, destination = tmp_path / 'old.jsonl', tmp_path / 'new.jsonl'
    simulation.start_recording(previous)
    old_recording = simulation._recorder._active
    write = _recording._Recording.write

    def fail_old(recording, state):
        if recording is old_recording:
            raise OSError('old file write failed')
        write(recording, state)

    monkeypatch.setattr(_recording._Recording, 'write', fail_old)
    with pytest.raises(OSError, match='old file'):
        simulation.start_recording(destination)
    assert old_recording._stream.closed
    proceed[0].set()
    assert reached[1].wait(5)
    simulation.stop_recording()
    assert [row['seq'] for row in read_rows(destination)] == [0, 1, 1]


def test_writer_failure_preserves_physics_and_closes_stream(blocked_run, tmp_path, monkeypatch):
    simulation, reached, proceed = blocked_run
    simulation.start_recording(tmp_path / 'failure.jsonl')
    recording = simulation._recorder._active

    def fail_write(recording, state):
        raise OSError('disk write failed')

    monkeypatch.setattr(_recording._Recording, 'write', fail_write)
    proceed[0].set()
    assert reached[1].wait(5)
    assert simulation._recorder.wait(5)
    assert simulation.snapshot()['status'] == 'running'
    assert simulation._recorder.status()['status'] == 'error'
    assert 'disk write failed' in simulation._recorder.status()['error']
    with pytest.raises(OSError, match='disk write failed'):
        simulation.stop_recording()
    for event in proceed:
        event.set()
    with pytest.raises(OSError, match='disk write failed'):
        simulation.wait(5)
    assert simulation.snapshot()['status'] == 'finished'
    assert recording._stream.closed


@pytest.mark.parametrize('kind', ['ode', 'dae'])
def test_start_recording_option_uses_relative_time_for_nonzero_t0(tmp_path, kind):
    if kind == 'ode':
        simulation = Simulation(lambda t, x: [t], x0=[1], u0=[], t0=4, dt=0.5)
    else:
        simulation = Simulation(lambda t, x, dx: [dx[0] - t, x[1] - 2*x[0]],
                                stepper=DAEStepper([4, 8], fixed_x=[0]),
                                x0=[1, 2], u0=[], t0=4, dt=0.5)
    path = tmp_path / 'capture.jsonl'
    simulation.start(duration=1, dt_real=0.001, record=path)
    assert simulation.wait(5)
    assert simulation.snapshot()['status'] == 'finished'
    rows = read_rows(path)
    assert [row['t'] for row in rows] == [0, 0.5, 1, 1]
    assert rows[0]['wall_elapsed'] == 0
    assert all(row['wall_elapsed'] >= 0 for row in rows)
    assert all(row['applied_at_sim_time'] == 0 for row in rows)
    assert rows[-1]['x'][0] == pytest.approx(5.5)
    assert simulation.snapshot()['t'] == 5
    if kind == 'dae':
        assert rows[-1]['xdot'] == pytest.approx([5, 10])


def test_capture_start_requires_running_plant_and_stop_is_idempotent(tmp_path):
    simulation = Simulation(lambda t, x: [1], x0=[0], u0=[])
    path = tmp_path / 'capture.jsonl'
    simulation.stop_recording()
    with pytest.raises(RuntimeError, match='running'):
        simulation.start_recording(path)
    assert not path.exists()
    simulation.stop()
    with pytest.raises(RuntimeError, match='running'):
        simulation.start_recording(path)
    simulation.stop_recording()
    assert not path.exists()


def test_recording_preserves_command_sequences_and_original_receipt_timestamps(tmp_path):
    simulation = Simulation(lambda t, x, *, u: [sum(u)], x0=[0], u0=[1, 2], t0=7)
    timestamp = '2026-11-20T09:00:00Z'
    ack = simulation.submit_input([3, 4], controller_id='python', source_timestamp=timestamp)
    path = tmp_path / 'commands.jsonl'
    simulation.start(duration=0.01, dt_real=0.001, record=path)
    assert simulation.wait(5)
    initial, committed, terminal = read_rows(path)
    assert initial['pending_input_seq'] == ack['accepted_input_seq']
    assert initial['received_at_monotonic'] == ack['received_at_monotonic']
    assert initial['received_source_timestamp'] == timestamp
    assert initial['u'] == [1, 2]
    assert committed['u'] == terminal['u'] == [3, 4]
    assert committed['input_seq'] == ack['accepted_input_seq']
    assert committed['source_timestamp'] == timestamp
    assert committed['applied_received_at_monotonic'] == ack['received_at_monotonic']
    assert committed['applied_at_sim_time'] == 0
    assert committed['t'] == pytest.approx(0.01)
    assert simulation.snapshot()['applied_at_sim_time'] == 7


def test_solver_failure_writes_terminal_error_and_closes_capture(tmp_path):
    simulation = Simulation(lambda t, x: [1 if t == 4 else np.nan],
                            x0=[0], u0=[], t0=4)
    path = tmp_path / 'failure.jsonl'
    simulation.start(duration=0.01, dt_real=0.001, record=path)
    assert simulation.wait(5)
    rows = read_rows(path)
    assert [row['t'] for row in rows] == [0, 0]
    assert rows[-1]['status'] == 'error'
    assert rows[-1]['x'] == [0]
    assert 'error' in rows[-1]
    assert simulation._recorder.status()['status'] == 'idle'
    path.rename(tmp_path / 'closed.jsonl')


def test_slow_writer_allows_physics_to_finish_and_overwrites_oldest_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(_recording, '_BUFFER_CAPACITY', 3)
    busy, release = threading.Event(), threading.Event()
    writer_threads = set()
    write = _recording._Recording.write

    def slow_write(recording, state):
        writer_threads.add(threading.current_thread().name)
        if state['seq'] == 1:
            busy.set()
            if not release.wait(10):
                raise RuntimeError('test did not release the writer')
        write(recording, state)

    monkeypatch.setattr(_recording._Recording, 'write', slow_write)
    simulation = Simulation(lambda t, x: [t], x0=[0], u0=[], t0=10, dt=0.25)
    integrate = simulation._stepper.step

    def ordered_step(dt, u):
        if simulation._seq == 1:
            assert busy.wait(5)  # Hold the writer before producing the remaining steps.
        return integrate(dt, u)

    monkeypatch.setattr(simulation._stepper, 'step', ordered_step)
    path = tmp_path / 'rolling.jsonl'
    simulation.start(duration=5, dt_real=0.001, record=path)
    try:
        assert busy.wait(5)
        simulation._thread.join(5)
        assert not simulation._thread.is_alive()
        assert simulation.snapshot()['status'] == 'finished'
        assert simulation.snapshot()['seq'] == 20
        assert simulation.snapshot()['t'] == 15
        assert simulation.snapshot()['x'] == pytest.approx([62.5])
        assert simulation.wait(0) is False  # Only the writer remains blocked.
        assert 'recording' not in simulation.snapshot()
        assert not hasattr(simulation, 'recording_status')
    finally:
        release.set()
        simulation.stop()
    assert writer_threads == {'simulation-recording'}
    rows = read_rows(path)
    assert [row['seq'] for row in rows] == [0, 1, 18, 19, 20, 20]
    assert [row['t'] for row in rows] == [0, 0.25, 4.5, 4.75, 5, 5]
    assert rows[-1]['status'] == 'finished'


def test_rolling_samples_preserve_capture_commands_and_file_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(_recording, '_BUFFER_CAPACITY', 2)
    busy, release = threading.Event(), threading.Event()
    write = _recording._Recording.write

    def state(seq):
        return {'seq': seq, 't': float(seq), 'wall_elapsed': float(seq),
                'applied_at_sim_time': 0.0, 'x': [seq], 'status': 'running'}

    def slow_write(recording, snapshot):
        if snapshot['seq'] == 1:
            busy.set()
            if not release.wait(10):
                raise RuntimeError('test did not release the writer')
        write(recording, snapshot)

    monkeypatch.setattr(_recording._Recording, 'write', slow_write)
    writer = _recording._RecordingWorker()
    first, second = tmp_path / 'first.jsonl', tmp_path / 'second.jsonl'
    writer.request('start', state(0), first).wait()
    try:
        writer.publish(state(1))
        assert busy.wait(5)
        writer.publish(state(2))
        writer.publish(state(3))
        rotate = writer.request('start', state(3), second)
        for seq in (4, 5, 6):
            writer.publish(state(seq))
        close = writer.request('stop', state(6))
        writer.finish(dict(state(6), status='finished'))
    finally:
        release.set()
        writer.finish(dict(state(6), status='finished'))
        assert writer.wait(5)
    rotate.wait()
    close.wait()
    writer.raise_error()
    assert [row['seq'] for row in read_rows(first)] == [0, 1, 3]
    assert [row['seq'] for row in read_rows(second)] == [3, 5, 6, 6]
    assert [row['t'] for row in read_rows(second)] == [0, 2, 3, 3]
    assert read_rows(first)[-1]['x'] == read_rows(second)[0]['x']


def test_capture_can_restart_after_an_observed_writer_error(blocked_run, tmp_path, monkeypatch):
    simulation, reached, proceed = blocked_run
    simulation.start_recording(tmp_path / 'failed.jsonl')
    failed = simulation._recorder
    active = failed._active
    write = _recording._Recording.write

    def fail_old(recording, state):
        if recording is active:
            raise OSError('disk unavailable')
        write(recording, state)

    monkeypatch.setattr(_recording._Recording, 'write', fail_old)
    proceed[0].set()
    assert reached[1].wait(5)
    assert failed.wait(5)
    with pytest.raises(OSError, match='disk unavailable'):
        simulation.stop_recording()
    destination = tmp_path / 'recovered.jsonl'
    simulation.start_recording(destination)
    assert simulation._recorder is not failed
    for event in proceed:
        event.set()
    assert simulation.wait(5)
    rows = read_rows(destination)
    assert rows[0]['t'] == 0
    assert rows[-1]['t'] == 1.5
    assert rows[-1]['status'] == 'finished'


def test_slow_capture_open_does_not_hold_publication_lock(blocked_run, tmp_path, monkeypatch):
    simulation, _, proceed = blocked_run
    busy, release = threading.Event(), threading.Event()
    write = _recording._Recording.write

    def slow_initial_write(recording, state):
        if state['seq'] == 0:
            busy.set()
            if not release.wait(10):
                raise RuntimeError('test did not release capture opening')
        write(recording, state)

    monkeypatch.setattr(_recording._Recording, 'write', slow_initial_write)
    destination = tmp_path / 'slow-open.jsonl'
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(simulation.start_recording, destination)
        try:
            assert busy.wait(5)
            for event in proceed:
                event.set()
            simulation._thread.join(5)
            assert not simulation._thread.is_alive()
            assert simulation.snapshot()['status'] == 'finished'
            assert not request.done()
        finally:
            release.set()
        assert request.result(timeout=5) is None
    assert simulation.wait(5)
    rows = read_rows(destination)
    assert [row['seq'] for row in rows] == [0, 1, 2, 3, 4, 4]

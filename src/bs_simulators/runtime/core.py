"""Simulation state and wall-clock pacing, independent of the viewer."""

from __future__ import annotations

import json
import copy
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp

from benchmark_systems.models import pendulum
from .signals import PENDULUM_SIGNALS, validate_source_timestamp, controller_identifier


def _finite(name, value, *, positive=False, nonnegative=False):
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not np.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _vector(name, values, size=None):
    try:
        raw = np.asarray(values)
        if raw.dtype.kind not in 'iuf':
            raise ValueError
        result = np.asarray(values, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite numeric vector") from exc
    if result.ndim != 1 or result.size == 0 or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a non-empty finite vector")
    if size is not None and result.size != size:
        raise ValueError(f"{name} must contain {size} values")
    return result.copy()


class _ODEStepper:
    """Advance ``f(t, x, u)`` with absolute time and a frozen input per step.

    This object has one owner and is not thread-safe. ``step`` commits its
    state only after successful integration. A fresh solve_ivp call at each
    input boundary avoids reusing multistep history across discontinuities.
    """

    def __init__(self, f, y0, *, t0=0.0, input_size=1, rtol=1e-7, atol=1e-9):
        if not callable(f):
            raise ValueError("f must be callable")
        if isinstance(input_size, bool) or not isinstance(input_size, int) or input_size < 1:
            raise ValueError("input_size must be a positive integer")
        self.f = f
        self.t = _finite("t0", t0)
        self.x = _vector("y0", y0)
        self.input_size = input_size
        self.rtol = _finite("rtol", rtol, positive=True)
        self.atol = _finite("atol", atol, positive=True)

    def step(self, dt, u):
        """Return a copy of the new state, holding ``u`` over this interval."""
        dt = _finite("dt", dt, positive=True)
        held = _vector("u", u, self.input_size)
        held.flags.writeable = False
        target = self.t + dt
        if not np.isfinite(target) or target <= self.t:
            raise ValueError("dt must advance finite simulation time")

        def rhs(t, x):
            return _vector("derivative", self.f(t, x, held), self.x.size)

        result = solve_ivp(rhs, (self.t, target), self.x.copy(), t_eval=[target],
                           rtol=self.rtol, atol=self.atol)
        if not result.success:
            raise RuntimeError(result.message)
        new_state = _vector("integrated state", result.y[:, -1], self.x.size)
        self.x = new_state
        self.t = target
        return self.x.copy()


@dataclass(frozen=True)
class _InputCommand:
    seq: int
    values: tuple[float, ...]
    controller_id: str
    received_at: float
    source_timestamp: str | None


class PendulumSimulation:
    """Single-worker, fixed-step pendulum simulator with latest-value ZOH.

    Commands are accepted atomically and applied at the next step boundary.
    An acknowledgement indicates acceptance, not physical application. The
    browser reads copied snapshots and never owns or advances the simulation.
    Pacing provides soft real time: overload accumulates visible lag, while
    preserving every physical step. Completed runs cannot be restarted.
    """

    def __init__(self, *, m=1.0, L=1.0, drag=0.1, theta0=0.6,
                 omega0=0.0, dt=0.01, u0=0.0, active_controller='http'):
        self.parameters = {
            'm': _finite('m', m, positive=True),
            'L': _finite('L', L, positive=True),
            'drag': _finite('drag', drag, nonnegative=True),
        }
        self.dt = _finite('dt', dt, positive=True)
        self._active_controller = controller_identifier(active_controller)
        initial = [_finite('theta0', theta0), _finite('omega0', omega0)]
        self._u = [_finite('u0', u0)]
        self._stepper = _ODEStepper(
            lambda t, x, u: pendulum(t, x, **self.parameters, u=u[0]), initial)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._pending = None
        self._inflight = None
        self._received_command = None
        self._applied_command = None
        self._command_seq = 0
        self._input_seq = 0
        self._applied_at = 0.0
        self._seq = 0
        self._status = 'ready'
        self._error = None
        self._started = None
        self._snapshot = self._build_snapshot(0.0)

    def _build_snapshot(self, elapsed):
        state = {
            't': self._stepper.t, 'x': self._stepper.x.tolist(),
            'u': list(self._u), 'seq': self._seq, 'input_seq': self._input_seq,
            'pending_input_seq': self._pending_sequence(),
            'applied_at_sim_time': self._applied_at,
            'wall_elapsed': elapsed, 'lag': max(0.0, elapsed - self._stepper.t),
            'status': self._status, 'parameters': dict(self.parameters),
        }
        if self._error is not None:
            state['error'] = self._error
        state.update(self._input_metadata())
        return state

    def _input_metadata(self):
        received = self._received_command
        applied = self._applied_command
        return {
            'active_controller': self._active_controller,
            'controller_id': applied.controller_id if applied else None,
            'received_input_seq': received.seq if received else 0,
            'received_at_monotonic': received.received_at if received else None,
            'source_timestamp': applied.source_timestamp if applied else None,
            'received_source_timestamp': received.source_timestamp if received else None,
            'applied_received_at_monotonic': applied.received_at if applied else None,
        }

    def _pending_sequence(self):
        command = self._pending or self._inflight
        return command.seq if command else None

    def snapshot(self):
        """Return a detached, JSON-compatible snapshot of committed state."""
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            result['pending_input_seq'] = self._pending_sequence()
            result.update(self._input_metadata())
            return result

    def describe_signals(self):
        """Return detached metadata; direction is relative to the simulator."""
        return [signal.as_dict() for signal in PENDULUM_SIGNALS]

    def read_measurements(self):
        """Map this model's committed state to its named measurements."""
        state = self.snapshot()
        return {'theta': state['x'][0], 'omega': state['x'][1],
                'torque': state['u'][0], 'time': state['t'], 'status': state['status']}

    @property
    def active_controller(self):
        with self._lock:
            return self._active_controller

    def configure_controller(self, controller_id):
        """Explicitly select one controller before start.

        Switching while ready clears queued inputs and receipt metadata from
        the previous controller. The held initial value remains unchanged.
        """
        controller_id = controller_identifier(controller_id)
        with self._lock:
            if self._status != 'ready':
                raise RuntimeError('controller selection is only allowed before start')
            if controller_id != self._active_controller:
                self._active_controller = controller_id
                self._pending = None
                self._received_command = None

    def set_input(self, u):
        """Trusted Python convenience: submit as the configured active controller.

        External bridges should use InMemoryAdapter with a fixed controller_id,
        which enforces ownership instead of impersonating the active controller.
        """
        return self.submit_input(u, controller_id=self.active_controller)

    def submit_input(self, u, *, controller_id, source_timestamp=None):
        """Validate and atomically enqueue a complete input vector.

        Receipt order uses local monotonic time and sequence numbers. A UTC
        source timestamp is diagnostic metadata and never schedules commands.
        Repeated values are fresh receipts; rejected commands change nothing.
        """
        values = _vector('u', u, 1).tolist()
        controller_id = controller_identifier(controller_id)
        source_timestamp = validate_source_timestamp(source_timestamp)
        with self._lock:
            if self._status not in ('ready', 'running'):
                raise RuntimeError('simulation is not accepting inputs')
            if controller_id != self._active_controller:
                raise PermissionError(f'controller {controller_id!r} is not the active controller')
            self._command_seq += 1
            self._pending = _InputCommand(self._command_seq, tuple(values), controller_id,
                                          time.monotonic(), source_timestamp)
            self._received_command = self._pending
            return {'input_seq': self._command_seq, 'pending_input_seq': self._command_seq,
                    'accepted_input_seq': self._command_seq, 'applied_input_seq': self._input_seq,
                    'controller_id': controller_id, 'received_at_monotonic': self._pending.received_at}

    def start(self, *, duration=None, record=None):
        """Start a worker; optionally record initial state and each committed step.

        ``duration`` is in simulated seconds. It must be a whole number of
        fixed steps so recorded time and requested duration agree exactly.
        ``record`` is a JSONL path, created exclusively to avoid overwrites.
        """
        steps = None
        if duration is not None:
            duration = _finite('duration', duration, positive=True)
            count = duration / self.dt
            if not np.isfinite(count) or not np.isclose(count, round(count), rtol=0, atol=1e-8):
                raise ValueError('duration must be a whole number of dt steps')
            steps = int(round(count))
            if steps < 1:
                raise ValueError('duration must include at least one step')
        with self._lock:
            if self._status != 'ready':
                raise RuntimeError('simulation has already been started or stopped')
            stream = Path(record).open('x', encoding='utf-8') if record is not None else None
            self._started = time.monotonic()
            self._status = 'running'
            self._snapshot = self._build_snapshot(0.0)
            self._thread = threading.Thread(target=self._run, args=(steps, stream),
                                            name='pendulum-simulation', daemon=False)
            self._thread.start()
        return self

    def _run(self, steps, stream):
        try:
            if stream is not None:
                stream.write(json.dumps(self.snapshot(), allow_nan=False) + '\n')
            while not self._stop_event.is_set() and (steps is None or self._seq < steps):
                with self._lock:
                    command = self._pending
                    self._pending = None
                    self._inflight = command
                    held = list(command.values if command else self._u)
                    input_seq = command.seq if command else self._input_seq
                    applied_at = self._stepper.t if command else self._applied_at
                # Freeze at the interval's beginning: commands arriving while
                # we wait cannot retroactively affect the preceding interval.
                # Absolute deadlines avoid cumulative sleep/compute drift.
                deadline = self._started + (self._seq + 1) * self.dt
                if self._stop_event.wait(max(0.0, deadline - time.monotonic())):
                    break
                self._stepper.step(self.dt, held)
                with self._lock:
                    self._u = held
                    self._input_seq = input_seq
                    if command is not None:
                        self._applied_command = command
                    self._applied_at = applied_at
                    self._inflight = None
                    self._seq += 1
                    self._snapshot = self._build_snapshot(time.monotonic() - self._started)
                    recorded = dict(self._snapshot)
                if stream is not None:
                    stream.write(json.dumps(recorded, allow_nan=False) + '\n')
        except Exception as exc:
            with self._lock:
                self._status = 'error'
                self._error = f'{type(exc).__name__}: {exc}'
        finally:
            with self._lock:
                if self._status != 'error':
                    self._status = 'finished' if steps is not None and self._seq >= steps else 'stopped'
                self._pending = None
                self._inflight = None
                self._snapshot = self._build_snapshot(time.monotonic() - self._started)
                terminal = dict(self._snapshot)
            if stream is not None:
                try:
                    stream.write(json.dumps(terminal, allow_nan=False) + '\n')
                    stream.close()
                except Exception as exc:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    with self._lock:
                        self._status = 'error'
                        self._error = f'{type(exc).__name__}: {exc}'
                        self._snapshot = self._build_snapshot(time.monotonic() - self._started)

    def stop(self):
        """Request shutdown and wait for the in-flight integration to complete."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._lock:
            if self._status == 'ready':
                self._status = 'stopped'
                self._pending = None
                self._snapshot = self._build_snapshot(0.0)

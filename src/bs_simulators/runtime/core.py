"""Simulation state and wall-clock pacing, independent of the viewer."""

from __future__ import annotations

import json
import copy
import threading
import time
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from ._validation import _finite, _vector
from .protocols import Stepper
from .steppers import ODEStepper
from .signals import SignalDescriptor, validate_source_timestamp, controller_identifier


@dataclass(frozen=True)
class _InputCommand:
    """Immutable input receipt carrying its sequence, controller and diagnostic timestamps."""
    seq: int
    values: tuple[float, ...]
    saturated: tuple[bool, ...]
    controller_id: str
    received_at: float
    source_timestamp: str | None


class Simulation:
    """Single-worker, model-independent simulator with latest-value ZOH.

    Commands are accepted atomically and applied at the next step boundary.
    An acknowledgement indicates acceptance, not physical application. The
    browser reads copied snapshots and never owns or advances the simulation.
    Pacing provides soft real time: overload accumulates visible lag, while
    preserving every physical step. Completed runs cannot be restarted.
    """

    def __init__(self, model, *, stepper: Stepper | None = None, x0, u0, parameters=None,
                 dt=0.01, t0=0.0, state_signals=None, input_signals=None,
                 active_controller='python', viewer=None, input_map=None):
        """Configure a numerical model and an optional integrator.

        Parameters
        ----------
        model : callable
            A benchmark-systems function or compatible numerical callable.
            ODEs receive ``(t, x, **parameters, u=...)``; DAEs also receive
            the derivative as their third positional argument. The chosen
            stepper determines which signature is used.
        stepper : Stepper or None, optional
            Integrator configuration owned exclusively by this run. If omitted
            or None, create a fresh ODEStepper(method='RK45'). Pass DAEStepper
            explicitly for residual models, or another Stepper implementation
            to customize integration.
        x0, u0 : sequence of float
            Initial state and complete held input vector. An empty input
            vector denotes an autonomous model, called without ``u``.
        parameters : mapping, optional
            Detached, JSON-compatible model keyword arguments. NumPy arrays
            and scalars are converted to lists and Python scalar values.
        dt : float, optional
            Positive integration interval in simulated seconds.
        t0 : float, optional
            Initial absolute model time. Duration and pacing are relative to it.
        state_signals, input_signals : sequence of SignalDescriptor, optional
            Ordered descriptions mapped to state and input vector positions.
            Defaults are ``x0``, ``x1``, ... and ``u0``, ``u1``, ... .
        active_controller : str, optional
            Controller authorized to submit inputs; defaults to ``'python'``.
        viewer : str or None, optional
            Optional packaged-viewer identifier for communication adapters.
        input_map : callable, optional
            Map the complete input vector to model keyword arguments. By
            default, one input becomes scalar ``u`` and several become vector
            ``u``. Use a mapping for different names or a one-element vector.

        Notes
        -----
        Construction initializes the stepper but does not start a thread.
        A DAE stepper may adjust algebraic initial values for consistency.
        Models with scalar/vector input conventions that differ from the
        defaults should supply ``input_map`` explicitly.
        """
        if not callable(model):
            raise ValueError('model must be callable')
        if stepper is None:
            stepper = ODEStepper(method='RK45')
        if not isinstance(stepper, Stepper):
            raise TypeError('stepper must implement the Stepper protocol')
        if input_map is not None and not callable(input_map):
            raise ValueError('input_map must be callable or None')
        if viewer is not None and (not isinstance(viewer, str) or not viewer):
            raise ValueError('viewer must be a non-empty identifier or None')
        self.dt = _finite('dt', dt, positive=True)
        self._t0 = _finite('t0', t0)
        initial = _vector('x0', x0)
        held = _vector('u0', u0, allow_empty=True)
        self._input_size = held.size
        self._active_controller = controller_identifier(active_controller)
        self._parameters = self._copy_parameters(parameters)
        self._state_signals = self._descriptors(state_signals, initial.size, 'x', 'output')
        self._input_signals = self._descriptors(input_signals, held.size, 'u', 'input')
        names = [signal.name for signal in (*self._state_signals, *self._input_signals)]
        if len(set(names)) != len(names):
            raise ValueError('signal names must be unique across states and inputs')
        self._viewer = viewer
        self._model_name = getattr(model, '__name__', type(model).__name__)
        self._model = model
        self._input_map = input_map
        self._u, self._input_saturated = self._saturate_input(held.tolist())
        self._stepper = stepper
        stepper.initialize(self._evaluate, initial, t0=self._t0, u0=np.asarray(self._u, dtype=float))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._pending = None
        self._inflight = None
        self._received_command = None
        self._applied_command = None
        self._command_seq = 0
        self._input_seq = 0
        self._applied_at = self._t0
        self._seq = 0
        self._status = 'ready'
        self._error = None
        self._started = None
        self._dt_real = self.dt
        self._snapshot = self._build_snapshot(0.0)

    @staticmethod
    def _copy_parameters(parameters):
        """Detach model parameters through JSON, normalizing NumPy values and rejecting nonfinite data."""
        if parameters is None:
            return {}
        if not isinstance(parameters, Mapping) or any(not isinstance(key, str) for key in parameters):
            raise ValueError('parameters must be a mapping with string keys')

        def convert(value):
            """Convert NumPy arrays or scalars for JSON serialization; reject unsupported objects."""
            if isinstance(value, np.ndarray):
                return value.tolist()
            if isinstance(value, np.generic):
                return value.item()
            raise TypeError('parameters must contain JSON-compatible values')

        try:
            return json.loads(json.dumps(dict(parameters), default=convert, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError('parameters must contain finite JSON-compatible values') from exc

    @staticmethod
    def _descriptors(descriptors, count, prefix, direction):
        """Build default signal names or validate supplied descriptors against vector size and direction."""
        if descriptors is None:
            return tuple(SignalDescriptor(f'{prefix}{index}', '', 'float64', direction)
                         for index in range(count))
        descriptors = tuple(descriptors)
        if len(descriptors) != count:
            raise ValueError(f'{direction} signal count must match its vector dimension')
        result = []
        for descriptor in descriptors:
            if not isinstance(descriptor, SignalDescriptor):
                raise TypeError('signals must be SignalDescriptor instances')
            if (not isinstance(descriptor.name, str) or not descriptor.name.strip()
                    or descriptor.name in ('time', 'status')):
                raise ValueError('signal names must be non-empty and cannot be time or status')
            if descriptor.direction != direction:
                raise ValueError(f'signal direction must be {direction!r}')
            if not isinstance(descriptor.unit, str) or not isinstance(descriptor.dtype, str):
                raise ValueError('signal unit and dtype must be strings')
            lower = None if descriptor.lower is None else _finite('lower', descriptor.lower)
            upper = None if descriptor.upper is None else _finite('upper', descriptor.upper)
            if lower is not None and upper is not None and lower > upper:
                raise ValueError('signal lower bound must not exceed its upper bound')
            result.append(SignalDescriptor(descriptor.name, descriptor.unit, descriptor.dtype,
                                           direction, lower, upper))
        return tuple(result)

    @property
    def parameters(self):
        """Return detached model parameters for interactive inspection."""
        return copy.deepcopy(self._parameters)

    @staticmethod
    def _clamp(value, signal):
        """Limit a value to the configured endpoints, if any."""
        if signal.lower is not None and value < signal.lower:
            return signal.lower
        if signal.upper is not None and value > signal.upper:
            return signal.upper
        return value

    def _saturate_input(self, values):
        """Return effective actuator values and a flag for each clipped command."""
        applied = [self._clamp(value, signal)
                   for value, signal in zip(values, self._input_signals)]
        return applied, [value != effective for value, effective in zip(values, applied)]

    def _evaluate(self, t, x, *arguments):
        """Adapt stepper arguments to the model signature, binding physical parameters and mapped inputs."""
        *derivative, u = arguments
        if self._input_map is not None:
            inputs = self._input_map(u.copy())
            if not isinstance(inputs, Mapping) or any(not isinstance(key, str) for key in inputs):
                raise ValueError('input_map must return keyword arguments as a mapping')
        elif self._input_size == 0:
            inputs = {}
        else:
            inputs = {'u': float(u[0]) if self._input_size == 1 else u}
        if self._parameters.keys() & inputs.keys():
            raise ValueError('model parameters must not duplicate mapped input names')
        return self._model(t, x, *derivative, **self._parameters, **inputs)

    def _build_snapshot(self, elapsed):
        """Assemble committed solver state and timing metadata during initialization or while holding the lock."""
        state = {
            't': self._stepper.t, 'x': self._stepper.x.tolist(),
            'u': list(self._u), 'input_saturated': list(self._input_saturated),
            'seq': self._seq, 'input_seq': self._input_seq,
            'pending_input_seq': self._pending_sequence(),
            'applied_at_sim_time': self._applied_at,
            'wall_elapsed': elapsed, 'lag': max(0.0, elapsed - self._seq * self._dt_real),
            'status': self._status, 'parameters': self.parameters,
            'model': self._model_name, 'viewer': self._viewer,
        }
        if self._stepper.xdot is not None:
            state['xdot'] = self._stepper.xdot.tolist()
        if self._error is not None:
            state['error'] = self._error
        state.update(self._input_metadata())
        return state

    def _input_metadata(self):
        """Describe the latest received and applied commands; callers hold the lock after initialization."""
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
        """Return the newest queued or in-flight sequence; callers hold the lock after initialization."""
        command = self._pending or self._inflight
        return command.seq if command else None

    def snapshot(self):
        """Return the committed model state before sensor limits.

        The snapshot includes the applied input vector and its saturation
        flags. Use read_measurements() for named sensor readings.
        """
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            result['pending_input_seq'] = self._pending_sequence()
            result.update(self._input_metadata())
            return result

    def describe_signals(self):
        """Return detached metadata; direction is relative to the simulator."""
        return [signal.as_dict() for signal in (*self._state_signals, *self._input_signals,
                SignalDescriptor('time', 's', 'float64', 'output'),
                SignalDescriptor('status', '', 'string', 'output'))]

    def read_measurements(self):
        """Return named sensor readings, applied inputs, time and status.

        State readings are clipped at configured output bounds. Use
        snapshot() to inspect the model state before sensor limits.
        """
        state = self.snapshot()
        values = {signal.name: self._clamp(value, signal)
                  for signal, value in zip(self._state_signals, state['x'])}
        values.update({signal.name: value for signal, value in zip(self._input_signals, state['u'])})
        values.update(time=state['t'], status=state['status'])
        return values

    @property
    def active_controller(self):
        """Return the controller identity currently authorized to submit inputs."""
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
        Commands outside actuator bounds are clipped per input. Repeated
        values are fresh receipts; rejected commands change nothing.
        """
        requested = _vector('u', u, self._input_size, allow_empty=True).tolist()
        values, saturated = self._saturate_input(requested)
        controller_id = controller_identifier(controller_id)
        source_timestamp = validate_source_timestamp(source_timestamp)
        with self._lock:
            if self._status not in ('ready', 'running'):
                raise RuntimeError('simulation is not accepting inputs')
            if controller_id != self._active_controller:
                raise PermissionError(f'controller {controller_id!r} is not the active controller')
            self._command_seq += 1
            self._pending = _InputCommand(self._command_seq, tuple(values), tuple(saturated),
                                          controller_id, time.monotonic(), source_timestamp)
            self._received_command = self._pending
            return {'input_seq': self._command_seq, 'pending_input_seq': self._command_seq,
                    'accepted_input_seq': self._command_seq, 'applied_input_seq': self._input_seq,
                    'controller_id': controller_id, 'received_at_monotonic': self._pending.received_at}

    def start(self, *, duration=None, record=None, dt_real=None):
        """Start a worker; optionally record initial state and each committed step.

        ``duration`` is in simulated seconds. It must be a whole number of
        fixed steps so recorded time and requested duration agree exactly.
        ``record`` is a JSONL path, created exclusively to avoid overwrites.
        ``dt_real`` is the positive interval between step deadlines in wall-clock
        seconds. It defaults to ``dt``. The solver always advances by ``dt``.
        """
        period = self.dt if dt_real is None else _finite('dt_real', dt_real, positive=True)
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
            self._dt_real = period
            self._started = time.monotonic()
            self._status = 'running'
            self._snapshot = self._build_snapshot(0.0)
            self._thread = threading.Thread(target=self._run, args=(steps, stream),
                                            name='simulation', daemon=False)
            self._thread.start()
        return self

    def _wait_until(self, deadline):
        """Wait for a wall-clock deadline, returning true on a stop request."""
        remaining = deadline - time.monotonic()
        while remaining > threading.TIMEOUT_MAX:
            if self._stop_event.wait(threading.TIMEOUT_MAX):
                return True
            remaining = deadline - time.monotonic()
        return self._stop_event.wait(max(0.0, remaining))

    def _run(self, steps, stream):
        """Pace fixed intervals, apply held inputs and publish completed steps from the worker thread.

        Freeze inputs before waiting for each deadline; publish state only after a
        successful step. Record initial, committed and terminal snapshots when a
        stream is supplied, then close it even if integration or recording fails.
        """
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
                deadline = self._started + (self._seq + 1) * self._dt_real
                if self._wait_until(deadline):
                    break
                self._stepper.step(self.dt, held)
                with self._lock:
                    self._u = held
                    self._input_seq = input_seq
                    if command is not None:
                        self._applied_command = command
                        self._input_saturated = command.saturated
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

    def wait(self, timeout=None):
        """Wait for completion and return whether this run has stopped.

        ``timeout`` is an optional nonnegative number of wall-clock seconds.
        This method does not advance the model or request a stop.
        """
        if timeout is not None:
            timeout = _finite('timeout', timeout, nonnegative=True)
        thread = self._thread
        if thread is None:
            return self._status != 'ready'
        if thread is threading.current_thread():
            raise RuntimeError('the simulation worker cannot wait for itself')
        thread.join(timeout)
        return not thread.is_alive()

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

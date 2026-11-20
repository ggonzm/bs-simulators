"""Simulation state and wall-clock pacing, independent of the viewer."""

from __future__ import annotations

import json
import copy
import threading
import time
from dataclasses import dataclass
from collections.abc import Mapping
from os import PathLike
from typing import Any, Self

import numpy as np
from ._validation import _finite, _vector
from ._recording import _RecordingWorker
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
        # Recording operations and step publication share this lock. Always
        # acquire it before _lock; solver evaluations hold neither lock.
        self._recording_lock = threading.Lock()
        self._recorder = None
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
    def parameters(self) -> dict[str, Any]:
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

    def snapshot(self) -> dict[str, Any]:
        """Return the committed model state before sensor limits.

        The snapshot includes the applied input vector and its saturation
        flags. Use read_measurements() for named sensor readings.

        Returns
        -------
        dict[str, Any]
            Detached, JSON-compatible state and metadata. ``x`` and ``u`` are
            lists of floats; ``input_saturated`` is a list of booleans in input
            order. DAE snapshots also contain ``xdot``. See
            :ref:`snapshot-return-fields` for all fields and their types.
        """
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            result['pending_input_seq'] = self._pending_sequence()
            result.update(self._input_metadata())
        return result

    def describe_signals(self) -> list[dict[str, Any]]:
        """Return detached metadata; direction is relative to the simulator.

        Returns
        -------
        list[dict[str, Any]]
            State descriptions, then input descriptions, then ``time`` and
            ``status``. Each dictionary contains ``name``, ``unit``, ``dtype``,
            ``direction`` (strings), and ``lower`` and ``upper`` (floats or
            None). Input order defines the vector accepted by submit_input().
        """
        return [signal.as_dict() for signal in (*self._state_signals, *self._input_signals,
                SignalDescriptor('time', 's', 'float64', 'output'),
                SignalDescriptor('status', '', 'string', 'output'))]

    def read_measurements(self) -> dict[str, float | str]:
        """Return named sensor readings, applied inputs, time and status.

        State readings are clipped at configured output bounds. Use
        snapshot() to inspect the model state before sensor limits.

        Returns
        -------
        dict[str, float | str]
            Signal names mapped to numeric readings, including applied inputs,
            plus ``time`` (simulated seconds, float) and ``status`` (str).
            State readings use sensor bounds; input readings already reflect
            actuator saturation. Sequence and saturation metadata are available
            from snapshot(). The returned dictionary is independent of the run.
        """
        state = self.snapshot()
        values = {signal.name: self._clamp(value, signal)
                  for signal, value in zip(self._state_signals, state['x'])}
        values.update({signal.name: value for signal, value in zip(self._input_signals, state['u'])})
        values.update(time=state['t'], status=state['status'])
        return values

    @property
    def active_controller(self) -> str:
        """Return the controller identity currently authorized to submit inputs."""
        with self._lock:
            return self._active_controller

    def configure_controller(self, controller_id) -> None:
        """Explicitly select one controller before start.

        Switching while ready clears queued inputs and receipt metadata from
        the previous controller. The held initial value remains unchanged.

        Returns
        -------
        None
            Selection updates this simulation in place.
        """
        controller_id = controller_identifier(controller_id)
        with self._lock:
            if self._status != 'ready':
                raise RuntimeError('controller selection is only allowed before start')
            if controller_id != self._active_controller:
                self._active_controller = controller_id
                self._pending = None
                self._received_command = None

    def set_input(self, u) -> dict[str, Any]:
        """Trusted Python convenience: submit as the configured active controller.

        External bridges should use InMemoryAdapter with a fixed controller_id,
        which enforces ownership instead of impersonating the active controller.

        Returns
        -------
        dict[str, Any]
            The same acceptance acknowledgement as submit_input(). See
            :ref:`input-acknowledgement-fields` for its fields and types.
        """
        return self.submit_input(u, controller_id=self.active_controller)

    def submit_input(self, u, *, controller_id, source_timestamp=None) -> dict[str, Any]:
        """Validate and atomically enqueue a complete input vector.

        Receipt order uses local monotonic time and sequence numbers. A UTC
        source timestamp is diagnostic metadata and never schedules commands.
        Commands outside actuator bounds are clipped per input. Repeated
        values are fresh receipts; rejected commands change nothing.

        Returns
        -------
        dict[str, Any]
            Acceptance metadata with integer sequence numbers, controller
            identity (str), and local receipt time (float). Acceptance does
            not imply application. See :ref:`input-acknowledgement-fields`
            for the complete field descriptions.
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

    def start(self, *, duration=None, record=None, dt_real=None) -> Self:
        """Start a worker; optionally record initial state and each committed step.

        ``duration`` is in simulated seconds. It must be a whole number of
        fixed steps so recorded time and requested duration agree exactly.
        ``record`` is a JSONL path, created exclusively to avoid overwrites.
        Its time fields use capture-relative origins, beginning at zero even
        when the model starts at a nonzero t0. Captures can later be changed
        with start_recording() or closed with stop_recording().
        ``dt_real`` is the positive interval between step deadlines in wall-clock
        seconds. It defaults to ``dt``. The solver always advances by ``dt``.

        Returns
        -------
        Self
            This simulation instance after starting its worker thread. The
            worker may still be running when this method returns. Use wait()
            to wait for completion, or stop() to request shutdown.
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
        with self._recording_lock, self._lock:
            if self._status != 'ready':
                raise RuntimeError('simulation has already been started or stopped')
            initial = copy.deepcopy(self._snapshot)
            initial.update(self._input_metadata())
            initial.update(status='running', wall_elapsed=0.0,
                           pending_input_seq=self._pending_sequence())
            if record is not None:
                self._recorder = _RecordingWorker()
                try:
                    self._recorder.request('start', initial, record).wait()
                except BaseException:
                    self._recorder.finish(initial)
                    self._recorder.wait()
                    raise
            self._dt_real = period
            self._started = time.monotonic()
            self._status = 'running'
            self._snapshot = self._build_snapshot(0.0)
            self._thread = threading.Thread(target=self._run, args=(steps,),
                                            name='simulation', daemon=False)
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._status = 'ready'
                self._started = None
                self._snapshot = self._build_snapshot(0.0)
                if self._recorder is not None:
                    self._recorder.finish(dict(self._snapshot))
                    self._recorder.wait()
                raise
        return self

    def start_recording(self, path: str | PathLike[str]) -> None:
        """Begin a capture, replacing any active capture at a committed state.

        Parameters
        ----------
        path : str or os.PathLike[str]
            New JSONL destination, created exclusively. Parent directories must
            exist. The file starts with the latest committed state at t=0.

        Returns
        -------
        None
            The capture is active and its initial row has been flushed when
            this method returns. The model and worker keep their global clock.

        Raises
        ------
        RuntimeError
            If the simulation is not running or shutdown has been requested.
        OSError
            If the file cannot be opened or written. A failed opening or
            initial write leaves the previous capture active.

        Notes
        -----
        A change uses the latest published state, even during integration of
        the next step. No intermediate solver state is recorded. The previous
        file ends and the new file begins at that same committed boundary.
        The old file is closed after the new initial row is written; if closing
        the old file fails, the new capture remains active and the error is
        raised to the caller. See :ref:`capture-time-fields` for time fields.
        File operations run on a dedicated writer. Confirmation is awaited
        after releasing simulation locks, allowing physics to continue.
        """
        with self._recording_lock:
            with self._lock:
                if self._status != 'running' or self._stop_event.is_set():
                    raise RuntimeError('recording requires a running simulation')
            state = self._recording_snapshot()
            if self._recorder is None or not self._recorder.alive:
                self._recorder = _RecordingWorker()
            request = self._recorder.request('start', state, path)
        request.wait()

    def stop_recording(self) -> None:
        """Finish the active capture while the plant continues running.

        Returns
        -------
        None
            The final committed state has been written and the file closed.
            Calling this method with no active capture has no effect. The
            final row may repeat the preceding row's simulated time.

        Raises
        ------
        OSError
            If writing or closing fails. The capture is detached and its
            stream is closed; the caller receives the error. Stored background
            writer errors are also raised here while physics keeps running.
        """
        with self._recording_lock:
            recorder = self._recorder
            if recorder is None:
                return
            request = (recorder.request('stop', self._recording_snapshot())
                       if recorder.accepting else None)
        if request is not None:
            request.wait()
        else:
            recorder.wait()
            recorder.raise_error()

    def _recording_snapshot(self):
        """Read a committed boundary with current wall time while holding the recording lock."""
        state = self.snapshot()
        state['wall_elapsed'] = time.monotonic() - self._started
        return state

    def _wait_until(self, deadline):
        """Wait for a wall-clock deadline, returning true on a stop request."""
        remaining = deadline - time.monotonic()
        while remaining > threading.TIMEOUT_MAX:
            if self._stop_event.wait(threading.TIMEOUT_MAX):
                return True
            remaining = deadline - time.monotonic()
        return self._stop_event.wait(max(0.0, remaining))

    def _run(self, steps):
        """Pace fixed intervals, apply held inputs and publish completed steps from the worker thread.

        Freeze inputs before waiting for each deadline; publish state only after a
        successful step. Serialize publication with queue insertion. The writer
        owns file I/O and drains its queue independently when physics finishes.
        """
        try:
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
                with self._recording_lock:
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
                    if self._recorder is not None:
                        self._recorder.publish(recorded)
        except Exception as exc:
            with self._recording_lock, self._lock:
                self._status = 'error'
                self._error = f'{type(exc).__name__}: {exc}'
        finally:
            with self._recording_lock:
                with self._lock:
                    if self._status != 'error':
                        self._status = 'finished' if steps is not None and self._seq >= steps else 'stopped'
                    self._pending = None
                    self._inflight = None
                    self._snapshot = self._build_snapshot(time.monotonic() - self._started)
                    terminal = dict(self._snapshot)
                if self._recorder is not None:
                    self._recorder.finish(terminal)

    def wait(self, timeout=None) -> bool:
        """Wait for physics completion and for the recording writer to finish.

        ``timeout`` is an optional nonnegative number of wall-clock seconds.
        This method does not advance the model or request a stop.

        Returns
        -------
        bool
            True when physics and recording have stopped, including completion,
            explicit stop, or failure. False if either is still running after the timeout,
            or if the simulation has never been started or stopped. True
            does not imply success; inspect snapshot() for status and error.

        Raises
        ------
        OSError
            If the recording writer failed. This is raised after both workers
            finish and is independent of the plant's execution status.
        """
        if timeout is not None:
            timeout = _finite('timeout', timeout, nonnegative=True)
        thread = self._thread
        if thread is None:
            return self._status != 'ready'
        if thread is threading.current_thread():
            raise RuntimeError('the simulation worker cannot wait for itself')
        began = time.monotonic()
        thread.join(timeout)
        if thread.is_alive():
            return False
        remaining = None if timeout is None else max(0.0, timeout - (time.monotonic() - began))
        recorder = self._recorder
        if recorder is not None:
            if not recorder.wait(remaining):
                return False
            recorder.raise_error()
        return True

    def stop(self) -> None:
        """Request shutdown and wait for integration and queued recording to finish.

        Returns
        -------
        None
            Shutdown updates this simulation in place. From the worker itself,
            the method requests shutdown without waiting for its own thread.

        Raises
        ------
        OSError
            If the recording writer failed. Physics has stopped and the writer
            has released its file before this error is propagated to the caller.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._recording_lock, self._lock:
            if self._status == 'ready':
                self._status = 'stopped'
                self._pending = None
                self._snapshot = self._build_snapshot(0.0)
                if self._recorder is not None:
                    self._recorder.finish(dict(self._snapshot))
        if self._recorder is not None and thread is not threading.current_thread():
            self._recorder.wait()
            self._recorder.raise_error()

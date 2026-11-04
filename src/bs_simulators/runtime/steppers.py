"""Numerical integration between input boundaries, independent of pacing.

Steppers have one exclusive owner. They neither wait for wall time nor perform
communication I/O. Models use the normalized signatures supplied by Simulation;
physical model implementations remain in benchmark-systems or user code.
"""

from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp
from solve_dae.integrate import consistent_initial_conditions, solve_dae

from ._validation import _finite, _vector


def _readonly_input(name, values, size=None):
    result = _vector(name, values, size, allow_empty=True)
    result.flags.writeable = False
    return result


def _fixed_indices(values):
    if values is None:
        return None
    try:
        values = tuple(values)
    except TypeError as exc:
        raise ValueError('fixed_x must be a sequence of state indices or None') from exc
    if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer))
           for value in values):
        raise ValueError('fixed_x must contain integer state indices')
    if any(value < 0 for value in values) or len(set(values)) != len(values):
        raise ValueError('fixed_x indices must be nonnegative and unique')
    try:
        return np.asarray(values, dtype=int)
    except (ValueError, OverflowError) as exc:
        raise ValueError('fixed_x indices must be representable integers') from exc


class _ConfiguredStepper:
    """Shared state access and configuration; public steppers define the solver."""

    _methods = ()

    def __init__(self, *, method, rtol, atol):
        if not isinstance(method, str) or method not in self._methods:
            raise ValueError(f'method must be one of {self._methods}')
        self._method = method
        self._rtol = _finite('rtol', rtol, positive=True)
        self._atol = _finite('atol', atol, positive=True)
        self._model = None
        self._input_size = None
        self._state = None

    def _require_initialized(self):
        if self._state is None:
            raise RuntimeError('stepper is not initialized; call initialize() first')

    def _initial_values(self, model, x0, t0, u0):
        if self._state is not None:
            raise RuntimeError('stepper is already initialized; create a new stepper for another run')
        if not callable(model):
            raise ValueError('model must be callable')
        return (_finite('t0', t0), _vector('x0', x0), _readonly_input('u0', u0))

    def _step_values(self, dt, u):
        self._require_initialized()
        dt = _finite('dt', dt, positive=True)
        held = _readonly_input('u', u, self._input_size)
        target = self._state[0] + dt
        if not np.isfinite(target) or target <= self._state[0]:
            raise ValueError('dt must advance finite simulation time')
        return target, held

    @property
    def t(self) -> float:
        """Current absolute simulation time; available after initialization."""
        self._require_initialized()
        return self._state[0]

    @property
    def x(self) -> np.ndarray:
        """Detached copy of the most recently committed state vector."""
        self._require_initialized()
        return self._state[1].copy()

    @property
    def xdot(self) -> np.ndarray | None:
        """Detached DAE derivatives, or None for an initialized ODE stepper."""
        self._require_initialized()
        return None if self._state[2] is None else self._state[2].copy()


class ODEStepper(_ConfiguredStepper):
    """Advance a normalized ``model(t, x, u)`` using SciPy solve_ivp.

    Parameters
    ----------
    method : str, optional
        SciPy integration method; default is RK45.
    rtol, atol : float, optional
        Positive scalar relative and absolute tolerances.

    Notes
    -----
    Construct this object with solver configuration, then initialize it once
    with a model, initial state, absolute time and complete input vector.
    Every step freezes a detached, read-only input vector over its interval.
    A failed initialization or step does not commit partial state. Successful
    initialization is single-use; the object is not thread-safe and must be
    owned exclusively by its simulation.
    """

    _methods = ('RK23', 'RK45', 'DOP853', 'Radau', 'BDF', 'LSODA')

    def __init__(self, *, method='RK45', rtol=1e-7, atol=1e-9):
        super().__init__(method=method, rtol=rtol, atol=atol)

    def initialize(self, model, x0, *, t0=0.0, u0) -> None:
        """Bind a model and initial state after validating its derivative.

        ``model`` returns a finite one-dimensional derivative vector of the
        same size as x0. ``u0`` defines the complete input size and may be an
        empty vector for an autonomous plant. Time uses the model's units.
        """
        time, state, held = self._initial_values(model, x0, t0, u0)
        _vector('model derivative', model(time, state.copy(), held), state.size)
        self._model = model
        self._input_size = held.size
        self._state = (time, state, None)

    def step(self, dt, u) -> np.ndarray:
        """Advance one interval with constant input, returning detached state.

        The model receives absolute time. Committed state changes only after
        the solver succeeds and its output has been validated. ``xdot`` remains
        None; evaluate the model separately when ODE derivatives are needed.
        """
        target, held = self._step_values(dt, u)
        start, state, _ = self._state

        def rhs(time, values):
            return _vector('model derivative', self._model(time, values, held), state.size)

        result = solve_ivp(rhs, (start, target), state.copy(), t_eval=[target],
                           method=self._method, rtol=self._rtol, atol=self._atol)
        if not result.success:
            raise RuntimeError(f'ODE integration failed: {result.message}')
        new_state = _vector('integrated state', result.y[:, -1], state.size)
        self._state = (target, new_state, None)
        return new_state.copy()


class DAEStepper(_ConfiguredStepper):
    """Advance a normalized ``model(t, x, xdot, u)`` with solve-dae.

    Parameters
    ----------
    xdot0 : array-like
        Finite one-dimensional initial derivative guess, matching the state
        size supplied later to initialize(). A detached copy is retained.
    fixed_x : sequence of int or None, optional
        State indices held exactly fixed while consistency is recomputed.
        None fixes every state component. For a differential/algebraic model,
        supply its differential-state indices to allow algebraic states to
        adjust at input changes. An empty sequence leaves all states free.
    method : str, optional
        solve-dae integration method, Radau by default; BDF is also supported.
    rtol, atol : float, optional
        Positive scalar relative and absolute tolerances used for consistency
        calculation and integration.

    Notes
    -----
    Consistency is recomputed at initialization and every step boundary with
    the new frozen input. All calculations use local state/derivative copies;
    a failed consistency calculation or integration leaves committed time,
    state and derivatives unchanged. Only fixed_x indices are guaranteed
    continuous through consistency correction. The installed consistency
    routine supports differentiation index at most one; this wrapper does
    not perform index reduction or promise support for higher-index systems.
    Successful initialization is single-use and requires exclusive ownership.
    """

    _methods = ('Radau', 'BDF')

    def __init__(self, xdot0, *, fixed_x=None, method='Radau', rtol=1e-7, atol=1e-9):
        super().__init__(method=method, rtol=rtol, atol=atol)
        self._xdot0 = _vector('xdot0', xdot0)
        self._fixed_x = _fixed_indices(fixed_x)

    def _consistent(self, model, time, state, derivative, held):
        size = state.size
        fixed = np.arange(size) if self._fixed_x is None else self._fixed_x
        if fixed.size and np.any(fixed >= size):
            raise ValueError('fixed_x indices must lie within the state vector')
        original = state.copy()

        def residual(at, values, rates):
            return _vector('model residual', model(at, values, rates, held), size)

        initial_residual = residual(time, state.copy(), derivative.copy())
        try:
            consistent_x, consistent_xdot, _ = consistent_initial_conditions(
                residual, time, state.copy(), derivative.copy(),
                fixed_y0=fixed.copy(), rtol=self._rtol, atol=self._atol,
            )
        except Exception as exc:
            raise RuntimeError(f'DAE consistency calculation failed: {exc}') from exc
        consistent_x = _vector('consistent state', consistent_x, size)
        consistent_xdot = _vector('consistent derivatives', consistent_xdot, size)
        # Explicit restoration preserves fixed components, including roundoff
        # and signed zeros, even if a backend returns a perturbed copy.
        consistent_x[fixed] = original[fixed]
        corrected_residual = residual(time, consistent_x.copy(), consistent_xdot.copy())
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            scale = self._atol + self._rtol * np.abs(initial_residual)
            error = np.linalg.norm(corrected_residual / scale) / np.sqrt(size)
        if not np.isfinite(error) or error > 1.0:
            raise RuntimeError('DAE consistency calculation did not satisfy residual tolerances')
        return consistent_x, consistent_xdot

    def initialize(self, model, x0, *, t0=0.0, u0) -> None:
        """Bind the plant after finding consistent initial state/derivatives.

        Full state and derivative sizes must match. Initial algebraic guesses
        come from x0; xdot0 supplies derivative guesses. The complete input
        vector u0 determines the input size and may be empty.
        """
        time, state, held = self._initial_values(model, x0, t0, u0)
        derivative = _vector('xdot0', self._xdot0, state.size)
        state, derivative = self._consistent(model, time, state, derivative, held)
        self._model = model
        self._input_size = held.size
        self._state = (time, state, derivative)

    def step(self, dt, u) -> np.ndarray:
        """Recompute boundary consistency, integrate, then commit atomically.

        State components listed in fixed_x remain exact during the boundary
        correction. After integration, xdot is taken from the solver's yp
        output; it is not inferred by differencing states. All properties and
        the returned vector expose detached copies.
        """
        target, held = self._step_values(dt, u)
        start, previous, previous_derivative = self._state
        state, derivative = self._consistent(
            self._model, start, previous, previous_derivative, held,
        )

        def residual(time, values, rates):
            return _vector('model residual', self._model(time, values, rates, held), state.size)

        result = solve_dae(residual, (start, target), state.copy(), derivative.copy(),
                           method=self._method, t_eval=[target],
                           rtol=self._rtol, atol=self._atol)
        if not result.success:
            raise RuntimeError(f'DAE integration failed: {result.message}')
        new_state = _vector('integrated state', result.y[:, -1], state.size)
        new_derivative = _vector('integrated derivatives', result.yp[:, -1], state.size)
        self._state = (target, new_state, new_derivative)
        return new_state.copy()

"""A corrected local DAE boundary must not leak through a failed solve."""

from types import SimpleNamespace

import numpy as np
import pytest

from bs_simulators.runtime import steppers


def test_failed_dae_integration_does_not_commit_algebraic_boundary_correction(monkeypatch):
    def residual(t, x, xdot, u):
        return np.array([xdot[0] + x[0] - u[0], x[1] - x[0] - u[0]])

    stepper = steppers.DAEStepper([0, 0], fixed_x=[0])
    stepper.initialize(residual, [1, 1], u0=[0])
    stepper.step(0.01, [0])
    before = (stepper.t, stepper.x.copy(), stepper.xdot.copy())
    captured = []

    def failed_solve(model, t_span, x0, xdot0, **kwargs):
        captured.append((x0.copy(), xdot0.copy()))
        # Consistency succeeded locally: only the algebraic state jumped.
        assert x0[0] == before[1][0]
        assert x0[1] == pytest.approx(before[1][0] + 2)
        assert np.linalg.norm(model(t_span[0], x0, xdot0), np.inf) < 1e-6
        return SimpleNamespace(success=False, message='deliberate backend integration failure')

    monkeypatch.setattr(steppers, 'solve_dae', failed_solve)
    with pytest.raises(RuntimeError, match='deliberate backend integration failure'):
        stepper.step(0.01, [2])

    assert len(captured) == 1
    assert stepper.t == before[0]
    np.testing.assert_array_equal(stepper.x, before[1])
    np.testing.assert_array_equal(stepper.xdot, before[2])

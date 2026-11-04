"""Small model configurations; physical equations remain in benchmark-systems."""

from benchmark_systems.models import pendulum

from .runtime.core import Simulation
from .runtime.signals import SignalDescriptor
from .runtime._validation import _finite


PENDULUM_STATE_SIGNALS = (
    SignalDescriptor('theta', 'rad', 'float64', 'output'),
    SignalDescriptor('omega', 'rad/s', 'float64', 'output'),
)
PENDULUM_INPUT_SIGNALS = (
    SignalDescriptor('torque', 'N m', 'float64', 'input'),
)


def create_pendulum(*, m=1.0, L=1.0, drag=0.1, theta0=0.6, omega0=0.0,
                    dt=0.01, u0=0.0, active_controller='http'):
    """Return a Simulation configured for the pendulum and its browser scene.

    This is a convenience factory, not another simulation implementation.
    All physics is evaluated by benchmark_systems.models.pendulum.
    """
    return Simulation(
        pendulum,
        x0=[_finite('theta0', theta0), _finite('omega0', omega0)],
        u0=[_finite('u0', u0)], dt=dt,
        parameters={'m': _finite('m', m, positive=True),
                    'L': _finite('L', L, positive=True),
                    'drag': _finite('drag', drag, nonnegative=True)},
        state_signals=PENDULUM_STATE_SIGNALS,
        input_signals=PENDULUM_INPUT_SIGNALS,
        active_controller=active_controller, viewer='pendulum',
    )

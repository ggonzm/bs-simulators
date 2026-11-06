# Simulation and steppers

`Simulation` runs a numerical model in its own thread. The model describes
physics, the stepper advances one integration interval,
and the simulation manages wall-clock pacing, held inputs and recording.
The [core class diagram](api.md#functional-core) shows their relationships.
See [Common pitfalls](common-pitfalls.md) for examples of timing and sensor
limits in a control loop.

## An ODE model

```python
from benchmark_systems.models import dc_motor
from bs_simulators import Simulation

simulation = Simulation(
    dc_motor,
    x0=[0.0, 0.0, 0.0],
    u0=[1.4],
    parameters={"J": 0.02, "b": 0.1, "Ke": 0.1, "Kt": 0.1, "R": 2.0, "L": 0.5},
    dt=0.01,
)
simulation.start(duration=2.0)
simulation.wait()
simulation.read_measurements()
```

The motor has three states: angular position, angular velocity and current.
`x0` supplies these initial values. `u0` is the initial held voltage vector.
The physics function is imported directly from `benchmark-systems`; its
parameters are passed as keyword arguments. `dt` sets input boundaries,
while SciPy chooses its internal integration steps. `duration` is measured
from the simulation's initial time and must be a whole number of intervals.

`start()` returns immediately after starting the worker. `wait()` blocks
until completion; `wait(timeout=0.1)` returns whether it finished within that
wall-clock timeout. `stop()` requests shutdown and waits for integration to
finish. A simulation and its stepper belong to a single run; create fresh
objects to repeat an experiment.

(execution-cadence)=
## Set the execution cadence

`dt` is the amount of simulated time advanced in each interval. The optional
`dt_real` argument to `start()` sets the target time between intervals in
wall-clock seconds. It must be positive and finite, and defaults to `dt`.

For a simulation configured with `dt=1.0`, this run advances 60 simulated
seconds in 60 intervals, with a target wall-clock duration of 6 seconds:

```python
simulation.start(duration=60.0, dt_real=0.1)
simulation.wait()
```

The worker schedules each interval against the clock at the start of the run.
If it falls behind, it continues through every interval. The solver still
advances by `dt`, and each interval uses the last applied input if there is no
new command. A controller sending commands every 100 ms of wall-clock time
would target one command per simulated second in this example, subject to
the timing of its commands relative to the simulation boundaries.

## Inputs and model signatures

Every command is a complete vector, including single-input commands such as
`simulation.set_input([2.0])`. Without a new command the last input remains
active indefinitely. A newly accepted input is applied at an interval
boundary, not retrospectively to an interval already in progress.

The default model calls are:

```text
ODE: model(t, x, **parameters, u=...)
DAE: model(t, x, xdot, **parameters, u=...)
```

One input becomes a scalar `u`; multiple inputs become a vector `u`. An empty
`u0=[]` calls an autonomous model without an input keyword. For a model that
expects a one-element vector, pass `input_map=lambda u: {"u": u}`. A custom
mapping can also target different keyword names, for example
`input_map=lambda u: {"force": u[0], "bias": u[1]}`. Parameter names must not
collide with mapped input names.

If stepper is omitted or None, Simulation creates a fresh
`ODEStepper(method="RK45")` for that run. To change the method or tolerances,
pass an explicit configuration such as `stepper=ODEStepper(method="Radau")`
after importing `ODEStepper`. Residual models require an explicit
`DAEStepper`. Function names and signatures are not used to infer model type.

## Named signals

By default the measurements are named `x0`, `x1`, ... for state components and
`u0`, `u1`, ... for input components, plus `time` and `status`. To provide
meaningful names and units, supply ordered `SignalDescriptor` objects through
`state_signals` and `input_signals`.

For a one-input, three-state motor these could be:

```python
from bs_simulators import SignalDescriptor

state_signals = [
    SignalDescriptor("theta", "rad", "float64", "output"),
    SignalDescriptor("omega", "rad/s", "float64", "output"),
    SignalDescriptor("current", "A", "float64", "output"),
]
input_signals = [SignalDescriptor("voltage", "V", "float64", "input")]
```

Pass these lists when constructing `Simulation`. Each list follows the order
of its corresponding vector. Each configured endpoint is inclusive.

Input bounds represent actuator limits. The simulator clips each component
of `u0` and of every valid command before passing it to the model.
`snapshot()["u"]` gives the effective input, and `snapshot()["input_saturated"]`
contains one boolean per component in the same order. A flag is true only
when clipping changed the applied command. It stays with that input until a
new command is applied.

Output bounds represent sensor limits. Browser instruments also use these
bounds as [display scales](catalogue.md#instrument-scales-from-signal-descriptions).
The `InMemoryAdapter` can read and write named signals without knowing the
model's state layout.

(snapshot-and-measurements)=
## Simulation snapshots and sensor measurements

| Method | What it returns | When to use it |
| --- | --- | --- |
| `snapshot()` | The committed model state `x` before sensor limits, the applied input `u`, and timing and command metadata including `input_saturated`. | Inspect or diagnose the simulated plant. |
| `read_measurements()` | A mapping from signal names to sensor readings clipped at configured output bounds, applied inputs, time and status. | Feed a controller with the values available to its instruments. |

For example, if the model current is 2.5 A and the current sensor has an
upper bound of 2 A, the corresponding component of `snapshot()["x"]` is
2.5, while `read_measurements()["current"]` is 2. The model continues
integrating from its actual state. Both methods read the latest committed
state at the time of the call without advancing the simulation. A running
worker can commit a step between calls.

Over HTTP, [`GET /api/state` and `GET /api/measurements`](http.md) provide
the corresponding views.

## A DAE model

A DAE returns a residual vector `F(t, x, xdot, u) = 0`. It needs an initial
derivative guess in addition to the initial state. For example, the published
quadrotor residual can run through the same simulation class:

```python
import numpy as np
from benchmark_systems.models import quadrotor_dae
from bs_simulators import Simulation, DAEStepper

simulation = Simulation(
    quadrotor_dae,
    stepper=DAEStepper(xdot0=np.zeros(12)),
    x0=np.zeros(12),
    u0=[5.2, 5.2, 5.2, 5.2],
    parameters={"Ixx": 1.2, "Iyy": 1.3, "Izz": 2.1, "k": 0.9,
                "L": 0.25, "m": 2.0, "drag": 0.0},
)
simulation.start(duration=0.5)
simulation.wait()
simulation.snapshot()["xdot"]
```

The stepper computes consistent initial derivatives and integrates with
`solve-dae`. DAE snapshots include `xdot` alongside `x`. The example starts
at zero attitude with equal rotor inputs; it does not configure a drone
browser scene.

For a system containing algebraic variables, `fixed_x` specifies which state
indices must remain fixed during consistency correction. Normally these are
the differential-state indices. The other components may adjust to satisfy
the algebraic equations at initialization and input changes. For example,
`DAEStepper(xdot0=[0.0, 0.0], fixed_x=[0])` permits the second state to change
at an input boundary while preserving the first state there.

By default `fixed_x=None` fixes every state component and adjusts derivatives
only. This requires the supplied states to satisfy any purely algebraic
constraints. The consistency routine supports differentiation index at most
one; higher-index models require a separate initialization/index-reduction
strategy and are not generally supported by this stepper.

A failed consistency calculation or solver interval does not commit a partial
state or mark its command as applied. The worker exposes an `error` status
and message. No integration or communication is performed by reading a
snapshot.

## Stepper interface

`Stepper` is a structural Python protocol with these members:

| Member | Purpose |
| --- | --- |
| `initialize(model, x0, *, t0, u0)` | Bind normalized model and initial data once |
| `t` | Committed absolute model time |
| `x` | Detached committed state vector |
| `xdot` | Detached DAE derivative vector, or `None` for ODE |
| `step(dt, u)` | Integrate one interval atomically with frozen input |

`Simulation` calls `step` internally. Selecting a stepper does not introduce
another public execution mode. `ODEStepper` supports the SciPy methods RK23,
RK45, DOP853, Radau, BDF and LSODA. `DAEStepper` supports Radau and BDF. Both
accept positive scalar `rtol` and `atol` tolerances.

## Browser observation

The HTTP adapter accepts any `SimulationPort` independently of the model or
stepper. Without an assigned scene, its state, signal and input API routes
work and the viewer route returns 404. Set `viewer="pendulum"` only for a
compatible pendulum state and parameters; scenes are never inferred from
vector length.

The [catalogue](catalogue.md) supplies optional configurations for named
signals and browser selection. Its values can be overridden for each run;
this direct model-based API does not require a configuration file. See the
[pendulum example](pendulum.md) and the
{download}`interactive notebook <../examples/pendulum_core.ipynb>`.

# Simple pendulum example

We will construct a pendulum simulation directly from its numerical model,
inspect the object and send torque commands from a Python scan loop. The
{download}`interactive notebook <../examples/pendulum_core.ipynb>` provides
the same workflow with separate cells for inspecting variables.

## Construct the simulator

```python
import time
import numpy as np

from benchmark_systems.models import pendulum
from bs_simulators import Simulation, SignalDescriptor

simulation = Simulation(
    pendulum,
    x0=[np.pi - 0.25, 0.0],
    u0=[0.0],
    parameters={"m": 1.0, "L": 1.0, "drag": 0.1},
    dt=0.01,
    state_signals=[
        SignalDescriptor("theta", "rad", "float64", "output"),
        SignalDescriptor("omega", "rad/s", "float64", "output"),
    ],
    input_signals=[SignalDescriptor("torque", "N m", "float64", "input")],
    active_controller="python",
    viewer="pendulum",
)
```

`pendulum` supplies the equations. Its state is `x=[theta, omega]` and its
input is `u=[torque]`. We will choose an initial angle of `pi - 0.25` rad,
close to the upright position, with zero angular velocity and torque. Zero
angle is the downward position. `m`, `L` and `drag` specify mass, length and
viscous damping.

`dt=0.01` sets the integration interval in simulated seconds. Omitting
`stepper` creates a fresh `ODEStepper(method="RK45")`; SciPy chooses its
internal integration steps. The signal descriptors name each vector
component and specify its units. `active_controller` selects who may write
inputs; `viewer` selects a compatible scene for optional HTTP observation.
Construction initializes the numerical state without starting a thread.

## Inspect the object

```python
simulation.parameters
simulation.describe_signals()
simulation.read_measurements()
simulation.snapshot()
```

`parameters` returns the physical settings. `describe_signals()` describes
the ordered state and input components. `read_measurements()` returns the
named sensor readings `theta` and `omega`, the applied `torque`, and `time`
and `status`. Use these readings as controller feedback. `snapshot()` gives
the model state before sensor limits, the applied input vector and command
metadata for inspection. This example leaves the sensors unbounded, so the
state readings match the model values when both calls observe the same step.
See {ref}`simulation snapshots and sensor measurements <snapshot-and-measurements>`.

## Connect a controller adapter

```python
from bs_simulators import InMemoryAdapter

controller = InMemoryAdapter(simulation, controller_id="python")
controller.read_measurements()
```

The adapter uses the simulation's `SimulationPort` methods. It reads the
same named measurements and supplies `controller_id="python"` with each
write, matching the selected controller. It does not own the worker or its
clock. We will use this adapter in the scan loop below.

## Add an optional browser viewer

```python
import threading
from bs_simulators.comms.http import create_server

server = create_server(simulation, port=0)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
viewer_url = f"http://127.0.0.1:{server.server_address[1]}/"
print(viewer_url)
```

We will open the printed address before running the control loop. `port=0`
asks the operating system for an available local port. The server observes
the existing `simulation` object; it does not construct another simulator or
advance time. The browser initially displays the initial state. Python
remains the selected controller, so this HTTP connection is for observation.
This section can be skipped when running only the core.

## Run a scan loop

```python
scan_period = 0.05
kp, kd = 20.0, 4.0
reference = np.pi
previous_error = 0.0

simulation.start()
for _ in range(100):
    measurements = controller.read_measurements()
    error = reference - measurements["theta"]
    error_rate = (error - previous_error) / scan_period
    previous_error = error
    torque = kp * error + kd * error_rate
    controller.write_inputs([torque])
    time.sleep(scan_period)
simulation.stop()
```

`start()` launches the independent simulation worker and returns immediately.
Each scan reads the latest committed measurements, computes a PD torque and
submits a complete input vector through `controller.write_inputs([torque])`.
The adapter delegates that write to `Simulation.submit_input()` with its
controller identity. Direct Python code can alternatively use
`simulation.set_input([torque])` for the selected controller. `kp` acts on
the angular error and `kd` acts on its finite-difference derivative. We will
use `pi` radians as the upright reference.

The worker integrates at `dt=0.01` s while the controller waits approximately
`0.05` s between commands. The last input is held between commands, including
when the controller takes longer than expected. A new command is applied at
an integration boundary; accepting it does not mean it has already affected
the state. The [runtime sequence diagram](api.md#runtime-sequence) shows
these separate operations.

After 100 scans, `stop()` requests shutdown and waits for the worker. The
sleep is an approximate scan delay; loop execution adds to the scan period.
For a finite experiment without a controller loop, use
`start(duration=5.0)` followed by `wait()` instead.

## Inspect the final state and close the viewer

```python
simulation.read_measurements()
simulation.snapshot()
```

The stopped object remains available for inspection. In a snapshot, `t` is
the committed simulated time, `x` is the state vector and `u` is the applied
input. `input_seq` identifies the command used by the latest committed state.
Create a fresh `Simulation` to repeat the experiment.

If the optional HTTP server was started, close it after inspecting the final
state:

```python
server.shutdown()
server.server_close()
server_thread.join()
```

Stopping the simulation leaves the HTTP server available to show its final
state until this shutdown. The server retains the simulation object passed
to `create_server()`.

## Continue with the core

[Simulation and steppers](simulation.md) explains model signatures, named
signals and ODE/DAE integration. [Architecture](architecture.md) explains how
controllers and adapters use the same runtime through Python protocols.

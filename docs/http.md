# HTTP observation and control

The local HTTP interface observes a simulation independently of its physics
worker. The current server listens on loopback and uses Python's standard
library. The browser polls the latest state approximately every 50 ms.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Detached state, inputs, timing and command metadata |
| `GET /api/measurements` | Named sensor readings, applied inputs, time and status |
| `GET /api/signals` | Ordered signal descriptions |
| `POST /api/input` | Submit a complete input vector |
| `GET /` | Configured browser scene, if available |
| `GET /assets/...` | Registered shared viewer scripts and styles |

A request body such as `{"u": [2.0]}` submits a torque command to the pendulum.
An optional `source_timestamp` must be an ISO 8601 UTC timestamp; it records
the sender's time without scheduling the command. A successful request
returns HTTP 202, acknowledging acceptance.

## Select the controller

The server submits commands with controller identity `http`. Construct the
simulation with `active_controller="http"` to permit these writes. Other controller identities
can observe through HTTP, but HTTP cannot replace their input. Identities
provide local ownership arbitration, not device authentication.

Compare the acknowledgement's `accepted_input_seq` with the snapshot's
`input_seq` to observe application. `pending_input_seq` reports a pending or
in-flight command. `applied_at_sim_time` records its integration boundary.
If no command arrives, the last applied input remains active indefinitely.

`GET /api/state` returns the model state, effective input `u` and the matching
`input_saturated` flags. `GET /api/measurements` applies configured sensor
limits to state readings, matching `Simulation.read_measurements()`.
See [Common pitfalls](common-pitfalls.md) for examples of sensor and actuator
limits and how to confirm application of a command.

## Start a server from Python

```python
import threading
from benchmark_systems.models import pendulum
from bs_simulators import Simulation
from bs_simulators.comms.http import create_server

simulation = Simulation(
    pendulum,
    x0=[0.6, 0.0],
    u0=[0.0],
    parameters={"m": 1.0, "L": 1.0, "drag": 0.1},
    viewer="pendulum",
)
server = create_server(simulation, port=8765)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
simulation.start()
```

The server is ready at creation time but starts serving when its thread runs.
The simulation starts independently. This example keeps Python as controller;
set `active_controller="http"` in `Simulation(...)` to accept HTTP input writes.
In a notebook, keep these objects available and run the shutdown cell when
finished:

```python
simulation.stop()
server.shutdown()
server.server_close()
server_thread.join()
```

`viewer="pendulum"`, `viewer="dc-motor"` and `viewer="neutralization"` select
the packaged scenes for compatible model states.
Other models remain fully available through API endpoints without
requiring an animation. Reading or closing a viewer never advances or owns
the simulation clock.

The [adapter interaction diagram](api.md#runtime-sequence) shows the path
from a write request to a committed state. For terminal execution, the
optional [CLI](catalogue.md) accepts `--controller http` to select HTTP input.

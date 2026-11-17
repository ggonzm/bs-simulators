# HTTP observation and control

The local HTTP interface observes a simulation independently of its physics
worker. FastAPI runs on Uvicorn and listens on loopback. The browser polls
the latest state approximately every 50 ms.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/state` | Detached state, inputs, timing and command metadata |
| `GET /api/measurements` | Named sensor readings, applied inputs, time and status |
| `GET /api/signals` | Ordered signal descriptions |
| `POST /api/input` | Submit a complete input vector |
| `GET /` | Configured browser scene, if available |
| `GET /assets/...` | Registered shared viewer scripts and styles |
| `GET /docs` | Interactive API documentation for the local server |

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

## Try a command from the interactive API

Start the catalogue pendulum in a terminal and leave it running.

```shell
bs-simulators --catalogue pendulum --controller http
```

Open the local [interactive API](http://127.0.0.1:8765/docs), then use **Try it
out** and **Execute** on these endpoints.

1. Read `GET /api/measurements`. For the catalogue pendulum, `theta` is the
   angle in radians, `omega` is the angular velocity in radians per second and
   `torque` is the currently applied input in newton-metres.
2. Send `{"u": [2.0]}` to `POST /api/input`. The complete vector contains one
   torque command. A `202` response includes its `accepted_input_seq`.
3. Read `GET /api/state` again until `input_seq` matches that sequence. The
   snapshot then shows the effective input in `u` and the boundary at which
   it was applied in `applied_at_sim_time`. A subsequent measurement shows
   the applied torque under its signal name. If `input_seq` has already
   advanced past the accepted sequence, the current snapshot alone cannot
   confirm whether that earlier command was applied.

The same requests work from a separate SIL client. Refer to
`GET /api/signals` for each model's signal names, units and input order.
The local server also serves its OpenAPI schema at `GET /openapi.json`.

### Input errors

The application reports input errors as JSON objects with an `error` string.

| Status | Cause |
| --- | --- |
| `400` | Invalid JSON, input vector or timestamp, or unsupported transfer encoding |
| `403` | The request fails the Host or Origin check, or HTTP is not the active controller |
| `408` | The server timed out while reading the request body |
| `409` | The simulation has stopped accepting commands |
| `411` | The request lacks a valid Content-Length |
| `413` | The body is empty or exceeds 4096 bytes |
| `415` | The body has a content type other than `application/json` |

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

`viewer="pendulum"`, `viewer="dc-motor"`, `viewer="neutralization"` and
`viewer="johansson"` select the packaged scenes for compatible model states.
Other models remain fully available through API endpoints without
requiring an animation. Reading or closing a viewer never advances or owns
the simulation clock.

The [adapter interaction diagram](api.md#runtime-sequence) shows the path
from a write request to a committed state. For terminal execution, the
optional [CLI](catalogue.md) accepts `--controller http` to select HTTP input.

## Open the viewer in Google Colab

Colab routes the iframe through an HTTPS proxy. Start a read-only server
for the viewer using the exact proxy origin returned by the notebook.
With the `simulation` from the example above, replace its HTTP startup
lines with the following cell. Use a free port in the Colab runtime.

```python
from google.colab import output

port = 8766
proxy_origin = output.eval_js(f"google.colab.kernel.proxyPort({port})")
server = create_server(simulation, port=port, colab_origin=proxy_origin)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
simulation.start()
output.serve_kernel_port_as_iframe(port, height=650)
```

The iframe can read the simulation and render the scene. If an HTTP SIL
controller runs in the same runtime, start a separate default server on
another loopback port and point the controller there. Use the shutdown
sequence above for each server you start.

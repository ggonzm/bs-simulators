# Live pendulum simulation and replay

The `create_pendulum()` factory configures the generic `Simulation` with the
numerical pendulum model from `benchmark-systems`.
It advances continuously, receives complete torque commands and publishes the
latest state for observation. Completed recordings can be replayed separately
with the original Matplotlib animation helpers.

## Start an experiment

From a prepared source checkout, run:

```shell
pdm run python -m bs_simulators --port 8765 --dt 0.01
```

Open [the local viewer](http://127.0.0.1:8765/). The browser draws the pendulum
using Canvas and polls its state approximately every 50 ms. This display rate
is independent of the integration interval `dt`. Closing the browser leaves
the simulation running; Ctrl+C in the simulator terminal stops it.

The default pendulum has mass 1 kg, length 1 m, damping coefficient 0.1,
initial angle 0.6 rad and zero angular velocity and torque. Useful options are:

| Option | Meaning |
| --- | --- |
| `--m`, `--length`, `--drag` | Mass, length and rotational damping |
| `--theta0`, `--omega0` | Initial angle in rad and angular velocity in rad/s |
| `--u0` | Initial torque in N m, held until a command is applied |
| `--dt` | Integration interval in simulated seconds |
| `--controller` / `--active-controller` | Explicit controller identity; default `http` |
| `--duration` | Optional simulated duration, a whole number of `dt` intervals |
| `--record` | New JSONL output path |

Zero angle points vertically downward. The state order is `[theta, omega]`,
time is in seconds, and the input vector is `[torque]` in N m.

## Send commands from another process

With the simulator running, start the HTTP controller example in a second
terminal:

```shell
pdm run python examples/pendulum_sil_controller.py
```

The controller submits a sequence of torque commands and waits until each is
reported as applied. Its timing is based on simulated time. After it exits,
the last applied torque remains active indefinitely until another valid
command arrives.

## Record and replay

```shell
pdm run python -m bs_simulators --duration 15 --record pendulum-run.jsonl
```

The recording contains the initial snapshot, one snapshot per completed
interval and a terminal snapshot. The terminal entry may repeat the last
physical time and sequence; it does not represent an extra integration step.
The command preserves existing files and closes the HTTP service when the
run ends.

After the experiment, export a replay:

```shell
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py pendulum-run.jsonl --output pendulum-run.gif
```

The replay filters repeated terminal timestamps and passes the recorded angle
trajectory to `benchmark_systems.animations.Pendulum` and `AutoAnimation`.
Pillow writes the GIF. Replay is independent of the live HTTP service.

## Access signals directly from Python

`InMemoryAdapter` provides the same input and measurement interface without
HTTP requests:

```python
import time
from bs_simulators import InMemoryAdapter, create_pendulum

simulation = create_pendulum(active_controller="python", theta0=0.6)
adapter = InMemoryAdapter(simulation, controller_id="python")
adapter.write_signals({"torque": 2.0})
simulation.start(duration=1.0)
try:
    while adapter.read_snapshot()["status"] == "running":
        time.sleep(0.02)
    print(adapter.read_measurements())
finally:
    simulation.stop()
```

The matching controller identities authorize this adapter's writes. The
command is accepted before the worker starts, then applied at an integration
boundary. The loop waits for this one-second experiment to finish before
reading its final measurements. `stop()` also ensures cleanup if the
experiment is interrupted.

The available named signals are:

| Signal | Meaning | Unit | Direction |
| --- | --- | --- | --- |
| `theta` | Pendulum angle | rad | output |
| `omega` | Angular velocity | rad/s | output |
| `torque` | Applied torque | N m | input, also readable |
| `time` | Simulated time | s | output |
| `status` | Execution status | none | output |

`describe_signals()` returns names, units, types, directions and optional
bounds. `read_measurements()` returns values under these names, while
`read_snapshot()` includes execution and command metadata. Named writes must
contain the complete set of input signals: currently `{"torque": value}`.
The equivalent vector write is `write_inputs([value])`.

For a longer named-signal example with optional browser observation:

```shell
pdm run python examples/pendulum_signal_adapter.py --port 8766
```

This selects `plc-demo` as its controller identity. The browser can observe
the experiment, while HTTP input writes are rejected. The example is an
in-process Python bridge demonstration, not a connection to a physical PLC.

## Select the controller

Set `active_controller` when creating the simulation, or call
`configure_controller(controller_id)` before starting it. Switching the
controller while the engine is ready clears commands pending from the
previous controller. Controller selection is fixed during a run.

An adapter's `controller_id` must match that selection. The HTTP adapter
always identifies as `http`; its request body cannot choose a different
identity. Thus `active_controller="python"` permits Python adapter writes
and browser observation, while rejecting HTTP writes. These identifiers
provide local ownership arbitration, not device authentication.

## Distinguish acceptance from application

Each valid command receives a sequence number and local monotonic receipt
time. The acknowledgement confirms acceptance without waiting for the
integration worker. The latest pending vector is selected at an interval
boundary and held constant throughout that interval. A command arriving
during the interval affects a later interval. Several pending writes may
coalesce into the most recent command.

Compare an acknowledgement's `accepted_input_seq` with a snapshot's
`input_seq` to observe application. `pending_input_seq` identifies a pending
or in-flight command, and `applied_at_sim_time` identifies the applied
command's simulation-time boundary. `received_input_seq` reports the most
recently accepted command separately.

An optional `source_timestamp` is an ISO 8601 UTC timestamp using `Z` or a
zero UTC offset. It is diagnostic metadata: it neither schedules a command
nor changes local sequence order. Local receipt timing uses a monotonic clock.
Sending the same torque again is a new valid receipt. Invalid commands leave
the accepted input and receipt metadata unchanged.

## HTTP endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Packaged pendulum viewer |
| `GET /api/signals` | Signal descriptions |
| `GET /api/state` | Latest detached snapshot |
| `POST /api/input` | Submit `{"u": [2.0]}` with optional `source_timestamp` |

A successful POST returns HTTP 202 and an acceptance acknowledgement. The
snapshot contains `t`, `x`, `u`, execution `status`, `active_controller`,
command sequences and timing information. Applied command metadata uses
`controller_id` and `source_timestamp`; the most recent receipt has separate
`received_*` fields. The server listens on loopback for local experiments.

## Timing and current scope

The worker aims to advance one simulated second per wall-clock second using
a monotonic clock. SciPy takes internal solver steps within each fixed `dt`
interval. If computation falls behind, physical intervals are preserved and
the snapshot's `lag` increases; this is soft real time, without guaranteed
deadlines.

Communication adapters perform their network work outside the physics worker.
JSONL recording currently writes from that worker, so slow storage can add
lag. This configuration provides the pendulum ODE, a local HTTP interface and a
two-dimensional Canvas scene. The [generic engine](simulation.md) also accepts
other ODE models and index-1 DAEs through an explicitly selected stepper.
Industrial bridges, higher-index DAE initialization, persistent CasADi
integration, pause/reset and 3D scenes are not implemented.

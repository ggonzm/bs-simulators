# Record and replay

We will record a finite experiment using the same `Simulation` API:

```python
from benchmark_systems.models import pendulum
from bs_simulators import Simulation

simulation = Simulation(
    pendulum, x0=[0.6, 0.0], u0=[0.0],
    parameters={"m": 1.0, "L": 1.0, "drag": 0.1},
)
simulation.start(duration=5.0, record="experiment.jsonl")
simulation.wait()
```

`duration` is in simulated seconds and must be a whole number of integration
intervals. `record` selects a new JSONL file. The capture records its initial
state, every committed interval and a terminal snapshot, then closes the
file. `wait()` waits for completion. Each destination must be a new file.
No HTTP server is needed for recording.

The final row may repeat the last physical time. It records completion and
is not another integration interval. Snapshots contain `t`, `x`, `u`, model
parameters, status and separate acceptance/application metadata. DAE snapshots
also contain `xdot`.

## Capture separate experiments while the plant runs

We will start the plant once and record two experiments with a gap between
them. The state and held input continue evolving during that gap.

```python
from time import sleep
from bs_simulators import load_configuration

simulation = load_configuration("pendulum").create().start()
simulation.start_recording("experiment_1.jsonl")
sleep(2)
simulation.stop_recording()

sleep(1)
simulation.start_recording("experiment_2.jsonl")
sleep(2)
simulation.stop_recording()
simulation.stop()
```

`start_recording(path)` creates a file and immediately writes the latest
committed state with `t = 0`. It requires a running plant. `stop_recording()`
writes a final copy of the latest committed state and closes the file before
returning. Repeated calls with no active capture have no effect. Both methods
return `None`. The `sleep()` intervals above are wall-clock seconds, so the
recorded simulated duration also depends on `dt` and `dt_real`.

Calling `start_recording("experiment_2.jsonl")` while the first capture is
active closes the first and starts the second at the same committed state.
The operation uses the latest published boundary even when the solver is
working on the next interval. Retained samples are written in order to the
corresponding file. File-change requests are ordered with step publication.

The destination is opened and its initial row written before replacing an
active capture. If either action fails, the previous capture stays active.
Opening an existing file raises `FileExistsError`. A failure while closing
the previous file is raised to the caller with the new capture already active.
`stop_recording()` detaches and closes its file even if writing its final
row fails. Completion or `simulation.stop()` automatically closes the active
capture after the writer drains the retained samples.

## Writer thread and rolling sample buffer

The simulation publishes samples to a circular buffer. A dedicated thread
owns JSON serialization, file creation, writing, flushing and closing.
The writer swaps the pending buffer for an empty one under a short lock,
then processes the extracted batch outside the lock. Queries and controller
input submissions continue to use the simulation's shared state.

```{figure} _static/architecture/recording-buffer-swap.svg
:alt: The writer replaces the pending buffer with an empty one under a short lock. It writes the extracted batch to JSONL while the simulation fills the new buffer.
:width: 100%

The writer takes ownership of the previous buffer. Serialization and writing
proceed outside the lock while the simulation fills the new buffer.
```

The pending sample buffer holds 1024 samples. Once full, a new sample
replaces its oldest pending sample. Acquisition continues, and the file can
contain gaps in `t` and `seq`. Values retain their original times and global
step sequences. The writer can also hold one extracted batch of up to 1024
samples while the pending buffer fills again.

Capture requests are preserved separately from rolling samples. A shared
ordering number lets the writer merge requests and retained samples in
their original order, including across file changes. Initial and final
capture states belong to those requests and are preserved through sample
buffer overwrites.

`start_recording()` and `stop_recording()` wait for their file operation to
be confirmed after releasing the simulation's locks. `wait(timeout)` covers
both physics completion and writer completion using the same timeout budget.
`stop()` waits for physics and for the writer to finish. These caller-side
waits can be delayed by slow storage while physics proceeds independently.
The buffer's short lock, Python scheduling and numerical workload still
affect soft real-time execution.

An opening failure is raised by `start_recording()`. A background writing
or flushing failure is stored internally and raised as `OSError` by
`stop_recording()`, or by `wait()`/`stop()` when finalizing the run. The plant
keeps its own execution status and can continue after a recording failure.
If possible, the failed file ends with a `recording_error` description.
After observing the error and allowing the writer to close, a new capture
can be started on the running plant.

(capture-time-fields)=
## Time fields within each capture

Every capture uses its initial committed simulated time as its own origin.
If a capture starts when `snapshot()["t"]` is 120 seconds, its first row has
`t = 0`, followed by retained step times. With no overwritten samples these
are `dt`, `2*dt`, and so on. This also applies to
`start(record=...)` with a nonzero model `t0`.

| Field | Meaning in a recording |
| --- | --- |
| `t` | Simulated seconds since the capture's initial committed state. |
| `applied_at_sim_time` | Input application time relative to the same origin. It can be negative when the held input was applied before the capture began. |
| `wall_elapsed` | Wall-clock seconds since capture activation, starting at zero. Initial and final rows use the wall time of the recording request. |
| `lag` | Global simulation scheduling delay in wall-clock seconds, preserving the worker's pacing diagnostic. |
| `seq`, `input_seq` and other command sequences | Global simulation sequence numbers, preserved across captures. |
| Monotonic receipt times and source timestamps | Original receipt and UTC source metadata, preserved across captures. |

Only copies written to the file use these relative time origins. The model,
`snapshot()`, `read_measurements()` and HTTP viewer continue using the global
simulation clock. In particular, a time-dependent model keeps receiving its
absolute model time throughout all captures.

Each file contains an initial and final row, even if no step occurs between
them. Closing or rotating a capture can repeat the last recorded simulated
time. Its final `status` describes the plant, so a manually closed capture
normally ends with `status="running"`.

## Replay a pendulum trajectory

```shell
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py experiment.jsonl --output experiment.gif
```

The replay example filters repeated timestamps and uses
`benchmark_systems.animations.Pendulum` and `AutoAnimation` to export a GIF.
It consumes the completed recording independently of the live service. This
particular replay expects pendulum states and the length parameter `L`.
Recordings from other models can be inspected or plotted with their own
state conventions.

## Inspect the stored data

```python
import json
from pathlib import Path

rows = [json.loads(line) for line in Path("experiment.jsonl").read_text().splitlines()]
final_state = rows[-1]
final_state["t"], final_state["x"], final_state["status"]
```

`lag` is measured in wall-clock seconds relative to the target cadence for
the latest committed interval. The target elapsed time after `n` intervals
is `n * dt_real`, where `dt_real` defaults to `dt`. Scheduling, integration
and recording can affect the lag. The timestamps `t` in the recording remain
in capture-relative simulated seconds. Use the recording's `t` values to
plot the experiment from zero; `seq` continues to identify global plant steps.

## Record from the terminal

The optional [catalogue and CLI](catalogue.md) can construct and record the
same experiment without a Python script:

```shell
pdm run bs-simulators --catalogue pendulum --duration 5 --record experiment.jsonl
```

The CLI owns the HTTP server as well as the worker, and closes both when the
finite experiment ends.

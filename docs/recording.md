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
intervals. `record` selects a new JSONL file. The worker records its initial
state, every committed interval and a terminal snapshot, then closes the
file. `wait()` waits for completion. Existing files are preserved rather
than overwritten. No HTTP server is needed for recording.

The final row may repeat the last physical time. It records completion and
is not another integration interval. Snapshots contain `t`, `x`, `u`, model
parameters, status and separate acceptance/application metadata. DAE snapshots
also contain `xdot`.

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
in simulated seconds.

## Record from the terminal

The optional [catalogue and CLI](catalogue.md) can construct and record the
same experiment without a Python script:

```shell
pdm run bs-simulators --catalogue pendulum --duration 5 --record experiment.jsonl
```

The CLI owns the HTTP server as well as the worker, and closes both when the
finite experiment ends.

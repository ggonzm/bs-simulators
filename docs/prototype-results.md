# Pendulum example and expected behavior

The pendulum example demonstrates a live numerical plant, a controller
interface and browser observation. Its purpose is to check the complete
experiment flow, including what happens after a controller stops sending
commands.

## Run a named-signal experiment

```shell
pdm run python examples/pendulum_signal_adapter.py --duration 6 --port 8766 --record signal-run.jsonl
```

This starts a six-second simulated experiment and selects `plc-demo` as the
active controller. The example sends torques of 0, 2 and -1 N m at simulated
times near 0, 2 and 4 seconds. It waits for each accepted command to appear
as applied, then stops sending commands while the plant continues. At the
end, the applied torque remains -1 N m and the execution status is `finished`.
The browser at [port 8766](http://127.0.0.1:8766/) observes the same plant.

The controller is a Python demonstration. No physical PLC or industrial
transport is connected.

## Inspect the recording

```python
import json
from pathlib import Path

rows = [
    json.loads(line)
    for line in Path("signal-run.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
print(rows[-1]["t"], rows[-1]["u"], rows[-1]["status"])
```

The final entry reports the completed simulation time, applied torque and
terminal status. Floating-point time accumulation can produce a value close
to 6 rather than an exact decimal representation. The recording contains
600 physical intervals at `dt=0.01`, plus initial and terminal snapshots;
the terminal snapshot may repeat the final physical timestamp.

`input_seq` and `applied_at_sim_time` identify which command generated a
committed interval. Receipt fields identify the latest accepted command,
which may still be pending. These fields make it possible to distinguish
input submission from physical application without relying on browser timing.

## View the completed trajectory

```shell
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py signal-run.jsonl --output signal-run.gif
```

The exported GIF replays the recorded angles and retained torques using the
original Matplotlib animation helpers. It does not connect to the live worker
or send new commands.

During live observation, closing the browser should leave the worker running.
When the experiment reaches its configured duration, both the worker and
local HTTP service close. The last valid input is retained throughout the
remaining simulation after command submission ends.

## Interpret timing results

The `lag` field compares committed simulation time with elapsed wall time.
It can vary with solver work, operating-system scheduling and recording
storage. A recorded run provides evidence for that machine and configuration;
it is not a general timing benchmark or a strict real-time guarantee.

For automated checks of integration accuracy, input retention, controller
ownership and HTTP behavior, run the repository's test suite as described in
[the validation checklist](../MIGRATION_VALIDATION.md).

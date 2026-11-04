# bs-simulators

Live simulation, controller adapters and browser visualization for
[`benchmark-systems`](https://github.com/ggonzm/benchmark-systems) models.

Version **0.1.0** provides a model-independent `Simulation` with RK45 ODE
integration by default and configurable ODE/index-1 DAE steppers, a local HTTP interface, a pendulum Canvas viewer and
JSONL recording. It requires Python 3.13 or later and
`benchmark-systems>=1.1.2,<2`, installed from PyPI. Numerical and symbolic
models, along with offline Matplotlib animations, remain available through
`benchmark-systems`.

## Install from a source checkout

Run these commands from the repository root with PDM available:

```shell
pdm sync -G dev
```

This prepares the project's environment from its lockfile, including test and
notebook tooling. PDM resolves the model library and other package dependencies
from PyPI.

## Run the pendulum

In the first terminal:

```shell
pdm run python -m bs_simulators --port 8765 --dt 0.01
```

Open [the local viewer](http://127.0.0.1:8765/). The simulator runs independently
of the browser and retains the last valid torque until another command arrives.
Press Ctrl+C in the simulator terminal to stop it. The installed
`bs-simulators` command provides the same CLI.

In a second terminal:

```shell
pdm run python examples/pendulum_sil_controller.py
```

This example sends torque commands from a separate process and waits until
they are applied. When the controller exits, the simulator continues with the
last applied value.

## Use named signals

```shell
pdm run python examples/pendulum_signal_adapter.py --port 8766
```

This example writes the `torque` signal and reads measurements through
`InMemoryAdapter`. It selects `plc-demo` as the active controller and optionally
opens a read-only browser view. It demonstrates the Python interface without
connecting a physical PLC or implementing an industrial communication protocol.

The [simulation guide](docs/simulation.md) explains model configuration, ODE/DAE
steppers and named signals. The [pendulum guide](docs/pendulum.md) explains controller selection, signals,
timestamps and HTTP requests.

## Use the interactive example

Open [`examples/pendulum_core.ipynb`](examples/pendulum_core.ipynb) with the
project's Python environment. It demonstrates a pendulum controller scan loop
and exposes signal descriptions, measurements and snapshots for inspection.
Its cells define the experiment's initial condition, reference and feedback
settings; browser observation is optional.

## Record and replay

```shell
pdm run python -m bs_simulators --duration 15 --record pendulum-run.jsonl
```

The simulator records the initial state, each integration interval and a
terminal snapshot. It stops after 15 simulated seconds and closes its HTTP
service. An existing recording file is preserved rather than overwritten.

For Matplotlib replay and GIF export:

```shell
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py pendulum-run.jsonl --output pendulum.gif
```

The `replay` extra supplies the original animation helpers and Pillow. The
replay example reads the completed recording and exports a GIF; it does not
restart the live simulator.

## Package structure

```text
src/bs_simulators/
├── runtime/    simulation clock, integration, controller selection and signals
├── comms/      in-memory adapter and local HTTP transport
└── viewers/    packaged browser resources
```

`SimulationPort` is a structural Python protocol used by the adapters. The
runtime reads numerical models from `benchmark-systems` and runs without
importing the HTTP or browser components. See the
[architecture guide](docs/migration.md) for the data flow.

## Validation and scope

```shell
pdm run pytest
pdm build
```

The test suite checks integration, input retention, controller ownership,
communication behavior and packaged resources. PDM builds the wheel and source
distribution, including the HTML viewer.

The simulator provides soft real time: scheduling or computation can introduce
lag, and strict timing deadlines are not guaranteed. The current implementation
uses Python's standard-library HTTP server, an argparse CLI and browser Canvas.
Higher-index DAEs, OPC UA/Modbus bridges, pause/reset controls and 3D scenes
are outside the current prototype.
MIT licensed. `LICENSE.benchmark-systems` preserves the original library's
license notices.

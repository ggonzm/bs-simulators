# bs-simulators

Live experiments for [benchmark-systems](https://benchmark-systems.readthedocs.io/en/latest/)
models, with independent simulation workers, external control inputs, local
HTTP observation and trajectory recording. Python 3.13 or later is required.

## Prepare the environment

```shell
pdm sync -G dev
pdm run bs-simulators --help
```

The model library is installed from PyPI. The development group includes the
kernel and data tools for the example notebook.

## Run a simulation from Python

```python
from benchmark_systems.models import pendulum
from bs_simulators import Simulation

simulation = Simulation(pendulum, x0=[0.6, 0.0], u0=[0.0],
                        parameters={"m": 1.0, "L": 1.0})
simulation.start(duration=1.0)
simulation.wait()
```

Direct numerical callables do not need a catalogue entry. The
[interactive notebook](examples/pendulum_core.ipynb) demonstrates a scan loop,
object inspection and optional HTTP visualization.

## Optional catalogue and CLI

```shell
pdm run bs-simulators --list-catalogue
pdm run bs-simulators --list-models
pdm run bs-simulators --model pendulum --describe
pdm run bs-simulators --catalogue pendulum --parameter "m=2.0"
```

Open <http://127.0.0.1:8765/> for the pendulum viewer. Select exactly one of
`--catalogue`, `--config` or `--model`; running
without a selection returns an error. The optional catalogue contains bundled,
read-only TOML defaults; users can override parameters and initial conditions
or supply a custom file. Creating a simulation never writes a configuration.
A three-state motor and a DAE neutralization process also include browser scenes:

```shell
pdm run bs-simulators --catalogue dc-motor --parameter "R=3.0" --u0 "[2.0]"
pdm run bs-simulators --catalogue neutralization
```

The current state remains available at `/api/state`. For the separate HTTP
controller example, start the pendulum with `--controller http` and run
`pdm run python examples/pendulum_sil_controller.py` in another terminal.
The last valid input remains active until a new command arrives.

## Documentation

```shell
pdm sync -G dev -G docs
pdm run docs-serve
```

Open <http://127.0.0.1:8001/>. The preview rebuilds when documentation or source
changes. Start with [installation](docs/installation.md), the
[pendulum example](docs/pendulum.md), [simulation](docs/simulation.md),
[architecture](docs/architecture.md) and [API reference](docs/api.md). The
[configuration catalogue and CLI](docs/catalogue.md) provide optional shortcuts.

## Record and replay

```shell
pdm run bs-simulators --catalogue pendulum --duration 5 --record experiment.jsonl
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py experiment.jsonl --output experiment.gif
```

The replay example uses the original Matplotlib animation helpers for a
completed pendulum recording. The live browser scene uses Canvas.

## Development

```shell
pdm run pytest
pdm run docs-build
pdm run docs-test
pdm build
```

See [architecture](docs/architecture.md) and [development](docs/development.md).
The runtime supports configurable ODE integration and index-1 DAE consistency.
It offers soft real time; industrial protocol bridges, higher-index DAE
initialization and scenes beyond the three bundled examples require further implementation.

MIT licensed. `LICENSE.benchmark-systems` preserves the model library's notices.

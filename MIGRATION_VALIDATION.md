# Validation checklist

This checklist covers the `bs-simulators` 0.1.0 package, its pendulum example
and its dependency on `benchmark-systems>=1.1.2,<2` from PyPI. Validation
results should identify the package versions, interpreter and environment
used.

## Prepare the project environment

```shell
pdm sync -G dev -G replay
pdm run python -c "import bs_simulators, benchmark_systems; print(bs_simulators.__version__, benchmark_systems.__version__)"
```

PDM installs the locked runtime, development and replay dependencies into the
project environment. The model package should resolve from PyPI within the
declared version range.

## Run the automated suite

```shell
pdm run pytest
```

The suite exercises numerical integration and absolute time, held inputs,
interval boundaries, model-independent ODE/DAE execution, consistent algebraic
input boundaries, controller selection, timestamp validation, structural
Python protocols and standalone HTTP processes. It also covers independent
engine implementations, named multi-input submission, resource loading and
distribution contents when a wheel is available.

Record the actual pass/fail/skip summary from the environment being checked.

## Build and inspect distributions

```shell
pdm build
pdm run pytest tests/test_distribution.py
```

PDM builds the wheel and source distribution. The distribution check inspects
the HTML viewer resource, version metadata, console entry point and external
model-package requirement. It also verifies that `benchmark_systems` is not
bundled inside the companion wheel. `BS_SIMULATORS_WHEEL` can select an
explicit wheel path when validating a separate artifact.

The wheel should include `bs_simulators/viewers/pendulum.html` and declare
version-based requirements for both the main model dependency and the
optional replay dependency. It should not require a local checkout or contain
Python cache files.

## Verify an installed wheel

Install the built wheel into a separate clean environment with its declared
dependencies. Then change to a directory outside the source checkout and run:

```shell
python -c "import bs_simulators, benchmark_systems; print(bs_simulators.__file__); print(benchmark_systems.__file__)"
bs-simulators --help
```

The import locations should belong to that environment, and the installed
console command should display its options. Start the simulator there and
check that `GET /`, `GET /api/state` and `GET /api/signals` work with the
packaged resource rather than source-directory files.

## Check the visible experiment

Follow the [pendulum guide](docs/pendulum.md) to run a controller separately
from the simulator. Confirm that the browser shows the pendulum and its
simulation time, input commands are observed as applied, and closing the
browser does not stop integration. For a finite run, confirm its terminal
snapshot and replay the recording as a GIF.

Record timing observations as measurements of a particular run. Soft-real-time
behavior does not establish strict deadlines or validation with physical
industrial equipment.

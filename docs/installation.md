# Installation

The package requires Python 3.13 or later. From a source checkout, PDM creates
and manages the project environment:

```shell
pdm sync -G dev
pdm run bs-simulators --help
```

Runtime dependencies, including `benchmark-systems>=1.2.0`, are resolved
from PyPI. The development group includes the kernel and data tools needed
by the example notebook. Select this project's `.venv` Python interpreter
when opening the notebook in an editor.

To export recorded experiments with the original Matplotlib animation helpers:

```shell
pdm sync -G dev -G replay
```

To build and preview this documentation:

```shell
pdm sync -G dev -G docs
pdm run docs-serve
```

The default documentation address is <http://127.0.0.1:8001/>. Documentation
tooling belongs to its own development group and is not required to execute
a simulation. See [Development](development.md) for build and test commands.

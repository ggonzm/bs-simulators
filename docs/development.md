# Development and documentation

Use the project's Python 3.13 environment and PDM:

```shell
pdm sync -G dev -G docs -G replay
pdm run pytest
pdm build
```

The test suite covers numerical reference solutions, complete-vector input
retention, interval boundaries, controller selection, consistent DAE input
boundaries, custom steppers, HTTP processes and catalogue overrides. The
wheel contains browser resources and bundled TOML configurations, while the
model library remains a separate PyPI dependency.

## Build the documentation

```shell
pdm run docs-build
pdm run docs-test
```

The HTML build treats warnings as errors. Executable documentation examples
use the doctest builder. Output stays in the ignored `docs/_build/` directory.

For a live local preview with automatic rebuilding:

```shell
pdm run docs-serve
```

Open <http://127.0.0.1:8001/>. Stop the preview with Ctrl+C in its terminal.
The documentation uses Sphinx, MyST and the PyData theme. The same source can
be hosted by Read the Docs using the repository's `.readthedocs.yaml`.

When documentation dependencies change, regenerate their pinned requirements:

```shell
pdm export --no-default -G docs --without-hashes -o docs/requirements.txt
```

Model configurations live in `src/bs_simulators/catalogue/`. A new entry is a
TOML package resource. It should document its state/input order and units,
provide usable initial data, and reference a public model. Check it through
both the Python loader and the CLI. A specific browser scene is optional.

## CLI organization

The `bs_simulators.cli` package keeps the terminal application separate from
the numerical runtime:

| Module | Responsibility |
| --- | --- |
| `cli.commands` | Typer options, selection validation and command routing |
| `cli.configuration` | TOML literal parsing and public-model metadata inspection |
| `cli.execution` | Simulation and HTTP lifecycle, cleanup and exit status |
| `cli.presentation` | Shared Rich tables, panels and literal-value rendering |

`from bs_simulators.cli import app` is the application entrypoint. Help and
catalogue listing must remain independent of scientific and HTTP imports.
Presentation functions create their consoles at call time so output respects
the active terminal, redirection and command-test capture. Dynamic values
are rendered as literal text, preserving brackets in vectors and model
parameter descriptions.

## Browser scale checks

Viewer scale and rendering checks use Node.js's built-in test runner (Node
18 or later), without npm dependencies or a frontend build:

```shell
node --test tests/viewer_scales.test.mjs
```

These checks cover partial/asymmetric limits, constant ranges, clipping for
visualization only, and association by state-vector position. Node.js is a
development tool for these checks; it is not required to run the Python
package or open its viewers.

# Configuration catalogue and CLI

The catalogue is a set of TOML configurations **shipped with the package**.
Loading an entry reads one of those files. Calling `create()` constructs an
ordinary `Simulation` in memory; it does not create a TOML file, add an entry
to the catalogue, or overwrite the bundled configuration. Constructing
`Simulation` directly also does not save a configuration.

Physical-parameter and initial-vector overrides apply only to the new run.
To keep a reusable setup, we will write a separate TOML file explicitly and
pass its path with `--config`. The CLI reads that file without modifying it.
Trajectory recording with `--record` writes results to a JSONL file; it is
independent of configuration loading.

The [configuration diagram](api.md#optional-configuration-and-access) shows
how loaded data constructs the same core used in the Python examples.

## Select what to run

Execution requires exactly one of these selections:

| Selection | Source of initial data and physical parameters |
| --- | --- |
| `--catalogue NAME` | A bundled TOML entry, with optional CLI overrides |
| `--config FILE` | A complete user-written TOML file, with optional CLI overrides |
| `--model NAME` | A public `benchmark_systems.models` function, with initial vectors and parameters supplied on the CLI |

No model is selected implicitly. Omitting the selection, or combining these
three alternatives, returns a usage error before a worker or server starts.
Use `--help` for general options. These commands inspect available choices
without starting a simulation:

```shell
pdm run bs-simulators --list-catalogue
pdm run bs-simulators --list-models
pdm run bs-simulators --model pendulum --describe
pdm run bs-simulators --catalogue pendulum --describe
```

`--list-models` displays a table with each public numerical model's name,
ODE/DAE type and short description. `--describe` shows the model signature in a panel, followed by separate
tables for physical parameters and simulation settings. The parameter table
distinguishes required values, function defaults and selected values. The
complete model documentation follows in a panel, with section headings and
parameter declarations highlighted.
For a catalogue entry or custom file it also shows configured values, initial
vectors, integration settings, controller identity and viewer. Overrides can
be supplied with `--describe` to inspect the proposed settings without running
them. A raw model can be described before its required parameters are supplied.

The **Execution (optional)** help group contains optional settings:

| Option | When omitted |
| --- | --- |
| `--port` | Serve HTTP on local port `8765`; use `0` for an available port |
| `--duration` | Continue until interrupted or stopped |
| `--dt-real` | Use the effective `dt` as the interval in wall-clock seconds |
| `--record` | Do not write a trajectory recording |
| `--controller` | Inherit the configuration identity, or use `python` with `--model` |

These defaults do not replace the required model/configuration selection.

To advance neutralization by one simulated second every 100 ms of wall-clock
time, use:

```shell
pdm run bs-simulators --catalogue neutralization --dt 1 --dt-real 0.1 --duration 60
```

`--dt-real` sets the {ref}`execution cadence <execution-cadence>`
for this run. `--duration` remains in simulated seconds.

## Choose a configuration

```shell
pdm run bs-simulators --list-catalogue
pdm run bs-simulators --catalogue dc-motor --parameter "R=3.0" --u0 "[2.0]"
```

The bundled entries are:

| Name | Public benchmark model | States | Input | Browser scene |
| --- | --- | --- | --- | --- |
| `pendulum` | `pendulum` | angle, angular velocity | torque | Pendulum Canvas |
| `dc-motor` | `dc_motor` | angle, angular velocity, current | voltage | Motor shaft and indicators |
| `neutralization` | `neutralization` (DAE) | Wa4, Wb4, height, pH | alkaline feed q3 | Reactor, level and pH gauges |

Each entry includes a browser scene following its Matplotlib counterpart in
`benchmark-systems`. State and named signals remain available through
`/api/state` and `/api/signals`. Models without a configured scene can still
use these endpoints and do not have a page at `/`.

A repeated `--parameter NAME=VALUE` overrides individual physical parameters. Values use
TOML syntax: numbers, arrays and inline tables are supported. The final value
wins if the same name is repeated. `--x0` and `--u0` replace complete
vectors; `--dt` and `--controller` override their configuration values.

## Inspect and reuse a configuration

```{doctest}
>>> from bs_simulators.catalogue import list_configurations, load_configuration
>>> list_configurations()
['dc-motor', 'neutralization', 'pendulum']
>>> configuration = load_configuration("pendulum")
>>> configuration.parameters["m"]
1.0
>>> experiment = configuration.create(parameters={"m": 2.0})
>>> experiment.parameters["m"], configuration.parameters["m"]
(2.0, 1.0)
>>> experiment.stop()
```

Each `create()` call constructs a new simulation and integrator. Parameter
keys are merged, with nested values copied. Initial-vector overrides replace
the full vector. The configuration object and its source file retain their
original values. Configurations load independently of the working directory.
Listing and reading metadata do not start a worker or import a model.

## Use a custom file

A custom file can select any public numerical function exported by
`benchmark_systems.models`. For example, save this as `motor.toml`:

```toml
name = "motor-experiment"
model = "dc_motor"
x0 = [0.0, 0.0, 0.0]
u0 = [2.0]
dt = 0.01
active_controller = "python"

[parameters]
J = 0.02
b = 0.1
Ke = 0.01
Kt = 0.01
R = 1.0
L = 0.5
```

Then run:

```shell
pdm run bs-simulators --config motor.toml --parameter "R=3.0" --duration 2.0
```

`--config`, `--catalogue` and `--model` are alternatives. A custom file is complete:
`model`, `parameters`, `x0` and `u0` are required. An omitted name defaults to
the filename stem. `model` is the public function name, not an import path;
configuration loading never evaluates Python expressions. Unknown parameter
names are rejected before a worker starts.

Without signal descriptions, measurements are named `x0`, `x1`, ... and `u0`,
`u1`, ... . Optional `[[state_signals]]` and `[[input_signals]]` tables define
ordered names, units, dtype and direction. Their counts must match the vectors.
The bundled TOML files provide complete examples:

```{literalinclude} ../src/bs_simulators/catalogue/pendulum.toml
:language: toml
```

## Run a model without a configuration file

```shell
pdm run bs-simulators --model pendulum --x0 "[0.2, 0.0]" --u0 "[0.0]" --parameter "m=1.0" --parameter "L=1.0" --duration 2.0
```

`--model` resolves the numerical function by its public name. `--x0` and
`--u0` are required complete vectors, even for a single input. Physical
parameters without function defaults, such as `m` and `L`, must be provided.
Parameters with defaults can be omitted: this pendulum uses the function's
`drag=0.0`. No catalogue entry is loaded or created; required physical values
such as mass and length must still be supplied explicitly. Unknown parameter names are rejected before execution.

The initial time is zero, the integration interval defaults to `0.01` s,
and the controller defaults to `python`. Use `--dt` and `--controller` to
change the latter settings. Raw model execution has no configured browser
scene or named signals: `/api/state` and `/api/signals` remain available, with
default names `x0`, `x1`, ... and `u0`, `u1`, ... . Use a custom TOML file or
the direct Python API to configure signal descriptions and viewer selection.

## Configure the integrator

An ODE uses a fresh RK45 integrator unless another stepper is supplied. For another ODE
method, include:

```toml
[stepper]
kind = "ode"
method = "Radau"
rtol = 1e-7
atol = 1e-9
```

For a residual model choose `kind = "dae"` explicitly and provide `xdot0`,
with one derivative guess per state. `fixed_x` optionally lists indices held
fixed during consistency corrections. DAE methods are `Radau` and `BDF`;
ODE methods are described in [Simulation](simulation.md). The consistency
routine supports index at most one.

In Python, a fresh explicit stepper can override the configuration:

```python
from bs_simulators import ODEStepper

simulation = configuration.create(stepper=ODEStepper(method="DOP853"))
```

Numerical callables outside `benchmark-systems` can always be passed directly
to `Simulation`. They do not need a catalogue entry or configuration file.

The CLI accepts the same integrator specification as an inline TOML table:

```shell
pdm run bs-simulators --catalogue pendulum --stepper '{kind="ode", method="DOP853"}'
```

`--stepper` replaces the complete integrator specification for that run.
For a raw DAE selected with `--model`, an explicit table with `kind="dae"`
and `xdot0=[...]` is required. The derivative guess must have the same size
as `--x0`; `fixed_x=[...]` can identify states preserved during consistency
correction. The CLI never silently selects a DAE solver. A custom TOML file
is usually clearer for larger DAE setups.

CLI output uses a consistent set of tables and panels. Startup shows the
API and optional viewer addresses. Completion shows status, simulated time
and the recording path when requested. Worker failures appear in an error
panel and return a nonzero exit code. Usage errors retain the standard
Typer error presentation.

## Three bundled experiments

```shell
pdm run bs-simulators --catalogue pendulum
pdm run bs-simulators --catalogue dc-motor
pdm run bs-simulators --catalogue neutralization
```

Run one command at a time and open the displayed viewer address. Use distinct
`--port` values to run several experiments simultaneously. Add
`--controller http` when an external HTTP client will provide inputs.

The bundled pendulum uses `m=1`, `L=2`, zero drag, initial state
`[pi - 0.1, 0]` and zero torque. The motor uses `J=0.02`, `b=0.1`,
`Ke=Kt=0.01`, `R=1`, `L=0.5`, zero initial state and `u=[1]` V.
These initial operating points follow the numerical model examples.
Simulation intervals and integrator settings are configured independently
of an offline experiment's output sample grid.

The motor scene reproduces `DCMotor`: a blue rotor, its shaft marker, and
signed angular-velocity and current bars. Its source angles remain radians;
the label displays degrees. Matplotlib can scale bars using a completed
trajectory. When signal limits are provided, the live viewer uses them as its instrument
scale. Unspecified motor endpoints use the maximum magnitude received since
the observed run began, with a minimum magnitude of `0.001`.

Neutralization reproduces `pHNeutralization`: liquid height and pH-dependent
colour, inlet/outlet arrows, a schematic mixer, and pH/height gauges. The
default height display scale is 25 cm and the pH gauge defaults to `[0, 14]`.
Declared signal limits override the corresponding instrument endpoints.
Clipping changes only the drawing, not the model state. Mixer rotation is schematic and stops advancing when simulated
time stops. Feed labels use the applied input, including while that input is
held between commands.

The neutralization state is `[Wa4, Wb4, h, pH]`. `Wa4` and `Wb4` use mol/L,
`h` uses cm, and pH is dimensionless. The configured DAE stepper preserves
indices `[0, 1, 2]` during consistency correction and adjusts the algebraic
pH. The initial value `7.0` is a guess; the initialized value is approximately
`7.001881`. Initial derivative guesses are zero; consistent derivatives are
computed by the stepper.

This configuration uses the reference experiment's cm³/s flow convention
with area in cm² and height in cm: acid feed `q1=16.6`, buffer feed `d=0.55`,
and controlled alkaline feed `u=[15.6]`. The numerical model's flow docstrings
say L/s, whereas its reference experiment and Matplotlib viewer use raw
cm³/s values. No conversion is performed by this package. Keep the chosen
flow and geometry units consistent when overriding these parameters.

## Instrument scales from signal descriptions

The browser reads `/api/signals` and uses `lower` and `upper` as the scale
endpoints of the corresponding instrument. It follows state-vector order,
so custom signal names work without changing the viewer. The motor uses the
second and third state descriptors for angular velocity and current;
neutralization uses the third and fourth for height and pH.

For example, a custom motor configuration can describe its current signal as:

```toml
[[state_signals]]
name = "current"
unit = "A"
dtype = "float64"
direction = "output"
lower = -2.0
upper = 2.0
```

This table belongs in the third position of the complete `state_signals`
list. The current indicator then keeps the fixed range `[-2, 2]` A even when
the model current exceeds the range. The viewer labels show the model value
from `/api/state`. A controller reading `/api/measurements` sees the value
clipped to the instrument limits. The instrument shows its endpoints, and
expanded **Simulation data** lists the active instrument ranges.

A single declared endpoint is retained, and the missing endpoint comes from
the scene's existing fallback. If that would produce a reversed or zero-width
interval, only the missing endpoint is extended. Two equal declared limits
represent a constant range: its exact value is drawn at the midpoint, avoiding
a division by zero.

Motor bars support asymmetric, positive-only and negative-only intervals;
zero is the reference when it lies inside the interval. Neutralization's
height gauge uses both endpoints. The liquid fill still represents physical
height above zero, using the inferred or declared positive upper endpoint
as the vessel display scale; it does not subtract a sensor's lower limit.
The pH water-colour mapping remains anchored to the chemical `[0, 14]` scale.

Input limits continue to validate commands in the runtime. They affect visual
scaling only when an instrument actually represents that input. Output limits
provide display metadata and do not constrain the numerical model or stop an
experiment. The pendulum and motor shaft retain their geometric angle display.

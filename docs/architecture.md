# Architecture and interactions

The functional core consists of a numerical model, a `Simulation` and a
`Stepper`. We will first follow their execution, then add observation and
configuration around the same objects. The [API reference](api.md) contains
the class, sequence and configuration diagrams alongside the public methods.

## Model, simulation and stepper

The [functional core diagram](api.md#functional-core) separates these roles:

| Component | Responsibility |
| --- | --- |
| Numerical callable | Evaluate derivatives or DAE residuals from state, time, parameters and inputs |
| `Simulation` | Own the worker, pace time, retain inputs, publish snapshots and record results |
| `Stepper` protocol | Define initialization, interval integration and access to committed numerical state |
| `ODEStepper` / `DAEStepper` | Implement that protocol using SciPy or solve-dae |
| `SignalDescriptor` | Describe ordered state/input names, units and optional bounds |

`Simulation(model, ...)` binds physical parameters and maps the input vector
to model keyword arguments. It initializes its exclusively owned stepper
once. Without an explicit stepper, it creates a fresh RK45 ODE stepper.
DAEs require an explicit `DAEStepper` with an initial derivative guess.

The model remains an ordinary numerical callable. It can come from
`benchmark-systems` or user code; introducing another model does not require
a new simulation subclass. Steppers integrate intervals without wall-clock
waiting or communication I/O. `Simulation` owns that execution schedule.

## From command receipt to physical state

The [runtime sequence](api.md#runtime-sequence) shows why receipt and
application are separate:

1. A controller submits a complete input vector. The simulation validates
   it, checks controller ownership and returns an acceptance sequence.
2. The worker freezes the newest pending command, or retains the previous
   input if there is none, at the beginning of an interval.
3. After waiting for its wall-clock deadline, it calls `stepper.step(dt, u)`.
   The solver may evaluate the bound model several times within that interval.
4. On success the worker publishes the completed state and applied command
   sequence together. Readers obtain a detached snapshot of that state.

A command received after an interval's input has been frozen affects a later
interval. Several pending writes may coalesce into the latest command. The
last applied input is retained indefinitely. A failed solver interval does
not publish a partially integrated state or mark its command as applied.

`start()`, `wait()` and `stop()` control the worker lifecycle. They belong to
`Simulation`, while the narrower observation/control protocol below leaves
lifecycle ownership with the application.

## Controllers and adapters

`SimulationPort` exposes `snapshot()`, `read_measurements()`,
`describe_signals()` and `submit_input()`. `Simulation` satisfies this
structural Python protocol without inheriting from it. An adapter therefore
depends on these methods rather than the simulation's private implementation.

`InMemoryAdapter` binds a port to a controller identity. Its read methods
delegate to that port; `write_inputs()` supplies the identity when calling
`submit_input()`. `write_signals()` first orders a complete named input
mapping using the signal descriptions. Only the selected controller may
write; other clients may still observe. Direct Python code can use
`Simulation.set_input()` as a convenience for its selected controller.

The HTTP server uses an `InMemoryAdapter` with identity `http` and runs
network I/O outside the physics worker. The browser reads snapshots through
HTTP polling. It neither owns the clock nor advances the model. The runtime
imports neither communications nor viewer resources.

## Optional configuration and terminal execution

The [extended architecture diagram](api.md#optional-configuration-and-access) adds
the catalogue and CLI around the same core. A `SimulationConfiguration`
contains model selection, physical parameters, initial vectors, signal
descriptions and integrator settings. Its `create()` method constructs a
fresh `Simulation` with optional overrides. Once construction is complete,
the catalogue has no role in the worker's integration or control loop.

The Typer CLI requires an explicit catalogue entry, custom TOML file or
public model name. It loads configuration data or constructs it in memory
from CLI arguments, then manages the simulation and HTTP server lifecycle.
Inspection options show model documentation and settings without starting a
worker. Constructing a simulation never writes a configuration file. A direct
Python application supplies the same constructor data itself. Both paths
produce the same runtime objects and use the same integration semantics.

See [Configuration catalogue and CLI](catalogue.md) for these construction
shortcuts after working through the direct Python examples.

The CLI separates option routing, configuration parsing, execution lifecycle
and terminal presentation into modules within `bs_simulators.cli`. Rich
tables and panels present observations without introducing communication or
formatting dependencies into the simulation worker.

Packaged browser scenes share one polling client and stylesheet. A registry
maps explicit scene identifiers to HTML resources; the HTTP transport serves
only registered scripts and styles. Numerical models remain in the Python
worker, while the browser renders committed state without integrating it.

# API reference

The reference follows the execution core first, then its observation/control
interfaces, and finally optional construction helpers. The
[pendulum example](pendulum.md) combines these objects in a runnable workflow.

(functional-core)=
## Functional core

```{figure} _static/architecture/functional-core.svg
:alt: Simulation owns a Stepper, evaluates a numerical model and satisfies SimulationPort. InMemoryAdapter uses that port. ODE and DAE steppers satisfy the Stepper protocol.
:width: 100%

Public classes and structural protocols. Dashed arrows mean “satisfies the
protocol”; no inheritance from a protocol is required.
```

`Simulation` binds the numerical model to its parameters and input mapping.
It owns one stepper and supplies that stepper with the bound model during
initialization. The worker later calls `step(dt, u)` for each fixed interval.
The solver evaluates the bound model as needed within that interval.

`SignalDescriptor` describes the ordered vector components.
`SimulationPort` exposes observation and input submission to adapters; worker
lifecycle methods remain on `Simulation`. The diagram shows that public
boundary without exposing internal locks or implementation helpers.

### Simulation

`start()` returns the same simulation instance (`Self`), so
`simulation = configuration.create().start()` is valid. It starts a worker
and returns while the run proceeds. `wait()` returns a `bool` indicating
whether execution has stopped; inspect `snapshot()["status"]` to distinguish
completion from failure. `wait()` also waits for the recording writer using
the same timeout budget. `stop()` and `configure_controller()` return `None`.
The `parameters` property returns a detached `dict[str, Any]`, and
`active_controller` returns a `str`.

`start_recording(path)` starts or changes a capture on a running simulation.
`stop_recording()` closes that capture while the plant keeps running. Both
return `None`. They wait for confirmation outside the running plant's locks.
Background file errors are raised by `stop_recording()` or by `wait()`/`stop()`
at finalization while physics retains its own execution status. Each file starts at `t = 0`; see
{ref}`capture-time-fields` for the recorded time fields and the
[recording guide](recording.md) for examples.

```{eval-rst}
.. autoclass:: bs_simulators.runtime.core.Simulation
   :members:
   :special-members: __init__
```

(snapshot-return-fields)=
### Snapshot return fields

`Simulation.snapshot()` returns a detached `dict[str, Any]`. Arrays are
represented as Python lists, so the result can also be serialized as JSON.
Reading or editing the returned data does not advance or modify the model.
The state and applied input describe the latest committed integration result.
Receipt and pending-command metadata are refreshed at read time and can
describe commands awaiting application.

| Field | Python type | Meaning |
| --- | --- | --- |
| `t` | `float` | Absolute simulated time in seconds, starting at `t0`. |
| `x` | `list[float]` | Model state in configured order, before sensor limits. |
| `u` | `list[float]` | Applied input in configured order, after actuator saturation. |
| `input_saturated` | `list[bool]` | One flag per applied input, true when clipping changed its requested value. |
| `xdot` | `list[float]` | DAE derivatives in state order. Present only when the stepper supplies derivatives. |
| `seq` | `int` | Number of committed integration intervals, initially zero. |
| `input_seq` | `int` | Sequence of the applied command, or zero for the initial input. |
| `pending_input_seq` | `int` or `None` | Latest queued or in-flight command sequence, or `None` when none exists. |
| `applied_at_sim_time` | `float` | Simulated boundary time at which the held input was applied. Initially `t0`. |
| `wall_elapsed` | `float` | Wall-clock seconds elapsed at the committed snapshot, zero before start. |
| `lag` | `float` | Nonnegative delay in wall-clock seconds relative to the target cadence. |
| `status` | `str` | Execution status, described below. |
| `parameters` | `dict[str, Any]` | Detached physical model parameters. |
| `model` | `str` | Model identifier used by the simulation. |
| `viewer` | `str` or `None` | Selected viewer identifier, if configured. |
| `active_controller` | `str` | Controller currently authorized to submit inputs. |
| `controller_id` | `str` or `None` | Controller of the applied command, or `None` before a command is applied. |
| `received_input_seq` | `int` | Latest accepted command sequence, or zero before any receipt. |
| `received_at_monotonic` | `float` or `None` | Local monotonic receipt time of the latest accepted command. |
| `source_timestamp` | `str` or `None` | Optional UTC timestamp supplied with the applied command. |
| `received_source_timestamp` | `str` or `None` | Optional UTC timestamp supplied with the latest accepted command. |
| `applied_received_at_monotonic` | `float` or `None` | Local monotonic receipt time of the applied command. |
| `error` | `str` | Failure description, present only when an error has been recorded. |

`status` is `"ready"` before start, `"running"` during execution,
`"finished"` after the requested duration, `"stopped"` after an explicit stop,
or `"error"` after a worker failure. A stopped or completed instance cannot
be restarted.

Monotonic timestamps are local clock readings in seconds. Their differences
measure receipt intervals; their absolute values are unrelated to simulated
time or UTC. Source timestamps are ISO 8601 UTC strings supplied by the
controller and remain optional diagnostic metadata.

`read_measurements()` returns a detached `dict[str, float | str]` keyed by
signal name. State readings are clipped at sensor bounds, input readings
contain applied actuator values, `time` is simulated time in seconds, and
`status` is the execution status. The state names and order come from
`describe_signals()`. See {ref}`snapshot-and-measurements` for an example of
the difference between model state and sensor readings.

(input-acknowledgement-fields)=
### Input acknowledgement fields

`set_input()` and `submit_input()` return `dict[str, Any]` with the following
fields. `InMemoryAdapter.write_inputs()` and `write_signals()` forward the
same acknowledgement when connected to a `Simulation`.

| Field | Python type | Meaning |
| --- | --- | --- |
| `input_seq` | `int` | Sequence allocated to this accepted command. |
| `pending_input_seq` | `int` | The same newly accepted sequence at receipt time. |
| `accepted_input_seq` | `int` | Explicit name for the accepted sequence, equal to `input_seq`. |
| `applied_input_seq` | `int` | Sequence already applied when the acknowledgement was created. |
| `controller_id` | `str` | Identity of the submitting controller. |
| `received_at_monotonic` | `float` | Local monotonic receipt time in seconds. |

The acknowledgement confirms receipt. A later snapshot identifies the
command actually applied through its `input_seq`. A newer command can replace
an earlier pending command before application.

### Numerical steppers

An omitted stepper creates a fresh `ODEStepper(method="RK45")`. Pass an
explicit stepper to select integration settings or use a DAE residual model.
Each successful initialization belongs to a single simulation run.

```{eval-rst}
.. autoclass:: bs_simulators.runtime.steppers.ODEStepper
   :members: initialize, step, t, x, xdot
   :special-members: __init__

.. autoclass:: bs_simulators.runtime.steppers.DAEStepper
   :members: initialize, step, t, x, xdot
   :special-members: __init__

.. autoclass:: bs_simulators.runtime.protocols.Stepper
   :members:
```

### Signal descriptions

```{eval-rst}
.. autoclass:: bs_simulators.runtime.signals.SignalDescriptor
   :members: as_dict
```

(runtime-sequence)=
## Runtime sequence

```{figure} _static/architecture/runtime-sequence.svg
:alt: A controller submits an input through an adapter, receives acceptance, and the simulation worker later freezes the input, waits, integrates and publishes a committed state.
:width: 100%

Command receipt, interval integration and observation. The worker lane is
the simulation's thread, rather than another public class.
```

`write_inputs()` delegates to `submit_input()` with the adapter's controller
identity. Its acknowledgement reports acceptance. A command received after
the worker has frozen an interval's input waits for a later interval.
The worker retains the last input when there is no new command.

The stepper returns after a successful interval. The worker then publishes
the state and applied `input_seq` together. The model lane represents the
callable bound by `Simulation`, which can be evaluated several times per
solver interval.

`InMemoryAdapter.read_snapshot()` delegates to `Simulation.snapshot()` and
exposes the model state before sensor limits. `InMemoryAdapter.read_measurements()`
delegates to `Simulation.read_measurements()` and gives the controller named
readings clipped at configured sensor limits. Both reads leave the clock
and model state untouched. See
{ref}`simulation snapshots and sensor measurements <snapshot-and-measurements>`.

### Observation and control protocol

```{eval-rst}
.. autoclass:: bs_simulators.runtime.protocols.SimulationPort
   :members:
```

### Adapters and HTTP

`InMemoryAdapter` accepts any object satisfying `SimulationPort` and fixes the
controller identity used for writes. The HTTP server uses this adapter with
identity `http`. Network handling and browser polling run outside the
physics worker. See [HTTP observation and control](http.md) for startup and
shutdown examples.

```{eval-rst}
.. autoclass:: bs_simulators.comms.adapters.InMemoryAdapter
   :members:
   :special-members: __init__

.. autofunction:: bs_simulators.comms.http.create_server
```

(configuration-and-access)=
## Optional configuration and access

```{figure} _static/architecture/configuration-access.svg
:alt: Direct Python arguments or a loaded SimulationConfiguration construct the same Simulation. The optional CLI loads configurations and manages lifecycle; HTTP clients reach the core through InMemoryAdapter and SimulationPort.
:width: 100%

Construction helpers and communication components surround the same core.
```

Direct Python code constructs `Simulation` from a callable and initial data.
Alternatively, a loader produces a `SimulationConfiguration` from bundled or
custom TOML. `create()` resolves the model, applies overrides and returns a
fresh `Simulation` with its own stepper. The configuration does not take part
in subsequent integration or command handling.

The CLI requires an explicit catalogue entry, custom TOML file or model
name. It combines construction data, overrides and lifecycle management.
`create()` does not save a configuration or change its TOML source. Browser clients
observe through HTTP; an HTTP controller can submit inputs when selected.

### Configuration catalogue

```{eval-rst}
.. autoclass:: bs_simulators.catalogue.SimulationConfiguration
   :members: create

.. autofunction:: bs_simulators.catalogue.list_configurations
.. autofunction:: bs_simulators.catalogue.load_configuration
.. autofunction:: bs_simulators.catalogue.load_configuration_file
```

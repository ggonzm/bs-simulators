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

```{eval-rst}
.. autoclass:: bs_simulators.runtime.core.Simulation
   :members:
   :special-members: __init__
```

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

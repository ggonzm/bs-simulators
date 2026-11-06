# bs-simulators

**Live experiments for benchmark-systems models.**

`bs-simulators` runs numerical models in an independent simulation thread.
A controller reads measurements and submits complete input vectors while the
worker integrates the model. The last input remains active until another
valid command arrives. Completed trajectories can be recorded and replayed.

Start with the [simple pendulum example](pendulum.md) to construct a
`Simulation`, inspect its signals and run a control loop. Then explore
[simulation and steppers](simulation.md) and the
[component interactions](architecture.md). Numerical models come from
[benchmark-systems](https://benchmark-systems.readthedocs.io/en/latest/)
or a compatible Python callable.

[HTTP observation](http.md) adds a browser view or an external controller to
the same simulation. The optional [configuration catalogue](catalogue.md)
and command-line application provide shortcuts for constructing and running
experiments once their underlying components are familiar.

[Common pitfalls](common-pitfalls.md) covers timing, measurement limits and
the distinction between accepted and applied commands.

The runtime supports ODEs and DAE consistency of differentiation index at
most one. Packaged browser scenes depict the pendulum, DC motor and pH neutralization
reactor. Other models can run through Python and HTTP without a dedicated scene.

```{toctree}
:maxdepth: 2
:caption: User guide
:hidden:

installation
pendulum
simulation
architecture
http
recording
catalogue
common-pitfalls
```

```{toctree}
:maxdepth: 2
:caption: Reference
:hidden:

api
development
```

# bs-simulators

`bs-simulators` runs [benchmark-systems](https://benchmark-systems.readthedocs.io/en/latest/)
models as virtual plants for live control experiments. A controller interacts
with the simulation as it would with a physical plant: it reads sensor
measurements and sends actuator commands while the plant evolves independently.

## Installation

Install from PyPI with Python 3.13 or later:

```shell
python -m pip install bs-simulators
```

See the [documentation on Read the Docs](https://bs-simulators.readthedocs.io/en/latest/)
for examples and the API reference.

## Run a virtual pendulum

After installation, launch the bundled pendulum and open
<http://127.0.0.1:8765/> to watch it evolve.

```shell
bs-simulators --catalogue pendulum
```

The catalogue also includes a DC motor and a pH neutralization process.
Use `bs-simulators --list-catalogue` to see the available configurations.

## Run a simulation from Python

```python
from benchmark_systems.models import pendulum
from bs_simulators import Simulation

simulation = Simulation(
    pendulum, x0=[0.6, 0.0], u0=[0.0],
    parameters={"m": 1.0, "L": 1.0},
)
simulation.start(duration=1.0)
simulation.wait()
```

The [pendulum control example](https://bs-simulators.readthedocs.io/en/latest/pendulum.html)
shows how to read measurements and send commands in a control loop. A controller
in a separate process can use the [HTTP interface](https://bs-simulators.readthedocs.io/en/latest/http.html).

[MIT License](LICENSE)

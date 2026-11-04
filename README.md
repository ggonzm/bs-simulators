# bs-simulators

Simulación en vivo, comunicaciones y visualización para los modelos de
[`benchmark-systems`](https://github.com/ggonzm/benchmark-systems).

Este paquete complementario mantiene separada la ejecución en vivo de la
librería original. Los modelos numéricos, simbólicos y las animaciones offline
siguen perteneciendo a `benchmark-systems`.

Estado: prototipo de péndulo ODE, versión `0.1.0`, Python 3.13 o posterior.
HTTP y Canvas están implementados; OPC UA y Modbus son extensiones futuras.

## Desarrollo local

Colocar ambos repositorios como carpetas hermanas:

```text
GitLab Projects/
├── benchmark-systems/
└── bs-simulators/
```

Desde `bs-simulators`, preparar su propio entorno con PDM:

```powershell
# Primera preparación, o después de modificar dependencias:
pdm lock -G dev -G replay
pdm sync -G dev
pdm run pytest
```

La dependencia publicada se declara como `benchmark-systems>=1.0.7,<2`.
Como el núcleo todavía no está disponible en PyPI, la resolución de desarrollo
de PDM utiliza su commit `0334ffd8179fa5355236d0aeb00aef42b3280517` (versión
1.0.7) en GitHub. Esa configuración no introduce rutas locales ni una URL Git
en los requisitos públicos del wheel. El lockfile fija el commit y las demás
dependencias. Cuando el núcleo se publique en un índice apropiado, se podrá
retirar este override y regenerar el lockfile.

El simulador utiliza esa versión instalada, no una copia editable del checkout
hermano. Los cambios locales del núcleo no se incorporan automáticamente.
Todavía no se presupone que `bs-simulators` esté publicado en un índice.

## Probar el péndulo

```powershell
# Terminal 1
pdm run python -m bs_simulators --port 8765 --dt 0.01

# Terminal 2
pdm run python examples/pendulum_sil_controller.py
```

Abrir <http://127.0.0.1:8765>. También se puede arrancar con el comando instalado
`pdm run bs-simulators --port 8765`. El motor conserva el último par válido si
el controlador deja de enviar órdenes. El visor puede cerrarse sin detenerlo.

Para usar señales por nombre sin escrituras HTTP:

```powershell
pdm run python examples/pendulum_signal_adapter.py --port 8766
```

Ese ejemplo selecciona el controlador `plc-demo`, pero no conecta un PLC físico.

## Core notebook example

Open [`examples/pendulum_core.ipynb`](examples/pendulum_core.ipynb) with the
project's Python environment. It starts the pendulum thread, runs a minimal
PD scan loop, and exposes signal descriptions, measurements, and snapshots
for interactive inspection.

## Registrar y reproducir

```powershell
pdm run python -m bs_simulators --duration 15 --record pendulum-run.jsonl

# Dependencias opcionales para exportar un GIF con Matplotlib:
pdm sync -G dev -G replay
pdm run python examples/pendulum_sil_replay.py pendulum-run.jsonl --output pendulum.gif
```

Se conservan dos flujos: simulación offline con los modelos y animaciones
originales, y simulación en vivo con este paquete. La integración por intervalos
es interna y no se presenta como un tercer modo público.

## Organización

```text
src/bs_simulators/
├── runtime/       motor, señales y protocolos de Python
├── comms/         adaptador en memoria y HTTP
└── viewers/       recursos del visor del péndulo
```

`SimulationPort` es un `typing.Protocol`. Los adaptadores leen medidas y
entregan entradas completas sin depender de un modelo concreto. El motor
selecciona explícitamente el controlador activo y distingue recepción y
aplicación, fecha UTC del origen e instante local monotónico de recepción.
Consultar la [guía del péndulo](docs/pendulum.md) y la
[arquitectura y extracción](docs/migration.md).

## Verificación y distribución

```powershell
pdm run pytest
pdm build
```

En este checkout de Windows/OneDrive, si la limpieza de `.pdm-build` encuentra
un directorio de solo lectura, puede usarse la opción oficial del backend
`pdm build -C no-clean-build=true`, sin eliminar ni editar los generados a mano.

El HTML del visor forma parte de las distribuciones y se carga con
`importlib.resources`; no necesita el checkout para funcionar. El ejemplo de
replay usa el módulo `benchmark_systems.animations` del paquete original.

Las nuevas propuestas se desarrollan en `develop`. La fusión con `main` y el
push se realizan cuando el usuario lo indique; antes se incrementa el tercer
número de versión en `pyproject.toml` y `src/bs_simulators/__init__.py`.

El servicio HTTP es local. La ejecución ofrece tiempo real blando y no
garantiza plazos estrictos. El prototipo no incluye DAE en vivo, comunicación
industrial real, pause/reset ni escenas 3D.

Licencia MIT. Se conserva además `LICENSE.benchmark-systems` con los avisos de
la librería de origen.

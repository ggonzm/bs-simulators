# Péndulo: simulación en vivo y reproducción de experimentos

La librería ofrece dos flujos de trabajo:

- **Offline:** integrar una trayectoria completa con las herramientas actuales
  y reproducirla con `animations.Pendulum` y `AutoAnimation`.
- **En vivo:** ejecutar el motor continuamente, recibir entradas de un
  controlador externo y observar los estados a medida que se calculan.

No se añade un tercer modo público de avance manual. La integración por
intervalos es un detalle interno necesario para aplicar entradas y verificar el
motor. Las animaciones existentes, los modelos y los notebooks conservan su API.

## Ejecutar el péndulo en vivo

Con Python 3.13 y las dependencias del proyecto, desde su carpeta raíz:

```powershell
# Terminal 1: simulador y visor local. Ctrl+C para terminar.
pdm run python -m bs_simulators --port 8765 --dt 0.01

# Terminal 2: controlador externo de ejemplo.
pdm run python examples/pendulum_sil_controller.py
```

Abrir <http://127.0.0.1:8765>. Si PDM no está disponible en el PATH pero ya existe
el entorno instalado, sustituir `pdm run python` por `.venv/Scripts/python.exe`.

El visor tiene fondo blanco, ejes en metros, varilla y masa con los colores y
proporciones de `animations.Pendulum`. Muestra el tiempo y un estado discreto.
Los datos técnicos están ocultos en «Datos de la simulación». La imagen se
dibuja con Canvas; no se envían imágenes Matplotlib en cada actualización.

El controlador manda 0, 2 y -1 N·m con dos segundos simulados entre órdenes,
espera a observarlas aplicadas y termina. El motor continúa con -1 N·m. Antes
de recibir entradas conserva `u0` (cero por defecto). Cerrar el visor no detiene
la integración.

## Registrar y reproducir una trayectoria completa

```powershell
pdm run python -m bs_simulators --duration 15 --record pendulum-run.jsonl

# Tras terminar, reproducir con los animadores Matplotlib existentes.
pdm run python examples/pendulum_sil_replay.py pendulum-run.jsonl --output pendulum-run.gif
```

El JSONL guarda el estado inicial, cada intervalo integrado y un estado
terminal. Este último puede repetir tiempo/secuencia y el ejemplo de replay lo
filtra. Una ruta ya existente no se sobrescribe. Al alcanzar la duración, el
proceso termina y cierra también el servicio HTTP. El visor conserva la última
imagen e indica que no hay conexión.

## Protocolos de Python y adaptadores

`SimulationPort`, definido mediante `typing.Protocol`, describe la interfaz que
necesita un adaptador: describir señales, leer medidas e instantáneas y entregar
entradas. Es una interfaz estructural: no obliga a heredar de una clase base.

`InMemoryAdapter` permite usar esa interfaz directamente desde Python, sin
peticiones HTTP. El servidor HTTP utiliza el mismo adaptador. Un futuro puente
OPC UA o Modbus podrá convertir sus mensajes a estas operaciones y publicar
las medidas en sentido inverso.

```python
from bs_simulators import InMemoryAdapter, PendulumSimulation

simulation = PendulumSimulation(active_controller="plc", theta0=0.6)
bridge = InMemoryAdapter(simulation, controller_id="plc")
bridge.write_signals({"torque": 2.0})
simulation.start(duration=1.0)
try:
    signals = bridge.describe_signals()
    measures = bridge.read_measurements()
finally:
    simulation.stop()
```

Este fragmento ilustra el acceso por interfaz, no una conexión industrial real.
Para un ejemplo que deja evolucionar el péndulo y registra medidas:

```powershell
pdm run python examples/pendulum_signal_adapter.py
```

Añadir `--port 8766` permite observar ese ejemplo en el navegador. El motor
selecciona `plc-demo` como controlador; las escrituras HTTP no pueden sustituirla.
No se está comunicando con un PLC real: el ejemplo prepara el punto donde se
conectaría su puente OPC UA o Modbus.

El controlador activo se selecciona al crear el motor, o antes de arrancarlo con
`configure_controller`. Un controlador distinto no puede enviar órdenes ni tomar
el control automáticamente. El servicio HTTP se identifica como `http` y no
permite elegir otra identidad en el cuerpo de una petición. Con
`active_controller="plc"`, el navegador puede observar y las escrituras HTTP se
rechazan. Esta selección es arbitraje lógico local, no autenticación de equipos.

Los descriptores incluyen nombres, dirección, unidad, tipo y límites opcionales.
`write_signals` exige el conjunto completo de entradas; actualmente es
`{"torque": valor}`. En un sistema de varios actuadores el puente deberá preparar
un vector completo antes de entregarlo, evitando actualizaciones parciales.
Los registros Modbus y los nodos OPC UA se definirán en sus futuros adaptadores.

## Recepción y aplicación

Una entrada se valida y recibe una secuencia antes de aplicarse. El motor toma
la última entrada válida al comienzo de un intervalo y la congela durante ese
intervalo. Si llegan varias antes de esa frontera, se conserva la más reciente;
no se trata de una cola de órdenes programadas.

- `source_timestamp`, si se proporciona, representa una fecha UTC del origen.
  Se conserva como metadato; no altera el orden local ni programa una entrada
  para el pasado o el futuro.
- El tiempo de recepción utiliza un reloj monotónico local independiente de
  esa fecha. Enviar de nuevo el mismo par cuenta como una recepción válida.
La última entrada aplicada se mantiene indefinidamente hasta recibir otra
entrada válida, sin umbrales de antigüedad ni cambios automáticos de valor.

Los adaptadores no deben ejecutar lectura de red o esperar al PLC dentro del
integrador: reciben mensajes aparte y entregan entradas completas al motor.

## Interfaz HTTP del prototipo

- `GET /api/signals`: descriptores de las señales.
- `GET /api/state`: instantánea con `t`, `x=[theta, omega]`, `u=[torque]`,
  secuencias de recepción/aplicación, controlador activo, tiempos y estado.
- `POST /api/input`: cuerpo `{"u": [2.0]}`; admite una fecha UTC de origen
  opcional. La respuesta confirma recepción. `input_seq` en una instantánea
  identifica la entrada aplicada y `applied_at_sim_time` su frontera temporal.

El ángulo cero es la vertical inferior; theta se expresa en radianes, omega en
rad/s, tiempo en segundos y par en N·m. El navegador consulta el estado cada
50 ms aproximadamente; esa frecuencia es independiente de `dt`.

## Alcance

El motor intenta avanzar un segundo simulado por segundo real con reloj
monotónico. SciPy usa pasos internos adaptativos dentro de cada intervalo
`dt`. No se omiten intervalos si hay retraso, que queda reflejado en `lag`.
Es ejecución en tiempo real blando: no garantiza plazos estrictos.

Esta ampliación prepara protocolos y adaptadores, pero no implementa clientes
ni servidores OPC UA/Modbus ni acredita ensayos con PLC físico. Tampoco añade
DAE en vivo, integradores CasADi persistentes, pause/reset o escenas 3D.
Se mantienen HTTP de la biblioteca estándar y Canvas, sin dependencias nuevas.
FastAPI, WebSocket y SSE pueden evaluarse por separado cuando aporten una
mejora necesaria. El servicio actual solo escucha en loopback y está destinado
a experimentos locales.

El registro JSONL actual escribe en disco desde el worker. Un disco lento puede
añadir retraso; para cadencias más exigentes habría que medirlo y evaluar un
escritor independiente con memoria acotada. Los adaptadores de comunicaciones
no realizan operaciones de red dentro del worker físico.

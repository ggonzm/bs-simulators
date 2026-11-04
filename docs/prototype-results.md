# Resultado del prototipo del péndulo y su ampliación

> Histórico anterior a la extracción. Las rutas y cifras de este documento
> describen el prototipo cuando estaba en `benchmark-systems`. La distribución
> actual es `bs-simulators` 0.1.0; ver [la migración](migration.md) y el informe
> `MIGRATION_VALIDATION.md` de la raíz para el resultado actual.

Rama `develop`, versión `1.0.7`. Cambios sin commit, push ni merge.

## Flujos de trabajo

Se conserva la simulación offline de trayectorias completas y su reproducción
con Matplotlib. Se añade la simulación en vivo con entradas externas. No se
presenta un modo adicional de avance manual: `_ODEStepper` es un detalle interno,
fuera de las exportaciones públicas. Sigue siendo necesario para integrar por
intervalos, aplicar entradas completas y verificar el motor.

## Cambios realizados

| Archivo | Finalidad |
| --- | --- |
| `simulation/core.py` | Motor del péndulo en vivo, ZOH, reloj monotónico, registro y selección explícita del controlador. Conserva recepción y aplicación por separado. |
| `simulation/protocols.py` | `SimulationPort`, definido mediante `typing.Protocol`, para describir señales, leer medidas/instantáneas y entregar entradas. |
| `simulation/signals.py` | Descriptores de nombre, unidad, tipo, dirección y límites opcionales; validación de controlador y fecha UTC de origen. |
| `simulation/adapters.py` | `InMemoryAdapter`: acceso por señales nombradas o vector completo sin HTTP; delega las medidas al motor. |
| `simulation/server.py` | HTTP local usa ese mismo adaptador. Añade `/api/signals`, conserva `/api/state` y `/api/input`; un controlador HTTP no puede suplantar otro. |
| `simulation/__main__.py` | CLI del motor con parámetros, registro, duración, controlador activo. |
| `simulation/__init__.py` | Exporta `PendulumSimulation`, `SimulationPort`, `InMemoryAdapter` y `SignalDescriptor`. |
| `simulation/viewer.html` | Fondo blanco, ejes, varilla gris y masa azul con proporciones del animador Matplotlib. Tiempo visible y datos técnicos plegados. Se elimina el dashboard y el historial del visor. |
| `examples/pendulum_sil_controller.py` | Controlador HTTP en otro proceso que termina tras aplicar tres órdenes. |
| `examples/pendulum_signal_adapter.py` | Puente de ejemplo que escribe `torque` y lee medidas sin HTTP; puede añadir visor de solo lectura. No conecta un PLC físico. |
| `examples/pendulum_sil_replay.py` | Reproducción de registros completos con `Pendulum` y `AutoAnimation` existentes; exportación GIF. |
| `examples/pendulum_sil.md` | Guía actualizada, protocolos de Python, metadatos y alcance pendiente. |
| `tests/test_simulation.py` y `tests/test_simulation_adapters.py` | Evidencia física, pruebas de procesos y de interfaces estructurales independientes del péndulo. |
| `README.md` | Enlace al nuevo ejemplo, preservando la guía de animaciones offline. |

Las rutas `simulation/` de esta tabla corresponden a
`src/bs_simulators/`. Los cambios de `AGENTS.md` sobre ramas y
versiones proceden de las peticiones previas. `.vscode/` ya estaba sin seguimiento.
No se cambian modelos, animadores actuales, notebooks, dependencias, lockfile
ni versión.

## Evidencia automática

Suite completa del tester: **81 pruebas superadas en 22,32 s** — 40 anteriores
y 41 añadidas por el prototipo y su ampliación. Se utilizó
`.venv/Scripts/python.exe -m pytest -q` porque PDM no está disponible en el PATH.

- Soluciones analíticas comprueban tiempo absoluto y retención por tramos.
- La integración incremental y el registro coinciden con una solución offline
  de alta precisión dentro de las tolerancias declaradas en las pruebas.
- Proceso controlador separado, continuidad sin visor/controlador, lector HTTP
  lento, entradas inválidas y registro terminal permanecen cubiertos.
- Un reloj controlado acredita que una orden recibida durante un intervalo se
  aplica al siguiente y nunca modifica retrospectivamente el anterior.
- `SimulationPort` se prueba con motores independientes sin heredar clases de
  la librería. HTTP y el adaptador usan realmente esa interfaz.
- Un motor ficticio de tanque devuelve `level/outflow` sin estados `x/u` del
  péndulo: el adaptador obtiene sus medidas por delegación.
- Un caso multientrada verifica que el mapa de señales se convierte en un
  vector completo y ordenado antes de una sola entrega.
- Otros controladores no toman el control, aunque el controlador seleccionado tenga
  entradas antiguas. La selección previa al arranque es explícita.
- Valores y fechas inválidos no cambian el par, las secuencias ni
  los instantes de recepción, medidos con un reloj monotónico local.
- Repetir el mismo par renueva la recepción. Los metadatos recibidos y aplicados
  se conservan por separado, incluida la fecha UTC del origen.
- El ejemplo funciona en memoria sin pasar por HTTP.

## Experimentos y comprobación visual

El primer prototipo completó 1.000 pasos de 0,01 s, durante 10 s simulados. La
última orden fue -1 N·m y se mantuvo durante los 5,58 s restantes. En esa
ejecución el retraso mediano fue 8,15 ms, el percentil 95 fue 29,82 ms y el máximo
151,21 ms. Son datos de una ejecución, no un benchmark general ni garantía
temporal.

El nuevo ejemplo de señales completó **600 pasos y 6 s**, con controlador
`plc-demo`. Aplicó 0, 2 y -1 N·m. Terminó con -1 N·m y `status=finished`.
El último par se conserva hasta recibir otra entrada válida.

El visor simplificado se ha abierto en un navegador real y se ha comprobado su
escena, tiempo y panel plegado. Reproduce la geometría y el estilo del péndulo
Matplotlib mediante Canvas; no es un backend Matplotlib. El registro completo
sigue pudiendo visualizarse con el módulo de animaciones existente.

## Problemas encontrados y resueltos

1. La primera implementación recogía entradas al final de la espera de un
   intervalo. Se corrigió la frontera y una prueba determinista cubre el caso.
2. El JSONL no identificaba la finalización. Se añadió una instantánea terminal
   sin paso físico extra; el replay filtra el tiempo repetido.
3. Recepción y aplicación podían confundirse. Ahora tienen secuencias y datos
   temporales separados; la confirmación HTTP indica recepción.
4. El primer adaptador interpretaba índices del péndulo. Se trasladó ese mapa
   al motor y se verificó con el caso independiente del tanque.
5. La presentación anterior resultaba recargada y los modos eran confusos.
   Se eliminó el dashboard y la exposición pública del avance manual.

## Alcance pendiente

No se han implementado OPC UA ni Modbus, ni probado un PLC físico. Los nombres
de controlador son arbitraje lógico, no autenticación. El prototipo no usa la fecha de origen para programar la aplicación de entradas.

Se retiene el último valor válido indefinidamente hasta recibir otra entrada válida. No se
añaden DAE en vivo, integradores CasADi persistentes, pause/reset ni escenas 3D.
FastAPI, WebSocket y SSE siguen siendo opciones para evaluar; no son nuevas
dependencias del prototipo.

No se realizan esperas de comunicaciones industriales dentro del integrador.
El registro JSONL sigue escribiéndose desde el worker: un disco lento puede
añadir retraso. La integración SciPy se reinicia en cada intervalo; hay que
medir ambos costes antes de exigir cadencias mayores. El servicio HTTP actual
es local y no se prepara para exposición pública.

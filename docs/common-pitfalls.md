# Common pitfalls

## Why does changing the real-time cadence affect my PLC loop?

`dt` is the simulated time advanced in each step. `dt_real` is the target
interval between step deadlines in wall-clock seconds. It defaults to
`dt`. In the CLI, `--dt` and `--dt-real` set the two intervals.

For example, with `dt=1.0` and `dt_real=0.1`, a step advances one simulated
second every 100 ms of wall-clock time when the worker keeps up. A PLC that
sends a command every 200 ms of wall-clock time then updates the input
about every two simulated seconds. The clocks remain independent even when
the PLC period matches `dt_real`, so commands can arrive at different
points in the step interval. The worker holds the last applied input across
any step without a new command.
If the worker misses a deadline, it continues through each simulation step
and reports the lag in wall-clock seconds.

See {ref}`execution cadence <execution-cadence>` for setup examples.

## Why do the snapshot and the sensor reading differ?

Use `snapshot()` or `GET /api/state` to inspect the committed model state
`x` before sensor limits. Use `read_measurements()` or
`GET /api/measurements` for the named readings available to the controller.
The measurement view clips each state reading to its configured output
bounds.

For a model current of 2.5 A and a sensor upper bound of 2 A, the snapshot
contains 2.5 A and the named current reading is 2 A. The integrator
continues from the model state. A running worker can also commit a step
between the two reads.

See {ref}`simulation snapshots and sensor measurements <snapshot-and-measurements>`
for the fields returned by each method.

## Why has an accepted command not reached the model?

An acknowledgement from `submit_input()` or HTTP `202` from
`POST /api/input` confirms receipt. The worker selects an input at the
start of each interval, before waiting for the step deadline. A newer command
can replace an accepted command before it is selected. After a successful
step, the snapshot reports the input that was applied.

For example, if the acknowledgement returns `accepted_input_seq=8` and the
snapshot still shows `input_seq=7`, it still reports the input from command
7. A snapshot with `input_seq=8` confirms command 8 was applied in its latest
committed interval. If the snapshot has already advanced to `input_seq=9`,
check the recorded snapshots to find out whether command 8 was applied,
provided recording was enabled.
The acknowledgement's `applied_input_seq` reports what was already applied
at receipt time. The last applied input is held until another command is
applied.

Inspect the snapshot's `u` and `input_saturated` together. With a 10 V upper
bound, a request for 12 V and a request for exactly 10 V both apply 10 V.
The saturation flag for that input is `True` only for the 12 V request,
because the actuator limit changed the requested value.

See [HTTP observation and control](http.md) for command and snapshot fields.

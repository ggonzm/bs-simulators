"""Simulation and HTTP lifecycle for terminal execution."""

import threading

import typer

from . import presentation


def run_simulation(configuration, *, parameters, initial, held, dt,
                   active_controller, stepper, port, duration, record, dt_real=None):
    """Run one selected experiment, release its resources and report its outcome."""
    from ..comms.http import create_server

    simulation = None
    server = None
    server_thread = None
    try:
        try:
            simulation = configuration.create(parameters=parameters, x0=initial, u0=held,
                                              dt=dt, active_controller=active_controller,
                                              stepper=stepper)
            server = create_server(simulation, port=port)
            simulation.start(duration=duration, record=record, dt_real=dt_real)
        except (ValueError, OSError, TypeError, IndexError, ArithmeticError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        server_thread = threading.Thread(
            target=server.serve_forever, kwargs={'poll_interval': 0.1},
            name='simulation-http', daemon=True,
        )
        server_thread.start()
        base = f'http://127.0.0.1:{server.server_address[1]}'
        presentation.show_started(base, simulation.snapshot().get('viewer'))
        while not simulation.wait(0.1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if simulation is not None:
            simulation.stop()
        if server is not None:
            if server_thread is not None and server_thread.ident is not None:
                server.shutdown()
            server.server_close()
        if server_thread is not None and server_thread.ident is not None:
            server_thread.join()

    if simulation is not None:
        snapshot = simulation.snapshot()
        if snapshot['status'] == 'error':
            presentation.show_error(snapshot['error'])
            raise typer.Exit(code=1)
        presentation.show_finished(snapshot, record)

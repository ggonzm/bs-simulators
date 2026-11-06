"""Demonstrate a named-signal bridge without an industrial protocol or HTTP writes.

The bridge submits torque and reads measurements through a Python Protocol.
An optional local HTTP server lets a browser observe the same experiment.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Annotated

import typer


def main(
    duration: Annotated[float, typer.Option('--duration', help='Simulated experiment duration.')] = 6.0,
    port: Annotated[int | None, typer.Option(
        '--port', min=0, max=65535, help='Optional local read-only viewer port.',
    )] = None,
    record: Annotated[Path | None, typer.Option(
        '--record', dir_okay=False, help='New JSONL recording path.',
    )] = None,
) -> None:
    """Run the named-signal bridge demonstration."""
    from bs_simulators import InMemoryAdapter, load_configuration
    from bs_simulators.comms.http import create_server

    if not 5 <= duration < float('inf'):
        raise typer.BadParameter('duration must be finite and at least 5 simulated seconds',
                                 param_hint='--duration')
    simulation = load_configuration('pendulum').create(active_controller='plc-demo', dt=0.01)
    bridge = InMemoryAdapter(simulation, controller_id='plc-demo')
    server = None
    server_thread = None
    try:
        try:
            server = create_server(simulation, port=port) if port is not None else None
            simulation.start(duration=duration, record=record)
        except (ValueError, OSError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        if server is not None:
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            print(f'Viewer: http://127.0.0.1:{server.server_address[1]}/', flush=True)
        print('Named-signal bridge demo; no physical PLC is connected.', flush=True)
        for target_time, torque in [(0.0, 0.0), (2.0, 2.0), (4.0, -1.0)]:
            while bridge.read_snapshot()['t'] < target_time:
                if bridge.read_snapshot()['status'] != 'running':
                    raise RuntimeError('simulation ended before the next command')
                time.sleep(0.01)
            receipt = bridge.write_signals(
                {'torque': torque},
                source_timestamp=datetime.now(timezone.utc).isoformat())
            deadline = time.monotonic() + 5
            while bridge.read_snapshot()['input_seq'] != receipt['accepted_input_seq']:
                if time.monotonic() > deadline or bridge.read_snapshot()['status'] != 'running':
                    raise RuntimeError('command was not observed as applied')
                time.sleep(0.01)
            print(json.dumps(bridge.read_measurements()), flush=True)
        print('Bridge stops sending commands; the last torque remains active.', flush=True)
        while bridge.read_snapshot()['status'] == 'running':
            time.sleep(0.05)
        if bridge.read_snapshot()['status'] == 'error':
            raise RuntimeError(bridge.read_snapshot()['error'])
        print(json.dumps(bridge.read_snapshot()), flush=True)
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        simulation.stop()
        if server_thread is not None and server_thread.ident is not None:
            server.shutdown()
            server_thread.join()
        if server is not None:
            server.server_close()


if __name__ == '__main__':
    typer.run(main)

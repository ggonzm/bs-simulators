"""Demonstrate a named-signal bridge without an industrial protocol or HTTP writes.

The bridge submits torque and reads measurements through a Python Protocol.
An optional local HTTP server lets a browser observe the same experiment.
"""

import argparse
from datetime import datetime, timezone
import json
import threading
import time

from bs_simulators import InMemoryAdapter, create_pendulum
from bs_simulators.comms.http import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=float, default=6.0)
    parser.add_argument('--port', type=int, help='optional local read-only viewer port')
    parser.add_argument('--record', help='new JSONL recording path')
    args = parser.parse_args()
    if not 5 <= args.duration < float('inf'):
        parser.error('duration must be finite and at least 5 simulated seconds')
    simulation = create_pendulum(active_controller='plc-demo', dt=0.01)
    bridge = InMemoryAdapter(simulation, controller_id='plc-demo')
    server = create_server(simulation, port=args.port) if args.port is not None else None
    server_thread = None
    try:
        simulation.start(duration=args.duration, record=args.record)
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
    finally:
        simulation.stop()
        if server_thread is not None:
            server.shutdown()
            server_thread.join()
        if server is not None:
            server.server_close()


if __name__ == '__main__':
    main()

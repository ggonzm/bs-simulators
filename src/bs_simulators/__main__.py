"""Run ``python -m bs_simulators`` for the local demo."""

import argparse
import threading

from .presets import create_pendulum
from .comms.http import create_server


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local soft-real-time pendulum simulator')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--dt', type=float, default=0.01, help='fixed simulation step in seconds')
    parser.add_argument('--duration', type=float, help='simulated seconds; must be a multiple of dt')
    parser.add_argument('--record', help='new JSONL output file (existing files are preserved)')
    parser.add_argument('--theta0', type=float, default=0.6, help='initial angle in radians')
    parser.add_argument('--omega0', type=float, default=0.0, help='initial angular velocity in rad/s')
    parser.add_argument('--u0', type=float, default=0.0, help='initial torque in N m')
    parser.add_argument('--m', type=float, default=1.0, help='mass in kg')
    parser.add_argument('--length', type=float, default=1.0, help='length in m')
    parser.add_argument('--drag', type=float, default=0.1)
    parser.add_argument('--controller', '--active-controller', dest='active_controller', default='http',
                        help='one explicitly selected controller identity; HTTP writes use http')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    try:
        simulation = create_pendulum(m=args.m, L=args.length, drag=args.drag,
                                        theta0=args.theta0, omega0=args.omega0,
                                        dt=args.dt, u0=args.u0, active_controller=args.active_controller)
        server = create_server(simulation, port=args.port)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        simulation.start(duration=args.duration, record=args.record)
    except (ValueError, OSError) as exc:
        server.server_close()
        parser.error(str(exc))
    server_thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1},
                                     name='pendulum-http', daemon=True)
    server_thread.start()
    print(f'Pendulum viewer: http://127.0.0.1:{server.server_address[1]}/', flush=True)
    try:
        # A bounded wait also permits Ctrl+C on Windows without busy spinning.
        while not simulation.wait(0.1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        simulation.stop()
        server.shutdown()
        server.server_close()
        server_thread.join()
    snapshot = simulation.snapshot()
    if snapshot['status'] == 'error':
        print(snapshot['error'], flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

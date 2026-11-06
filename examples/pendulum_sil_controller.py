"""Send three torque commands from a process separate from the simulator.

Run the simulator first: python -m bs_simulators --catalogue pendulum --controller http
Then: python examples/pendulum_sil_controller.py
The final torque stays active after this controller exits.
"""

import json
import time
from typing import Annotated
from urllib.request import Request, urlopen

import typer


def request_json(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = Request(base_url + path, data=body,
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def main(
    url: Annotated[str, typer.Option('--url', help='Running simulator URL.')] = 'http://127.0.0.1:8765',
) -> None:
    """Send the demonstration torque sequence to a running simulator."""
    try:
        initial = request_json(url, '/api/state')
        if initial['status'] != 'running':
            raise typer.BadParameter('the simulator must be running', param_hint='--url')
        start = initial['t']
        for offset, torque in [(0.0, 0.0), (2.0, 2.0), (4.0, -5.0)]:
            while True:
                state = request_json(url, '/api/state')
                if state['status'] != 'running':
                    raise RuntimeError(f'simulator stopped: {state}')
                if state['t'] >= start + offset:
                    break
                time.sleep(0.01)
            ack = request_json(url, '/api/input', {'u': [torque]})
            sequence = ack['input_seq']
            deadline = time.monotonic() + 5
            while True:
                state = request_json(url, '/api/state')
                if state['input_seq'] == sequence:
                    print(json.dumps({'t': state['t'], 'u': state['u'],
                                      'input_seq': sequence,
                                      'applied_at_sim_time': state['applied_at_sim_time']}),
                          flush=True)
                    break
                if state['status'] != 'running' or time.monotonic() > deadline:
                    raise RuntimeError('command was not observed as applied')
                time.sleep(0.01)
        print(f'Controller finished. The simulator keeps the last torque ({torque:g} N m).', flush=True)
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint='--url') from exc
    except (OSError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


if __name__ == '__main__':
    typer.run(main)

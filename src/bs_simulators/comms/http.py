"""Local HTTP transport for the pendulum proof of concept.

This intentionally small server binds only to loopback. It is for local
experiments, not public deployment. Control and observation are decoupled
from the simulation worker; slow browser connections do not block physics.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit

from .adapters import InMemoryAdapter
from ..runtime.protocols import SimulationPort


def create_server(simulation: SimulationPort, *, host='127.0.0.1', port=8765):
    """Return an unstarted local HTTP server bound to ``simulation``.

    GET /api/state returns a copied snapshot. GET /api/signals returns signal
    metadata. POST /api/input accepts ``{"u": [torque]}`` with optional
    UTC ``source_timestamp``. The fixed HTTP controller identity
    is ``http``; it can write only when that controller is explicitly selected.
    Requests acknowledge acceptance, not application.
    The next committed snapshot's input_seq identifies an applied command.
    """
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('the demo server must bind to loopback')
    adapter = InMemoryAdapter(simulation, controller_id='http')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(3.0)

        def log_message(self, format, *args):
            # Polling should not flood a user's terminal at 30 Hz.
            pass

        def _send(self, code, payload, content_type='application/json; charset=utf-8'):
            # Windows may reset a connection closed with an unread POST body,
            # hiding the error response. Drain only small, declared bodies;
            # the socket timeout still bounds an incomplete request.
            if code >= 400 and self.command == 'POST' and not getattr(self, '_body_consumed', False):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if 0 < length <= 4096 and self.headers.get('Transfer-Encoding') is None:
                        self.rfile.read(length)
                except (ValueError, OSError):
                    pass
                self._body_consumed = True
            data = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def _local_request(self):
            # Validate Host too, so DNS rebinding cannot turn arbitrary sites
            # into an apparently same-origin caller of this local service.
            expected_port = self.server.server_address[1]
            hosts = {f'127.0.0.1:{expected_port}', f'localhost:{expected_port}'}
            if expected_port == 80:
                hosts.update(('127.0.0.1', 'localhost'))
            if self.headers.get('Host', '').lower() not in hosts:
                self._send(403, {'error': 'invalid local Host'})
                return False
            origin = self.headers.get('Origin')
            if origin is not None and origin.lower() != f"http://{self.headers['Host'].lower()}":
                self._send(403, {'error': 'cross-origin requests are not allowed'})
                return False
            return True

        def do_GET(self):
            if not self._local_request():
                return
            path = urlsplit(self.path).path
            if path == '/api/state':
                self._send(200, adapter.read_snapshot())
            elif path == '/api/signals':
                self._send(200, adapter.describe_signals())
            elif path in ('/', '/viewer.html'):
                try:
                    payload = files('bs_simulators.viewers').joinpath('pendulum.html').read_bytes()
                except FileNotFoundError:
                    self._send(404, {'error': 'viewer is unavailable'})
                    return
                self._send(200, payload, 'text/html; charset=utf-8')
            else:
                self._send(404, {'error': 'unknown endpoint'})

        def do_POST(self):
            if not self._local_request():
                return
            if urlsplit(self.path).path != '/api/input':
                self._send(404, {'error': 'unknown endpoint'})
                return
            if self.headers.get('Transfer-Encoding') is not None:
                self._send(400, {'error': 'transfer encoding is unsupported'})
                return
            if self.headers.get_content_type() != 'application/json':
                self._send(415, {'error': 'Content-Type must be application/json'})
                return
            try:
                length = int(self.headers.get('Content-Length', ''))
            except ValueError:
                self._send(411, {'error': 'valid Content-Length is required'})
                return
            if not 0 < length <= 4096:
                self._send(413, {'error': 'request body must contain 1 to 4096 bytes'})
                return
            try:
                self._body_consumed = True
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError('incomplete request body')
                body = json.loads(raw)
                if (not isinstance(body, dict) or 'u' not in body or
                        not set(body) <= {'u', 'source_timestamp'}):
                    raise ValueError('expected u with optional source_timestamp')
                ack = adapter.write_inputs(body['u'], source_timestamp=body.get('source_timestamp'))
            except (ValueError, TypeError, UnicodeDecodeError) as exc:
                self._send(400, {'error': str(exc)})
                return
            except RuntimeError as exc:
                self._send(409, {'error': str(exc)})
                return
            except PermissionError as exc:
                self._send(403, {'error': str(exc)})
                return
            except TimeoutError:
                self._send(408, {'error': 'request body timed out'})
                return
            self._send(202, ack)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server

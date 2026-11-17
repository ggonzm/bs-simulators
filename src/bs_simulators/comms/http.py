"""FastAPI transport for local simulation control and browser observation."""

import asyncio
import socket
import threading
from importlib.resources import files
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapters import InMemoryAdapter
from ..runtime.protocols import SimulationPort
from ..viewers import ASSET_FILES, VIEWER_FILES


class _InputCommand(BaseModel):
    """Complete input vector received from an HTTP controller."""

    model_config = ConfigDict(
        extra='forbid', strict=True, json_schema_extra={'examples': [{'u': [2.0]}]},
    )
    u: list[FiniteFloat] = Field(description='Complete vector in the input order of GET /api/signals.')
    source_timestamp: str | None = Field(
        default=None, description='Optional ISO 8601 UTC timestamp for tracking the command.',
    )


class _InputAcknowledgement(BaseModel):
    """Metadata returned when the runtime accepts a command."""

    model_config = ConfigDict(json_schema_extra={'examples': [{
        'input_seq': 8, 'pending_input_seq': 8, 'accepted_input_seq': 8,
        'applied_input_seq': 7, 'controller_id': 'http', 'received_at_monotonic': 123.45,
    }]})
    input_seq: int = Field(description='Alias of accepted_input_seq in this acknowledgement.')
    pending_input_seq: int = Field(description='Sequence that was pending when this command was accepted.')
    accepted_input_seq: int = Field(description='Sequence assigned to this command.')
    applied_input_seq: int = Field(description='Last committed input sequence at acceptance.')
    controller_id: str = Field(description='Controller identity associated with the command.')
    received_at_monotonic: float = Field(description='Receipt time in seconds on the server monotonic clock.')


class _RequestError(BaseModel):
    """Error returned by the HTTP application."""

    error: str


_INPUT_ERROR_RESPONSES = {
    status: {'model': _RequestError, 'description': description}
    for status, description in {
        400: 'Invalid JSON, input vector, timestamp, or transfer encoding.',
        403: 'Request rejected by the Host or Origin policy, or HTTP control is unavailable.',
        408: 'Timed out while reading the request body.',
        409: 'The simulation has stopped accepting commands.',
        411: 'A valid Content-Length header is required.',
        413: 'The request body must contain 1 to 4096 bytes.',
        415: 'Content-Type must be application/json.',
        '4XX': 'Other client errors.',
    }.items()
}


class _HTTPServer:
    """Run Uvicorn with the socket and lifecycle used by notebooks and the CLI."""

    def __init__(self, app, listener):
        self._listener = listener
        self.server_address = listener.getsockname()
        self.server_port = self.server_address[1]
        self._server = uvicorn.Server(uvicorn.Config(
            app, host=self.server_address[0], port=self.server_port, workers=1,
            access_log=False, proxy_headers=False, lifespan='off',
            timeout_graceful_shutdown=3,
        ))
        self._serving = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server_close()

    def serve_forever(self, poll_interval=0.5):
        """Serve until shutdown is requested. Accept the legacy poll argument."""
        with self._serving:
            if not self._server.should_exit:
                self._server.run(sockets=[self._listener])

    def shutdown(self):
        """Signal Uvicorn and wait for the serving thread to release the socket."""
        self._server.should_exit = True
        with self._serving:
            pass

    def server_close(self):
        """Stop serving and release the listening socket."""
        self.shutdown()
        self._listener.close()


def _colab_host(origin):
    """Extract the exact authority from the HTTPS URL returned by Colab."""
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('colab_origin must be the HTTPS origin of the Colab proxy')
    return parsed.netloc.lower()


def _build_app(adapter, *, viewer, port, colab_host):
    """Configure routes and the host and origin policy for this listener."""
    read_only = colab_host is not None
    app = FastAPI(title='bs-simulators', docs_url=None if read_only else '/docs',
                  redoc_url=None, openapi_url=None if read_only else '/openapi.json')
    authorities = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if port == 80:
        authorities.update(('127.0.0.1', 'localhost'))
    if read_only:
        authorities.add(colab_host)

    @app.middleware('http')
    async def check_request(request: Request, call_next):
        host = request.headers.get('host', '').lower()
        origin = request.headers.get('origin')
        expected_origin = f'https://{host}' if host == colab_host else f'http://{host}'
        if host not in authorities:
            response = JSONResponse({'error': 'invalid local Host'}, status_code=403)
        elif origin is not None and origin.lower() != expected_origin:
            response = JSONResponse({'error': 'cross-origin requests are not allowed'}, status_code=403)
        elif read_only and request.method not in ('GET', 'HEAD'):
            response = JSONResponse({'error': 'viewer is read-only'}, status_code=405,
                                    headers={'Allow': 'GET, HEAD'})
        elif request.method == 'POST' and request.url.path == '/api/input':
            response = await _read_command(request, call_next)
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        return JSONResponse({'error': error.detail}, status_code=error.status_code,
                            headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_command(request, error):
        return JSONResponse({'error': 'expected a finite numeric vector u with optional source_timestamp'},
                            status_code=400)

    def resource(name, media_type):
        try:
            payload = files('bs_simulators.viewers').joinpath(name).read_bytes()
        except FileNotFoundError as error:
            raise HTTPException(404, 'viewer resource is unavailable') from error
        return Response(payload, media_type=media_type)

    @app.get('/', include_in_schema=False)
    @app.get('/viewer.html', include_in_schema=False)
    def scene():
        if viewer is None:
            raise HTTPException(404, 'no viewer is configured for this simulation')
        return resource(VIEWER_FILES[viewer], 'text/html; charset=utf-8')

    @app.get('/assets/{name}', include_in_schema=False)
    def asset(name: str):
        if name not in ASSET_FILES:
            raise HTTPException(404, 'unknown viewer resource')
        return resource(name, ASSET_FILES[name])

    @app.get('/api/state')
    def read_state():
        """Return the committed model state before sensor limits.

        `u` contains the effective actuator inputs. `input_seq` identifies
        the last applied command. `applied_at_sim_time` gives its step boundary.
        """
        return adapter.read_snapshot()

    @app.get('/api/measurements')
    def read_measurements():
        """Return named sensor readings, applied inputs, simulation time and status."""
        return adapter.read_measurements()

    @app.get('/api/signals')
    def read_signals():
        """Return signal names, units, types, directions and configured bounds."""
        return adapter.describe_signals()

    if not read_only:
        @app.post('/api/input', status_code=202, response_model=_InputAcknowledgement,
                  responses=_INPUT_ERROR_RESPONSES)
        def write_input(command: _InputCommand):
            """Accept an input command for a subsequent simulation interval.

            Compare the acknowledgement's `accepted_input_seq` with `input_seq`
            from GET /api/state to confirm application.
            """
            try:
                ack = adapter.write_inputs(command.u, source_timestamp=command.source_timestamp)
            except (TypeError, ValueError) as error:
                raise HTTPException(400, str(error)) from error
            except PermissionError as error:
                raise HTTPException(403, str(error)) from error
            except RuntimeError as error:
                raise HTTPException(409, str(error)) from error
            # Structural simulation ports may return different metadata.
            return JSONResponse(ack, status_code=202)

    return app


async def _read_command(request, call_next):
    """Check the framing and bound the time spent reading an input body."""
    if 'transfer-encoding' in request.headers:
        return JSONResponse({'error': 'transfer encoding is unsupported'}, status_code=400)
    content_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type != 'application/json':
        return JSONResponse({'error': 'Content-Type must be application/json'}, status_code=415)
    try:
        size = int(request.headers.get('content-length', ''))
    except ValueError:
        return JSONResponse({'error': 'valid Content-Length is required'}, status_code=411)
    if not 0 < size <= 4096:
        return JSONResponse({'error': 'request body must contain 1 to 4096 bytes'}, status_code=413)
    try:
        await asyncio.wait_for(request.body(), timeout=3)
    except TimeoutError:
        return JSONResponse({'error': 'request body timed out'}, status_code=408)
    return await call_next(request)


def create_server(
    simulation: SimulationPort, *, host='127.0.0.1', port=8765, viewer=None, colab_origin=None,
):
    """Return an unstarted loopback server for a simulation.

    The default server hosts the viewer, the HTTP API and interactive API
    documentation at /docs. Writes use the controller identity "http".
    Pass the HTTPS origin returned by Colab as colab_origin to open a
    read-only listener for the notebook iframe. Port 0 selects a free port.
    """
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('the demo server must bind to loopback')
    selected_viewer = viewer if viewer is not None else simulation.snapshot().get('viewer')
    if selected_viewer is not None and selected_viewer not in VIEWER_FILES:
        raise ValueError('unknown packaged viewer')
    colab_host = _colab_host(colab_origin) if colab_origin is not None else None
    adapter = InMemoryAdapter(simulation, controller_id='http')

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(('127.0.0.1', port))
        listener.listen()
        app = _build_app(adapter, viewer=selected_viewer, port=listener.getsockname()[1],
                         colab_host=colab_host)
        return _HTTPServer(app, listener)
    except BaseException:
        listener.close()
        raise

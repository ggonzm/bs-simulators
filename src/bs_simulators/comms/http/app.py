"""FastAPI routes, packaged viewer assets, and API schemas."""

from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...viewers import ASSET_FILES, VIEWER_FILES
from .middleware import _install_request_policy


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


def _build_app(adapter, *, viewer, port, colab_host):
    """Configure the FastAPI routes for the simulation and its viewer."""
    read_only = colab_host is not None
    app = FastAPI(title='bs-simulators', docs_url=None if read_only else '/docs',
                  redoc_url=None, openapi_url=None if read_only else '/openapi.json')
    _install_request_policy(app, port=port, colab_host=colab_host)

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

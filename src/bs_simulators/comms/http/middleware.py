"""Request validation for local control and read-only Colab viewers."""

import asyncio
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse


def _colab_host(origin):
    """Extract the exact authority from the HTTPS URL returned by Colab."""
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('colab_origin must be the HTTPS origin of the Colab proxy')
    return parsed.netloc.lower()


def _install_request_policy(app, *, port, colab_host):
    """Apply host, origin, method and body rules to one listener."""
    read_only = colab_host is not None
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

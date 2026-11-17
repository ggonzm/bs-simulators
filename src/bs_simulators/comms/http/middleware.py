"""Request validation for local control and read-only Colab viewers."""

import asyncio
import re
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse


_COLAB_PROXY_HOST = re.compile(
    r'[0-9]{1,5}-(?P<runtime>m-[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<zone>[a-z])'
    r'\.(?P<region>[a-z]+(?:-[a-z]+)+[0-9]+)\.prod\.r\.colab\.dev'
)


def _colab_host(origin):
    """Extract the exact authority from the HTTPS URL returned by Colab."""
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('colab_origin must be the HTTPS origin of the Colab proxy')
    return parsed.netloc.lower()


def _colab_upstream_authority(host):
    """Validate an exact hostname and optional port observed behind the Colab proxy."""
    if not isinstance(host, str) or not re.fullmatch(r'[a-zA-Z0-9.-]+(?::[0-9]{1,5})?', host):
        raise ValueError('colab_upstream_host must be an exact hostname with an optional port')
    urlsplit(f'//{host}').port  # Reject ports outside the permitted range.
    return host.lower()


def _infer_colab_upstream_host(public_host):
    """Derive the runtime hostname for the observed Colab proxy DNS format."""
    match = _COLAB_PROXY_HOST.fullmatch(public_host)
    if match is None:
        return None
    return '{runtime}.{region}-{zone}.c.codatalab-user-runtimes.internal'.format(**match.groupdict())


def _install_request_policy(app, *, port, colab_host, colab_upstream_host=None):
    """Apply host, origin, method and body rules to one listener."""
    read_only = colab_host is not None
    authorities = {f'127.0.0.1:{port}', f'localhost:{port}'}
    if port == 80:
        authorities.update(('127.0.0.1', 'localhost'))
    if read_only:
        authorities.add(colab_host)
        if colab_upstream_host is not None:
            authorities.add(colab_upstream_host)
    inferred_host = (_infer_colab_upstream_host(colab_host)
                     if read_only and colab_upstream_host is None else None)

    @app.middleware('http')
    async def check_request(request: Request, call_next):
        host = request.headers.get('host', '').lower()
        origin = request.headers.get('origin')
        hostname, separator, port_text = host.partition(':')
        matches_upstream = hostname == inferred_host and (
            not separator or (re.fullmatch(r'[0-9]{1,5}', port_text) and 0 < int(port_text) <= 65535)
        )
        from_colab = host in (colab_host, colab_upstream_host) or matches_upstream
        expected_origin = f'https://{colab_host}' if from_colab else f'http://{host}'
        if host not in authorities and not matches_upstream:
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

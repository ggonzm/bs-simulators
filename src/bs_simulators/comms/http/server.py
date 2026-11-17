"""Loopback listener and Uvicorn lifecycle for the CLI and notebooks."""

import socket
import threading

import uvicorn

from ...runtime.protocols import SimulationPort
from ...viewers import VIEWER_FILES
from ..adapters import InMemoryAdapter
from .app import _build_app
from .middleware import _colab_host


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

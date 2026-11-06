"""In-process adapter shared by HTTP and future external protocol bridges."""

from collections.abc import Mapping

from ..runtime.protocols import SimulationPort
from ..runtime.signals import controller_identifier


class InMemoryAdapter:
    """Read a simulation and submit commands under one fixed controller identity.

    Configure the engine's active_controller explicitly to select the controller.
    The identity is a routing/ownership label, not authentication. A bridge
    runs its network loop outside the simulation worker and can use this
    adapter directly without an intermediate HTTP request.
    """

    def __init__(self, port: SimulationPort, *, controller_id='python'):
        """Bind a simulation port and validate the controller identity used for every write."""
        self._port = port
        self.controller_id = controller_identifier(controller_id)

    def read_snapshot(self):
        """Read the committed state and command metadata through the simulation port."""
        return self._port.snapshot()

    def read_measurements(self):
        """Read state and held input values by signal name, together with time and status."""
        return self._port.read_measurements()

    def describe_signals(self):
        """Read ordered signal descriptions for mapping named inputs and measurements."""
        return self._port.describe_signals()

    def write_inputs(self, u, *, source_timestamp=None):
        """Submit a complete input vector as this controller; return acceptance metadata, without waiting for application."""
        return self._port.submit_input(u, controller_id=self.controller_id,
                                       source_timestamp=source_timestamp)

    def write_signals(self, values, *, source_timestamp=None):
        """Map a complete set of named writable signals to the input vector."""
        names = [signal['name'] for signal in self.describe_signals()
                 if signal['direction'] == 'input']
        if not isinstance(values, Mapping) or set(values) != set(names):
            raise ValueError(f'expected the complete writable signal set: {names}')
        return self.write_inputs([values[name] for name in names],
                                 source_timestamp=source_timestamp)

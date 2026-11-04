"""Structural Python protocols used by simulation transport adapters."""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SimulationPort(Protocol):
    """Expose measurements and input submission independently of transport.

    Engines satisfy this protocol structurally: inheritance is not required.
    Calls must return without waiting for integration or performing network
    I/O. Implementations synchronize access to shared state and return detached,
    JSON-compatible data that callers can inspect without changing the engine.
    Starting and stopping an experiment are outside this protocol.
    """

    def snapshot(self) -> dict[str, Any]:
        """Return a detached snapshot of the latest committed simulation state.

        Returns
        -------
        dict[str, Any]
            Model-specific state and execution metadata. The pendulum includes
            simulated time ``t``, state vector ``x``, applied input ``u``, and
            execution ``status``. Its sequence fields distinguish accepted,
            pending, and applied inputs. Transport adapters pass this data
            through without assuming a particular state-vector layout.

        Notes
        -----
        Reading must not advance the simulation or wait for the next step.
        Physics values describe a committed integration result; receipt and
        input metadata may be refreshed when the snapshot is requested.
        """
        ...

    def read_measurements(self) -> dict[str, Any]:
        """Return detached measurements keyed by their public signal names.

        Returns
        -------
        dict[str, Any]
            Named values corresponding to the signals described by
            ``describe_signals()``. For the pendulum these are ``theta``,
            ``omega``, ``torque``, ``time``, and ``status``. Readable inputs
            report the applied value, rather than a pending command.

        Notes
        -----
        Bridges use these names to map measurements to external addresses
        without interpreting the engine's internal state vector. Reading
        does not advance the simulation.
        """
        ...

    def describe_signals(self) -> list[dict[str, Any]]:
        """Return detached descriptions of the engine's public signals.

        Returns
        -------
        list[dict[str, Any]]
            Each description contains ``name``, ``unit``, ``dtype``,
            ``direction``, ``lower``, and ``upper``. Direction is relative to
            the simulator: ``input`` permits writes and ``output`` is read-only.
            All described signals can be read. Bounds are inclusive; ``None``
            means no configured bound. Input descriptions define the order
            expected by ``submit_input()``.

        Notes
        -----
        Descriptions provide metadata, not transport addresses. An adapter's
        named writes must supply the complete set of input signals.
        """
        ...

    def submit_input(self, u: Sequence[float], *, controller_id: str,
                     source_timestamp: str | None = None
                     ) -> dict[str, Any]:
        """Validate and enqueue a complete input vector for a future interval.

        Parameters
        ----------
        u : Sequence[float]
            Complete finite input vector, ordered as the input signals from
            ``describe_signals()``. For the pendulum this is ``[torque]``.
        controller_id : str
            Identity of the submitting controller. It must match the engine's
            explicitly selected active controller; it is not authentication.
        source_timestamp : str or None, optional
            ISO 8601 UTC timestamp with ``Z`` or a zero UTC offset. This is
            diagnostic metadata, not a scheduled application time. Receipt
            times use the engine's local monotonic clock.

        Returns
        -------
        dict[str, Any]
            Acceptance acknowledgement. The pendulum returns
            ``accepted_input_seq`` and ``applied_input_seq`` to distinguish
            receipt from physical application. Its snapshot reports the
            committed input sequence as ``input_seq``.

        Raises
        ------
        ValueError
            If the vector, controller identifier, or timestamp is invalid.
        PermissionError
            If the submitting controller is not the active controller.
        RuntimeError
            If the engine is no longer accepting inputs.

        Notes
        -----
        Acceptance does not wait for integration or guarantee immediate
        application. The latest pending vector replaces earlier pending
        vectors and is held constant over an integration interval. If no new
        command arrives, the last applied input remains active indefinitely. Rejected
        commands must leave the accepted input and receipt metadata unchanged.
        """
        ...

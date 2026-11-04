"""Simulation physics, pacing and transport-neutral Python protocols."""

from .core import PendulumSimulation
from .protocols import SimulationPort
from .signals import SignalDescriptor

__all__ = ['PendulumSimulation', 'SimulationPort', 'SignalDescriptor']

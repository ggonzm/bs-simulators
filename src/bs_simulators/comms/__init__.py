"""Transport adapters; no communication I/O runs in the physics worker."""

from .adapters import InMemoryAdapter

__all__ = ['InMemoryAdapter']

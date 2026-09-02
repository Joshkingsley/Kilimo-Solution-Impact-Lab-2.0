"""Nitapata? — citation-locked fertiliser-subsidy assistant (SPEC.md v1.2).

Per-message pipeline: keyword -> normalise -> scope gate -> resolve county and
declared state -> retrieve -> generate -> citation check -> render -> segment.
The public entry point is :func:`nitapata.pipeline.handle`.
"""
from nitapata.pipeline import handle  # noqa: F401

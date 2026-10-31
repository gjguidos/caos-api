"""Immutable Agent definition history (REV-1, ADR-0005).

Agent-neutral, insert-only revision records with chained digests, and a read-only
baseline verifier. Nothing here approves, applies or projects a revision, writes
``coll_agents`` or grants anything.
"""

"""Nataraja: deterministic, development-only environment supervisor (DEV-0.1).

Inspects the actual local CAOS environment, compares it with a versioned manifest,
verifies the Genesis lifecycle in disposable infrastructure and proposes a dry-run
reconciliation plan. It never writes Mongo or Redis, never creates or activates
Agents and never bypasses Genesis, Khepri, UTU, CPBX or Seshat: every proposed
action names the existing System Citizen or service that must perform it.
"""

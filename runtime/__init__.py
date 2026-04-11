"""
Cookie-Fooocus v3 — Runtime Package
=====================================
Contains two mutually exclusive profiles:

    runtime.local  — single-user desktop/web UI (forgiving, no auth)
    runtime.server — multi-user API server (strict, auth required)

Only one profile is active per process. Controlled by CF_MODE env var in
entrypoint.py. Never import both profiles in the same process.
"""

"""Run Watercolor Stock under a production WSGI server (Waitress).

Usage:  python serve.py   (or: venv\\Scripts\\python.exe serve.py)

This serves the SAME app as app.py, but through Waitress instead of Flask's
development server: no debug mode, no auto-reload, and able to handle many
visitors at once. This is what you'd use when hosting the app for real.

Configurable via environment variables:
    WATERCOLOR_HOST  (default 127.0.0.1 — your machine only)
    WATERCOLOR_PORT  (default 8000)
"""

import os

from waitress import serve

from app import app  # importing runs app.py's setup, incl. database.init_db()

host = os.environ.get("WATERCOLOR_HOST", "127.0.0.1")
port = int(os.environ.get("WATERCOLOR_PORT", "8000"))

print(f"Watercolor Stock (production server) → http://{host}:{port}")
print("Press Ctrl+C to stop.")
serve(app, host=host, port=port)

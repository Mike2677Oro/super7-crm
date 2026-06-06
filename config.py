"""
config.py — Super7 CRM
Configuración global del proyecto.
Los valores sensibles se sobreescriben desde variables de entorno o server.py.
"""

import os

# ── Ontraport ──────────────────────────────────────────────────────────────────
ONTRAPORT_API_KEY = os.environ.get("ONTRAPORT_API_KEY", "")
ONTRAPORT_APP_ID  = os.environ.get("ONTRAPORT_APP_ID",  "")

# ── emBlue ─────────────────────────────────────────────────────────────────────
EMBLUE_API_KEY  = os.environ.get("EMBLUE_API_KEY", "")
EMBLUE_BASE_URL = os.environ.get("EMBLUE_BASE_URL", "https://api.embluemail.com")

# ── Google Sheets ──────────────────────────────────────────────────────────────
GS_CREDENTIALS_FILE = os.environ.get("GS_CREDENTIALS_FILE", "credentials.json")
GS_SHEET_CABA = os.environ.get("GS_SHEET_CABA", "")
GS_SHEET_MDZ  = os.environ.get("GS_SHEET_MDZ",  "")
GS_TAB_CABA   = os.environ.get("GS_TAB_CABA",   "Jugadores")
GS_TAB_MDZ    = os.environ.get("GS_TAB_MDZ",    "Jugadores")

# ── OpenAI ─────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ── Flask ──────────────────────────────────────────────────────────────────────
FLASK_DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
FLASK_PORT  = int(os.environ.get("PORT", 5000))
MAX_UPLOAD_MB = 50

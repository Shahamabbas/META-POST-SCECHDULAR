"""Vercel serverless entrypoint for Meta Post Scheduler."""
import os
import sys
from pathlib import Path

# Add the meta-scheduler directory to Python path
ROOT_DIR = Path(__file__).resolve().parent.parent
SCHEDULER_DIR = ROOT_DIR / "meta-scheduler"
if str(SCHEDULER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_DIR))

import app as scheduler_app
from app import Handler, handler, app, application, init

# Initialize database tables on startup
try:
    init()
except Exception:
    pass

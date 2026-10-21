"""Root entrypoint for Meta Post Scheduler."""
import os
import sys
import importlib.util
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SCHEDULER_DIR = ROOT_DIR / "meta-scheduler"
if str(SCHEDULER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_DIR))

# Load meta-scheduler/app.py cleanly to avoid circular self-import
spec = importlib.util.spec_from_file_location("scheduler_core", SCHEDULER_DIR / "app.py")
core = importlib.util.module_from_spec(spec)
sys.modules["scheduler_core"] = core
spec.loader.exec_module(core)

# Inherit handler from core.Handler for BaseHTTPRequestHandler support
class handler(core.Handler):
    pass

Handler = handler
app = handler
application = handler
init = core.init
worker = core.worker

try:
    init()
except Exception:
    pass

if __name__ == "__main__":
    import threading
    from http.server import ThreadingHTTPServer

    PASSWORD = os.environ.get("APP_PASSWORD", "")
    if len(PASSWORD) < 12:
        raise SystemExit("Set APP_PASSWORD to a unique password of at least 12 characters before starting.")
    init()
    threading.Thread(target=worker, daemon=True).start()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Meta Scheduler: http://{host}:{port} — keep this process running for scheduling.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()

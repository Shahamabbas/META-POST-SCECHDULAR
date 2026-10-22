"""Root entrypoint for Meta Post Scheduler."""
import os
import sys
import importlib.util
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SCHEDULER_DIR = ROOT_DIR / "meta-scheduler"
if str(SCHEDULER_DIR) not in sys.path:
    sys.path.insert(0, str(SCHEDULER_DIR))

# Load .env file if available
for env_file in [ROOT_DIR / ".env", SCHEDULER_DIR / ".env"]:
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass

DEFAULT_PASSWORD = "MySchedulerPass2026!"
if not os.environ.get("APP_PASSWORD"):
    os.environ["APP_PASSWORD"] = DEFAULT_PASSWORD

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

    PASSWORD = os.environ.get("APP_PASSWORD", DEFAULT_PASSWORD)
    core.PASSWORD = PASSWORD
    init()
    threading.Thread(target=worker, daemon=True).start()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    print(f"Meta Scheduler: http://{host}:{port} — password: {PASSWORD}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()

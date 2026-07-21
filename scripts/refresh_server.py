"""V5-2: HTTP trigger for refresh_loop.

Producer-side FastAPI service. Consumer (everclaw CLI) hits this to
trigger an immediate refresh of one source without waiting for cron.

The actual ingest still runs producer-side (single writer to SQLite,
keeps the V3 architecture invariant — consumer never writes the library).

Endpoints:
    GET  /health              → {"ok": true}
    GET  /sources             → list configured sources + last refresh state
    POST /refresh?source=X    → trigger refresh of source X (or all if omitted)
    GET  /status              → currently-running refresh job (if any)

Usage:
    python -m skill_library.scripts.refresh_server [--port 8765] [--host 0.0.0.0]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from skill_library.scripts.refresh_loop import (  # noqa: E402
    DEFAULT_YAML, STATE_FILE, _load_state, _load_yaml,
)

# Mutex: at most one refresh running at a time (avoid SQLite write contention).
_REFRESH_LOCK = threading.Lock()
_CURRENT_JOB: dict[str, Any] = {}


def _run_refresh_async(source: str | None, force: bool = False) -> dict[str, Any]:
    """Spawn refresh_loop subprocess. Returns job descriptor."""
    cmd = [sys.executable, "-u", "-m", "skill_library.scripts.refresh_loop"]
    if source:
        cmd += ["--source", source]
    if force:
        cmd += ["--force"]
    log_path = Path("/tmp") / f"refresh_{(source or 'all').replace('/', '_')}_{int(time.time())}.log"
    proc = subprocess.Popen(
        cmd, stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,
    )
    _CURRENT_JOB.update({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": source or "all-due",
        "force": force,
        "pid": proc.pid,
        "log": str(log_path),
    })
    _CURRENT_JOB.pop("finished_at", None)
    _CURRENT_JOB.pop("returncode", None)

    def _watch():
        proc.wait()
        _CURRENT_JOB["finished_at"] = datetime.now(timezone.utc).isoformat()
        _CURRENT_JOB["returncode"] = proc.returncode
        _REFRESH_LOCK.release()
    threading.Thread(target=_watch, daemon=True).start()
    return dict(_CURRENT_JOB)


def _build_app():
    """Construct FastAPI app. Imported lazily so the module can be
    introspected without FastAPI installed."""
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import JSONResponse

    app = FastAPI(title="skill_library refresh trigger", version="1.0")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/sources")
    def sources():
        cfg = _load_yaml(DEFAULT_YAML)
        state = _load_state()
        return {
            "sources": [
                {
                    "name": s.get("name"),
                    "repo": s.get("repo"),
                    "pull_cadence": s.get("pull_cadence"),
                    "type": s.get("type"),
                    "last_success": state.get(s.get("name"), {}).get("last_success"),
                    "last_added": state.get(s.get("name"), {}).get("last_added"),
                }
                for s in cfg.get("sources", [])
            ],
            "defaults": cfg.get("defaults", {}),
        }

    @app.get("/status")
    def status():
        if _CURRENT_JOB and "finished_at" not in _CURRENT_JOB:
            return _CURRENT_JOB
        return JSONResponse(status_code=204, content=None)

    @app.post("/refresh")
    def refresh(source: str | None = Query(default=None),
                force: bool = Query(default=False)):
        if not _REFRESH_LOCK.acquire(blocking=False):
            raise HTTPException(409, "refresh already running, see /status")
        try:
            return _run_refresh_async(source, force=force)
        except Exception as e:
            _REFRESH_LOCK.release()
            raise HTTPException(500, f"failed to start refresh: {e}")

    return app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("!! uvicorn not installed. pip install fastapi uvicorn",
              file=sys.stderr)
        return 1
    app = _build_app()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

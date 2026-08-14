"""HTTP adapter — for hosts that are not Python.

OpenClaw's plugins are TypeScript, so it cannot import this package. It
can, however, make one HTTP call on its ``before_prompt_build`` hook. This
serves exactly that:

    POST /retrieve   {"query": "..."}  ->  {"text": "..."}
    GET  /health                       ->  {"ok": true}

Retrieval is stateless: it searches the query it is given and nothing
else, so there is no session to identify. A caller may send extra fields;
they are ignored.

Run it next to the agent::

    python -m skillsearch.adapters.http_server --config ./skillsearch.json

One caveat worth stating plainly: over HTTP the server and the agent may
not share a filesystem, so ``{baseDir}`` and relative links in a skill body
cannot be resolved to paths the agent can open. Set ``resolve_refs: false``
when the two are on different machines — otherwise the injected text
promises files the agent cannot read.

Binds to loopback by default and has no authentication. Put it behind a
proxy before exposing it anywhere else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from skillsearch.config import SearchConfig
from skillsearch.engine import SkillSearch

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 1 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    search: SkillSearch
    loop: asyncio.AbstractEventLoop
    timeout_s: float = 10.0

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/retrieve":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "request too large"})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            query = str(data.get("query") or "")
            future = asyncio.run_coroutine_threadsafe(
                type(self).search.retrieve(query),
                type(self).loop,
            )
            try:
                self._send(200, {"text": future.result(timeout=type(self).timeout_s)})
            except TimeoutError:
                # Stop the work, not just the waiting: the coroutine would
                # otherwise run to completion on the server's loop long
                # after nobody is waiting for its answer.
                future.cancel()
                raise
        except TimeoutError:
            # Fail open, like every other adapter: an empty block costs the
            # turn its skills, an error would cost the host its turn.
            self._send(200, {"text": ""})
        except Exception as e:
            log.warning("skillsearch: /retrieve failed (%s)", e)
            self._send(200, {"text": ""})


def serve(
    config: SearchConfig,
    *,
    host: str = "127.0.0.1",
    port: int = 8477,
    timeout_s: float = 10.0,
) -> None:
    loop = asyncio.new_event_loop()

    import threading

    threading.Thread(target=loop.run_forever, name="skillsearch-loop", daemon=True).start()

    _Handler.search = SkillSearch(config)
    _Handler.loop = loop
    _Handler.timeout_s = timeout_s
    log.info("skillsearch: listening on %s:%d", host, port)
    ThreadingHTTPServer((host, port), _Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", help="JSON file of SearchConfig fields")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8477)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw: dict[str, Any] = {}
    if args.config:
        raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    serve(
        SearchConfig.from_mapping(raw),
        host=args.host,
        port=args.port,
        timeout_s=args.timeout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

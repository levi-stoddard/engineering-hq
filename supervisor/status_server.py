"""Read-only status page for the supervisor, on 127.0.0.1 only.

GET only. Every other method is refused. Requests whose Host header is not this loopback
address are refused (protection against DNS rebinding), and so are cross-site requests that
send an Origin header. It shows state; it has no way to change anything. There is deliberately
no approve button: a click on an unauthenticated local page cannot prove who made it.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_config  # noqa: E402
from policy import Policy  # noqa: E402
from registers import Registers  # noqa: E402

MARKER = "engineering-hq status"


def make_handler(config: dict[str, Any], port: int) -> type[BaseHTTPRequestHandler]:
    state_dir = Path(config["state_dir"])
    registers = Registers(state_dir)
    policy = Policy.from_config(config)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def requests_view() -> list[dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for row in registers.requests.rows():
            if row["kind"] == "status":
                statuses[row["request_id"]] = row
        return [
            {
                "id": r["id"],
                "action": r["action"],
                "class": policy.classify(r["action"]).value,
                "requester": r["requester"],
                "summary": r["summary"],
                "status": statuses.get(r["id"], {}).get("status"),
                "detail": statuses.get(r["id"], {}).get("detail"),
            }
            for r in registers.requests.find(kind="request")
        ]

    routes = {
        "/health": lambda: {"service": MARKER, "ok": True},
        "/api/state": lambda: json.loads((state_dir / "state.json").read_text(encoding="utf-8")) if (state_dir / "state.json").exists() else {},
        "/api/requests": requests_view,
        "/api/evidence": lambda: registers.evidence.rows(),
        "/api/decisions": lambda: [
            {**row, "status": registers.decision_status(row["id"])} for row in registers.decisions.find(kind="decision")
        ],
        "/api/verify": lambda: {"problems": registers.verify()},
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "engineering-hq-status/1.0"
        sys_version = ""

        def _send(self, status: int, body: Any) -> None:
            data = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.headers.get("Host") not in allowed_hosts:
                return self._send(403, {"error": "host not allowed"})
            if self.headers.get("Origin"):
                return self._send(403, {"error": "cross-origin requests are not served"})
            route = routes.get(self.path.split("?", 1)[0])
            if route is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, route())

        def _refuse(self) -> None:
            self._send(405, {"error": "read-only: only GET is served"})

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

        def log_message(self, *_args: Any) -> None:
            pass

    return Handler


def serve(config: dict[str, Any], port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(config, port))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = serve(load_config(Path(args.config)), args.port)
    print(f"status page on http://127.0.0.1:{args.port}/api/requests (read-only)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

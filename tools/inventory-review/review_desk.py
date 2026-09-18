"""Run the local NetAlertX review desk or export a portable interactive demo."""

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from desk_ai import OllamaClient
from desk_data import prepare_report
from inventory_review import compare, read_devices

ROOT = Path(__file__).resolve().parent
MAX_BODY = 8 * 1024 * 1024


def demo_report():
    """Build a fictional comparison using the shipped native export examples."""
    before = read_devices(ROOT / "examples" / "before.json")
    after = read_devices(ROOT / "examples" / "after.json")
    stable = {
        "devMac": "02:00:00:00:00:06",
        "devName": "Core gateway",
        "devOwner": "IT",
        "devLastIP": "192.0.2.1",
        "devType": "Router",
        "devVlan": "10",
        "devSite": "Demo office",
        "devLocation": "Rack",
    }
    before.append(stable)
    after.append(stable)
    return {
        "schema_version": 1,
        "scope": "Demo office and branch",
        "before_at": "2026-09-17T12:00:00Z",
        "after_at": "2026-09-18T12:00:00Z",
        "baseline_devices": len(before),
        "current_devices": len(after),
        "devices": compare(before, after),
    }


def document(report, offline=False, is_demo=False):
    """Render the workspace with safely embedded JSON and optional bundled assets."""
    prepared = prepare_report(report)
    canonical = {
        key: prepared[key]
        for key in (
            "scope",
            "before_at",
            "after_at",
            "baseline_devices",
            "current_devices",
        )
    }
    canonical["schema_version"] = 1
    canonical["devices"] = [
        {
            key: item[key]
            for key in (
                "change",
                "device",
                "owner_status",
                "changes",
                "uncompared_fields",
            )
        }
        for item in prepared["records"]
    ]
    initial = json.dumps(
        {"report": prepared, "source": canonical, "demo": is_demo}, ensure_ascii=True
    ).replace("<", "\\u003c")
    template = (ROOT / "desk" / "index.html").read_text()
    if offline:
        style = "<style>" + (ROOT / "desk" / "desk.css").read_text() + "</style>"
        core = (
            (ROOT / "desk" / "core.mjs")
            .read_text()
            .replace("export function", "function")
        )
        script_code = re.sub(
            r"\Aimport[\s\S]*?from\s+['\"]\./core\.mjs['\"];\s*",
            "",
            (ROOT / "desk" / "desk.mjs").read_text(),
        )
        script = '<script type="module">' + core + "\n" + script_code + "</script>"
    else:
        style = '<link rel="stylesheet" href="/desk.css">'
        script = '<script type="module" src="/desk.mjs"></script>'
    policy = (
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src data:; base-uri 'none'; form-action 'none'\">"
        if offline
        else ""
    )
    return (
        template.replace("<!--STYLES-->", style)
        .replace("<!--SCRIPT-->", script)
        .replace("<!--POLICY-->", policy)
        .replace("__MODE__", "offline" if offline else "server")
        .replace("<!--INITIAL-->", initial)
    )


def handler_for(raw_report, client=None, is_demo=False):
    """Create a request handler with immutable startup data and isolated imports."""
    page = document(raw_report, is_demo=is_demo).encode()
    client = client or OllamaClient()
    ai_lock = threading.Lock()

    class DeskHandler(BaseHTTPRequestHandler):
        """Serve only the review desk and bounded same-origin APIs."""

        def setup(self):
            """Bound the time allowed to receive an HTTP request."""
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            """Avoid printing imported evidence or model output to logs."""

        def allowed_host(self):
            """Reject unexpected Host headers, including DNS rebinding requests."""
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def send(self, code, payload, content_type="application/json"):
            """Return a noncacheable response with restrictive browser headers."""
            body = (
                json.dumps(payload).encode()
                if isinstance(payload, (dict, list))
                else payload
            )
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            """Serve a fixed resource list and local model availability."""
            if not self.allowed_host():
                self.send(403, {"error": "Unexpected request host"})
                return
            path = urlsplit(self.path).path
            if path == "/":
                self.send(200, page, "text/html; charset=utf-8")
            elif path in ("/desk.css", "/desk.mjs", "/core.mjs"):
                data = (ROOT / "desk" / path[1:]).read_bytes()
                self.send(
                    200, data, "text/css" if path.endswith("css") else "text/javascript"
                )
            elif path == "/api/models":
                try:
                    self.send(200, {"available": True, "models": client.models()})
                except ValueError as error:
                    self.send(
                        200, {"available": False, "models": [], "message": str(error)}
                    )
            else:
                self.send(404, {"error": "Not found"})

        def do_POST(self):
            """Validate local imports and generate optional read-only AI drafts."""
            origin = self.headers.get("Origin")
            if not self.allowed_host() or origin != "http://" + self.headers.get(
                "Host", ""
            ):
                self.send(
                    403, {"error": "Open the review desk locally to use this action"}
                )
                return
            path = urlsplit(self.path).path
            if path not in ("/api/prepare", "/api/summary"):
                self.send(404, {"error": "Not found"})
                return
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Type", "").split(";")[0]
                != "application/json"
            ):
                self.send(415, {"error": "Expected a JSON request"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    self.send(413, {"error": "Choose a report smaller than 8 MB"})
                    return
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected a report object")
                report = prepare_report(data.get("report"))
                if path == "/api/prepare":
                    self.send(200, {"report": report})
                elif not ai_lock.acquire(blocking=False):
                    self.send(
                        409,
                        {
                            "error": "Another AI summary is running. Retry after it finishes."
                        },
                    )
                else:
                    try:
                        self.send(200, client.summarize(data.get("model"), report))
                    finally:
                        ai_lock.release()
            except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
                self.send(
                    400,
                    {
                        "error": str(error)
                        if isinstance(error, ValueError)
                        else "Invalid report structure"
                    },
                )
            except (OSError, TimeoutError):
                self.send(
                    408,
                    {"error": "The request timed out. Retry with a smaller report."},
                )

    return DeskHandler


def main():
    """Start a loopback workspace or export its interactive HTML preview."""
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true")
    source.add_argument(
        "--report", type=Path, help="Version 1 inventory-review JSON output"
    )
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--export",
        type=Path,
        help="Write a standalone HTML preview instead of starting a server",
    )
    args = parser.parse_args()
    try:
        if args.report and args.report.stat().st_size > MAX_BODY:
            raise ValueError("Choose a report smaller than 8 MB")
        raw = (
            demo_report()
            if args.demo
            else json.loads(args.report.read_text(encoding="utf-8-sig"))
        )
        prepare_report(raw)
        if args.export:
            if args.report and args.export.resolve() == args.report.resolve():
                raise ValueError("The preview cannot overwrite its input report")
            args.export.parent.mkdir(parents=True, exist_ok=True)
            args.export.write_text(
                document(raw, offline=True, is_demo=args.demo), encoding="utf-8"
            )
            print(f"Preview saved: {args.export}")
            return
        if not 1024 <= args.port <= 65535:
            raise ValueError("Choose a port between 1024 and 65535")
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), handler_for(raw, is_demo=args.demo)
        )
        print(f"Review desk: http://127.0.0.1:{args.port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    except (ValueError, OSError) as error:
        parser.exit(2, f"Cannot open the review desk: {error}\n")


if __name__ == "__main__":
    main()

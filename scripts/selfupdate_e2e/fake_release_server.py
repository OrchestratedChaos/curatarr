"""Minimal local HTTP server standing in for GitHub Releases, for the
self-update real end-to-end CI workflow
(.github/workflows/selfupdate-e2e.yml).

Serves:
  GET /api/latest                          -> {"tag_name": "v<TAG_VERSION>"}
  GET /download/v<TAG_VERSION>/<filename>   -> bytes of RELEASE_DIR/<filename>

RELEASE_DIR and TAG_VERSION are passed on argv so the same script can be
pointed at any of the prepared fixture directories (good / bad_hash /
bad_sig / rollback - see scripts/selfupdate_e2e/build_fixtures.py).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RELEASE_DIR = sys.argv[1]
TAG_VERSION = sys.argv[2]
PORT = int(sys.argv[3])


class FastBindThreadingHTTPServer(ThreadingHTTPServer):
    """HTTPServer.server_bind() calls socket.getfqdn(host) to set
    self.server_name - a reverse DNS lookup that's completely
    irrelevant for a loopback-only test server, but confirmed (via
    direct testing - see this repo's v2.8.29 PR description) to HANG
    for 30+ seconds, sometimes effectively indefinitely, in some
    sandboxed/CI network configurations, even for 127.0.0.1. That kept
    this server "alive" (process running) but never actually reaching
    the LISTEN state, so every client request timed out - looking
    exactly like the self-update logic was broken when it never was.
    Skips the FQDN lookup entirely; nothing here ever needs it."""

    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[fake-release-server] {self.address_string()} - {fmt % args}\n")

    def do_GET(self):
        if self.path == '/api/latest':
            body = json.dumps({'tag_name': f'v{TAG_VERSION}'}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        prefix = f'/download/v{TAG_VERSION}/'
        if self.path.startswith(prefix):
            filename = self.path[len(prefix):]
            filepath = os.path.join(RELEASE_DIR, filename)
            if not os.path.isfile(filepath):
                self.send_response(404)
                self.end_headers()
                return
            with open(filepath, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    server = FastBindThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f"[fake-release-server] serving {RELEASE_DIR} as v{TAG_VERSION} on 127.0.0.1:{PORT}", flush=True)
    server.serve_forever()

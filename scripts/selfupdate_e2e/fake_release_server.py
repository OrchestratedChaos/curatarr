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
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f"[fake-release-server] serving {RELEASE_DIR} as v{TAG_VERSION} on 127.0.0.1:{PORT}", flush=True)
    server.serve_forever()

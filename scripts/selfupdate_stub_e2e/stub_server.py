"""Tiny stand-in 'binary' server used by the self-update hand-off
script's fast local test harness (see build_stubs.py). Serves
GET /healthz -> {"version": "<VERSION>"} on $CURATARR_UI_PORT, or
simulates a broken build via MODE.

Usage: python stub_server.py <normal|crash|hang> <version>
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[1]
VERSION = sys.argv[2]

if MODE == 'crash':
    sys.exit(1)

if MODE == 'hang':
    while True:
        time.sleep(3600)

PORT = int(os.environ.get('CURATARR_UI_PORT', '8787'))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == '/healthz':
            body = json.dumps({'version': VERSION}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    server.serve_forever()

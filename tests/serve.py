"""Loopback-only, allowlisted test server; never serves local secrets or Git data."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import re
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        pathname = unquote(parsed.path)
        if pathname.startswith('/DokDoki-Launcher/'):
            root = ROOT / 'website'
            relative = pathname.removeprefix('/DokDoki-Launcher/') or 'index.html'
        elif pathname.startswith('/tests/'):
            root = ROOT / 'tests'
            relative = pathname.removeprefix('/tests/') or 'index.html'
            if not relative.endswith(('.html', '.js')):
                self.send_error(404)
                return
        elif pathname == '/worker/src/index.js':
            root = ROOT / 'worker' / 'src'
            relative = 'index.js'
        else:
            self.send_error(404)
            return
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or any(part.startswith('.') for part in Path(relative).parts) or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        if root == ROOT / 'website' and path.suffix == '.html' and '_test' in parse_qs(parsed.query):
            body = body.replace(b'<head>', b'<head>\n  <script src="/tests/bootstrap.js"></script>', 1)
            if '_manual' in parse_qs(parsed.query):
                body = re.sub(rb'  <script src="js/requests.js" defer></script>', b'', body)
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Even an incorrectly configured browser fixture cannot write upstream.
        self.send_error(405)

    def log_message(self, *args):
        pass


def make_server(port=0):
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(f'Local mocked tests: http://127.0.0.1:{server.server_port}/tests/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

"""Loopback-only, allowlisted test server; never serves local secrets or Git data."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import html
import json
import re
from urllib.parse import parse_qs, unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
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
        elif pathname == '/worker/wrangler.jsonc':
            root = ROOT / 'worker'
            relative = 'wrangler.jsonc'
        else:
            self.send_error(404)
            return
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or any(part.startswith('.') for part in Path(relative).parts) or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        if root == ROOT / 'website' and path.suffix == '.html' and '_test' in query:
            body = body.replace(b'<head>', b'<head>\n  <script src="/tests/bootstrap.js"></script>', 1)
            if '_config' in query:
                script_query = urlencode({'_test': query['_test'][0], '_config': query['_config'][0]})
                script_src = html.escape('js/requests.js?' + script_query, quote=True).encode()
                body = body.replace(b'src="js/requests.js"', b'src="' + script_src + b'"')
        if root == ROOT / 'website' and relative == 'js/requests.js' and '_test' in query and '_config' in query:
            # Test-only source variants exercise closed/invalid configuration without
            # introducing any configuration override into the deployed application.
            try:
                config = json.loads(query['_config'][0])
                if (not isinstance(config, dict) or set(config) != {'SUBMISSIONS_ENABLED', 'API_BASE_URL', 'TURNSTILE_SITE_KEY'}
                        or type(config['SUBMISSIONS_ENABLED']) is not bool
                        or not all(isinstance(config[key], str) for key in ('API_BASE_URL', 'TURNSTILE_SITE_KEY'))):
                    raise ValueError('Invalid fixture configuration')
                source = body.decode()
                for key, value in config.items():
                    source, count = re.subn(r'const ' + key + r' = [^;]+;', lambda _: 'const ' + key + ' = ' + json.dumps(value) + ';', source)
                    if count != 1:
                        raise ValueError('Missing configuration constant')
                body = source.encode()
            except (ValueError, TypeError):
                self.send_error(400)
                return
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

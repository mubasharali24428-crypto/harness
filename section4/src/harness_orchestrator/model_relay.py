"""Fixed destination HTTP relay: no CONNECT, redirects, arbitrary hosts, or logging."""

import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP = {
    "connection",
    "proxy-connection",
    "transfer-encoding",
    "host",
    "content-length",
    "keep-alive",
    "upgrade",
    "te",
    "trailer",
    "proxy-authorization",
}


def main():
    scheme, host, port = sys.argv[1:]

    class Relay(BaseHTTPRequestHandler):
        def do_POST(self):
            self.forward()

        def do_GET(self):
            self.forward()

        def forward(self):
            if not self.path.startswith("/") or self.path.startswith("//"):
                self.send_error(400)
                return
            connection = (
                http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
            )(host, int(port), timeout=180)
            try:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
                connection.request(self.command, self.path, body, headers)
                response = connection.getresponse()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, ValueError, http.client.HTTPException):
                self.close_connection = True
            finally:
                connection.close()

        def log_message(self, *_args):
            pass

    ThreadingHTTPServer(("0.0.0.0", 8080), Relay).serve_forever()


if __name__ == "__main__":
    main()

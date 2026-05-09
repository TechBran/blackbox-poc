#!/usr/bin/env python3
"""
Apparition - 3D AI Avatar App Server
Serves static files on port 8073
"""

import http.server
import socketserver
import os

PORT = 8073
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # CORS headers for API calls
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # Cache control for development
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        # Custom log format with timestamp
        import datetime
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] {args[0]}")


if __name__ == '__main__':
    print(f"""
    ╔═══════════════════════════════════════════════════════╗
    ║           APPARITION - 3D AI Avatar                   ║
    ║                                                       ║
    ║   Server running at: http://localhost:{PORT}/          ║
    ║   Press Ctrl+C to stop                                ║
    ╚═══════════════════════════════════════════════════════╝
    """)

    try:
        with ReusableTCPServer(("", PORT), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server stopped]")
    except OSError as e:
        if e.errno == 98:
            print(f"Error: Port {PORT} is already in use.")
            print("Try: kill $(lsof -t -i:{PORT})")
        else:
            raise

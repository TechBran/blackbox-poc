#!/usr/bin/env python3
"""
Modern Pac-Man Game Server
Serves the modernized Pac-Man web application
"""

import http.server
import socketserver
import os

PORT = 8074
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        print(f"[Pac-Man Modern] {self.address_string()} - {format % args}")

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    os.chdir(DIRECTORY)

    with ReusableTCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"🎮 Modern Pac-Man server starting on port {PORT}")
        print(f"📁 Serving directory: {DIRECTORY}")
        print(f"👻 Access at: http://localhost:{PORT}")
        print(f"🕹️  Press Ctrl+C to stop the server\n")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🎮 Pac-Man server stopped gracefully")
            httpd.shutdown()

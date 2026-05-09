#!/usr/bin/env python3
"""
Squeeze Cat App Server
Serves the Squeeze Cat Holistic Pelvic Trainer web application
"""

import http.server
import socketserver
import os

PORT = 7001
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Add CORS headers
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        # Disable caching completely
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def log_message(self, format, *args):
        # Custom logging
        print(f"[Squeeze Cat] {self.address_string()} - {format % args}")

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    os.chdir(DIRECTORY)

    with ReusableTCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"✨ Squeeze Cat server starting on port {PORT}")
        print(f"📁 Serving directory: {DIRECTORY}")
        print(f"🌸 Access at: http://localhost:{PORT}")
        print(f"💫 Press Ctrl+C to stop the server\n")

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🐱 Squeeze Cat server stopped gracefully")
            httpd.shutdown()

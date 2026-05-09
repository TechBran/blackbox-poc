#!/usr/bin/env python3
"""
Simple HTTP server for Retro Calculator app
"""
import http.server
import socketserver
import os

PORT = 8070
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Custom logging
        print(f"[Retro Calculator] {self.address_string()} - {format % args}")

if __name__ == "__main__":
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"[Retro Calculator] Server running at http://localhost:{PORT}")
        print(f"[Retro Calculator] Serving files from: {DIRECTORY}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[Retro Calculator] Server stopped")

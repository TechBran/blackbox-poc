#!/usr/bin/env python3
"""Simple HTTP server for the Grocery Store test app.

This server provides the web interface for the grocery store
demonstration application within the AI BlackBox system.
"""
import http.server
import socketserver
import os

# Server configuration
PORT = 8065  # Port number for the grocery store app
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        print(f"[Grocery Store] {args[0]}")

if __name__ == "__main__":
    os.chdir(DIRECTORY)
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Grocery Store running at http://localhost:{PORT}")
        print("Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")

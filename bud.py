#!/usr/bin/env python3
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
import time

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)
    
    def do_GET(self):
        # Ignore favicon requests
        if self.path == '/favicon.ico':
            self.send_response(204)  # No Content
            self.end_headers()
            return
        super().do_GET()

def main():
    os.chdir(DIRECTORY)
    print(f"Serving from: {DIRECTORY}")
    print(f"Available files: {os.listdir(DIRECTORY)}")
    
    server = HTTPServer(("", PORT), Handler)
    webbrowser.open(f"http://localhost:{PORT}")
    
    try:
        print(f"\nServer running at http://localhost:{PORT}")
        print("Press Ctrl+C to stop\n")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")

if __name__ == "__main__":
    main()
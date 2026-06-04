#!/usr/bin/env python3
import argparse
import os
import re
import urllib.parse
import http.server
import socket


class FileServerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            self.send_error(404, "File not found")
            return None
        if os.path.isdir(path):
            return self.list_directory(path)
        ctype = self.guess_type(path)
        try:
            f = open(path, 'rb')
        except OSError:
            self.send_error(404, "File not found")
            return None
        try:
            fs = os.fstat(f.fileno())
            size = fs.st_size
            last_modified = fs.st_mtime
        except OSError:
            f.close()
            self.send_error(500, "Internal server error")
            return None

        if self.headers.get('Range'):
            self._handle_range(path, f, ctype, size, last_modified)
            return None

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Last-Modified", self.date_time_string(last_modified))
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return f

    def _handle_range(self, path, f, ctype, size, last_modified):
        match = re.match(r'^bytes=(\d*)-(\d*)$', self.headers['Range'].strip())
        if not match:
            f.close()
            self.send_error(416, "Range Not Satisfiable")
            return False

        start_str, end_str = match.group(1), match.group(2)
        start = int(start_str) if start_str != '' else None
        end = int(end_str) if end_str != '' else None

        if start is None and end is not None:
            start = max(size - end, 0)
            end = size - 1
        elif start is not None and end is None:
            end = size - 1
        elif start is not None and end is not None:
            pass
        else:
            f.close()
            self.send_error(416, "Range Not Satisfiable")
            return False

        if start >= size or start < 0 or end >= size or end < start:
            f.close()
            self.send_error(416, "Range Not Satisfiable")
            self.send_header("Content-Range", f"bytes */{size}")
            return False

        length = end - start + 1
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Last-Modified", self.date_time_string(last_modified))
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        remaining = length
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)
        f.close()
        return True

    def list_directory(self, path):
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            self.send_error(500, "Internal server error")
            return None
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        lines = []
        lines.append("<!DOCTYPE html>")
        lines.append("<html><head><meta charset='utf-8'>")
        lines.append(f"<title>Index of {self.path}</title>")
        lines.append("<style>")
        lines.append("body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 40px; }")
        lines.append("h1 { font-size: 1.5em; color: #333; }")
        lines.append("ul { list-style: none; padding: 0; }")
        lines.append("li { padding: 6px 0; }")
        lines.append("a { color: #0366d6; text-decoration: none; }")
        lines.append("a:hover { text-decoration: underline; }")
        lines.append("</style></head><body>")
        lines.append(f"<h1>Index of {self.path}</h1>")
        lines.append("<ul>")
        if self.path != '/':
            parent = urllib.parse.quote(os.path.dirname(self.path.rstrip('/')))
            lines.append(f'<li><a href="{parent}/">../</a></li>')
        for name in entries:
            full = os.path.join(path, name)
            display = name + '/' if os.path.isdir(full) else name
            encoded = urllib.parse.quote(name)
            lines.append(f'<li><a href="{encoded}">{display}</a></li>')
        lines.append("</ul></body></html>")
        return self.wfile.write('\n'.join(lines).encode('utf-8'))


def main():
    parser = argparse.ArgumentParser(description='Simple HTTP File Server')
    parser.add_argument('--port', '-p', type=int, default=8080, help='Port to listen on (default: 8080)')
    parser.add_argument('--dir', '-d', default=os.getcwd(), help='Shared directory (default: current directory)')
    parser.add_argument('--bind', '-b', default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    args = parser.parse_args()

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        print(f"Error: Directory '{directory}' does not exist")
        return 1

    os.chdir(directory)

    server = http.server.ThreadingHTTPServer((args.bind, args.port), lambda *a, **kw: FileServerHandler(*a, directory=directory, **kw))
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    print(f"Serving files from: {directory}")
    print(f"Server URL:        http://{args.bind}:{args.port}/")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == '__main__':
    main()

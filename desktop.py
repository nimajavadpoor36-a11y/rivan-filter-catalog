"""Desktop app (Windows, macOS, Linux): the same interface in its own window.

  python desktop.py                       own local database, works offline
  python desktop.py --url https://your-site.example   opens your hosted site (shared data)

Package into a single executable with:  pyinstaller --noconfirm --windowed --name FilterFinder \
    --add-data "templates:templates" --add-data "static:static" desktop.py
(on Windows use ";" instead of ":" in --add-data)
"""
import argparse
import os
import socket
import sys
import threading
from pathlib import Path


def user_data_dir():
    home = Path.home()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home)) / "FilterFinder"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "FilterFinder"
    return Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share")) / "FilterFinder"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="open a hosted instance instead of running a local one")
    args = ap.parse_args()

    if args.url:
        target = args.url
    else:
        base = user_data_dir()
        os.environ.setdefault("FILTERFINDER_DATA", str(base / "data"))
        os.environ.setdefault("FILTERFINDER_UPLOADS", str(base / "uploads"))
        import app as filter_app  # imported after the paths are set
        from werkzeug.serving import make_server

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = make_server("127.0.0.1", port, filter_app.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        target = f"http://127.0.0.1:{port}/"

    try:
        import webview
    except ImportError:
        import webbrowser
        print("pywebview is not installed; opening your browser instead.")
        webbrowser.open(target)
        if not args.url:
            threading.Event().wait()
        return
    webview.create_window("Filter Finder", target, width=1200, height=820)
    webview.start()


if __name__ == "__main__":
    main()

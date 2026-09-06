"""Entry point: python3 -m query_console"""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from .server import ConsoleContext, serve

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local SQL console for the election warehouse.")
    parser.add_argument(
        "--db",
        default=str(REPO_ROOT / "election_data.db"),
        help="Warehouse to query (opened read-only). Default: election_data.db",
    )
    parser.add_argument("--port", type=int, default=8787, help="Port to listen on (default 8787).")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to bind (default localhost).")
    parser.add_argument(
        "--keep-results",
        type=int,
        default=50,
        help="Result sets to keep cached during a session (default 50). Cached rows"
        " are cleared when the console starts and when it stops; the query log itself"
        " is kept.",
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        print("Database not found: {}".format(db_path), file=sys.stderr)
        return 1

    state_dir = Path(__file__).resolve().parent / ".state"
    context = ConsoleContext(REPO_ROOT, db_path, state_dir, args.keep_results)
    httpd = serve(context, args.host, args.port)

    url = "http://{}:{}/".format(args.host, args.port)
    print("Query console  →  {}".format(url))
    print("Database       →  {} (read-only)".format(db_path))
    print("History        →  {} (rows cached for this session only)".format(
        state_dir / "history.db"))
    print("Ctrl-C to stop.")

    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()

    # Cached rows are session state, so clear them on the way out however we
    # get there. A kill -9 or a crash skips this; the wipe at startup is the
    # guarantee, this is the tidy-up.
    atexit.register(context.store.clear_results)

    def _stop(signum, frame):  # noqa: ARG001 - signal handler signature
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()
        context.store.clear_results()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

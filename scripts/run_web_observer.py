#!/usr/bin/env python3
"""Run the midicrt read-only web observer.

Read-only invariants:
- exposes only GET /, GET /healthz, and GET /ws;
- no mutation or command execution endpoints are registered;
- websocket fanout is bounded by --max-broadcast-hz and per-client queue coalescing.
"""

from web.observer import main

if __name__ == "__main__":
    raise SystemExit(main())

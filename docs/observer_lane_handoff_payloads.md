# Observer lane handoff payload examples

This document captures observer bridge payload examples for downstream QA checks.

## Exposed fields and refresh cadence audit

Current observer surfaces:

- `GET /healthz`: bridge connectivity + throttling + schema health + read-only invariants.
- `GET /ws`: read-only snapshot stream payloads (no command channels).
- Static dashboard (`/`) renders transport/channels/module outputs/deep research/schema health.

Refresh cadence and bounds:

- Bridge receives IPC snapshots with `SnapshotClient(timeout_s=1.0)` and reconnect backoff jitter.
- Websocket broadcast loop fans out **latest snapshot only** at `1 / max_broadcast_hz` seconds.
- Default `max_broadcast_hz` is 20 Hz; floor is 1 Hz.
- Per-client queue is bounded (`client_queue_size`, default 8) with coalescing on overflow.

## Sample `/ws` payload (current contract)

```json
{
  "seq": 42,
  "snapshot": {
    "schema_version": 4,
    "transport": {"tick": 960, "bar": 13, "running": true, "bpm": 120.0},
    "status_text": "playing",
    "channels": [{"channel": 1, "active_notes": [60, 64, 67]}],
    "module_outputs": {"timesig": {"meter": "4/4"}},
    "deep_research": {"produced_at": 1700000123.5, "source_tick": 948, "stale": false, "lag_ms": 33.2}
  },
  "bridge": {
    "connected": true,
    "consecutive_failures": 0,
    "total_failures": 1,
    "successful_reconnects": 1,
    "last_update_age_ms": 28.4
  },
  "metrics": {
    "sequence_gap": 0,
    "last_update_age_ms": 28.4,
    "fanout": {"queue_dropped": 0, "queue_coalesced": 2}
  },
  "deep_research": {
    "available": true,
    "produced_at": 1700000123.5,
    "source_tick": 948,
    "stale": false,
    "lag_ms": 33.2
  },
  "schema_health": {
    "latest_snapshot_version": 4,
    "ipc_freshness_age_ms": 28.4,
    "normalization_fallbacks": 3
  },
  "read_only": {
    "mutation_endpoints": [],
    "command_execution_paths": [],
    "bounded_stream_rate_hz": 20.0
  }
}
```

## Sample `/healthz` payload

```json
{
  "ok": true,
  "seq": 42,
  "has_snapshot": true,
  "bridge": {"connected": true, "last_update_age_ms": 28.4},
  "schema_health": {
    "latest_snapshot_version": 4,
    "ipc_freshness_age_ms": 28.4,
    "normalization_fallbacks": 3
  },
  "max_broadcast_hz": 20.0,
  "client_queue_size": 8,
  "telemetry": {"queue_dropped": 0, "queue_coalesced": 2},
  "read_only": {
    "mutation_endpoints": [],
    "command_execution_paths": [],
    "bounded_polling": {"max_broadcast_hz": 20.0, "client_queue_size": 8}
  }
}
```

## Backward compatibility note

`ui.client.normalize_snapshot()` accepts modern schema payloads and legacy wrapped payloads
(`schema`, `payload.schema`, and `payload` schema envelopes). Legacy normalization paths
increment a process-local fallback counter surfaced in `schema_health.normalization_fallbacks`.

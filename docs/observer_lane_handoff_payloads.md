# Observer lane handoff payload examples

This document captures observer bridge payload examples for downstream QA checks.

## Exposed fields and refresh cadence audit

Current observer surfaces:

- `GET /healthz`: bridge connectivity + throttling + schema health + read-only invariants.
- `GET /ws`: read-only snapshot stream payloads (no command channels).
- Static dashboard (`/`) renders schema/compat state, transport quality, timing summaries, capture status, channels/module outputs, and deep research metadata.

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
    "compat_mode": "native",
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
  "observer_views": {
    "tempo_quality": {"bpm": 120.0, "confidence": 0.95, "jitter_ms": 1.8, "drift_ppm": -0.2},
    "microtiming": {"title": "Microtiming", "total_samples": 128, "buckets": ["early", "on", "late"]},
    "motif": {"found": true, "pattern": "+4 -2", "count": 3, "window": 2},
    "capture_status": {
      "armed": true,
      "state": "capturing",
      "buffer_fill": 24,
      "buffer_capacity": 128,
      "commit_state": "dirty",
      "last_commit": "2026-02-27T12:00:00Z",
      "last_commit_age_s": 2.4
    }
  },
  "read_only": {
    "mode": "strict-read-only",
    "mutation_endpoints": [],
    "command_execution_paths": [],
    "allowed_http_methods": ["GET"],
    "websocket_inbound_actions": ["ping"],
    "websocket_rejected_actions": ["*"],
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
    "mode": "strict-read-only",
    "mutation_endpoints": [],
    "command_execution_paths": [],
    "allowed_http_methods": ["GET"],
    "websocket_inbound_actions": ["ping"],
    "websocket_rejected_actions": ["*"],
    "bounded_polling": {"max_broadcast_hz": 20.0, "client_queue_size": 8}
  }
}
```

## Backward compatibility note

`ui.client.normalize_snapshot()` accepts modern schema payloads and legacy wrapped payloads
(`schema`, `payload.schema`, and `payload` schema envelopes). Legacy normalization paths
increment a process-local fallback counter surfaced in `schema_health.normalization_fallbacks`.


## Operator debugging snippets

Use these snippets during incident triage to verify observer contract stability and strict read-only behavior.

```json
{
  "schema_health": {
    "latest_snapshot_version": 4,
    "normalization_fallbacks": 0
  },
  "snapshot": {
    "compat_mode": "native"
  },
  "observer_views": {
    "tempo_quality": {"jitter_ms": 0.9, "drift_ppm": 0.1},
    "microtiming": {"total_samples": 256},
    "motif": {"found": false, "pattern": "", "count": 0},
    "capture_status": {"buffer_fill": 0, "buffer_capacity": 128, "commit_state": "clean"}
  }
}
```

```json
{
  "error": "read-only observer: mutation methods are disabled",
  "read_only": {
    "mode": "strict-read-only",
    "allowed_http_methods": ["GET"]
  }
}
```

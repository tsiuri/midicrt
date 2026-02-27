# UI Architecture (TTY-first)

This directory introduces a widget tree + renderer split so pages can build
content once and render to different backends.

## Goals

- Keep rendering strictly terminal-safe by default.
- Preserve monochrome CRT behavior (character-cell semantics first).
- Enable incremental migration of existing pages.

## Components

- `ui/model.py`: widget and layout primitives (`Column`, `TextBlock`, `Spacer`).
- `ui/renderers/base.py`: renderer protocol.
- `ui/renderers/text/`: ANSI/Blessed renderer (`TextRenderer`).

## Monochrome CRT constraints

1. Character cells are the primary layout unit.
2. Prefer plain ASCII/Unicode text; no color is required for meaning.
3. Optional emphasis is limited to terminal-safe attributes (`reverse`, `bold`).
4. Block graphics (`█`, `░`) are optional enhancements only.
5. Avoid pixel-addressed assumptions (no font metrics, no sub-cell positioning).
6. Output must degrade cleanly when style sequences are stripped.

## Optional future renderers

Any pixel or framebuffer renderer must be optional (extra dependency), and must
not be imported by default startup paths.

## Incremental migration plan

- Done: Transport page converted to widget-building (`build_widget`) rendered via
  `TextRenderer`.
- Next parity targets: Notes page and Event Log page.
- During migration, the runtime supports both APIs:
  - new: `build_widget(state)`
  - legacy: `draw(state)` fallback

## Snapshot schema (engine -> UI)

Schema version `2` adds an optional top-level `views` map while keeping existing
transport/channel/module fields stable.

Backward-compatibility notes:
- UIs may receive a direct schema snapshot over IPC.
- In-process consumers may still receive an engine state envelope containing
  `snapshot['schema']`; normalize by preferring top-level schema fields and
  falling back to `snapshot['schema']`.
- Legacy/schema-v2 envelopes may place `deep_research` next to `schema`
  rather than inside `schema`; clients should merge it into the normalized
  schema snapshot when present.

### `deep_research` optional payload contract

`deep_research` is optional and may be omitted entirely when the module is
not loaded/disabled. Consumers must treat absence as "unavailable" and avoid
hard failures.

Stable metadata fields for remote tooling:

- `produced_at` (`float`, unix seconds): wall-clock time the result payload was produced.
- `source_tick` (`int`): transport tick associated with the source snapshot.
- `lag_ms` (`float`): production lag in milliseconds (publish-time or module-provided).
- `stale` (`bool`): whether the payload is stale at publish time.

Legacy aliases remain available for compatibility (`timestamp` and
`source_snapshot_*`), but remote tools should prefer the fields above.

### Base payload

```json
{
  "schema_version": 2,
  "timestamp": 1730000000.123,
  "transport": {"tick": 0, "bar": 0, "running": false, "bpm": 0.0},
  "channels": [{"channel": 0, "active_notes": [60]}],
  "active_notes": {"0": [60]},
  "module_outputs": {},
  "status_text": "idle",
  "views": {"8": {"...": "..."}, "pianoroll": {"...": "..."}}
}
```

### `views.pianoroll` (page 8)

Renderer-facing contract for page 8. Both text and pixel renderers must consume
this same payload-to-widget mapping.

Required/standard fields and semantics:

- `time_cols` (`int`): history buffer width represented by `columns` before
  viewport slicing.
- `tick_right` (`int`): absolute transport tick represented by the newest
  (right-most) roll column.
- `active_count` (`int`): count of currently active notes in the source state.
- `pitch_low` (`int`): lowest pitch in the source roll range.
- `pitch_high` (`int`): highest pitch in the source roll range.
- `columns` (`list[list[tuple[int,int,int]]]`): visible roll columns, oldest to
  newest. Each event tuple is `(pitch, channel_1_based, velocity)`.
- `active_notes` (`list[list[int,int,int]]`): compact active-note list encoded
  as `[channel_1_based, pitch, velocity]`.
- `recent_hits` (`list[list[int,int,int,int]]`): recent transient accents
  encoded as `[pitch, channel_1_based, velocity, age_ms]`.
- `overflow_flags` (`object`): hold-state flags with shape
  `{ "above": bool, "below": bool }`.
- `overflow` (`object`): detail object with shape
  `{ "above": [pitch, channel_1_based, ts] | null, "below": [...],
     "above_count": int, "below_count": int }`.

Compatibility path:

- If `views.pianoroll`/`views["8"]` is unavailable, in-process UI code may adapt
  direct `active_notes` state to this exact schema before widget assembly.

### View payload throttling

View payload generation is intentionally throttled in the engine (`view_publish_hz`)
so heavier page data does not regenerate on every MIDI event. The publisher
frequency cap (`publish_hz`) still bounds outgoing snapshot rate.


## Renderer parity matrix

| Page | Widget contract | Text renderer | Pixel renderer | Compositor renderer | Notes |
|---|---|---|---|---|---|
| 1 Notes | `NotesWidget` | ✅ | ✅ | ✅ | Built via adapter capture; reverse-text emphasis may degrade in adapter mode. |
| 3 Transport | `TransportWidget` | ✅ | ✅ | ✅ | Structured fields (`running`, `bpm`, `bar`, `tick`, `time_signature`). |
| 6 Event Log | `EventLogWidget` | ✅ | ✅ | ✅ | Structured title/filter/entries/marker contract. |
| 8 Piano Roll | `PianoRollWidget` | ✅ | ✅ | ✅ | Primary parity target already migrated. |
| Footer/Status | `FooterStatusWidget` | ✅ | ✅ | ✅ | Contract defined; integration in plugin/footer path pending. |
| Remaining pages | `PageLinesWidget` | ✅ | ✅ | ✅ | Page widgets now emit structured lines without legacy draw-capture fallback. |

### Known parity gaps

- ANSI styling emitted directly by legacy pages during draw capture (e.g. reverse text via terminal control writes) is not fully preserved in adapter-based widgets.
- Plugin draw overlays still use terminal-capture integration and are not yet expressed as first-class widget nodes.

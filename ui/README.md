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

Compact payload intended for renderers and remotes:

- `time_cols` (`int`): backing history width used by the roll buffer.
- `pitch_low` (`int`): lowest visible MIDI pitch.
- `pitch_high` (`int`): highest visible MIDI pitch.
- `active_notes` (`list[list[int,int,int]]`): `[channel, pitch, velocity]`.
- `recent_hits` (`list[list[int,int,int,int]]`):
  `[pitch, channel, velocity, age_ms]`.
- `overflow_flags` (`object`): `{ "above": bool, "below": bool }`.

### View payload throttling

View payload generation is intentionally throttled in the engine (`view_publish_hz`)
so heavier page data does not regenerate on every MIDI event. The publisher
frequency cap (`publish_hz`) still bounds outgoing snapshot rate.

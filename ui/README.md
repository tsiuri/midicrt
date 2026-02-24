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

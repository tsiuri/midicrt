# midicrt Performance Reference

Raspberry Pi 3B — ARM Cortex-A53 @ 1.2GHz, LPDDR2 ~4GB/s, `/dev/fb0` 800×475 RGB565.

---

## Hardware limits (hard ceilings)

| Operation | Measured | Notes |
|-----------|----------|-------|
| fb0 write (760 KB) | ~0.5 ms | write-combine cached path |
| fb0 read (760 KB) | ~3 ms | uncached — avoid reading fb0 |
| numpy `buf[:] = scalar_u16` (760 KB) | ~1 ms | scalar broadcast, fast |
| numpy `buf[:] = (r,g,b)` tuple | **~20 ms** | do NOT use on large arrays |
| numpy `astype("<u2")` + bitwise (RGB888→RGB565) | ~16–24 ms | per-frame when we had RGB888 buffer |
| numpy `np.copyto(u16, u16)` (760 KB) | ~0.5 ms | pure memcpy |
| numpy fancy index (LUT gather, 380K pixels) | ~42 ms | ARM gather is slow — avoid |
| `np.asarray(PIL.Image)` | ~6.5 ms | makes a COPY, not a view |

## Frame budget at target FPS

| FPS | Budget | Notes |
|-----|--------|-------|
| 60 | 16.7 ms | tight on Pi 3B — currently unachievable at full render load |
| 30 | 33 ms | comfortable for text-only pages |
| 20 | 50 ms | original target after first opt round |

---

## Render pipeline (compositor path)

Each frame runs:
1. `frame_clear()` — `buf[:] = bg_scalar` — **~1 ms**
2. Header / transport text — ~3–4 ms
3. Page content (`render(widget)` or `page.draw()`) — **3–15 ms** depending on page
4. Plugin overlay — ~1–3 ms (cached at 30 Hz)
5. Notes badge (page 1 only) — cached at 24 Hz, **~1 ms** when cache hits
6. `frame_flush()` — `np.copyto(fb_arr, buf)` — **~0.5 ms**

**Dominant cost is text rendering** (`draw_text_buf16`): each call builds a numpy
boolean mask for the entire text row and does a boolean-index write. For a full
screen of text (~55 rows × 100 chars) this is significant.

### Profiled frame breakdown (pre-optimization baseline, 52ms total at 20fps target)

```
flush      24.8 ms  45%   ← eliminated by native RGB565
page       12.6 ms  23%
badge       5.7 ms  10%   ← pre-computed frames cut to ~1ms
clear       5.0 ms   9%   ← scalar fill cut to ~1ms
header      3.5 ms   6%
plugins     3.6 ms   6%
state       0.5 ms   1%
```

After RGB565 migration + pre-computed badge + clear fix:
```
flush      ~0.5 ms         ← memcpy only
clear      ~1.0 ms         ← scalar uint16 fill
badge      ~1.0 ms         ← cache hit
page       ~10–15 ms       ← still the bottleneck
header      ~3–4 ms
plugins     ~1–3 ms
```

---

## MIDI engine cost (transport running)

At 300 BPM: MIDI clock = `300 * 24 / 60 = **120 messages/sec**` on the ingest thread.
Plus note_on/note_off at high density.

Each `ENGINE.ingest(msg)` call chain:

| Step | Cost | Notes |
|------|------|-------|
| `_normalize_event()` | low | dict + `vars(msg)` |
| `_update_transport()` | **medium** | acquires lock; historically called `_collect_meter_candidates()` every tick |
| `_collect_meter_candidates()` | **high** | iterates ALL modules calling `get_outputs()` — was running 120×/sec |
| `_capture_event()` | low for clock | early-returns on clock/start/stop |
| `_run_modules()` | medium | calls `should_run()` + `on_event()` per module; also `on_clock()` |
| `_route_legacy_event()` | medium | was calling `lambda: dict(PAGES)` on every message |
| `handle_engine_event()` | medium | was calling `get_transport_state()` (lock + dict) on every clock |
| `publisher.wants_publish()` | low | check only |

### Profiled frame breakdown (MIDICRT_PROFILE=1, page 1, idle vs 300BPM transport)

```
Section       Idle    300BPM  Notes
snapshot      4.71ms  2.79ms  get_snapshot() (expensive) + get_transport_state()
page          11.96ms  8.55ms  notes page build_widget + compositor render
plugins       10.91ms  8.79ms  capture_plugin_overlay_widget (was never cache-hitting)
header         5.66ms  4.27ms  page title marquee + draw_text_line
badge          4.88ms  4.66ms  mini roll + spectrum + piano graphic
flush          2.15ms  1.89ms  np.copyto to mmap (higher than theoretical 0.5ms)
footer         1.19ms  1.11ms  footer widget render
page_cache     1.13ms  0.81ms  notes page region copy
total         42.54ms 32.91ms
```

**Key discovery — cache TTL vs actual frame time:**
The `page` and `plugins` caches used a 33ms TTL (30Hz). But actual frame time was
~38ms, meaning the cache expired **every single frame** and never helped. Changed
both to 100ms (10Hz). Plugin text and page content do not visually need faster refresh.

**Key discovery — lock contention jitter:**
At 26fps actual, the profile shows 32ms render but 38ms wall time = ~6ms/frame spent
waiting for `self._lock`. With 120+ ingest calls/sec at 300BPM, the MIDI ingest
thread and UI thread compete for the same lock. This creates frame time *variance*
(jitter), where most frames are fine but occasional frames spike to 50–200ms,
creating the subjective "5fps" feeling even when the average is 26fps.

**Key discovery — `get_snapshot()` called every frame:**
`ENGINE.get_snapshot()` builds a full module state dict (iterating all modules,
building scheduler diagnostics, capturing deep research state) and acquires the lock
twice. Was called every frame at 60fps = 60 times/sec. Cached at 10Hz — the UI
only needs fresh module output/diagnostics at that rate.

### Fixes applied (2026-02-27)

1. **Meter candidate cache** — `_collect_meter_candidates()` now cached for 96 ticks (one 4/4 bar). Refreshes on start/stop. Was running 120+/sec, now runs ~2/sec.
2. **Pages dict cache** — `legacy_page_router` caches `pages_provider()` result. Was creating a fresh `dict(PAGES)` on every MIDI message.
3. **Skip clock in `handle_engine_event`** — clock ticks no longer trigger `get_transport_state()` + diagnostics parsing. The UI loop refreshes transport globals each frame anyway.
4. **Scheduler health throttled** — checked at 2 Hz instead of per-message.

5. **`get_snapshot()` cached at 10Hz** — was called every frame (60/sec). Full module output/diagnostics dict now rebuilt at 10Hz. UI still gets fresh transport state (lightweight lock read) every frame.
6. **Page and plugin caches slowed to 10Hz** — 30Hz cache TTL (33ms) was shorter than actual frame time (~38ms), so caches never hit. 100ms TTL ensures 9 out of 10 frames are cache hits.

### Remaining MIDI-thread hotspots (not yet fixed)

- **`_run_modules()` + `on_clock()`** — still calls every module's `on_clock()` for every clock tick. At 120 clock/sec × N modules this is significant. Could throttle modules that don't need per-tick resolution.
- **`plugin_state_provider()` in `_route_background_ticks`** — calls `plugin_state_dict()` which may do significant work, called for each background page on every clock tick.
- **Lock contention** — ingest thread and UI loop both acquire `self._lock`. At 120 ingest/sec the UI loop may be starved.
- **`get_snapshot()`** — called by publisher; builds a full module state dict. Already gated by `wants_publish()` but worth watching.

---

## Numpy gotchas on ARM Cortex-A53

| Pattern | Result | Why |
|---------|--------|-----|
| `buf[:] = (r, g, b)` | **SLOW ~20ms** | Tuple broadcast allocates intermediates |
| `buf[:] = scalar` | Fast ~1ms | Single value, optimised broadcast |
| `np.copyto(dst, src)` | Fast ~0.5ms | Pure memcpy |
| LUT fancy index `lut[arr]` | **SLOW ~42ms** | ARM gather ops kill cache |
| `astype("<u2")` + bitwise | ~16ms | Multiple temp arrays, but still beats LUT |
| `buf[bool_mask] = val` | OK, ~5–15µs per text row | Depends on mask density |
| `np.asarray(PIL.Image)` | **~6.5ms, makes a COPY** | Not a view — Pillow 12.1.1 |
| PIL `frombuffer` → numpy | **No write-through** | Paste ops don't update backing array |
| `Image.frombuffer` → numpy | Broken | Pillow 12.1.1: paste doesn't write through |

**Key rule:** always work in the native pixel format (RGB565 uint16). Never maintain
an RGB888 buffer and convert — that 24ms/frame cost is fatal at 60fps target.

---

## Text rendering

PSF font (VGA 8×8), vectorised numpy path:

- `draw_text_buf16()` builds `(n_chars, h, w)` bool glyph array → transpose → reshape → single boolean-index write
- ~11.5 µs/char after vectorisation (was ~57 µs/char per-char, ~35 µs/char PIL paste)
- Spaces are free (all-False glyph mask)
- **Cost scales with character count**, not screen area
- Per-frame text budget at 60fps (16.7ms total): ~1450 chars across all draws

### Potential text optimisations (not yet done)

- **Dirty-row caching**: skip re-drawing text rows that haven't changed (compare against a shadow buffer)
- **Pre-render static lines**: header/tab bar changes rarely — cache as a numpy region
- **Reduce text volume**: fewer text rows drawn = directly faster

---

## Architecture / structural optimisations

### Done

- Native RGB565 `(H,W)` uint16 back-buffer (eliminates per-frame RGB conversion)
- Elapsed-aware frame sleep (was sleeping full `1/FPS` on top of render time)
- Pre-computed badge animation frames (48 frames at init)
- Row-vectorised PSF text rendering (5× faster than per-char PIL paste)
- Plugin draw signature cache (avoid `inspect.signature()` per frame)
- `TerminalCapture` removed for `page.draw()` path (save ~5ms)
- `strip_seqs` regex skipped for plain text
- Notes page content cached at 30 Hz
- Plugin overlay cached at 30 Hz

### High-value, not yet done

| Idea | Estimated saving | Complexity |
|------|-----------------|------------|
| Dirty-region tracking (skip clear+redraw of unchanged areas) | 30–50% render time | Medium |
| C extension for text rendering (ctypes/cffi) | 5–10× text throughput | High |
| Move `on_clock()` to a dedicated throttled thread (not ingest thread) | Reduces MIDI thread contention | Medium |
| Pre-render static header/tab bar region | ~3ms/frame | Low |
| Throttle `_route_background_ticks` (not every clock tick) | ~1ms/frame | Low |
| Reduce FPS target dynamically when MIDI load is high | Keeps frame budget intact | Medium |
| Page-specific render cache (piano roll grid is mostly static) | Significant on roll page | Medium |

---

## Profiling how-to

### Quick CPU check
```bash
top -b -n 3 -d 2 -p $(pgrep -f midicrt.py | head -1) | grep python
```

### Add frame timing (temporary — remove before commit)
Insert at the top of the `while not exit_flag:` loop body in `_ui_loop_body()`:
```python
_t = {}; _t0 = time.monotonic()
```
After each section, insert:
```python
_t['section_name'] = time.monotonic() - _t0; _t0 = time.monotonic()
```
At the end of the loop, write to `/tmp/midicrt_perf.txt`:
```python
with open('/tmp/midicrt_perf.txt','w') as f:
    f.write('\n'.join(f'{k:20s}: {v*1000:.2f}ms' for k,v in _t.items()))
```

### Profile the MIDI ingest path
Wrap `ENGINE.ingest()` call with `time.monotonic()` timing, accumulate in a
deque, periodically write median/p95 to `/tmp/midicrt_midi_perf.txt`.

### Python cProfile (offline)
```bash
python -m cProfile -o /tmp/prof.out midicrt.py --profile run_compositor
# then: python -m pstats /tmp/prof.out
```
Note: cProfile overhead is significant on the ingest thread.

---

## Settings that affect performance

`config/settings.json` → `core` section:

| Key | Effect |
|-----|--------|
| `fps` | Target frame rate. 60 is aggressive for Pi 3B. 20–30 is stable. |
| `header_scroll_speed` | Non-zero costs a `time.time()` call + float math per frame |

`config/settings.json` → `pagecycle`:

| Key | Effect |
|-----|--------|
| `cycle_pages` | Including page 9 (audiospectrum) means FFT runs in background |

---

## Key files

| File | Role |
|------|------|
| `fb/compositor.py` | RGB565 back-buffer, `clear/rect/text/flush` primitives |
| `fb/compositor_renderer.py` | Widget rendering, badge, piano roll, page dispatch |
| `fb/psf_font.py` | PSF font loader; `draw_text_buf16()` vectorised text |
| `engine/core.py` | MIDI ingest pipeline; `_update_transport`, `_run_modules` |
| `engine/legacy_page_router.py` | Per-message page/plugin routing |
| `midicrt.py` | UI loop; `_ui_loop_body()` is the frame loop |

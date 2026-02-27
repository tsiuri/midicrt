# Interface Connection Hardening Checklist

Use this checklist before declaring reconnect/autoconnect hardening complete.

## Scope

- Observer bridge reconnect behavior (`web.observer.SnapshotBridge`)
- ALSA `aconnect` parser behavior used by autoconnect routines (`midicrt._parse_aconnect`)
- Runtime operator validation sequence on Pi hardware

## Deterministic local QA (no hardware required)

1. Run deterministic reconnect simulation:

   ```bash
   python scripts/verify_observer_reconnect.py --reconnect-delay-s 0.02
   ```

   Expected signals:
   - first snapshot is published only after simulated reconnect
   - `reconnect_attempts` increments after socket disappearance
   - bridge resumes publishing after socket reappearance

2. Run parser fixture tests:

   ```bash
   pytest -q tests/test_aconnect_parser.py
   ```

   Fixture inputs:
   - `tests/fixtures/aconnect_o_sample.txt`
   - `tests/fixtures/aconnect_l_sample.txt`

3. (Optional broader regression) Run observer bridge unit tests:

   ```bash
   pytest -q tests/test_web_observer_bridge.py
   ```

## Pi hardware validation flow

1. Start/attach runtime session:

   ```bash
   tmux attach -t midicrt
   ```

2. Restart runtime so log state is fresh:

   ```bash
   tmux send-keys -t midicrt:0 q ''
   tmux send-keys -t midicrt:0 '/home/billie/run_midicrt.sh' Enter
   ```

3. Validate observer health endpoint while bridge is steady:

   ```bash
   curl -s http://127.0.0.1:8765/healthz
   ```

4. Simulate input/interface churn (e.g., unplug/replug USB MIDI), then re-check:

   ```bash
   curl -s http://127.0.0.1:8765/healthz
   tmux capture-pane -t midicrt:0 -p
   ```

5. Verify autoconnect parser inputs if needed:

   ```bash
   aconnect -o
   aconnect -l
   ```

## Done criteria

A change is **done** only when all criteria hold:

1. **Reconnect latency bound**
   - Observer bridge reconnects and republishes snapshots within `<= 2.0s` of socket return under default settings.

2. **No duplicate connections**
   - Autoconnect behavior does not create repeated duplicate `aconnect src:port dst:port` links for the same pair.

3. **No infinite retry spam**
   - Failure loops are bounded by configured reconnect delay; logs/health counters advance without unbounded per-frame retry spam.

4. **Correct health status transitions**
   - `/healthz` bridge metadata transitions cleanly across states:
     - connected → reconnecting (error/retry increments) → connected.

5. **Deterministic QA pass**
   - `scripts/verify_observer_reconnect.py` exits zero.
   - `pytest -q tests/test_aconnect_parser.py` passes.


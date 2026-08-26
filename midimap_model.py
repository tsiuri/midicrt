"""midimap_model — pure mapping model for the external-controller translation layer.

A mapping binds an incoming plain-MIDI source (7-bit CC, or 14-bit NRPN) on a
channel to one controller field on one device, so Cirklon / any external
controller can address every parameter of every synth by MIDI alone — no
page needs to be on screen.  Persisted in settings.json section "midimap":

  {"mappings": [
     {"device": "lxp1", "key": "p0", "src": {"type": "cc", "ch": 1, "num": 74}},
     {"device": "matrix1000", "key": "n21", "src": {"type": "nrpn", "ch": 1, "num": 300}}
  ]}

No midicrt / mido imports — usable from the plugin, the web layer, and tests.
"""

SECTION = "midimap"


def src_key(src):
    return (str(src.get("type", "cc")), int(src.get("ch", 1)), int(src.get("num", 0)))


def describe_src(src):
    t, ch, num = src_key(src)
    return f"ch{ch} {'nrpn' if t == 'nrpn' else 'cc'}{num}"


class MapModel:
    def __init__(self, mappings=None):
        self.mappings = [m for m in (mappings or []) if self._valid(m)]
        self._reindex()

    @staticmethod
    def _valid(m):
        return isinstance(m, dict) and "device" in m and "key" in m and isinstance(m.get("src"), dict)

    def _reindex(self):
        self._by_src = {}
        self._by_target = {}
        for m in self.mappings:
            self._by_src.setdefault(src_key(m["src"]), []).append(m)
            self._by_target[(m["device"], m["key"])] = m

    def to_config(self):
        return {"mappings": list(self.mappings)}

    # --- edits -----------------------------------------------------------

    def bind(self, device, key, src):
        """Bind src -> (device,key). A target has one source; a source may fan
        out to several targets (deliberate: one CC can move two synths)."""
        self.unbind(device, key)
        self.mappings.append({"device": device, "key": key,
                              "src": {"type": src_key(src)[0], "ch": src_key(src)[1],
                                      "num": src_key(src)[2]}})
        self._reindex()

    def unbind(self, device, key):
        before = len(self.mappings)
        self.mappings = [m for m in self.mappings if not (m["device"] == device and m["key"] == key)]
        self._reindex()
        return len(self.mappings) != before

    # --- queries ---------------------------------------------------------

    def targets_for(self, src_type, ch, num):
        return list(self._by_src.get((src_type, int(ch), int(num)), []))

    def source_for(self, device, key):
        m = self._by_target.get((device, key))
        return m["src"] if m else None

    def describe(self, device, key):
        s = self.source_for(device, key)
        return describe_src(s) if s else ""


def scale_to_field(value, in_max, fmin, fmax):
    """Map an incoming 0..in_max value linearly onto [fmin, fmax] (ints)."""
    if in_max <= 0:
        return fmin
    frac = max(0, min(in_max, int(value))) / float(in_max)
    return int(round(fmin + frac * (fmax - fmin)))


class NrpnAssembler:
    """Per-channel NRPN state machine: CC99/98 select, CC6 (+ optional CC38)
    deliver data. Feed control changes; get (param, value, bits) when a data
    entry completes. 7-bit if only CC6 arrives; 14-bit once CC38 follows."""

    def __init__(self):
        self.param_msb = {}
        self.param_lsb = {}
        self.data_msb = {}

    def feed(self, ch, cc, val):
        if cc == 99:
            self.param_msb[ch] = val
            return None
        if cc == 98:
            self.param_lsb[ch] = val
            return None
        if ch not in self.param_msb and ch not in self.param_lsb:
            return None
        param = (self.param_msb.get(ch, 0) << 7) | self.param_lsb.get(ch, 0)
        if cc == 6:
            self.data_msb[ch] = val
            return (param, val, 7)
        if cc == 38 and ch in self.data_msb:
            return (param, (self.data_msb[ch] << 7) | val, 14)
        return None

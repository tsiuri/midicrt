# plugins/devicesync.py — mirror hardware-originated MIDI into controller pages
#
# Units whose MIDI OUT is cabled back into the interface (LXP-1 with its jack
# jumpered as OUT, Bass Station Rack, ...) transmit when their front-panel
# controls move.  This plugin hands every incoming message from the main
# monitor input to any page that implements on_device_message(msg), whether
# or not that page is currently displayed, so the GUI shadow tracks the
# hardware.  Pages decide what's theirs (manufacturer id, channel).

import midicrt


def handle(msg):
    for page in list(midicrt.PAGES.values()):
        fn = getattr(page, "on_device_message", None)
        if fn is None:
            continue
        try:
            fn(msg)
        except Exception:
            pass

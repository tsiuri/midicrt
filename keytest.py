from blessed import Terminal
term = Terminal()

print("Press some keys — I'll show key.name and repr(key).  Press ESC twice to quit.")
with term.cbreak():
    while True:
        key = term.inkey(timeout=None)
        print(f"name={key.name!r}, codepoints={list(map(ord, key))}, repr={repr(key)}")
        if key.is_sequence and key.name == "KEY_ESCAPE":
            k2 = term.inkey(timeout=0.5)
            if k2.is_sequence and k2.name == "KEY_ESCAPE":
                break

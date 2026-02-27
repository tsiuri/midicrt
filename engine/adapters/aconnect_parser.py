from __future__ import annotations

import re

_CLIENT_RE = re.compile(r"^client\s+(\d+):\s+'([^']+)'")
_PORT_RE = re.compile(r"^\s+(\d+)\s+'([^']+)'")


def parse_aconnect_output(text: str) -> list[tuple[str, str, str, str]]:
    """Parse `aconnect -l/-o/-i` output into (client_id, client_name, port_id, port_name)."""
    entries: list[tuple[str, str, str, str]] = []
    current: tuple[str, str] | None = None
    for line in (text or "").splitlines():
        m_client = _CLIENT_RE.match(line)
        if m_client:
            current = (m_client.group(1), m_client.group(2))
            continue
        if current is None:
            continue
        m_port = _PORT_RE.match(line)
        if not m_port:
            continue
        entries.append((current[0], current[1], m_port.group(1), m_port.group(2)))
    return entries


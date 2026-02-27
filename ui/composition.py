"""UI composition helpers for transport/header/footer snapshot fields."""

from __future__ import annotations

from ui.model import FooterStatusWidget, TransportWidget


def build_transport_widget(snapshot: dict) -> TransportWidget:
    """Build a transport widget from schema snapshot fields."""
    transport = snapshot.get("transport") if isinstance(snapshot.get("transport"), dict) else {}
    return TransportWidget(
        running=bool(transport.get("running", False)),
        bpm=float(transport.get("bpm", 0.0)),
        bar=int(transport.get("bar", 0)),
        tick=int(transport.get("tick", 0)),
        time_signature=str(transport.get("time_signature", "") or ""),
    )


def build_footer_widget(snapshot: dict) -> FooterStatusWidget:
    """Build footer/status widget from structured snapshot fields."""
    footer = snapshot.get("footer") if isinstance(snapshot.get("footer"), dict) else {}
    left = str(footer.get("left", "") or "")
    right = str(footer.get("right", "") or "")
    if not left:
        left = str(snapshot.get("status_text", "") or "")
    if not right:
        right = str(snapshot.get("fps_status", "") or "")
    return FooterStatusWidget(left=left, right=right)


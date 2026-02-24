"""Optional pixel renderer backend.

This module is intentionally imported only by the run_pixel profile.
"""

from ui.renderers.text import TextRenderer
from blessed import Terminal


class PixelRenderer(TextRenderer):
    """Placeholder pixel renderer with optional dependency checks.

    Today this keeps text rendering semantics while validating that the
    optional pixel runtime stack is installed. Future SDL/KMSDRM output can be
    implemented behind this class without changing startup profile plumbing.
    """

    def __init__(self, renderer_name: str = "sdl2"):
        self.renderer_name = renderer_name
        self._validate_optional_stack(renderer_name)
        super().__init__(Terminal())

    @staticmethod
    def _validate_optional_stack(renderer_name: str):
        name = (renderer_name or "sdl2").lower()
        if name in {"sdl2", "kmsdrm", "framebuffer", "fb"}:
            try:
                import pygame  # noqa: F401
            except Exception as exc:
                raise RuntimeError(
                    "Optional pixel stack unavailable. Install extras: pip install 'midicrt[pixel]'"
                ) from exc

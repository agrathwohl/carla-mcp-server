"""Built-in platform parsers for hosts with stable schemas."""

from earshot.parsers import bandcamp, soundcloud

PARSERS = {
    "bandcamp": bandcamp,
    "soundcloud": soundcloud,
}

__all__ = ["PARSERS", "bandcamp", "soundcloud"]

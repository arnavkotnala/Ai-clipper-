"""ai-clipper: offline-first gate for everything that UPLOADS, POSTS or UPDATES.

The brief forbids automatic uploads/publishing and unnecessary network use. SharkyClips' publishing
code (YouTube API + OAuth, TikTok API/browser, Instagram/Facebook Graph, Telegram, git-pull
updater) stays in the tree as upstream code, but every network entry point calls `require()`
first, and it refuses unless AICLIPPER_PUBLISHING=1.

Nothing in the app sets that variable. Only the test suite does, so the dormant upstream code keeps
its own fake-server tests. Downloading videos (yt-dlp) and chat replays is acquisition, not
publishing, and is not gated here.
"""

from __future__ import annotations

import os

from .errors import SharkyClipsError


def publishing_enabled() -> bool:
    return os.environ.get("AICLIPPER_PUBLISHING") == "1"


def require(what: str) -> None:
    """Raise unless publishing/network features were explicitly enabled (tests only)."""
    if not publishing_enabled():
        raise SharkyClipsError(
            f"{what} is turned off in ai-clipper (offline-first: no uploads, posting or online updates).",
            "Your finished clips are MP4 files in the exports folder: upload them yourself if you want to.")

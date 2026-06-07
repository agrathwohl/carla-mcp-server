"""Standalone web feed server for Earshot listening sessions.

Serves the single-page UI and streams a session's commentary to the browser.
Fully decoupled from the stdio MCP server — it only reads the on-disk session
artifacts (`session.json` for theming + playhead, `feed.jsonl` for the live
commentary the headless worker writes). One-directional: the browser never
controls playback, it only visualizes.

Run:  uv run python -m earshot.runtime.feed_server [--session ID] [--port 8080]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_SESSIONS_DIR = Path(
    os.environ.get("EARSHOT_SESSIONS_DIR",
                   str(Path.home() / ".carla-mcp" / "earshot" / "sessions"))
)


def _resolve_session_dir(sessions_dir: Path, session_id: Optional[str]) -> Optional[Path]:
    if session_id:
        d = sessions_dir / session_id
        return d if (d / "session.json").exists() else None
    candidates = [p.parent for p in sessions_dir.glob("*/session.json")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p / "session.json").stat().st_mtime)


class FeedServer:
    def __init__(self, sessions_dir: Path, session_id: Optional[str]):
        self.sessions_dir = sessions_dir
        self.session_id = session_id

    def _session_dir(self) -> Optional[Path]:
        return _resolve_session_dir(self.sessions_dir, self.session_id)

    async def index(self, request: web.Request) -> web.Response:
        return web.FileResponse(WEB_DIR / "index.html")

    async def static_asset(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        target = (WEB_DIR / name).resolve()
        if WEB_DIR not in target.parents or not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(target)

    async def session(self, request: web.Request) -> web.Response:
        sd = self._session_dir()
        if sd is None:
            return web.json_response(
                {"error": "no active session", "sessions_dir": str(self.sessions_dir)},
                status=404,
            )
        return web.json_response(json.loads((sd / "session.json").read_text("utf-8")))

    async def feed(self, request: web.Request) -> web.StreamResponse:
        sd = self._session_dir()
        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        await resp.prepare(request)
        if sd is None:
            await resp.write(b": no active session\n\n")
            return resp
        jsonl = sd / "feed.jsonl"
        offset = 0
        idle = 0.0
        try:
            while True:
                if jsonl.exists():
                    size = jsonl.stat().st_size
                    if size < offset:
                        offset = 0  # file rotated/truncated
                    if size > offset:
                        with jsonl.open("rb") as fp:
                            fp.seek(offset)
                            chunk = fp.read(size - offset)
                        last_nl = chunk.rfind(b"\n")
                        if last_nl != -1:
                            complete = chunk[: last_nl + 1]
                            offset += len(complete)  # never advance into a partial line
                            for raw in complete.split(b"\n"):
                                raw = raw.strip()
                                if raw:
                                    await resp.write(b"data: " + raw + b"\n\n")
                            idle = 0.0
                idle += 0.5
                if idle >= 15.0:
                    await resp.write(b": keep-alive\n\n")
                    idle = 0.0
                await asyncio.sleep(0.5)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        except Exception:
            logger.debug("feed stream ended for %s", sd.name, exc_info=True)
        return resp


def build_app(sessions_dir: Path, session_id: Optional[str]) -> web.Application:
    server = FeedServer(sessions_dir, session_id)
    app = web.Application()
    app.add_routes([
        web.get("/", server.index),
        web.get("/api/session", server.session),
        web.get("/api/feed", server.feed),
        web.get("/{name:(app\\.js|style\\.css)}", server.static_asset),
    ])
    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Earshot listening-session web feed server")
    ap.add_argument("--session", default=None, help="Session id (default: newest)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIR))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sessions_dir = Path(args.sessions_dir)
    logger.info("earshot feed server: http://%s:%d  (sessions: %s, session=%s)",
                args.host, args.port, sessions_dir, args.session or "newest")
    web.run_app(build_app(sessions_dir, args.session),
                host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()

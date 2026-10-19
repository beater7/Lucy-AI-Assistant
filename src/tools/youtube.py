import json
import os
import subprocess
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import yt_dlp

from config import YOUTUBE_BRIDGE_PORT, YOUTUBE_EXTENSION_ID

YOUTUBE_BRIDGE_LOCK = threading.Lock()
YOUTUBE_BRIDGE_SERVER = None
YOUTUBE_CONTROL_LOCK = threading.Lock()
YOUTUBE_CONTROL_REQUESTS = {}


def complete_youtube_control(request_id, status):
    if status not in {"paused", "playing", "no_youtube_tab", "no_video"}:
        return False
    with YOUTUBE_CONTROL_LOCK:
        request = YOUTUBE_CONTROL_REQUESTS.get(request_id)
        if request is None:
            return False
        request["status"] = status
        request["event"].set()
    return True

class YouTubeBridgeHandler(BaseHTTPRequestHandler):
    def _send_response(self, status, body, content_type):
        encoded_body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded_body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Access-Control-Allow-Origin",
            f"chrome-extension://{YOUTUBE_EXTENSION_ID}",
        )
        self.end_headers()
        self.wfile.write(encoded_body)

    def do_GET(self):
        request = urlsplit(self.path)
        if request.path == "/tab-bridge":
            parameters = parse_qs(request.query)
            target_url = parameters.get("url", [""])[0]
            action = parameters.get("action", ["play"])[0]
            request_id = parameters.get("id", [""])[0]
            if action == "play" and not is_youtube_url(target_url):
                self._send_response(400, "Invalid YouTube URL", "text/plain; charset=utf-8")
                return
            if action not in {"play", "pause", "resume"} or not request_id:
                self._send_response(400, "Invalid YouTube action", "text/plain; charset=utf-8")
                return

            if action == "play":
                fallback_url = json.dumps(target_url).replace("<", "\\u003c")
                page = (
                    "<!doctype html><meta charset=utf-8><title>Opening YouTube</title>"
                    "<p>Lucy is opening YouTube. If it doesn't open automatically, "
                    f"<a href=\"{fallback_url}\">open the video</a>.</p>"
                )
            else:
                page = (
                    "<!doctype html><meta charset=utf-8>"
                    "<title>Controlling YouTube</title>"
                    "<p>Lucy is controlling YouTube playback...</p>"
                )
            self._send_response(200, page, "text/html; charset=utf-8")
            return

        self._send_response(404, "Not found", "text/plain; charset=utf-8")

    def do_OPTIONS(self):
        if self.path != "/complete":
            self._send_response(404, "Not found", "text/plain; charset=utf-8")
            return
        self.send_response(204)
        self.send_header(
            "Access-Control-Allow-Origin",
            f"chrome-extension://{YOUTUBE_EXTENSION_ID}",
        )
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self.path != "/complete":
            self._send_response(404, "Not found", "text/plain; charset=utf-8")
            return
        if self.headers.get("Origin") != f"chrome-extension://{YOUTUBE_EXTENSION_ID}":
            self._send_response(403, "Forbidden", "text/plain; charset=utf-8")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 4096:
                raise ValueError("Invalid completion payload size.")
            completion = json.loads(self.rfile.read(content_length))
        except (ValueError, json.JSONDecodeError):
            self._send_response(400, "Invalid completion payload", "text/plain; charset=utf-8")
            return
        request_id = completion.get("id") if isinstance(completion, dict) else None
        status = completion.get("status") if isinstance(completion, dict) else None
        if not isinstance(request_id, str) or not isinstance(status, str):
            self._send_response(400, "Invalid completion fields", "text/plain; charset=utf-8")
            return

        completed = complete_youtube_control(request_id, status)
        response = "OK" if completed else "Unknown request"
        self._send_response(
            200 if completed else 404,
            response,
            "text/plain; charset=utf-8",
        )

    def log_message(self, format_string, *args):
        return

def is_youtube_url(url):
    parsed_url = urlsplit(url)
    return (
        parsed_url.scheme == "https"
        and parsed_url.hostname is not None
        and (
            parsed_url.hostname == "youtube.com"
            or parsed_url.hostname.endswith(".youtube.com")
        )
        and parsed_url.path in {"", "/", "/watch"}
    )

def start_youtube_bridge():
    global YOUTUBE_BRIDGE_SERVER
    with YOUTUBE_BRIDGE_LOCK:
        if YOUTUBE_BRIDGE_SERVER is None:
            YOUTUBE_BRIDGE_SERVER = ThreadingHTTPServer(
                ("127.0.0.1", YOUTUBE_BRIDGE_PORT),
                YouTubeBridgeHandler,
            )
            bridge_thread = threading.Thread(
                target=YOUTUBE_BRIDGE_SERVER.serve_forever,
                name="LucyYouTubeBridge",
                daemon=True,
            )
            bridge_thread.start()
    return YOUTUBE_BRIDGE_SERVER

def search_youtube(query):
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as youtube:
            result = youtube.extract_info(f"ytsearch5:{query}", download=False)
    except yt_dlp.utils.DownloadError as error:
        raise RuntimeError(f"YouTube search failed: {error}") from error

    results = []
    for entry in (result or {}).get("entries", []):
        if not entry or entry.get("availability") in {
            "private",
            "needs_auth",
            "subscriber_only",
            "premium_only",
        }:
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        results.append(
            {
                "title": entry.get("title") or "Untitled video",
                "url": f"https://www.youtube.com/watch?v={video_id}&autoplay=1",
            }
        )
    return results

def open_youtube_result(result):
    return open_youtube_url(result["url"])

def open_chrome_url(url):
    for environment_variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        install_root = os.environ.get(environment_variable)
        if not install_root:
            continue
        chrome_path = (
            Path(install_root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        )
        if not chrome_path.is_file():
            continue

        try:
            subprocess.Popen(
                [str(chrome_path), url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except OSError as error:
            print(f"Couldn't open Chrome directly: {error}")
            break
        return True

    return webbrowser.open(url)

def open_youtube_url(url):
    if not is_youtube_url(url):
        raise ValueError("Lucy can only open YouTube links with this tab integration.")

    request_id = uuid.uuid4().hex
    try:
        start_youtube_bridge()
        bridge_url = (
            f"http://127.0.0.1:{YOUTUBE_BRIDGE_PORT}/tab-bridge?"
            + urlencode({"action": "play", "id": request_id, "url": url})
        )
    except OSError as error:
        print(f"YouTube tab extension bridge unavailable; opening a new tab: {error}")
        return open_chrome_url(url)

    if not open_chrome_url(bridge_url):
        return open_chrome_url(url)

    return True

def control_youtube_playback(action):
    if action not in {"pause", "resume"}:
        raise ValueError(f"Unsupported YouTube playback action: {action}")

    request_id = uuid.uuid4().hex
    request = {"event": threading.Event(), "status": None}
    with YOUTUBE_CONTROL_LOCK:
        YOUTUBE_CONTROL_REQUESTS[request_id] = request

    try:
        start_youtube_bridge()
        bridge_url = (
            f"http://127.0.0.1:{YOUTUBE_BRIDGE_PORT}/tab-bridge?"
            + urlencode({"action": action, "id": request_id})
        )
        if not open_chrome_url(bridge_url):
            return "I couldn't reach Chrome to control YouTube."

        if not request["event"].wait(timeout=8):
            return (
                "I couldn't confirm YouTube playback control. Make sure the Lucy "
                "Chrome extension is enabled and reloaded."
            )

        status = request["status"]
        if status == "no_youtube_tab":
            return "I couldn't find an open YouTube tab."
        if status == "no_video":
            return "I found YouTube, but there isn't a video ready to control."
        if action == "pause" and status == "paused":
            return "Paused the YouTube video."
        if action == "resume" and status == "playing":
            return "Resumed the YouTube video."
        raise RuntimeError(
            f"YouTube playback control returned an unexpected status: {status}"
        )
    except OSError as error:
        raise RuntimeError(f"Couldn't connect to Lucy's YouTube extension: {error}") from error
    finally:
        with YOUTUBE_CONTROL_LOCK:
            YOUTUBE_CONTROL_REQUESTS.pop(request_id, None)

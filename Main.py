import asyncio
import array
import datetime
import gc
import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlencode, urlsplit
from urllib.request import Request, urlopen

import pyjokes
import edge_tts
import miniaudio
import pyttsx3
import speech_recognition as sr
import yt_dlp
from aiohttp import ClientError
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from lucy_ui import LucyControlPanel


MODEL = "qwen2.5:3b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
SYSTEM_PROMPT_PATH = Path(__file__).with_name("system_prompt.txt")
YOUTUBE_EXTENSION_ID = "oobemglpmdagidpodobnccbokjplkjgk"
YOUTUBE_BRIDGE_PORT = 8765
YOUTUBE_BRIDGE_LOCK = threading.Lock()
YOUTUBE_BRIDGE_SERVER = None
YOUTUBE_CONTROL_LOCK = threading.Lock()
YOUTUBE_CONTROL_REQUESTS = {}
NEURAL_VOICE = "en-US-JennyNeural"
try:
    SYSTEM_INSTRUCTION = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
except OSError as error:
    raise RuntimeError(f"Couldn't read Lucy's system prompt at {SYSTEM_PROMPT_PATH}.") from error
if not SYSTEM_INSTRUCTION:
    raise RuntimeError(f"Lucy's system prompt is empty: {SYSTEM_PROMPT_PATH}.")

SUPPORTED_INTENTS = {
    "chat",
    "unsupported",
    "web_search",
    "google_search",
    "open_google",
    "play_youtube",
    "select_youtube_result",
    "open_youtube",
    "open_facebook",
    "open_messenger",
    "pause_youtube",
    "resume_youtube",
    "browse_folders",
    "open_folder",
    "time",
    "timezone",
    "weather",
    "joke",
    "stop_listening",
    "resume_listening",
    "goodbye",
}
INTERPRETATION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": sorted(SUPPORTED_INTENTS)},
        "query": {"type": "string"},
        "reply": {"type": "string"},
        "when": {"type": "string", "enum": ["today", "tomorrow"]},
    },
    "required": ["intent", "query", "reply", "when"],
    "additionalProperties": False,
}
MAX_HISTORY_MESSAGES = 12
WAKE_RESPONSES = (
    "What's up?",
    "Hey! I'm here. What's going on?",
    "Yeah, I'm listening. What do you need?",
    "Hey, what's on your mind?",
)
GOODBYE_RESPONSES = (
    "Alright, take care! I'll be right here whenever you want to chat again. Bye for now!",
    "Bye for now! Hope the rest of your day goes well. I'll be here when you need me.",
    "Take care! It was nice chatting with you. Talk again soon!",
    "See you later! Have a lovely day, and don't hesitate to call if you need anything.",
)
LAST_RESPONSE_BY_TYPE = {}
USER_FOLDER_NAMES = {
    "desktop": ("Desktop",),
    "documents": ("Documents", "My Documents"),
    "downloads": ("Downloads",),
    "pictures": ("Pictures", "Photos", "Images"),
    "music": ("Music",),
    "videos": ("Videos",),
}
FOLDER_SEARCH_MAX_DEPTH = 5
FOLDER_SEARCH_MAX_DIRECTORIES = 5000

listener = sr.Recognizer()
listener.dynamic_energy_threshold = True
listener.dynamic_energy_ratio = 1.2
listener.pause_threshold = 1.8
MICROPHONE_MUTED = threading.Event()
control_panel = None


class MicrophoneMuted(Exception):
    pass


class InterruptibleMicrophoneStream:
    def __init__(self, stream, on_audio_level=None):
        self.stream = stream
        self.on_audio_level = on_audio_level

    def read(self, size):
        if MICROPHONE_MUTED.is_set():
            raise MicrophoneMuted
        audio = self.stream.read(size)
        if self.on_audio_level is not None and len(audio) >= 2:
            samples = array.array("h")
            samples.frombytes(audio[: len(audio) - len(audio) % 2])
            if samples:
                rms = math.sqrt(
                    sum(sample * sample for sample in samples) / len(samples)
                )
                normalized_level = max(rms - 120, 0) / 2200
                self.on_audio_level(min(normalized_level, 1.0) ** 0.7)
        return audio

    def close(self):
        self.stream.close()


def greet_user():
    talk("Hello, I am Lucy. How can I help you?")


def choose_fresh_response(response_type, responses):
    previous = LAST_RESPONSE_BY_TYPE.get(response_type)
    choices = [response for response in responses if response != previous]
    response = random.choice(choices or list(responses))
    LAST_RESPONSE_BY_TYPE[response_type] = response
    return response


def select_voice_id(voices, preferred_language="en-GB"):
    preferred_language = preferred_language.casefold()
    for voice in voices:
        languages = {
            language.casefold()
            for language in getattr(voice, "languages", [])
            if isinstance(language, str)
        }
        if preferred_language in languages:
            return voice.id

    return next(
        (
            voice.id
            for voice in voices
            if "zira" in f"{voice.name} {voice.id}".casefold()
        ),
        None,
    )


def talk(text):
    print(f"Lucy: {text}")
    if control_panel is not None:
        if control_panel.closing.is_set():
            return
        control_panel.set_status("Speaking")
    try:
        speak_with_neural_voice(text)
    except (
        ClientError,
        edge_tts.exceptions.EdgeTTSException,
        OSError,
        subprocess.SubprocessError,
        TimeoutError,
    ) as error:
        print(f"Neural speech unavailable; using installed Windows voice: {error}")
        speak_with_windows_voice(text)


def find_vlc_executable():
    vlc_from_path = shutil.which("vlc")
    if vlc_from_path:
        return vlc_from_path

    for environment_variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        program_files = os.environ.get(environment_variable)
        if not program_files:
            continue
        candidate = (
            Path(program_files) / "VideoLAN" / "VLC" / "vlc.exe"
        )
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError("VLC is not installed; cannot play Lucy's neural speech.")


async def synthesize_neural_speech(text, audio_path):
    communicate = edge_tts.Communicate(text, voice=NEURAL_VOICE)
    await communicate.save(str(audio_path))


def audio_level_envelope(audio_path, frame_duration=0.03):
    decoded_audio = miniaudio.decode_file(
        str(audio_path),
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=22050,
    )
    samples = decoded_audio.samples
    frame_size = max(1, round(decoded_audio.sample_rate * frame_duration))
    levels = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        if not frame:
            continue
        rms = math.sqrt(sum(sample * sample for sample in frame) / len(frame))
        levels.append(min(rms / 7000, 1.0))
    return levels


def play_audio_with_levels(vlc_executable, audio_path):
    try:
        levels = audio_level_envelope(audio_path)
    except miniaudio.DecodeError as error:
        print(f"Couldn't decode Lucy's speech for the waveform: {error}")
        levels = []
    process = subprocess.Popen(
        [
            vlc_executable,
            "--intf",
            "dummy",
            "--play-and-exit",
            "--no-video",
            "--quiet",
            str(audio_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    playback_started = time.monotonic() + 0.08
    try:
        while process.poll() is None:
            elapsed = time.monotonic() - playback_started
            frame_index = int(max(elapsed, 0) / 0.03)
            if control_panel is not None:
                level = 0.35 if not levels else 0
                if 0 <= frame_index < len(levels):
                    level = levels[frame_index]
                control_panel.set_speech_level(level)
            if elapsed > 120:
                process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    process.args, 120, output=stdout, stderr=stderr
                )
            time.sleep(0.02)

        stdout, stderr = process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode,
                process.args,
                output=stdout,
                stderr=stderr,
            )
    finally:
        if control_panel is not None:
            control_panel.set_speech_level(0)


def speak_with_neural_voice(text):
    vlc_executable = find_vlc_executable()
    with tempfile.TemporaryDirectory(prefix="lucy-speech-") as temporary_directory:
        audio_path = Path(temporary_directory) / "response.mp3"
        asyncio.run(synthesize_neural_speech(text, audio_path))
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise edge_tts.exceptions.NoAudioReceived(
                f"Neural speech service returned no audio for voice {NEURAL_VOICE}."
            )

        play_audio_with_levels(vlc_executable, audio_path)


def speak_with_windows_voice(text):
    engine = pyttsx3.init("sapi5")
    speech_timer = None
    speech_timer_lock = threading.Lock()

    def finish_word():
        if control_panel is not None:
            control_panel.set_speech_level(0)

    def on_word_started(_name, _location, length):
        nonlocal speech_timer
        if control_panel is None:
            return
        control_panel.set_speech_level(0.65)
        with speech_timer_lock:
            if speech_timer is not None:
                speech_timer.cancel()
            speech_timer = threading.Timer(
                min(max(length / 24, 0.08), 0.4),
                finish_word,
            )
            speech_timer.daemon = True
            speech_timer.start()

    def on_utterance_finished(_name, _completed):
        nonlocal speech_timer
        with speech_timer_lock:
            if speech_timer is not None:
                speech_timer.cancel()
                speech_timer = None
        finish_word()

    try:
        engine.setProperty("rate", 200)
        voices = engine.getProperty("voices")
        voice_id = select_voice_id(voices)
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.connect("started-word", on_word_started)
        engine.connect("finished-utterance", on_utterance_finished)
        engine.say(text)
        engine.runAndWait()
    except RuntimeError as error:
        print(f"Text-to-speech error: {error}")
    finally:
        on_utterance_finished("", False)
        engine.stop()
        del engine
        gc.collect()


def calibrate_microphone():
    try:
        with sr.Microphone() as source:
            source.stream = InterruptibleMicrophoneStream(source.stream)
            print("Calibrating the microphone. Please stay quiet for a moment...")
            listener.adjust_for_ambient_noise(source, duration=1)
        return True
    except MicrophoneMuted:
        return True
    except (OSError, AttributeError) as error:
        print(f"Microphone error: {error}")
        talk("I can't access the microphone. Check its connection and permissions.")
        return False


def take_command():
    if MICROPHONE_MUTED.is_set():
        return ""
    try:
        with sr.Microphone() as source:
            source.stream = InterruptibleMicrophoneStream(
                source.stream,
                on_audio_level=(
                    control_panel.set_audio_level
                    if control_panel is not None
                    else None
                ),
            )
            print("Listening...")
            voice = listener.listen(source, timeout=5)
        if MICROPHONE_MUTED.is_set():
            return ""
        command = listener.recognize_google(voice).lower().strip()
        print(f"You: {command}")
        return command
    except MicrophoneMuted:
        return ""
    except sr.WaitTimeoutError:
        return ""
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as error:
        print(f"Speech recognition service error: {error}")
        talk("I can't reach speech recognition right now.")
    except (OSError, AttributeError) as error:
        print(f"Microphone error: {error}")
        talk("I can't access the microphone. Check your microphone connection and permissions.")
    return ""


def user_folder_roots():
    home = Path.home()
    roots = []
    seen_paths = set()
    for names in USER_FOLDER_NAMES.values():
        for name in names:
            path = home / name
            if path.is_dir():
                normalized_path = os.path.normcase(os.path.abspath(path))
                if normalized_path not in seen_paths:
                    roots.append(path)
                    seen_paths.add(normalized_path)
                break
    return roots


def normalize_folder_query(query):
    normalized = re.sub(r"\s+", " ", query).strip().casefold()
    normalized = re.sub(r"^(?:my|the)\s+", "", normalized)
    normalized = re.sub(r"\s+(?:folder|directory)$", "", normalized)
    return normalized.strip()


def find_user_folders(query):
    folder_name = normalize_folder_query(query)
    if not folder_name:
        raise ValueError("Tell me which folder you're looking for.")

    parent_match = re.fullmatch(
        r"(.+?)\s+(?:in|inside|under|within)\s+(.+)",
        folder_name,
    )
    if parent_match:
        folder_name, parent_query = parent_match.groups()
        parent_matches, parent_search_limit_reached = find_user_folders(
            parent_query
        )
        if len(parent_matches) != 1:
            format_folder_matches(
                parent_query,
                parent_matches,
                parent_search_limit_reached,
            )
            raise ValueError(
                f"I couldn't locate the parent folder {parent_query}."
            )
        roots = parent_matches
    else:
        roots = user_folder_roots()

    for canonical_name, aliases in USER_FOLDER_NAMES.items():
        if folder_name in {alias.casefold() for alias in aliases}:
            folder_name = canonical_name
            break

    if folder_name in USER_FOLDER_NAMES and not parent_match:
        matching_roots = [
            root
            for root in roots
            if root.name.casefold() in
            {name.casefold() for name in USER_FOLDER_NAMES[folder_name]}
        ]
        return matching_roots, False

    matches = []
    directories_examined = 0
    search_limit_reached = False
    for root in roots:
        pending = [(root, 0)]
        while pending:
            parent, depth = pending.pop()
            if depth >= FOLDER_SEARCH_MAX_DEPTH:
                continue
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        try:
                            attributes = entry.stat(follow_symlinks=False).st_file_attributes
                        except AttributeError:
                            attributes = 0
                        except OSError as error:
                            print(f"Couldn't inspect folder entry {entry.name}: {error}")
                            continue
                        if entry.is_symlink() or attributes & 0x400:
                            continue

                        directories_examined += 1
                        folder_path = Path(entry.path)
                        if entry.name.casefold() == folder_name:
                            matches.append(folder_path)
                        if directories_examined >= FOLDER_SEARCH_MAX_DIRECTORIES:
                            search_limit_reached = True
                            break
                        pending.append((folder_path, depth + 1))
            except OSError as error:
                print(f"Couldn't search folders in {parent}: {error}")
                continue
            if search_limit_reached:
                break
        if search_limit_reached:
            break
    return matches, search_limit_reached


def format_folder_matches(query, matches, search_limit_reached):
    if not matches:
        detail = " I reached the folder search limit." if search_limit_reached else ""
        raise ValueError(f"I couldn't find a folder named {query}.{detail}")
    if len(matches) > 1:
        paths = "; ".join(str(path) for path in matches[:5])
        extra = f" I found {len(matches)} matches: {paths}."
        if len(matches) > 5:
            extra += " There are more matches."
        raise ValueError(
            f"I found more than one folder named {query}.{extra} "
            "Please say its parent folder, such as 'Projects in Documents'."
        )
    if search_limit_reached:
        print("Folder search reached its directory limit.")
    return matches[0]


def browse_user_folders(query):
    if not normalize_folder_query(query):
        roots = user_folder_roots()
        if not roots:
            return "I couldn't find any of your standard folders."
        names = ", ".join(root.name for root in roots)
        return f"Your available folders are: {names}. Which one would you like to browse?"

    folder_path = format_folder_matches(
        query,
        *find_user_folders(query),
    )
    subfolders = []
    try:
        with os.scandir(folder_path) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    attributes = getattr(
                        entry.stat(follow_symlinks=False),
                        "st_file_attributes",
                        0,
                    )
                except OSError as error:
                    print(f"Couldn't inspect folder entry {entry.name}: {error}")
                    continue
                if not entry.is_symlink() and not attributes & 0x400:
                    subfolders.append(entry.name)
        subfolders.sort(key=str.casefold)
    except OSError as error:
        raise RuntimeError(f"I couldn't list folders in {folder_path}: {error}") from error

    if not subfolders:
        return f"There are no subfolders in {folder_path.name}."
    listed_names = ", ".join(subfolders[:20])
    if len(subfolders) > 20:
        listed_names += f", and {len(subfolders) - 20} more"
    return f"Folders in {folder_path.name}: {listed_names}."


def open_user_folder(query):
    folder_path = format_folder_matches(
        query,
        *find_user_folders(query),
    )
    try:
        os.startfile(str(folder_path))
    except OSError as error:
        raise RuntimeError(f"I couldn't open {folder_path}: {error}") from error
    return f"Opened {folder_path.name}."


def is_thank_you_only(command):
    if re.search(r"\b(?:goodbye|bye|exit|quit)\b", command, re.IGNORECASE):
        return False
    if not re.search(r"\b(?:thank\s+you|thanks)\b", command, re.IGNORECASE):
        return False

    remaining = re.sub(
        r"\b(?:thank\s+you|thanks|okay|ok|it'?s|it\s+is|playing|so\s+much|a\s+lot)\b",
        "",
        command,
        flags=re.IGNORECASE,
    )
    return not re.sub(r"[\s,!.?]+", "", remaining)


def interpret_request(history, command, youtube_results):
    if is_thank_you_only(command):
        recent_song = any(
            message["role"] == "assistant"
            and " on YouTube." in message["content"]
            and message["content"].startswith("Playing ")
            for message in reversed(history)
        )
        reply = "You're welcome! Enjoy the song." if recent_song else "You're welcome!"
        return {"intent": "chat", "query": "", "reply": reply, "when": "today"}

    normalized_command = command.casefold().strip(" \t?.!")
    search_command = re.sub(
        r"^(?:no[, ]+)?i\s+mean[, ]+",
        "",
        normalized_command,
    )
    youtube_pause_command = re.fullmatch(
        r"(?:please\s+)?(?:pause|stop)\s+"
        r"(?:(?:the|this)\s+)?(?:youtube(?:\s+video)?|video|song|music)(?:\s+please)?",
        search_command,
    )
    if youtube_pause_command:
        return {
            "intent": "pause_youtube",
            "query": "",
            "reply": "",
            "when": "today",
        }

    youtube_resume_command = re.fullmatch(
        r"(?:please\s+)?(?:resume|unpause|continue|play)\s+"
        r"(?:(?:the|this)\s+)?(?:youtube(?:\s+video)?|video|song|music)"
        r"(?:\s+please)?",
        search_command,
    )
    if youtube_resume_command:
        return {
            "intent": "resume_youtube",
            "query": "",
            "reply": "",
            "when": "today",
        }

    if re.fullmatch(
        r"(?:can|could|would)\s+(?:you|lucy)\s+(?:please\s+)?"
        r"(?:search(?:\s+for\s+me)?|google|look\s+up)"
        r"(?:\s+(?:in|on)\s+google)?",
        search_command,
    ):
        if "lucy" in search_command:
            reply = (
                "Yes. I can search on your behalf. Tell me what you want to find; "
                "I can summarize web results aloud or open Google results if you ask for Google."
            )
        elif "google" in search_command:
            reply = "Sure. What would you like me to search for on Google?"
        else:
            reply = "Sure. What would you like me to search for?"
        return {"intent": "chat", "query": "", "reply": reply, "when": "today"}

    if re.fullmatch(
        r"(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
        r"(?:search(?:\s+google)?|google|look\s+up)(?:\s+for\s+me)?"
        r"(?:\s+(?:in|on)\s+google)?",
        search_command,
    ):
        reply = (
            "Sure. What would you like me to search for on Google?"
            if "google" in search_command
            else "Sure. What would you like me to search for?"
        )
        return {"intent": "chat", "query": "", "reply": reply, "when": "today"}

    google_search_patterns = (
        r"(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
        r"(?:search|look\s+up)\s+(?:in\s+)?google\s+(?:for\s+)?(.+)",
        r"(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
        r"(?:search|look\s+up)\s+for\s+(.+?)\s+(?:in|on)\s+google",
        r"(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
        r"google\s+(?:search\s+)?for\s+(.+)",
    )
    for pattern in google_search_patterns:
        match = re.fullmatch(pattern, search_command)
        if match and match.group(1).strip():
            return {
                "intent": "google_search",
                "query": match.group(1).strip(),
                "reply": "",
                "when": "today",
            }

    if re.fullmatch(
        r"(?:(?:can|could)\s+you\s+)?(?:please\s+)?"
        r"(?:open|go\s+to|launch)\s+google(?:\s+(?:homepage|home\s+page|website))?",
        search_command,
    ):
        return {
            "intent": "open_google",
            "query": "",
            "reply": "",
            "when": "today",
        }

    messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    if youtube_results:
        available = "\n".join(
            f"{index}. {result['title']}"
            for index, result in enumerate(youtube_results, start=1)
        )
        messages.append(
            {
                "role": "system",
                "content": f"These YouTube results are currently available:\n{available}",
            }
        )
    else:
        messages.append(
            {
                "role": "system",
                "content": (
                    "There are no cached YouTube results. A request to play a named "
                    "song or video starts a new YouTube search, not result selection."
                ),
            }
        )
    messages.extend(history)
    messages.append({"role": "user", "content": command})
    for attempt in range(2):
        request = Request(
            OLLAMA_CHAT_URL,
            data=json.dumps(
                {
                    "model": MODEL,
                    "messages": messages,
                    "stream": False,
                    "format": INTERPRETATION_SCHEMA,
                    "options": {"num_predict": 220, "temperature": 0.35},
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Ollama returned HTTP {error.code}: {details}") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError(
                "Couldn't connect to Ollama. Make sure the Ollama app is running."
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Ollama returned an invalid response.") from error

        content = result.get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("The local AI returned an empty response.")
        try:
            interpretation = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError("The local AI returned invalid request data.") from error

        if not isinstance(interpretation, dict):
            raise ValueError("The local AI response must be a JSON object.")
        intent = interpretation.get("intent")
        query = interpretation.get("query", "")
        reply = interpretation.get("reply", "")
        when = interpretation.get("when", "today")
        if query is None:
            query = ""
        if reply is None:
            reply = ""
        if when is None:
            when = "today"

        invalid_fields = [
            name
            for name, value, expected_type in (
                ("intent", intent, str),
                ("query", query, str),
                ("reply", reply, str),
                ("when", when, str),
            )
            if not isinstance(value, expected_type)
        ]
        if not invalid_fields:
            break
        if attempt == 1:
            raise ValueError(
                "The local AI returned invalid request fields: "
                + ", ".join(invalid_fields)
                + "."
            )

        print(
            "Local AI returned invalid request fields ("
            + ", ".join(invalid_fields)
            + "); retrying once."
        )
        messages.insert(
            -1,
            {
                "role": "system",
                "content": (
                    "Your previous output had fields of the wrong type. Return exactly "
                    "one JSON object matching the required schema. Every field must be "
                    "present and a string. For normal conversation, use intent='chat', "
                    "an empty query, and a natural reply."
                ),
            },
        )

    if intent not in SUPPORTED_INTENTS:
        if query.strip() or not reply.strip():
            raise ValueError("The local AI returned an unsupported request type.")
        intent = "chat"
    if intent == "open_youtube" and query.strip():
        intent = "play_youtube"
    if intent == "select_youtube_result" and not youtube_results:
        requested_video = re.search(r"\bplay\s+(?:me\s+)?(.+)$", command)
        if requested_video:
            candidate = requested_video.group(1).strip(" \t.,!?;:")
            if not re.fullmatch(
                r"(?:the\s+)?(?:first|second|third|fourth|fifth|\d+(?:st|nd|rd|th)?)(?:\s+result)?",
                candidate,
            ):
                intent = "play_youtube"
                query = candidate
    if intent == "weather" and when not in {"today", "tomorrow"}:
        raise ValueError("The local AI returned an invalid forecast date.")
    if intent == "select_youtube_result" and not query.strip().isdigit():
        raise ValueError("The local AI returned an invalid YouTube result number.")
    if intent == "chat" and not reply.strip():
        raise ValueError("The local AI returned an empty chat reply.")
    if intent == "web_search" and not query.strip():
        raise ValueError("The local AI didn't identify what to search for.")
    if intent == "google_search" and not query.strip():
        raise ValueError("The local AI didn't identify what to search for on Google.")
    if intent == "play_youtube" and not query.strip():
        raise ValueError("The local AI didn't identify a video to search for.")

    return {
        "intent": intent,
        "query": query.strip(),
        "reply": reply.strip(),
        "when": when.strip().lower(),
    }


def get_json(url):
    request = Request(url, headers={"User-Agent": "Lucy Voice Assistant"})
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Weather service returned HTTP {error.code}: {details}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("Couldn't connect to the weather service.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("The weather service returned invalid data.") from error
    return result


def known_philippines_locations(text):
    locations = []
    if re.search(r"\bmakati\b", text, re.IGNORECASE):
        locations.append("Makati City, Philippines")
    if re.search(
        r"\b(?:bgc|bonifacio\s+global\s+city|taguig)\b",
        text,
        re.IGNORECASE,
    ):
        locations.append("Taguig, Philippines")
    return locations


def lookup_location(location):
    if re.search(r"\bto(?:\s+[a-z])?\s*$", location, re.IGNORECASE):
        raise ValueError(f"I need the complete location name, not {location}.")

    aliases = known_philippines_locations(location)
    if len(aliases) == 1:
        location = aliases[0]

    parts = [part.strip() for part in location.split(",", maxsplit=1)]
    requested_name = re.sub(r"[^a-z0-9 ]", "", parts[0].lower()).split()
    requested_name = {
        token for token in requested_name if token not in {"city", "town", "the", "of"}
    }
    requested_country = parts[1].casefold() if len(parts) == 2 else None
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote_plus(location)}&count=10&language=en&format=json"
    )
    results = get_json(url).get("results", [])
    if not results:
        raise ValueError(f"I couldn't find a location called {location}.")

    matching_places = []
    for place in results:
        place_name = re.sub(
            r"[^a-z0-9 ]", "", place.get("name", "").lower()
        ).split()
        place_name = {
            token for token in place_name if token not in {"city", "town", "the", "of"}
        }
        country_matches = (
            not requested_country
            or place.get("country", "").casefold() == requested_country
        )
        if requested_name and requested_name.issubset(place_name) and country_matches:
            matching_places.append(place)

    if not matching_places:
        raise ValueError(f"I couldn't confidently match the location {location}.")
    place = matching_places[0]
    if place.get("feature_code") == "PCLI":
        raise ValueError(f"Which city in {location} should I look up?")
    return place


def describe_timezone(location=None):
    if location:
        place = lookup_location(location)
        zone = place.get("timezone")
        place_name = ", ".join(
            part for part in (place.get("name"), place.get("country")) if part
        )
        if not zone:
            raise ValueError(f"I couldn't find a time zone for {location}.")
        return f"{place_name} is in the {zone} time zone."

    local_time = datetime.datetime.now().astimezone()
    zone = local_time.tzname() or str(local_time.tzinfo)
    offset = local_time.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"Your computer's local time zone is {zone} (UTC{offset})."


def describe_time(location=None):
    if not location:
        return f"The current time is {datetime.datetime.now():%I:%M %p}."

    place = lookup_location(location)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={place['latitude']}&longitude={place['longitude']}"
        "&current=temperature_2m&timezone=auto&forecast_days=1"
    )
    result = get_json(url)
    current = result.get("current", {})
    time_text = current.get("time")
    if not time_text:
        raise ValueError("The time service didn't return the current local time.")
    local_time = datetime.datetime.fromisoformat(time_text)
    place_name = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    zone = result.get("timezone")
    zone_text = f" in {zone}" if zone else ""
    return f"It's {local_time:%I:%M %p} in {place_name}{zone_text}."


def describe_weather(location, when):
    if not location:
        return None
    place = lookup_location(location)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={place['latitude']}&longitude={place['longitude']}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min"
        "&temperature_unit=celsius&timezone=auto&forecast_days=3"
    )
    forecast = get_json(url)
    daily = forecast.get("daily", {})
    dates = daily.get("time", [])
    index = 1 if when == "tomorrow" else 0
    if len(dates) <= index:
        raise ValueError("The weather service didn't return that forecast.")

    try:
        code = daily["weather_code"][index]
        high = daily["temperature_2m_max"][index]
        low = daily["temperature_2m_min"][index]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("The weather service didn't return a complete forecast.") from error
    conditions = {
        0: "clear",
        1: "mostly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "foggy",
        48: "foggy",
        51: "light drizzle",
        53: "drizzle",
        55: "heavy drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        80: "rain showers",
        81: "rain showers",
        82: "heavy rain showers",
        95: "thunderstorms",
        96: "thunderstorms with hail",
        99: "thunderstorms with hail",
    }.get(code, "changeable")
    day_name = "tomorrow" if index else "today"
    place_name = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    return (
        f"{day_name.capitalize()} in {place_name}, expect {conditions}, "
        f"with a high of {high:.0f} and a low of {low:.0f} degrees Celsius."
    )


def summarize_search_results(query, results):
    search_context = "\n".join(
        f"[{index}] {result.get('title', 'Untitled')}\n"
        f"URL: {result.get('href', '')}\n"
        f"Snippet: {result.get('body', '')}"
        for index, result in enumerate(results, start=1)
    )
    request = Request(
        OLLAMA_CHAT_URL,
        data=json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Answer the user's question using only the supplied search "
                            "results. Give a concise, natural answer that sounds good "
                            "when spoken aloud. Say when the results don't establish an "
                            "answer. Never include numbered citations, bracketed source "
                            "tags, markdown, or references like [1] or [2]. When a "
                            "specific useful next step fits the topic, end with one "
                            "short offer that Lucy can fulfill, such as explaining one "
                            "part in more detail. Do not add a generic follow-up question."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Question: {query}\n\nSearch results:\n{search_context}",
                    },
                ],
                "stream": False,
                "options": {"num_predict": 220, "temperature": 0.2},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Ollama returned HTTP {error.code}: {details}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(
            "Couldn't connect to Ollama to summarize web results."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid response.") from error

    answer = result.get("message", {}).get("content", "")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("The local model returned an empty search summary.")
    answer = re.sub(r"\[\s*\d+\s*\]", "", answer)
    answer = re.sub(r"\s+([,.;!?])", r"\1", answer)
    answer = re.sub(r"[ \t]{2,}", " ", answer).strip()
    if not answer:
        raise ValueError("The local model returned an empty search summary.")
    sources = [
        {"title": item.get("title", "Untitled"), "url": item.get("href", "")}
        for item in results
        if item.get("href")
    ]
    return answer.strip(), sources


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
        except (ValueError, json.JSONDecodeError) as error:
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


def search_web(query):
    try:
        with DDGS(timeout=15) as search_client:
            results = search_client.text(query, region="wt-wt", max_results=5)
    except DDGSException as error:
        raise RuntimeError(f"DuckDuckGo search failed: {error}") from error
    if not results:
        raise ValueError("The web search didn't return any results.")

    answer, sources = summarize_search_results(query, results)
    return answer, sources, "DuckDuckGo"


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


def handle_local_command(command):
    if command in {"time", "what time is it", "tell me the time", "what's the time"}:
        current_time = datetime.datetime.now().strftime("%I:%M %p")
        talk(f"The current time is {current_time}")
        return True

    if command in {"joke", "tell me a joke", "say a joke"}:
        talk(pyjokes.get_joke())
        return True

    return False


def perform_intent(interpretation, history, command, youtube_results):
    intent = interpretation["intent"]
    query = interpretation["query"]
    reply = interpretation["reply"]

    if intent == "chat":
        response = reply
    elif intent == "web_search":
        response, sources, provider = search_web(query)
        print(f"Search sources ({provider}):")
        for source in sources:
            print(f"- {source['title']}: {source['url']}")
    elif intent == "google_search":
        google_url = "https://www.google.com/search?q=" + quote_plus(query)
        if open_chrome_url(google_url):
            response = f"I've opened Google results for {query}."
        else:
            response = "I couldn't open Google. Please check your browser settings."
    elif intent == "open_google":
        if open_chrome_url("https://www.google.com/"):
            response = "Opened Google."
        else:
            response = "I couldn't open Google. Please check your browser settings."
    elif intent == "play_youtube":
        try:
            results = search_youtube(query)
        except RuntimeError as error:
            print(f"YouTube search error: {error}")
            results = []
            response = "I couldn't search YouTube just now. Please try again in a moment."
        else:
            youtube_results[:] = results
            if not results:
                response = f"I couldn't find a playable YouTube video for {query}."
            elif open_youtube_result(results[0]):
                response = f"Playing {results[0]['title']} on YouTube."
            else:
                response = "I found the video, but couldn't open your browser."
    elif intent == "select_youtube_result":
        result_number = int(query)
        if result_number < 1 or result_number > len(youtube_results):
            response = "I don't have that result available. Ask me to search for the video again."
        elif open_youtube_result(youtube_results[result_number - 1]):
            response = f"Playing {youtube_results[result_number - 1]['title']} on YouTube."
        else:
            response = "I found the video, but couldn't open your browser."
    elif intent == "open_youtube":
        if open_youtube_url("https://www.youtube.com/"):
            response = "Opened YouTube."
        else:
            response = "I couldn't open YouTube. Please check your browser settings."
    elif intent == "open_facebook":
        if webbrowser.open("https://www.facebook.com/"):
            response = "Opened Facebook in your browser."
        else:
            response = "I couldn't open Facebook. Please check your browser settings."
    elif intent == "open_messenger":
        if webbrowser.open("https://www.messenger.com/"):
            response = "Opened Messenger in your browser."
        else:
            response = "I couldn't open Messenger. Please check your browser settings."
    elif intent == "pause_youtube":
        response = control_youtube_playback("pause")
    elif intent == "resume_youtube":
        response = control_youtube_playback("resume")
    elif intent == "browse_folders":
        response = browse_user_folders(query)
    elif intent == "open_folder":
        response = open_user_folder(query)
    elif intent == "time":
        response = describe_time(query or None)
    elif intent == "timezone":
        response = describe_timezone(query or None)
    elif intent == "weather":
        location_text = f"{query} {command}"
        if re.search(r"\bto(?:\s+[a-z])?\s*$", location_text, re.IGNORECASE):
            response = (
                "I only caught the start of that location. Which city or area "
                f"should I check the forecast for {interpretation['when']}?"
            )
        else:
            locations = known_philippines_locations(location_text)
            if not locations:
                locations = [query]
            forecasts = [
                describe_weather(location, interpretation["when"])
                for location in locations
            ]
            forecasts = [forecast for forecast in forecasts if forecast]
            if forecasts:
                response = " ".join(forecasts)
            else:
                response = (
                    f"Which city should I check the forecast for {interpretation['when']}?"
                )
    elif intent == "joke":
        response = pyjokes.get_joke()
    elif intent == "stop_listening":
        response = "I'll pause here. Say 'Hey Lucy' when you want me back."
    elif intent == "resume_listening":
        response = "I'm here. What can I do for you?"
    elif intent == "goodbye":
        response = choose_fresh_response("goodbye", GOODBYE_RESPONSES)
    else:
        response = (
            "I can't do that directly yet. I can chat, tell you the time or a joke, "
            "check a city forecast, search the web, and play videos on YouTube."
        )

    history.append({"role": "user", "content": command})
    history.append({"role": "assistant", "content": response})
    while len(history) > MAX_HISTORY_MESSAGES:
        history.popleft()
    talk(response)
    return intent


def run_assistant(control_panel):
    if not calibrate_microphone():
        control_panel.set_status("Microphone unavailable")
        return

    greet_user()
    history = deque()
    youtube_results = []
    listening = True

    while not control_panel.closing.is_set():
        if MICROPHONE_MUTED.is_set():
            control_panel.set_status("Muted")
            time.sleep(0.1)
            continue

        control_panel.set_status("Listening" if listening else "Wake word only")
        command = take_command()
        if not command or control_panel.closing.is_set() or MICROPHONE_MUTED.is_set():
            continue

        wake_match = re.search(
            r"\b(?:hey|hello)\s+luc(?:y)?\b"
            r"|\b(?:hey|hello)\s+siri\b"
            r"|^\s*(?:lucy|siri)\b",
            command,
            re.IGNORECASE,
        )
        if wake_match:
            listening = True
            command = (
                command[: wake_match.start()]
                + command[wake_match.end() :]
            ).strip(" \t,.:;!?")
            if not command:
                talk(choose_fresh_response("wake", WAKE_RESPONSES))
                continue

        if not listening:
            continue

        if handle_local_command(command):
            continue

        control_panel.set_status("Thinking")
        try:
            interpretation = interpret_request(history, command, youtube_results)
        except RuntimeError as error:
            print(f"Local AI error: {error}")
            talk("I couldn't reach my local AI. Check that Ollama is running.")
        except ValueError as error:
            print(f"Local AI response error: {error}")
            talk("I didn't get a usable reply. Please try asking another way.")
        else:
            if control_panel.closing.is_set() or MICROPHONE_MUTED.is_set():
                continue
            try:
                intent = perform_intent(
                    interpretation,
                    history,
                    command,
                    youtube_results,
                )
            except (RuntimeError, ValueError) as error:
                print(f"Request error: {error}")
                talk(str(error))
                continue
            if intent == "stop_listening":
                listening = False
            elif intent == "resume_listening":
                listening = True
            elif intent == "goodbye":
                control_panel.updates.put(("close", None))
                return


def run_lucy():
    global control_panel
    control_panel = LucyControlPanel(MICROPHONE_MUTED)
    worker = threading.Thread(
        target=run_assistant,
        args=(control_panel,),
        name="Lucy assistant",
        daemon=True,
    )
    worker.start()
    control_panel.run()


if __name__ == "__main__":
    run_lucy()

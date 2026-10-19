import asyncio
import array
import gc
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import edge_tts
import miniaudio
import pyttsx3
import speech_recognition as sr
from aiohttp import ClientError

from config import LAST_RESPONSE_BY_TYPE, NEURAL_VOICE

listener = sr.Recognizer()
listener.dynamic_energy_threshold = True
listener.dynamic_energy_ratio = 1.2
listener.pause_threshold = 1.8
MICROPHONE_MUTED = threading.Event()
control_panel = None


def configure_control_panel(panel):
    global control_panel
    control_panel = panel


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

def choose_fresh_response(response_type, responses, avoid=()):
    previous = LAST_RESPONSE_BY_TYPE.get(response_type)
    excluded = set(avoid)
    if previous is not None:
        excluded.add(previous)
    choices = [response for response in responses if response not in excluded]
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

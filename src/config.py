from pathlib import Path

MODEL = "qwen2.5:3b"
OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.txt"
YOUTUBE_EXTENSION_ID = "oobemglpmdagidpodobnccbokjplkjgk"
YOUTUBE_BRIDGE_PORT = 8765
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
    "Alright, take care!",
    "See you later!",
    "Bye for now. Talk soon!",
    "Take it easy. I'll be here when you need me.",
    "Catch you later!",
    "Have a good one. Bye!",
    "Okay, I'll let you go. Take care!",
    "Until next time!",
)

GOODNIGHT_RESPONSES = (
    "Good night. Sleep well!",
    "Night! Hope you get some good rest.",
    "Sleep well. I'll be here when you need me.",
    "Rest easy, and have a peaceful night.",
    "Good night! Talk to you later.",
    "Sweet dreams. Take care!",
    "All right, I'll let you get some rest. Good night!",
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

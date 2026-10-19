import unittest
from collections import deque
from threading import Event
from unittest.mock import Mock, patch

import main
from assistant import router as actions
from audio.speech import MICROPHONE_MUTED, InterruptibleMicrophoneStream, MicrophoneMuted
from model.ollama_client import interpret_request
from tools import youtube


class ModuleTests(unittest.TestCase):
    def test_entry_point_and_modules_import(self):
        self.assertTrue(callable(main.run_lucy))
        self.assertTrue(youtube.YOUTUBE_BRIDGE_PORT > 0)

    def test_deterministic_pause_intent(self):
        result = interpret_request([], "pause YouTube", [])
        self.assertEqual(result["intent"], "pause_youtube")

    def test_web_search_intent_dispatches_to_tool(self):
        history = deque()
        interpretation = {
            "intent": "web_search",
            "query": "Lucy AI",
            "reply": "",
            "when": "today",
        }
        with (
            patch.object(
                actions,
                "search_web",
                return_value=(
                    "Search summary.",
                    [{"title": "Lucy", "url": "https://example.com"}],
                    "DuckDuckGo",
                ),
            ) as search,
            patch.object(actions, "talk") as speak,
        ):
            result = actions.perform_intent(
                interpretation,
                history,
                "search Lucy AI",
                [],
            )

        self.assertEqual(result, "web_search")
        search.assert_called_once_with("Lucy AI")
        speak.assert_called_once_with("Search summary.")
        self.assertEqual(history[-1]["content"], "Search summary.")

    def test_muted_microphone_stream_does_not_read(self):
        stream = Mock()
        MICROPHONE_MUTED.set()
        self.addCleanup(MICROPHONE_MUTED.clear)
        with self.assertRaises(MicrophoneMuted):
            InterruptibleMicrophoneStream(stream).read(1024)
        stream.read.assert_not_called()

    def test_youtube_bridge_validates_urls_and_completions(self):
        self.assertTrue(youtube.is_youtube_url("https://www.youtube.com/watch?v=abc"))
        self.assertFalse(youtube.is_youtube_url("https://youtube.com.example.org/watch?v=abc"))
        request_event = Event()
        youtube.YOUTUBE_CONTROL_REQUESTS["test-request"] = {
            "event": request_event,
            "status": None,
        }
        self.addCleanup(youtube.YOUTUBE_CONTROL_REQUESTS.pop, "test-request", None)

        self.assertTrue(youtube.complete_youtube_control("test-request", "paused"))
        self.assertTrue(request_event.is_set())
        self.assertFalse(youtube.complete_youtube_control("missing", "paused"))


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path
import sys
from threading import Event
from unittest.mock import Mock, patch

# The application uses a src layout and can also be started directly with
# `python src/main.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main
from assistant import router as actions
from audio.speech import MICROPHONE_MUTED, InterruptibleMicrophoneStream, MicrophoneMuted
from assistant.context import AssistantContext
from assistant.loop import is_open_confirmation
from config import GOODNIGHT_RESPONSES
from model.ollama_client import (
    interpret_request,
    is_explicit_youtube_result_selection,
    is_goodbye_request,
    is_simple_acknowledgement,
    lyrics_search_query,
    youtube_search_query,
)
from tools import youtube


class OllamaResponse:
    def __init__(self, interpretation):
        self.interpretation = interpretation

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        content = json.dumps({"message": {"content": json.dumps(self.interpretation)}})
        return content.encode("utf-8")


class ModuleTests(unittest.TestCase):
    def test_entry_point_and_modules_import(self):
        self.assertTrue(callable(main.run_lucy))
        self.assertTrue(youtube.YOUTUBE_BRIDGE_PORT > 0)

    def test_deterministic_pause_intent(self):
        result = interpret_request([], "pause YouTube", [])
        self.assertEqual(result["intent"], "pause_youtube")

    def test_goodnight_and_bye_bye_are_deterministic_farewells(self):
        for command in (
            "buy Lucy good night",
            "bye-bye good night meaning yes you can stop now",
            "you can stop now",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_goodbye_request(command))
                result = interpret_request([], command, [])
                self.assertEqual(result["intent"], "goodbye")

    def test_goodbye_intent_requires_explicit_farewell_or_offline_request(self):
        model_response = {
            "intent": "goodbye",
            "query": "",
            "reply": "Okay, I'll let you go. Take care!",
            "when": "today",
        }
        for command in ("tell me about the goodbye song",):
            with self.subTest(command=command), patch(
                "model.ollama_client.urlopen",
                return_value=OllamaResponse(model_response),
            ):
                result = interpret_request([], command, [])

            self.assertEqual(result["intent"], "chat")
            self.assertEqual(result["reply"], "I'm here if you need anything else.")

    def test_goodnight_replies_are_natural_and_do_not_repeat(self):
        context = AssistantContext(
            history=[
                {
                    "role": "assistant",
                    "content": "Good night! Have a pleasant sleep.",
                }
            ]
        )
        interpretation = {
            "intent": "goodbye",
            "query": "",
            "reply": "",
            "when": "today",
        }
        with patch.object(actions, "talk"):
            actions.perform_intent(
                interpretation,
                context,
                "buy Lucy good night",
            )
            first_farewell = context.history[-1]["content"]
            actions.perform_intent(
                interpretation,
                context,
                "bye-bye good night",
            )
            second_farewell = context.history[-1]["content"]

        self.assertIn(first_farewell, GOODNIGHT_RESPONSES)
        self.assertIn(second_farewell, GOODNIGHT_RESPONSES)
        self.assertNotEqual(first_farewell, second_farewell)

    def test_lyrics_request_routes_to_search_without_model_summary(self):
        result = interpret_request(
            [],
            "Can you give me the lyrics of Joji Love You Less?",
            [],
        )
        self.assertEqual(result["intent"], "web_search")
        self.assertEqual(result["query"], "Joji Love You Less lyrics")
        self.assertEqual(
            lyrics_search_query("What are the lyrics to Joji Love You Less?"),
            "Joji Love You Less lyrics",
        )

    def test_acknowledgement_does_not_resume_youtube(self):
        self.assertTrue(is_simple_acknowledgement("okay"))
        result = interpret_request(
            [{"role": "assistant", "content": "Paused the YouTube video."}],
            "okay",
            [{"title": "Previous video"}],
        )
        self.assertEqual(result["intent"], "chat")
        self.assertEqual(result["reply"], "Okay.")

    def test_grateful_acknowledgement_does_not_end_session(self):
        for command in ("great thank you", "thank you so much", "thanks, that's great"):
            with self.subTest(command=command):
                with patch("model.ollama_client.urlopen") as model_request:
                    result = interpret_request([], command, [])
                self.assertEqual(result["intent"], "chat")
                model_request.assert_not_called()

    def test_named_video_is_not_treated_as_cached_result_selection(self):
        self.assertFalse(
            is_explicit_youtube_result_selection("Fortress Defense by 10 Feet")
        )
        self.assertTrue(is_explicit_youtube_result_selection("play the first result"))
        self.assertEqual(
            youtube_search_query("I said Pokémon lullabies"),
            "Pokémon lullabies",
        )
        self.assertEqual(
            youtube_search_query("Fortress Defense by 10 ft"),
            "Fortress Defense by 10 ft",
        )

    def test_named_video_replaces_model_cached_result_selection(self):
        model_response = {
            "intent": "select_youtube_result",
            "query": "1",
            "reply": "",
            "when": "today",
        }
        for command, expected_query in (
            ("Fortress Defense by 10 ft", "Fortress Defense by 10 ft"),
            ("I said Pokémon lullabies", "Pokémon lullabies"),
        ):
            with self.subTest(command=command), patch(
                "model.ollama_client.urlopen",
                return_value=OllamaResponse(model_response),
            ):
                result = interpret_request(
                    [],
                    command,
                    [{"title": "A previous video"}],
                )

            self.assertEqual(result["intent"], "play_youtube")
            self.assertEqual(result["query"], expected_query)

    def test_web_search_intent_dispatches_to_tool(self):
        context = AssistantContext()
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
                context,
                "search Lucy AI",
            )

        self.assertEqual(result, "web_search")
        search.assert_called_once_with("Lucy AI")
        speak.assert_called_once_with(
            "Search summary. Would you like me to open one of the websites I found?"
        )
        self.assertEqual(context.history[-1]["content"], speak.call_args.args[0])
        self.assertTrue(context.pending_web_open)
        self.assertEqual(context.web_results[0]["url"], "https://example.com")

    def test_follow_up_interpretation_receives_search_results(self):
        model_response = {
            "intent": "chat",
            "query": "",
            "reply": "The search results suggest that the song is on the artist's page.",
            "when": "today",
        }
        request_messages = []

        def respond_to_model(request, timeout):
            request_messages.extend(json.loads(request.data)["messages"])
            return OllamaResponse(model_response)

        with patch(
            "model.ollama_client.urlopen",
            side_effect=respond_to_model,
        ):
            result = interpret_request(
                [
                    {"role": "user", "content": "Search for the song."},
                    {"role": "assistant", "content": "I found several pages."},
                ],
                "Which result mentions the artist?",
                [],
                [
                    {
                        "title": "Artist page",
                        "url": "https://example.com/artist",
                        "snippet": "Official artist information.",
                    }
                ],
            )

        self.assertEqual(result["intent"], "chat")
        search_context = next(
            message["content"]
            for message in request_messages
            if "latest web search results" in message["content"]
        )
        self.assertIn("Artist page", search_context)
        self.assertIn("Official artist information.", search_context)

    def test_lyrics_request_offers_to_open_without_summarizing_lyrics(self):
        context = AssistantContext()
        interpretation = {
            "intent": "web_search",
            "query": "Joji Love You Less lyrics",
            "reply": "",
            "when": "today",
        }
        with (
            patch.object(
                actions,
                "search_web",
                return_value=(
                    "",
                    [{"title": "Lyrics page", "url": "https://example.com/lyrics"}],
                    "DuckDuckGo",
                ),
            ) as search,
            patch.object(actions, "talk") as speak,
        ):
            actions.perform_intent(
                interpretation,
                context,
                "Can you give me the lyrics of Joji Love You Less?",
            )

        search.assert_called_once_with("Joji Love You Less lyrics", summarize=False)
        self.assertIn("Would you like me to open the top result?", speak.call_args.args[0])
        self.assertTrue(context.pending_web_open)

    def test_confirmed_search_follow_up_opens_first_result(self):
        context = AssistantContext(
            web_results=[
                {"title": "Lyrics page", "url": "https://example.com/lyrics"}
            ],
            pending_web_open=True,
        )
        self.assertTrue(is_open_confirmation("yes"))
        self.assertTrue(is_open_confirmation("yeah, open it"))
        self.assertTrue(is_open_confirmation("open the first one"))
        with (
            patch.object(actions.webbrowser, "open", return_value=True) as open_page,
            patch.object(actions, "talk") as speak,
        ):
            actions.perform_intent(
                {"intent": "open_search_result", "query": "1", "reply": ""},
                context,
                "yes",
            )

        open_page.assert_called_once_with("https://example.com/lyrics")
        speak.assert_called_once_with("Opened Lyrics page in your browser.")

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

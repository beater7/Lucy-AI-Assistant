import re
import time

from .router import handle_local_command, perform_intent
from audio.speech import (
    MICROPHONE_MUTED,
    calibrate_microphone,
    choose_fresh_response,
    greet_user,
    take_command,
    talk,
)
from config import WAKE_RESPONSES
from model.ollama_client import interpret_request
from .context import AssistantContext


def is_open_confirmation(command):
    return re.fullmatch(
        r"\s*(?:(?:yes|yeah|yep|sure)(?:\s+please)?"
        r"(?:[, ]+(?:open (?:it|them|one|the website|the top result)|"
        r"bring (?:it|them) up|show me))?|please|go ahead|"
        r"open (?:it|them|one|the website|the top result|the first one)|"
        r"bring (?:it|them) up|show me|that one|the first one)"
        r"[\s,!.?]*",
        command,
        re.IGNORECASE,
    ) is not None


def run_assistant(control_panel):
    if not calibrate_microphone():
        control_panel.set_status("Microphone unavailable")
        return

    greet_user()
    context = AssistantContext()
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

        if context.pending_web_open:
            context.pending_web_open = False
            if is_open_confirmation(command):
                try:
                    perform_intent(
                        {
                            "intent": "open_search_result",
                            "query": "1",
                            "reply": "",
                        },
                        context,
                        command,
                    )
                except (RuntimeError, ValueError) as error:
                    print(f"Request error: {error}")
                    talk(str(error))
                continue

        if handle_local_command(command):
            continue

        control_panel.set_status("Thinking")
        try:
            interpretation = interpret_request(
                context.history,
                command,
                context.youtube_results,
                context.web_results,
            )
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
                    context,
                    command,
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

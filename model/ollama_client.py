import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import (
    INTERPRETATION_SCHEMA,
    MODEL,
    OLLAMA_CHAT_URL,
    SUPPORTED_INTENTS,
    SYSTEM_INSTRUCTION,
)


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

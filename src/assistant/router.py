import datetime
import pyjokes
import re
import webbrowser
from urllib.parse import quote_plus

from audio.speech import choose_fresh_response, talk
from config import (
    GOODBYE_RESPONSES,
    GOODNIGHT_RESPONSES,
    MAX_HISTORY_MESSAGES,
)
from tools.folders import browse_user_folders, open_user_folder
from tools.weather import describe_time, describe_timezone, describe_weather, known_philippines_locations
from tools.web import search_web
from tools.youtube import (
    control_youtube_playback,
    open_chrome_url,
    open_youtube_result,
    open_youtube_url,
    search_youtube,
)


def handle_local_command(command):
    if command in {"time", "what time is it", "tell me the time", "what's the time"}:
        current_time = datetime.datetime.now().strftime("%I:%M %p")
        talk(f"The current time is {current_time}")
        return True

    if command in {"joke", "tell me a joke", "say a joke"}:
        talk(pyjokes.get_joke())
        return True

    return False

def is_lyrics_request(text):
    return re.search(r"\blyrics?\b", text, re.IGNORECASE) is not None


def perform_intent(interpretation, context, command):
    intent = interpretation["intent"]
    query = interpretation["query"]
    reply = interpretation["reply"]
    history = context.history
    youtube_results = context.youtube_results

    if intent == "chat":
        response = reply
    elif intent == "web_search":
        lyrics_request = is_lyrics_request(query) or is_lyrics_request(command)
        context.pending_web_open = False
        if lyrics_request:
            _, sources, provider = search_web(query, summarize=False)
            context.web_results[:] = sources
            context.pending_web_open = bool(sources)
            response = (
                "I can't provide the full lyrics, but I found lyric pages. "
                "Would you like me to open the top result?"
                if sources
                else "I can't provide the full lyrics, and I couldn't find a page to open."
            )
        else:
            response, sources, provider = search_web(query)
            context.web_results[:] = sources
            context.pending_web_open = bool(sources)
            if sources:
                response = f"{response} Would you like me to open one of the websites I found?"
        print(f"Search sources ({provider}):")
        for source in sources:
            print(f"- {source['title']}: {source['url']}")
    elif intent == "open_search_result":
        result_number = int(query)
        if result_number < 1 or result_number > len(context.web_results):
            response = "I don't have that search result available. Please search again."
        else:
            result = context.web_results[result_number - 1]
            if webbrowser.open(result["url"]):
                response = f"Opened {result['title']} in your browser."
            else:
                response = "I couldn't open that page. Please check your browser settings."
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
        is_goodnight = re.search(r"\bgood\s*night\b", command, re.IGNORECASE)
        farewell_responses = (
            GOODNIGHT_RESPONSES if is_goodnight else GOODBYE_RESPONSES
        )
        recent_replies = [
            message["content"]
            for message in reversed(history)
            if message["role"] == "assistant"
        ][:3]
        response = choose_fresh_response(
            "farewell",
            farewell_responses,
            avoid=recent_replies,
        )
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

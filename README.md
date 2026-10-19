# Lucy AI Assistant

Lucy is a Windows desktop voice assistant with a small floating control widget. [`src/main.py`](./src/main.py) starts the assistant; its application code is organized under `src/`.

`src/config.py` contains shared settings; `src/assistant/loop.py` runs the conversation flow, `src/assistant/context.py` holds conversation state, and `src/assistant/router.py` dispatches intents. `src/model/ollama_client.py` handles Ollama interpretation and search summaries, `src/audio/speech.py` handles speech input and output, `src/tools/` contains web, YouTube, weather, and folder tools, and `src/ui/lucy_ui.py` contains the floating widget. The system instructions are in `src/system_prompt.txt`.

## What Lucy can do

- Answer conversational requests with a local Qwen 2.5 model through Ollama.
- Search the web with DuckDuckGo, summarize results aloud, and ask before opening a result; lyric requests offer lyric pages instead of reproducing lyrics. Google searches open in the browser.
- Search and play YouTube videos, reuse an existing YouTube tab with the optional Chrome extension, and pause or resume YouTube playback.
- Tell the time, report time zones, check weather forecasts, and tell jokes.
- Browse and open folders by name in Desktop, Documents, Downloads, Pictures, Music, and Videos. Lucy does not read files or search elsewhere.
- Show listening, thinking, speaking, and muted states in a draggable, always-on-top widget. Click the microphone icon to mute; right-click and choose **Exit Lucy**, or press **Escape**, to exit.

Lucy does not require a wake phrase while unmuted. Voice-command **“pause listening”** still listens for **“Hey Lucy”** to resume; the widget's mute button stops microphone capture until you manually unmute.

## Requirements

- Windows 10 or 11, 64-bit.
- Python 3.11, 64-bit is the recommended version (the project is currently tested with Python 3.11).
- A working microphone and speakers/headphones.
- Internet access for speech recognition, neural speech, web/weather/YouTube searches, and other online services.
- [Ollama for Windows](https://ollama.com/download/windows), with the `qwen2.5:3b` model downloaded, for conversation and request interpretation.
- [VLC for Windows](https://www.videolan.org/vlc/) to play Lucy's neural speech without opening a media-player window. Lucy falls back to the installed Windows voice if neural speech or VLC is unavailable.
- Google Chrome is optional. It is only needed for the Lucy extension's existing-YouTube-tab reuse and playback control.

Lucy does not need an API key or a `.env` file. Speech recognition sends microphone audio to Google's recognition service; Lucy's neural speech text is sent to Microsoft's Edge speech service. Qwen runs locally through Ollama. Search requests and city names are sent to the relevant search or weather service. Lucy keeps conversation history in memory only.

## Install and run

Open PowerShell in the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install Ollama, then download Lucy's model:

```powershell
ollama pull qwen2.5:3b
```

Make sure Ollama is running, keep VLC installed, then start Lucy:

```powershell
.\.venv\Scripts\python.exe .\src\main.py
```

On first startup, Lucy calibrates the microphone. Keep quiet briefly while she calibrates. She uses the Windows default microphone. Her speech detector is tuned to be more sensitive and waits 1.8 seconds of silence before treating a phrase as complete. The widget displays a live microphone waveform and follows the amplitude of Lucy's generated speech.

Run the focused regression checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Optional: Chrome YouTube extension

The extension is only required to reuse an already-open YouTube tab and to control YouTube playback; Lucy can still search and open YouTube without it.

1. In Chrome, open `chrome://extensions` and turn on **Developer mode**.
2. Select **Load unpacked** and choose this project's `chrome_extension` folder.
3. Leave **Lucy YouTube Tab Reuse** enabled and use the Chrome profile containing your YouTube tabs.
4. After changing extension files, select **Reload** for the extension. The current manifest version is **1.5.0**.

When the extension is unavailable, Lucy falls back to opening YouTube in the browser normally. The extension communicates with Lucy's local bridge at `127.0.0.1`; it needs YouTube tab URLs to find and navigate an existing YouTube tab. It does not read page contents or account data.

## Troubleshooting

- **Ollama connection error:** Start the Ollama app and verify `qwen2.5:3b` is installed with `ollama list`.
- **Microphone unavailable:** Check the Windows default input device and microphone privacy permissions for desktop apps.
- **PyAudio install error:** Use 64-bit Python 3.11 and recreate the virtual environment before installing requirements.
- **Neural speech does not play:** Install VLC and restart Lucy. Lucy logs the neural-speech error and tries the installed Windows SAPI voice as a fallback.
- **YouTube opens a new tab:** Confirm the extension is enabled, loaded from this project's `chrome_extension` folder, and running in the same Chrome profile as the YouTube tab.

## Customize Lucy

Lucy's assistant instructions are in [`src/system_prompt.txt`](./src/system_prompt.txt). Edit that file to adjust her conversational style or request interpretation; restart Lucy to load changes.

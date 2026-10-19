import threading

from assistant.loop import run_assistant
from audio.speech import MICROPHONE_MUTED, configure_control_panel
from ui.lucy_ui import LucyControlPanel


def run_lucy():
    control_panel = LucyControlPanel(MICROPHONE_MUTED)
    configure_control_panel(control_panel)
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

import math
import queue
import tkinter as tk
from threading import Event, Lock
from time import monotonic


class ScaledCanvas:
    def __init__(self, canvas, scale):
        self.canvas = canvas
        self.scale = scale

    def __getattr__(self, name):
        return getattr(self.canvas, name)

    def _scale_options(self, options):
        scaled_options = options.copy()
        if "width" in scaled_options:
            scaled_options["width"] = max(1, scaled_options["width"] * self.scale)
        if "font" in scaled_options:
            family, size, *style = scaled_options["font"]
            scaled_options["font"] = (
                family,
                max(6, round(size * self.scale)),
                *style,
            )
        return scaled_options

    def create_polygon(self, *coordinates, **options):
        scaled = (coordinate * self.scale for coordinate in coordinates)
        return self.canvas.create_polygon(
            *scaled, **self._scale_options(options)
        )

    def create_oval(self, *coordinates, **options):
        scaled = (coordinate * self.scale for coordinate in coordinates)
        return self.canvas.create_oval(*scaled, **self._scale_options(options))

    def create_line(self, *coordinates, **options):
        scaled = (coordinate * self.scale for coordinate in coordinates)
        return self.canvas.create_line(*scaled, **self._scale_options(options))

    def create_arc(self, *coordinates, **options):
        scaled = (coordinate * self.scale for coordinate in coordinates)
        return self.canvas.create_arc(*scaled, **self._scale_options(options))

    def create_text(self, x, y, **options):
        return self.canvas.create_text(
            x * self.scale, y * self.scale, **self._scale_options(options)
        )


class LucyControlPanel:
    WIDTH = 344
    HEIGHT = 112
    SCALE = 0.74
    BACKGROUND_KEY = "#010203"
    MIC_CENTER = (292, 55)
    MIC_RADIUS = 31

    def __init__(self, microphone_muted: Event):
        self.microphone_muted = microphone_muted
        self.root = tk.Tk()
        self.root.title("Lucy")
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.97)
        try:
            self.root.attributes("-transparentcolor", self.BACKGROUND_KEY)
        except tk.TclError:
            pass

        canvas = tk.Canvas(
            self.root,
            width=round(self.WIDTH * self.SCALE),
            height=round(self.HEIGHT * self.SCALE),
            bg=self.BACKGROUND_KEY,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack()
        self.canvas = ScaledCanvas(canvas, self.SCALE)
        self.updates = queue.Queue()
        self.level_lock = Lock()
        self.audio_level = 0.0
        self.latest_audio_level = 0.0
        self.speech_level = 0.0
        self.latest_speech_level = 0.0
        self.closing = Event()
        self.muted = microphone_muted.is_set()
        self.status = "Muted" if self.muted else "Starting"
        self.started_at = monotonic()
        self.drag_origin = None
        self.dragged = False
        self.pressed_mic = False
        self.update_callback = None
        self.context_menu = tk.Menu(
            self.root,
            tearoff=False,
            bg="#252834",
            fg="#eeeeF6",
            activebackground="#7657d5",
            activeforeground="#ffffff",
            bd=0,
        )
        self.context_menu.add_command(label="Exit Lucy", command=self.close)

        self._draw_widget()
        self.canvas.bind("<ButtonPress-1>", self.start_pointer)
        self.canvas.bind("<B1-Motion>", self.move_pointer)
        self.canvas.bind("<ButtonRelease-1>", self.release_pointer)
        self.canvas.bind("<Button-3>", self.show_context_menu)
        self.root.bind(
            "<Escape>",
            lambda event: self.close() if event.keysym == "Escape" else None,
        )
        self.root.bind("<Button-3>", self.show_context_menu)

        self.root.update_idletasks()
        self.root.geometry(
            f"{round(self.WIDTH * self.SCALE)}x{round(self.HEIGHT * self.SCALE)}"
            f"+{self.root.winfo_screenwidth() - round(self.WIDTH * self.SCALE) - 28}"
            f"+{self.root.winfo_screenheight() - round(self.HEIGHT * self.SCALE) - 78}"
        )
        self.update_callback = self.root.after(35, self.process_updates)

    def _draw_widget(self):
        canvas = self.canvas
        canvas.delete("all")
        muted = self.muted
        shadow = "#08090d" if not muted else "#111217"
        panel = "#252834" if not muted else "#1d1f26"
        outline = "#686b7c" if not muted else "#444651"
        accent = "#ad79ff" if not muted else "#635678"
        face = "#151720" if not muted else "#181920"
        foreground = "#e8e2f5" if not muted else "#84828c"

        for offset, shade in ((6, "#07080b"), (4, "#11131a"), (2, shadow)):
            self._rounded_rectangle(
                9 + offset,
                8 + offset,
                self.WIDTH - 9 + offset,
                self.HEIGHT - 9 + offset,
                45,
                fill=shade,
                outline="",
            )
        self._rounded_rectangle(
            9,
            8,
            self.WIDTH - 9,
            self.HEIGHT - 9,
            45,
            fill=panel,
            outline=outline,
            width=1.4,
        )
        self._rounded_rectangle(
            15,
            14,
            self.WIDTH - 15,
            self.HEIGHT - 15,
            39,
            fill="",
            outline="#353845" if not muted else "#292b33",
            width=1,
        )

        self._draw_mascot(face, accent, foreground)
        self._draw_wave(accent)
        self._draw_microphone(accent, foreground, outline)

    def _rounded_rectangle(
        self, left, top, right, bottom, radius, fill, outline, width=1
    ):
        points = []
        for center_x, center_y, start in (
            (right - radius, top + radius, 270),
            (right - radius, bottom - radius, 0),
            (left + radius, bottom - radius, 90),
            (left + radius, top + radius, 180),
        ):
            for step in range(9):
                angle = math.radians(start + step * 90 / 8)
                points.append(
                    (
                        center_x + radius * math.cos(angle),
                        center_y + radius * math.sin(angle),
                    )
                )
        flattened = [coordinate for point in points for coordinate in point]
        self.canvas.create_polygon(
            *flattened,
            smooth=True,
            splinesteps=12,
            fill=fill,
            outline=outline,
            width=width,
        )

    def _draw_mascot(self, face, accent, foreground):
        canvas = self.canvas
        phase = monotonic() - self.started_at
        if self.status in {"Listening", "Wake word only"}:
            bob = math.sin(phase * 9) * 2.0
        elif self.status in {"Thinking", "Speaking"}:
            bob = math.sin(phase * 2.2) * 0.8
        else:
            bob = math.sin(phase * 1.8) * 1.4

        center_x, center_y = 57, 55 + bob
        radius = 27
        canvas.create_oval(
            center_x - radius - 3,
            center_y - radius - 3,
            center_x + radius + 3,
            center_y + radius + 3,
            fill="#343040" if self.status == "Listening" else "",
            outline="",
        )
        canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            fill=face,
            outline="#443c58" if self.status == "Listening" else "",
            width=1.2,
        )

        if self.muted:
            canvas.create_text(
                center_x,
                center_y,
                text="- -",
                fill="#827a91",
                font=("Segoe UI", 13, "bold"),
            )
            self._draw_sleep_zs(center_y)
        elif self.status == "Thinking":
            for eye_x in (center_x - 9, center_x + 9):
                canvas.create_line(
                    eye_x - 3,
                    center_y - 5,
                    eye_x + 3,
                    center_y - 5,
                    fill=accent,
                    width=2.5,
                    capstyle=tk.ROUND,
                )
            canvas.create_line(
                center_x - 5,
                center_y + 8,
                center_x + 5,
                center_y + 8,
                fill=foreground,
                width=2,
                capstyle=tk.ROUND,
            )
        elif self.status in {"Listening", "Wake word only"}:
            for eye_x in (center_x - 9, center_x + 9):
                canvas.create_oval(
                    eye_x - 2.3,
                    center_y - 7,
                    eye_x + 2.3,
                    center_y - 2.4,
                    fill=accent,
                    outline="",
                )
            canvas.create_arc(
                center_x - 7,
                center_y + 1,
                center_x + 7,
                center_y + 13,
                start=200,
                extent=140,
                style=tk.ARC,
                outline=accent,
                width=2.5,
            )
        else:
            for eye_x in (center_x - 9, center_x + 9):
                canvas.create_arc(
                    eye_x - 4,
                    center_y - 8,
                    eye_x + 4,
                    center_y,
                    start=20,
                    extent=140,
                    style=tk.ARC,
                    outline=accent,
                    width=2.5,
                )
            canvas.create_arc(
                center_x - 7,
                center_y + 1,
                center_x + 7,
                center_y + 13,
                start=200,
                extent=140,
                style=tk.ARC,
                outline=foreground,
                width=2.5,
            )

    def _draw_sleep_zs(self, center_y):
        panel_color = "#252834"
        sleep_color = "#9a82bd"
        elapsed = monotonic() - self.started_at
        lifetime = 2.4
        for index, size in enumerate((8, 9, 10)):
            progress = (elapsed + index * 0.88) % 3.0
            if progress >= lifetime:
                continue
            fade_in = min(progress / 0.25, 1.0)
            fade_out = min((lifetime - progress) / 0.65, 1.0)
            visibility = max(0.0, min(fade_in, fade_out))
            color = self._blend_color(panel_color, sleep_color, visibility)
            self.canvas.create_text(
                76 + progress * 1.5,
                center_y - 24 - progress * 10,
                text="z" if index < 2 else "Z",
                fill=color,
                font=("Segoe UI", size, "bold"),
            )

    @staticmethod
    def _blend_color(background, foreground, amount):
        background_values = tuple(
            int(background[offset : offset + 2], 16) for offset in (1, 3, 5)
        )
        foreground_values = tuple(
            int(foreground[offset : offset + 2], 16) for offset in (1, 3, 5)
        )
        blended = tuple(
            round(start + (end - start) * amount)
            for start, end in zip(background_values, foreground_values)
        )
        return "#" + "".join(f"{value:02x}" for value in blended)

    def _draw_wave(self, accent):
        center_x = 180
        center_y = 55
        count = 31
        spacing = 4.5
        phase = monotonic() - self.started_at
        listening = self.status in {"Listening", "Wake word only"} and not self.muted
        thinking = self.status == "Thinking" and not self.muted
        speaking = self.status == "Speaking" and not self.muted

        with self.level_lock:
            target_level = self.latest_audio_level if listening else 0.0
            target_speech_level = self.latest_speech_level if speaking else 0.0
        self.audio_level += (target_level - self.audio_level) * 0.45
        smoothing = 0.72 if target_speech_level > self.speech_level else 0.38
        self.speech_level += (target_speech_level - self.speech_level) * smoothing

        for index in range(count):
            x = center_x + (index - (count - 1) / 2) * spacing
            envelope = 0.2 + 0.8 * math.sin(math.pi * (index + 1) / (count + 1))
            if listening:
                ripple = 0.42 + 0.58 * abs(
                    math.sin(index * 0.57 - phase * 7.5)
                )
                height = min(
                    39,
                    2.0
                    + envelope * (2.5 + self.audio_level * 45 * ripple),
                )
            elif thinking:
                height = 2.5 + envelope * (
                    12 + 6 * math.sin(index * 0.39 - phase * 2.8)
                )
            elif speaking:
                voice_shape = 0.5 + 0.5 * abs(math.sin(index * 0.47))
                height = 2.0 + envelope * self.speech_level * 40 * voice_shape
            else:
                height = 2.0

            shade = accent if index % 3 else "#d0a7ff"
            if self.muted:
                shade = "#514a5c"
            self.canvas.create_line(
                x,
                center_y - height,
                x,
                center_y + height,
                fill=shade,
                width=2.6,
                capstyle=tk.ROUND,
            )

    def _draw_microphone(self, accent, foreground, outline):
        canvas = self.canvas
        center_x, center_y = self.MIC_CENTER
        canvas.create_oval(
            center_x - self.MIC_RADIUS,
            center_y - self.MIC_RADIUS,
            center_x + self.MIC_RADIUS,
            center_y + self.MIC_RADIUS,
            fill="#282a35" if not self.muted else "#22232a",
            outline=outline,
            width=1.2,
        )
        mic_color = accent if not self.muted else "#85818d"
        canvas.create_oval(
            center_x - 5,
            center_y - 15,
            center_x + 5,
            center_y + 3,
            outline=mic_color,
            width=2,
        )
        canvas.create_arc(
            center_x - 10,
            center_y - 5,
            center_x + 10,
            center_y + 13,
            start=180,
            extent=180,
            style=tk.ARC,
            outline=mic_color,
            width=2,
        )
        canvas.create_line(
            center_x,
            center_y + 12,
            center_x,
            center_y + 19,
            fill=mic_color,
            width=2,
            capstyle=tk.ROUND,
        )
        canvas.create_line(
            center_x - 6,
            center_y + 20,
            center_x + 6,
            center_y + 20,
            fill=mic_color,
            width=2,
            capstyle=tk.ROUND,
        )
        if self.muted:
            canvas.create_line(
                center_x - 21,
                center_y - 21,
                center_x + 21,
                center_y + 21,
                fill=foreground,
                width=2.5,
                capstyle=tk.ROUND,
            )

    def start_pointer(self, event):
        self.drag_origin = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
        )
        self.dragged = False
        self.pressed_mic = self._inside_mic(event.x, event.y)

    def move_pointer(self, event):
        if self.drag_origin is None or self.pressed_mic:
            return
        pointer_x, pointer_y, window_x, window_y = self.drag_origin
        delta_x = event.x_root - pointer_x
        delta_y = event.y_root - pointer_y
        if abs(delta_x) + abs(delta_y) > 3:
            self.dragged = True
        if self.dragged:
            self.root.geometry(f"+{window_x + delta_x}+{window_y + delta_y}")

    def release_pointer(self, event):
        if self.pressed_mic and self._inside_mic(event.x, event.y) and not self.dragged:
            self.toggle_mute()
        self.drag_origin = None
        self.pressed_mic = False

    def _inside_mic(self, x, y):
        center_x, center_y = self.MIC_CENTER
        x /= self.SCALE
        y /= self.SCALE
        return (x - center_x) ** 2 + (y - center_y) ** 2 <= self.MIC_RADIUS**2

    def show_context_menu(self, event):
        if self.closing.is_set():
            return
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            if not self.closing.is_set():
                raise
        finally:
            if not self.closing.is_set():
                try:
                    self.context_menu.grab_release()
                except tk.TclError:
                    if not self.closing.is_set():
                        raise

    def toggle_mute(self):
        self.muted = not self.muted
        if self.muted:
            self.microphone_muted.set()
            self.status = "Muted"
        else:
            self.microphone_muted.clear()
            self.status = "Listening"
        self.root.attributes("-alpha", 0.82 if self.muted else 0.97)
        self._draw_widget()

    def set_status(self, status):
        self.updates.put(("status", status))

    def set_audio_level(self, level):
        with self.level_lock:
            self.latest_audio_level = max(0.0, min(1.0, level))

    def set_speech_level(self, level):
        with self.level_lock:
            self.latest_speech_level = max(0.0, min(1.0, level))

    def process_updates(self):
        if self.closing.is_set():
            return
        self.update_callback = None
        while True:
            try:
                update, value = self.updates.get_nowait()
            except queue.Empty:
                break
            if update == "status":
                if value == "Muted" and not self.muted:
                    continue
                if self.muted and value in {
                    "Starting",
                    "Listening",
                    "Thinking",
                    "Wake word only",
                }:
                    value = "Muted"
                if value != self.status:
                    self.status = value
            elif update == "close":
                self.close()
                return

        self._draw_widget()
        if not self.closing.is_set():
            self.update_callback = self.root.after(35, self.process_updates)

    def close(self):
        if self.closing.is_set():
            return
        self.closing.set()
        self.microphone_muted.set()
        if self.update_callback is not None:
            self.root.after_cancel(self.update_callback)
            self.update_callback = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()

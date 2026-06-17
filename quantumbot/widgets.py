from .settings import tk

# ============================================================
# WIDGET GRAFICI CUSTOM: PULSANTI E CARD ARROTONDATE
# ============================================================

class RoundedButton(tk.Canvas):
    """Pulsante arrotondato disegnato su Canvas.

    Tkinter/ttk non offre veri bordi arrotondati in modo affidabile con il tema
    scuro, soprattutto quando si forza il tema "clam". Questo widget mantiene
    un aspetto coerente su macOS sia da script sia da app PyInstaller.
    """

    def __init__(
        self,
        master,
        text,
        command=None,
        bg_color="#21262d",
        hover_color="#30363d",
        active_color="#1f6feb",
        fg_color="#ffffff",
        canvas_bg=None,
        radius=18,
        height=34,
        min_width=92,
        font=("Helvetica", 9, "bold"),
        padx=18,
        **kwargs,
    ):
        self.text = text
        self.command = command
        self.bg_color = bg_color
        self.hover_color = hover_color
        self.active_color = active_color
        self.fg_color = fg_color
        self.disabled_color = "#475569"
        self.disabled_fg_color = "#cbd5e1"
        self.radius = radius
        self.btn_height = height
        self.min_width = min_width
        self.font = font
        self.padx = padx
        self._state = "normal"
        self._pressed = False

        if canvas_bg is None:
            try:
                canvas_bg = master.cget("bg")
            except Exception:
                canvas_bg = "#0d1117"

        width = max(min_width, len(str(text)) * 8 + padx * 2)
        super().__init__(
            master,
            width=width,
            height=height,
            bg=canvas_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kwargs,
        )

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _event: self._draw())
        self._draw()

    def _rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _current_color(self):
        if self._state == "disabled":
            return self.disabled_color
        if self._state == "pressed":
            return self.active_color
        if self._state == "hover":
            return self.hover_color
        return self.bg_color

    def _current_fg(self):
        if self._state == "disabled":
            return self.disabled_fg_color
        return self.fg_color

    def _draw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        self._rounded_rect(1, 1, w - 1, h - 1, self.radius, fill=self._current_color(), outline="")
        self.create_text(w / 2, h / 2, text=self.text, fill=self._current_fg(), font=self.font)

    def _on_enter(self, _event=None):
        if self._state == "disabled":
            return
        self._state = "hover"
        self._draw()

    def _on_leave(self, _event=None):
        if self._state == "disabled":
            return
        self._state = "normal"
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None):
        if self._state == "disabled":
            return
        self._state = "pressed"
        self._pressed = True
        self._draw()

    def _on_release(self, event=None):
        if self._state == "disabled":
            return
        inside = True
        if event is not None:
            inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._state = "hover" if inside else "normal"
        self._draw()
        if inside and self._pressed and callable(self.command):
            self.command()
        self._pressed = False

    def configure(self, cnf=None, **kwargs):
        cnf = cnf or {}
        if isinstance(cnf, dict):
            kwargs.update(cnf)
        if "text" in kwargs:
            self.text = kwargs.pop("text")
            width = max(self.min_width, len(str(self.text)) * 8 + self.padx * 2)
            super().configure(width=width)
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "bg_color" in kwargs:
            self.bg_color = kwargs.pop("bg_color")
        if "hover_color" in kwargs:
            self.hover_color = kwargs.pop("hover_color")
        if "active_color" in kwargs:
            self.active_color = kwargs.pop("active_color")
        if "fg_color" in kwargs:
            self.fg_color = kwargs.pop("fg_color")
        if "state" in kwargs:
            state = str(kwargs.pop("state") or "normal")
            self._state = "disabled" if state == "disabled" else "normal"
            self._pressed = False
            try:
                super().configure(cursor="" if self._state == "disabled" else "hand2")
            except Exception:
                pass
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure


class RoundedMetricCard(tk.Canvas):
    """Card superiore con sfondo arrotondato e testo aggiornato da StringVar."""

    def __init__(
        self,
        master,
        label,
        value_var,
        panel_bg,
        canvas_bg,
        muted_fg,
        value_fg,
        radius=20,
        height=86,
        command=None,
        hint="clicca",
        **kwargs,
    ):
        cursor = "hand2" if callable(command) else ""
        super().__init__(master, height=height, bg=canvas_bg, highlightthickness=0, bd=0, cursor=cursor, **kwargs)
        self.label = label
        self.value_var = value_var
        self.panel_bg = panel_bg
        self.muted_fg = muted_fg
        self.value_fg = value_fg
        self.radius = radius
        self.command = command
        self.hint = hint or "clicca"
        self.value_var.trace_add("write", lambda *_: self._draw())
        self.bind("<Configure>", lambda _event: self._draw())
        if callable(self.command):
            self.bind("<ButtonRelease-1>", self._on_click)
        self._draw()

    def _on_click(self, event=None):
        if callable(self.command):
            try:
                self.command(event)
            except TypeError:
                self.command()

    def _rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def _draw(self):
        self.delete("all")
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        self._rounded_rect(1, 1, w - 1, h - 1, self.radius, fill=self.panel_bg, outline="#263852", width=1)
        self._rounded_rect(12, 10, 48, 14, 3, fill=self.value_fg, outline="")
        self.create_text(14, 28, text=self.label, anchor="w", fill=self.muted_fg, font=("Helvetica", 9))
        self.create_text(14, 55, text=self.value_var.get(), anchor="w", fill=self.value_fg, font=("Helvetica", 13, "bold"))
        if callable(getattr(self, "command", None)):
            self.create_text(w - 14, h - 13, text=str(getattr(self, "hint", "clicca")), anchor="e", fill=self.muted_fg, font=("Helvetica", 8))



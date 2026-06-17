from . import settings as _settings
globals().update({k: v for k, v in vars(_settings).items() if not k.startswith('__')})

from . import widgets as _widgets
globals().update({k: v for k, v in vars(_widgets).items() if not k.startswith('__')})
from . import database as _database
globals().update({k: v for k, v in vars(_database).items() if not k.startswith('__')})
from . import engine as _engine
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith('__')})

# ============================================================
# DASHBOARD
# ============================================================

class QuantumDashboard:
    # Tema moderno: contrasto alto, colori morbidi e meno effetto "software tecnico".
    BG = "#08111f"
    PANEL = "#101c2f"
    PANEL_2 = "#0d1728"
    PANEL_3 = "#15233a"
    BORDER = "#263852"
    TEXT = "#e6edf7"
    MUTED = "#8fa4bd"
    BLUE = "#7dd3fc"
    GREEN = "#34d399"
    RED = "#fb7185"
    YELLOW = "#fde68a"
    VIOLET = "#a78bfa"

    BTN_DARK = ("#1d2b42", "#283a59", "#162238")
    BTN_BLUE = ("#2563eb", "#3b82f6", "#1d4ed8")
    BTN_GREEN = ("#059669", "#10b981", "#047857")
    BTN_RED = ("#e11d48", "#fb7185", "#be123c")

    def __init__(self):
        self.cfg = carica_config()
        self.status = load_json_file(FILE_STATUS, {})
        self.crypto_selezionata_grafico = "BTC/EUR"
        self.modalita_grafico_crypto = "linea"
        self.confronto_attivo = False
        self.crypto_confronto = []
        self._righe_storico_trade = {}
        self._refresh_after_id = None
        self._closed = False
        self._responsive_mode = None
        self._last_good_dashboard_balances = None
        self._last_good_status = self.status if status_dashboard_valido(self.status) else {}
        self._last_good_cfg = deep_copy_json(self.cfg)
        self.last_valid_dashboard_state = {}
        self.last_valid_config = deep_copy_json(self.cfg)
        self._report_busy = False
        self._engine_transition = None
        self._engine_transition_started = 0.0
        self._engine_poll_after_id = None
        self._charts_autorefresh_after_id = None

        # Tkinter variables must be created only after the root window exists.
        # Keeping them before tk.Tk() causes RuntimeError: "Too early to create variable"
        # when the app starts from PyInstaller/windowed builds.
        self.root = tk.Tk()
        self.var_auto_refresh_grafici = tk.BooleanVar(master=self.root, value=True)
        self.var_auto_refresh_grafici_interval = tk.StringVar(master=self.root, value="60")
        self.var_auto_refresh_grafici_status = tk.StringVar(master=self.root, value="Auto-refresh grafici: ON · avvio automatico…")
        self.root.title(f"Quantum Bot Studio {APP_VERSION} - Modern Dashboard")
        try:
            log_evento(f"Dashboard avviata. Cartella dati: {APP_DIR}")
            log_percorsi_operativi("dashboard")
        except Exception:
            pass
        self.root.geometry("1180x820")
        self.root.minsize(760, 520)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.var_crypto = tk.StringVar(value=self.crypto_selezionata_grafico)
        self.var_importo = tk.StringVar(value="100")
        self.var_importo_vendita = tk.StringVar(value="")
        risk_cfg = safe_float(self.cfg.get("percentuale_rischio_per_trade", 5.0))
        self.var_investimento_percentuale = tk.StringVar(value=f"{risk_cfg:.2f}%")
        auto_trade_txt = "Auto Trade ON" if self.cfg.get("auto_trading_attivo", False) else "Auto Trade OFF"
        self.var_auto_trade_status = tk.StringVar(value=auto_trade_txt)
        self.var_modalita_trading = tk.StringVar(value=self.cfg.get("modalita_trading", "Normale"))
        self.var_posizioni_limite = tk.StringVar(value="")
        self.var_timeframe = tk.StringVar(value=self.cfg.get("timeframe", "1m"))
        self.var_modalita_grafico = tk.StringVar(value=self.modalita_grafico_crypto)

        self.setup_style()
        self.build_scroll_container()
        self.build_ui()
        self.root.after(300, lambda: self.apply_responsive_layout(self._dashboard_width()))

        self.scrivi_log(f"[SISTEMA] QuantumBot {APP_VERSION} avviato.")
        self.scrivi_log(f"[SISTEMA] Cartella dati: {APP_DIR}")
        self.scrivi_log("[SISTEMA] Chiudere la Dashboard non ferma l'Engine.")
        self.scrivi_log("[GRAFICI] Auto-refresh PNG SQLite sempre attivo ogni 60s.")
        self._schedule_auto_refresh_grafici(immediate=True)

        if engine_is_running():
            self.scrivi_log(f"[ENGINE] Già attivo. PID {get_engine_pid()}.")
        elif safe_bool(os.environ.get("QUANTUMBOT_AUTO_START"), False):
            self.scrivi_log("[ENGINE] Avvio automatico richiesto da QUANTUMBOT_AUTO_START.")
            self._set_engine_transition("starting")
            if start_engine_process(wait_for_confirm=False):
                self._poll_engine_transition()
            else:
                self._clear_engine_transition()
                self.scrivi_log("[ERRORE] Engine non avviato. Controlla engine_stderr.log, engine_launch.log e errori_bot.log.")
        else:
            self.scrivi_log("[ENGINE] Engine fermo. Usa Avvia Bot per avviare la simulazione.")

        self.refresh_dashboard()

    def setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=self.BG, foreground=self.TEXT, fieldbackground=self.PANEL, font=("Helvetica", 10))
        style.configure("Treeview", background=self.PANEL_2, fieldbackground=self.PANEL_2, foreground="#f8fafc", rowheight=28, font=("Helvetica", 9), borderwidth=0, relief="flat")
        style.configure("Treeview.Heading", background=self.PANEL_3, foreground=self.BLUE, font=("Helvetica", 9, "bold"), borderwidth=0, relief="flat")
        style.map("Treeview", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=self.PANEL_2, background=self.PANEL_2, foreground=self.TEXT, arrowcolor=self.BLUE, borderwidth=0, padding=(10, 5))
        style.map("TCombobox", fieldbackground=[("readonly", self.PANEL_2)], selectbackground=[("readonly", self.PANEL_2)], selectforeground=[("readonly", self.TEXT)])
        style.configure("Vertical.TScrollbar", background=self.PANEL_3, troughcolor=self.PANEL_2, bordercolor=self.PANEL_2, arrowcolor=self.MUTED, relief="flat")
        style.configure("Horizontal.TScrollbar", background=self.PANEL_3, troughcolor=self.PANEL_2, bordercolor=self.PANEL_2, arrowcolor=self.MUTED, relief="flat")

    def build_scroll_container(self):
        """Crea un contenitore scrollabile per tutta la dashboard.

        Serve soprattutto su MacBook o finestre ridotte: la dashboard si riproporziona
        automaticamente e mantiene lo scroll solo quando lo spazio diventa davvero
        troppo piccolo per mostrare tutte le sezioni.
        """
        self.dashboard_min_width = 760

        self.scroll_shell = tk.Frame(self.root, bg=self.BG)
        self.scroll_shell.pack(fill=tk.BOTH, expand=True)

        self.dashboard_canvas = tk.Canvas(
            self.scroll_shell,
            bg=self.BG,
            highlightthickness=0,
            bd=0,
            xscrollincrement=24,
            yscrollincrement=24,
        )
        self.scrollbar_y = ttk.Scrollbar(self.scroll_shell, orient="vertical", command=self.dashboard_canvas.yview)
        self.scrollbar_x = ttk.Scrollbar(self.scroll_shell, orient="horizontal", command=self.dashboard_canvas.xview)
        self.dashboard_canvas.configure(yscrollcommand=self.scrollbar_y.set, xscrollcommand=self.scrollbar_x.set)

        self.dashboard_canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_y.grid(row=0, column=1, sticky="ns")
        self.scrollbar_x.grid(row=1, column=0, sticky="ew")

        self.scroll_shell.grid_rowconfigure(0, weight=1)
        self.scroll_shell.grid_columnconfigure(0, weight=1)

        self.content = tk.Frame(self.dashboard_canvas, bg=self.BG)
        self.content_id = self.dashboard_canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._on_content_configure)
        self.dashboard_canvas.bind("<Configure>", self._on_canvas_configure)
        self.dashboard_canvas.bind_all("<MouseWheel>", self._on_dashboard_mousewheel)
        self.dashboard_canvas.bind_all("<Shift-MouseWheel>", self._on_dashboard_shift_mousewheel)
        self.dashboard_canvas.bind_all("<Button-4>", self._on_dashboard_mousewheel_linux)
        self.dashboard_canvas.bind_all("<Button-5>", self._on_dashboard_mousewheel_linux)

    def _on_content_configure(self, event=None):
        try:
            self.dashboard_canvas.configure(scrollregion=self.dashboard_canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas_configure(self, event):
        try:
            # Il contenuto segue la larghezza reale della finestra.
            # Sotto la larghezza minima resta disponibile lo scroll orizzontale,
            # ma nelle dimensioni normali/macOS non rimane più bloccato a 1180 px.
            width = max(self.dashboard_min_width, event.width)
            self.dashboard_canvas.itemconfigure(self.content_id, width=width)
            self.dashboard_canvas.configure(scrollregion=self.dashboard_canvas.bbox("all"))
            self.apply_responsive_layout(width)
        except Exception:
            pass

    def _mousewheel_units(self, event):
        delta = getattr(event, "delta", 0)
        if delta == 0:
            return 0
        # macOS può mandare delta piccoli; Windows usa spesso multipli di 120.
        if abs(delta) >= 120:
            return int(-delta / 120)
        return -1 if delta > 0 else 1

    def _on_dashboard_mousewheel(self, event):
        units = self._mousewheel_units(event)
        if units:
            self.dashboard_canvas.yview_scroll(units, "units")

    def _on_dashboard_shift_mousewheel(self, event):
        units = self._mousewheel_units(event)
        if units:
            self.dashboard_canvas.xview_scroll(units, "units")

    def _on_dashboard_mousewheel_linux(self, event):
        if getattr(event, "num", None) == 4:
            self.dashboard_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.dashboard_canvas.yview_scroll(1, "units")

    def _dashboard_width(self):
        """Restituisce la larghezza utile della dashboard."""
        try:
            width = self.dashboard_canvas.winfo_width()
            if width <= 1:
                width = self.root.winfo_width()
            return max(self.dashboard_min_width, int(width))
        except Exception:
            return self.dashboard_min_width

    def apply_responsive_layout(self, width=None):
        """Adatta automaticamente layout e griglie alla larghezza della finestra.

        - Wide: card su una riga e colonne laterali affiancate.
        - Medium: card su due righe, colonne ancora affiancate.
        - Compact: card su più righe e contenuto in verticale, senza perdere sezioni.
        """
        try:
            if width is None:
                width = self._dashboard_width()

            if width >= 1120:
                mode = "wide"
            elif width >= 920:
                mode = "medium"
            else:
                mode = "compact"

            if mode == self._responsive_mode:
                return

            self._responsive_mode = mode
            self._layout_topbar(mode)
            self._layout_cards(mode)
            self._layout_main_columns(mode)
        except Exception as e:
            log_errore("Errore layout responsive", e)

    def _layout_topbar(self, mode):
        if not hasattr(self, "topbar"):
            return

        for child in (getattr(self, "title_box", None), getattr(self, "actions", None)):
            if child is not None:
                try:
                    child.grid_forget()
                except Exception:
                    pass

        if mode == "compact":
            self.topbar.grid_columnconfigure(0, weight=1)
            self.topbar.grid_columnconfigure(1, weight=0)
            self.title_box.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
            self.actions.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 14))
        else:
            self.topbar.grid_columnconfigure(0, weight=1)
            self.topbar.grid_columnconfigure(1, weight=0)
            self.title_box.grid(row=0, column=0, sticky="nsew", padx=16, pady=14)
            self.actions.grid(row=0, column=1, sticky="e", padx=14, pady=14)

        self._layout_topbar_buttons(mode)

    def _layout_topbar_buttons(self, mode):
        if not hasattr(self, "top_buttons_frame") or not hasattr(self, "top_buttons"):
            return

        for btn in self.top_buttons:
            try:
                btn.grid_forget()
            except Exception:
                pass

        columns = 2 if mode == "compact" else len(self.top_buttons)
        for index, btn in enumerate(self.top_buttons):
            row = index // columns
            col = index % columns
            btn.grid(row=row, column=col, padx=4, pady=3, sticky="ew")

        for col in range(max(columns, 1)):
            self.top_buttons_frame.grid_columnconfigure(col, weight=1 if mode == "compact" else 0)

    def _layout_cards(self, mode):
        if not hasattr(self, "cards_frame") or not hasattr(self, "card_widgets"):
            return

        if mode == "wide":
            columns = 6
        elif mode == "medium":
            columns = 3
        else:
            columns = 2

        for card in self.card_widgets:
            try:
                card.grid_forget()
            except Exception:
                pass

        for index, card in enumerate(self.card_widgets):
            row = index // columns
            col = index % columns
            card.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)

        for col in range(6):
            self.cards_frame.grid_columnconfigure(col, weight=1 if col < columns else 0)

    def _layout_main_columns(self, mode):
        if not hasattr(self, "main_frame"):
            return

        try:
            self.left_col.grid_forget()
            self.right_col.grid_forget()
        except Exception:
            pass

        if mode == "compact":
            self.main_frame.grid_columnconfigure(0, weight=1)
            self.main_frame.grid_columnconfigure(1, weight=0)
            self.left_col.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 8))
            self.right_col.grid(row=1, column=0, sticky="nsew", padx=0, pady=(0, 0))
        else:
            self.main_frame.grid_columnconfigure(0, weight=42)
            self.main_frame.grid_columnconfigure(1, weight=58)
            self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
            self.right_col.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)

        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(1, weight=1 if mode == "compact" else 0)

    def make_button(self, parent, text, command, variant="dark", min_width=92):
        palette = {
            "dark": self.BTN_DARK,
            "blue": self.BTN_BLUE,
            "green": self.BTN_GREEN,
            "red": self.BTN_RED,
        }.get(variant, self.BTN_DARK)
        try:
            canvas_bg = parent.cget("bg")
        except Exception:
            canvas_bg = self.BG
        return RoundedButton(
            parent,
            text=text,
            command=command,
            bg_color=palette[0],
            hover_color=palette[1],
            active_color=palette[2],
            fg_color="#ffffff",
            canvas_bg=canvas_bg,
            radius=19,
            height=36,
            min_width=min_width,
        )

    def _widget_alive(self, attr_name):
        widget = getattr(self, attr_name, None)
        try:
            return widget is not None and bool(widget.winfo_exists())
        except Exception:
            return False

    def _popup_alive(self, attr_name):
        popup = getattr(self, attr_name, None)
        try:
            return popup is not None and bool(popup.winfo_exists())
        except Exception:
            return False

    def _focus_popup(self, attr_name):
        popup = getattr(self, attr_name, None)
        try:
            popup.lift()
            popup.focus_force()
        except Exception:
            pass

    def _center_popup(self, popup, width=900, height=640):
        try:
            self.root.update_idletasks()
            x = self.root.winfo_x() + max(40, (self.root.winfo_width() - width) // 2)
            y = self.root.winfo_y() + max(40, (self.root.winfo_height() - height) // 2)
            popup.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            popup.geometry(f"{width}x{height}")

    def build_ui(self):
        self.build_topbar()
        self.build_cards()
        self.build_main_area()
        self.build_log_area()

    def build_topbar(self):
        self.topbar = tk.Frame(self.content, bg=self.PANEL, bd=0, highlightthickness=1, highlightbackground=self.BORDER)
        self.topbar.pack(fill=tk.X, padx=14, pady=(14, 8))
        self.topbar.grid_columnconfigure(0, weight=1)

        self.title_box = tk.Frame(self.topbar, bg=self.PANEL)
        tk.Label(
            self.title_box,
            text="Quantum Bot Studio",
            bg=self.PANEL,
            fg="#f8fafc",
            font=("Helvetica", 22, "bold")
        ).pack(anchor="w")
        tk.Label(
            self.title_box,
            text=f"{APP_VERSION} · Simulazione crypto · Dashboard macOS",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica", 10)
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            self.title_box,
            text="Dashboard pulita · SQLite integrato · Auto Trade RSI",
            bg=self.PANEL,
            fg=self.BLUE,
            font=("Helvetica", 9, "bold")
        ).pack(anchor="w", pady=(6, 0))

        self.actions = tk.Frame(self.topbar, bg=self.PANEL)

        self.lbl_engine = tk.Label(
            self.actions,
            text="ENGINE: controllo...",
            bg=self.PANEL_2,
            fg=self.YELLOW,
            font=("Helvetica", 10, "bold"),
            padx=12,
            pady=5
        )
        self.lbl_engine.pack(anchor="e", pady=(0, 8))

        self.top_buttons_frame = tk.Frame(self.actions, bg=self.PANEL)
        self.top_buttons_frame.pack(anchor="e", fill=tk.X)

        self.btn_engine = self.make_button(
            self.top_buttons_frame,
            "Avvia/Ferma Bot",
            self.toggle_engine,
            "dark",
            min_width=126
        )
        btn_data = self.make_button(self.top_buttons_frame, "Cartella dati", self.apri_cartella_dati, "dark", min_width=112)

        # Dashboard pulita: nella topbar restano solo i comandi globali.
        # Registro e log sono disponibili nel pannello "Dati e strumenti" per evitare duplicati visivi.
        self.top_buttons = [self.btn_engine, btn_data]
        self._layout_topbar(self._responsive_mode or "wide")

    def build_cards(self):
        self.card_vars = {}
        self.cards_frame = tk.Frame(self.content, bg=self.BG)
        self.cards_frame.pack(fill=tk.X, padx=14, pady=6)

        # Dashboard più navigabile: tutte le card principali sono interattive.
        # Non aggiungiamo altri pulsanti: la card apre direttamente il pannello più coerente.
        cards = [
            ("saldo", "Saldo disponibile", "0,00 EUR", self.apri_card_saldo, "gestisci"),
            ("investito", "Capitale investito", "0,00 EUR", self.apri_card_posizioni, "posizioni"),
            ("valore", "Valore posizioni", "0,00 EUR", self.apri_card_posizioni, "posizioni"),
            ("patrimonio", "Patrimonio simulato", "0,00 EUR", self.apri_card_report, "report"),
            ("pl", "P/L totale", "+0,00 EUR", self.apri_card_report, "PnL"),
            ("ops", "Operazioni", "0 acquisti · 0 vendite", self.apri_movimenti_da_card, "movimenti"),
        ]

        self.card_widgets = []
        for key, label, value, command, hint in cards:
            var = tk.StringVar(value=value)
            self.card_vars[key] = var
            card = RoundedMetricCard(
                self.cards_frame,
                label=label,
                value_var=var,
                panel_bg=self.PANEL_3,
                canvas_bg=self.BG,
                muted_fg=self.MUTED,
                value_fg="#f8fafc",
                command=command,
                hint=hint,
            )
            self.card_widgets.append(card)

        self._layout_cards(self._responsive_mode or "wide")

    def build_main_area(self):
        # Dashboard compatta: nella finestra principale restano comandi, riepilogo
        # e grafico. Le tabelle pesanti sono disponibili in popup dedicati.
        self.main_frame = tk.Frame(self.content, bg=self.BG)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=6)

        self.left_col = tk.Frame(self.main_frame, bg=self.BG)
        self.right_col = tk.Frame(self.main_frame, bg=self.BG)

        self.build_compact_summary(self.left_col)
        self.build_trading_panel(self.left_col)
        self.build_popup_launcher(self.left_col)
        self.build_chart_panel(self.right_col)

        self._layout_main_columns(self._responsive_mode or "wide")

    def build_compact_summary(self, parent):
        lf = self.make_labelframe(parent, "Riepilogo crypto selezionata")
        lf.pack(fill=tk.X, pady=(0, 8))

        self.var_selected_title = tk.StringVar(value="BTC/EUR")
        self.var_selected_price = tk.StringVar(value="Prezzo: -")
        self.var_selected_rsi = tk.StringVar(value="RSI: -")
        self.var_selected_var = tk.StringVar(value="Var.: -")
        self.var_selected_source = tk.StringVar(value="Fonte: -")
        self.var_selected_position = tk.StringVar(value="Posizione: no")

        title = tk.Label(
            lf,
            textvariable=self.var_selected_title,
            bg=self.PANEL,
            fg="#f8fafc",
            font=("Helvetica", 18, "bold")
        )
        title.pack(anchor="w", padx=14, pady=(8, 2))

        grid = tk.Frame(lf, bg=self.PANEL)
        grid.pack(fill=tk.X, padx=10, pady=(4, 10))

        items = [
            self.var_selected_price,
            self.var_selected_rsi,
            self.var_selected_var,
            self.var_selected_source,
            self.var_selected_position,
        ]
        for idx, var in enumerate(items):
            pill = tk.Label(
                grid,
                textvariable=var,
                bg=self.PANEL_2,
                fg=self.TEXT,
                font=("Helvetica", 10, "bold"),
                padx=10,
                pady=6
            )
            pill.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=4)

        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

    def build_popup_launcher(self, parent):
        lf = self.make_labelframe(parent, "Dati e strumenti")
        lf.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            lf,
            text="Le card sopra sono interattive: Saldo, Posizioni, Patrimonio/P&L e Movimenti. Qui restano solo gli strumenti secondari.",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica", 9),
            wraplength=420,
            justify=tk.LEFT
        ).pack(anchor="w", padx=12, pady=(8, 6))

        grid = tk.Frame(lf, bg=self.PANEL)
        grid.pack(fill=tk.X, padx=8, pady=(0, 10))

        buttons = [
            ("Watchlist", self.apri_popup_watchlist, "blue", 112),
            ("Strategia", self.apri_popup_strategia, "green", 108),
            ("Log sistema", self.apri_popup_log, "dark", 112),
        ]

        for idx, (label, command, variant, width) in enumerate(buttons):
            btn = self.make_button(grid, label, command, variant, min_width=width)
            btn.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=4, pady=4)

        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

    def aggiorna_riepilogo_crypto_selezionata(self):
        if not hasattr(self, "var_selected_title"):
            return

        simbolo = self.crypto_corrente()
        watchlist = self.status.get("watchlist", {})
        data = watchlist.get(simbolo, {})

        price = safe_float(data.get("price", 0.0))
        rsi = safe_float(data.get("rsi", 0.0))
        change = safe_float(data.get("change_pct", 0.0))
        source = str(data.get("source", "N/D")).replace("/USDT", "/USDT*")
        positions = {p.get("simbolo"): p for p in self.status.get("positions", [])}
        pos = positions.get(simbolo)

        self.var_selected_title.set(simbolo)
        self.var_selected_price.set(f"Prezzo: {format_price(price)}")
        self.var_selected_rsi.set(f"RSI: {rsi:.1f}")
        self.var_selected_var.set(f"Var.: {format_pct(change)}")
        self.var_selected_source.set(f"Fonte: {source}")

        if pos:
            pl = safe_float(pos.get("pl_eur", 0.0))
            pl_pct = safe_float(pos.get("pl_pct", 0.0))
            self.var_selected_position.set(f"Posizione: sì · P/L {pl:+.2f} EUR ({pl_pct:+.2f}%)")
        else:
            self.var_selected_position.set("Posizione: no")

    def apri_popup_watchlist(self):
        if self._popup_alive("_popup_watchlist"):
            self._focus_popup("_popup_watchlist")
            return

        win = tk.Toplevel(self.root)
        self._popup_watchlist = win
        win.title("Watchlist completa")
        win.configure(bg=self.BG)
        self._center_popup(win, 760, 560)

        def on_close():
            self.tabella_listino = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = tk.Frame(frame, bg=self.BG)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="Watchlist completa", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(side=tk.LEFT)
        self.make_button(header, "Aggiorna", self.aggiorna_tabelle, "dark", min_width=90).pack(side=tk.RIGHT)

        cols = ("prezzo", "rsi", "var", "fonte", "pos")
        self.tabella_listino = ttk.Treeview(frame, columns=cols, show="tree headings", height=18)
        self.tabella_listino.heading("#0", text="Crypto")
        self.tabella_listino.heading("prezzo", text="Prezzo")
        self.tabella_listino.heading("rsi", text="RSI")
        self.tabella_listino.heading("var", text="Var.")
        self.tabella_listino.heading("fonte", text="Fonte")
        self.tabella_listino.heading("pos", text="Pos.")
        self.tabella_listino.column("#0", width=90, stretch=False)
        self.tabella_listino.column("prezzo", width=130, stretch=False, anchor="e")
        self.tabella_listino.column("rsi", width=70, stretch=False, anchor="e")
        self.tabella_listino.column("var", width=80, stretch=False, anchor="e")
        self.tabella_listino.column("fonte", width=120, stretch=True)
        self.tabella_listino.column("pos", width=60, stretch=False, anchor="center")
        self.tabella_listino.tag_configure("positivo", foreground=self.GREEN)
        self.tabella_listino.tag_configure("negativo", foreground=self.RED)
        self.tabella_listino.tag_configure("neutro", foreground="#f0f6fc")
        self.tabella_listino.bind("<ButtonRelease-1>", self.seleziona_crypto_da_lista)

        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=self.tabella_listino.yview)
        self.tabella_listino.configure(yscrollcommand=scroll_y.set)
        self.tabella_listino.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.aggiorna_tabelle()

    def apri_popup_portafoglio(self):
        if self._popup_alive("_popup_portafoglio"):
            self._focus_popup("_popup_portafoglio")
            return

        win = tk.Toplevel(self.root)
        self._popup_portafoglio = win
        win.title("Portafoglio simulato")
        win.configure(bg=self.BG)
        self._center_popup(win, 860, 560)

        def on_close():
            self.tabella_pancia = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = tk.Frame(frame, bg=self.BG)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="Portafoglio simulato", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(side=tk.LEFT)
        self.make_button(header, "Vendi 25%", lambda: self.vendi_percentuale(25), "dark", min_width=90).pack(side=tk.RIGHT, padx=3)
        self.make_button(header, "Vendi 50%", lambda: self.vendi_percentuale(50), "dark", min_width=90).pack(side=tk.RIGHT, padx=3)
        self.make_button(header, "Vendi 100%", lambda: self.vendi_percentuale(100), "red", min_width=96).pack(side=tk.RIGHT, padx=3)

        cols = ("entry", "qty", "capitale", "valore", "pl", "plpct")
        self.tabella_pancia = ttk.Treeview(frame, columns=cols, show="tree headings", height=16)
        self.tabella_pancia.heading("#0", text="Crypto")
        self.tabella_pancia.heading("entry", text="Entry")
        self.tabella_pancia.heading("qty", text="Quantità")
        self.tabella_pancia.heading("capitale", text="Capitale")
        self.tabella_pancia.heading("valore", text="Valore")
        self.tabella_pancia.heading("pl", text="P/L")
        self.tabella_pancia.heading("plpct", text="%")
        self.tabella_pancia.column("#0", width=90, stretch=False)
        self.tabella_pancia.column("entry", width=110, anchor="e", stretch=False)
        self.tabella_pancia.column("qty", width=110, anchor="e", stretch=False)
        self.tabella_pancia.column("capitale", width=110, anchor="e", stretch=False)
        self.tabella_pancia.column("valore", width=110, anchor="e", stretch=False)
        self.tabella_pancia.column("pl", width=110, anchor="e", stretch=False)
        self.tabella_pancia.column("plpct", width=80, anchor="e", stretch=False)
        self.tabella_pancia.tag_configure("positivo", foreground=self.GREEN)
        self.tabella_pancia.tag_configure("negativo", foreground=self.RED)
        self.tabella_pancia.bind("<ButtonRelease-1>", self.seleziona_posizione_popup)

        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=self.tabella_pancia.yview)
        self.tabella_pancia.configure(yscrollcommand=scroll_y.set)
        self.tabella_pancia.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

        self.aggiorna_tabelle()

    def seleziona_posizione_popup(self, event=None):
        if not self._widget_alive("tabella_pancia"):
            return
        selected = self.tabella_pancia.selection()
        if selected:
            simbolo = selected[0]
            self.crypto_selezionata_grafico = simbolo
            self.var_crypto.set(simbolo)
            self.aggiorna_riepilogo_crypto_selezionata()
            self.aggiorna_grafici()

    def apri_popup_storico(self, tab=None):
        if self._popup_alive("_popup_storico"):
            self._focus_popup("_popup_storico")
            self.seleziona_tab_movimenti(tab)
            return

        win = tk.Toplevel(self.root)
        self._popup_storico = win
        win.title("Storico operazioni")
        win.configure(bg=self.BG)
        self._center_popup(win, 1120, 720)

        def on_close():
            self.tabella_registro = None
            self.tabella_acquisti = None
            self.tabella_vendite = None
            self.tabella_storico_dashboard = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        self.build_registry_panel(frame)
        self.build_trade_history_panel(frame)
        self.make_button(frame, "Genera report SQLite", self.genera_report_sqlite_popup, "blue", min_width=170).pack(anchor="e", padx=8, pady=(0, 8))
        self.aggiorna_registro()
        self.aggiorna_storico_trade()
        self.seleziona_tab_movimenti(tab)

    def apri_popup_gestione_fondi(self):
        if self._popup_alive("_popup_fondi"):
            self._focus_popup("_popup_fondi")
            return

        win = tk.Toplevel(self.root)
        self._popup_fondi = win
        win.title("Gestione fondi simulati")
        win.configure(bg=self.BG)
        self._center_popup(win, 520, 300)

        def on_close():
            self._popup_fondi = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        tk.Label(frame, text="Gestione fondi simulati", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(anchor="w", pady=(0, 8))

        balances = dict(getattr(self, "last_valid_dashboard_state", {}) or {})
        if not dashboard_state_ha_dati_reali(balances):
            balances = stima_balances_dashboard_da_config(self.cfg, self.status)

        saldo = safe_float(balances.get("saldo_eur", self.cfg.get("saldo_eur", 0.0)))
        investito = safe_float(balances.get("investito", 0.0))
        valore = safe_float(balances.get("valore_posizioni", 0.0))
        patrimonio = safe_float(balances.get("patrimonio", saldo + valore))

        summary = tk.Label(
            frame,
            text=(
                f"Saldo disponibile: {format_eur(saldo)}\n"
                f"Capitale investito: {format_eur(investito)}\n"
                f"Valore posizioni: {format_eur(valore)}\n"
                f"Patrimonio simulato: {format_eur(patrimonio)}"
            ),
            bg=self.PANEL,
            fg=self.TEXT,
            justify=tk.LEFT,
            anchor="w",
            padx=12,
            pady=12,
        )
        summary.pack(fill=tk.X, pady=(0, 12))

        buttons = tk.Frame(frame, bg=self.BG)
        buttons.pack(fill=tk.X)
        self.make_button(buttons, "Aggiungi fondi", self.aggiungi_fondi_popup, "green", min_width=140).pack(side=tk.LEFT, padx=(0, 6))
        self.make_button(buttons, "Chiudi", on_close, "dark", min_width=90).pack(side=tk.RIGHT)

    def apri_card_saldo(self, event=None):
        """Card Saldo disponibile: apre la gestione saldo/fondi simulati."""
        try:
            self.scrivi_log("[UI] Card Saldo cliccata: apertura gestione fondi.")
        except Exception:
            pass
        self.apri_popup_gestione_fondi()

    def apri_card_posizioni(self, event=None):
        """Card Capitale investito / Valore posizioni: apre il portafoglio aperto."""
        try:
            self.scrivi_log("[UI] Card Posizioni cliccata: apertura portafoglio simulato.")
        except Exception:
            pass
        self.apri_popup_portafoglio()

    def apri_card_report(self, event=None):
        """Card Patrimonio / P&L: apre report SQLite con equity, PnL e drawdown."""
        try:
            self.scrivi_log("[UI] Card Report/PnL cliccata: apertura report SQLite.")
        except Exception:
            pass
        self.apri_popup_report_sqlite()

    def apri_movimenti_da_card(self, event=None):
        """Apre lo storico movimenti dalla card Operazioni.

        Click nella metà sinistra: tab Acquisti.
        Click nella metà destra: tab Vendite.
        Così la card in alto resta compatta ma diventa navigabile.
        """
        tab = "acquisti"
        try:
            if event is not None and event.x >= event.widget.winfo_width() * 0.52:
                tab = "vendite"
        except Exception:
            tab = "acquisti"
        self.apri_popup_storico(tab=tab)

    def seleziona_tab_movimenti(self, tab=None):
        if not tab or not self._widget_alive("notebook_trade"):
            return
        tab_norm = str(tab).strip().lower()
        try:
            if "vend" in tab_norm:
                self.notebook_trade.select(1)
            elif "acq" in tab_norm or "buy" in tab_norm:
                self.notebook_trade.select(0)
        except Exception as e:
            log_errore("Errore selezione tab movimenti", e)

    def genera_e_apri_report_sqlite_file(self, path: Path):
        """Rigenera report/grafici SQLite e apre subito il file richiesto.

        Questo evita di aprire un PNG vuoto se il grafico non è ancora stato creato
        o se il database è stato aggiornato dopo l'ultima generazione.
        """
        try:
            genera_file_report_sqlite()
            self.scrivi_log(f"[SQLITE] Report aggiornato. Apro: {Path(path).name}")
            self.apri_file(path)
        except Exception as e:
            log_errore(f"Errore apertura grafico/report SQLite {path}", e)
            messagebox.showerror("Report SQLite", f"Errore durante apertura/generazione file:\n{Path(path).name}\n\n{e}")

    def apri_popup_report_sqlite(self):
        if self._popup_alive("_popup_report_sqlite"):
            self._focus_popup("_popup_report_sqlite")
            return

        win = tk.Toplevel(self.root)
        self._popup_report_sqlite = win
        win.title("Report SQLite")
        win.configure(bg=self.BG)
        self._center_popup(win, 1120, 760)

        def on_close():
            self.canvas_report_sqlite = None
            self.fig_report_sqlite = None
            self.ax_report_equity = None
            self.ax_report_pnl = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = tk.Frame(frame, bg=self.BG)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="Report SQLite", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(side=tk.LEFT)
        self.make_button(header, "Aggiorna report", self.aggiorna_popup_report_sqlite, "blue", min_width=140).pack(side=tk.RIGHT, padx=3)
        self.make_button(header, "Cartella dati", self.apri_cartella_dati, "dark", min_width=120).pack(side=tk.RIGHT, padx=3)

        self.var_report_sqlite_summary = tk.StringVar(value="Report non ancora caricato.")
        lbl = tk.Label(
            frame,
            textvariable=self.var_report_sqlite_summary,
            bg=self.PANEL,
            fg=self.TEXT,
            font=("Helvetica", 10),
            justify=tk.LEFT,
            anchor="w",
            padx=12,
            pady=10,
        )
        lbl.pack(fill=tk.X, pady=(0, 8))

        buttons = tk.Frame(frame, bg=self.BG)
        buttons.pack(fill=tk.X, pady=(0, 8))
        self.make_button(buttons, "Apri TXT", lambda: self.apri_file(FILE_REPORT_TXT), "dark", min_width=100).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Apri CSV", lambda: self.apri_file(FILE_REPORT_CSV), "dark", min_width=100).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Apri JSON", lambda: self.apri_file(FILE_REPORT_JSON), "dark", min_width=104).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Grafico equity PNG", lambda: self.apri_file(FILE_REPORT_EQUITY_PNG), "green", min_width=150).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Grafico PnL PNG", lambda: self.apri_file(FILE_REPORT_PNL_CRYPTO_PNG), "green", min_width=140).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Drawdown PNG", lambda: self.apri_file(FILE_REPORT_DRAWDOWN_PNG), "dark", min_width=126).pack(side=tk.LEFT, padx=3)

        self.fig_report_sqlite = Figure(figsize=(9.8, 5.8), dpi=100, facecolor=self.BG)
        self.ax_report_equity = self.fig_report_sqlite.add_subplot(211, facecolor=self.PANEL_2)
        self.ax_report_pnl = self.fig_report_sqlite.add_subplot(212, facecolor=self.PANEL_2)
        self.fig_report_sqlite.tight_layout(pad=2.0)

        self.canvas_report_sqlite = FigureCanvasTkAgg(self.fig_report_sqlite, master=frame)
        self.canvas_report_sqlite.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=2, pady=(0, 2))

        self.aggiorna_popup_report_sqlite()

    def aggiorna_popup_report_sqlite(self):
        try:
            self._report_busy = True
            try:
                if hasattr(self, "var_report_sqlite_summary"):
                    self.var_report_sqlite_summary.set("Generazione report in corso… la dashboard mantiene l'ultimo stato valido.")
                self.root.update_idletasks()
            except Exception:
                pass
            report = genera_file_report_sqlite()
            # UI-DELAY FIX: il report è read-only rispetto alla dashboard principale.
            # Non ricarichiamo config/status e non chiamiamo aggiorna_header() da qui,
            # così evitiamo il lampeggio 5.000/0 posizioni mentre l'engine scrive status_bot.json.
            dati = leggi_dati_grafici_sqlite()
            snap = report.get("ultimo_snapshot") or {}
            summary = (
                f"File database: {FILE_DB}\n"
                f"Generato: {report.get('generato_il')}   |   Trade totali: {report.get('trade_totali', 0)}   |   "
                f"Vendite: {report.get('vendite', 0)}   |   Win rate: {report.get('win_rate_pct', 0.0):.2f}%\n"
                f"PnL realizzato: {format_signed_eur(report.get('pnl_realizzato', 0.0))}   |   "
                f"Commissioni: {format_eur(report.get('commissioni_totali', 0.0))}   |   "
                f"Moltiplicatore da 5000 EUR: x{report.get('moltiplicatore_da_5000', 0.0):.3f}   |   "
                f"Controllo coerenza: {report.get('audit_coerenza', report.get('audit_x3'))}"
            )
            if snap:
                summary += (
                    f"\nUltima equity: {format_eur(snap.get('equity_totale', 0.0))}   |   "
                    f"Valore posizioni: {format_eur(snap.get('valore_posizioni', 0.0))}   |   "
                    f"PnL non realizzato: {format_signed_eur(snap.get('profitto_non_realizzato', 0.0))}   |   "
                    f"Drawdown: {safe_float(snap.get('drawdown_pct', 0.0)):.2f}%"
                )
            self.var_report_sqlite_summary.set(summary)

            self.ax_report_equity.clear()
            self.ax_report_pnl.clear()

            equity = dati.get("equity") or []
            if equity:
                labels = [_short_datetime_label(r.get("data_ora")) for r in equity]
                x = list(range(len(equity)))
                y_equity = [safe_float(r.get("equity_totale")) for r in equity]
                y_cash = [safe_float(r.get("saldo_cash")) for r in equity]
                y_pos = [safe_float(r.get("valore_posizioni")) for r in equity]
                self.ax_report_equity.plot(x, y_equity, color=self.GREEN, linewidth=2.0, label="Equity")
                self.ax_report_equity.plot(x, y_cash, color=self.BLUE, linewidth=1.3, label="Cash")
                self.ax_report_equity.plot(x, y_pos, color=self.YELLOW, linewidth=1.3, label="Posizioni")
                step = max(1, len(labels) // 7)
                ticks = list(range(0, len(labels), step))
                if ticks and ticks[-1] != len(labels) - 1:
                    ticks.append(len(labels) - 1)
                self.ax_report_equity.set_xticks(ticks)
                self.ax_report_equity.set_xticklabels([labels[i] for i in ticks], rotation=20, ha="right")
                self.ax_report_equity.set_title("Equity totale da SQLite", fontsize=9, fontweight="bold")
                legend = self.ax_report_equity.legend(fontsize=7, loc="best", facecolor=self.PANEL, edgecolor=self.BORDER)
                for text in legend.get_texts():
                    text.set_color(self.TEXT)
            else:
                self.ax_report_equity.text(0.5, 0.5, "Nessuno snapshot equity disponibile", ha="center", va="center", color=self.MUTED, transform=self.ax_report_equity.transAxes)
                self.ax_report_equity.set_title("Equity totale da SQLite", fontsize=9, fontweight="bold")
            self.style_axis(self.ax_report_equity)

            pnl_rows = list(reversed((dati.get("pnl_crypto") or [])[:10]))
            if pnl_rows:
                symbols = [str(r.get("crypto") or "-") for r in pnl_rows]
                pnl = [safe_float(r.get("pnl")) for r in pnl_rows]
                colors = [self.GREEN if v >= 0 else self.RED for v in pnl]
                self.ax_report_pnl.barh(range(len(symbols)), pnl, color=colors)
                self.ax_report_pnl.set_yticks(range(len(symbols)))
                self.ax_report_pnl.set_yticklabels(symbols)
                self.ax_report_pnl.axvline(0, color=self.MUTED, linewidth=0.8)
                self.ax_report_pnl.set_title("PnL realizzato per crypto", fontsize=9, fontweight="bold")
            else:
                self.ax_report_pnl.text(0.5, 0.5, "Nessuna vendita chiusa disponibile", ha="center", va="center", color=self.MUTED, transform=self.ax_report_pnl.transAxes)
                self.ax_report_pnl.set_title("PnL realizzato per crypto", fontsize=9, fontweight="bold")
            self.style_axis(self.ax_report_pnl)

            self.fig_report_sqlite.tight_layout(pad=2.0)
            self.canvas_report_sqlite.draw_idle()
            self.scrivi_log("[SQLITE] Report e grafici aggiornati senza refresh delle card principali.")
        except Exception as e:
            log_errore("Errore aggiornamento popup report SQLite", e)
            messagebox.showerror("Report SQLite", f"Errore durante l'aggiornamento report:\n{e}")
        finally:
            self._report_busy = False

    def genera_report_sqlite_popup(self):
        try:
            report = genera_file_report_sqlite()
            # CARD/REPORT FIX: il report non deve aggiornare le card principali.
            # La dashboard continuerà ad aggiornarsi dal refresh normale dell'engine.
            msg = (
                f"Report SQLite generato.\n\n"
                f"Trade totali: {report.get('trade_totali', 0)}\n"
                f"PnL realizzato: {report.get('pnl_realizzato', 0.0):+.2f} EUR\n"
                f"Commissioni: {report.get('commissioni_totali', 0.0):.2f} EUR\n"
                f"Win rate: {report.get('win_rate_pct', 0.0):.2f}%\n\n"
                f"File creati nella cartella del bot:\n"
                f"- {FILE_REPORT_TXT.name}\n"
                f"- {FILE_REPORT_JSON.name}\n"
                f"- {FILE_REPORT_CSV.name}\n"
                f"- {FILE_REPORT_EQUITY_PNG.name}\n"
                f"- {FILE_REPORT_PNL_CRYPTO_PNG.name}\n"
                f"- {FILE_REPORT_DRAWDOWN_PNG.name}"
            )
            messagebox.showinfo("Report SQLite", msg)
            self.scrivi_log("[SQLITE] Report generato.")
        except Exception as e:
            log_errore("Errore report SQLite da dashboard", e)
            messagebox.showerror("Report SQLite", f"Errore durante la generazione del report:\n{e}")

    def apri_popup_grafici(self):
        if self._popup_alive("_popup_grafici"):
            self._focus_popup("_popup_grafici")
            return

        win = tk.Toplevel(self.root)
        self._popup_grafici = win
        win.title("Grafici avanzati")
        win.configure(bg=self.BG)
        self._center_popup(win, 520, 390)

        tk.Label(win, text="Grafici avanzati", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
        tk.Label(
            win,
            text="Usa questi comandi per il grafico live oppure apri direttamente i grafici generati da SQLite.",
            bg=self.BG,
            fg=self.MUTED,
            wraplength=360,
            justify=tk.LEFT
        ).pack(anchor="w", padx=16, pady=(0, 12))

        auto_frame = tk.Frame(win, bg=self.PANEL, highlightthickness=1, highlightbackground=self.BORDER)
        auto_frame.pack(fill=tk.X, padx=16, pady=(0, 10))
        tk.Label(
            auto_frame,
            text="Auto-refresh PNG SQLite sempre attivo",
            bg=self.PANEL,
            fg=self.GREEN,
            font=("Helvetica", 9, "bold"),
        ).pack(side=tk.LEFT, padx=8, pady=8)
        tk.Label(auto_frame, text="Intervallo", bg=self.PANEL, fg=self.MUTED, font=("Helvetica", 9)).pack(side=tk.LEFT, padx=(8, 4))
        combo_refresh = ttk.Combobox(
            auto_frame,
            values=("30", "60", "120"),
            textvariable=self.var_auto_refresh_grafici_interval,
            width=5,
            state="readonly",
        )
        combo_refresh.pack(side=tk.LEFT, padx=4)
        combo_refresh.bind("<<ComboboxSelected>>", lambda _e: self.on_auto_refresh_interval_changed())
        tk.Label(auto_frame, text="sec", bg=self.PANEL, fg=self.MUTED, font=("Helvetica", 9)).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(auto_frame, textvariable=self.var_auto_refresh_grafici_status, bg=self.PANEL, fg=self.MUTED, font=("Helvetica", 8)).pack(side=tk.RIGHT, padx=8)

        grid = tk.Frame(win, bg=self.BG)
        grid.pack(fill=tk.X, padx=12, pady=8)
        self.make_button(grid, "Linea", lambda: self.set_modalita_grafico("linea"), "dark", min_width=90).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Candele", lambda: self.set_modalita_grafico("candele"), "dark", min_width=90).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Confronta crypto", self.seleziona_confronto_crypto, "blue", min_width=150).grid(row=1, column=0, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Singola crypto", self.disattiva_confronto_crypto, "dark", min_width=130).grid(row=1, column=1, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Equity SQLite", lambda: self.genera_e_apri_report_sqlite_file(FILE_REPORT_EQUITY_PNG), "green", min_width=150).grid(row=2, column=0, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "PnL per crypto", lambda: self.genera_e_apri_report_sqlite_file(FILE_REPORT_PNL_CRYPTO_PNG), "green", min_width=150).grid(row=2, column=1, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Drawdown SQLite", lambda: self.genera_e_apri_report_sqlite_file(FILE_REPORT_DRAWDOWN_PNG), "dark", min_width=150).grid(row=3, column=0, padx=4, pady=4, sticky="ew")
        self.make_button(grid, "Report completo", self.apri_popup_report_sqlite, "blue", min_width=150).grid(row=3, column=1, padx=4, pady=4, sticky="ew")
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

    def _auto_refresh_interval_ms(self):
        try:
            seconds = int(str(self.var_auto_refresh_grafici_interval.get()).strip())
        except Exception:
            seconds = 60
        seconds = min(600, max(10, seconds))
        return seconds * 1000

    def toggle_auto_refresh_grafici(self):
        """Mantiene l'auto-refresh dei PNG SQLite sempre attivo.

        Dalla v1.0.18 l'utente non può spegnerlo dalla dashboard, così i PNG
        restano aggiornati senza rischio di dimenticare il toggle. Questa funzione
        resta per compatibilità con eventuali vecchi callback, ma forza sempre ON.
        """
        self.var_auto_refresh_grafici.set(True)
        self.var_auto_refresh_grafici_status.set("Auto-refresh grafici: ON · sempre attivo")
        self._schedule_auto_refresh_grafici(immediate=True)

    def on_auto_refresh_interval_changed(self):
        self.var_auto_refresh_grafici.set(True)
        self._cancel_auto_refresh_grafici()
        self.var_auto_refresh_grafici_status.set(
            f"Auto-refresh grafici: ON · ogni {int(self._auto_refresh_interval_ms()/1000)}s"
        )
        self._schedule_auto_refresh_grafici(immediate=False)

    def _cancel_auto_refresh_grafici(self):
        if self._charts_autorefresh_after_id is not None:
            try:
                self.root.after_cancel(self._charts_autorefresh_after_id)
            except Exception:
                pass
            self._charts_autorefresh_after_id = None

    def _schedule_auto_refresh_grafici(self, immediate=False):
        self._cancel_auto_refresh_grafici()
        if self._closed:
            return
        self.var_auto_refresh_grafici.set(True)
        delay = 250 if immediate else self._auto_refresh_interval_ms()
        self._charts_autorefresh_after_id = self.root.after(delay, self._auto_refresh_grafici_tick)

    def _auto_refresh_grafici_tick(self):
        if self._closed:
            return
        self.var_auto_refresh_grafici.set(True)
        try:
            # Read-only rispetto a bot/engine/config/status. Genera soltanto PNG.
            genera_grafici_sqlite_png(sync_csv=False)
            ts = datetime.now().strftime("%H:%M:%S")
            interval = int(self._auto_refresh_interval_ms() / 1000)
            self.var_auto_refresh_grafici_status.set(f"Auto-refresh grafici: ON · ultimo {ts} · ogni {interval}s")
        except Exception as e:
            log_errore("Errore auto-refresh grafici SQLite", e)
            self.var_auto_refresh_grafici_status.set("Auto-refresh grafici: errore, vedi log")
        finally:
            self._schedule_auto_refresh_grafici(immediate=False)

    def apri_popup_strategia(self):
        if self._popup_alive("_popup_strategia"):
            self._focus_popup("_popup_strategia")
            return

        win = tk.Toplevel(self.root)
        self._popup_strategia = win
        win.title("Strategia e automazioni")
        win.configure(bg=self.BG)
        self._center_popup(win, 520, 420)

        tk.Label(win, text="Strategia e automazioni", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
        tk.Label(
            win,
            text="Gestisci modalità trading, Auto Trade RSI, Auto P/L, percentuale investimento e timeframe.",
            bg=self.BG,
            fg=self.MUTED,
            wraplength=460,
            justify=tk.LEFT
        ).pack(anchor="w", padx=16, pady=(0, 12))

        grid = tk.Frame(win, bg=self.BG)
        grid.pack(fill=tk.X, padx=12, pady=8)

        actions = [
            ("Modalità trading", self.configura_modalita_trading, "green", 160),
            ("Auto Trade RSI", self.configura_auto_trade, "blue", 150),
            ("Auto Profit/Loss", self.configura_auto_profit_loss, "blue", 160),
            ("Percentuale investimento", self.configura_percentuale_investimento, "dark", 190),
            ("Usa % saldo", self.usa_percentuale_investimento, "dark", 130),
            ("Aggiungi fondi", self.aggiungi_fondi_popup, "green", 140),
        ]

        for idx, (label, command, variant, width) in enumerate(actions):
            self.make_button(grid, label, command, variant, min_width=width).grid(
                row=idx // 2,
                column=idx % 2,
                sticky="ew",
                padx=4,
                pady=5
            )

        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        info = tk.Label(
            win,
            textvariable=self.var_auto_trade_status,
            bg=self.PANEL_2,
            fg=self.YELLOW,
            font=("Helvetica", 10, "bold"),
            padx=12,
            pady=8
        )
        info.pack(fill=tk.X, padx=16, pady=(12, 4))

    def apri_popup_log(self):
        if self._popup_alive("_popup_log"):
            self._focus_popup("_popup_log")
            return

        win = tk.Toplevel(self.root)
        self._popup_log = win
        win.title("Log sistema")
        win.configure(bg=self.BG)
        self._center_popup(win, 940, 640)

        frame = tk.Frame(win, bg=self.BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = tk.Frame(frame, bg=self.BG)
        header.pack(fill=tk.X, pady=(0, 8))
        tk.Label(header, text="Log sistema", bg=self.BG, fg=self.TEXT, font=("Helvetica", 16, "bold")).pack(side=tk.LEFT)

        text_widget = tk.Text(
            frame,
            bg=self.PANEL_2,
            fg="#d1fae5",
            insertbackground="#d1fae5",
            font=("Courier New", 10),
            bd=0,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.BORDER
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scroll.set)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def carica_log(path):
            text_widget.delete("1.0", tk.END)
            try:
                if Path(path).exists():
                    content = Path(path).read_text(encoding="utf-8", errors="replace")
                    text_widget.insert(tk.END, content[-30000:] if content else "File vuoto.")
                else:
                    try:
                        Path(path).parent.mkdir(parents=True, exist_ok=True)
                        Path(path).touch(exist_ok=True)
                        text_widget.insert(tk.END, "File creato ora. Nessun errore registrato." if Path(path).name == FILE_ERRORI.name else "File creato ora. Nessun contenuto registrato.")
                    except Exception:
                        text_widget.insert(tk.END, f"File non trovato: {path}")
            except Exception as e:
                text_widget.insert(tk.END, f"Errore lettura log: {e}")

        buttons = tk.Frame(header, bg=self.BG)
        buttons.pack(side=tk.RIGHT)
        self.make_button(buttons, "Eventi", lambda: carica_log(FILE_EVENTI), "dark", min_width=80).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Errori", lambda: carica_log(FILE_ERRORI), "red", min_width=80).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Engine err", lambda: carica_log(FILE_ENGINE_STDERR), "dark", min_width=100).pack(side=tk.LEFT, padx=3)
        self.make_button(buttons, "Engine out", lambda: carica_log(FILE_ENGINE_STDOUT), "dark", min_width=100).pack(side=tk.LEFT, padx=3)

        carica_log(FILE_EVENTI)


    def make_labelframe(self, parent, text):
        # Pannello moderno: bordo sottile, titolo a pillola e più respiro interno.
        # Mantiene la compatibilità con i widget Tkinter esistenti senza cambiare la logica.
        panel = tk.Frame(parent, bg=self.PANEL, bd=0, highlightthickness=1, highlightbackground=self.BORDER)
        header = tk.Frame(panel, bg=self.PANEL)
        header.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(
            header,
            text=text.upper(),
            bg=self.PANEL_3,
            fg=self.BLUE,
            font=("Helvetica", 9, "bold"),
            padx=10,
            pady=4,
        ).pack(anchor="w")
        return panel

    def build_watchlist(self, parent):
        lf = self.make_labelframe(parent, "Watchlist")
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        cols = ("prezzo", "rsi", "var", "fonte", "pos")
        self.tabella_listino = ttk.Treeview(lf, columns=cols, show="tree headings", height=10)
        self.tabella_listino.heading("#0", text="Crypto")
        self.tabella_listino.heading("prezzo", text="Prezzo")
        self.tabella_listino.heading("rsi", text="RSI")
        self.tabella_listino.heading("var", text="Var.")
        self.tabella_listino.heading("fonte", text="Fonte")
        self.tabella_listino.heading("pos", text="Pos.")
        self.tabella_listino.column("#0", width=86, stretch=False)
        self.tabella_listino.column("prezzo", width=110, stretch=False, anchor="e")
        self.tabella_listino.column("rsi", width=54, stretch=False, anchor="e")
        self.tabella_listino.column("var", width=60, stretch=False, anchor="e")
        self.tabella_listino.column("fonte", width=80, stretch=True)
        self.tabella_listino.column("pos", width=45, stretch=False, anchor="center")
        self.tabella_listino.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)

        scroll = ttk.Scrollbar(lf, orient="vertical", command=self.tabella_listino.yview)
        self.tabella_listino.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=8)

        self.tabella_listino.tag_configure("positivo", foreground=self.GREEN)
        self.tabella_listino.tag_configure("negativo", foreground=self.RED)
        self.tabella_listino.tag_configure("neutro", foreground="#f0f6fc")
        self.tabella_listino.bind("<ButtonRelease-1>", self.seleziona_crypto_da_lista)

    def build_trading_panel(self, parent):
        lf = self.make_labelframe(parent, "Trading simulato")
        lf.pack(fill=tk.X, pady=(0, 8))

        row1 = tk.Frame(lf, bg=self.PANEL)
        row1.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(row1, text="Crypto", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        self.combo_crypto = ttk.Combobox(row1, textvariable=self.var_crypto, values=[simbolo_visuale(b) for b in self.cfg["crypto_base_list"]], state="readonly", width=11)
        self.combo_crypto.pack(side=tk.LEFT, padx=(6, 12))
        self.combo_crypto.bind("<<ComboboxSelected>>", self.seleziona_crypto_combo)

        tk.Label(row1, text="Importo EUR", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        entry = tk.Entry(row1, textvariable=self.var_importo, bg=self.PANEL_2, fg="#f8fafc", insertbackground="#f8fafc", width=10, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground=self.BORDER, highlightcolor=self.BLUE)
        entry.pack(side=tk.LEFT, padx=(6, 12))

        # UI guard v1.0.18: i pulsanti Compra/Vendi EUR stanno su una riga
        # dedicata e sempre visibile. Prima erano sulla stessa riga di Crypto
        # e Importo EUR; su finestre più strette macOS/Tk poteva nascondere
        # Vendi EUR fuori dallo spazio visibile, pur essendo presente nel codice.
        row1_actions = tk.Frame(lf, bg=self.PANEL)
        row1_actions.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Label(row1_actions, text="Azioni importo", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        self.btn_buy = self.make_button(row1_actions, "Compra", self.compra_da_entry, "green", min_width=96)
        self.btn_buy.pack(side=tk.LEFT, padx=(8, 3))
        self.btn_sell_eur_main = self.make_button(row1_actions, "Vendi EUR", self.vendi_importo_eur_da_entry_principale, "red", min_width=110)
        self.btn_sell_eur_main.pack(side=tk.LEFT, padx=3)
        tk.Label(row1_actions, text="usa il campo Importo EUR sopra", bg=self.PANEL, fg=self.MUTED, font=("Helvetica", 8)).pack(side=tk.LEFT, padx=(8, 0))

        row2 = tk.Frame(lf, bg=self.PANEL)
        row2.pack(fill=tk.X, padx=8, pady=(4, 4))
        tk.Label(row2, text="Vendi quota", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        for pct in [25, 50, 75, 100]:
            self.make_button(row2, f"{pct}%", lambda p=pct: self.vendi_percentuale(p), "dark", min_width=54).pack(side=tk.LEFT, padx=3)

        row2b = tk.Frame(lf, bg=self.PANEL)
        row2b.pack(fill=tk.X, padx=8, pady=(0, 8))
        tk.Label(row2b, text="Vendi importo EUR", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        entry_sell = tk.Entry(row2b, textvariable=self.var_importo_vendita, bg=self.PANEL_2, fg="#f8fafc", insertbackground="#f8fafc", width=10, relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground=self.BORDER, highlightcolor=self.BLUE)
        entry_sell.pack(side=tk.LEFT, padx=(6, 8))
        self.make_button(row2b, "Vendi EUR", self.vendi_importo_eur, "red", min_width=92).pack(side=tk.LEFT, padx=3)
        self.make_button(row2b, "Chiudi tutte", self.chiudi_tutte_posizioni, "red", min_width=106).pack(side=tk.LEFT, padx=(10, 3))
        self.make_button(row2b, "Reset", self.reset_simulazione, "dark", min_width=68).pack(side=tk.LEFT, padx=3)

        row3 = tk.Frame(lf, bg=self.PANEL)
        row3.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(row3, text="Timeframe", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        combo_tf = ttk.Combobox(row3, textvariable=self.var_timeframe, values=VALID_TIMEFRAMES, state="readonly", width=7)
        combo_tf.pack(side=tk.LEFT, padx=(6, 8))
        combo_tf.bind("<<ComboboxSelected>>", self.cambia_timeframe)
        self.make_button(row3, "Auto P/L", self.configura_auto_profit_loss, "blue", min_width=82).pack(side=tk.LEFT, padx=3)
        self.make_button(row3, "Auto Trade", self.configura_auto_trade, "blue", min_width=98).pack(side=tk.LEFT, padx=3)
        self.make_button(row3, "Modalità", self.configura_modalita_trading, "green", min_width=92).pack(side=tk.LEFT, padx=3)

        row4 = tk.Frame(lf, bg=self.PANEL)
        row4.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(row4, text="Gestione saldo", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        self.make_button(row4, "+ Aggiungi fondi", self.aggiungi_fondi_popup, "green", min_width=138).pack(side=tk.LEFT, padx=(8, 3))
        tk.Label(row4, text="Aumenta il saldo EUR della simulazione", bg=self.PANEL, fg=self.MUTED, font=("Helvetica", 9)).pack(side=tk.LEFT, padx=(8, 0))

        row5 = tk.Frame(lf, bg=self.PANEL)
        row5.pack(fill=tk.X, padx=8, pady=(0, 8))
        tk.Label(row5, text="Investimento", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        tk.Label(row5, textvariable=self.var_investimento_percentuale, bg=self.PANEL, fg=self.GREEN, font=("Helvetica", 10, "bold")).pack(side=tk.LEFT, padx=(6, 8))
        self.make_button(row5, "Usa % saldo", self.usa_percentuale_investimento, "dark", min_width=106).pack(side=tk.LEFT, padx=3)
        self.make_button(row5, "Imposta %", self.configura_percentuale_investimento, "blue", min_width=96).pack(side=tk.LEFT, padx=3)

        row6 = tk.Frame(lf, bg=self.PANEL)
        row6.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(row6, text="Modalità", bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        tk.Label(row6, textvariable=self.var_modalita_trading, bg=self.PANEL, fg=self.GREEN, font=("Helvetica", 9, "bold")).pack(side=tk.LEFT, padx=(6, 10))
        tk.Label(row6, textvariable=self.var_posizioni_limite, bg=self.PANEL, fg=self.YELLOW, font=("Helvetica", 9, "bold")).pack(side=tk.LEFT, padx=(6, 0))

        row7 = tk.Frame(lf, bg=self.PANEL)
        row7.pack(fill=tk.X, padx=8, pady=(0, 10))
        tk.Label(row7, textvariable=self.var_auto_trade_status, bg=self.PANEL, fg=self.YELLOW, font=("Helvetica", 9, "bold")).pack(side=tk.LEFT)

    def build_dashboard_trade_summary(self, parent):
        """Riquadro compatto con gli acquisti recenti vicino ai comandi."""
        lf = self.make_labelframe(parent, "Acquisti recenti")
        lf.pack(fill=tk.BOTH, expand=False, pady=(0, 8))

        header = tk.Frame(lf, bg=self.PANEL)
        header.pack(fill=tk.X, padx=8, pady=(6, 2))
        self.lbl_storico_dashboard = tk.Label(
            header,
            text="Dettaglio acquisti",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica", 9),
        )
        self.lbl_storico_dashboard.pack(side=tk.LEFT)
        self.make_button(header, "Aggiorna", self.aggiorna_storico_dashboard, "dark", min_width=82).pack(side=tk.RIGHT)

        cols = ("data", "crypto", "qty", "prezzo", "importo", "comm", "saldo")
        table_frame = tk.Frame(lf, bg=self.PANEL)
        table_frame.pack(fill=tk.X, padx=8, pady=(2, 8))

        self.tabella_storico_dashboard = ttk.Treeview(table_frame, columns=cols, show="headings", height=5)
        self.tabella_storico_dashboard.heading("data", text="Data")
        self.tabella_storico_dashboard.heading("crypto", text="Crypto")
        self.tabella_storico_dashboard.heading("qty", text="Quantità")
        self.tabella_storico_dashboard.heading("prezzo", text="Prezzo")
        self.tabella_storico_dashboard.heading("importo", text="Importo")
        self.tabella_storico_dashboard.heading("comm", text="Comm.")
        self.tabella_storico_dashboard.heading("saldo", text="Saldo")
        self.tabella_storico_dashboard.column("data", width=122, stretch=False)
        self.tabella_storico_dashboard.column("crypto", width=72, stretch=False)
        self.tabella_storico_dashboard.column("qty", width=88, anchor="e", stretch=False)
        self.tabella_storico_dashboard.column("prezzo", width=92, anchor="e", stretch=False)
        self.tabella_storico_dashboard.column("importo", width=92, anchor="e", stretch=False)
        self.tabella_storico_dashboard.column("comm", width=74, anchor="e", stretch=False)
        self.tabella_storico_dashboard.column("saldo", width=92, anchor="e", stretch=False)
        self.tabella_storico_dashboard.grid(row=0, column=0, sticky="nsew")

        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tabella_storico_dashboard.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tabella_storico_dashboard.xview)
        self.tabella_storico_dashboard.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.grid_columnconfigure(0, weight=1)

        self.tabella_storico_dashboard.tag_configure("acquisto", foreground=self.GREEN)
        self.tabella_storico_dashboard.bind("<Double-1>", self.mostra_dettaglio_operazione)

    def build_positions(self, parent):
        lf = self.make_labelframe(parent, "Posizioni aperte")
        lf.pack(fill=tk.BOTH, expand=True)

        cols = ("entry", "qty", "capitale", "valore", "pl", "plpct")
        self.tabella_pancia = ttk.Treeview(lf, columns=cols, show="tree headings", height=8)
        self.tabella_pancia.heading("#0", text="Crypto")
        self.tabella_pancia.heading("entry", text="Entry")
        self.tabella_pancia.heading("qty", text="Quantità")
        self.tabella_pancia.heading("capitale", text="Capitale")
        self.tabella_pancia.heading("valore", text="Valore")
        self.tabella_pancia.heading("pl", text="P/L")
        self.tabella_pancia.heading("plpct", text="%")
        self.tabella_pancia.column("#0", width=78, stretch=False)
        self.tabella_pancia.column("entry", width=82, anchor="e", stretch=False)
        self.tabella_pancia.column("qty", width=78, anchor="e", stretch=False)
        self.tabella_pancia.column("capitale", width=80, anchor="e", stretch=False)
        self.tabella_pancia.column("valore", width=80, anchor="e", stretch=False)
        self.tabella_pancia.column("pl", width=78, anchor="e", stretch=False)
        self.tabella_pancia.column("plpct", width=55, anchor="e", stretch=False)
        self.tabella_pancia.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tabella_pancia.tag_configure("positivo", foreground=self.GREEN)
        self.tabella_pancia.tag_configure("negativo", foreground=self.RED)

    def build_chart_panel(self, parent):
        lf = self.make_labelframe(parent, "Grafici")
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        tools = tk.Frame(lf, bg=self.PANEL)
        tools.pack(fill=tk.X, padx=8, pady=6)
        self.lbl_chart_title = tk.Label(tools, text="Grafico", bg=self.PANEL, fg="#f8fafc", font=("Helvetica", 10, "bold"))
        self.lbl_chart_title.pack(side=tk.LEFT)
        self.make_button(tools, "Linea", lambda: self.set_modalita_grafico("linea"), "dark", min_width=66).pack(side=tk.RIGHT, padx=3)
        self.make_button(tools, "Candele", lambda: self.set_modalita_grafico("candele"), "dark", min_width=82).pack(side=tk.RIGHT, padx=3)
        self.btn_confronto_crypto = self.make_button(tools, "Confronta crypto", self.seleziona_confronto_crypto, "blue", min_width=132)
        self.btn_confronto_crypto.pack(side=tk.RIGHT, padx=3)
        self.make_button(tools, "Singola crypto", self.disattiva_confronto_crypto, "dark", min_width=112).pack(side=tk.RIGHT, padx=3)

        self.fig = Figure(figsize=(6.6, 5.4), dpi=100, facecolor=self.BG)
        self.ax_crypto = self.fig.add_subplot(211, facecolor=self.PANEL_2)
        self.ax_fondi = self.fig.add_subplot(212, facecolor=self.PANEL_2)
        self.fig.tight_layout(pad=2.0)

        self.canvas = FigureCanvasTkAgg(self.fig, master=lf)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def build_registry_panel(self, parent):
        lf = self.make_labelframe(parent, "Ultime operazioni")
        lf.pack(fill=tk.X)

        cols = ("data", "op", "prezzo", "saldo", "note")
        self.tabella_registro = ttk.Treeview(lf, columns=cols, show="tree headings", height=5)
        self.tabella_registro.heading("data", text="Data")
        self.tabella_registro.heading("op", text="Operazione")
        self.tabella_registro.heading("prezzo", text="Prezzo")
        self.tabella_registro.heading("saldo", text="Saldo")
        self.tabella_registro.heading("note", text="Note")
        self.tabella_registro.column("data", width=140, stretch=False)
        self.tabella_registro.column("op", width=120, stretch=False)
        self.tabella_registro.column("prezzo", width=110, anchor="e", stretch=False)
        self.tabella_registro.column("saldo", width=95, anchor="e", stretch=False)
        self.tabella_registro.column("note", width=260, stretch=True)
        self.tabella_registro.pack(fill=tk.X, padx=8, pady=8)


    def build_trade_history_panel(self, parent):
        """Mostra separatamente acquisti e vendite letti da registro_trade.csv.

        La sezione non sostituisce "Ultime operazioni": serve a capire subito
        cosa è stato comprato e cosa è stato venduto, senza dover aprire il CSV.
        """
        lf = self.make_labelframe(parent, "Acquisti e vendite")
        lf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        header = tk.Frame(lf, bg=self.PANEL)
        header.pack(fill=tk.X, padx=8, pady=(6, 2))
        self.lbl_storico_trade = tk.Label(
            header,
            text="Storico operazioni simulato",
            bg=self.PANEL,
            fg=self.MUTED,
            font=("Helvetica", 9),
        )
        self.lbl_storico_trade.pack(side=tk.LEFT)
        self.make_button(header, "Aggiorna", self.aggiorna_storico_trade, "dark", min_width=82).pack(side=tk.RIGHT)

        self.notebook_trade = ttk.Notebook(lf)
        self.notebook_trade.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        tab_acquisti = tk.Frame(self.notebook_trade, bg=self.PANEL)
        tab_vendite = tk.Frame(self.notebook_trade, bg=self.PANEL)
        self.notebook_trade.add(tab_acquisti, text="Acquisti")
        self.notebook_trade.add(tab_vendite, text="Vendite")

        def crea_tabella(parent_tab):
            cols = ("data", "tipo", "qty", "prezzo", "importo", "comm", "pl", "saldo", "note")
            container = tk.Frame(parent_tab, bg=self.PANEL)
            container.pack(fill=tk.BOTH, expand=True)
            tabella = ttk.Treeview(container, columns=cols, show="tree headings", height=6)

            tabella.heading("#0", text="Crypto")
            tabella.heading("data", text="Data")
            tabella.heading("tipo", text="Tipo")
            tabella.heading("qty", text="Quantità")
            tabella.heading("prezzo", text="Prezzo")
            tabella.heading("importo", text="Importo/Ricavo")
            tabella.heading("comm", text="Comm.")
            tabella.heading("pl", text="P/L")
            tabella.heading("saldo", text="Saldo")
            tabella.heading("note", text="Dettaglio")
            tabella.column("#0", width=82, stretch=False)
            tabella.column("data", width=132, stretch=False)
            tabella.column("tipo", width=118, stretch=False)
            tabella.column("qty", width=92, anchor="e", stretch=False)
            tabella.column("prezzo", width=95, anchor="e", stretch=False)
            tabella.column("importo", width=106, anchor="e", stretch=False)
            tabella.column("comm", width=78, anchor="e", stretch=False)
            tabella.column("pl", width=86, anchor="e", stretch=False)
            tabella.column("saldo", width=92, anchor="e", stretch=False)
            tabella.column("note", width=230, stretch=True)
            tabella.grid(row=0, column=0, sticky="nsew")

            scroll_y = ttk.Scrollbar(container, orient="vertical", command=tabella.yview)
            scroll_x = ttk.Scrollbar(container, orient="horizontal", command=tabella.xview)
            tabella.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
            scroll_y.grid(row=0, column=1, sticky="ns")
            scroll_x.grid(row=1, column=0, sticky="ew")
            container.grid_rowconfigure(0, weight=1)
            container.grid_columnconfigure(0, weight=1)

            tabella.tag_configure("acquisto", foreground=self.GREEN)
            tabella.tag_configure("vendita_gain", foreground=self.GREEN)
            tabella.tag_configure("vendita_loss", foreground=self.RED)
            tabella.tag_configure("neutro", foreground=self.MUTED)
            tabella.bind("<Double-1>", self.mostra_dettaglio_operazione)
            return tabella

        self.tabella_acquisti = crea_tabella(tab_acquisti)
        self.tabella_vendite = crea_tabella(tab_vendite)

    @staticmethod
    def _operazione_is_acquisto(nome_operazione):
        nome = str(nome_operazione or "").upper()
        return "COMPRA" in nome or "BUY" in nome

    @staticmethod
    def _operazione_is_vendita(nome_operazione):
        nome = str(nome_operazione or "").upper()
        return "VENDI" in nome or "SELL" in nome or "TAKE_PROFIT" in nome or "STOP_LOSS" in nome

    def _dettaglio_breve_operazione(self, row):
        quantita = format_quantita(row.get("Quantita", ""))
        commissione = format_eur_detail(row.get("Commissione_EUR", ""))
        note = str(row.get("Note", "") or "")
        parti = []
        if quantita != "-":
            parti.append(f"Qtà {quantita}")
        if commissione != "-":
            parti.append(f"Comm. {commissione}")
        if note:
            parti.append(note)
        return " | ".join(parti) if parti else "-"

    def _tag_operazione(self, row):
        op = row.get("Operazione", "")
        if self._operazione_is_acquisto(op):
            return "acquisto"
        if self._operazione_is_vendita(op):
            return "vendita_gain" if safe_float(row.get("Profitto_EUR", 0.0)) >= 0 else "vendita_loss"
        return "neutro"

    def _valori_riga_trade(self, row, includi_crypto=False):
        values = [
            row.get("Data_Ora", ""),
            row.get("Crypto", ""),
            row.get("Operazione", ""),
            format_quantita(row.get("Quantita", "")),
            format_eur_detail(row.get("Importo_EUR", "")),
            format_signed_eur(row.get("Profitto_EUR", "")),
            self._dettaglio_breve_operazione(row)[:160],
        ]
        if includi_crypto:
            return values
        return (
            values[0],
            values[2],
            values[3],
            format_price(safe_float(row.get("Prezzo_EUR", 0.0))),
            values[4],
            format_eur_detail(row.get("Commissione_EUR", "")),
            values[5],
            format_eur_detail(row.get("Saldo_EUR", "")),
            values[6],
        )

    def mostra_dettaglio_operazione(self, event=None):
        widget = event.widget if event is not None else None
        if widget is None or not hasattr(widget, "selection"):
            return
        selected = widget.selection()
        if not selected:
            return

        row = self._righe_storico_trade.get(selected[0])
        if not row:
            return

        dettaglio = [
            f"Data: {row.get('Data_Ora', '-')}",
            f"Crypto: {row.get('Crypto', '-')}",
            f"Operazione: {row.get('Operazione', '-')}",
            f"Prezzo: {format_price(safe_float(row.get('Prezzo_EUR', 0.0)))}",
            f"RSI: {safe_float(row.get('RSI', 0.0)):.2f}",
            f"Quantità: {format_quantita(row.get('Quantita', ''))}",
            f"Importo/Ricavo: {format_eur_detail(row.get('Importo_EUR', ''))}",
            f"Commissione: {format_eur_detail(row.get('Commissione_EUR', ''))}",
            f"P/L: {format_signed_eur(row.get('Profitto_EUR', ''))}",
            f"Quota: {safe_float(row.get('Percentuale', 0.0)):.2f}%",
            f"Saldo dopo operazione: {format_eur_detail(row.get('Saldo_EUR', ''))}",
            "",
            str(row.get("Note", "") or "Nessuna nota"),
        ]
        messagebox.showinfo("Dettaglio operazione", "\n".join(dettaglio), parent=self.root)

    def build_log_area(self):
        self.txt_log = tk.Text(self.content, height=5, bg=self.PANEL_2, fg="#86efac", insertbackground="#86efac", font=("Courier New", 10), bd=0, relief=tk.FLAT, highlightthickness=1, highlightbackground=self.BORDER)
        self.txt_log.pack(fill=tk.X, padx=14, pady=(2, 12))

    def scrivi_log(self, testo):
        try:
            self.txt_log.insert(tk.END, testo + "\n")
            self.txt_log.see(tk.END)
        except Exception:
            print(testo)

    def on_close(self):
        self._closed = True
        if self._refresh_after_id is not None:
            try:
                self.root.after_cancel(self._refresh_after_id)
            except Exception:
                pass
        if self._engine_poll_after_id is not None:
            try:
                self.root.after_cancel(self._engine_poll_after_id)
            except Exception:
                pass
        if self._charts_autorefresh_after_id is not None:
            try:
                self.root.after_cancel(self._charts_autorefresh_after_id)
            except Exception:
                pass
            self._charts_autorefresh_after_id = None
        messagebox.showinfo(
            "Dashboard chiusa",
            "La Dashboard verrà chiusa.\n\n"
            "Il bot continuerà a lavorare in background.\n"
            "Per fermarlo davvero usa il pulsante 'Avvia/Ferma Bot' prima di chiudere."
        )
        self.root.destroy()

    def apri_cartella_dati(self):
        self.apri_file(APP_DIR)

    def apri_file(self, path: Path):
        try:
            if not Path(path).exists():
                if Path(path).suffix:
                    Path(path).touch()
                else:
                    Path(path).mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            log_errore(f"Errore apertura file/cartella {path}", e)
            messagebox.showerror("Errore", f"Non riesco ad aprire:\n{path}")

    def _reconcile_engine_ui_state(self, motivo=""):
        """Allinea la UI allo stato reale dell'engine.

        Se l'engine è terminato ma rimangono engine.pid/engine.lock/status vecchi,
        il pulsante non deve restare bloccato su "Ferma Bot". Questa funzione
        rimuove solo artifacts stale e poi aggiorna la label.
        """
        try:
            rimuovi_artifacts_engine_stale(motivo)
        except Exception:
            pass
        try:
            self.aggiorna_engine_label()
        except Exception:
            pass

    def _set_engine_transition(self, transition):
        self._engine_transition = transition
        self._engine_transition_started = time.time()
        try:
            if transition == "starting":
                self.lbl_engine.config(text="ENGINE: AVVIO IN CORSO...", fg=self.YELLOW)
                self.btn_engine.config(text="Avvio in corso...", state="disabled")
            elif transition == "stopping":
                self.lbl_engine.config(text="ENGINE: ARRESTO IN CORSO...", fg=self.YELLOW)
                self.btn_engine.config(text="Arresto in corso...", state="disabled")
        except Exception:
            pass

    def _clear_engine_transition(self):
        self._engine_transition = None
        self._engine_transition_started = 0.0
        if self._engine_poll_after_id is not None:
            try:
                self.root.after_cancel(self._engine_poll_after_id)
            except Exception:
                pass
            self._engine_poll_after_id = None
        try:
            self.btn_engine.config(state="normal")
        except Exception:
            pass
        self.aggiorna_engine_label()

    def _poll_engine_transition(self):
        if self._closed:
            return

        transition = self._engine_transition
        pid = get_engine_pid()
        running = pid is not None
        elapsed = time.time() - safe_float(self._engine_transition_started, time.time())

        if transition == "starting" and running:
            self.scrivi_log(f"[ENGINE] Engine confermato attivo. PID {pid if pid != -1 else 'in rilevamento'}.")
            self._clear_engine_transition()
            return

        if transition == "stopping" and not running:
            self.scrivi_log("[ENGINE] Engine confermato fermo.")
            self._clear_engine_transition()
            return

        timeout = 45.0 if transition == "starting" else 15.0
        if transition and elapsed >= timeout:
            self.scrivi_log(f"[ENGINE] Timeout durante {'avvio' if transition == 'starting' else 'arresto'}: riconcilio stato reale.")
            self._reconcile_engine_ui_state(f"timeout_{transition}")
            self._clear_engine_transition()
            return

        self._engine_poll_after_id = self.root.after(500, self._poll_engine_transition)

    def toggle_engine(self):
        if self._engine_transition:
            return

        if engine_is_running():
            conferma = messagebox.askyesno(
                "Ferma Bot",
                "Vuoi fermare davvero il motore del bot?\n\n"
                "Dopo lo stop non controllerà più prezzi, RSI, Take Profit o Stop Loss."
            )
            if conferma:
                # Prima riconciliamo: se il PID/lock è stale, non inviamo STOP a un engine inesistente.
                self._reconcile_engine_ui_state("prima_stop")
                if not engine_is_running():
                    self.scrivi_log("[ENGINE] Nessun engine reale attivo: UI riportata su Avvia Bot.")
                    self.aggiorna_engine_label()
                    return
                self._set_engine_transition("stopping")
                append_comando("STOP")
                self.scrivi_log("[ENGINE] Richiesto stop engine.")
                self._poll_engine_transition()
        else:
            self._set_engine_transition("starting")
            self.scrivi_log("[ENGINE] Avvio in corso...")
            ok = start_engine_process(wait_for_confirm=False)
            if ok:
                self._poll_engine_transition()
            else:
                self._clear_engine_transition()
                messagebox.showerror(
                    "Errore",
                    "Engine non avviato.\n\nControlla nella cartella dell'app:\n- engine_stderr.log\n- engine_launch.log\n- errori_bot.log"
                )

    def cambia_timeframe(self, event=None):
        tf = self.var_timeframe.get()
        self.cfg = carica_config()
        self.cfg["timeframe"] = tf
        self.cfg["modalita_trading"] = "Personalizzata"
        salva_config(self.cfg)
        self.var_modalita_trading.set("Personalizzata")
        append_comando("SET_TIMEFRAME", {"timeframe": tf})
        self.scrivi_log(f"[COMANDO] Timeframe richiesto: {tf}")

    def aggiungi_fondi_popup(self):
        valore = simpledialog.askfloat("Aggiungi fondi", "Inserisci l'importo da aggiungere al saldo EUR:", minvalue=0.01, parent=self.root)
        if valore is None:
            return

        # Aggiorniamo subito config.json invece di dipendere dal comando engine.
        # Così il saldo aumenta anche se l'engine è momentaneamente fermo.
        self.cfg = carica_config()
        self.cfg["saldo_eur"] = safe_float(self.cfg.get("saldo_eur", 0.0)) + float(valore)
        self.cfg.setdefault("storico_saldi", [])
        self.cfg["storico_saldi"].append(round(safe_float(self.cfg["saldo_eur"]), 2))
        salva_config(self.cfg)

        registra_operazione(
            "SIMULAZIONE",
            "AGGIUNGI_FONDI",
            0.0,
            0.0,
            safe_float(self.cfg["saldo_eur"]),
            f"Aggiunti {float(valore):.2f} EUR al saldo simulato"
        )

        self.scrivi_log(f"[DASHBOARD] Fondi aggiunti subito: +{valore:.2f} EUR")
        self.refresh_dashboard()

    def saldo_disponibile_corrente(self):
        balances = self.status.get("balances", {})
        return safe_float(balances.get("saldo_eur", self.cfg.get("saldo_eur", 0.0)))

    def configura_percentuale_investimento(self):
        self.cfg = carica_config()
        attuale = safe_float(self.cfg.get("percentuale_rischio_per_trade", 5.0))
        nuovo = simpledialog.askfloat(
            "Percentuale investimento",
            "Percentuale del saldo disponibile da usare per ogni acquisto.\n\n"
            "Esempio: 10 = usa il 10% del saldo disponibile.\n"
            "Con Auto Trade ON viene usata anche dagli acquisti automatici RSI.",
            initialvalue=attuale,
            minvalue=0.1,
            maxvalue=100.0,
            parent=self.root,
        )
        if nuovo is None:
            return

        nuovo = float(nuovo)
        self.cfg["percentuale_rischio_per_trade"] = nuovo
        self.cfg["modalita_trading"] = "Personalizzata"
        salva_config(self.cfg)
        append_comando("SET_RISK", {"risk_percent": nuovo})
        self.var_investimento_percentuale.set(f"{nuovo:.2f}%")
        self.var_modalita_trading.set("Personalizzata")
        self.scrivi_log(f"[COMANDO] Percentuale investimento aggiornata: {nuovo:.2f}%")

    def usa_percentuale_investimento(self):
        self.cfg = carica_config()
        percentuale = safe_float(self.cfg.get("percentuale_rischio_per_trade", 5.0))
        saldo = self.saldo_disponibile_corrente()

        if saldo <= 0:
            messagebox.showerror("Saldo non disponibile", "Non c'è saldo EUR disponibile per calcolare l'importo.")
            return

        importo = saldo * percentuale / 100.0
        self.var_importo.set(f"{importo:.2f}")
        self.scrivi_log(f"[DASHBOARD] Importo impostato al {percentuale:.2f}% del saldo: {importo:.2f} EUR")

        if importo < 10:
            messagebox.showinfo(
                "Importo sotto il minimo",
                f"Il {percentuale:.2f}% del saldo corrisponde a {importo:.2f} EUR.\n\n"
                "L'acquisto simulato richiede almeno 10 EUR."
            )

    def crypto_corrente(self):
        simbolo = self.var_crypto.get() or self.crypto_selezionata_grafico
        simbolo = simbolo.upper()
        if "/" not in simbolo:
            simbolo = simbolo_visuale(simbolo)
        return simbolo

    def compra_da_entry(self):
        simbolo = self.crypto_corrente()
        try:
            importo = safe_float(str(self.var_importo.get()).replace(",", "."))
        except Exception:
            importo = 0.0

        if importo < 10:
            messagebox.showerror("Importo non valido", "Inserisci un importo di almeno 10 EUR.")
            return

        saldo = safe_float(self.status.get("balances", {}).get("saldo_eur", self.cfg.get("saldo_eur", 0.0)))
        if importo > saldo:
            messagebox.showerror("Saldo insufficiente", f"Saldo disponibile: {format_eur(saldo)}")
            return

        conferma = messagebox.askyesno("Conferma acquisto", f"Vuoi acquistare {simbolo} per {importo:.2f} EUR?\n\nOperazione simulata.")
        if not conferma:
            return

        append_comando("BUY_MANUAL", {"symbol": simbolo, "amount": float(importo)})
        self.scrivi_log(f"[COMANDO] Compra manuale {simbolo}: {importo:.2f} EUR")

    def vendi_percentuale(self, percentuale):
        simbolo = self.crypto_corrente()
        posizioni = {p.get("simbolo"): p for p in self.status.get("positions", [])}
        if simbolo not in posizioni:
            messagebox.showerror("Nessuna posizione", f"Non hai una posizione aperta su {simbolo}.")
            return

        conferma = messagebox.askyesno("Conferma vendita", f"Vuoi vendere il {percentuale}% della posizione {simbolo}?\n\nOperazione simulata.")
        if not conferma:
            return

        append_comando("SELL_MANUAL", {"symbol": simbolo, "percent": float(percentuale)})
        self.scrivi_log(f"[COMANDO] Vendi manuale {simbolo}: {percentuale}% della posizione")

    def vendi_importo_eur_da_entry_principale(self):
        """Vende un importo EUR libero usando il campo Importo EUR vicino a Compra.

        La v1.0.17 aveva già il comando SELL_MANUAL_EUR e il pannello dedicato;
        questa scorciatoia rende la funzione visibile anche accanto a Compra,
        come richiesto, senza rimuovere i pulsanti percentuali né il campo
        secondario Vendi importo EUR.
        """
        try:
            self.var_importo_vendita.set(str(self.var_importo.get()).strip())
        except Exception:
            pass
        self.vendi_importo_eur()

    def vendi_importo_eur(self):
        simbolo = self.crypto_corrente()
        posizioni = {p.get("simbolo"): p for p in self.status.get("positions", [])}
        pos = posizioni.get(simbolo)
        if not pos:
            messagebox.showerror("Nessuna posizione", f"Non hai una posizione aperta su {simbolo}.")
            return

        try:
            importo = safe_float(str(self.var_importo_vendita.get()).replace(",", "."))
        except Exception:
            importo = 0.0

        if importo <= 0:
            messagebox.showerror("Importo non valido", "Inserisci un importo EUR positivo da vendere.")
            return

        valore_posizione = safe_float(pos.get("valore", 0.0))
        if valore_posizione <= 0:
            messagebox.showerror("Posizione non valida", f"Il valore della posizione {simbolo} non è disponibile.")
            return

        if importo > valore_posizione + 0.01:
            messagebox.showerror(
                "Importo superiore alla posizione",
                f"Vuoi vendere {importo:.2f} EUR, ma la posizione {simbolo} vale circa {valore_posizione:.2f} EUR."
            )
            return

        percentuale_stimata = min(100.0, (importo / valore_posizione) * 100.0)
        conferma = messagebox.askyesno(
            "Conferma vendita importo EUR",
            f"Vuoi vendere circa {importo:.2f} EUR lordi della posizione {simbolo}?\n\n"
            f"Valore posizione stimato: {valore_posizione:.2f} EUR\n"
            f"Quota stimata: {percentuale_stimata:.2f}%\n\n"
            "Il saldo accreditato sarà al netto della commissione. Operazione simulata."
        )
        if not conferma:
            return

        append_comando("SELL_MANUAL_EUR", {"symbol": simbolo, "amount_eur": float(importo)})
        self.scrivi_log(f"[COMANDO] Vendi manuale {simbolo}: importo libero {importo:.2f} EUR lordi")

    def chiudi_tutte_posizioni(self):
        conferma = messagebox.askyesno("Chiudi posizioni", "Vuoi chiudere tutte le posizioni simulate al prezzo corrente?")
        if conferma:
            append_comando("CLOSE_ALL")
            self.scrivi_log("[COMANDO] Chiusura di tutte le posizioni richiesta.")

    def reset_simulazione(self):
        conferma = messagebox.askyesno(
            "Reset simulazione",
            "Vuoi davvero azzerare la simulazione e ripartire da 5000 EUR?\n\n"
            "Questa azione chiude/azzera tutte le posizioni simulate."
        )
        if conferma:
            append_comando("RESET")
            self.scrivi_log("[COMANDO] Reset simulazione richiesto.")

    def configura_modalita_trading(self):
        self.cfg = carica_config()
        finestra = tk.Toplevel(self.root)
        finestra.title("Modalità trading")
        finestra.configure(bg=self.BG)
        finestra.transient(self.root)
        finestra.grab_set()
        finestra.resizable(False, False)

        tk.Label(
            finestra,
            text="Scegli la modalità trading",
            bg=self.BG,
            fg="#f8fafc",
            font=("Helvetica", 15, "bold"),
        ).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(
            finestra,
            text="La modalità modifica percentuale investimento, RSI, Take Profit, Stop Loss e timeframe.\n"
                 "Non attiva da sola Auto Trade: quello resta sotto il tuo controllo.",
            bg=self.BG,
            fg=self.MUTED,
            justify=tk.LEFT,
            font=("Helvetica", 10),
        ).pack(anchor="w", padx=16, pady=(0, 10))

        current = self.cfg.get("modalita_trading", "Normale")

        for nome, preset in TRADING_MODE_PRESETS.items():
            row = tk.Frame(finestra, bg=self.PANEL)
            row.pack(fill=tk.X, padx=16, pady=5)

            titolo = f"{nome}"
            if normalizza_modalita_trading(current) == nome:
                titolo += "  ✓"
            tk.Label(row, text=titolo, bg=self.PANEL, fg=self.GREEN if nome in {"Aggressiva", "Ultra aggressiva"} else "#f8fafc", font=("Helvetica", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            descrizione = (
                f"{preset['descrizione']}\n"
                f"Investimento {preset['risk_percent']:.0f}% · Buy RSI≤{preset['buy_rsi']:.0f} · "
                f"Sell RSI≥{preset['sell_rsi']:.0f} · TP {preset['take_profit']:.1f}% · "
                f"SL {preset['stop_loss']:.1f}% · Timeframe {preset['timeframe']}"
            )
            tk.Label(row, text=descrizione, bg=self.PANEL, fg=self.MUTED, justify=tk.LEFT, font=("Helvetica", 9)).pack(anchor="w", padx=12, pady=(0, 8))
            self.make_button(row, f"Usa {nome}", lambda m=nome, w=finestra: self.applica_modalita_trading(m, w), "blue" if nome != "Ultra aggressiva" else "red", min_width=132).pack(anchor="e", padx=12, pady=(0, 10))

        self.make_button(finestra, "Annulla", finestra.destroy, "dark", min_width=100).pack(anchor="e", padx=16, pady=(4, 14))

    def applica_modalita_trading(self, modalita, finestra=None):
        self.cfg = carica_config()
        modalita, preset = applica_modalita_a_config(self.cfg, modalita)
        salva_config(self.cfg)
        append_comando("SET_TRADING_MODE", {"mode": modalita})

        self.var_modalita_trading.set(descrizione_modalita_trading(modalita))
        self.var_investimento_percentuale.set(f"{preset['risk_percent']:.2f}%")
        self.var_timeframe.set(preset["timeframe"])
        stato = "ON" if self.cfg.get("auto_trading_attivo", False) else "OFF"
        self.var_auto_trade_status.set(
            f"Auto Trade {stato} · Buy RSI≤{preset['buy_rsi']:.1f} · Sell RSI≥{preset['sell_rsi']:.1f}"
        )
        self.scrivi_log(
            f"[COMANDO] Modalità {modalita}: inv. {preset['risk_percent']:.1f}% | "
            f"Buy RSI <= {preset['buy_rsi']:.1f} | Sell RSI >= {preset['sell_rsi']:.1f} | "
            f"TP {preset['take_profit']:.1f}% | SL {preset['stop_loss']:.1f}% | TF {preset['timeframe']}"
        )
        if finestra is not None:
            try:
                finestra.destroy()
            except Exception:
                pass

        posizioni_aperte = len(self.status.get("positions", []))
        if posizioni_aperte <= 0:
            posizioni_aperte = conta_posizioni_aperte_cfg(self.cfg)
        max_posizioni = safe_int(preset.get("max_posizioni", self.cfg.get("max_posizioni_aperte", 8)), 8)
        if posizioni_aperte > max_posizioni:
            messagebox.showwarning(
                "Limite posizioni superato",
                f"Hai {posizioni_aperte} posizioni aperte, ma la modalità {modalita} ne consente {max_posizioni}.\n\n"
                "Il bot non venderà automaticamente le posizioni extra: semplicemente non aprirà nuovi acquisti "
                "finché il numero di posizioni non rientra nel limite."
            )
            self.scrivi_log(f"[AVVISO] Posizioni aperte {posizioni_aperte}/{max_posizioni}: limite modalità {modalita} superato.")

        messagebox.showinfo(
            "Modalità applicata",
            f"Modalità {modalita} applicata.\n\n"
            f"Investimento: {preset['risk_percent']:.1f}%\n"
            f"Buy RSI: {preset['buy_rsi']:.1f}\n"
            f"Sell RSI: {preset['sell_rsi']:.1f}\n"
            f"Take Profit: {preset['take_profit']:.1f}%\n"
            f"Stop Loss: {preset['stop_loss']:.1f}%\n"
            f"Timeframe: {preset['timeframe']}\n\n"
            f"Auto Trade resta {'attivo' if self.cfg.get('auto_trading_attivo', False) else 'spento'}."
        )

    def configura_auto_trade(self):
        self.cfg = carica_config()
        scelta = messagebox.askyesnocancel(
            "Auto Trade RSI",
            "Vuoi attivare l'acquisto/vendita automatica simulata basata su RSI?\n\n"
            "Sì = attiva\n"
            "No = disattiva\n"
            "Annulla = lascia invariato\n\n"
            "Regola: compra se RSI è sotto la soglia di acquisto e non hai già posizione; "
            "vende se RSI supera la soglia di vendita."
        )
        if scelta is None:
            return

        active = bool(scelta)
        buy_rsi = safe_float(self.cfg.get("soglia_acquisto", 35))
        sell_rsi = safe_float(self.cfg.get("soglia_vendita", 65))

        if active:
            nuovo_buy = simpledialog.askfloat(
                "Soglia acquisto RSI",
                "Compra automaticamente se RSI è minore o uguale a:",
                initialvalue=buy_rsi,
                minvalue=1.0,
                maxvalue=99.0,
                parent=self.root,
            )
            if nuovo_buy is None:
                return
            nuovo_sell = simpledialog.askfloat(
                "Soglia vendita RSI",
                "Vende automaticamente se RSI è maggiore o uguale a:",
                initialvalue=sell_rsi,
                minvalue=1.0,
                maxvalue=99.0,
                parent=self.root,
            )
            if nuovo_sell is None:
                return
            buy_rsi = float(nuovo_buy)
            sell_rsi = float(nuovo_sell)

            if buy_rsi >= sell_rsi:
                messagebox.showerror(
                    "Soglie non valide",
                    "La soglia di acquisto deve essere più bassa della soglia di vendita.\n\n"
                    "Esempio sensato: compra RSI <= 35, vendi RSI >= 65."
                )
                return

        self.cfg["auto_trading_attivo"] = active
        self.cfg["soglia_acquisto"] = buy_rsi
        self.cfg["soglia_vendita"] = sell_rsi
        self.cfg["modalita_trading"] = "Personalizzata"
        salva_config(self.cfg)
        append_comando("SET_AUTO_TRADE", {"active": active, "buy_rsi": buy_rsi, "sell_rsi": sell_rsi})
        stato = "ON" if active else "OFF"
        self.var_auto_trade_status.set(f"Auto Trade {stato} · Buy RSI≤{buy_rsi:.1f} · Sell RSI≥{sell_rsi:.1f}")
        self.var_modalita_trading.set("Personalizzata")
        self.scrivi_log(f"[COMANDO] Auto Trade {stato} | Buy RSI <= {buy_rsi:.1f} | Sell RSI >= {sell_rsi:.1f}")

    def configura_auto_profit_loss(self):
        self.cfg = carica_config()
        scelta = messagebox.askyesnocancel(
            "Auto Profit/Loss",
            "Vuoi attivare l'Auto Profit/Loss?\n\nSì = attiva\nNo = disattiva\nAnnulla = lascia invariato"
        )
        if scelta is None:
            return

        active = bool(scelta)
        tp = safe_float(self.cfg.get("take_profit_percentuale", 2.0))
        sl = safe_float(self.cfg.get("stop_loss_percentuale", 3.0))

        if active:
            nuovo_tp = simpledialog.askfloat("Take Profit automatico", "Percentuale di guadagno da incassare automaticamente:", initialvalue=tp, minvalue=0.1, maxvalue=100.0, parent=self.root)
            if nuovo_tp is None:
                return
            nuovo_sl = simpledialog.askfloat("Stop Loss automatico", "Percentuale massima di perdita prima di chiudere la posizione:", initialvalue=sl, minvalue=0.1, maxvalue=100.0, parent=self.root)
            if nuovo_sl is None:
                return
            tp = float(nuovo_tp)
            sl = float(nuovo_sl)

        self.cfg["auto_profit_loss_attivo"] = active
        self.cfg["take_profit_percentuale"] = tp
        self.cfg["stop_loss_percentuale"] = sl
        self.cfg["modalita_trading"] = "Personalizzata"
        salva_config(self.cfg)
        self.var_modalita_trading.set("Personalizzata")
        append_comando("SET_AUTO_PL", {"active": active, "take_profit": tp, "stop_loss": sl})
        self.scrivi_log(f"[COMANDO] Auto P/L {'ON' if active else 'OFF'} | TP {tp:.2f}% | SL {sl:.2f}%")

    def seleziona_crypto_da_lista(self, event=None):
        if not self._widget_alive("tabella_listino"):
            return
        selected = self.tabella_listino.selection()
        if selected:
            item = selected[0]
            self.crypto_selezionata_grafico = item
            self.var_crypto.set(item)
            self.confronto_attivo = False
            self.crypto_confronto = []
            self.scrivi_log(f"[GRAFICO] Focus su: {item}")
            self.aggiorna_riepilogo_crypto_selezionata()
            self.aggiorna_grafici()

    def seleziona_crypto_combo(self, event=None):
        self.crypto_selezionata_grafico = self.crypto_corrente()
        self.confronto_attivo = False
        self.crypto_confronto = []
        self.scrivi_log(f"[GRAFICO] Focus su: {self.crypto_selezionata_grafico}")
        self.aggiorna_grafici()

    def set_modalita_grafico(self, modalita):
        self.confronto_attivo = False
        self.crypto_confronto = []
        self.modalita_grafico_crypto = modalita
        self.var_modalita_grafico.set(modalita)
        self.btn_confronto_crypto.config(text="Confronta crypto")
        self.scrivi_log(f"[GRAFICO] Modalità {modalita} attiva.")
        self.aggiorna_grafici()

    def seleziona_confronto_crypto(self):
        simboli_disponibili = list(self.status.get("ohlc", {}).keys())
        if len(simboli_disponibili) < 2:
            simboli_disponibili = [simbolo_visuale(b) for b in self.cfg.get("crypto_base_list", [])]

        finestra = tk.Toplevel(self.root)
        finestra.title("Confronta crypto")
        finestra.geometry("360x520")
        finestra.configure(bg=self.BG)
        finestra.transient(self.root)
        finestra.grab_set()

        tk.Label(finestra, text="Seleziona 2 o più crypto da confrontare", bg=self.BG, fg=self.TEXT, font=("Helvetica", 10, "bold")).pack(pady=(12, 4))
        tk.Label(finestra, text="Il confronto mostra la performance % dal primo punto disponibile.", bg=self.BG, fg=self.MUTED, wraplength=310).pack(pady=(0, 8))

        listbox = tk.Listbox(finestra, selectmode=tk.MULTIPLE, height=16, bg=self.PANEL, fg="#f0f6fc", selectbackground="#1f6feb", selectforeground="#ffffff", exportselection=False, font=("Helvetica", 10))
        listbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        for simbolo in simboli_disponibili:
            listbox.insert(tk.END, simbolo)

        preselezione = []
        if self.crypto_selezionata_grafico in simboli_disponibili:
            preselezione.append(self.crypto_selezionata_grafico)
        for simbolo in simboli_disponibili:
            if simbolo not in preselezione:
                preselezione.append(simbolo)
            if len(preselezione) >= 4:
                break
        for i, simbolo in enumerate(simboli_disponibili):
            if simbolo in preselezione:
                listbox.selection_set(i)

        def conferma():
            selezioni = [simboli_disponibili[i] for i in listbox.curselection()]
            if len(selezioni) < 2:
                messagebox.showerror("Errore", "Seleziona almeno 2 crypto.")
                return
            self.crypto_confronto = selezioni
            self.confronto_attivo = True
            self.modalita_grafico_crypto = "confronto"
            self.btn_confronto_crypto.config(text=f"Confronto: {len(selezioni)}")
            self.scrivi_log("[GRAFICO] Confronto attivo: " + ", ".join(selezioni))
            self.aggiorna_grafici()
            finestra.destroy()

        frame_btn = tk.Frame(finestra, bg=self.BG)
        frame_btn.pack(pady=10)
        self.make_button(frame_btn, "Mostra confronto", conferma, "blue", min_width=136).pack(side=tk.LEFT, padx=5)
        self.make_button(frame_btn, "Annulla", finestra.destroy, "dark", min_width=82).pack(side=tk.LEFT, padx=5)

    def disattiva_confronto_crypto(self):
        self.confronto_attivo = False
        self.crypto_confronto = []
        self.modalita_grafico_crypto = "linea"
        self.var_modalita_grafico.set("linea")
        self.btn_confronto_crypto.config(text="Confronta crypto")
        self.scrivi_log("[GRAFICO] Vista singola crypto attiva.")
        self.aggiorna_grafici()

    def leggi_status_dashboard_sicuro(self):
        """Legge status_bot.json senza far lampeggiare la UI sui valori default.

        Se il file è temporaneamente incompleto, mantiene l'ultimo status valido.
        Questo elimina il delay grafico dopo Report/Card quando l'engine aggiorna
        status_bot.json in parallelo alla dashboard.
        """
        status = load_json_file(FILE_STATUS, {})
        if status_dashboard_valido(status):
            self._last_good_status = status
            return status
        last = getattr(self, "_last_good_status", {}) or {}
        if status_dashboard_valido(last):
            try:
                self.scrivi_log("[GUARD] status_bot.json incompleto: mantengo ultimo status valido per la UI.")
            except Exception:
                pass
            return last
        return status if isinstance(status, dict) else {}

    def leggi_config_dashboard_sicura(self, status=None):
        """Legge config.json mantenendo l'ultimo config valido se appare default.

        Non modifica i file. Serve solo a proteggere le card da refresh temporanei
        in cui la UI leggerebbe un config incompleto/default.
        """
        cfg = carica_config()
        try:
            balances = stima_balances_dashboard_da_config(cfg, status or {})
            if dashboard_state_sembra_reset(balances):
                last_cfg = getattr(self, "_last_good_cfg", None)
                if isinstance(last_cfg, dict):
                    last_balances = stima_balances_dashboard_da_config(last_cfg, status or {})
                    if dashboard_state_ha_dati_reali(last_balances):
                        try:
                            self.scrivi_log("[GUARD] config letto come default: mantengo ultimo config valido per la UI.")
                        except Exception:
                            pass
                        return deep_copy_json(last_cfg)
            if dashboard_state_ha_dati_reali(balances):
                self._last_good_cfg = deep_copy_json(cfg)
                self.last_valid_config = deep_copy_json(cfg)
        except Exception as e:
            log_errore("Errore lettura config sicura dashboard", e)
        if dashboard_state_sembra_reset(stima_balances_dashboard_da_config(cfg, status or {})):
            if isinstance(getattr(self, "last_valid_config", None), dict):
                last_balances = stima_balances_dashboard_da_config(self.last_valid_config, status or {})
                if dashboard_state_ha_dati_reali(last_balances):
                    return deep_copy_json(self.last_valid_config)
        return cfg

    def refresh_dashboard(self):
        if self._closed:
            return

        self.status = self.leggi_status_dashboard_sicuro()
        self.cfg = self.leggi_config_dashboard_sicura(self.status)

        self.aggiorna_engine_label()
        self.aggiorna_header()
        self.aggiorna_tabelle()
        self.aggiorna_grafici()
        self.aggiorna_registro()
        self.aggiorna_storico_dashboard()
        self.aggiorna_storico_trade()
        self.aggiorna_bottoni()

        self._refresh_after_id = self.root.after(2000, self.refresh_dashboard)

    def aggiorna_engine_label(self):
        pid = get_engine_pid()
        engine_status = self.status.get("engine", {})
        last = engine_status.get("last_update", "mai")
        msg = engine_status.get("messaggio", "")

        if self._engine_transition == "starting" and pid:
            self._clear_engine_transition()
            return
        if self._engine_transition == "stopping" and not pid:
            self._clear_engine_transition()
            return
        if self._engine_transition == "starting":
            self.lbl_engine.config(text="ENGINE: AVVIO IN CORSO...", fg=self.YELLOW)
            self.btn_engine.config(text="Avvio in corso...", state="disabled")
            return
        if self._engine_transition == "stopping":
            self.lbl_engine.config(text="ENGINE: ARRESTO IN CORSO...", fg=self.YELLOW)
            self.btn_engine.config(text="Arresto in corso...", state="disabled")
            return

        if pid:
            pid_label = str(pid) if pid != -1 else "in rilevamento"
            self.lbl_engine.config(text=f"ENGINE: ATTIVO · PID {pid_label} · {last}", fg=self.GREEN)
            self.btn_engine.config(text="Ferma Bot", state="normal")
        else:
            self.lbl_engine.config(text=f"ENGINE: FERMO · ultimo status: {last}", fg=self.RED)
            self.btn_engine.config(text="Avvia Bot", state="normal")

        if msg and not hasattr(self, "_last_engine_msg"):
            self._last_engine_msg = msg
        elif msg and getattr(self, "_last_engine_msg", "") != msg:
            self._last_engine_msg = msg
            self.scrivi_log(f"[ENGINE] {msg}")

    def aggiorna_header(self):
        raw_balances = self.status.get("balances", {}) if isinstance(self.status, dict) else {}
        cfg_balances = stima_balances_dashboard_da_config(self.cfg, self.status)

        # Se status_bot.json è assente, vecchio o vuoto, NON mostrare valori default:
        # usa config.json come fonte minima. Il report non deve mai dare l'impressione
        # di reset del portafoglio.
        status_valido = bool(raw_balances) and (
            safe_float(raw_balances.get("saldo_eur", 0.0)) > 0
            or safe_float(raw_balances.get("valore_posizioni", 0.0)) > 0
            or safe_int(raw_balances.get("totale_acquisti", 0), 0) > 0
            or safe_int(raw_balances.get("posizioni_aperte", 0), 0) > 0
        )
        balances = dict(cfg_balances)
        if status_valido:
            balances.update(raw_balances)

        # CARD/REPORT FIX: impedisce reset VISIVI delle card.
        # Se un click su card/report ricarica uno status vuoto/default, manteniamo
        # l'ultimo stato buono o, in alternativa, l'ultimo snapshot SQLite.
        cfg_has_real_data = dashboard_state_ha_dati_reali(cfg_balances)
        if dashboard_state_sembra_reset(balances) and cfg_has_real_data:
            balances = dict(cfg_balances)
            try:
                self.scrivi_log("[GUARD] Valori default/status vuoti ignorati: uso config.json per le card.")
            except Exception:
                pass

        if dashboard_state_sembra_reset(balances):
            sqlite_balances = stima_balances_dashboard_da_sqlite()
            if dashboard_state_ha_dati_reali(sqlite_balances):
                balances = sqlite_balances
                try:
                    self.scrivi_log("[GUARD] Reset visivo evitato: uso ultimo snapshot SQLite per le card.")
                except Exception:
                    pass

        if dashboard_state_sembra_reset(balances) and getattr(self, "_last_good_dashboard_balances", None):
            last_good = dict(self._last_good_dashboard_balances or {})
            if dashboard_state_ha_dati_reali(last_good):
                balances = last_good
                try:
                    self.scrivi_log("[GUARD] Reset visivo evitato: uso ultimo stato dashboard valido.")
                except Exception:
                    pass

        if dashboard_state_ha_dati_reali(balances):
            self._last_good_dashboard_balances = dict(balances)
            self.last_valid_dashboard_state = dict(balances)

        saldo = safe_float(balances.get("saldo_eur", self.cfg.get("saldo_eur", 0.0)))
        investito = safe_float(balances.get("investito", cfg_balances.get("investito", 0.0)))
        valore = safe_float(balances.get("valore_posizioni", cfg_balances.get("valore_posizioni", 0.0)))
        patrimonio = safe_float(balances.get("patrimonio", saldo + valore))
        pl_realizzato = safe_float(balances.get("profitto_accumulato", self.cfg.get("profitto_accumulato", 0.0)))
        pl_non_realizzato = safe_float(balances.get("profitto_non_realizzato", valore - investito))
        totale_pl = pl_realizzato + pl_non_realizzato
        acquisti = int(balances.get("totale_acquisti", self.cfg.get("totale_acquisti", 0)))
        vendite = int(balances.get("totale_vendite", self.cfg.get("totale_vendite", 0)))

        self.card_vars["saldo"].set(format_eur(saldo))
        self.card_vars["investito"].set(format_eur(investito))
        self.card_vars["valore"].set(format_eur(valore))
        self.card_vars["patrimonio"].set(format_eur(patrimonio))
        self.card_vars["pl"].set(f"{totale_pl:+.2f} EUR")
        self.card_vars["ops"].set(f"{acquisti} acquisti · {vendite} vendite")

        settings = self.status.get("settings", {})
        tf = settings.get("timeframe", self.cfg.get("timeframe", "1m"))
        if self.var_timeframe.get() != tf:
            self.var_timeframe.set(tf)

        risk = safe_float(settings.get("risk_percent", self.cfg.get("percentuale_rischio_per_trade", 5.0)))
        nuovo_testo_risk = f"{risk:.2f}%"
        if self.var_investimento_percentuale.get() != nuovo_testo_risk:
            self.var_investimento_percentuale.set(nuovo_testo_risk)

        modalita = settings.get("modalita_trading", self.cfg.get("modalita_trading", "Normale"))
        if modalita != "Personalizzata":
            modalita = normalizza_modalita_trading(modalita)
            modalita_txt = descrizione_modalita_trading(modalita)
        else:
            modalita_txt = "Personalizzata"
        if self.var_modalita_trading.get() != modalita_txt:
            self.var_modalita_trading.set(modalita_txt)

        max_posizioni = safe_int(settings.get("max_posizioni_aperte", self.cfg.get("max_posizioni_aperte", 8)), 8)
        posizioni_aperte = int(balances.get("posizioni_aperte", len(self.status.get("positions", []))))
        if posizioni_aperte > max_posizioni:
            txt_limite = f"⚠️ Posizioni {posizioni_aperte}/{max_posizioni}: nuovi acquisti bloccati"
        else:
            txt_limite = f"Posizioni {posizioni_aperte}/{max_posizioni}"
        if self.var_posizioni_limite.get() != txt_limite:
            self.var_posizioni_limite.set(txt_limite)

        auto_trade = bool(settings.get("auto_trading_attivo", self.cfg.get("auto_trading_attivo", False)))
        buy_rsi = safe_float(settings.get("soglia_acquisto", self.cfg.get("soglia_acquisto", 35)))
        sell_rsi = safe_float(settings.get("soglia_vendita", self.cfg.get("soglia_vendita", 65)))
        stato = "ON" if auto_trade else "OFF"
        nuovo_auto_txt = f"Auto Trade {stato} · Buy RSI≤{buy_rsi:.1f} · Sell RSI≥{sell_rsi:.1f}"
        if self.var_auto_trade_status.get() != nuovo_auto_txt:
            self.var_auto_trade_status.set(nuovo_auto_txt)

    def aggiorna_tabelle(self):
        watchlist = self.status.get("watchlist", {})
        symbols_all = [simbolo_visuale(b) for b in self.cfg.get("crypto_base_list", [])]
        combo_values = symbols_all

        try:
            self.combo_crypto.configure(values=combo_values)
        except Exception:
            pass

        if self._widget_alive("tabella_listino"):
            for item in self.tabella_listino.get_children():
                self.tabella_listino.delete(item)

            for simbolo in symbols_all:
                data = watchlist.get(simbolo, {})
                price = safe_float(data.get("price", 0.0))
                rsi = safe_float(data.get("rsi", 0.0))
                change = safe_float(data.get("change_pct", 0.0))
                source = str(data.get("source", "N/D")).replace("/USDT", "/USDT*")
                has_pos = bool(data.get("has_position", False))
                tag = "positivo" if change > 0 else "negativo" if change < 0 else "neutro"

                self.tabella_listino.insert(
                    "", tk.END, iid=simbolo, text=simbolo,
                    values=(format_price(price), f"{rsi:.1f}", format_pct(change), source, "Sì" if has_pos else "No"),
                    tags=(tag,)
                )

            if self.crypto_selezionata_grafico in self.tabella_listino.get_children():
                self.tabella_listino.selection_set(self.crypto_selezionata_grafico)

        if self._widget_alive("tabella_pancia"):
            for item in self.tabella_pancia.get_children():
                self.tabella_pancia.delete(item)

            for posizione in self.status.get("positions", []):
                simbolo = posizione.get("simbolo", "")
                entry = safe_float(posizione.get("entry", 0.0))
                quantita = safe_float(posizione.get("quantita", 0.0))
                capitale = safe_float(posizione.get("capitale", 0.0))
                valore = safe_float(posizione.get("valore", 0.0))
                pl_eur = safe_float(posizione.get("pl_eur", 0.0))
                pl_pct = safe_float(posizione.get("pl_pct", 0.0))
                tag = "positivo" if pl_eur >= 0 else "negativo"

                self.tabella_pancia.insert(
                    "", tk.END, iid=simbolo, text=simbolo,
                    values=(format_price(entry), f"{quantita:.6f}", format_eur(capitale), format_eur(valore), f"{pl_eur:+.2f} EUR", f"{pl_pct:+.2f}%"),
                    tags=(tag,)
                )

        self.aggiorna_riepilogo_crypto_selezionata()


    def style_axis(self, ax):
        ax.grid(True, color=self.BORDER, linestyle="--", linewidth=0.6, alpha=0.65)
        ax.tick_params(colors=self.MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(self.BORDER)
        ax.title.set_color(self.MUTED)
        ax.xaxis.label.set_color(self.MUTED)
        ax.yaxis.label.set_color(self.MUTED)

    def aggiorna_grafici(self):
        storico = self.status.get("storico_saldi", self.cfg.get("storico_saldi", [self.cfg.get("saldo_eur", 0.0)]))
        ohlc_dict = self.status.get("ohlc", {})
        ohlc = ohlc_dict.get(self.crypto_selezionata_grafico, [])

        if not ohlc and ohlc_dict:
            primo_simbolo = next(iter(ohlc_dict.keys()))
            self.crypto_selezionata_grafico = primo_simbolo
            self.var_crypto.set(primo_simbolo)
            ohlc = ohlc_dict.get(primo_simbolo, [])

        self.ax_crypto.clear()
        self.ax_fondi.clear()

        # Grafico capitale
        try:
            valori = [safe_float(v) for v in storico if safe_float(v) > 0]
            if valori:
                self.ax_fondi.plot(valori, color=self.GREEN, linewidth=2, marker="o", markersize=3)
                self.ax_fondi.set_title("Evoluzione patrimonio/saldo simulato", fontsize=9, fontweight="bold")
            else:
                self.ax_fondi.text(0.5, 0.5, "Nessun dato saldo", ha="center", va="center", color=self.MUTED, transform=self.ax_fondi.transAxes)
        except Exception:
            self.ax_fondi.text(0.5, 0.5, "Grafico saldo non disponibile", ha="center", va="center", color=self.MUTED, transform=self.ax_fondi.transAxes)
        self.style_axis(self.ax_fondi)

        # Grafico crypto / confronto
        if self.confronto_attivo and len(self.crypto_confronto) >= 2:
            almeno_una = False
            for simbolo in self.crypto_confronto:
                dati = ohlc_dict.get(simbolo, [])
                chiusure = [safe_float(c.get("close", 0.0)) for c in dati if safe_float(c.get("close", 0.0)) > 0]
                if len(chiusure) < 2:
                    continue
                base = chiusure[0]
                if base <= 0:
                    continue
                performance = [((prezzo / base) - 1) * 100 for prezzo in chiusure]
                self.ax_crypto.plot(performance, linewidth=1.6, label=simbolo)
                almeno_una = True

            if almeno_una:
                self.ax_crypto.axhline(0, color=self.MUTED, linewidth=0.8, linestyle="--")
                self.ax_crypto.set_title("Confronto performance crypto (%)", fontsize=9, fontweight="bold")
                legend = self.ax_crypto.legend(fontsize=7, loc="best", facecolor=self.PANEL, edgecolor=self.BORDER)
                for text in legend.get_texts():
                    text.set_color(self.TEXT)
                self.lbl_chart_title.config(text="Confronto crypto")
            else:
                self.ax_crypto.text(0.5, 0.5, "Dati insufficienti per il confronto", ha="center", va="center", color=self.MUTED, transform=self.ax_crypto.transAxes)
        else:
            if ohlc:
                if self.modalita_grafico_crypto == "candele":
                    larghezza = 0.55
                    for i, candle in enumerate(ohlc):
                        apertura = safe_float(candle.get("open"))
                        massimo = safe_float(candle.get("high"))
                        minimo = safe_float(candle.get("low"))
                        chiusura = safe_float(candle.get("close"))
                        colore = self.GREEN if chiusura >= apertura else self.RED
                        self.ax_crypto.vlines(i, minimo, massimo, color=colore, linewidth=1)
                        bottom = min(apertura, chiusura)
                        altezza = abs(chiusura - apertura)
                        if altezza == 0:
                            altezza = massimo * 0.0001 if massimo > 0 else 0.0001
                        self.ax_crypto.add_patch(matplotlib.patches.Rectangle((i - larghezza / 2, bottom), larghezza, altezza, facecolor=colore, edgecolor=colore, linewidth=0.8))
                    self.ax_crypto.set_xlim(-1, len(ohlc))
                    self.ax_crypto.set_title(f"Candele live: {self.crypto_selezionata_grafico}", fontsize=9, fontweight="bold")
                else:
                    chiusure = [safe_float(c.get("close", 0.0)) for c in ohlc]
                    self.ax_crypto.plot(chiusure, color=self.BLUE, linewidth=2)
                    if len(chiusure) >= 2:
                        var = ((chiusure[-1] - chiusure[0]) / chiusure[0] * 100) if chiusure[0] > 0 else 0.0
                        self.ax_crypto.set_title(f"Linea live: {self.crypto_selezionata_grafico} · {var:+.2f}%", fontsize=9, fontweight="bold")
                    else:
                        self.ax_crypto.set_title(f"Linea live: {self.crypto_selezionata_grafico}", fontsize=9, fontweight="bold")
                self.lbl_chart_title.config(text=f"Grafico {self.crypto_selezionata_grafico}")
            else:
                self.ax_crypto.text(0.5, 0.5, "In attesa dati mercato...", ha="center", va="center", color=self.MUTED, transform=self.ax_crypto.transAxes)
                self.ax_crypto.set_title("Mercato", fontsize=9, fontweight="bold")

        self.style_axis(self.ax_crypto)
        self.fig.tight_layout(pad=2.0)
        try:
            self.canvas.draw_idle()
        except Exception:
            pass

    def aggiorna_registro(self):
        if not self._widget_alive("tabella_registro"):
            return

        for item in self.tabella_registro.get_children():
            self.tabella_registro.delete(item)

        if not FILE_LOG.exists():
            return

        try:
            rows = leggi_registro_operazioni()[-5:]
            for row in reversed(rows):
                self.tabella_registro.insert(
                    "", tk.END,
                    values=(
                        row.get("Data_Ora", ""),
                        f"{row.get('Crypto', '')} {row.get('Operazione', '')}",
                        format_price(safe_float(row.get("Prezzo_EUR", 0.0))),
                        format_eur(safe_float(row.get("Saldo_EUR", 0.0))),
                        row.get("Note", "")[:120]
                    )
                )
        except Exception as e:
            log_errore("Errore lettura registro dashboard", e)


    def aggiorna_storico_dashboard(self):
        """Aggiorna il riquadro compatto visibile nella colonna sinistra."""
        if not self._widget_alive("tabella_storico_dashboard"):
            return

        for item in self.tabella_storico_dashboard.get_children():
            self.tabella_storico_dashboard.delete(item)

        try:
            rows = leggi_registro_operazioni()
            acquisti = [row for row in rows if self._operazione_is_acquisto(row.get("Operazione", ""))]
            if not acquisti:
                if hasattr(self, "lbl_storico_dashboard"):
                    self.lbl_storico_dashboard.config(text="Nessun acquisto registrato")
                return

            for idx, row in enumerate(reversed(acquisti[-8:])):
                iid = f"dashboard-buy-{idx}"
                self._righe_storico_trade[iid] = row
                self.tabella_storico_dashboard.insert(
                    "", tk.END,
                    iid=iid,
                    values=(
                        row.get("Data_Ora", ""),
                        row.get("Crypto", ""),
                        format_quantita(row.get("Quantita", "")),
                        format_price(safe_float(row.get("Prezzo_EUR", 0.0))),
                        format_eur_detail(row.get("Importo_EUR", "")),
                        format_eur_detail(row.get("Commissione_EUR", "")),
                        format_eur_detail(row.get("Saldo_EUR", "")),
                    ),
                    tags=("acquisto",),
                )

            if hasattr(self, "lbl_storico_dashboard"):
                totale = sum(safe_float(row.get("Importo_EUR", 0.0)) for row in acquisti)
                commissioni = sum(safe_float(row.get("Commissione_EUR", 0.0)) for row in acquisti)
                self.lbl_storico_dashboard.config(
                    text=f"{len(acquisti)} acquisti · Investito {format_eur(totale)} · Comm. {format_eur(commissioni)}"
                )
        except Exception as e:
            log_errore("Errore lettura storico visibile dashboard", e)
            if hasattr(self, "lbl_storico_dashboard"):
                self.lbl_storico_dashboard.config(text="Errore lettura storico operazioni")

    def aggiorna_storico_trade(self):
        """Aggiorna le due tabelle dedicate ad acquisti e vendite."""
        if not self._widget_alive("tabella_acquisti") or not self._widget_alive("tabella_vendite"):
            return

        for tabella in (self.tabella_acquisti, self.tabella_vendite):
            for item in tabella.get_children():
                tabella.delete(item)

        acquisti = []
        vendite = []
        try:
            rows = leggi_registro_operazioni()
            if not rows:
                if hasattr(self, "lbl_storico_trade"):
                    self.lbl_storico_trade.config(text="Nessun registro operazioni trovato")
                return

            for row in rows:
                operazione = row.get("Operazione", "")
                if self._operazione_is_acquisto(operazione):
                    acquisti.append(row)
                elif self._operazione_is_vendita(operazione):
                    vendite.append(row)

            for idx, row in enumerate(reversed(acquisti[-80:])):
                iid = f"acquisto-{idx}"
                self._righe_storico_trade[iid] = row
                self.tabella_acquisti.insert(
                    "", tk.END,
                    iid=iid,
                    text=row.get("Crypto", ""),
                    values=self._valori_riga_trade(row),
                    tags=("acquisto",),
                )

            for idx, row in enumerate(reversed(vendite[-80:])):
                iid = f"vendita-{idx}"
                self._righe_storico_trade[iid] = row
                self.tabella_vendite.insert(
                    "", tk.END,
                    iid=iid,
                    text=row.get("Crypto", ""),
                    values=self._valori_riga_trade(row),
                    tags=(self._tag_operazione(row),),
                )

            if hasattr(self, "lbl_storico_trade"):
                totale_acquisti = sum(safe_float(r.get("Importo_EUR", 0.0)) for r in acquisti)
                totale_vendite = sum(safe_float(r.get("Importo_EUR", 0.0)) for r in vendite)
                profitto_vendite = sum(safe_float(r.get("Profitto_EUR", 0.0)) for r in vendite)
                commissioni = sum(safe_float(r.get("Commissione_EUR", 0.0)) for r in acquisti + vendite)
                self.lbl_storico_trade.config(
                    text=(
                        f"{len(acquisti)} acquisti {format_eur(totale_acquisti)} · "
                        f"{len(vendite)} vendite {format_eur(totale_vendite)} · "
                        f"P/L {profitto_vendite:+.2f} EUR · Comm. {format_eur(commissioni)}"
                    )
                )
        except Exception as e:
            log_errore("Errore lettura storico acquisti/vendite", e)
            if hasattr(self, "lbl_storico_trade"):
                self.lbl_storico_trade.config(text="Errore lettura storico acquisti/vendite")

    def aggiorna_bottoni(self):
        # Mantiene l'interfaccia coerente se la crypto selezionata cambia da tabella.
        if self.var_crypto.get() != self.crypto_selezionata_grafico:
            self.var_crypto.set(self.crypto_selezionata_grafico)

    def run(self):
        self.root.mainloop()



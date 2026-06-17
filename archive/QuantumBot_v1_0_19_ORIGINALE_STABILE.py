import os
import sys
import csv
import time
import json
import uuid
import math
import random
import re
import sqlite3
import hashlib
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import ccxt
except ImportError:
    ccxt = None

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

def _looks_like_quantumbot_data_dir(path: Path) -> bool:
    data_files = (
        "registro_trade.csv",
        "config.json",
        "status_bot.json",
        "eventi_bot.log",
        "QuantumBot.app",
    )
    return path.exists() and any((path / name).exists() for name in data_files)


def _bootstrap_app_directory() -> Path:
    env_dir = os.environ.get("QUANTUMBOT_APP_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser()
        if _looks_like_quantumbot_data_dir(candidate) or candidate.exists():
            return candidate

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        # PyInstaller macOS .app:
        #   dist/QuantumBot.app/Contents/MacOS/QuantumBot -> dist
        for parent in exe_path.parents:
            if parent.suffix == ".app":
                return parent.parent

        # PyInstaller onedir:
        #   dist/QuantumBot/QuantumBot -> dist
        # Se la cartella che contiene l'eseguibile ha un parent che contiene già
        # i dati operativi, preferiamo quel parent invece di scrivere nel bundle.
        for candidate in [exe_path.parent.parent, *exe_path.parents]:
            try:
                if _looks_like_quantumbot_data_dir(candidate):
                    return candidate
            except Exception:
                pass
        return exe_path.parent

    try:
        script_dir = Path(__file__).resolve().parent
    except Exception:
        script_dir = Path.cwd()

    dist_dir = script_dir / "dist"
    if _looks_like_quantumbot_data_dir(dist_dir):
        return dist_dir
    return script_dir


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def configure_matplotlib_cache():
    """Imposta una cache Matplotlib scrivibile prima dell'import di matplotlib."""
    candidates = []
    existing = os.environ.get("MPLCONFIGDIR")
    if existing:
        candidates.append(Path(existing).expanduser())

    candidates.append(_bootstrap_app_directory() / ".quantumbot_matplotlib")

    try:
        user_id = os.getuid()
    except Exception:
        user_id = "user"
    candidates.append(Path(tempfile.gettempdir()) / f"quantumbot_matplotlib_{user_id}")

    for candidate in candidates:
        if _is_writable_directory(candidate):
            os.environ["MPLCONFIGDIR"] = str(candidate)
            return


configure_matplotlib_cache()

import matplotlib
try:
    # TkAgg è il backend corretto per la dashboard Tkinter su macOS.
    # In ambienti senza interfaccia grafica, per esempio durante test/compile,
    # usiamo Agg per evitare crash all'import.
    if sys.platform == "darwin" or os.name == "nt" or os.environ.get("DISPLAY"):
        matplotlib.use("TkAgg")
    else:
        matplotlib.use("Agg")
except Exception:
    pass
import matplotlib.patches
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


# ============================================================
# QUANTUM BOT STUDIO v1.0.0 - DASHBOARD COMPATTA + POPUP
# Dashboard + Engine separato, ottimizzato per macOS.
#
# - Se chiudi la Dashboard, l'Engine continua a lavorare.
# - Per fermare davvero il bot usa il pulsante "Ferma Bot".
# - Trading simulato interno: compra, vendi 25/50/75/100, vendi importo EUR libero, chiudi tutto.
# - Dati reali Binance con fallback BASE/EUR -> BASE/USDT convertito in EUR.
# - Grafico linea, candele e confronto multi-crypto normalizzato in %.
# - Auto Profit/Loss con Take Profit e Stop Loss configurabili.
# - Layout più morbido: pulsanti arrotondati, card soft e pannelli meno squadrati.
# - Percentuale investimento visibile e configurabile dalla dashboard.
# - Auto Trade opzionale: acquisto/vendita automatica simulata su soglie RSI.
# - Modalità trading selezionabile: Conservativa, Normale, Aggressiva, Ultra.
# - Dashboard scrollabile verticalmente/orizzontalmente per finestre ridotte.
# - Tema visuale più moderno: palette deep navy, pannelli soft, bordi sottili.
# ============================================================

APP_VERSION = "v1.0.19"
BUILD_NOTE = "v1.0.18 dashboard fix: Vendi EUR visibile + AutoCharts sempre ON"
# v1.0.19: patch conservativa da v1.0.18 corretta.
# Mantiene vendita manuale EUR libera, vendita percentuale, EngineGuard, FeeAware,
# MaxPositionsFix, SQLite/CSV e dashboard. Mantiene AutoCharts sempre attivo
# e rende Vendi EUR visibile in dashboard su una riga dedicata, senza affidarsi
# alla stessa riga di Crypto/Importo EUR.
ENGINE_ENV_FLAG = "QUANTUMBOT_ENGINE_MODE"


# ============================================================
# PERCORSI
# ============================================================

def get_app_directory() -> Path:
    """
    Cartella in cui salvare config/log/status.

    Script .py:
        cartella del file .py

    PyInstaller macOS .app:
        cartella che contiene QuantumBot.app
    """
    return _bootstrap_app_directory()


APP_DIR = get_app_directory()
FILE_CONFIG = APP_DIR / "config.json"
FILE_STATUS = APP_DIR / "status_bot.json"
FILE_COMMANDI = APP_DIR / "comandi_bot.jsonl"
FILE_COMMAND_STATE = APP_DIR / "comandi_state.json"
FILE_PID = APP_DIR / "engine.pid"
FILE_ENGINE_LOCK = APP_DIR / "engine.lock"
FILE_LOG = APP_DIR / "registro_trade.csv"
FILE_ERRORI = APP_DIR / "errori_bot.log"
FILE_EVENTI = APP_DIR / "eventi_bot.log"
FILE_ENGINE_STDOUT = APP_DIR / "engine_stdout.log"
FILE_ENGINE_STDERR = APP_DIR / "engine_stderr.log"
FILE_ENGINE_LAUNCH = APP_DIR / "engine_launch.log"
FILE_DB = APP_DIR / "quantumbot.db"
FILE_REPORT_TXT = APP_DIR / "report_quantumbot.txt"
FILE_REPORT_JSON = APP_DIR / "report_quantumbot.json"
FILE_REPORT_CSV = APP_DIR / "report_quantumbot.csv"
FILE_REPORT_EQUITY_PNG = APP_DIR / "report_sqlite_equity.png"
FILE_REPORT_PNL_CRYPTO_PNG = APP_DIR / "report_sqlite_pnl_crypto.png"
FILE_REPORT_DRAWDOWN_PNG = APP_DIR / "report_sqlite_drawdown.png"


def ensure_runtime_files():
    """Crea i file log minimi se mancano.

    In una build PyInstaller/windowed il pulsante "Errori" non deve mostrare
    "File non trovato" solo perché non si è ancora verificato nessun errore.
    """
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    for path in (FILE_ERRORI, FILE_EVENTI, FILE_ENGINE_STDOUT, FILE_ENGINE_STDERR, FILE_ENGINE_LAUNCH):
        try:
            Path(path).touch(exist_ok=True)
        except Exception:
            pass


ensure_runtime_files()

# Safety guard: i dati fallback servono solo a non lasciare vuota la dashboard.
# Non devono mai attivare acquisti, vendite, take profit o stop loss automatici.
AUTO_TRADE_REQUIRES_REAL_DATA = True
MAX_PRICE_JUMP_TRADE_PCT = 8.0
USDT_EUR_MAX_AGE_SECONDS = 300
PRICE_JUMP_CONFIRMATIONS = 3
PRICE_JUMP_STABILITY_PCT = 2.0

REGISTRO_FIELDNAMES = [
    "Data_Ora",
    "Crypto",
    "Operazione",
    "Prezzo_EUR",
    "RSI",
    "Saldo_EUR",
    "Quantita",
    "Importo_EUR",
    "Commissione_EUR",
    "Profitto_EUR",
    "Percentuale",
    "Note",
]


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


# ============================================================
# MODALITÀ TRADING
# ============================================================

TRADING_MODE_PRESETS = {
    "Conservativa": {
        "risk_percent": 3.0,
        "buy_rsi": 30.0,
        "sell_rsi": 70.0,
        "take_profit": 2.5,
        "stop_loss": 3.0,
        "timeframe": "5m",
        "profitto_minimo_netto": 0.70,
        "cooldown_minuti": 20,
        "max_posizioni": 5,
        "descrizione": "Pochi ingressi, posizione piccola, segnali più selettivi."
    },
    "Normale": {
        "risk_percent": 5.0,
        "buy_rsi": 35.0,
        "sell_rsi": 65.0,
        "take_profit": 2.0,
        "stop_loss": 3.0,
        "timeframe": "1m",
        "profitto_minimo_netto": 0.45,
        "cooldown_minuti": 12,
        "max_posizioni": 8,
        "descrizione": "Equilibrio tra frequenza operativa e prudenza."
    },
    "Aggressiva": {
        "risk_percent": 10.0,
        "buy_rsi": 45.0,
        "sell_rsi": 60.0,
        "take_profit": 1.5,
        "stop_loss": 2.5,
        "timeframe": "1m",
        "profitto_minimo_netto": 0.35,
        "cooldown_minuti": 8,
        "max_posizioni": 10,
        "descrizione": "Più ingressi, posizione più grande, uscite più rapide."
    },
    "Ultra aggressiva": {
        "risk_percent": 15.0,
        "buy_rsi": 50.0,
        "sell_rsi": 58.0,
        "take_profit": 1.0,
        "stop_loss": 2.0,
        "timeframe": "1m",
        "profitto_minimo_netto": 0.25,
        "cooldown_minuti": 5,
        "max_posizioni": 12,
        "descrizione": "Massima frequenza simulata: molti falsi segnali possibili."
    },
}


def normalizza_modalita_trading(value):
    value = str(value or "Normale").strip()
    if value.lower() == "personalizzata":
        return "Personalizzata"
    if value.lower() in {"ultra", "ultra aggressiva", "ultra-aggressiva"}:
        return "Ultra aggressiva"
    for nome in TRADING_MODE_PRESETS:
        if value.lower() == nome.lower():
            return nome
    return "Normale"


def applica_modalita_a_config(cfg, modalita):
    modalita = normalizza_modalita_trading(modalita)
    if modalita == "Personalizzata":
        modalita = "Normale"
    preset = TRADING_MODE_PRESETS[modalita]
    cfg["modalita_trading"] = modalita
    cfg["percentuale_rischio_per_trade"] = float(preset["risk_percent"])
    cfg["soglia_acquisto"] = float(preset["buy_rsi"])
    cfg["soglia_vendita"] = float(preset["sell_rsi"])
    cfg["take_profit_percentuale"] = float(preset["take_profit"])
    cfg["stop_loss_percentuale"] = float(preset["stop_loss"])
    cfg["timeframe"] = preset["timeframe"] if preset["timeframe"] in VALID_TIMEFRAMES else "1m"
    cfg["profitto_minimo_netto_percentuale"] = float(preset.get("profitto_minimo_netto", cfg.get("profitto_minimo_netto_percentuale", 0.45)))
    cfg["cooldown_trade_minuti"] = int(preset.get("cooldown_minuti", cfg.get("cooldown_trade_minuti", 12)))
    cfg["max_posizioni_aperte"] = int(preset.get("max_posizioni", cfg.get("max_posizioni_aperte", 8)))
    return modalita, preset


def descrizione_modalita_trading(modalita):
    modalita = normalizza_modalita_trading(modalita)
    if modalita == "Personalizzata":
        return "Personalizzata"
    p = TRADING_MODE_PRESETS[modalita]
    return (
        f"{modalita} · Inv. {p['risk_percent']:.0f}% · "
        f"Buy RSI≤{p['buy_rsi']:.0f} · Sell RSI≥{p['sell_rsi']:.0f} · "
        f"TP {p['take_profit']:.1f}% · SL {p['stop_loss']:.1f}% · "
        f"Net min {p.get('profitto_minimo_netto', 0.45):.2f}% · "
        f"CD {p.get('cooldown_minuti', 12)}m · Max pos {p.get('max_posizioni', 8)} · TF {p['timeframe']}"
    )


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_CONFIG = {
    "crypto_base_list": [
        "BTC", "ETH", "SOL", "BNB", "XRP",
        "ADA", "DOGE", "DOT", "LINK", "SHIB",
        "AVAX", "MATIC", "LTC", "UNI", "NEAR",
        "ATOM", "ICP", "XLM", "ETC", "FIL"
    ],
    "timeframe": "1m",
    "periodo_rsi": 14,
    "soglia_acquisto": 35,
    "soglia_vendita": 65,
    "take_profit_percentuale": 2.0,
    "stop_loss_percentuale": 3.0,
    "auto_profit_loss_attivo": True,
    "auto_trading_attivo": False,
    "modalita_trading": "Normale",
    "percentuale_rischio_per_trade": 5.0,
    "commissione_percentuale": 0.1,
    "fee_aware_sell_attivo": True,
    "profitto_minimo_netto_percentuale": 0.45,
    "cooldown_trade_minuti": 12,
    "max_posizioni_aperte": 8,
    "ultimo_trade_ts": {},
    "saldo_eur": 5000.0,
    "totale_acquisti": 0,
    "totale_vendite": 0,
    "profitto_accumulato": 0.0,
    "storico_saldi": [5000.0],
    "crypto_in_pancia": {},
    "prezzo_acquisto_effettivo": {},
    "importo_speso_effettivo": {},
    "posizioni_aperte": {}
}

VALID_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h"]


# ============================================================
# UTILITY FILE / LOG
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_eur(value, decimals=2):
    try:
        return f"{float(value):,.{decimals}f} EUR".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0,00 EUR"


def format_price(value):
    try:
        value = float(value)
        if abs(value) < 1:
            return f"{value:.8f} EUR"
        if abs(value) < 100:
            return f"{value:.4f} EUR"
        return f"{value:.2f} EUR"
    except Exception:
        return "-"


def format_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "+0.00%"


def has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


def format_quantita(value):
    if not has_value(value):
        return "-"
    try:
        value = float(str(value).replace(",", "."))
        if abs(value) < 0.000001:
            return f"{value:.10f}".rstrip("0").rstrip(".") or "0"
        if abs(value) < 1:
            return f"{value:.8f}".rstrip("0").rstrip(".")
        return f"{value:.6f}".rstrip("0").rstrip(".")
    except Exception:
        return "-"


def format_eur_detail(value):
    if not has_value(value):
        return "-"
    return format_eur(safe_float(value))


def format_signed_eur(value):
    if not has_value(value):
        return "-"
    value = safe_float(value)
    return f"{value:+.2f} EUR"


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0

    value = str(value).strip().lower()
    if value in {"1", "true", "vero", "yes", "si", "sì", "on"}:
        return True
    if value in {"0", "false", "falso", "no", "off"}:
        return False
    return default


def normalizza_lista_crypto(lista):
    risultato = []
    if not isinstance(lista, list):
        lista = DEFAULT_CONFIG["crypto_base_list"]

    for item in lista:
        base = str(item).split("/")[0].strip().upper()
        if base and base not in risultato:
            risultato.append(base)

    return risultato or list(DEFAULT_CONFIG["crypto_base_list"])


def simbolo_visuale(base: str) -> str:
    return f"{str(base).split('/')[0].strip().upper()}/EUR"


def log_errore(messaggio, eccezione=None):
    try:
        with open(FILE_ERRORI, "a", encoding="utf-8") as f:
            if eccezione is None:
                f.write(f"[{now_str()}] {messaggio}\n")
            else:
                f.write(f"[{now_str()}] {messaggio} | {repr(eccezione)}\n")
    except Exception:
        pass


def log_evento(messaggio):
    try:
        with open(FILE_EVENTI, "a", encoding="utf-8") as f:
            f.write(f"[{now_str()}] {messaggio}\n")
    except Exception:
        pass


_PATHS_LOGGED = set()


def log_percorsi_operativi(contesto="runtime"):
    """Logga in modo esplicito la cartella dati e i file critici usati."""
    key = (os.getpid(), str(contesto))
    if key in _PATHS_LOGGED:
        return
    _PATHS_LOGGED.add(key)
    dettagli = [
        f"cartella dati={APP_DIR}",
        f"config.json={FILE_CONFIG}",
        f"registro_trade.csv={FILE_LOG}",
        f"quantumbot.db={FILE_DB}",
        f"status_bot.json={FILE_STATUS}",
        f"engine.lock={FILE_ENGINE_LOCK}",
        f"engine.pid={FILE_PID}",
    ]
    for dettaglio in dettagli:
        log_evento(f"[PERCORSI:{contesto}] {dettaglio}")


def load_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return default
        return json.loads(text)
    except Exception as e:
        log_errore(f"Errore lettura JSON: {path}", e)
        return default


def save_json_atomic(path: Path, data):
    """
    Salvataggio atomico robusto.

    Nota: più processi possono scrivere status_bot.json/comandi_state.json.
    Usare sempre un file temporaneo univoco evita la race condition:
    processo A sposta status_bot.json.tmp mentre processo B sta per fare os.replace()
    e B trova il temp già sparito -> FileNotFoundError.
    """
    temp = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        unique = f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        temp = path.parent / unique
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(temp, path)
        if os.name != "nt":
            dir_fd = None
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                os.fsync(dir_fd)
            except Exception:
                pass
            finally:
                try:
                    if dir_fd is not None:
                        os.close(dir_fd)
                except Exception:
                    pass
    except Exception as e:
        log_errore(f"Errore salvataggio JSON: {path}", e)
        try:
            if temp is not None and Path(temp).exists():
                Path(temp).unlink()
        except Exception:
            pass


def save_text_atomic(path: Path, text: str):
    """Scrive piccoli file di stato testuali senza finestre di file vuoto."""
    temp = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        with open(temp, "w", encoding="utf-8") as f:
            f.write(str(text))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(temp, path)
        if os.name != "nt":
            dir_fd = None
            try:
                dir_fd = os.open(str(path.parent), os.O_RDONLY)
                os.fsync(dir_fd)
            except Exception:
                pass
            finally:
                try:
                    if dir_fd is not None:
                        os.close(dir_fd)
                except Exception:
                    pass
    except Exception as e:
        log_errore(f"Errore salvataggio testo: {path}", e)
        try:
            if temp is not None and Path(temp).exists():
                Path(temp).unlink()
        except Exception:
            pass


def deep_copy_json(obj):
    return json.loads(json.dumps(obj))


def inizializza_dizionari_config(cfg):
    cfg["crypto_base_list"] = normalizza_lista_crypto(cfg.get("crypto_base_list", DEFAULT_CONFIG["crypto_base_list"]))

    for key in ("crypto_in_pancia", "prezzo_acquisto_effettivo", "importo_speso_effettivo", "posizioni_aperte", "ultimo_trade_ts"):
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}

    for base in cfg["crypto_base_list"]:
        sym = simbolo_visuale(base)
        cfg["crypto_in_pancia"].setdefault(sym, 0.0)
        cfg["prezzo_acquisto_effettivo"].setdefault(sym, 0.0)
        cfg["importo_speso_effettivo"].setdefault(sym, 0.0)
        cfg["posizioni_aperte"].setdefault(sym, False)
        cfg["ultimo_trade_ts"].setdefault(sym, 0.0)
        cfg["crypto_in_pancia"][sym] = safe_float(cfg["crypto_in_pancia"].get(sym, 0.0))
        cfg["prezzo_acquisto_effettivo"][sym] = safe_float(cfg["prezzo_acquisto_effettivo"].get(sym, 0.0))
        cfg["importo_speso_effettivo"][sym] = safe_float(cfg["importo_speso_effettivo"].get(sym, 0.0))
        cfg["posizioni_aperte"][sym] = safe_bool(cfg["posizioni_aperte"].get(sym, False))
        cfg["ultimo_trade_ts"][sym] = safe_float(cfg["ultimo_trade_ts"].get(sym, 0.0))


def carica_config():
    cfg = deep_copy_json(DEFAULT_CONFIG)

    if FILE_CONFIG.exists():
        loaded = load_json_file(FILE_CONFIG, {})
        if isinstance(loaded, dict):
            cfg.update(loaded)

    # Compatibilità con vecchi config.
    if "crypto_da_monitorare" in cfg and "crypto_base_list" not in cfg:
        cfg["crypto_base_list"] = cfg["crypto_da_monitorare"]

    cfg["crypto_base_list"] = normalizza_lista_crypto(cfg.get("crypto_base_list", DEFAULT_CONFIG["crypto_base_list"]))

    if cfg.get("timeframe") not in VALID_TIMEFRAMES:
        cfg["timeframe"] = "1m"

    # Compatibilità con vecchi config: queste chiavi possono mancare se arrivi da v6.8/v6.9.
    cfg.setdefault("auto_profit_loss_attivo", True)
    cfg.setdefault("auto_trading_attivo", False)
    cfg["modalita_trading"] = normalizza_modalita_trading(cfg.get("modalita_trading", "Normale"))
    cfg.setdefault("percentuale_rischio_per_trade", 5.0)
    cfg.setdefault("soglia_acquisto", 35)
    cfg.setdefault("soglia_vendita", 65)

    cfg["periodo_rsi"] = max(2, safe_int(cfg.get("periodo_rsi", DEFAULT_CONFIG["periodo_rsi"]), DEFAULT_CONFIG["periodo_rsi"]))
    cfg["soglia_acquisto"] = min(99.0, max(1.0, safe_float(cfg.get("soglia_acquisto"), DEFAULT_CONFIG["soglia_acquisto"])))
    cfg["soglia_vendita"] = min(99.0, max(1.0, safe_float(cfg.get("soglia_vendita"), DEFAULT_CONFIG["soglia_vendita"])))
    cfg["take_profit_percentuale"] = min(100.0, max(0.1, safe_float(cfg.get("take_profit_percentuale"), DEFAULT_CONFIG["take_profit_percentuale"])))
    cfg["stop_loss_percentuale"] = min(100.0, max(0.1, safe_float(cfg.get("stop_loss_percentuale"), DEFAULT_CONFIG["stop_loss_percentuale"])))
    cfg["percentuale_rischio_per_trade"] = min(100.0, max(0.1, safe_float(cfg.get("percentuale_rischio_per_trade"), DEFAULT_CONFIG["percentuale_rischio_per_trade"])))
    cfg["commissione_percentuale"] = min(10.0, max(0.0, safe_float(cfg.get("commissione_percentuale"), DEFAULT_CONFIG["commissione_percentuale"])))
    cfg["fee_aware_sell_attivo"] = safe_bool(cfg.get("fee_aware_sell_attivo", DEFAULT_CONFIG["fee_aware_sell_attivo"]), True)
    cfg["profitto_minimo_netto_percentuale"] = min(10.0, max(0.0, safe_float(cfg.get("profitto_minimo_netto_percentuale"), DEFAULT_CONFIG["profitto_minimo_netto_percentuale"])))
    cfg["cooldown_trade_minuti"] = min(240, max(0, safe_int(cfg.get("cooldown_trade_minuti"), DEFAULT_CONFIG["cooldown_trade_minuti"])))
    cfg["max_posizioni_aperte"] = min(99, max(1, safe_int(cfg.get("max_posizioni_aperte"), DEFAULT_CONFIG["max_posizioni_aperte"])))
    cfg["saldo_eur"] = max(0.0, safe_float(cfg.get("saldo_eur"), DEFAULT_CONFIG["saldo_eur"]))
    cfg["profitto_accumulato"] = safe_float(cfg.get("profitto_accumulato"), DEFAULT_CONFIG["profitto_accumulato"])
    cfg["totale_acquisti"] = max(0, safe_int(cfg.get("totale_acquisti"), DEFAULT_CONFIG["totale_acquisti"]))
    cfg["totale_vendite"] = max(0, safe_int(cfg.get("totale_vendite"), DEFAULT_CONFIG["totale_vendite"]))
    cfg["auto_profit_loss_attivo"] = safe_bool(cfg.get("auto_profit_loss_attivo"), True)
    cfg["auto_trading_attivo"] = safe_bool(cfg.get("auto_trading_attivo"), False)

    if not isinstance(cfg.get("storico_saldi"), list) or len(cfg.get("storico_saldi", [])) == 0:
        cfg["storico_saldi"] = [cfg["saldo_eur"]]
    else:
        cfg["storico_saldi"] = [round(max(0.0, safe_float(v, cfg["saldo_eur"])), 2) for v in cfg["storico_saldi"][-300:]]

    inizializza_dizionari_config(cfg)
    return cfg


def salva_config(cfg):
    inizializza_dizionari_config(cfg)
    save_json_atomic(FILE_CONFIG, cfg)


def backup_dati_operativi(motivo="backup"):
    """Crea un backup non distruttivo dei file dati principali.

    Il report deve essere solo lettura/generazione file. Questo backup serve come
    paracadute prima di operazioni di audit/report, in modo da poter recuperare
    config, registro e database anche se l'app viene chiusa o se macOS/PyInstaller
    usa una cartella dati inattesa.
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_motivo = re.sub(r"[^A-Za-z0-9_-]+", "_", str(motivo or "backup")).strip("_") or "backup"
        backup_dir = APP_DIR / "backups" / f"{timestamp}_{safe_motivo}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        files_to_copy = [
            FILE_CONFIG,
            FILE_LOG,
            FILE_DB,
            APP_DIR / f"{FILE_DB.name}-wal",
            APP_DIR / f"{FILE_DB.name}-shm",
            FILE_STATUS,
            FILE_ERRORI,
            FILE_EVENTI,
        ]

        copied = []
        for path in files_to_copy:
            try:
                if path.exists() and path.is_file():
                    shutil.copy2(path, backup_dir / path.name)
                    copied.append(path.name)
            except Exception as copy_error:
                log_errore(f"Backup non riuscito per {path.name}", copy_error)

        log_evento(f"Backup dati creato: {backup_dir.name} ({', '.join(copied) if copied else 'nessun file copiato'})")
        return backup_dir
    except Exception as e:
        log_errore("Errore creazione backup dati", e)
        return None


def _parse_numero_da_testo(pattern, text, default=""):
    match = re.search(pattern, str(text or ""), flags=re.IGNORECASE)
    if not match:
        return default
    return str(match.group(1)).replace(",", ".")


def normalizza_riga_registro(row):
    row = dict(row or {})
    for field in REGISTRO_FIELDNAMES:
        row.setdefault(field, "")

    note = row.get("Note", "")
    operazione = str(row.get("Operazione", "") or "").upper()
    prezzo = safe_float(row.get("Prezzo_EUR", 0.0))

    if not has_value(row.get("Importo_EUR")):
        importo = _parse_numero_da_testo(r"(?:Importo|Ricavo netto)\s+([+-]?\d+(?:[\.,]\d+)?)\s*EUR", note)
        if importo:
            row["Importo_EUR"] = f"{safe_float(importo):.2f}"

    if not has_value(row.get("Profitto_EUR")):
        profitto = _parse_numero_da_testo(r"P/L\s*([+-]?\d+(?:[\.,]\d+)?)\s*EUR", note)
        if profitto:
            row["Profitto_EUR"] = f"{safe_float(profitto):.2f}"

    if not has_value(row.get("Percentuale")):
        percentuale = _parse_numero_da_testo(r"quota\s+([+-]?\d+(?:[\.,]\d+)?)\s*%", note)
        if percentuale:
            row["Percentuale"] = f"{safe_float(percentuale):.2f}"
        elif "COMPRA" in operazione or "BUY" in operazione:
            row["Percentuale"] = "100.00"

    if not has_value(row.get("Commissione_EUR")):
        commissione_eur = _parse_numero_da_testo(r"Commissione\s+([+-]?\d+(?:[\.,]\d+)?)\s*EUR", note)
        if commissione_eur:
            row["Commissione_EUR"] = f"{safe_float(commissione_eur):.2f}"
        else:
            commissione_pct = _parse_numero_da_testo(r"Commissione\s+([+-]?\d+(?:[\.,]\d+)?)\s*%", note)
            importo = safe_float(row.get("Importo_EUR", 0.0))
            if commissione_pct and importo > 0:
                row["Commissione_EUR"] = f"{importo * safe_float(commissione_pct) / 100:.2f}"

    if not has_value(row.get("Quantita")):
        importo = safe_float(row.get("Importo_EUR", 0.0))
        commissione = safe_float(row.get("Commissione_EUR", 0.0))
        if prezzo > 0 and importo > 0 and ("COMPRA" in operazione or "BUY" in operazione):
            row["Quantita"] = f"{max(0.0, importo - commissione) / prezzo:.12f}"

    return row


# ============================================================
# SQLITE ADD-ON: DATABASE, REPORT E AUDIT DATI
# ============================================================

SQLITE_SCHEMA_VERSION = "1.0"
SQLITE_SNAPSHOT_INTERVAL_SECONDS = 30
_ultimo_snapshot_sqlite = 0.0


def _sqlite_connect():
    conn = sqlite3.connect(FILE_DB, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def _sqlite_connect_readonly():
    """Connessione SQLite di sola lettura per dashboard/report."""
    if not FILE_DB.exists():
        raise FileNotFoundError(str(FILE_DB))
    db_path = FILE_DB.resolve()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_columns(conn, table):
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _ensure_sqlite_column(conn, table, column, definition):
    cols = _sqlite_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")



def inizializza_database_sqlite():
    """Crea il database SQLite senza modificare la logica originale del bot.

    SQLite è un add-on laterale: il CSV resta attivo come backup, mentre il DB
    serve per report, audit PnL e snapshot equity.
    """
    try:
        with _sqlite_connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                chiave TEXT PRIMARY KEY,
                valore TEXT
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT UNIQUE,
                data_ora TEXT NOT NULL,
                modalita TEXT NOT NULL DEFAULT 'SIMULAZIONE',
                crypto TEXT NOT NULL,
                operazione TEXT NOT NULL,
                prezzo_eur REAL DEFAULT 0,
                rsi REAL DEFAULT 0,
                saldo_eur REAL DEFAULT 0,
                quantita REAL DEFAULT 0,
                importo_eur REAL DEFAULT 0,
                commissione_eur REAL DEFAULT 0,
                profitto_eur REAL DEFAULT 0,
                percentuale REAL DEFAULT 0,
                note TEXT,
                created_at TEXT NOT NULL
            );


            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ora TEXT NOT NULL,
                modalita TEXT NOT NULL DEFAULT 'SIMULAZIONE',
                saldo_cash REAL DEFAULT 0,
                capitale_investito REAL DEFAULT 0,
                valore_posizioni REAL DEFAULT 0,
                equity_totale REAL DEFAULT 0,
                profitto_realizzato REAL DEFAULT 0,
                profitto_non_realizzato REAL DEFAULT 0,
                posizioni_aperte INTEGER DEFAULT 0,
                drawdown_pct REAL DEFAULT 0,
                motivo TEXT
            );


            CREATE TABLE IF NOT EXISTS current_positions (
                crypto TEXT PRIMARY KEY,
                aperta INTEGER DEFAULT 0,
                quantita REAL DEFAULT 0,
                prezzo_medio_acquisto REAL DEFAULT 0,
                importo_investito REAL DEFAULT 0,
                prezzo_corrente REAL DEFAULT 0,
                valore_attuale REAL DEFAULT 0,
                pnl_non_realizzato REAL DEFAULT 0,
                pnl_pct REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data_ora TEXT NOT NULL,
                livello TEXT,
                messaggio TEXT,
                dettagli TEXT
            );
            """)
            # Migrazione non distruttiva: se esiste un vecchio quantumbot.db
            # creato da una build precedente, aggiungiamo solo le colonne mancanti.
            for col, definition in {
                "source_id": "TEXT",
                "data_ora": "TEXT",
                "modalita": "TEXT DEFAULT 'SIMULAZIONE'",
                "crypto": "TEXT",
                "operazione": "TEXT",
                "prezzo_eur": "REAL DEFAULT 0",
                "rsi": "REAL DEFAULT 0",
                "saldo_eur": "REAL DEFAULT 0",
                "quantita": "REAL DEFAULT 0",
                "importo_eur": "REAL DEFAULT 0",
                "commissione_eur": "REAL DEFAULT 0",
                "profitto_eur": "REAL DEFAULT 0",
                "percentuale": "REAL DEFAULT 0",
                "note": "TEXT",
                "created_at": "TEXT",
            }.items():
                _ensure_sqlite_column(conn, "trades", col, definition)

            for col, definition in {
                "data_ora": "TEXT",
                "modalita": "TEXT DEFAULT 'SIMULAZIONE'",
                "saldo_cash": "REAL DEFAULT 0",
                "capitale_investito": "REAL DEFAULT 0",
                "valore_posizioni": "REAL DEFAULT 0",
                "equity_totale": "REAL DEFAULT 0",
                "profitto_realizzato": "REAL DEFAULT 0",
                "profitto_non_realizzato": "REAL DEFAULT 0",
                "posizioni_aperte": "INTEGER DEFAULT 0",
                "drawdown_pct": "REAL DEFAULT 0",
                "motivo": "TEXT",
            }.items():
                _ensure_sqlite_column(conn, "equity_snapshots", col, definition)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_data ON trades(data_ora)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_crypto ON trades(crypto)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_operazione ON trades(operazione)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_equity_data ON equity_snapshots(data_ora)")
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_source_id ON trades(source_id)")
            except Exception:
                # Se un DB precedente contiene duplicati, il bot deve continuare a funzionare.
                pass

            meta_cols = _sqlite_columns(conn, "meta")
            if {"chiave", "valore"}.issubset(meta_cols):
                conn.execute(
                    "INSERT OR REPLACE INTO meta (chiave, valore) VALUES (?, ?)",
                    ("schema_version", SQLITE_SCHEMA_VERSION),
                )
            elif {"key", "value"}.issubset(meta_cols):
                if "updated_at" in meta_cols:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
                        ("schema_version", SQLITE_SCHEMA_VERSION, now_str()),
                    )
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        ("schema_version", SQLITE_SCHEMA_VERSION),
                    )
            conn.commit()
    except Exception as e:
        # Non bloccare mai il bot per un problema al database.
        try:
            with open(FILE_ERRORI, "a", encoding="utf-8") as f:
                f.write(f"[{now_str()}] Errore inizializzazione SQLite | {repr(e)}\n")
        except Exception:
            pass


def _trade_source_id(row):
    base = "|".join(str(row.get(k, "")) for k in REGISTRO_FIELDNAMES)
    return hashlib.sha256(base.encode("utf-8", errors="replace")).hexdigest()


def salva_trade_sqlite(row, source_id=None):
    """Salva una riga trade nel DB. Non solleva eccezioni verso il bot.

    È compatibile sia con il nuovo schema v1.0-stable-add-on sia con database
    creati da build precedenti, perché compila dinamicamente solo le colonne
    presenti nella tabella.
    """
    try:
        inizializza_database_sqlite()
        row = normalizza_riga_registro(row)
        source_id = source_id or _trade_source_id(row)
        modalita = row.get("Modalita") or "SIMULAZIONE"
        created = now_str()
        valori = {
            "source_id": source_id,
            "unique_key": source_id,
            "trade_uid": source_id,
            "imported_at": created,
            "created_at": created,
            "source": "registro_trade.csv",
            "mode": modalita,
            "modalita": modalita,
            "data_ora": row.get("Data_Ora") or created,
            "symbol": row.get("Crypto", ""),
            "crypto": row.get("Crypto", ""),
            "operation": row.get("Operazione", ""),
            "operazione": row.get("Operazione", ""),
            "price_eur": safe_float(row.get("Prezzo_EUR", 0.0)),
            "prezzo_eur": safe_float(row.get("Prezzo_EUR", 0.0)),
            "prezzo": safe_float(row.get("Prezzo_EUR", 0.0)),
            "rsi": safe_float(row.get("RSI", 0.0)),
            "balance_eur": safe_float(row.get("Saldo_EUR", 0.0)),
            "saldo_eur": safe_float(row.get("Saldo_EUR", 0.0)),
            "saldo_dopo": safe_float(row.get("Saldo_EUR", 0.0)),
            "saldo_prima": safe_float(row.get("Saldo_Prima_EUR", row.get("Saldo_EUR", 0.0))),
            "quantity": safe_float(row.get("Quantita", 0.0)),
            "quantita": safe_float(row.get("Quantita", 0.0)),
            "amount_eur": safe_float(row.get("Importo_EUR", 0.0)),
            "importo_eur": safe_float(row.get("Importo_EUR", 0.0)),
            "importo": safe_float(row.get("Importo_EUR", 0.0)),
            "fee_eur": safe_float(row.get("Commissione_EUR", 0.0)),
            "commissione_eur": safe_float(row.get("Commissione_EUR", 0.0)),
            "commissione": safe_float(row.get("Commissione_EUR", 0.0)),
            "pnl_eur": safe_float(row.get("Profitto_EUR", 0.0)),
            "profitto_eur": safe_float(row.get("Profitto_EUR", 0.0)),
            "profitto_perdita": safe_float(row.get("Profitto_EUR", 0.0)),
            "percent": safe_float(row.get("Percentuale", 0.0)),
            "percentuale": safe_float(row.get("Percentuale", 0.0)),
            "note": row.get("Note", ""),
        }
        with _sqlite_connect() as conn:
            cols_presenti = _sqlite_columns(conn, "trades")
            cols = [c for c in valori.keys() if c in cols_presenti]
            placeholders = ", ".join(["?"] * len(cols))
            sql = f"INSERT OR IGNORE INTO trades ({', '.join(cols)}) VALUES ({placeholders})"
            conn.execute(sql, [valori[c] for c in cols])
            conn.commit()
    except Exception as e:
        try:
            with open(FILE_ERRORI, "a", encoding="utf-8") as f:
                f.write(f"[{now_str()}] Errore scrittura trade SQLite | {repr(e)}\n")
        except Exception:
            pass



def inizializza_file_registro():
    try:
        if not FILE_LOG.exists():
            with open(FILE_LOG, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=REGISTRO_FIELDNAMES)
                writer.writeheader()
            return

        with open(FILE_LOG, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            if all(field in fieldnames for field in REGISTRO_FIELDNAMES):
                return
            rows = list(reader)

        with open(FILE_LOG, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REGISTRO_FIELDNAMES)
            writer.writeheader()
            for row in rows:
                row = normalizza_riga_registro(row)
                writer.writerow({field: row.get(field, "") for field in REGISTRO_FIELDNAMES})
    except Exception as e:
        log_errore("Errore creazione registro trade", e)


def registra_operazione(
    simbolo,
    operazione,
    prezzo,
    rsi,
    saldo,
    note="",
    quantita=0.0,
    importo=0.0,
    commissione=0.0,
    profitto=0.0,
    percentuale=0.0,
):
    try:
        inizializza_file_registro()
        row = {
            "Data_Ora": now_str(),
            "Crypto": simbolo,
            "Operazione": operazione,
            "Prezzo_EUR": f"{safe_float(prezzo):.8f}",
            "RSI": f"{safe_float(rsi):.2f}",
            "Saldo_EUR": f"{safe_float(saldo):.2f}",
            "Quantita": f"{safe_float(quantita):.12f}",
            "Importo_EUR": f"{safe_float(importo):.2f}",
            "Commissione_EUR": f"{safe_float(commissione):.2f}",
            "Profitto_EUR": f"{safe_float(profitto):.2f}",
            "Percentuale": f"{safe_float(percentuale):.2f}",
            "Note": note,
        }
        with open(FILE_LOG, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=REGISTRO_FIELDNAMES)
            writer.writerow(row)
        salva_trade_sqlite(row)
    except Exception as e:
        log_errore("Errore scrittura registro trade", e)


def leggi_registro_operazioni(read_only=False):
    try:
        if read_only:
            if not FILE_LOG.exists():
                return []
        else:
            inizializza_file_registro()
        with open(FILE_LOG, "r", encoding="utf-8", newline="") as f:
            return [normalizza_riga_registro(row) for row in csv.DictReader(f)]
    except Exception as e:
        log_errore("Errore lettura registro trade", e)
        return []


def sincronizza_csv_in_sqlite():
    """Importa registro_trade.csv nel DB senza duplicare le righe già presenti."""
    count = 0
    try:
        inizializza_database_sqlite()
        rows = leggi_registro_operazioni(read_only=True)
        for row in rows:
            before = _conteggio_trade_sqlite()
            salva_trade_sqlite(row)
            after = _conteggio_trade_sqlite()
            if after > before:
                count += 1
        log_evento(f"SQLite sync completata: {count} nuove righe importate dal CSV.")
    except Exception as e:
        log_errore("Errore sync CSV -> SQLite", e)
    return count


def _conteggio_trade_sqlite():
    try:
        inizializza_database_sqlite()
        with _sqlite_connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()
            return int(row["n"] if row else 0)
    except Exception:
        return 0


def _leggi_ultimo_snapshot_sqlite(readonly=False):
    try:
        if readonly and not FILE_DB.exists():
            return None
        if not readonly:
            inizializza_database_sqlite()
        opener = _sqlite_connect_readonly if readonly else _sqlite_connect
        with opener() as conn:
            return conn.execute(
                "SELECT * FROM equity_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        return None


def calcola_report_sqlite(sync_csv=False):
    """Ritorna un report sintetico basato su SQLite + CSV importato."""
    if sync_csv:
        sincronizza_csv_in_sqlite()
    cfg = carica_config()
    ultimo_snapshot = _leggi_ultimo_snapshot_sqlite(readonly=True)

    report = {
        "generato_il": now_str(),
        "database": str(FILE_DB),
        "csv": str(FILE_LOG),
        "modalita": "SIMULAZIONE",
        "saldo_cash_config": safe_float(cfg.get("saldo_eur", 0.0)),
        "profitto_accumulato_config": safe_float(cfg.get("profitto_accumulato", 0.0)),
        "posizioni_aperte_config": sum(1 for v in cfg.get("posizioni_aperte", {}).values() if safe_bool(v)),
    }

    try:
        with _sqlite_connect_readonly() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) AS trade_totali,
                    SUM(CASE WHEN UPPER(operazione) LIKE '%COMPRA%' OR UPPER(operazione) LIKE '%BUY%' THEN 1 ELSE 0 END) AS acquisti,
                    SUM(CASE WHEN UPPER(operazione) LIKE '%VEND%' OR UPPER(operazione) LIKE '%SELL%' OR UPPER(operazione) LIKE '%PROFIT%' OR UPPER(operazione) LIKE '%LOSS%' THEN 1 ELSE 0 END) AS vendite,
                    COALESCE(SUM(commissione_eur), 0) AS commissioni_totali,
                    COALESCE(SUM(profitto_eur), 0) AS pnl_realizzato
                FROM trades
            """).fetchone()
            report.update({
                "trade_totali": int(row["trade_totali"] or 0),
                "acquisti": int(row["acquisti"] or 0),
                "vendite": int(row["vendite"] or 0),
                "commissioni_totali": float(row["commissioni_totali"] or 0.0),
                "pnl_realizzato": float(row["pnl_realizzato"] or 0.0),
            })

            win = conn.execute("""
                SELECT COUNT(*) AS n FROM trades
                WHERE profitto_eur > 0 AND (
                    UPPER(operazione) LIKE '%VEND%' OR UPPER(operazione) LIKE '%SELL%' OR
                    UPPER(operazione) LIKE '%PROFIT%' OR UPPER(operazione) LIKE '%LOSS%'
                )
            """).fetchone()["n"]
            loss = conn.execute("""
                SELECT COUNT(*) AS n FROM trades
                WHERE profitto_eur < 0 AND (
                    UPPER(operazione) LIKE '%VEND%' OR UPPER(operazione) LIKE '%SELL%' OR
                    UPPER(operazione) LIKE '%PROFIT%' OR UPPER(operazione) LIKE '%LOSS%'
                )
            """).fetchone()["n"]
            chiusi = int(win or 0) + int(loss or 0)
            report["trade_vincenti"] = int(win or 0)
            report["trade_perdenti"] = int(loss or 0)
            report["win_rate_pct"] = (int(win or 0) / chiusi * 100.0) if chiusi else 0.0

            best = conn.execute("SELECT crypto, operazione, profitto_eur, data_ora FROM trades ORDER BY profitto_eur DESC LIMIT 1").fetchone()
            worst = conn.execute("SELECT crypto, operazione, profitto_eur, data_ora FROM trades ORDER BY profitto_eur ASC LIMIT 1").fetchone()
            if best:
                report["miglior_trade"] = dict(best)
            if worst:
                report["peggior_trade"] = dict(worst)

            per_crypto = conn.execute("""
                SELECT crypto, COUNT(*) AS n, COALESCE(SUM(profitto_eur),0) AS pnl
                FROM trades
                GROUP BY crypto
                ORDER BY pnl DESC
                LIMIT 20
            """).fetchall()
            report["pnl_per_crypto"] = [dict(r) for r in per_crypto]

    except Exception as e:
        log_errore("Errore calcolo report SQLite", e)
        report["errore_report"] = repr(e)
        report.setdefault("trade_totali", 0)
        report.setdefault("acquisti", 0)
        report.setdefault("vendite", 0)
        report.setdefault("commissioni_totali", 0.0)
        report.setdefault("pnl_realizzato", 0.0)
        report.setdefault("trade_vincenti", 0)
        report.setdefault("trade_perdenti", 0)
        report.setdefault("win_rate_pct", 0.0)
        report.setdefault("pnl_per_crypto", [])

    # Calcolo drawdown corretto sugli snapshot equity: picco equity -> equity corrente.
    try:
        with _sqlite_connect_readonly() as conn:
            equity_rows = conn.execute(
                "SELECT data_ora, equity_totale FROM equity_snapshots WHERE COALESCE(equity_totale, 0) > 0 ORDER BY id ASC"
            ).fetchall()
            picco = 0.0
            max_dd = 0.0
            dd_corrente = 0.0
            for r in equity_rows:
                eq = safe_float(r["equity_totale"])
                picco = max(picco, eq)
                dd = ((picco - eq) / picco * 100.0) if picco > 0 else 0.0
                max_dd = max(max_dd, dd)
                dd_corrente = dd
            report["drawdown_corrente_pct"] = dd_corrente
            report["drawdown_massimo_pct"] = max_dd
    except Exception as e:
        log_errore("Errore calcolo drawdown corretto report SQLite", e)
        report.setdefault("drawdown_corrente_pct", 0.0)
        report.setdefault("drawdown_massimo_pct", 0.0)

    if ultimo_snapshot:
        drawdown_snapshot = safe_float(report.get("drawdown_corrente_pct", ultimo_snapshot["drawdown_pct"] or 0.0))
        report.update({
            "ultimo_snapshot": {
                "data_ora": ultimo_snapshot["data_ora"],
                "saldo_cash": float(ultimo_snapshot["saldo_cash"] or 0.0),
                "capitale_investito": float(ultimo_snapshot["capitale_investito"] or 0.0),
                "valore_posizioni": float(ultimo_snapshot["valore_posizioni"] or 0.0),
                "equity_totale": float(ultimo_snapshot["equity_totale"] or 0.0),
                "profitto_realizzato": float(ultimo_snapshot["profitto_realizzato"] or 0.0),
                "profitto_non_realizzato": float(ultimo_snapshot["profitto_non_realizzato"] or 0.0),
                "drawdown_pct": float(drawdown_snapshot),
            }
        })

    saldo_iniziale = safe_float(DEFAULT_CONFIG.get("saldo_eur", 5000.0), 5000.0)
    equity = report.get("ultimo_snapshot", {}).get("equity_totale", report["saldo_cash_config"])
    report["moltiplicatore_da_5000"] = (equity / saldo_iniziale) if saldo_iniziale else 0.0
    if report["moltiplicatore_da_5000"] >= 3.0:
        report["audit_coerenza"] = "DA_VERIFICARE - crescita anomala"
    else:
        report["audit_coerenza"] = "OK - nessuna anomalia grave"
    report["audit_x3"] = report["audit_coerenza"]  # Compatibilità con report/database precedenti.
    return report



# ============================================================
# SQLITE ADD-ON: GRAFICI REPORT
# ============================================================

def _short_datetime_label(value):
    """Etichetta compatta per assi temporali, tollerante ai formati testuali."""
    try:
        text = str(value or "")
        if len(text) >= 16:
            # Esempio: 2026-06-14 18:22 -> 06-14 18:22
            return text[5:16]
        return text
    except Exception:
        return ""


def leggi_dati_grafici_sqlite(limit=300, sync_csv=False):
    """Legge da SQLite i dataset usati dai grafici report.

    Non modifica la logica operativa del bot: in modalità report/dashboard legge
    il database in sola lettura e prepara dati aggregati per visualizzazione/audit.
    """
    dati = {
        "equity": [],
        "pnl_crypto": [],
        "trade_count_crypto": [],
        "drawdown": [],
    }
    try:
        if sync_csv:
            sincronizza_csv_in_sqlite()
        if not FILE_DB.exists():
            return dati
        limit = max(20, min(int(limit or 300), 2000))
        with _sqlite_connect_readonly() as conn:
            equity_rows = conn.execute(
                """
                SELECT data_ora, saldo_cash, valore_posizioni, equity_totale,
                       profitto_realizzato, profitto_non_realizzato,
                       posizioni_aperte, drawdown_pct
                FROM (
                    SELECT id, data_ora, saldo_cash, valore_posizioni, equity_totale,
                           profitto_realizzato, profitto_non_realizzato,
                           posizioni_aperte, drawdown_pct
                    FROM equity_snapshots
                    WHERE COALESCE(equity_totale, 0) > 0
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY data_ora ASC
                """,
                (limit,),
            ).fetchall()
            dati["equity"] = [dict(r) for r in equity_rows]
            dati["drawdown"] = [dict(r) for r in equity_rows]

            pnl_rows = conn.execute(
                """
                SELECT crypto,
                       SUM(COALESCE(profitto_eur, 0)) AS pnl,
                       SUM(COALESCE(commissione_eur, 0)) AS commissioni,
                       COUNT(*) AS vendite
                FROM trades
                WHERE crypto IS NOT NULL
                  AND TRIM(crypto) <> ''
                  AND (
                    UPPER(COALESCE(operazione, '')) LIKE '%VEND%' OR
                    UPPER(COALESCE(operazione, '')) LIKE '%SELL%' OR
                    UPPER(COALESCE(operazione, '')) LIKE '%PROFIT%' OR
                    UPPER(COALESCE(operazione, '')) LIKE '%LOSS%'
                  )
                GROUP BY crypto
                ORDER BY pnl DESC
                LIMIT 20
                """
            ).fetchall()
            dati["pnl_crypto"] = [dict(r) for r in pnl_rows]

            count_rows = conn.execute(
                """
                SELECT crypto,
                       COUNT(*) AS trade_count,
                       SUM(CASE WHEN UPPER(COALESCE(operazione, '')) LIKE '%COMP%' OR UPPER(COALESCE(operazione, '')) LIKE '%BUY%' THEN 1 ELSE 0 END) AS acquisti,
                       SUM(CASE WHEN UPPER(COALESCE(operazione, '')) LIKE '%VEND%' OR UPPER(COALESCE(operazione, '')) LIKE '%SELL%' OR UPPER(COALESCE(operazione, '')) LIKE '%PROFIT%' OR UPPER(COALESCE(operazione, '')) LIKE '%LOSS%' THEN 1 ELSE 0 END) AS vendite
                FROM trades
                WHERE crypto IS NOT NULL AND TRIM(crypto) <> ''
                GROUP BY crypto
                ORDER BY trade_count DESC
                LIMIT 20
                """
            ).fetchall()
            dati["trade_count_crypto"] = [dict(r) for r in count_rows]
    except Exception as e:
        log_errore("Errore lettura dati grafici SQLite", e)
    return dati


def _setup_report_axis(ax, title):
    try:
        ax.set_facecolor("#111827")
        ax.set_title(title, color="#f8fafc", fontsize=11, fontweight="bold")
        ax.grid(True, color="#334155", linestyle="--", linewidth=0.6, alpha=0.65)
        ax.tick_params(colors="#cbd5e1", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")
        ax.xaxis.label.set_color("#cbd5e1")
        ax.yaxis.label.set_color("#cbd5e1")
    except Exception:
        pass


def _save_placeholder_chart(path, title, message):
    fig = Figure(figsize=(9, 4.8), dpi=120, facecolor="#0f172a")
    ax = fig.add_subplot(111, facecolor="#111827")
    ax.text(0.5, 0.5, message, ha="center", va="center", color="#cbd5e1", transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])
    _setup_report_axis(ax, title)
    fig.tight_layout(pad=2.0)
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    return path


def genera_grafici_sqlite_png(sync_csv=False):
    """Genera grafici PNG partendo dai dati SQLite.

    Output:
    - report_sqlite_equity.png
    - report_sqlite_pnl_crypto.png
    - report_sqlite_drawdown.png
    """
    info = {"files": {}, "equity_points": 0, "pnl_crypto_rows": 0}
    dati = leggi_dati_grafici_sqlite(sync_csv=sync_csv)
    equity = dati.get("equity") or []
    pnl_crypto = dati.get("pnl_crypto") or []

    try:
        # Grafico equity / cash / valore posizioni
        if equity:
            labels = [_short_datetime_label(r.get("data_ora")) for r in equity]
            x = list(range(len(equity)))
            y_equity = [safe_float(r.get("equity_totale")) for r in equity]
            y_cash = [safe_float(r.get("saldo_cash")) for r in equity]
            y_pos = [safe_float(r.get("valore_posizioni")) for r in equity]

            fig = Figure(figsize=(10, 5.2), dpi=120, facecolor="#0f172a")
            ax = fig.add_subplot(111, facecolor="#111827")
            ax.plot(x, y_equity, color="#22c55e", linewidth=2.2, label="Equity totale")
            ax.plot(x, y_cash, color="#38bdf8", linewidth=1.5, label="Saldo cash")
            ax.plot(x, y_pos, color="#facc15", linewidth=1.5, label="Valore posizioni")
            step = max(1, len(labels) // 8)
            ticks = list(range(0, len(labels), step))
            if ticks and ticks[-1] != len(labels) - 1:
                ticks.append(len(labels) - 1)
            ax.set_xticks(ticks)
            ax.set_xticklabels([labels[i] for i in ticks], rotation=25, ha="right")
            ax.set_ylabel("EUR")
            _setup_report_axis(ax, "Equity totale da SQLite")
            legend = ax.legend(fontsize=8, facecolor="#111827", edgecolor="#334155")
            for text in legend.get_texts():
                text.set_color("#f8fafc")
            fig.tight_layout(pad=2.0)
            fig.savefig(FILE_REPORT_EQUITY_PNG, facecolor=fig.get_facecolor(), bbox_inches="tight")
        else:
            _save_placeholder_chart(FILE_REPORT_EQUITY_PNG, "Equity totale da SQLite", "Nessuno snapshot equity disponibile. Lascia girare il bot o genera un report.")
        info["files"]["equity"] = FILE_REPORT_EQUITY_PNG
        info["equity_points"] = len(equity)
    except Exception as e:
        log_errore("Errore generazione grafico equity SQLite", e)

    try:
        # Grafico PnL per crypto
        if pnl_crypto:
            rows = list(reversed(pnl_crypto[:10]))
            symbols = [str(r.get("crypto") or "-") for r in rows]
            pnl = [safe_float(r.get("pnl")) for r in rows]
            colors = ["#22c55e" if v >= 0 else "#ef4444" for v in pnl]

            fig = Figure(figsize=(10, 5.2), dpi=120, facecolor="#0f172a")
            ax = fig.add_subplot(111, facecolor="#111827")
            ax.barh(range(len(symbols)), pnl, color=colors)
            ax.set_yticks(range(len(symbols)))
            ax.set_yticklabels(symbols)
            ax.axvline(0, color="#cbd5e1", linewidth=0.8)
            ax.set_xlabel("PnL realizzato EUR")
            _setup_report_axis(ax, "PnL realizzato per crypto da SQLite")
            fig.tight_layout(pad=2.0)
            fig.savefig(FILE_REPORT_PNL_CRYPTO_PNG, facecolor=fig.get_facecolor(), bbox_inches="tight")
        else:
            _save_placeholder_chart(FILE_REPORT_PNL_CRYPTO_PNG, "PnL realizzato per crypto da SQLite", "Nessuna vendita chiusa disponibile nel database.")
        info["files"]["pnl_crypto"] = FILE_REPORT_PNL_CRYPTO_PNG
        info["pnl_crypto_rows"] = len(pnl_crypto)
    except Exception as e:
        log_errore("Errore generazione grafico PnL crypto SQLite", e)

    try:
        # Grafico drawdown
        if equity:
            labels = [_short_datetime_label(r.get("data_ora")) for r in equity]
            x = list(range(len(equity)))
            drawdown = []
            picco = 0.0
            for r in equity:
                eq = safe_float(r.get("equity_totale"))
                picco = max(picco, eq)
                drawdown.append(((picco - eq) / picco * 100.0) if picco > 0 else 0.0)
            fig = Figure(figsize=(10, 4.8), dpi=120, facecolor="#0f172a")
            ax = fig.add_subplot(111, facecolor="#111827")
            ax.plot(x, drawdown, color="#fb7185", linewidth=2.0, label="Drawdown %")
            step = max(1, len(labels) // 8)
            ticks = list(range(0, len(labels), step))
            if ticks and ticks[-1] != len(labels) - 1:
                ticks.append(len(labels) - 1)
            ax.set_xticks(ticks)
            ax.set_xticklabels([labels[i] for i in ticks], rotation=25, ha="right")
            ax.set_ylabel("%")
            _setup_report_axis(ax, "Drawdown da SQLite")
            fig.tight_layout(pad=2.0)
            fig.savefig(FILE_REPORT_DRAWDOWN_PNG, facecolor=fig.get_facecolor(), bbox_inches="tight")
        else:
            _save_placeholder_chart(FILE_REPORT_DRAWDOWN_PNG, "Drawdown da SQLite", "Nessuno snapshot equity disponibile.")
        info["files"]["drawdown"] = FILE_REPORT_DRAWDOWN_PNG
    except Exception as e:
        log_errore("Errore generazione grafico drawdown SQLite", e)

    return info

def genera_file_report_sqlite(sync_csv=False):
    report = calcola_report_sqlite(sync_csv=sync_csv)
    try:
        save_json_atomic(FILE_REPORT_JSON, report)

        righe = [
            "QUANTUMBOT - REPORT SQLITE",
            f"Generato: {report.get('generato_il')}",
            f"Database: {report.get('database')}",
            "",
            f"Saldo cash config: {format_eur(report.get('saldo_cash_config', 0.0))}",
            f"Trade totali: {report.get('trade_totali', 0)}",
            f"Acquisti: {report.get('acquisti', 0)}",
            f"Vendite: {report.get('vendite', 0)}",
            f"PnL realizzato: {format_signed_eur(report.get('pnl_realizzato', 0.0))}",
            f"Commissioni totali: {format_eur(report.get('commissioni_totali', 0.0))}",
            f"Win rate: {report.get('win_rate_pct', 0.0):.2f}%",
            f"Drawdown massimo equity: {report.get('drawdown_massimo_pct', 0.0):.2f}%",
            f"Posizioni aperte config: {report.get('posizioni_aperte_config', 0)}",
            f"Rapporto equity/capitale base: x{report.get('moltiplicatore_da_5000', 0.0):.3f}",
            f"Controllo coerenza: {report.get('audit_coerenza', report.get('audit_x3'))}",
        ]
        snap = report.get("ultimo_snapshot") or {}
        if snap:
            righe.extend([
                "",
                "ULTIMO SNAPSHOT EQUITY",
                f"Data: {snap.get('data_ora')}",
                f"Equity totale: {format_eur(snap.get('equity_totale', 0.0))}",
                f"Valore posizioni: {format_eur(snap.get('valore_posizioni', 0.0))}",
                f"PnL non realizzato: {format_signed_eur(snap.get('profitto_non_realizzato', 0.0))}",
                f"Drawdown: {snap.get('drawdown_pct', 0.0):.2f}%",
            ])
        FILE_REPORT_TXT.write_text("\n".join(righe) + "\n", encoding="utf-8")

        with open(FILE_REPORT_CSV, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["metrica", "valore"])
            for key, value in report.items():
                if isinstance(value, (dict, list)):
                    writer.writerow([key, json.dumps(value, ensure_ascii=False)])
                else:
                    writer.writerow([key, value])

        try:
            chart_info = genera_grafici_sqlite_png()
            report["grafici_png"] = {key: str(path) for key, path in chart_info.get("files", {}).items()}
            report["grafici_generati_il"] = now_str()
            save_json_atomic(FILE_REPORT_JSON, report)
        except Exception as e_chart:
            log_errore("Errore generazione grafici PNG SQLite", e_chart)

        log_evento(
            f"Report SQLite generato: {FILE_REPORT_TXT.name}, {FILE_REPORT_JSON.name}, "
            f"{FILE_REPORT_CSV.name}, {FILE_REPORT_EQUITY_PNG.name}, {FILE_REPORT_PNL_CRYPTO_PNG.name}"
        )
    except Exception as e:
        log_errore("Errore generazione file report SQLite", e)
    return report



# ============================================================
# LOCK PROCESSO ENGINE
# ============================================================

_ENGINE_LOCK_HANDLE = None


def acquire_engine_lock() -> bool:
    """
    Impedisce che due engine lavorino contemporaneamente sulla stessa cartella dati.

    Questo è fondamentale perché due engine possono:
    - duplicare operazioni nel CSV;
    - scrivere status_bot.json nello stesso istante;
    - far apparire la dashboard incoerente.
    """
    global _ENGINE_LOCK_HANDLE
    try:
        cleanup_stale_engine_artifacts()
        FILE_ENGINE_LOCK.parent.mkdir(parents=True, exist_ok=True)
        handle = open(FILE_ENGINE_LOCK, "a+", encoding="utf-8")

        if os.name != "nt":
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    handle.close()
                except Exception:
                    pass
                log_evento("Engine non avviato: lock engine già occupato.")
                return False

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nstarted={now_str()}\nversion={APP_VERSION}\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except Exception:
            pass

        _ENGINE_LOCK_HANDLE = handle
        return True
    except Exception as e:
        # Su macOS/PyInstaller il lock è importante: se fallisce meglio non avviare
        # un secondo engine cieco.
        log_errore("Impossibile acquisire engine.lock", e)
        return False


def release_engine_lock():
    global _ENGINE_LOCK_HANDLE
    try:
        if _ENGINE_LOCK_HANDLE is not None:
            if os.name != "nt":
                try:
                    import fcntl
                    fcntl.flock(_ENGINE_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                _ENGINE_LOCK_HANDLE.close()
            except Exception:
                pass
            _ENGINE_LOCK_HANDLE = None
    except Exception:
        pass


# ============================================================
# GESTIONE PROCESSO ENGINE
# ============================================================

def _process_stat_command(pid: int):
    """Restituisce (stat, command) per un PID su macOS/Linux.

    Serve per evitare falsi positivi: os.kill(pid, 0) può risultare True anche
    per processi zombie o per PID riutilizzati da un processo diverso dal nostro
    engine. In quei casi la dashboard restava bloccata su "Ferma Bot" anche
    quando l'engine era già terminato.
    """
    if not pid or os.name == "nt":
        return "", ""
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o", "stat=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        ).strip()
        if not out:
            return "", ""
        parts = out.split(None, 1)
        stat = parts[0].strip() if parts else ""
        command = parts[1].strip() if len(parts) > 1 else ""
        return stat, command
    except Exception:
        return "", ""


def pid_is_running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False

    # Su macOS/Linux escludiamo processi zombie: tecnicamente il PID esiste,
    # ma l'engine non sta più lavorando e la UI deve poter tornare su "Avvia Bot".
    stat, _cmd = _process_stat_command(pid)
    if stat and "Z" in stat.upper():
        return False
    return True


def pid_is_quantumbot_engine(pid: int) -> bool:
    """True solo se il PID appartiene davvero al processo engine QuantumBot.

    Non basta sapere che un PID esiste: macOS può riutilizzare PID oppure il PID
    può riferirsi alla dashboard/app principale. L'engine viene avviato sempre
    con l'argomento --engine, quindi lo usiamo come discriminante forte.
    """
    if not pid_is_running(pid):
        return False
    if os.name == "nt":
        return True
    stat, command = _process_stat_command(pid)
    if stat and "Z" in stat.upper():
        return False
    if not command:
        return False
    return "--engine" in command


def rimuovi_artifacts_engine_stale(motivo=""):
    """Rimuove pid/lock stale e lascia la dashboard libera di riavviare.

    Questa funzione è volutamente conservativa: non tocca config, CSV o SQLite.
    Serve solo a sbloccare Avvia/Ferma quando l'engine è morto ma i file di stato
    sono rimasti indietro.
    """
    try:
        pid = None
        if FILE_PID.exists():
            try:
                pid = safe_int(FILE_PID.read_text(encoding="utf-8").strip(), 0)
            except Exception:
                pid = 0
            if not pid or not pid_is_quantumbot_engine(pid):
                try:
                    FILE_PID.unlink(missing_ok=True)
                    log_evento(f"ENGINEGUARD: rimosso engine.pid stale durante reconcile ({pid or 'non valido'}) {motivo}")
                except Exception as e:
                    log_errore("ENGINEGUARD: impossibile rimuovere engine.pid stale durante reconcile", e)

        if FILE_ENGINE_LOCK.exists() and not engine_lock_occupato_da_altro_processo():
            lock_pid = leggi_pid_da_engine_lock()
            if not lock_pid or not pid_is_quantumbot_engine(lock_pid):
                try:
                    FILE_ENGINE_LOCK.unlink(missing_ok=True)
                    log_evento(f"ENGINEGUARD: rimosso engine.lock stale durante reconcile ({lock_pid or 'PID assente'}) {motivo}")
                except Exception as e:
                    log_errore("ENGINEGUARD: impossibile rimuovere engine.lock stale durante reconcile", e)
    except Exception as e:
        log_errore("ENGINEGUARD: errore reconcile artifacts stale", e)


def leggi_pid_da_engine_lock():
    """Legge il PID scritto dentro engine.lock, se presente.

    Il file lock non è la fonte primaria, ma è utile quando engine.pid o
    status_bot.json sono in ritardo di qualche secondo rispetto all'avvio reale
    dell'engine. Non modifica il file.
    """
    try:
        if not FILE_ENGINE_LOCK.exists():
            return None
        text = FILE_ENGINE_LOCK.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.strip().startswith("pid="):
                return safe_int(line.split("=", 1)[1].strip(), 0) or None
    except Exception:
        return None
    return None


def engine_lock_occupato_da_altro_processo() -> bool:
    """Verifica se engine.lock è realmente bloccato da un altro processo.

    Serve alla dashboard per evitare falsi ENGINE DISATTIVO nei secondi in cui
    engine.pid/status_bot.json non sono ancora aggiornati ma l'engine è già vivo
    e detiene il lock. Su Windows restituisce False perché fcntl non è disponibile.
    """
    if os.name == "nt" or not FILE_ENGINE_LOCK.exists():
        return False
    handle = None
    try:
        import fcntl
        handle = open(FILE_ENGINE_LOCK, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock acquisibile: quindi non è occupato da un engine vivo.
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            return False
        except BlockingIOError:
            return True
    except Exception:
        return False
    finally:
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass


def cleanup_stale_engine_artifacts():
    """Rimuove engine.pid/engine.lock solo quando sono certamente stale."""
    try:
        if FILE_PID.exists():
            try:
                pid = safe_int(FILE_PID.read_text(encoding="utf-8").strip(), 0)
            except Exception:
                pid = 0
            if not pid or not pid_is_quantumbot_engine(pid):
                try:
                    FILE_PID.unlink()
                    log_evento(f"ENGINEGUARD: rimosso engine.pid stale ({pid or 'non valido'}).")
                except Exception as e:
                    log_errore("ENGINEGUARD: impossibile rimuovere engine.pid stale", e)

        if FILE_ENGINE_LOCK.exists() and not engine_lock_occupato_da_altro_processo():
            lock_pid = leggi_pid_da_engine_lock()
            if not lock_pid or not pid_is_quantumbot_engine(lock_pid):
                try:
                    FILE_ENGINE_LOCK.unlink()
                    log_evento(f"ENGINEGUARD: rimosso engine.lock stale ({lock_pid or 'PID assente'}).")
                except Exception as e:
                    log_errore("ENGINEGUARD: impossibile rimuovere engine.lock stale", e)
    except Exception as e:
        log_errore("ENGINEGUARD: errore pulizia lock stale", e)


def file_age_seconds(path: Path):
    try:
        return time.time() - path.stat().st_mtime
    except Exception:
        return None


def parse_status_timestamp(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def status_conferma_engine(pid: int, max_age_seconds=30) -> bool:
    if not pid_is_quantumbot_engine(pid):
        return False
    status = load_json_file(FILE_STATUS, {})
    engine = status.get("engine", {}) if isinstance(status, dict) else {}
    if not safe_bool(engine.get("running"), False):
        return False
    if safe_int(engine.get("pid"), 0) != pid:
        return False

    last_update = parse_status_timestamp(engine.get("last_update"))
    if last_update is None:
        return False

    age = (datetime.now() - last_update).total_seconds()
    return age <= max_age_seconds


def get_engine_pid():
    """
    Rileva l'engine usando prima engine.pid e poi, come fallback, status_bot.json.

    Prima la dashboard poteva mostrare ENGINE DISATTIVO se engine.pid era assente
    o temporaneamente non leggibile, anche se status_bot.json indicava un heartbeat
    recente con processo vivo.
    """
    cleanup_stale_engine_artifacts()

    # 1) Metodo principale: engine.pid
    try:
        if FILE_PID.exists():
            pid = int(FILE_PID.read_text(encoding="utf-8").strip())
            if pid_is_quantumbot_engine(pid):
                pid_file_age = file_age_seconds(FILE_PID)
                avvio_recente = pid_file_age is not None and pid_file_age <= 15
                if avvio_recente or status_conferma_engine(pid):
                    return pid
            else:
                try:
                    FILE_PID.unlink()
                except Exception:
                    pass
    except Exception:
        pass

    # 2) Fallback robusto: status_bot.json con heartbeat recente
    try:
        status = load_json_file(FILE_STATUS, {})
        engine = status.get("engine", {}) if isinstance(status, dict) else {}
        pid = safe_int(engine.get("pid"), 0)
        if pid and safe_bool(engine.get("running"), False) and pid_is_quantumbot_engine(pid):
            last_update = parse_status_timestamp(engine.get("last_update"))
            if last_update is not None:
                age = (datetime.now() - last_update).total_seconds()
                if age <= 30:
                    return pid
    except Exception:
        pass

    # 3) Fallback anti-delay: se engine.lock è occupato, l'engine è vivo anche
    # se engine.pid/status_bot.json non sono ancora leggibili dalla UI.
    try:
        lock_pid = leggi_pid_da_engine_lock()
        if lock_pid and pid_is_quantumbot_engine(lock_pid):
            return lock_pid
        if engine_lock_occupato_da_altro_processo():
            # Se non abbiamo un PID valido, usiamo -1 come sentinella interna:
            # engine_is_running() deve sapere solo che un engine c'è già.
            return lock_pid or -1
    except Exception:
        pass

    return None

def engine_is_running() -> bool:
    return get_engine_pid() is not None


def start_engine_process(wait_for_confirm=True, max_wait_seconds=15.0):
    """
    Avvia l'engine in background.
    Su macOS/PyInstaller può impiegare qualche secondo a scrivere engine.pid.
    """
    cleanup_stale_engine_artifacts()
    if engine_is_running():
        return True

    try:
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--engine"]
        else:
            args = [sys.executable, str(Path(__file__).resolve()), "--engine"]

        env = os.environ.copy()
        env[ENGINE_ENV_FLAG] = "1"
        env["QUANTUMBOT_APP_DIR"] = str(APP_DIR)

        with open(FILE_ENGINE_LAUNCH, "a", encoding="utf-8") as lf:
            lf.write(f"[{now_str()}] Avvio engine\n")
            lf.write(f"APP_DIR: {APP_DIR}\n")
            lf.write(f"sys.executable: {sys.executable}\n")
            lf.write(f"args: {args}\n")
            lf.write(f"frozen: {getattr(sys, 'frozen', False)}\n\n")

        stdout = open(FILE_ENGINE_STDOUT, "a", encoding="utf-8")
        stderr = open(FILE_ENGINE_STDERR, "a", encoding="utf-8")

        popen_kwargs = {
            "cwd": str(APP_DIR),
            "stdout": stdout,
            "stderr": stderr,
            "stdin": subprocess.DEVNULL,
            "env": env
        }

        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        process = subprocess.Popen(args, **popen_kwargs)
        try:
            stdout.close()
            stderr.close()
        except Exception:
            pass

        if not wait_for_confirm:
            log_evento(f"Avvio engine richiesto in background. PID processo launcher {process.pid}")
            return True

        tentativi = max(1, int(max_wait_seconds / 0.25))
        for _ in range(tentativi):
            time.sleep(0.25)
            if engine_is_running():
                log_evento(f"Engine avviato dalla dashboard. PID {get_engine_pid()}")
                return True
            if process.poll() is not None:
                log_errore(f"Engine terminato subito con codice {process.returncode}")
                return False

        if process.poll() is None:
            log_evento("Engine avviato ma PID non ancora rilevato: avvio considerato in corso.")
            return True

        return engine_is_running()

    except Exception as e:
        log_errore("Errore avvio engine", e)
        return False


def append_comando(azione, params=None):
    comando = {
        "id": str(uuid.uuid4()),
        "timestamp": now_str(),
        "azione": azione,
        "params": params or {}
    }
    try:
        with open(FILE_COMMANDI, "a", encoding="utf-8") as f:
            f.write(json.dumps(comando, ensure_ascii=False) + "\n")
        return comando["id"]
    except Exception as e:
        log_errore("Errore scrittura comando", e)
        return None


def leggi_comandi():
    if not FILE_COMMANDI.exists():
        return []

    comandi = []
    try:
        with open(FILE_COMMANDI, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    comandi.append(json.loads(line))
                except Exception:
                    continue
    except Exception as e:
        log_errore("Errore lettura comandi", e)

    return comandi


def carica_comandi_processati():
    state = load_json_file(FILE_COMMAND_STATE, {"processed_ids": []})
    ids = state.get("processed_ids", [])
    if not isinstance(ids, list):
        ids = []
    return set(ids)


def salva_comandi_processati(ids):
    ultimi = list(ids)[-1000:]
    save_json_atomic(FILE_COMMAND_STATE, {"processed_ids": ultimi})


# ============================================================
# DATI MERCATO
# ============================================================

def crea_exchange_binance():
    if ccxt is None:
        log_errore("Dipendenza mancante: ccxt. Installa con: python3 -m pip install ccxt")
        return None
    try:
        return ccxt.binance({"enableRateLimit": True, "timeout": 10000})
    except Exception as e:
        log_errore("Errore inizializzazione Binance/ccxt", e)
        return None


exchange = crea_exchange_binance()


def prepara_mercati(cfg):
    market_map = {}
    if exchange is None:
        # Non blocchiamo la dashboard se ccxt non è installato o Binance non è raggiungibile.
        # Il bot resta utilizzabile in simulazione con dati fallback locali.
        for base in cfg.get("crypto_base_list", DEFAULT_CONFIG["crypto_base_list"]):
            market_map[simbolo_visuale(base)] = {"reale": "SIMULATO", "conversione": "SIMULATO"}
        return market_map

    try:
        exchange.load_markets()
        for base in cfg["crypto_base_list"]:
            visual = simbolo_visuale(base)
            eur = f"{base}/EUR"
            usdt = f"{base}/USDT"
            if eur in exchange.markets:
                market_map[visual] = {"reale": eur, "conversione": "EUR"}
            elif usdt in exchange.markets:
                market_map[visual] = {"reale": usdt, "conversione": "USDT_TO_EUR"}
            else:
                market_map[visual] = {"reale": None, "conversione": None}
    except Exception as e:
        log_errore("Errore caricamento mercati Binance", e)
        for base in cfg["crypto_base_list"]:
            market_map[simbolo_visuale(base)] = {"reale": None, "conversione": None}

    return market_map


ULTIMO_CAMBIO_USDT_EUR = {"valore": 0.92, "timestamp": 0.0}


def ottieni_cambio_usdt_eur():
    if exchange is None:
        return ULTIMO_CAMBIO_USDT_EUR.get("valore", 0.92)

    adesso = time.time()
    if adesso - ULTIMO_CAMBIO_USDT_EUR.get("timestamp", 0.0) < 30:
        return ULTIMO_CAMBIO_USDT_EUR.get("valore", 0.92)

    try:
        if "EUR/USDT" in exchange.markets:
            ticker = exchange.fetch_ticker("EUR/USDT")
            prezzo = ticker.get("last")
            if prezzo and prezzo > 0:
                cambio = 1 / prezzo
                ULTIMO_CAMBIO_USDT_EUR.update({"valore": cambio, "timestamp": adesso})
                return cambio

        if "USDT/EUR" in exchange.markets:
            ticker = exchange.fetch_ticker("USDT/EUR")
            prezzo = ticker.get("last")
            if prezzo and prezzo > 0:
                ULTIMO_CAMBIO_USDT_EUR.update({"valore": prezzo, "timestamp": adesso})
                return prezzo
    except Exception as e:
        log_errore("Errore cambio USDT/EUR", e)

    return ULTIMO_CAMBIO_USDT_EUR.get("valore", 0.92)


def genera_ohlcv_fallback(simbolo_visual, timeframe="1m", limit=60):
    """Genera dati OHLC simulati se Binance/ccxt non è disponibile.

    Non sostituisce i dati reali: serve solo a evitare dashboard vuota o crash
    durante test, mancanza rete o dipendenze non installate.
    """
    if pd is None:
        raise RuntimeError("Dipendenza mancante: pandas. Installa con: python3 -m pip install pandas")

    base = str(simbolo_visual).split("/")[0].upper()
    basi_prezzo = {
        "BTC": 62000.0, "ETH": 3200.0, "SOL": 140.0, "BNB": 560.0, "XRP": 0.50,
        "ADA": 0.42, "DOGE": 0.12, "DOT": 6.0, "LINK": 14.0, "SHIB": 0.000022,
        "AVAX": 30.0, "MATIC": 0.65, "LTC": 75.0, "UNI": 9.0, "NEAR": 5.0,
        "ATOM": 8.0, "ICP": 10.0, "XLM": 0.11, "ETC": 25.0, "FIL": 5.5,
    }
    prezzo_base = basi_prezzo.get(base, 10.0)
    seed = sum(ord(c) for c in simbolo_visual) + int(time.time() // max(30, timeframe_seconds(timeframe)))
    rng = random.Random(seed)
    trend = rng.uniform(-0.025, 0.025)
    volatilita = max(prezzo_base * 0.006, 0.0000001)

    rows = []
    ultimo = prezzo_base * (1 + rng.uniform(-0.02, 0.02))
    adesso_ms = int(time.time() * 1000)
    step_ms = timeframe_seconds(timeframe) * 1000

    for i in range(limit):
        progress = (i / max(1, limit - 1)) - 0.5
        drift = prezzo_base * trend * progress
        apertura = max(0.00000001, ultimo)
        chiusura = max(0.00000001, prezzo_base + drift + rng.uniform(-volatilita, volatilita))
        massimo = max(apertura, chiusura) + abs(rng.uniform(0, volatilita * 0.8))
        minimo = max(0.00000001, min(apertura, chiusura) - abs(rng.uniform(0, volatilita * 0.8)))
        rows.append([adesso_ms - (limit - i) * step_ms, apertura, massimo, minimo, chiusura, 0.0])
        ultimo = chiusura

    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _marca_ohlcv(df, fonte="FALLBACK", trade_safe=False, motivo=""):
    """Aggiunge metadati al DataFrame OHLC senza alterare la dashboard.

    fonte="REAL" significa dato scaricato dall'exchange.
    fonte="FALLBACK" significa dato simulato locale: può essere mostrato nei grafici,
    ma NON deve essere usato da Auto Trade / Take Profit / Stop Loss.
    """
    try:
        df.attrs["fonte_dati"] = str(fonte)
        df.attrs["trade_safe"] = bool(trade_safe)
        df.attrs["motivo_sicurezza"] = str(motivo or "")
    except Exception:
        pass
    return df


def _ohlcv_fallback_solo_dashboard(simbolo_visual, timeframe="1m", limit=60, motivo=""):
    df = genera_ohlcv_fallback(simbolo_visual, timeframe=timeframe, limit=limit)
    return _marca_ohlcv(df, fonte="FALLBACK", trade_safe=False, motivo=motivo or "fallback simulato")


def fetch_ohlcv_in_eur(simbolo_visual, market_map, timeframe="1m", limit=60):
    if pd is None:
        raise RuntimeError("Dipendenza mancante: pandas. Installa con: python3 -m pip install pandas")

    info = market_map.get(simbolo_visual)

    if exchange is None:
        return _ohlcv_fallback_solo_dashboard(simbolo_visual, timeframe=timeframe, limit=limit, motivo="exchange non disponibile")

    if not info or not info.get("reale") or info.get("conversione") == "SIMULATO":
        return _ohlcv_fallback_solo_dashboard(simbolo_visual, timeframe=timeframe, limit=limit, motivo="mercato reale non mappato")

    try:
        candele = exchange.fetch_ohlcv(info["reale"], timeframe=timeframe, limit=limit)
        if not candele:
            raise ValueError("exchange ha restituito candele vuote")

        df = pd.DataFrame(candele, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
        if df.empty:
            raise ValueError("candele reali non valide o prezzo <= 0")

        trade_safe = True
        motivo_sicurezza = ""

        if info["conversione"] == "USDT_TO_EUR":
            cambio = ottieni_cambio_usdt_eur()
            cambio_age = time.time() - safe_float(ULTIMO_CAMBIO_USDT_EUR.get("timestamp", 0.0))
            if cambio <= 0:
                raise ValueError("cambio USDT/EUR non valido")
            if cambio_age > USDT_EUR_MAX_AGE_SECONDS:
                # Il prezzo crypto è reale, ma la conversione EUR non è fresca.
                # Lo mostriamo in dashboard, ma non lo usiamo per trading simulato.
                trade_safe = False
                motivo_sicurezza = f"cambio USDT/EUR non aggiornato da {cambio_age:.0f}s"
            for col in ["open", "high", "low", "close"]:
                df[col] = df[col] * cambio

        return _marca_ohlcv(df, fonte="REAL", trade_safe=trade_safe, motivo=motivo_sicurezza)
    except Exception as e:
        log_errore(f"Errore fetch OHLC {simbolo_visual}: fallback solo dashboard, Auto Trade bloccato su questo dato", e)
        return _ohlcv_fallback_solo_dashboard(simbolo_visual, timeframe=timeframe, limit=limit, motivo=repr(e))


def calcola_rsi(df, periodo=14):
    """Calcola RSI in modo stabile anche nei trend estremi.

    La formula classica genera divisioni per zero quando non ci sono perdite
    o quando non ci sono guadagni nel periodo. In quei casi il valore corretto
    è rispettivamente 100 oppure 0, non 50. Questo è importante per l'Auto
    Trade: un RSI estremo deve poter attivare compra/vendi.
    """
    delta = df["close"].diff()
    guadagno = delta.where(delta > 0, 0).rolling(window=periodo).mean()
    perdita = (-delta.where(delta < 0, 0)).rolling(window=periodo).mean()

    rs = guadagno / perdita.replace(0, math.nan)
    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask((perdita == 0) & (guadagno > 0), 100.0)
    rsi = rsi.mask((guadagno == 0) & (perdita > 0), 0.0)
    rsi = rsi.mask((guadagno == 0) & (perdita == 0), 50.0)
    return rsi.fillna(50.0)


def timeframe_seconds(tf_str):
    mapping = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
    return mapping.get(tf_str, 60)


def prossimo_controllo_secondi(tf_str):
    intervallo = timeframe_seconds(tf_str)
    trascorso = time.time() % intervallo
    return max(2.0, intervallo - trascorso + 2.0)


# ============================================================
# CALCOLI PORTAFOGLIO
# ============================================================

def reset_posizione_cfg(cfg, simbolo):
    cfg["crypto_in_pancia"][simbolo] = 0.0
    cfg["prezzo_acquisto_effettivo"][simbolo] = 0.0
    cfg["importo_speso_effettivo"][simbolo] = 0.0
    cfg["posizioni_aperte"][simbolo] = False


def valore_posizioni_cfg(cfg, ultimi_prezzi):
    totale = 0.0
    for simbolo, aperta in cfg["posizioni_aperte"].items():
        if aperta:
            quantita = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
            prezzo = safe_float(ultimi_prezzi.get(simbolo, cfg["prezzo_acquisto_effettivo"].get(simbolo, 0.0)))
            totale += quantita * prezzo
    return totale


def capitale_investito_cfg(cfg):
    totale = 0.0
    for simbolo, aperta in cfg["posizioni_aperte"].items():
        if aperta:
            totale += safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))
    return totale


def costruisci_posizioni_status(cfg, ultimi_prezzi):
    posizioni = []
    for simbolo in [simbolo_visuale(b) for b in cfg["crypto_base_list"]]:
        if not cfg["posizioni_aperte"].get(simbolo, False):
            continue

        entry = safe_float(cfg["prezzo_acquisto_effettivo"].get(simbolo, 0.0))
        quantita = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
        capitale = safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))
        prezzo_now = safe_float(ultimi_prezzi.get(simbolo, entry))
        valore = quantita * prezzo_now
        pl_eur = valore - capitale
        pl_pct = ((prezzo_now - entry) / entry * 100) if entry > 0 else 0.0

        posizioni.append({
            "simbolo": simbolo,
            "entry": entry,
            "quantita": quantita,
            "capitale": capitale,
            "prezzo_corrente": prezzo_now,
            "valore": valore,
            "pl_eur": pl_eur,
            "pl_pct": pl_pct
        })

    return posizioni


def stima_balances_dashboard_da_config(cfg, status=None):
    """Stima saldo/investito/valore anche se status_bot.json è assente o non aggiornato.

    Questo evita che la dashboard mostri 5.000/0/0 solo perché l'engine è fermo
    o lo status è temporaneamente vuoto. Non modifica config.json.
    """
    status = status if isinstance(status, dict) else {}
    ultimi_prezzi = {}
    try:
        watchlist = status.get("watchlist", {}) if isinstance(status.get("watchlist", {}), dict) else {}
        for simbolo, info in watchlist.items():
            prezzo = safe_float((info or {}).get("price"), 0.0)
            if prezzo > 0:
                ultimi_prezzi[str(simbolo)] = prezzo
    except Exception:
        ultimi_prezzi = {}

    saldo = safe_float(cfg.get("saldo_eur", 0.0))
    investito = capitale_investito_cfg(cfg)
    valore = valore_posizioni_cfg(cfg, ultimi_prezzi)
    patrimonio = saldo + valore
    pl_realizzato = safe_float(cfg.get("profitto_accumulato", 0.0))
    pl_non_realizzato = valore - investito

    return {
        "saldo_eur": saldo,
        "investito": investito,
        "valore_posizioni": valore,
        "patrimonio": patrimonio,
        "profitto_accumulato": pl_realizzato,
        "profitto_non_realizzato": pl_non_realizzato,
        "totale_acquisti": int(safe_int(cfg.get("totale_acquisti", 0), 0)),
        "totale_vendite": int(safe_int(cfg.get("totale_vendite", 0), 0)),
        "posizioni_aperte": sum(1 for v in cfg.get("posizioni_aperte", {}).values() if safe_bool(v)),
    }




def stima_balances_dashboard_da_sqlite():
    """Fallback SOLO VISIVO per la dashboard.

    Se per un problema di refresh/status la UI prova a mostrare valori default
    tipo 5.000 EUR e 0 posizioni, leggiamo l'ultimo snapshot SQLite per non
    dare l'impressione di reset. Non modifica config.json, CSV o database.
    """
    try:
        if not FILE_DB.exists():
            return {}
        with _sqlite_connect_readonly() as conn:
            snap = conn.execute(
                """
                SELECT saldo_cash, valore_posizioni, equity_totale,
                       profitto_realizzato AS pnl_realizzato,
                       profitto_non_realizzato AS pnl_non_realizzato,
                       posizioni_aperte
                FROM equity_snapshots
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            counts = conn.execute(
                """
                SELECT
                  SUM(CASE WHEN operazione LIKE 'COMPRA%' OR operazione LIKE 'BUY%' THEN 1 ELSE 0 END) AS acquisti,
                  SUM(CASE WHEN operazione LIKE 'VENDI%' OR operazione LIKE 'SELL%' OR operazione LIKE 'TAKE_PROFIT%' OR operazione LIKE 'STOP_LOSS%' OR operazione LIKE 'CHIUDI%' THEN 1 ELSE 0 END) AS vendite
                FROM trades
                """
            ).fetchone()
        if not snap:
            return {}
        saldo = safe_float(snap['saldo_cash'])
        valore = safe_float(snap['valore_posizioni'])
        patrimonio = safe_float(snap['equity_totale'], saldo + valore)
        if saldo <= 0 and valore <= 0 and patrimonio <= 0:
            return {}
        return {
            'saldo_eur': saldo,
            'investito': max(0.0, patrimonio - saldo),
            'valore_posizioni': valore,
            'patrimonio': patrimonio,
            'profitto_accumulato': safe_float(snap['pnl_realizzato']),
            'profitto_non_realizzato': safe_float(snap['pnl_non_realizzato']),
            'totale_acquisti': int(safe_int(counts['acquisti'] if counts else 0, 0)),
            'totale_vendite': int(safe_int(counts['vendite'] if counts else 0, 0)),
            'posizioni_aperte': int(safe_int(snap['posizioni_aperte'], 0)),
            '_fonte': 'sqlite_fallback_visuale',
        }
    except Exception as e:
        log_errore('Errore fallback dashboard da SQLite', e)
        return {}


def dashboard_state_sembra_reset(balances):
    """Rileva solo reset VISIVI: 5.000 cash, zero investito, zero movimenti.

    Non decide la logica trading. Serve solo a impedire che un click su card/report
    faccia apparire la dashboard come resettata quando i dati veri esistono ancora.
    """
    try:
        saldo = safe_float(balances.get('saldo_eur', 0.0))
        investito = safe_float(balances.get('investito', 0.0))
        valore = safe_float(balances.get('valore_posizioni', 0.0))
        acquisti = safe_int(balances.get('totale_acquisti', 0), 0)
        vendite = safe_int(balances.get('totale_vendite', 0), 0)
        pos = safe_int(balances.get('posizioni_aperte', 0), 0)
        return (
            abs(saldo - safe_float(DEFAULT_CONFIG.get('saldo_eur', 5000.0), 5000.0)) < 0.01
            and investito <= 0.01
            and valore <= 0.01
            and acquisti == 0
            and vendite == 0
            and pos == 0
        )
    except Exception:
        return False


def dashboard_state_ha_dati_reali(balances):
    try:
        return (
            safe_int(balances.get('totale_acquisti', 0), 0) > 0
            or safe_int(balances.get('totale_vendite', 0), 0) > 0
            or safe_int(balances.get('posizioni_aperte', 0), 0) > 0
            or safe_float(balances.get('valore_posizioni', 0.0)) > 0.01
            or abs(safe_float(balances.get('saldo_eur', 0.0)) - safe_float(DEFAULT_CONFIG.get('saldo_eur', 5000.0), 5000.0)) > 0.01
        )
    except Exception:
        return False



def status_dashboard_valido(status):
    """Ritorna True solo se status_bot.json contiene dati UI completi.

    Serve alla dashboard: se il file è temporaneamente vuoto/incompleto mentre
    l'engine lo sta riscrivendo, la UI deve mantenere l'ultimo stato valido
    invece di mostrare valori default tipo 5.000 EUR / 0 posizioni.
    """
    try:
        if not isinstance(status, dict) or not status:
            return False
        balances = status.get("balances", {})
        engine = status.get("engine", {})
        watchlist = status.get("watchlist", {})
        settings = status.get("settings", {})
        has_balances = isinstance(balances, dict) and (
            safe_float(balances.get("saldo_eur", 0.0)) > 0
            or safe_float(balances.get("valore_posizioni", 0.0)) > 0
            or safe_int(balances.get("totale_acquisti", 0), 0) > 0
            or safe_int(balances.get("totale_vendite", 0), 0) > 0
            or safe_int(balances.get("posizioni_aperte", 0), 0) > 0
        )
        has_engine = isinstance(engine, dict) and (
            has_value(engine.get("last_update"))
            or has_value(engine.get("pid"))
            or has_value(engine.get("messaggio"))
        )
        has_watchlist = isinstance(watchlist, dict) and len(watchlist) > 0
        has_settings = isinstance(settings, dict) and len(settings) > 0
        return has_balances or (has_engine and (has_watchlist or has_settings))
    except Exception:
        return False


def _calcola_drawdown_da_storico(storico_saldi):
    try:
        valori = [safe_float(v) for v in (storico_saldi or []) if safe_float(v) > 0]
        if not valori:
            return 0.0
        picco = valori[0]
        max_dd = 0.0
        for valore in valori:
            picco = max(picco, valore)
            if picco > 0:
                max_dd = min(max_dd, (valore - picco) / picco * 100.0)
        return abs(max_dd)
    except Exception:
        return 0.0


def salva_snapshot_equity_sqlite(cfg, ultimi_prezzi=None, motivo="", force=False):
    """Salva uno snapshot del portafoglio. Throttled per non scrivere ogni secondo."""
    global _ultimo_snapshot_sqlite
    try:
        adesso = time.time()
        if not force and (adesso - _ultimo_snapshot_sqlite) < SQLITE_SNAPSHOT_INTERVAL_SECONDS:
            return
        ultimi_prezzi = ultimi_prezzi or {}
        inizializza_database_sqlite()
        investito = capitale_investito_cfg(cfg)
        valore_pos = valore_posizioni_cfg(cfg, ultimi_prezzi)
        saldo_cash = safe_float(cfg.get("saldo_eur", 0.0))
        equity = saldo_cash + valore_pos
        pnl_realizzato = safe_float(cfg.get("profitto_accumulato", 0.0))
        pnl_non_realizzato = valore_pos - investito
        pos_aperte = sum(1 for v in cfg.get("posizioni_aperte", {}).values() if safe_bool(v))

        with _sqlite_connect() as conn:
            # Drawdown corretto: calcolato sull'equity totale, non sul solo saldo cash.
            # Il saldo cash scende quando il bot compra, quindi non può essere usato
            # come misura di drawdown del portafoglio.
            try:
                picco_row = conn.execute("SELECT MAX(equity_totale) AS picco FROM equity_snapshots").fetchone()
                picco_precedente = safe_float(picco_row["picco"] if picco_row else 0.0)
                picco = max(picco_precedente, equity)
                drawdown = ((picco - equity) / picco * 100.0) if picco > 0 else 0.0
            except Exception:
                drawdown = 0.0

            conn.execute(
                """
                INSERT INTO equity_snapshots (
                    data_ora, modalita, saldo_cash, capitale_investito, valore_posizioni,
                    equity_totale, profitto_realizzato, profitto_non_realizzato,
                    posizioni_aperte, drawdown_pct, motivo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_str(), "SIMULAZIONE", saldo_cash, investito, valore_pos, equity,
                    pnl_realizzato, pnl_non_realizzato, pos_aperte, drawdown, str(motivo or ""),
                ),
            )

            for simbolo in [simbolo_visuale(b) for b in cfg.get("crypto_base_list", [])]:
                aperta = 1 if safe_bool(cfg.get("posizioni_aperte", {}).get(simbolo, False)) else 0
                quantita = safe_float(cfg.get("crypto_in_pancia", {}).get(simbolo, 0.0))
                entry = safe_float(cfg.get("prezzo_acquisto_effettivo", {}).get(simbolo, 0.0))
                capitale = safe_float(cfg.get("importo_speso_effettivo", {}).get(simbolo, 0.0))
                prezzo_now = safe_float(ultimi_prezzi.get(simbolo, entry))
                valore = quantita * prezzo_now
                pnl = valore - capitale if aperta else 0.0
                pnl_pct = ((prezzo_now - entry) / entry * 100.0) if aperta and entry > 0 else 0.0
                conn.execute(
                    """
                    INSERT INTO current_positions (
                        crypto, aperta, quantita, prezzo_medio_acquisto, importo_investito,
                        prezzo_corrente, valore_attuale, pnl_non_realizzato, pnl_pct, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(crypto) DO UPDATE SET
                        aperta=excluded.aperta,
                        quantita=excluded.quantita,
                        prezzo_medio_acquisto=excluded.prezzo_medio_acquisto,
                        importo_investito=excluded.importo_investito,
                        prezzo_corrente=excluded.prezzo_corrente,
                        valore_attuale=excluded.valore_attuale,
                        pnl_non_realizzato=excluded.pnl_non_realizzato,
                        pnl_pct=excluded.pnl_pct,
                        updated_at=excluded.updated_at
                    """,
                    (simbolo, aperta, quantita, entry, capitale, prezzo_now, valore, pnl, pnl_pct, now_str()),
                )
            conn.commit()
        _ultimo_snapshot_sqlite = adesso
    except Exception as e:
        try:
            with open(FILE_ERRORI, "a", encoding="utf-8") as f:
                f.write(f"[{now_str()}] Errore snapshot SQLite | {repr(e)}\n")
        except Exception:
            pass



# ============================================================
# ENGINE
# ============================================================

def scrivi_status_engine(cfg, watchlist, ohlc_by_symbol, ultimi_prezzi, running=True, messaggio=""):
    investito = capitale_investito_cfg(cfg)
    valore_posizioni = valore_posizioni_cfg(cfg, ultimi_prezzi)
    patrimonio = safe_float(cfg["saldo_eur"]) + valore_posizioni
    profitto_non_realizzato = valore_posizioni - investito

    status = {
        "engine": {
            "running": running,
            "pid": os.getpid(),
            "last_update": now_str(),
            "messaggio": messaggio,
            "version": APP_VERSION
        },
        "balances": {
            "saldo_eur": safe_float(cfg["saldo_eur"]),
            "investito": investito,
            "valore_posizioni": valore_posizioni,
            "patrimonio": patrimonio,
            "profitto_accumulato": safe_float(cfg["profitto_accumulato"]),
            "profitto_non_realizzato": profitto_non_realizzato,
            "totale_acquisti": int(cfg["totale_acquisti"]),
            "totale_vendite": int(cfg["totale_vendite"]),
            "posizioni_aperte": sum(1 for v in cfg["posizioni_aperte"].values() if v)
        },
        "settings": {
            "risk_percent": safe_float(cfg["percentuale_rischio_per_trade"]),
            "modalita_trading": normalizza_modalita_trading(cfg.get("modalita_trading", "Normale")),
            "take_profit": safe_float(cfg["take_profit_percentuale"]),
            "stop_loss": safe_float(cfg["stop_loss_percentuale"]),
            "auto_profit_loss_attivo": bool(cfg["auto_profit_loss_attivo"]),
            "auto_trading_attivo": bool(cfg.get("auto_trading_attivo", False)),
            "timeframe": cfg["timeframe"],
            "periodo_rsi": int(cfg["periodo_rsi"]),
            "soglia_acquisto": safe_float(cfg["soglia_acquisto"]),
            "soglia_vendita": safe_float(cfg["soglia_vendita"]),
            "commissione_percentuale": safe_float(cfg["commissione_percentuale"]),
            "fee_aware_sell_attivo": safe_bool(cfg.get("fee_aware_sell_attivo", True), True),
            "profitto_minimo_netto_percentuale": safe_float(cfg.get("profitto_minimo_netto_percentuale", 0.45), 0.45),
            "cooldown_trade_minuti": safe_int(cfg.get("cooldown_trade_minuti", 12), 12),
            "max_posizioni_aperte": safe_int(cfg.get("max_posizioni_aperte", 8), 8)
        },
        "watchlist": watchlist,
        "positions": costruisci_posizioni_status(cfg, ultimi_prezzi),
        "ohlc": ohlc_by_symbol,
        "storico_saldi": cfg["storico_saldi"][-300:]
    }

    save_json_atomic(FILE_STATUS, status)
    salva_snapshot_equity_sqlite(cfg, ultimi_prezzi, motivo=messaggio)


def ottieni_prezzo_corrente_engine(simbolo, cfg, market_map, ultimi_prezzi):
    """Restituisce solo un prezzo già validato per operazioni manuali/chiusure.

    Se non esiste un prezzo sicuro in cache, prova a scaricarne uno nuovo. I dati
    FALLBACK possono essere mostrati nella dashboard, ma non devono mai alimentare
    acquisti, vendite o chiusure manuali/automatiche.
    """
    # Per operazioni manuali/chiusure forziamo un fetch fresco: usare solo la
    # cache potrebbe vendere/comprare a un prezzo vecchio se l'engine non aggiorna.
    df = fetch_ohlcv_in_eur(simbolo, market_map, timeframe=cfg.get("timeframe", "1m"), limit=2)
    trade_safe = bool(getattr(df, "attrs", {}).get("trade_safe", True))
    fonte_dati = str(getattr(df, "attrs", {}).get("fonte_dati", "REAL"))
    motivo = str(getattr(df, "attrs", {}).get("motivo_sicurezza", ""))
    if not trade_safe:
        raise ValueError(
            f"Prezzo non sicuro per {simbolo}: fonte {fonte_dati}. "
            f"Operazione bloccata. {motivo}"
        )

    prezzo = safe_float(df["close"].iloc[-1])
    if prezzo <= 0:
        raise ValueError(f"Prezzo corrente non valido per {simbolo}")
    ultimi_prezzi[simbolo] = prezzo
    return prezzo


def esegui_acquisto_manuale(cfg, simbolo, importo_eur, prezzo_corrente, rsi=0.0, operazione="COMPRA_MANUALE"):
    importo_eur = safe_float(importo_eur)
    prezzo_corrente = safe_float(prezzo_corrente)

    if importo_eur < 10.0:
        raise ValueError("Importo minimo manuale: 10 EUR")
    if prezzo_corrente <= 0:
        raise ValueError("Prezzo corrente non valido")
    if safe_float(cfg["saldo_eur"]) < importo_eur:
        raise ValueError("Saldo insufficiente per acquisto manuale")

    commissione = safe_float(cfg["commissione_percentuale"]) / 100
    capitale_netto = importo_eur * (1 - commissione)
    commissione_eur = importo_eur - capitale_netto
    quantita_nuova = capitale_netto / prezzo_corrente
    posizione_gia_aperta = cfg["posizioni_aperte"].get(simbolo, False)

    if posizione_gia_aperta:
        capitale_vecchio = safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))
        quantita_vecchia = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
        nuovo_capitale_totale = capitale_vecchio + importo_eur
        nuova_quantita_totale = quantita_vecchia + quantita_nuova
        cfg["crypto_in_pancia"][simbolo] = nuova_quantita_totale
        cfg["importo_speso_effettivo"][simbolo] = nuovo_capitale_totale
        cfg["prezzo_acquisto_effettivo"][simbolo] = (nuovo_capitale_totale * (1 - commissione)) / nuova_quantita_totale
    else:
        cfg["crypto_in_pancia"][simbolo] = quantita_nuova
        cfg["importo_speso_effettivo"][simbolo] = importo_eur
        cfg["prezzo_acquisto_effettivo"][simbolo] = prezzo_corrente
        cfg["posizioni_aperte"][simbolo] = True

    cfg["saldo_eur"] = safe_float(cfg["saldo_eur"]) - importo_eur
    cfg["totale_acquisti"] = int(cfg["totale_acquisti"]) + 1
    cfg["storico_saldi"].append(round(safe_float(cfg["saldo_eur"]), 2))

    registra_operazione(
        simbolo, operazione, prezzo_corrente, rsi, safe_float(cfg["saldo_eur"]),
        f"Importo {importo_eur:.2f} EUR | Quantità {quantita_nuova:.8f} | Commissione {commissione_eur:.2f} EUR",
        quantita=quantita_nuova,
        importo=importo_eur,
        commissione=commissione_eur,
        profitto=0.0,
        percentuale=100.0,
    )
    aggiorna_ultimo_trade_ts(cfg, simbolo)
    log_evento(f"{operazione} {simbolo}: {importo_eur:.2f} EUR a {prezzo_corrente:.8f}")


def esegui_vendita_manuale(cfg, simbolo, percentuale, prezzo_corrente, rsi=0.0, operazione="VENDI_MANUALE"):

    percentuale = safe_float(percentuale)
    prezzo_corrente = safe_float(prezzo_corrente)

    if percentuale <= 0 or percentuale > 100:
        raise ValueError("Percentuale vendita non valida")
    if prezzo_corrente <= 0:
        raise ValueError("Prezzo corrente non valido")
    if not cfg["posizioni_aperte"].get(simbolo, False):
        raise ValueError(f"Nessuna posizione aperta su {simbolo}")

    quota = percentuale / 100.0
    quantita_totale = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
    capitale_totale = safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))

    quantita_venduta = quantita_totale * quota
    capitale_venduto = capitale_totale * quota

    commissione = safe_float(cfg["commissione_percentuale"]) / 100
    ricavo_lordo = quantita_venduta * prezzo_corrente
    commissione_eur = ricavo_lordo * commissione
    ricavo_netto = ricavo_lordo - commissione_eur
    profitto_netto = ricavo_netto - capitale_venduto

    cfg["saldo_eur"] = safe_float(cfg["saldo_eur"]) + ricavo_netto
    cfg["profitto_accumulato"] = safe_float(cfg["profitto_accumulato"]) + profitto_netto
    cfg["totale_vendite"] = int(cfg["totale_vendite"]) + 1
    cfg["storico_saldi"].append(round(safe_float(cfg["saldo_eur"]), 2))

    quantita_residua = quantita_totale - quantita_venduta
    capitale_residuo = capitale_totale - capitale_venduto

    if percentuale >= 99.999 or quantita_residua <= 0 or capitale_residuo <= 0:
        reset_posizione_cfg(cfg, simbolo)
    else:
        cfg["crypto_in_pancia"][simbolo] = quantita_residua
        cfg["importo_speso_effettivo"][simbolo] = capitale_residuo
        cfg["posizioni_aperte"][simbolo] = True

    registra_operazione(
        simbolo, operazione, prezzo_corrente, rsi, safe_float(cfg["saldo_eur"]),
        f"Venduta quota {percentuale:.2f}% | Quantità {quantita_venduta:.8f} | Ricavo netto {ricavo_netto:.2f} EUR | P/L {profitto_netto:+.2f} EUR",
        quantita=quantita_venduta,
        importo=ricavo_netto,
        commissione=commissione_eur,
        profitto=profitto_netto,
        percentuale=percentuale,
    )
    aggiorna_ultimo_trade_ts(cfg, simbolo)
    log_evento(f"{operazione} {simbolo}: {percentuale:.2f}% | ricavo {ricavo_netto:.2f} EUR | P/L {profitto_netto:+.2f} EUR")


def esegui_vendita_importo_eur(cfg, simbolo, importo_eur, prezzo_corrente, rsi=0.0, operazione="VENDI_IMPORTO_EUR"):
    """Vende una quota della posizione scegliendo liberamente l'importo in EUR.

    L'importo è interpretato come valore lordo della posizione da liquidare al
    prezzo corrente. Il saldo accreditato sarà quindi al netto della commissione.
    """
    importo_eur = safe_float(importo_eur)
    prezzo_corrente = safe_float(prezzo_corrente)

    if importo_eur <= 0:
        raise ValueError("Importo vendita non valido")
    if prezzo_corrente <= 0:
        raise ValueError("Prezzo corrente non valido")
    if not cfg["posizioni_aperte"].get(simbolo, False):
        raise ValueError(f"Nessuna posizione aperta su {simbolo}")

    quantita_totale = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
    capitale_totale = safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))
    valore_lordo_totale = quantita_totale * prezzo_corrente

    if quantita_totale <= 0 or valore_lordo_totale <= 0:
        raise ValueError(f"Posizione {simbolo} non valida")

    # Tolleranza di 1 centesimo per evitare blocchi su arrotondamenti float.
    if importo_eur > valore_lordo_totale + 0.01:
        raise ValueError(
            f"Importo superiore alla posizione disponibile: {importo_eur:.2f} EUR richiesti, "
            f"{valore_lordo_totale:.2f} EUR disponibili"
        )

    if importo_eur >= valore_lordo_totale - 0.01:
        quota = 1.0
        importo_lordo_effettivo = valore_lordo_totale
    else:
        quota = importo_eur / valore_lordo_totale
        importo_lordo_effettivo = importo_eur

    percentuale = quota * 100.0
    quantita_venduta = quantita_totale * quota
    capitale_venduto = capitale_totale * quota

    commissione = safe_float(cfg["commissione_percentuale"]) / 100
    ricavo_lordo = quantita_venduta * prezzo_corrente
    commissione_eur = ricavo_lordo * commissione
    ricavo_netto = ricavo_lordo - commissione_eur
    profitto_netto = ricavo_netto - capitale_venduto

    cfg["saldo_eur"] = safe_float(cfg["saldo_eur"]) + ricavo_netto
    cfg["profitto_accumulato"] = safe_float(cfg["profitto_accumulato"]) + profitto_netto
    cfg["totale_vendite"] = int(cfg["totale_vendite"]) + 1
    cfg["storico_saldi"].append(round(safe_float(cfg["saldo_eur"]), 2))

    quantita_residua = quantita_totale - quantita_venduta
    capitale_residuo = capitale_totale - capitale_venduto

    if quota >= 0.999999 or quantita_residua <= 0 or capitale_residuo <= 0:
        reset_posizione_cfg(cfg, simbolo)
    else:
        cfg["crypto_in_pancia"][simbolo] = quantita_residua
        cfg["importo_speso_effettivo"][simbolo] = capitale_residuo
        cfg["posizioni_aperte"][simbolo] = True

    registra_operazione(
        simbolo, operazione, prezzo_corrente, rsi, safe_float(cfg["saldo_eur"]),
        f"Vendita importo libero {importo_lordo_effettivo:.2f} EUR lordi | Quantità {quantita_venduta:.8f} | "
        f"Ricavo netto {ricavo_netto:.2f} EUR | P/L {profitto_netto:+.2f} EUR",
        quantita=quantita_venduta,
        importo=ricavo_netto,
        commissione=commissione_eur,
        profitto=profitto_netto,
        percentuale=percentuale,
    )
    aggiorna_ultimo_trade_ts(cfg, simbolo)
    log_evento(
        f"{operazione} {simbolo}: {importo_lordo_effettivo:.2f} EUR lordi ({percentuale:.2f}%) | "
        f"ricavo netto {ricavo_netto:.2f} EUR | P/L {profitto_netto:+.2f} EUR"
    )


def conta_posizioni_aperte_cfg(cfg):
    try:
        return sum(1 for v in cfg.get("posizioni_aperte", {}).values() if safe_bool(v))
    except Exception:
        return 0


def limite_posizioni_auto_raggiunto(cfg):
    """Ritorna (raggiunto, posizioni_aperte, max_posizioni).

    Guard stretto per l'Auto Trade: viene valutato prima di ogni acquisto
    automatico e dopo ogni acquisto dello stesso ciclo, così un gruppo di segnali
    contemporanei non può superare il limite configurato.
    """
    max_pos = safe_int(cfg.get("max_posizioni_aperte", 8), 8)
    max_pos = max(1, max_pos)
    pos_aperte = conta_posizioni_aperte_cfg(cfg)
    return pos_aperte >= max_pos, pos_aperte, max_pos


def minuti_da_ultimo_trade(cfg, simbolo):
    try:
        ts = safe_float(cfg.get("ultimo_trade_ts", {}).get(simbolo, 0.0), 0.0)
        if ts <= 0:
            return 999999.0
        return max(0.0, (time.time() - ts) / 60.0)
    except Exception:
        return 999999.0


def aggiorna_ultimo_trade_ts(cfg, simbolo):
    try:
        if not isinstance(cfg.get("ultimo_trade_ts"), dict):
            cfg["ultimo_trade_ts"] = {}
        cfg["ultimo_trade_ts"][simbolo] = time.time()
    except Exception:
        pass


def calcola_pnl_netto_vendita(cfg, simbolo, percentuale, prezzo_corrente):
    """Stima PnL netto di una vendita includendo anche la commissione di uscita.

    Il capitale investito salvato in importo_speso_effettivo è già il costo lordo
    sostenuto in acquisto; quindi il PnL netto qui considera acquisto + vendita.
    """
    quota = safe_float(percentuale, 100.0) / 100.0
    quantita_totale = safe_float(cfg.get("crypto_in_pancia", {}).get(simbolo, 0.0), 0.0)
    capitale_totale = safe_float(cfg.get("importo_speso_effettivo", {}).get(simbolo, 0.0), 0.0)
    quantita = quantita_totale * quota
    capitale = capitale_totale * quota
    commissione_pct = safe_float(cfg.get("commissione_percentuale", 0.1), 0.1) / 100.0
    ricavo_lordo = quantita * safe_float(prezzo_corrente, 0.0)
    commissione_vendita = ricavo_lordo * commissione_pct
    ricavo_netto = ricavo_lordo - commissione_vendita
    pnl_netto = ricavo_netto - capitale
    pnl_netto_pct = (pnl_netto / capitale * 100.0) if capitale > 0 else 0.0
    return {
        "quantita": quantita,
        "capitale": capitale,
        "ricavo_lordo": ricavo_lordo,
        "commissione_vendita": commissione_vendita,
        "ricavo_netto": ricavo_netto,
        "pnl_netto": pnl_netto,
        "pnl_netto_pct": pnl_netto_pct,
    }


def vendita_auto_fee_aware_consentita(cfg, simbolo, prezzo_corrente, motivo="VENDI_AUTO"):
    """Blocca solo le uscite automatiche in micro-profitto.

    Lo stop loss deve poter uscire anche in perdita. Take profit e RSI sell invece
    devono coprire commissione acquisto, commissione vendita e margine minimo.
    """
    if not safe_bool(cfg.get("fee_aware_sell_attivo", True), True):
        return True, "fee-aware disattivato", {}

    motivo_norm = str(motivo or "").upper()
    if "STOP_LOSS" in motivo_norm:
        return True, "stop loss consentito", {}

    stima = calcola_pnl_netto_vendita(cfg, simbolo, 100.0, prezzo_corrente)
    soglia = safe_float(cfg.get("profitto_minimo_netto_percentuale", 0.45), 0.45)
    if stima["pnl_netto_pct"] >= soglia and stima["pnl_netto"] > 0:
        return True, f"PnL netto {stima['pnl_netto_pct']:.2f}% >= soglia {soglia:.2f}%", stima

    return False, (
        f"vendita automatica bloccata: PnL netto stimato {stima['pnl_netto']:+.2f} EUR "
        f"({stima['pnl_netto_pct']:+.2f}%) sotto soglia {soglia:.2f}%"
    ), stima


def acquisto_auto_consentito(cfg, simbolo):
    """Filtro strategico leggero per ridurre overtrading: cooldown e max posizioni."""
    limite_raggiunto, pos_aperte, max_pos = limite_posizioni_auto_raggiunto(cfg)
    if limite_raggiunto:
        return False, f"max posizioni aperte raggiunto ({pos_aperte}/{max_pos})"

    cooldown = safe_float(cfg.get("cooldown_trade_minuti", 12), 12)
    elapsed = minuti_da_ultimo_trade(cfg, simbolo)
    if cooldown > 0 and elapsed < cooldown:
        return False, f"cooldown {elapsed:.1f}/{cooldown:.1f} min"

    return True, "ok"


def vendita_auto_cooldown_consentita(cfg, simbolo, motivo="VENDI_AUTO"):
    """Applica cooldown alle vendite automatiche, escluso lo stop loss."""
    motivo_norm = str(motivo or "").upper()
    if "STOP_LOSS" in motivo_norm:
        return True, "stop loss fuori cooldown"

    cooldown = safe_float(cfg.get("cooldown_trade_minuti", 12), 12)
    elapsed = minuti_da_ultimo_trade(cfg, simbolo)
    if cooldown > 0 and elapsed < cooldown:
        return False, f"cooldown {elapsed:.1f}/{cooldown:.1f} min"
    return True, "ok"


def processa_comandi_engine(cfg, processed_ids, ultimi_prezzi):
    stop_requested = False
    changed = False
    comandi = leggi_comandi()
    market_map_cache = None

    for comando in comandi:
        cid = comando.get("id")
        if not cid or cid in processed_ids:
            continue

        azione = comando.get("azione")
        params = comando.get("params", {})

        try:
            if azione == "ADD_FUNDS":
                amount = safe_float(params.get("amount", 0.0))
                if amount > 0:
                    cfg["saldo_eur"] = safe_float(cfg["saldo_eur"]) + amount
                    cfg["storico_saldi"].append(round(safe_float(cfg["saldo_eur"]), 2))
                    log_evento(f"Aggiunti fondi: +{amount:.2f} EUR")
                    changed = True

            elif azione == "SET_RISK":
                value = safe_float(params.get("risk_percent", cfg["percentuale_rischio_per_trade"]))
                if 0.1 <= value <= 100:
                    cfg["percentuale_rischio_per_trade"] = value
                    cfg["modalita_trading"] = "Personalizzata"
                    log_evento(f"Rischio aggiornato al {value:.2f}%")
                    changed = True

            elif azione == "SET_TIMEFRAME":
                timeframe = str(params.get("timeframe", cfg["timeframe"])).strip()
                if timeframe in VALID_TIMEFRAMES:
                    cfg["timeframe"] = timeframe
                    cfg["modalita_trading"] = "Personalizzata"
                    log_evento(f"Timeframe aggiornato: {timeframe}")
                    changed = True

            elif azione == "SET_AUTO_PL":
                cfg["auto_profit_loss_attivo"] = bool(params.get("active", cfg["auto_profit_loss_attivo"]))
                tp = safe_float(params.get("take_profit", cfg["take_profit_percentuale"]))
                sl = safe_float(params.get("stop_loss", cfg["stop_loss_percentuale"]))
                if 0.1 <= tp <= 100:
                    cfg["take_profit_percentuale"] = tp
                if 0.1 <= sl <= 100:
                    cfg["stop_loss_percentuale"] = sl
                cfg["modalita_trading"] = "Personalizzata"
                log_evento(
                    f"Auto P/L aggiornato: {'ON' if cfg['auto_profit_loss_attivo'] else 'OFF'} "
                    f"TP {cfg['take_profit_percentuale']:.2f}% SL {cfg['stop_loss_percentuale']:.2f}%"
                )
                changed = True

            elif azione == "SET_TRADING_MODE":
                modalita = normalizza_modalita_trading(params.get("mode", cfg.get("modalita_trading", "Normale")))
                if modalita == "Personalizzata":
                    modalita = "Normale"
                modalita, preset = applica_modalita_a_config(cfg, modalita)
                log_evento(
                    f"Modalità trading impostata: {modalita} | "
                    f"Inv. {preset['risk_percent']:.1f}% | BUY RSI <= {preset['buy_rsi']:.1f} | "
                    f"SELL RSI >= {preset['sell_rsi']:.1f} | TP {preset['take_profit']:.1f}% | "
                    f"SL {preset['stop_loss']:.1f}% | TF {preset['timeframe']}"
                )
                changed = True

            elif azione == "SET_AUTO_TRADE":
                cfg["auto_trading_attivo"] = bool(params.get("active", cfg.get("auto_trading_attivo", False)))
                buy_rsi = safe_float(params.get("buy_rsi", cfg.get("soglia_acquisto", 35)))
                sell_rsi = safe_float(params.get("sell_rsi", cfg.get("soglia_vendita", 65)))
                if 1 <= buy_rsi <= 99:
                    cfg["soglia_acquisto"] = buy_rsi
                if 1 <= sell_rsi <= 99:
                    cfg["soglia_vendita"] = sell_rsi
                cfg["modalita_trading"] = "Personalizzata"
                log_evento(
                    f"Auto Trade aggiornato: {'ON' if cfg.get('auto_trading_attivo', False) else 'OFF'} "
                    f"BUY RSI <= {cfg['soglia_acquisto']:.1f} | SELL RSI >= {cfg['soglia_vendita']:.1f}"
                )
                changed = True

            elif azione == "BUY_MANUAL":
                simbolo = str(params.get("symbol", "")).upper()
                importo = safe_float(params.get("amount", 0.0))
                if simbolo not in cfg["posizioni_aperte"]:
                    raise ValueError(f"Simbolo non valido: {simbolo}")
                if market_map_cache is None:
                    market_map_cache = prepara_mercati(cfg)
                prezzo = ottieni_prezzo_corrente_engine(simbolo, cfg, market_map_cache, ultimi_prezzi)
                esegui_acquisto_manuale(cfg, simbolo, importo, prezzo)
                changed = True

            elif azione == "SELL_MANUAL":
                simbolo = str(params.get("symbol", "")).upper()
                percentuale = safe_float(params.get("percent", 100.0))
                if simbolo not in cfg["posizioni_aperte"]:
                    raise ValueError(f"Simbolo non valido: {simbolo}")
                if market_map_cache is None:
                    market_map_cache = prepara_mercati(cfg)
                prezzo = ottieni_prezzo_corrente_engine(simbolo, cfg, market_map_cache, ultimi_prezzi)
                esegui_vendita_manuale(cfg, simbolo, percentuale, prezzo)
                changed = True

            elif azione == "SELL_MANUAL_EUR":
                simbolo = str(params.get("symbol", "")).upper()
                importo = safe_float(params.get("amount_eur", 0.0))
                if simbolo not in cfg["posizioni_aperte"]:
                    raise ValueError(f"Simbolo non valido: {simbolo}")
                if market_map_cache is None:
                    market_map_cache = prepara_mercati(cfg)
                prezzo = ottieni_prezzo_corrente_engine(simbolo, cfg, market_map_cache, ultimi_prezzi)
                esegui_vendita_importo_eur(cfg, simbolo, importo, prezzo)
                changed = True

            elif azione == "CLOSE_ALL":
                aperte = [s for s, a in cfg["posizioni_aperte"].items() if a]
                if market_map_cache is None:
                    market_map_cache = prepara_mercati(cfg)
                for simbolo in aperte:
                    try:
                        prezzo_corrente = ottieni_prezzo_corrente_engine(simbolo, cfg, market_map_cache, ultimi_prezzi)
                        esegui_vendita_manuale(cfg, simbolo, 100.0, prezzo_corrente)
                    except Exception as e:
                        log_errore(f"Errore chiusura posizione {simbolo}", e)
                changed = True

            elif azione == "RESET":
                crypto_list = cfg.get("crypto_base_list", DEFAULT_CONFIG["crypto_base_list"])
                cfg.clear()
                cfg.update(deep_copy_json(DEFAULT_CONFIG))
                cfg["crypto_base_list"] = crypto_list
                inizializza_dizionari_config(cfg)
                log_evento("Simulazione resettata.")
                changed = True

            elif azione == "STOP":
                log_evento("Richiesta stop engine ricevuta.")
                stop_requested = True
                changed = True

        except Exception as e:
            log_errore(f"Errore processando comando {azione}", e)

        processed_ids.add(cid)

    if changed:
        salva_config(cfg)

    salva_comandi_processati(processed_ids)
    return stop_requested


def ciclo_mercati_engine(cfg, market_map, ultimi_prezzi):
    watchlist = {}
    ohlc_by_symbol = {}

    for base in cfg["crypto_base_list"]:
        simbolo = simbolo_visuale(base)
        try:
            df = fetch_ohlcv_in_eur(simbolo, market_map, timeframe=cfg["timeframe"], limit=60)
            df["RSI"] = calcola_rsi(df, int(cfg["periodo_rsi"]))

            ultimo_rsi = safe_float(df["RSI"].iloc[-1])
            ultimo_prezzo = safe_float(df["close"].iloc[-1])
            primo_prezzo = safe_float(df["close"].iloc[0])
            variazione_pct = ((ultimo_prezzo - primo_prezzo) / primo_prezzo * 100) if primo_prezzo > 0 else 0.0

            if ultimo_prezzo <= 0:
                continue

            info = market_map.get(simbolo, {})
            fonte = info.get("reale", "") or "N/D"
            fonte_dati = str(getattr(df, "attrs", {}).get("fonte_dati", "REAL"))
            trade_safe = bool(getattr(df, "attrs", {}).get("trade_safe", True))

            prezzo_precedente = safe_float(ultimi_prezzi.get(simbolo, 0.0))
            salto_prezzo_pct = 0.0
            pending_jump = getattr(ciclo_mercati_engine, "_pending_price_validation", {})
            if prezzo_precedente > 0:
                salto_prezzo_pct = abs((ultimo_prezzo - prezzo_precedente) / prezzo_precedente * 100.0)
                if salto_prezzo_pct > MAX_PRICE_JUMP_TRADE_PCT:
                    trade_safe = False
                    pending = pending_jump.get(simbolo, {})
                    pending_price = safe_float(pending.get("price", 0.0))
                    stable_pct = abs((ultimo_prezzo - pending_price) / pending_price * 100.0) if pending_price > 0 else 999.0
                    count = safe_int(pending.get("count", 0)) + 1 if stable_pct <= PRICE_JUMP_STABILITY_PCT else 1
                    pending_jump[simbolo] = {"price": ultimo_prezzo, "count": count, "timestamp": time.time()}
                    setattr(ciclo_mercati_engine, "_pending_price_validation", pending_jump)
                    log_errore(
                        f"Dato mercato bloccato {simbolo}: salto prezzo {salto_prezzo_pct:.2f}% superiore a {MAX_PRICE_JUMP_TRADE_PCT:.2f}%",
                        f"prezzo precedente {prezzo_precedente:.8f} -> ultimo {ultimo_prezzo:.8f} | conferme {count}/{PRICE_JUMP_CONFIRMATIONS}"
                    )
                    if count >= PRICE_JUMP_CONFIRMATIONS and fonte_dati.upper() != "FALLBACK":
                        # Prezzo reale anomalo ma stabile per più letture: aggiorniamo
                        # solo la baseline per evitare blocchi permanenti. Trading ancora
                        # bloccato in questo ciclo; potrà riprendere dal prossimo giro.
                        ultimi_prezzi[simbolo] = ultimo_prezzo
                        pending_jump.pop(simbolo, None)
                        setattr(ciclo_mercati_engine, "_pending_price_validation", pending_jump)
                        log_evento(f"Baseline prezzo aggiornata per {simbolo} dopo conferme anti-salto.")
            else:
                pending_jump.pop(simbolo, None)
                setattr(ciclo_mercati_engine, "_pending_price_validation", pending_jump)

            # Aggiorna la cache prezzi usabile per trading SOLO con dati sicuri.
            # Non salvare mai prezzi FALLBACK come riferimento: altrimenti, quando
            # tornano dati reali, il controllo salto-prezzo può bloccare il simbolo
            # in modo permanente o produrre confronti falsati.
            if trade_safe:
                ultimi_prezzi[simbolo] = ultimo_prezzo
                pending_jump.pop(simbolo, None)
                setattr(ciclo_mercati_engine, "_pending_price_validation", pending_jump)

            if fonte_dati.upper() == "FALLBACK":
                fonte = "FALLBACK - solo grafico"
            elif not trade_safe:
                fonte = "BLOCCATO - dato anomalo"

            watchlist[simbolo] = {
                "price": ultimo_prezzo,
                "rsi": ultimo_rsi,
                "source": fonte,
                "change_pct": variazione_pct,
                "has_position": bool(cfg["posizioni_aperte"].get(simbolo, False)),
                "trade_safe": bool(trade_safe),
                "data_source": fonte_dati,
                "price_jump_pct": salto_prezzo_pct,
            }
            ohlc_by_symbol[simbolo] = df[["open", "high", "low", "close"]].tail(60).to_dict("records")

            # AUTO TRADE RSI: apre una posizione simulata se RSI è sotto soglia
            # e non c'è già una posizione aperta sulla stessa crypto.
            if trade_safe and cfg.get("auto_trading_attivo", False) and not cfg["posizioni_aperte"].get(simbolo, False):
                soglia_buy = safe_float(cfg.get("soglia_acquisto", 35))
                saldo = safe_float(cfg.get("saldo_eur", 0.0))
                percentuale = safe_float(cfg.get("percentuale_rischio_per_trade", 5.0))
                importo_auto = min(saldo, saldo * percentuale / 100.0)

                if ultimo_rsi <= soglia_buy:
                    # MAXPOSITIONSFIX: riconta immediatamente prima di ogni acquisto.
                    # Questo impedisce che più segnali nello stesso ciclo superino
                    # max_posizioni_aperte prima che la dashboard/status si aggiorni.
                    buy_ok, buy_motivo = acquisto_auto_consentito(cfg, simbolo)
                    if not buy_ok:
                        log_evento(f"COMPRA_AUTO_RSI {simbolo} saltato: {buy_motivo}")
                    elif importo_auto < 10.0:
                        log_evento(f"COMPRA_AUTO_RSI {simbolo} saltato: importo minimo non raggiunto ({importo_auto:.2f} EUR)")
                    else:
                        limite_raggiunto, pos_aperte, max_pos = limite_posizioni_auto_raggiunto(cfg)
                        if limite_raggiunto:
                            log_evento(f"COMPRA_AUTO_RSI {simbolo} saltato: max posizioni aperte raggiunto ({pos_aperte}/{max_pos})")
                        else:
                            esegui_acquisto_manuale(
                                cfg,
                                simbolo,
                                importo_auto,
                                ultimo_prezzo,
                                rsi=ultimo_rsi,
                                operazione="COMPRA_AUTO_RSI",
                            )
                            salva_config(cfg)
                            watchlist[simbolo]["has_position"] = True
                            pos_aperte_after = conta_posizioni_aperte_cfg(cfg)
                            log_evento(
                                f"COMPRA_AUTO_RSI {simbolo}: RSI {ultimo_rsi:.2f} <= {soglia_buy:.2f} | "
                                f"importo {importo_auto:.2f} EUR | posizioni {pos_aperte_after}/{safe_int(cfg.get('max_posizioni_aperte', 8), 8)}"
                            )

            if trade_safe and cfg["auto_profit_loss_attivo"] and cfg["posizioni_aperte"].get(simbolo, False):
                prezzo_ingresso = safe_float(cfg["prezzo_acquisto_effettivo"].get(simbolo, 0.0))
                if prezzo_ingresso > 0:
                    variazione = ((ultimo_prezzo - prezzo_ingresso) / prezzo_ingresso) * 100
                    motivo = None
                    if variazione >= safe_float(cfg["take_profit_percentuale"]):
                        motivo = "TAKE_PROFIT_AUTO"
                    elif variazione <= -safe_float(cfg["stop_loss_percentuale"]):
                        motivo = "STOP_LOSS_AUTO"

                    if motivo is not None:
                        cooldown_ok, cooldown_motivo = vendita_auto_cooldown_consentita(cfg, simbolo, motivo)
                        if not cooldown_ok:
                            log_evento(f"{motivo} {simbolo} saltato: {cooldown_motivo}")
                            continue
                        sell_ok, sell_motivo, sell_stima = vendita_auto_fee_aware_consentita(cfg, simbolo, ultimo_prezzo, motivo)
                        if not sell_ok:
                            log_evento(f"{motivo} {simbolo} saltato: {sell_motivo}")
                            continue
                        capitale = safe_float(cfg["importo_speso_effettivo"].get(simbolo, 0.0))
                        quantita = safe_float(cfg["crypto_in_pancia"].get(simbolo, 0.0))
                        ricavo_lordo = quantita * ultimo_prezzo
                        commissione_eur = ricavo_lordo * safe_float(cfg["commissione_percentuale"]) / 100
                        ricavo = ricavo_lordo - commissione_eur
                        profitto = ricavo - capitale

                        cfg["saldo_eur"] = safe_float(cfg["saldo_eur"]) + ricavo
                        cfg["profitto_accumulato"] = safe_float(cfg["profitto_accumulato"]) + profitto
                        cfg["totale_vendite"] = int(cfg["totale_vendite"]) + 1
                        cfg["storico_saldi"].append(round(safe_float(cfg["saldo_eur"]), 2))

                        registra_operazione(
                            simbolo, motivo, ultimo_prezzo, ultimo_rsi, safe_float(cfg["saldo_eur"]),
                            f"Variazione {variazione:+.2f}% | Quantità {quantita:.8f} | Ricavo netto {ricavo:.2f} EUR | P/L {profitto:+.2f} EUR",
                            quantita=quantita,
                            importo=ricavo,
                            commissione=commissione_eur,
                            profitto=profitto,
                            percentuale=100.0,
                        )
                        reset_posizione_cfg(cfg, simbolo)
                        salva_config(cfg)
                        watchlist[simbolo]["has_position"] = False
                        log_evento(f"{motivo} {simbolo}: P/L {profitto:+.2f} EUR")

            # AUTO TRADE RSI: chiude la posizione se RSI è sopra soglia vendita.
            # Viene valutato dopo Take Profit/Stop Loss, così TP/SL hanno priorità.
            if trade_safe and cfg.get("auto_trading_attivo", False) and cfg["posizioni_aperte"].get(simbolo, False):
                soglia_sell = safe_float(cfg.get("soglia_vendita", 65))
                if ultimo_rsi >= soglia_sell:
                    cooldown_ok, cooldown_motivo = vendita_auto_cooldown_consentita(cfg, simbolo, "VENDI_AUTO_RSI")
                    sell_ok, sell_motivo, sell_stima = vendita_auto_fee_aware_consentita(cfg, simbolo, ultimo_prezzo, "VENDI_AUTO_RSI")
                    if not cooldown_ok:
                        log_evento(f"VENDI_AUTO_RSI {simbolo} saltata: {cooldown_motivo}")
                    elif not sell_ok:
                        log_evento(f"VENDI_AUTO_RSI {simbolo} saltata: {sell_motivo}")
                    else:
                        esegui_vendita_manuale(
                            cfg,
                            simbolo,
                            100.0,
                            ultimo_prezzo,
                            rsi=ultimo_rsi,
                            operazione="VENDI_AUTO_RSI",
                        )
                        salva_config(cfg)
                        watchlist[simbolo]["has_position"] = False
                        log_evento(f"VENDI_AUTO_RSI {simbolo}: RSI {ultimo_rsi:.2f} >= {soglia_sell:.2f} | {sell_motivo}")
                    continue

        except Exception as e:
            log_errore(f"Errore ciclo mercato {simbolo}", e)

    return watchlist, ohlc_by_symbol


def run_engine():
    try:
        log_evento(f"Tentativo avvio engine. PID corrente {os.getpid()}")
        log_percorsi_operativi("engine")
    except Exception:
        pass

    if not acquire_engine_lock():
        print("Engine già attivo o lock non disponibile.")
        return

    existing = get_engine_pid()
    if existing and existing != os.getpid():
        print(f"Engine già attivo con PID {existing}.")
        log_evento(f"Engine non avviato: già attivo PID {existing}")
        release_engine_lock()
        return

    try:
        FILE_PID.parent.mkdir(parents=True, exist_ok=True)
        save_text_atomic(FILE_PID, str(os.getpid()))
    except Exception as e:
        log_errore("Impossibile scrivere engine.pid", e)
        release_engine_lock()
        return

    inizializza_file_registro()
    inizializza_database_sqlite()
    sincronizza_csv_in_sqlite()
    cfg = carica_config()
    # Garantisce che config.json esista anche al primo avvio, utile per backup,
    # build onedir e ripartenze dopo stop/riavvio.
    salva_config(cfg)
    market_map = prepara_mercati(cfg)
    processed_ids = carica_comandi_processati()
    ultimi_prezzi = {}
    watchlist = {}
    ohlc_by_symbol = {}
    next_market_run = 0.0

    log_evento(f"Engine avviato. PID {os.getpid()}")

    try:
        while True:
            cfg = carica_config()
            stop = processa_comandi_engine(cfg, processed_ids, ultimi_prezzi)

            if stop:
                scrivi_status_engine(cfg, watchlist, ohlc_by_symbol, ultimi_prezzi, running=False, messaggio="Engine fermato da comando dashboard.")
                break

            adesso = time.time()
            if adesso >= next_market_run:
                market_map = prepara_mercati(cfg)
                watchlist, ohlc_by_symbol = ciclo_mercati_engine(cfg, market_map, ultimi_prezzi)
                cfg = carica_config()
                scrivi_status_engine(cfg, watchlist, ohlc_by_symbol, ultimi_prezzi, running=True, messaggio="Engine attivo.")
                next_market_run = adesso + prossimo_controllo_secondi(cfg["timeframe"])
            else:
                scrivi_status_engine(cfg, watchlist, ohlc_by_symbol, ultimi_prezzi, running=True, messaggio="Engine attivo.")
                time.sleep(1.0)

    except KeyboardInterrupt:
        log_evento("Engine interrotto da KeyboardInterrupt.")
    except Exception as e:
        log_errore("Errore critico engine", e)
    finally:
        try:
            if FILE_PID.exists() and FILE_PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
                FILE_PID.unlink()
        except Exception:
            pass

        cfg = carica_config()
        scrivi_status_engine(cfg, watchlist, ohlc_by_symbol, ultimi_prezzi, running=False, messaggio="Engine non attivo.")
        release_engine_lock()
        log_evento("Engine terminato.")


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


# ============================================================
# MAIN
# ============================================================

def main():
    engine_mode = "--engine" in sys.argv or os.environ.get(ENGINE_ENV_FLAG) == "1"
    if engine_mode:
        run_engine()
        return

    if "--sync" in sys.argv:
        inizializza_file_registro()
        inizializza_database_sqlite()
        nuove = sincronizza_csv_in_sqlite()
        print(f"SQLite sync completata: {nuove} nuove righe importate.")
        if "--report" not in sys.argv and "--charts" not in sys.argv and "--gui" not in sys.argv:
            return

    if "--report" in sys.argv:
        report = genera_file_report_sqlite()
        print(f"Report generato in: {FILE_REPORT_TXT}")
        print(f"Grafici generati in: {FILE_REPORT_EQUITY_PNG}, {FILE_REPORT_PNL_CRYPTO_PNG}, {FILE_REPORT_DRAWDOWN_PNG}")
        print(f"Trade totali: {report.get('trade_totali', 0)} | PnL realizzato: {report.get('pnl_realizzato', 0.0):+.2f} EUR")
        if "--charts" not in sys.argv and "--gui" not in sys.argv:
            return

    if "--charts" in sys.argv:
        info = genera_grafici_sqlite_png()
        print(f"Grafici SQLite generati: {', '.join(str(p) for p in info.get('files', {}).values())}")
        if "--gui" not in sys.argv:
            return

    app = QuantumDashboard()
    app.run()


if __name__ == "__main__":
    main()

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

APP_VERSION = "v2.0"
BUILD_NOTE = "Versione 2.0 ufficiale - base modulare stabile da v1.0.21 + SQLite final sync"
# v2.0: release ufficiale modulare stabile; include SQLite final sync dopo vendite/liquidazione/manual report.
# Mantiene la v1.0.20 modulare e allinea current_positions/equity_snapshots dopo comandi dashboard.
# Funzionalità ereditate dalla v1.0.19 senza riscrittura logica:
# Vendi EUR, vendita percentuale, EngineGuard, FeeAware, MaxPositionsFix,
# SQLite/CSV, AutoCharts sempre attivo e dashboard esistente.
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


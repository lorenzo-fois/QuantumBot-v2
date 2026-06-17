from . import settings as _settings
globals().update({k: v for k, v in vars(_settings).items() if not k.startswith('__')})

from . import database as _database
globals().update({k: v for k, v in vars(_database).items() if not k.startswith('__')})

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
            launcher = Path(os.environ.get("QUANTUMBOT_LAUNCHER", APP_DIR / "QuantumBot_v2_0.py"))
            args = [sys.executable, str(launcher), "--engine"]

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


def normalizza_watchlist_has_position(cfg, watchlist):
    """Allinea il flag visivo has_position della watchlist alla config reale.

    Micro-fix v1.0.20: dopo una liquidazione manuale o una chiusura totale,
    il saldo/SQLite/positions erano corretti ma alcuni elementi della watchlist
    potevano conservare has_position=True fino al ciclo prezzi successivo.
    Questa funzione corregge solo lo status/dashboard, senza modificare config,
    trading, CSV o SQLite.
    """
    if not isinstance(watchlist, dict):
        return {}

    try:
        symbols = set(str(k) for k in watchlist.keys())
        symbols.update(simbolo_visuale(base) for base in cfg.get("crypto_base_list", []))
        posizioni_aperte = cfg.get("posizioni_aperte", {}) if isinstance(cfg.get("posizioni_aperte", {}), dict) else {}
        quantita_map = cfg.get("crypto_in_pancia", {}) if isinstance(cfg.get("crypto_in_pancia", {}), dict) else {}
        capitale_map = cfg.get("importo_speso_effettivo", {}) if isinstance(cfg.get("importo_speso_effettivo", {}), dict) else {}

        normalizzata = {}
        for simbolo in sorted(symbols):
            info = watchlist.get(simbolo, {})
            info = dict(info) if isinstance(info, dict) else {}
            quantita = safe_float(quantita_map.get(simbolo, 0.0))
            capitale = safe_float(capitale_map.get(simbolo, 0.0))
            has_position_reale = bool(posizioni_aperte.get(simbolo, False)) and quantita > 0 and capitale > 0
            info["has_position"] = has_position_reale
            normalizzata[simbolo] = info
        return normalizzata
    except Exception as e:
        log_errore("Errore normalizzazione has_position watchlist", e)
        return watchlist


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
    watchlist = normalizza_watchlist_has_position(cfg, watchlist)
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
        # v2.0 SQLiteFinalSync:
        # dopo comandi manuali/dashboard (vendita %, Vendi EUR, Chiudi tutto, STOP)
        # forziamo uno snapshot SQLite immediato. Senza force, il throttle da 30s
        # può lasciare current_positions/equity_snapshots fermi allo stato precedente
        # se l'utente liquida e poi ferma/aggiorna subito il bot.
        try:
            salva_snapshot_equity_sqlite(cfg, ultimi_prezzi, motivo="Snapshot forzato dopo comando dashboard.", force=True)
        except Exception as e:
            log_errore("Errore snapshot forzato post-comando", e)

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


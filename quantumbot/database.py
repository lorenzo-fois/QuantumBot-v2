from . import settings as _settings
globals().update({k: v for k, v in vars(_settings).items() if not k.startswith('__')})

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



def sincronizza_posizioni_sqlite_da_config(cfg=None, motivo="sync_config_report"):
    """Allinea current_positions/equity_snapshots allo stato reale di config.json.

    Micro-fix v2.0: se l'utente liquida manualmente e poi genera subito
    report/grafici, il throttle degli snapshot può lasciare SQLite indietro di
    qualche secondo. Questa funzione è conservativa: non modifica config, CSV o
    logica trading; aggiorna solo le tabelle di report SQLite partendo dallo
    stato già salvato in config.json.
    """
    try:
        inizializza_database_sqlite()
        cfg = cfg or carica_config()
        if not isinstance(cfg, dict):
            return None

        symbols = [simbolo_visuale(b) for b in cfg.get("crypto_base_list", [])]
        saldo_cash = safe_float(cfg.get("saldo_eur", 0.0), 0.0)
        pnl_realizzato = safe_float(cfg.get("profitto_accumulato", 0.0), 0.0)

        with _sqlite_connect() as conn:
            prezzi_esistenti = {}
            try:
                for r in conn.execute("SELECT crypto, prezzo_corrente FROM current_positions").fetchall():
                    prezzi_esistenti[str(r["crypto"])] = safe_float(r["prezzo_corrente"], 0.0)
            except Exception:
                prezzi_esistenti = {}

            investito = 0.0
            valore_posizioni = 0.0
            pos_aperte = 0
            rows = []

            for simbolo in symbols:
                aperta = 1 if safe_bool(cfg.get("posizioni_aperte", {}).get(simbolo, False), False) else 0
                quantita = safe_float(cfg.get("crypto_in_pancia", {}).get(simbolo, 0.0), 0.0)
                entry = safe_float(cfg.get("prezzo_acquisto_effettivo", {}).get(simbolo, 0.0), 0.0)
                capitale = safe_float(cfg.get("importo_speso_effettivo", {}).get(simbolo, 0.0), 0.0)

                if not aperta or quantita <= 0 or capitale <= 0:
                    aperta = 0
                    quantita = 0.0
                    entry = 0.0
                    capitale = 0.0
                    prezzo_now = 0.0
                    valore = 0.0
                    pnl = 0.0
                    pnl_pct = 0.0
                else:
                    prezzo_now = safe_float(prezzi_esistenti.get(simbolo, 0.0), 0.0) or entry
                    valore = quantita * prezzo_now
                    pnl = valore - capitale
                    pnl_pct = ((prezzo_now - entry) / entry * 100.0) if entry > 0 else 0.0
                    investito += capitale
                    valore_posizioni += valore
                    pos_aperte += 1

                rows.append((simbolo, aperta, quantita, entry, capitale, prezzo_now, valore, pnl, pnl_pct, now_str()))

            equity = saldo_cash + valore_posizioni
            pnl_non_realizzato = valore_posizioni - investito
            try:
                picco_row = conn.execute("SELECT MAX(equity_totale) AS picco FROM equity_snapshots").fetchone()
                picco_precedente = safe_float(picco_row["picco"] if picco_row else 0.0, 0.0)
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
                    now_str(), "SIMULAZIONE", saldo_cash, investito, valore_posizioni, equity,
                    pnl_realizzato, pnl_non_realizzato, pos_aperte, drawdown, str(motivo or ""),
                ),
            )

            for row in rows:
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
                    row,
                )

            conn.commit()
            log_evento(
                f"SQLite final sync da config completato: posizioni {pos_aperte}, equity {equity:.2f} EUR, motivo={motivo}"
            )
            return {
                "saldo_cash": saldo_cash,
                "capitale_investito": investito,
                "valore_posizioni": valore_posizioni,
                "equity_totale": equity,
                "posizioni_aperte": pos_aperte,
            }
    except Exception as e:
        log_errore("Errore sync posizioni SQLite da config", e)
        return None

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
    # v2.0 SQLiteFinalSync: prima del report, allinea SQLite allo stato
    # finale salvato in config.json. Questo evita current_positions/snapshot
    # vecchi dopo liquidazioni manuali appena prima dello stop.
    sincronizza_posizioni_sqlite_da_config(motivo="report_final_sync")
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

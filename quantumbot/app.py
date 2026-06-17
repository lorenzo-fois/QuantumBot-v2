from . import settings as _settings
globals().update({k: v for k, v in vars(_settings).items() if not k.startswith('__')})

from . import database as _database
globals().update({k: v for k, v in vars(_database).items() if not k.startswith('__')})
from . import engine as _engine
globals().update({k: v for k, v in vars(_engine).items() if not k.startswith('__')})
from .dashboard import QuantumDashboard

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


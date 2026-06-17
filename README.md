# QuantumBot v2.0 Official

This repository contains the official modular v2.0 release of QuantumBot.

The original v1.0 stable version is available here:
https://github.com/lorenzo-fois/QuantumBot

QuantumBot è un'app desktop Python per **simulazione trading crypto**, dashboard locale, reportistica dati e analisi delle performance.

La versione v2.0 è la base modulare ufficiale: mantiene le funzioni della build stabile precedente e separa il progetto in file più leggibili, senza trasformarlo in un sistema di trading reale.

> **Nota importante:** QuantumBot è un progetto didattico/personale in modalità simulazione. Non esegue ordini reali e non costituisce consulenza finanziaria.

## Funzioni principali

- Dashboard desktop con Tkinter
- Engine separato dalla dashboard
- Trading simulato manuale
- Acquisto manuale
- Vendita per percentuale: 25%, 50%, 75%, 100%
- Vendita libera in EUR
- Chiusura/liquidazione posizioni
- Auto Trade RSI simulato
- Auto Profit/Loss simulato
- EngineGuard
- FeeAware
- MaxPositionsFix
- AutoCharts
- Registro operazioni CSV
- Database SQLite
- Snapshot equity e posizioni correnti
- Report TXT, JSON e CSV
- Grafici PNG: equity, PnL per crypto, drawdown
- Compatibilità macOS e supporto PyInstaller `onedir`

## Struttura progetto

```text
QuantumBot_v2_0.py              # launcher principale
quantumbot/
├── __init__.py
├── app.py                      # main runtime e opzioni CLI
├── dashboard.py                # interfaccia Tkinter
├── database.py                 # SQLite, CSV, report e grafici
├── engine.py                   # motore, comandi, mercato, trading simulato
├── settings.py                 # config, percorsi, utility, modalità trading
└── widgets.py                  # widget custom Tkinter
archive/
└── QuantumBot_v1_0_19_ORIGINALE_STABILE.py
```

## Requisiti

- Python 3.10 o superiore
- macOS consigliato per l'uso desktop
- Librerie indicate in `requirements.txt`

Installa le dipendenze:

```bash
python3 -m pip install -r requirements.txt
```

Se `ccxt` non è installato o Binance non è raggiungibile, QuantumBot resta utilizzabile in simulazione con dati fallback locali.

## Avvio

Da Terminale, nella cartella del progetto:

```bash
python3 QuantumBot_v2_0.py
```

Comandi utili senza GUI:

```bash
python3 QuantumBot_v2_0.py --sync
python3 QuantumBot_v2_0.py --report
python3 QuantumBot_v2_0.py --charts
python3 QuantumBot_v2_0.py --sync --report --charts
```

Controllo sintassi:

```bash
python3 -m py_compile QuantumBot_v2_0.py quantumbot/*.py
```

## Creazione app macOS

Puoi usare lo script incluso:

```bash
chmod +x crea_app_quantumbot_onedir.command
./crea_app_quantumbot_onedir.command
```

Oppure lanciare PyInstaller manualmente:

```bash
python3 -m PyInstaller --onedir --windowed --name QuantumBot QuantumBot_v2_0.py
```

La build verrà creata in:

```text
dist/QuantumBot.app
```

## File generati a runtime

QuantumBot crea diversi file locali durante l'utilizzo. Questi file sono esclusi da Git tramite `.gitignore`:

- `config.json`
- `status_bot.json`
- `registro_trade.csv`
- `quantumbot.db`
- `errori_bot.log`
- `eventi_bot.log`
- `engine_stdout.log`
- `engine_stderr.log`
- `report_quantumbot.*`
- `report_sqlite_*.png`

## Stato della versione

Versione: **v2.0 official**

Questa build è pensata come base stabile modulare per:

1. ulteriori test conservativi;
2. documentazione tecnica;
3. eventuale packaging macOS;
4. futuri miglioramenti grafici o strutturali senza perdere funzionalità.

## Roadmap consigliata

- Audit finale dei log dopo test reali
- Pulizia documentazione GitHub
- Test packaging macOS con PyInstaller `onedir`
- Manuale tecnico del codice
- Eventuale restyling grafico futuro
- Eventuale valutazione CustomTkinter solo dopo stabilità confermata

## Disclaimer

Il progetto è in modalità simulazione. Qualsiasi uso legato a investimenti reali richiederebbe test, controlli di rischio, validazione, conformità normativa e piena responsabilità dell'utilizzatore.

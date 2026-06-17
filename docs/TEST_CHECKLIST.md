# QuantumBot v2.0 - Test checklist

Usa questa checklist prima di promuovere una build come stabile.

## Avvio

- [ ] `python3 -m py_compile QuantumBot_v2_0.py quantumbot/*.py` senza errori
- [ ] `python3 QuantumBot_v2_0.py --sync --report --charts` senza crash
- [ ] Dashboard avviata correttamente
- [ ] Engine avviato correttamente
- [ ] `status_bot.json` aggiornato
- [ ] `engine.pid` o `engine.lock` coerente

## Dashboard

- [ ] Card saldo aggiornate
- [ ] Grafici visibili
- [ ] Popup watchlist funzionante
- [ ] Popup portafoglio funzionante
- [ ] Popup movimenti funzionante
- [ ] Popup report SQLite funzionante
- [ ] Popup log funzionante

## Trading simulato

- [ ] Compra manuale
- [ ] Vendi 25%
- [ ] Vendi 50%
- [ ] Vendi 75%
- [ ] Vendi 100%
- [ ] Vendi importo libero in EUR
- [ ] Chiudi tutte le posizioni
- [ ] Reset simulazione
- [ ] Aggiungi fondi

## Dati

- [ ] `registro_trade.csv` aggiornato
- [ ] `quantumbot.db` aggiornato
- [ ] `current_positions` coerente
- [ ] `equity_snapshots` coerente
- [ ] Report TXT generato
- [ ] Report JSON generato
- [ ] Report CSV generato
- [ ] Grafico equity PNG generato
- [ ] Grafico PnL PNG generato
- [ ] Grafico drawdown PNG generato

## Log

- [ ] `errori_bot.log` senza errori critici
- [ ] `engine_stderr.log` senza crash
- [ ] `engine_stdout.log` coerente
- [ ] `engine_launch.log` coerente
- [ ] `eventi_bot.log` coerente

## Packaging macOS

- [ ] `crea_app_quantumbot_onedir.command` eseguibile
- [ ] `dist/QuantumBot.app` generata
- [ ] App aperta da Finder
- [ ] Cartella dati scrivibile
- [ ] Dashboard chiusa senza fermare engine, se previsto
- [ ] Engine fermato correttamente da pulsante

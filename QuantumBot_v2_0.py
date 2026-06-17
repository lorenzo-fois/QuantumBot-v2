#!/usr/bin/env python3
"""QuantumBot v2.0 - launcher modulare con SQLite final snapshot fix.

Questo file resta il punto di ingresso per VS Code, Terminale e PyInstaller.
La logica è stata spostata nel package quantumbot/ senza cambiare le funzioni
stabili della v1.0.20 modulare, con micro-fix conservativi.
"""
import os
import sys
from pathlib import Path

# In modalità script, la cartella dati deve restare quella del launcher,
# non la sottocartella del package quantumbot/.
if not getattr(sys, "frozen", False):
    os.environ.setdefault("QUANTUMBOT_APP_DIR", str(Path(__file__).resolve().parent))
    os.environ.setdefault("QUANTUMBOT_LAUNCHER", str(Path(__file__).resolve()))

from quantumbot.app import main

if __name__ == "__main__":
    main()

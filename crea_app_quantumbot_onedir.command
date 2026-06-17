#!/bin/bash
set -e
cd "$(dirname "$0")"
echo "Pulizia build precedente..."
rm -rf build dist
rm -f QuantumBot.spec
echo "Creo app QuantumBot v2.0 ufficiale..."
python3 -m PyInstaller --onedir --windowed --name QuantumBot QuantumBot_v2_0.py
echo "App creata in: dist/QuantumBot.app"
open dist

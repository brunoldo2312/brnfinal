# explorer.py — Ponto de entrada principal.
# Sobe o nó completo (P2P + consenso PoS + web + ngrok) e,
# em paralelo, imprime no terminal o relatório da blockchain.

import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Carrega .env antes de importar os módulos da blockchain (eles leem env vars)
load_dotenv()

DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")
REPORT_INTERVAL = int(os.environ.get("BRN_REPORT_INTERVAL", "30"))


# ==================================================================
# Relatório em terminal (lê o SQLite)
# ==================================================================
def short(addr: str) -> str:
    if not addr:
        return "—"
    return addr[:14] + "…" + addr[-8:] if len(addr) > 24 else addr


def fmt_time(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def print_report():
    if not Path(DB_PATH).exists():
        print(f"[explorer] {DB_PATH} ainda não existe (aguarde o primeiro bloco).")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        print(f"[explorer] erro ao abrir {DB_PATH}: {e}")
        return

    print("\n" + "=" * 100)
    print(f" EXPLORER — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # ----- BLOCOS -----
    rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
    total_tx = 0
    total_vol = 0.0

    print(f"\n BLOCOS ({len(rows)}):")
    for (data,) in rows:
        blk = json.loads(data)
        txs = blk.get("transactions", [])
        total_tx += len(txs)
        print(f"  #{blk['index']:>4} | {fmt_time(blk['timestamp'])} | "
              f"val={short(blk.get('validator','')):22} | "
              f"{len(txs)} tx | {blk.get('hash','')[:16]}…")
        for tx in txs:
            total_vol += tx.get("amount", 0.0)
            print(f"          → {short(tx['from'])} → {short(tx['to'])} | "
                  f"{tx['amount']:.4f} BRN | {fmt_time(tx.get('timestamp',0))}")

    # ----- MEMPOOL -----
    mrows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
    print(f"\n MEMPOOL ({len(mrows)} pendentes):")
    if not mrows:
        print("   (vazia)")
    for (data,) in mrows:
        tx = json.loads(data)
        print(f"   {short(tx['from'])} → {short(tx['to'])} | "
              f"{tx['amount']:.4f} BRN | nonce={tx['nonce']}")

    # ----- SLASHING -----
    srows = conn.execute(
        "SELECT validator, reason, block_idx, ts FROM slashing ORDER BY ts DESC"
    ).fetchall()
    print(f"\n SLASHING ({len(srows)} banidos):")
    if not srows:
        print("   (nenhum)")
    for r in srows:
        print(f"   {short(r[0])} | bloco#{r[2]} | {r[1]} | {fmt_time(r[3])}")

    print("\n" + "-" * 100)
    print(f" Total de blocos ....: {len(rows)}")
    print(f" Total de tx ........: {total_tx}")
    print(f" Volume confirmado ..: {total_vol:.4f} BRN")
    print(f" Pendentes ..........: {len(mrows)}")
    print(f" Slashed ............: {len(srows)}")
    print("-" * 100)


# ==================================================================
# Loop de relatório em background
# ==================================================================
def report_loop():
    # Primeiro relatório após alguns segundos (dá tempo do nó iniciar)
    time.sleep(5)
    while True:
        try:
            print_report()
        except Exception as e:
            print(f"[explorer] erro no relatório: {e}")
        time.sleep(REPORT_INTERVAL)


# ==================================================================
# Main — sobe o nó completo + loop de relatório
# ==================================================================
def main():
    print("=" * 60)
    print(" BRN Explorer — iniciando nó completo")
    print("=" * 60)

    # Import tardio para garantir que .env já foi carregado
    from node import Node

    node = Node()

    # Sobe P2P + consenso + web + ngrok (dentro de uma thread)
    # para que o loop de relatório possa rodar em paralelo.
    t_node = threading.Thread(target=node.start, daemon=True, name="node")
    t_node.start()

    # Sobe loop de relatório em background
    t_report = threading.Thread(target=report_loop, daemon=True, name="report")
    t_report.start()

    # Mensagem final de instruções
    time.sleep(2)
    print("\n" + "=" * 60)
    print(" Dashboard disponível na URL do ngrok mostrada acima.")
    print(f" Banco SQLite: {os.path.abspath(DB_PATH)}")
    print(f" Relatório no terminal a cada {REPORT_INTERVAL}s.")
    print(" Pressione Ctrl+C para encerrar.")
    print("=" * 60 + "\n")

    # Mantém o processo principal vivo
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[explorer] encerrando…")
        node.running = False
        sys.exit(0)


if __name__ == "__main__":
    main()
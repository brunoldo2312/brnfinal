# web_server.py
# Dashboard web para visualizar transferências da blockchain BRN.
# Exposto publicamente via ngrok (token lido de variável de ambiente).

import os
import json
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request

# ---------- Configuração do ngrok (NUNCA hardcode o token) ----------
NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
WEB_HOST = os.environ.get("BRN_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("BRN_WEB_PORT", "5000"))
USE_NGROK = os.environ.get("BRN_USE_NGROK", "1") == "1"

app = Flask(__name__)

# Referência à blockchain (injetada pelo node.py)
_blockchain_ref = None


def set_blockchain(bc):
    """Chamado pelo node.py para injetar a instância da blockchain."""
    global _blockchain_ref
    _blockchain_ref = bc


# ------------------------------------------------------------------
# Coleta de transferências
# ------------------------------------------------------------------
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def collect_transfers(limit: int = 500) -> list:
    """Percorre a cadeia + mempool e devolve todas as transferências."""
    if _blockchain_ref is None:
        return []

    transfers = []

    # 1) Transações confirmadas (dentro de blocos)
    for blk in _blockchain_ref.chain:
        for tx in blk.transactions:
            transfers.append({
                "status": "confirmada",
                "block_index": blk.index,
                "block_hash": blk.hash[:16] + "...",
                "validator": blk.validator,
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "amount": tx.get("amount", 0.0),
                "nonce": tx.get("nonce", 0),
                "timestamp": tx.get("timestamp", blk.timestamp),
                "datetime": _iso(tx.get("timestamp", blk.timestamp)),
            })

    # 2) Transações pendentes (mempool)
    for tx in _blockchain_ref.pending:
        transfers.append({
            "status": "pendente",
            "block_index": None,
            "block_hash": None,
            "validator": None,
            "from": tx.get("from", ""),
            "to": tx.get("to", ""),
            "amount": tx.get("amount", 0.0),
            "nonce": tx.get("nonce", 0),
            "timestamp": tx.get("timestamp", time.time()),
            "datetime": _iso(tx.get("timestamp", time.time())),
        })

    # Mais recentes primeiro
    transfers.sort(key=lambda t: t["timestamp"], reverse=True)
    return transfers[:limit]


# ------------------------------------------------------------------
# Rotas HTTP
# ------------------------------------------------------------------
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>BRN — Transferências ao vivo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    background: #0d1117; color: #e6edf3; margin: 0; padding: 24px;
  }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .sub { color: #8b949e; font-size: 13px; margin-bottom: 20px; }
  .cards { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
  .card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 12px 16px; min-width: 160px;
  }
  .card .label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: .5px; }
  .card .value { font-size: 20px; font-weight: 600; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; background: #161b22; border-radius: 8px; overflow: hidden; }
  th, td { padding: 10px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #21262d; }
  th { background: #1c2128; font-weight: 600; color: #8b949e; text-transform: uppercase; font-size: 11px; letter-spacing: .4px; }
  tr:hover { background: #1c2128; }
  .addr { font-family: "SF Mono", Consolas, monospace; font-size: 12px; color: #58a6ff; }
  .amount { color: #3fb950; font-weight: 600; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge.confirmada { background: #1f6feb33; color: #58a6ff; }
  .badge.pendente { background: #d2992233; color: #d29922; }
  .empty { text-align: center; color: #8b949e; padding: 40px; }
</style>
</head>
<body>
  <h1>🟢 BRN — Transferências ao vivo</h1>
  <div class="sub">Atualização automática a cada 3 segundos · <span id="last-update">—</span></div>

  <div class="cards">
    <div class="card"><div class="label">Blocos</div><div class="value" id="stat-blocks">—</div></div>
    <div class="card"><div class="label">Transferências</div><div class="value" id="stat-total">—</div></div>
    <div class="card"><div class="label">Confirmadas</div><div class="value" id="stat-conf">—</div></div>
    <div class="card"><div class="label">Pendentes</div><div class="value" id="stat-pend">—</div></div>
    <div class="card"><div class="label">Volume total</div><div class="value" id="stat-volume">—</div></div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Data / Hora</th>
        <th>Status</th>
        <th>Carteira origem</th>
        <th>Carteira destino</th>
        <th>Valor</th>
        <th>Bloco</th>
      </tr>
    </thead>
    <tbody id="rows">
      <tr><td colspan="6" class="empty">Carregando…</td></tr>
    </tbody>
  </table>

<script>
async function load() {
  try {
    const r = await fetch('/api/transfers');
    const data = await r.json();
    render(data);
  } catch (e) {
    console.error(e);
  }
}

function shortAddr(a) {
  if (!a) return '—';
  return a.length > 22 ? a.slice(0, 10) + '…' + a.slice(-8) : a;
}

function render(data) {
  const t = data.transfers;
  document.getElementById('stat-blocks').textContent = data.stats.blocks;
  document.getElementById('stat-total').textContent  = data.stats.total;
  document.getElementById('stat-conf').textContent   = data.stats.confirmed;
  document.getElementById('stat-pend').textContent   = data.stats.pending;
  document.getElementById('stat-volume').textContent = data.stats.volume.toFixed(2);
  document.getElementById('last-update').textContent = 'atualizado ' + data.stats.now;

  const rows = document.getElementById('rows');
  if (t.length === 0) {
    rows.innerHTML = '<tr><td colspan="6" class="empty">Nenhuma transferência ainda.</td></tr>';
    return;
  }

  rows.innerHTML = t.map(x => `
    <tr>
      <td>${x.datetime}</td>
      <td><span class="badge ${x.status}">${x.status}</span></td>
      <td class="addr" title="${x.from}">${shortAddr(x.from)}</td>
      <td class="addr" title="${x.to}">${shortAddr(x.to)}</td>
      <td class="amount">${x.amount.toFixed(4)} BRN</td>
      <td>${x.block_index !== null ? '#' + x.block_index : '—'}</td>
    </tr>
  `).join('');
}

load();
setInterval(load, 3000);
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/transfers")
def api_transfers():
    transfers = collect_transfers()
    confirmed = [t for t in transfers if t["status"] == "confirmada"]
    pending   = [t for t in transfers if t["status"] == "pendente"]
    volume    = sum(t["amount"] for t in confirmed)

    blocks = 0
    if _blockchain_ref is not None:
        blocks = len(_blockchain_ref.chain)

    return jsonify({
        "transfers": transfers,
        "stats": {
            "blocks": blocks,
            "total": len(transfers),
            "confirmed": len(confirmed),
            "pending": len(pending),
            "volume": volume,
            "now": datetime.now().strftime("%H:%M:%S"),
        }
    })


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ngrok": bool(NGROK_AUTHTOKEN)})


# ------------------------------------------------------------------
# Execução com ngrok
# ------------------------------------------------------------------
def _run_flask():
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_server(blockchain):
    """Inicia Flask + ngrok em threads separadas."""
    set_blockchain(blockchain)

    threading.Thread(target=_run_flask, daemon=True).start()
    print(f"[web] dashboard local em http://{WEB_HOST}:{WEB_PORT}")

    if not USE_NGROK:
        return

    if not NGROK_AUTHTOKEN:
        print("[ngrok] BRN_USE_NGROK=1 mas NGROK_AUTHTOKEN não definido. Pulando túnel.")
        return

    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = NGROK_AUTHTOKEN
        public_url = ngrok.connect(WEB_PORT, "http").public_url
        print(f"[ngrok] dashboard público em: {public_url}")
    except ImportError:
        print("[ngrok] pyngrok não instalado. Rode: pip install pyngrok")
    except Exception as e:
        print(f"[ngrok] erro ao abrir túnel: {e}")


if __name__ == "__main__":
    # Uso isolado (sem nó): cria blockchain vazia só para testar a UI
    from bruno_blockchain_real import Blockchain
    start_web_server(Blockchain())
    while True:
        time.sleep(1)
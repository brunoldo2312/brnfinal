# main.py - Executor unificado do BRN
# Sobe tudo em um unico processo:
#   1. No P2P (CriptoAPI) com descoberta automatica na LAN
#   2. Explorador web Flask (porta 8080)
#   3. Tunel publico Ngrok apontando para o explorador
#   4. Janela da carteira (index.html via pywebview)
import os
import sys
import subprocess
import threading
import time

# Garante que roda a partir da pasta do projeto, nao importa de onde foi chamado
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, Response

from bruno_blockchain_real import (
    CriptoAPI,
    COIN_NAME,
    COIN_SYMBOL,
    BLOCK_REWARD,
    GENESIS_HASH,
)

# ============================================================
# CONFIGURACAO
# ============================================================
NGROK_AUTHTOKEN = os.environ.get(
    "NGROK_AUTHTOKEN",
    "3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4",
)
EXPLORER_PORT = 8080
BRN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6001

# ============================================================
# INSTANCIA UNICA DO NO BRN (compartilhada entre GUI e explorador)
# ============================================================
print(f"[BRN] Iniciando no na porta {BRN_PORT} ...")
api = CriptoAPI(BRN_PORT)

# ============================================================
# EXPLORADOR FLASK
# ============================================================
app = Flask(__name__)

EXPLORER_HTML = r"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<title>Explorador BRN</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  h1 { color:#7cc4ff; margin:0 0 4px; font-size:22px; }
  .sub { color:#8892a6; font-size:13px; margin-bottom:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:24px; }
  .card { background:#171a21; border:1px solid #232834; border-radius:10px; padding:16px; }
  .card .label { font-size:12px; color:#8892a6; text-transform:uppercase; letter-spacing:.5px; }
  .card .value { font-size:24px; font-weight:600; margin-top:6px; color:#fff; }
  .card .value.ok { color:#4ade80; }
  .card .value.warn { color:#fbbf24; }
  table { width:100%; border-collapse:collapse; background:#171a21; border-radius:10px; overflow:hidden; }
  th, td { padding:10px 12px; text-align:left; font-size:13px; border-bottom:1px solid #232834; }
  th { background:#1d2230; color:#a3aec2; font-weight:500; text-transform:uppercase; font-size:11px; letter-spacing:.5px; }
  tr:last-child td { border-bottom:none; }
  tr:hover td { background:#1b2029; }
  .hash { font-family: ui-monospace, Menlo, Consolas, monospace; color:#7cc4ff; font-size:12px; }
  .muted { color:#8892a6; }
  .footer { margin-top:24px; color:#5b6478; font-size:12px; text-align:center; }
</style>
</head>
<body>
  <h1>Explorador <span style="color:#8892a6;font-weight:400">/ BRN</span></h1>
  <div class="sub">Bruno (BRN) — rede local — atualiza a cada 5s</div>

  <div class="grid">
    <div class="card"><div class="label">Altura da cadeia</div><div class="value" id="height">–</div></div>
    <div class="card"><div class="label">Dificuldade atual</div><div class="value" id="diff">–</div></div>
    <div class="card"><div class="label">Peers conectados</div><div class="value" id="peers">–</div></div>
    <div class="card"><div class="label">Mempool</div><div class="value" id="mempool">–</div></div>
    <div class="card"><div class="label">Genesis</div><div class="value" id="genesis" style="font-size:14px">–</div></div>
  </div>

  <h3 style="color:#a3aec2;font-weight:500;font-size:14px;margin:8px 0 10px">Ultimos 20 blocos</h3>
  <table>
    <thead><tr>
      <th>#</th><th>Hash</th><th>Anterior</th><th>Txs</th><th>Diff</th><th>Nonce</th><th>Data/Hora</th>
    </tr></thead>
    <tbody id="blocks"><tr><td colspan="7" class="muted">Carregando…</td></tr></tbody>
  </table>

  <div class="footer">Explorador BRN — <span id="updated">–</span></div>

<script>
async function refresh() {
  try {
    const [chain, peers, mem] = await Promise.all([
      fetch('api/chain').then(r => r.json()),
      fetch('api/peers').then(r => r.json()),
      fetch('api/mempool').then(r => r.json()),
    ]);

    document.getElementById('height').textContent = chain.length;
    document.getElementById('diff').textContent = chain.current_difficulty ?? '–';
    document.getElementById('peers').textContent = peers.count ?? 0;
    document.getElementById('mempool').textContent = mem.count ?? 0;

    const g = document.getElementById('genesis');
    g.textContent = (chain.genesis_ok ? 'OK ' : 'DIVERGENTE ') + (chain.genesis_hash || '').slice(0,12) + '…';
    g.className = 'value ' + (chain.genesis_ok ? 'ok' : 'warn');

    const rows = (chain.chain || []).slice(-20).reverse().map(b => {
      const ts = new Date((b.timestamp || 0) * 1000).toLocaleString('pt-BR');
      const h = (b.hash || '').slice(0,18);
      const ph = (b.previous_hash || '').slice(0,18);
      const txCount = (b.transactions || []).length;
      return `<tr>
        <td><b>${b.index}</b></td>
        <td class="hash">${h}…</td>
        <td class="hash muted">${ph}…</td>
        <td>${txCount}</td>
        <td>${b.difficulty}</td>
        <td>${b.nonce}</td>
        <td class="muted">${ts}</td>
      </tr>`;
    }).join('');
    document.getElementById('blocks').innerHTML = rows ||
      '<tr><td colspan="7" class="muted">Sem blocos.</td></tr>';

    document.getElementById('updated').textContent = new Date().toLocaleTimeString('pt-BR');
  } catch (e) {
    console.error(e);
  }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


@app.route("/")
def explorador_inicio():
    return Response(EXPLORER_HTML, mimetype="text/html")


@app.route("/api/chain")
def explorador_cadeia():
    return jsonify(api.get_full_chain())


@app.route("/api/peers")
def explorador_peers():
    return jsonify(api.get_connected_peers())


@app.route("/api/mempool")
def explorador_mempool():
    return jsonify(api.get_mempool())


@app.route("/api/balance/<address>")
def explorador_saldo(address):
    return jsonify(api.get_balance(address))


@app.route("/api/tx/<address>")
def explorador_tx(address):
    return jsonify(api.get_transaction_history(address))


@app.route("/api/local_ips")
def explorador_ips():
    return jsonify(api.get_local_ips())


def _rodar_explorador():
    # use_reloader=False é obrigatorio: o modo reload do Flask reexecutaria
    # o arquivo e abriria uma segunda instância do nó BRN na porta 6001.
    app.run(host="0.0.0.0", port=EXPLORER_PORT, debug=False, use_reloader=False, threaded=True)


# ============================================================
# NGROK
# ============================================================
def _ngrok_worker():
    try:
        subprocess.run(
            ["ngrok", "config", "add-authtoken", NGROK_AUTHTOKEN],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.Popen(
            ["ngrok", "http", str(EXPLORER_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[ngrok] Tunel aberto para a porta {EXPLORER_PORT}")
        print(f"[ngrok] Painel local: http://127.0.0.1:4040")
    except FileNotFoundError:
        print("[ngrok] Binario 'ngrok' nao encontrado no PATH. Explorador local continua funcionando.")
    except subprocess.CalledProcessError as e:
        print(f"[ngrok] Falha ao configurar authtoken: {e}")
    except Exception as e:
        print(f"[ngrok] Erro ao iniciar tunel: {e}")


def iniciar_ngrok():
    threading.Thread(target=_ngrok_worker, daemon=True).start()


# ============================================================
# PRINCIPAL
# ============================================================
def main():
    # 1. Explorador Flask em thread paralela
    threading.Thread(target=_rodar_explorador, daemon=True).start()
    print(f"[explorador] http://localhost:{EXPLORER_PORT}")
    time.sleep(0.5)

    # 2. Ngrok
    iniciar_ngrok()
    time.sleep(1)

    # 3. Janela da carteira (bloqueia ate voce fechar)
    import webview
    webview.create_window(
        title=f"Carteira Nativa {COIN_NAME} (Porta: {BRN_PORT})",
        url="index.html",
        js_api=api,
        width=740,
        height=800,
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
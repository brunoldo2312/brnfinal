import sqlite3
from flask import Flask, render_template_string, request

app = Flask(__name__)

# ⚠️ ATENÇÃO: O nome do banco pode variar. Ajuste para o nome exato que o seu nó usa.
DB_PATH = "blockchain_node_.db" 

# ⚠️ ATENÇÃO: Substitua 'blocks', 'height', 'hash', 'timestamp' pelos nomes reais das colunas do seu SQLite.
# Se você não sabe os nomes, abra o terminal e rode: sqlite3 blockchain_node_.db ".schema"
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Explorador de Blocos - Moeda Bruno (BRN)</title>
    <style>
        body { background-color: #121212; color: #e0e0e0; font-family: 'Courier New', Courier, monospace; margin: 0; padding: 20px; }
        h1 { color: #00ffcc; text-align: center; }
        .container { max-width: 1000px; margin: 0 auto; }
        .card { background: #1e1e1e; border: 1px solid #333; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
        .card h2 { color: #ffaa00; border-bottom: 1px solid #333; padding-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { text-align: left; padding: 12px; border-bottom: 1px solid #333; word-break: break-all; }
        th { color: #888; text-transform: uppercase; font-size: 0.85em; }
        tr:hover { background-color: #2a2a2a; }
        a { color: #00ffcc; text-decoration: none; }
        a:hover { text-decoration: underline; }
        .hash { color: #00ffcc; font-size: 0.9em; }
        .footer { text-align: center; margin-top: 40px; color: #555; font-size: 0.8em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⛓️ Explorador de Blocos - Moeda Bruno (BRN)</h1>
        
        <div class="card">
            <h2>Últimos Blocos Minerados</h2>
            <table>
                <thead>
                    <tr>
                        <th>Altura (Height)</th>
                        <th>Hash do Bloco</th>
                        <th>Timestamp</th>
                        <th>Transações</th>
                    </tr>
                </thead>
                <tbody>
                    {% for block in blocks %}
                    <tr>
                        <td><a href="/block/{{ block.height }}">{{ block.height }}</a></td>
                        <td class="hash">{{ block.hash[:24] }}...</td>
                        <td>{{ block.timestamp }}</td>
                        <td>{{ block.tx_count }}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" style="text-align:center;">Nenhum bloco encontrado. O nó pode estar offline ou o DB está vazio.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="footer">Moeda Bruno (BRN) - Nó de Exploração Pública</div>
    </div>
</body>
</html>
"""

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    try:
        conn = get_db()
        # Query assumindo que a tabela se chama 'blocks'. Ajuste se for diferente (ex: 'blocos')
        # A coluna 'tx_count' pode não existir. Se não existir, remova-a da query e do HTML.
        query = "SELECT height, hash, timestamp, tx_count FROM blocks ORDER BY height DESC LIMIT 20"
        blocks = conn.execute(query).fetchall()
        conn.close()
    except Exception as e:
        blocks = []
        print(f"Erro ao ler o banco: {e}")
    
    return render_template_string(HTML_TEMPLATE, blocks=blocks)

@app.route('/block/<int:height>')
def block_detail(height):
    # Rota futura para detalhar um bloco específico
    return f"Detalhes do bloco {height} em construção."

if __name__ == '__main__':
    # O explorador rodará na porta 8080. 
    # Não use a mesma porta do nó principal (ex: 6001).
    print("Iniciando Explorador de Blocos na porta 8080...")
    app.run(host='0.0.0.0', port=8080, debug=False)
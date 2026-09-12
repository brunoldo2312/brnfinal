# node.py  (trecho alterado)
import os
from web_server import start_web_server   # <-- NOVO

class Node:
    def __init__(self):
        # ... seu código existente ...
        self.bc = Blockchain()
        self.peers = set(SEED_PEERS)
        # ... resto igual ...

    def start(self):
        # ... seu código existente ...
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.consensus_loop, daemon=True).start()

        # ⬇️ NOVO: inicia o dashboard web + ngrok
        start_web_server(self.bc)

        print(f"[node] escutando em {P2P_HOST}:{P2P_PORT} | rede={NETWORK_ID}")
        while self.running:
            time.sleep(1)
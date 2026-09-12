# node.py
# Nó P2P: escuta UDP, propaga blocos/transações, roda loop PoS
# e inicia o dashboard web (Flask + ngrok) para visualização.

import json
import socket
import threading
import time
import os
import sys
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env (se existir)
load_dotenv()

from bruno_blockchain_real import (
    Blockchain,
    Block,
    NETWORK_ID,
    P2P_HOST,
    P2P_PORT,
    SEED_PEERS,
    TARGET_BLOCK_TIME,
)
from web_server import start_web_server


class Node:
    def __init__(self):
        # ---------- Blockchain ----------
        self.bc = Blockchain()

        # ---------- Rede P2P ----------
        self.peers = set(SEED_PEERS)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((P2P_HOST, P2P_PORT))
        except OSError as e:
            print(f"[node] erro ao abrir UDP {P2P_HOST}:{P2P_PORT} -> {e}")
            sys.exit(1)

        self.running = True

        # ---------- Dashboard web ----------
        # start_web_server é chamado em start() para garantir que a
        # blockchain já esteja pronta.

    # ------------------------------------------------------------------
    # Rede P2P — envio
    # ------------------------------------------------------------------
    def broadcast(self, message: dict):
        """Envia mensagem JSON para todos os peers conhecidos."""
        data = json.dumps(message).encode("utf-8")
        for peer in list(self.peers):
            try:
                host, port = peer.split(":")
                self.sock.sendto(data, (host, int(port)))
            except Exception:
                # Peer inválido → remove
                self.peers.discard(peer)

    # ------------------------------------------------------------------
    # Rede P2P — recebimento
    # ------------------------------------------------------------------
    def listen(self):
        """Loop que recebe pacotes UDP e processa mensagens."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                peer = f"{addr[0]}:{addr[1]}"

                # Descobre novos peers automaticamente
                if peer not in self.peers:
                    self.peers.add(peer)
                    print(f"[p2p] novo peer: {peer}")

                msg = json.loads(data.decode("utf-8"))
                self.handle(msg, peer)

            except json.JSONDecodeError:
                # Pacote inválido → ignora silenciosamente
                continue
            except Exception as e:
                print(f"[p2p] erro no listen: {e}")
                continue

    # ------------------------------------------------------------------
    # Tratamento de mensagens
    # ------------------------------------------------------------------
    def handle(self, msg: dict, peer: str):
        """Processa uma mensagem recebida de um peer."""
        if msg.get("network_id") != NETWORK_ID:
            return  # rede diferente → ignora

        kind = msg.get("type")

        # ----- Transação nova -----
        if kind == "tx":
            tx = msg.get("tx")
            if not tx:
                return
            result = self.bc.add_transaction(tx)
            if result.get("ok"):
                print(f"[p2p] tx aceita de {peer}: {tx.get('amount')} BRN "
                      f"{tx.get('from','')[:10]}… → {tx.get('to','')[:10]}…")
                # Repassa para os outros peers (gossip)
                self.broadcast({
                    "type": "tx",
                    "network_id": NETWORK_ID,
                    "tx": tx,
                })

        # ----- Cadeia nova (bloco produzido por outro nó) -----
        elif kind == "chain":
            chain = msg.get("chain")
            if not chain:
                return
            if self.bc.replace_chain(chain):
                print(f"[p2p] cadeia atualizada via {peer} "
                      f"(altura={len(self.bc.chain)})")
                # Repassa para os outros peers
                self.broadcast({
                    "type": "chain",
                    "network_id": NETWORK_ID,
                    "chain": [b.to_dict() for b in self.bc.chain],
                })

        # ----- Handshake de peer (opcional) -----
        elif kind == "hello":
            print(f"[p2p] hello de {peer} | altura dele={msg.get('height')}")

    # ------------------------------------------------------------------
    # Loop de consenso (produção de blocos)
    # ------------------------------------------------------------------
    def consensus_loop(self):
        """Tenta produzir um bloco a cada TARGET_BLOCK_TIME segundos."""
        while self.running:
            time.sleep(TARGET_BLOCK_TIME)
            try:
                block = self.bc.produce_block()
                if block:
                    print(f"[consenso] bloco #{block.index} produzido "
                          f"por {block.validator[:12]}… "
                          f"({len(block.transactions)} tx)")
                    self.broadcast({
                        "type": "chain",
                        "network_id": NETWORK_ID,
                        "chain": [b.to_dict() for b in self.bc.chain],
                    })
            except Exception as e:
                print(f"[consenso] erro: {e}")

    # ------------------------------------------------------------------
    # Submissão de transação (API interna / CLI)
    # ------------------------------------------------------------------
    def submit_transaction(self, tx: dict):
        """Adiciona uma transação localmente e propaga para a rede."""
        result = self.bc.add_transaction(tx)
        if result.get("ok"):
            self.broadcast({
                "type": "tx",
                "network_id": NETWORK_ID,
                "tx": tx,
            })
        return result

    # ------------------------------------------------------------------
    # Inicialização
    # ------------------------------------------------------------------
    def start(self):
        """Inicia threads de rede, consenso e dashboard web."""
        # Thread de escuta UDP
        threading.Thread(target=self.listen, daemon=True).start()

        # Thread do loop de consenso PoS
        threading.Thread(target=self.consensus_loop, daemon=True).start()

        # Dashboard web + ngrok (o web_server lê NGROK_AUTHTOKEN do ambiente)
        start_web_server(self.bc)

        # Info final no terminal
        print("=" * 60)
        print(f"[node] escutando P2P em {P2P_HOST}:{P2P_PORT}")
        print(f"[node] rede = {NETWORK_ID}")
        print(f"[node] peers iniciais = {len(self.peers)}")
        print(f"[node] altura da cadeia = {len(self.bc.chain)}")
        print("=" * 60)

        # Mantém o processo vivo
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[node] encerrando…")
            self.running = False


# ----------------------------------------------------------------------
# Execução direta
# ----------------------------------------------------------------------
if __name__ == "__main__":
    Node().start()
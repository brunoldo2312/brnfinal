# node.py
# Nó P2P: escuta UDP, propaga blocos/transações, roda loop PoS.

import json
import socket
import threading
import time
import os
from bruno_blockchain_real import Blockchain, Block, NETWORK_ID, P2P_HOST, P2P_PORT, SEED_PEERS, TARGET_BLOCK_TIME


class Node:
    def __init__(self):
        self.bc = Blockchain()
        self.peers = set(SEED_PEERS)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((P2P_HOST, P2P_PORT))
        self.running = True

    # ---------- Rede ----------
    def broadcast(self, message: dict):
        data = json.dumps(message).encode("utf-8")
        for peer in list(self.peers):
            try:
                host, port = peer.split(":")
                self.sock.sendto(data, (host, int(port)))
            except Exception:
                self.peers.discard(peer)

    def listen(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
                peer = f"{addr[0]}:{addr[1]}"
                if peer not in self.peers:
                    self.peers.add(peer)
                msg = json.loads(data.decode("utf-8"))
                self.handle(msg, peer)
            except Exception:
                continue

    def handle(self, msg: dict, peer: str):
        if msg.get("network_id") != NETWORK_ID:
            return
        kind = msg.get("type")
        if kind == "tx":
            self.bc.add_transaction(msg["tx"])
        elif kind == "chain":
            if self.bc.replace_chain(msg["chain"]):
                self.broadcast({"type": "chain", "network_id": NETWORK_ID,
                                "chain": [b.to_dict() for b in self.bc.chain]})

    # ---------- Loop de consenso ----------
    def consensus_loop(self):
        while self.running:
            time.sleep(TARGET_BLOCK_TIME)
            block = self.bc.produce_block()
            if block:
                self.broadcast({
                    "type": "chain",
                    "network_id": NETWORK_ID,
                    "chain": [b.to_dict() for b in self.bc.chain],
                })

    def start(self):
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.consensus_loop, daemon=True).start()
        print(f"[node] escutando em {P2P_HOST}:{P2P_PORT} | rede={NETWORK_ID}")
        while self.running:
            time.sleep(1)


if __name__ == "__main__":
    Node().start()
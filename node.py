# node.py — Nó P2P multi-ativo (RWA).

import json
import socket
import threading
import time
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from bruno_blockchain_real import (
    Blockchain, Block, NETWORK_ID, P2P_HOST, P2P_PORT, SEED_PEERS,
    TARGET_BLOCK_TIME, FAUCET_ADDRESS, GENESIS_ALLOCATIONS,
    REGULATOR_ADDRESS, SlashingEvidence,
)
from cripto_wallet import WalletManager
from web_server import start_web_server, set_faucet_handler

IDENTITY_FILE = os.environ.get("BRN_IDENTITY_FILE", "node_identity.wallet")
FAUCET_KEY_FILE = os.environ.get("BRN_FAUCET_KEY_FILE", "faucet_identity.wallet")
MASTER_PASSWORD = os.environ.get("BRN_MASTER_PASSWORD", "")

if len(MASTER_PASSWORD) < 20:
    print("[ERRO] BRN_MASTER_PASSWORD deve ter >= 20 caracteres.")
    sys.exit(1)


def load_or_create_identity(path_str: str, label: str) -> dict:
    path = Path(path_str)
    if path.suffix != ".wallet":
        path = path.with_suffix(".wallet")
    if path.exists():
        r = WalletManager.load_encrypted_wallet(str(path), MASTER_PASSWORD)
        if r["status"] != "sucesso":
            print(f"[{label}] FALHA: {r['message']}")
            sys.exit(1)
        print(f"[{label}] carregada: {r['address'][:20]}…")
        return {"address": r["address"],
                "spend_secret_key": r["spend_secret_key"],
                "public_key": r["public_key"]}
    data = WalletManager.generate_keypair()
    r = WalletManager.save_encrypted_wallet(
        str(path), MASTER_PASSWORD, data["address"],
        data["spend_secret_key"], data["public_key"])
    if r["status"] != "sucesso":
        print(f"[{label}] FALHA: {r['message']}")
        sys.exit(1)
    print(f"[{label}] nova: {data['address'][:20]}…")
    return data


class Node:
    def __init__(self):
        self.identity = load_or_create_identity(IDENTITY_FILE, "identidade")
        self.faucet_identity = load_or_create_identity(FAUCET_KEY_FILE, "faucet")

        alloc = {a: dict(v) for a, v in GENESIS_ALLOCATIONS.items()}
        alloc.setdefault(self.faucet_identity["address"], {})["BRN"] = \
            alloc.get(self.faucet_identity["address"], {}).get("BRN", 0) + 1_000_000.0
        alloc.setdefault(self.identity["address"], {})["BRN"] = \
            alloc.get(self.identity["address"], {}).get("BRN", 0) + 10_000.0

        import bruno_blockchain_real as bbr
        if not bbr.FAUCET_ADDRESS:
            bbr.FAUCET_ADDRESS = self.faucet_identity["address"]
            os.environ["BRN_FAUCET_ADDRESS"] = self.faucet_identity["address"]

        self.bc = Blockchain(
            node_identity=self.identity,
            genesis_allocations=alloc,
            regulator_address=REGULATOR_ADDRESS,
        )

        self.peers = set(SEED_PEERS)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((P2P_HOST, P2P_PORT))
        except OSError as e:
            print(f"[node] erro UDP {P2P_HOST}:{P2P_PORT} -> {e}")
            sys.exit(1)
        self.running = True

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
                data, addr = self.sock.recvfrom(2 * 1024 * 1024)
                peer = f"{addr[0]}:{addr[1]}"
                if peer not in self.peers:
                    self.peers.add(peer)
                    print(f"[p2p] novo peer: {peer}")
                msg = json.loads(data.decode("utf-8"))
                self.handle(msg, peer)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"[p2p] erro: {e}")
                continue

    def handle(self, msg: dict, peer: str):
        if msg.get("network_id") != NETWORK_ID:
            return
        kind = msg.get("type")

        if kind == "tx":
            tx = msg.get("tx")
            if tx:
                r = self.bc.add_transaction(tx)
                if r.get("ok"):
                    print(f"[p2p] tx {tx['type']}/{tx['asset_id']} "
                          f"{tx['amount']} {tx['from'][:10]}… → {tx['to'][:10]}…")
                    self.broadcast({"type": "tx", "network_id": NETWORK_ID, "tx": tx})

        elif kind == "chain":
            chain = msg.get("chain")
            if chain and self.bc.replace_chain(chain):
                self.broadcast({
                    "type": "chain", "network_id": NETWORK_ID,
                    "chain": [b.to_dict() for b in self.bc.chain]})

        elif kind == "slashing":
            ev_data = msg.get("evidence")
            if ev_data:
                try:
                    ev = SlashingEvidence.from_dict(ev_data)
                    if self.bc.submit_slashing_evidence(ev):
                        self.broadcast({"type": "slashing",
                                        "network_id": NETWORK_ID,
                                        "evidence": ev.to_dict()})
                except Exception as e:
                    print(f"[p2p] evidência inválida: {e}")

        elif kind == "hello":
            print(f"[p2p] hello de {peer} | altura={msg.get('height')}")

    def consensus_loop(self):
        while self.running:
            time.sleep(TARGET_BLOCK_TIME)
            try:
                block = self.bc.produce_block()
                if block:
                    print(f"[consenso] bloco #{block.index} "
                          f"({len(block.transactions)} tx)")
                    self.broadcast({
                        "type": "chain", "network_id": NETWORK_ID,
                        "chain": [b.to_dict() for b in self.bc.chain]})
            except Exception as e:
                print(f"[consenso] erro: {e}")

    def submit_transaction(self, tx: dict) -> dict:
        r = self.bc.add_transaction(tx)
        if r.get("ok"):
            self.broadcast({"type": "tx", "network_id": NETWORK_ID, "tx": tx})
        return r

    def faucet_handler(self, to_address: str) -> dict:
        return self.bc.faucet(
            to_address=to_address,
            private_key_hex=self.faucet_identity["spend_secret_key"],
            public_key_hex=self.faucet_identity["public_key"])

    def report_invalid_block(self, invalid_block_dict: dict, reason: str) -> bool:
        blk = Block.from_dict(invalid_block_dict)
        ev = SlashingEvidence.build(blk, reason,
                                    self.identity["spend_secret_key"],
                                    self.identity["public_key"])
        if not self.bc.submit_slashing_evidence(ev):
            return False
        self.broadcast({"type": "slashing", "network_id": NETWORK_ID,
                        "evidence": ev.to_dict()})
        return True

    def start(self):
        threading.Thread(target=self.listen, daemon=True).start()
        threading.Thread(target=self.consensus_loop, daemon=True).start()
        set_faucet_handler(self.faucet_handler)
        start_web_server(self.bc)

        print("=" * 60)
        print(f"[node] P2P      : {P2P_HOST}:{P2P_PORT}")
        print(f"[node] rede     : {NETWORK_ID}")
        print(f"[node] DB       : {self.bc.db_path}")
        print(f"[node] altura   : {len(self.bc.chain)}")
        print(f"[node] wallet   : {self.identity['address'][:20]}…")
        print(f"[node] ativos   : {list(self.bc.registry.assets.keys())}")
        print("=" * 60)

        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[node] encerrando…")
            self.running = False


if __name__ == "__main__":
    Node().start()

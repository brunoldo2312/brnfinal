# bruno_blockchain_real.py
# Blockchain com:
#   - SHA3-256 para hashes
#   - Blocos assinados por ECDSA (validação por outros nós)
#   - Persistência em SQLite (sobrevive a reinicializações)
#   - Validação completa de transações (saldo, nonce, assinatura)
#   - Consenso PoS simplificado

import hashlib
import json
import time
import secrets
import threading
import sqlite3
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

from cripto_wallet import WalletManager

NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-mainnet-1")
BLOCK_REWARD = 50.0
MIN_STAKE = 100.0
DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")


# ------------------------------------------------------------------
# Bloco (agora assinado pelo validador)
# ------------------------------------------------------------------
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    validator: str
    validator_public_key: str = ""
    nonce: int = 0
    signature: str = ""
    hash: str = ""

    def calculate_hash(self) -> str:
        body = {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "validator": self.validator,
            "validator_public_key": self.validator_public_key,
            "nonce": self.nonce,
            "network_id": NETWORK_ID,
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def finalize(self):
        self.hash = self.calculate_hash()

    def sign(self, private_key_hex: str):
        """Assina o hash do bloco com a chave privada do validador."""
        if not self.hash:
            self.finalize()
        payload = {"block_hash": self.hash, "index": self.index}
        self.signature = WalletManager.sign_transaction(private_key_hex, payload)

    def verify(self) -> bool:
        """Verifica integridade e assinatura do bloco."""
        if self.hash != self.calculate_hash():
            return False
        if self.index == 0:
            return True  # gênese é confiada
        if not self.validator_public_key or not self.signature:
            return False
        derived = WalletManager.address_from_public_key(self.validator_public_key)
        if derived != self.validator:
            return False
        payload = {"block_hash": self.hash, "index": self.index}
        return WalletManager.verify_signature(
            self.validator_public_key, payload, self.signature
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(**d)


# ------------------------------------------------------------------
# Transações
# ------------------------------------------------------------------
class Transaction:
    REQUIRED_FIELDS = {"from", "to", "amount", "nonce", "public_key", "signature"}

    @staticmethod
    def build(sender_address, receiver_address, amount, nonce,
              private_key_hex, public_key_hex) -> dict:
        if amount <= 0:
            raise ValueError("Valor deve ser positivo.")
        if sender_address == receiver_address:
            raise ValueError("Remetente e destinatário iguais.")
        tx = {
            "from": sender_address,
            "to": receiver_address,
            "amount": float(amount),
            "nonce": int(nonce),
            "public_key": public_key_hex,
            "timestamp": time.time(),
        }
        tx["signature"] = WalletManager.sign_transaction(private_key_hex, tx)
        return tx

    @staticmethod
    def verify_signature(tx: dict) -> bool:
        if not Transaction.REQUIRED_FIELDS.issubset(tx.keys()):
            return False
        payload = {k: v for k, v in tx.items() if k != "signature"}
        return WalletManager.verify_signature(tx["public_key"], payload, tx["signature"])

    @staticmethod
    def derived_address(tx: dict) -> str:
        return WalletManager.address_from_public_key(tx["public_key"])


# ------------------------------------------------------------------
# Estado (saldos, nonces)
# ------------------------------------------------------------------
class State:
    def __init__(self):
        self.balances: Dict[str, float] = {}
        self.nonces: Dict[str, int] = {}

    def balance(self, addr: str) -> float:
        return self.balances.get(addr, 0.0)

    def nonce(self, addr: str) -> int:
        return self.nonces.get(addr, 0)

    def credit(self, addr: str, amount: float):
        self.balances[addr] = self.balances.get(addr, 0.0) + amount

    def debit(self, addr: str, amount: float):
        self.balances[addr] = self.balances.get(addr, 0.0) - amount

    def apply_transaction(self, tx: dict) -> bool:
        sender = tx["from"]
        if self.balance(sender) < tx["amount"]:
            return False
        if self.nonce(sender) != tx["nonce"]:
            return False
        self.debit(sender, tx["amount"])
        self.credit(tx["to"], tx["amount"])
        self.nonces[sender] = self.nonce(sender) + 1
        return True


# ------------------------------------------------------------------
# Blockchain (com persistência SQLite e validação por bloco)
# ------------------------------------------------------------------
class Blockchain:
    def __init__(self, db_path: str = DB_PATH, node_identity: Optional[dict] = None):
        self.db_path = db_path
        self.node_identity = node_identity
        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.lock = threading.RLock()

        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            print("[chain] gênese criada e persistida.")
        else:
            print(f"[chain] {len(self.chain)} blocos carregados de {self.db_path}")

    # ---------- SQLite ----------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    idx       INTEGER PRIMARY KEY,
                    hash      TEXT NOT NULL,
                    validator TEXT,
                    timestamp REAL,
                    data      TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(hash)")
            conn.commit()

    def _persist_block(self, block: Block):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO blocks (idx, hash, validator, timestamp, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (block.index, block.hash, block.validator,
                 block.timestamp, json.dumps(block.to_dict())),
            )
            conn.commit()

    def _replace_all_in_db(self, blocks: List[Block]):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM blocks")
            for blk in blocks:
                conn.execute(
                    "INSERT INTO blocks (idx, hash, validator, timestamp, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (blk.index, blk.hash, blk.validator,
                     blk.timestamp, json.dumps(blk.to_dict())),
                )
            conn.commit()

    def _load_from_db(self) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT data FROM blocks ORDER BY idx ASC"
                ).fetchall()
        except sqlite3.Error as e:
            print(f"[chain] erro lendo DB: {e}")
            return False

        if not rows:
            return False

        self.chain = [Block.from_dict(json.loads(r[0])) for r in rows]

        if not self._verify_chain_structure(self.chain):
            print("[chain] cadeia em disco INVÁLIDA — recriando gênese.")
            self.chain = []
            self._replace_all_in_db([])
            return False

        self._rebuild_state()
        return True

    def _rebuild_state(self):
        self.state = State()
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self.state.apply_transaction(tx)
            self.state.credit(blk.validator, BLOCK_REWARD)

    # ---------- Gênese ----------
    def _create_genesis(self):
        genesis = Block(
            index=0,
            timestamp=time.time(),
            previous_hash="0" * 64,
            transactions=[],
            validator="genesis",
        )
        genesis.finalize()
        self.chain.append(genesis)

    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    # ---------- Mempool ----------
    def add_transaction(self, tx: dict) -> dict:
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura inválida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereço não corresponde à chave pública."}
            if self.state.balance(tx["from"]) < tx["amount"]:
                return {"ok": False, "msg": "Saldo insuficiente."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False, "msg": "Nonce inválido (double-spend)."}
            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Duplicada na mempool."}
            self.pending.append(tx)
            return {"ok": True, "msg": "Transação aceita."}

    # ---------- Seleção do validador (PoS por peso) ----------
    def _select_validator(self) -> Optional[str]:
        eligible = {a: b for a, b in self.state.balances.items() if b >= MIN_STAKE}
        if not eligible:
            # fallback: permite que o próprio nó produza (bootstrap)
            if self.node_identity:
                return self.node_identity["address"]
            return None
        total = sum(eligible.values())
        pick = secrets.randbelow(max(1, int(total * 1_000_000))) / 1_000_000
        acc = 0.0
        for addr, weight in eligible.items():
            acc += weight
            if pick <= acc:
                return addr
        return list(eligible.keys())[-1]

    # ---------- Produção de bloco ----------
    def produce_block(self) -> Optional[Block]:
        with self.lock:
            if not self.node_identity:
                return None

            validator = self._select_validator()

            # Só produz se fui escolhido (ou bootstrap com fallback acima)
            if validator != self.node_identity["address"]:
                return None

            # Aplica transações sobre estado temporário
            temp = State()
            temp.balances = dict(self.state.balances)
            temp.nonces = dict(self.state.nonces)

            chosen = []
            for tx in sorted(self.pending, key=lambda t: (t["from"], t["nonce"])):
                if Transaction.verify_signature(tx) and temp.apply_transaction(tx):
                    chosen.append(tx)

            block = Block(
                index=self.last_block.index + 1,
                timestamp=time.time(),
                previous_hash=self.last_block.hash,
                transactions=chosen,
                validator=self.node_identity["address"],
                validator_public_key=self.node_identity["public_key"],
            )
            block.finalize()
            block.sign(self.node_identity["spend_secret_key"])

            if not block.verify():
                print("[consenso] bloco produzido NÃO passou na própria verificação.")
                return None

            # Aplica de verdade
            for tx in chosen:
                self.state.apply_transaction(tx)
            self.state.credit(block.validator, BLOCK_REWARD)

            self.pending = [t for t in self.pending if t not in chosen]
            self.chain.append(block)
            self._persist_block(block)

            return block

    # ---------- Verificação da cadeia completa ----------
    def _verify_chain_structure(self, chain: List[Block]) -> bool:
        if not chain:
            return False
        for i, blk in enumerate(chain):
            if not blk.verify():
                print(f"[chain] bloco #{blk.index} com assinatura/hash inválido.")
                return False
            if i == 0:
                continue
            prev = chain[i - 1]
            if blk.previous_hash != prev.hash:
                print(f"[chain] bloco #{blk.index} com previous_hash divergente.")
                return False
            if blk.index != prev.index + 1:
                print(f"[chain] índice fora de sequência em #{blk.index}.")
                return False
        return True

    # ---------- Substituir cadeia (com validação por bloco) ----------
    def replace_chain(self, new_chain: List[dict]) -> bool:
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception as e:
            print(f"[chain] erro ao desserializar cadeia recebida: {e}")
            return False

        with self.lock:
            if len(blocks) <= len(self.chain):
                return False
            if not self._verify_chain_structure(blocks):
                print("[chain] cadeia recebida rejeitada: assinatura/hash inválido.")
                return False

            # reconstrói estado (determinístico)
            new_state = State()
            for blk in blocks[1:]:
                for tx in blk.transactions:
                    if not Transaction.verify_signature(tx):
                        print(f"[chain] tx inválida no bloco #{blk.index}")
                        return False
                    if not new_state.apply_transaction(tx):
                        print(f"[chain] tx rejeitada no bloco #{blk.index}")
                        return False
                new_state.credit(blk.validator, BLOCK_REWARD)

            self.chain = blocks
            self.state = new_state
            self.pending = []
            self._replace_all_in_db(blocks)
            print(f"[chain] cadeia substituída → altura {len(blocks)}")
            return True

    # ---------- Serialização ----------
    def to_dict(self) -> dict:
        return {
            "network_id": NETWORK_ID,
            "length": len(self.chain),
            "chain": [b.to_dict() for b in self.chain],
        }
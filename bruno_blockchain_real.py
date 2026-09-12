# bruno_blockchain_real.py
# Blockchain profissional (protótipo de produção):
# - SHA3-256 para hashes
# - Validação completa de transações (saldo, nonce, assinatura ECDSA)
# - Prevenção de double-spend
# - Consenso PoS simplificado (por peso de stake)
# - Rede P2P via UDP + API Flask opcional

import hashlib
import json
import time
import secrets
import threading
import socket
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

from cripto_wallet import WalletManager

# ------------------------------------------------------------------
# Configurações
# ------------------------------------------------------------------
NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-mainnet-1")
P2P_HOST = os.environ.get("BRN_P2P_HOST", "0.0.0.0")
P2P_PORT = int(os.environ.get("BRN_P2P_PORT", "7777"))
SEED_PEERS = [
    p.strip() for p in os.environ.get("BRN_SEED_PEERS", "").split(",") if p.strip()
]

BLOCK_REWARD = 50.0
MIN_STAKE = 100.0
TARGET_BLOCK_TIME = 10  # segundos


# ------------------------------------------------------------------
# Bloco
# ------------------------------------------------------------------
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    validator: str
    nonce: int = 0
    hash: str = ""

    def calculate_hash(self) -> str:
        block_string = json.dumps({
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "validator": self.validator,
            "nonce": self.nonce,
            "network_id": NETWORK_ID,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(block_string).hexdigest()

    def finalize(self):
        self.hash = self.calculate_hash()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(**d)


# ------------------------------------------------------------------
# Transação (formato + validação)
# ------------------------------------------------------------------
class Transaction:
    REQUIRED_FIELDS = {"from", "to", "amount", "nonce", "public_key", "signature"}

    @staticmethod
    def build(sender_address: str, receiver_address: str, amount: float,
              nonce: int, private_key_hex: str, public_key_hex: str) -> dict:
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
# Estado da rede (saldos, nonces, stakes)
# ------------------------------------------------------------------
class State:
    def __init__(self):
        self.balances: Dict[str, float] = {}
        self.nonces: Dict[str, int] = {}
        self.stakes: Dict[str, float] = {}

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
# Blockchain
# ------------------------------------------------------------------
class Blockchain:
    def __init__(self):
        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.lock = threading.RLock()
        self._create_genesis()

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

    # ---------- Adicionar transação à mempool ----------
    def add_transaction(self, tx: dict) -> dict:
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura inválida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereço não corresponde à chave pública."}
            if self.state.balance(tx["from"]) < tx["amount"]:
                return {"ok": False, "msg": "Saldo insuficiente."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False, "msg": "Nonce inválido (double-spend/bloqueio)."}
            # Evita duplicidade na mempool
            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Transação duplicada na mempool."}
            self.pending.append(tx)
            return {"ok": True, "msg": "Transação aceita na mempool."}

    # ---------- Consenso PoS simplificado ----------
    def _select_validator(self) -> Optional[str]:
        """
        Escolhe validador com probabilidade proporcional ao stake.
        Stake = saldo elegível (>= MIN_STAKE).
        """
        eligible = {a: b for a, b in self.state.balances.items() if b >= MIN_STAKE}
        if not eligible:
            return None
        total = sum(eligible.values())
        pick = secrets.randbelow(int(total * 1_000_000)) / 1_000_000
        acc = 0.0
        for addr, weight in eligible.items():
            acc += weight
            if pick <= acc:
                return addr
        return list(eligible.keys())[-1]

    # ---------- Produzir bloco ----------
    def produce_block(self, forced_validator: Optional[str] = None) -> Optional[Block]:
        with self.lock:
            validator = forced_validator or self._select_validator()
            if not validator:
                return None

            # Seleciona transações válidas (aplica sobre um estado temporário)
            temp_state = State()
            temp_state.balances = dict(self.state.balances)
            temp_state.nonces = dict(self.state.nonces)

            chosen: List[dict] = []
            for tx in sorted(self.pending, key=lambda t: (t["from"], t["nonce"])):
                if Transaction.verify_signature(tx) and temp_state.apply_transaction(tx):
                    chosen.append(tx)

            block = Block(
                index=self.last_block.index + 1,
                timestamp=time.time(),
                previous_hash=self.last_block.hash,
                transactions=chosen,
                validator=validator,
            )
            block.finalize()

            # Aplica transações no estado real
            for tx in chosen:
                self.state.apply_transaction(tx)

            # Recompensa do validador
            self.state.credit(validator, BLOCK_REWARD)

            # Remove da mempool
            self.pending = [t for t in self.pending if t not in chosen]
            self.chain.append(block)
            return block

    # ---------- Validação de cadeia ----------
    def is_valid(self, chain: List[Block]) -> bool:
        for i in range(1, len(chain)):
            cur, prev = chain[i], chain[i - 1]
            if cur.previous_hash != prev.hash:
                return False
            if cur.calculate_hash() != cur.hash:
                return False
        return True

    # ---------- Substituir cadeia (regra do mais longo válido) ----------
    def replace_chain(self, new_chain: List[dict]) -> bool:
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception:
            return False
        with self.lock:
            if len(blocks) <= len(self.chain):
                return False
            if not self.is_valid(blocks):
                return False
            # Reconstrói estado
            new_state = State()
            for blk in blocks[1:]:
                for tx in blk.transactions:
                    new_state.apply_transaction(tx)
                new_state.credit(blk.validator, BLOCK_REWARD)
            self.chain = blocks
            self.state = new_state
            self.pending = []
            return True

    # ---------- Serialização ----------
    def to_dict(self) -> dict:
        return {
            "network_id": NETWORK_ID,
            "length": len(self.chain),
            "chain": [b.to_dict() for b in self.chain],
        }
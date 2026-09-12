# bruno_blockchain_real.py
# Blockchain profissional: SHA-3, PoS simplificado, validação UTXO,
# assinaturas ECDSA, anti-double-spend, P2P com peers confiáveis.
import hashlib
import json
import os
import time
import threading
import secrets
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

from cripto_wallet import WalletManager

NETWORK_ID = "brn-mainnet-v1"
GENESIS_PREV_HASH = "0" * 64
MIN_STAKE = 100.0
BASE_REWARD = 1.0
BLOCK_TIME_TARGET = 15.0   # segundos
DATA_DIR = Path.cwd() / "blockchain_data"
DATA_DIR.mkdir(exist_ok=True)
CHAIN_FILE = DATA_DIR / "chain.json"
MEMPOOL_FILE = DATA_DIR / "mempool.json"
STATE_FILE = DATA_DIR / "state.json"


# ------------------------------------------------------------------
# Transação
# ------------------------------------------------------------------
@dataclass
class Transaction:
    sender_pubkey: str
    sender_address: str
    recipient: str
    amount: float
    nonce: int
    timestamp: float
    signature: str = ""

    def to_signable(self) -> dict:
        return {
            "sender_pubkey": self.sender_pubkey,
            "sender_address": self.sender_address,
            "recipient": self.recipient,
            "amount": round(self.amount, 8),
            "nonce": self.nonce,
            "timestamp": self.timestamp,
        }

    def hash(self) -> str:
        payload = json.dumps(self.to_signable(), sort_keys=True,
                             separators=(",", ":")).encode()
        return hashlib.sha3_256(payload).hexdigest()

    def verify(self) -> bool:
        if self.amount <= 0:
            return False
        if not self.signature:
            return False
        try:
            derived = WalletManager.address_from_public_key(self.sender_pubkey)
            if derived != self.sender_address:
                return False
        except Exception:
            return False
        return WalletManager.verify_signature(
            self.sender_pubkey, self.to_signable(), self.signature
        )


# ------------------------------------------------------------------
# Bloco
# ------------------------------------------------------------------
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    validator: str                 # endereço do validador (PoS)
    stake: float                   # stake do validador
    merkle_root: str = ""
    hash: str = ""
    signature: str = ""            # assinatura do validador

    def compute_merkle_root(self) -> str:
        if not self.transactions:
            return hashlib.sha3_256(b"").hexdigest()
        layer = [hashlib.sha3_256(json.dumps(tx, sort_keys=True).encode()).hexdigest()
                 for tx in self.transactions]
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            layer = [hashlib.sha3_256((layer[i] + layer[i + 1]).encode()).hexdigest()
                     for i in range(0, len(layer), 2)]
        return layer[0]

    def compute_hash(self) -> str:
        self.merkle_root = self.compute_merkle_root()
        header = {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "validator": self.validator,
            "stake": round(self.stake, 8),
            "network_id": NETWORK_ID,
        }
        return hashlib.sha3_256(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def finalize(self):
        self.hash = self.compute_hash()


# ------------------------------------------------------------------
# Estado da rede (saldos + nonces + stakes)
# ------------------------------------------------------------------
class ChainState:
    def __init__(self):
        self.balances: Dict[str, float] = {}
        self.nonces: Dict[str, int] = {}
        self.stakes: Dict[str, float] = {}
        self.lock = threading.RLock()

    def balance_of(self, addr: str) -> float:
        return float(self.balances.get(addr, 0.0))

    def nonce_of(self, addr: str) -> int:
        return int(self.nonces.get(addr, 0))

    def stake_of(self, addr: str) -> float:
        return float(self.stakes.get(addr, 0.0))

    def apply_tx(self, tx: dict) -> bool:
        sender = tx["sender_address"]
        recipient = tx["recipient"]
        amount = float(tx["amount"])
        nonce = int(tx["nonce"])

        if self.balance_of(sender) < amount:
            return False
        if self.nonce_of(sender) != nonce:
            return False

        self.balances[sender] = self.balance_of(sender) - amount
        self.balances[recipient] = self.balance_of(recipient) + amount
        self.nonces[sender] = nonce + 1
        return True

    def to_dict(self) -> dict:
        return {
            "balances": self.balances,
            "nonces": self.nonces,
            "stakes": self.stakes,
        }

    def load_dict(self, data: dict):
        self.balances = data.get("balances", {})
        self.nonces = data.get("nonces", {})
        self.stakes = data.get("stakes", {})


# ------------------------------------------------------------------
# Blockchain
# ------------------------------------------------------------------
class Blockchain:
    def __init__(self):
        self.chain: List[Block] = []
        self.mempool: List[Transaction] = []
        self.state = ChainState()
        self.lock = threading.RLock()
        self.load()

    # -------- Persistência --------
    def load(self):
        if CHAIN_FILE.exists():
            try:
                with open(CHAIN_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.chain = [Block(**b) for b in raw]
                if STATE_FILE.exists():
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        self.state.load_dict(json.load(f))
                if MEMPOOL_FILE.exists():
                    with open(MEMPOOL_FILE, "r", encoding="utf-8") as f:
                        self.mempool = [Transaction(**t) for t in json.load(f)]
                return
            except Exception:
                pass
        self.chain = [self._create_genesis()]
        self.save()

    def save(self):
        with open(CHAIN_FILE, "w", encoding="utf-8") as f:
            json.dump([b.to_dict() for b in self.chain], f, indent=2)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f, indent=2)
        with open(MEMPOOL_FILE, "w", encoding="utf-8") as f:
            json.dump([t.__dict__ for t in self.mempool], f, indent=2)

    # -------- Gênese --------
    def _create_genesis(self) -> Block:
        genesis = Block(
            index=0,
            timestamp=time.time(),
            previous_hash=GENESIS_PREV_HASH,
            transactions=[],
            validator="genesis",
            stake=0.0,
        )
        genesis.finalize()
        return genesis

    # -------- Consulta --------
    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    def get_balance(self, addr: str) -> float:
        return self.state.balance_of(addr)

    def get_nonce(self, addr: str) -> int:
        return self.state.nonce_of(addr)

    # -------- Transações --------
    def add_transaction(self, tx: Transaction) -> dict:
        with self.lock:
            if not tx.verify():
                return {"ok": False, "error": "assinatura_invalida"}

            if self.state.balance_of(tx.sender_address) < tx.amount:
                return {"ok": False, "error": "saldo_insuficiente"}

            if self.state.nonce_of(tx.sender_address) != tx.nonce:
                return {"ok": False, "error": "nonce_invalido"}

            # Anti double-spend no mempool
            for pending in self.mempool:
                if (pending.sender_address == tx.sender_address
                        and pending.nonce == tx.nonce):
                    return {"ok": False, "error": "nonce_duplicado_mempool"}

            self.mempool.append(tx)
            self.save()
            return {"ok": True, "hash": tx.hash()}

    # -------- Seleção de validador (PoS) --------
    def _select_validator(self) -> Optional[tuple]:
        stakes = {addr: amt for addr, amt in self.state.stakes.items()
                  if amt >= MIN_STAKE}
        if not stakes:
            return None
        total = sum(stakes.values())
        r = secrets.randbelow(int(total * 1_000_000)) / 1_000_000
        accum = 0.0
        for addr, amt in stakes.items():
            accum += amt / total
            if r <= accum:
                return addr, amt
        addr = list(stakes.keys())[-1]
        return addr, stakes[addr]

    # -------- Mineração (validação de bloco) --------
    def mine_block(self, validator_secret: str, validator_pubkey: str) -> dict:
        with self.lock:
            addr = WalletManager.address_from_public_key(validator_pubkey)
            selected = self._select_validator()
            if selected is None or selected[0] != addr:
                return {"ok": False, "error": "validador_nao_selecionado",
                        "message": "Aguarde seu turno (PoS) ou aumente o stake."}

            # Aplica transações do mempool em ordem de nonce
            valid_txs = []
            local_state = ChainState()
            local_state.load_dict(self.state.to_dict())

            for tx in sorted(self.mempool, key=lambda t: (t.sender_address, t.nonce)):
                if local_state.apply_tx(tx.to_signable()):
                    valid_txs.append(tx)

            if not valid_txs:
                return {"ok": False, "error": "mempool_vazio"}

            block = Block(
                index=self.last_block.index + 1,
                timestamp=time.time(),
                previous_hash=self.last_block.hash,
                transactions=[t.__dict__ for t in valid_txs],
                validator=addr,
                stake=selected[1],
            )
            block.finalize()

            # Assinatura do validador sobre o hash do bloco
            block.signature = WalletManager.sign_transaction(
                validator_secret,
                {"block_hash": block.hash, "validator": addr}
            )

            # Commit
            self.chain.append(block)
            for tx in valid_txs:
                self.state.apply_tx(tx.to_signable())
            # Recompensa
            self.state.balances[addr] = self.state.balance_of(addr) + BASE_REWARD
            # Remove do mempool
            committed_hashes = {t.hash() for t in valid_txs}
            self.mempool = [t for t in self.mempool if t.hash() not in committed_hashes]

            self.save()
            return {"ok": True, "block": block.to_dict()}

    # -------- Validação de cadeia --------
    def is_valid_chain(self, chain: Optional[List[Block]] = None) -> bool:
        chain = chain or self.chain
        for i in range(1, len(chain)):
            cur, prev = chain[i], chain[i - 1]
            if cur.previous_hash != prev.hash:
                return False
            if cur.compute_hash() != cur.hash:
                return False
        return True

    # -------- Adicionar bloco externo --------
    def add_external_block(self, block_dict: dict) -> dict:
        with self.lock:
            try:
                block = Block(**block_dict)
            except Exception as e:
                return {"ok": False, "error": f"bloco_invalido: {e}"}

            if block.previous_hash != self.last_block.hash:
                return {"ok": False, "error": "previous_hash_desconhecido"}
            if block.compute_hash() != block.hash:
                return {"ok": False, "error": "hash_invalido"}

            # Aplica transações
            local = ChainState()
            local.load_dict(self.state.to_dict())
            for tx in block.transactions:
                if not local.apply_tx(tx):
                    return {"ok": False, "error": "tx_invalida_no_bloco"}

            self.chain.append(block)
            for tx in block.transactions:
                self.state.apply_tx(tx)
            self.state.balances[block.validator] = (
                self.state.balance_of(block.validator) + BASE_REWARD
            )
            self.save()
            return {"ok": True}

    # -------- Utilidade --------
    def to_summary(self) -> dict:
        return {
            "height": self.last_block.index,
            "last_hash": self.last_block.hash,
            "mempool_size": len(self.mempool),
            "peers_validators": len(self.state.stakes),
        }
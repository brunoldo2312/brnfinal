# bruno_blockchain_real.py
# Blockchain multi-ativo para tokenização de ativos financeiros (RWA).
#   - SHA3-256, blocos assinados por ECDSA
#   - Persistência SQLite (cadeia, mempool, slashing, faucet, ativos)
#   - Multi-ativo: BRN + tokens RWA (equity, bond, real_estate, …)
#   - KYC/AML e transferências restritas
#   - Emissão, resgate e congelamento por emissor
#   - Dividendos on-chain por snapshot de holders
#   - Slashing com evidência assinada
#   - Finality gadget (Casper FFG simplificado)

import hashlib
import json
import time
import secrets
import threading
import sqlite3
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set, Tuple

from cripto_wallet import WalletManager
from assets import (AssetRegistry, AssetDefinition, ComplianceRecord,
                    TransferRule, ASSET_TYPES, KYC_STATUS, KYC_LEVELS)

# ---------------------------------------------------------------- env
NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-rwa-1")
P2P_HOST = os.environ.get("BRN_P2P_HOST", "0.0.0.0")
P2P_PORT = int(os.environ.get("BRN_P2P_PORT", "7777"))
SEED_PEERS = [p.strip() for p in os.environ.get("BRN_SEED_PEERS", "").split(",") if p.strip()]
TARGET_BLOCK_TIME = int(os.environ.get("BRN_TARGET_BLOCK_TIME", "10"))

BLOCK_REWARD = float(os.environ.get("BRN_BLOCK_REWARD", "1"))
MIN_STAKE = float(os.environ.get("BRN_MIN_STAKE", "100"))
DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")

FINALITY_INTERVAL = int(os.environ.get("BRN_FINALITY_INTERVAL", "5"))
FINALITY_THRESHOLD = float(os.environ.get("BRN_FINALITY_THRESHOLD", "0.67"))

FAUCET_ADDRESS = os.environ.get("BRN_FAUCET_ADDRESS", "").strip()
FAUCET_AMOUNT = float(os.environ.get("BRN_FAUCET_AMOUNT", "100"))
FAUCET_COOLDOWN = int(os.environ.get("BRN_FAUCET_COOLDOWN", "3600"))

REGULATOR_ADDRESS = os.environ.get("BRN_REGULATOR_ADDRESS", "").strip()

# Formato novo: endereco:ativo:quantidade,endereco:ativo:quantidade
_raw_alloc = os.environ.get("BRN_GENESIS_ALLOC", "").strip()
GENESIS_ALLOCATIONS: Dict[str, Dict[str, float]] = {}   # addr -> {asset: amt}
if _raw_alloc:
    for pair in _raw_alloc.split(","):
        parts = pair.split(":")
        if len(parts) == 3:
            addr, asset, amt = parts
            try:
                GENESIS_ALLOCATIONS.setdefault(addr.strip(), {})[asset.strip()] = float(amt.strip())
            except ValueError:
                pass

NATIVE_ASSET = "BRN"   # ativo nativo (usado para stake, fees, recompensa)


# ==================================================================
# Bloco
# ==================================================================
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
    # Snapshot opcional do registry (só em blocos de asset_create/kyc)
    registry_snapshot: Optional[dict] = None

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
            "registry_hash": self._registry_hash(),
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def _registry_hash(self) -> str:
        if not self.registry_snapshot:
            return ""
        raw = json.dumps(self.registry_snapshot, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()

    def finalize(self):
        self.hash = self.calculate_hash()

    def sign(self, private_key_hex: str):
        if not self.hash:
            self.finalize()
        payload = {"block_hash": self.hash, "index": self.index}
        self.signature = WalletManager.sign_transaction(private_key_hex, payload)

    def verify(self) -> bool:
        if self.hash != self.calculate_hash():
            return False
        if self.index == 0:
            return True
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


# ==================================================================
# Transações multi-ativo
# ==================================================================
class Transaction:
    REQUIRED_FIELDS = {"type", "asset_id", "from", "to", "amount",
                       "nonce", "public_key", "signature"}

    VALID_TYPES = {"transfer", "issue", "redeem", "freeze", "unfreeze",
                   "kyc_register", "kyc_revoke", "asset_create",
                   "asset_update", "dividend"}

    @staticmethod
    def build(tx_type: str, asset_id: str, sender_address: str,
              receiver_address: str, amount: float, nonce: int,
              private_key_hex: str, public_key_hex: str,
              metadata: Optional[dict] = None) -> dict:
        if tx_type not in Transaction.VALID_TYPES:
            raise ValueError(f"Tipo de transação inválido: {tx_type}")
        if amount < 0:
            raise ValueError("Valor não pode ser negativo.")
        if tx_type == "transfer" and sender_address == receiver_address:
            raise ValueError("Remetente e destinatário iguais.")

        tx = {
            "type": tx_type,
            "asset_id": asset_id,
            "from": sender_address,
            "to": receiver_address,
            "amount": float(amount),
            "nonce": int(nonce),
            "public_key": public_key_hex,
            "timestamp": time.time(),
            "metadata": metadata or {},
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

    @staticmethod
    def hash(tx: dict) -> str:
        raw = json.dumps(tx, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()


# ==================================================================
# Estado multi-ativo
# ==================================================================
class State:
    """
    balances[addr][asset_id] = float
    nonces[addr] = int
    frozen[addr][asset_id] = float
    """

    def __init__(self):
        self.balances: Dict[str, Dict[str, float]] = {}
        self.nonces: Dict[str, int] = {}
        self.frozen: Dict[str, Dict[str, float]] = {}
        self.total_supply: Dict[str, float] = {}   # por asset_id

    def balance(self, addr: str, asset_id: str = NATIVE_ASSET) -> float:
        return self.balances.get(addr, {}).get(asset_id, 0.0)

    def available(self, addr: str, asset_id: str) -> float:
        return self.balance(addr, asset_id) - self.frozen.get(addr, {}).get(asset_id, 0.0)

    def all_balances(self, addr: str) -> Dict[str, float]:
        return dict(self.balances.get(addr, {}))

    def nonce(self, addr: str) -> int:
        return self.nonces.get(addr, 0)

    def credit(self, addr: str, asset_id: str, amount: float):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) + amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) + amount

    def debit(self, addr: str, asset_id: str, amount: float):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) - amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) - amount

    def freeze(self, addr: str, asset_id: str, amount: float):
        self.frozen.setdefault(addr, {})
        self.frozen[addr][asset_id] = self.frozen[addr].get(asset_id, 0.0) + amount

    def unfreeze(self, addr: str, asset_id: str, amount: float):
        self.frozen.setdefault(addr, {})
        cur = self.frozen[addr].get(asset_id, 0.0)
        self.frozen[addr][asset_id] = max(0.0, cur - amount)

    def copy(self) -> "State":
        s = State()
        s.balances = {a: dict(v) for a, v in self.balances.items()}
        s.nonces = dict(self.nonces)
        s.frozen = {a: dict(v) for a, v in self.frozen.items()}
        s.total_supply = dict(self.total_supply)
        return s


# ==================================================================
# Finality
# ==================================================================
class Finality:
    def __init__(self, interval: int = FINALITY_INTERVAL,
                 threshold: float = FINALITY_THRESHOLD):
        self.interval = interval
        self.threshold = threshold
        self.finalized_height: int = -1
        self.votes: Dict[int, Set[str]] = {}

    def is_checkpoint(self, height: int) -> bool:
        return height > 0 and height % self.interval == 0

    def vote(self, height: int, validator: str) -> bool:
        if not self.is_checkpoint(height):
            return False
        self.votes.setdefault(height, set()).add(validator)
        return False

    def try_finalize(self, height: int, stakes: Dict[str, float]) -> bool:
        if height <= self.finalized_height:
            return False
        if not self.is_checkpoint(height):
            return False
        voted = self.votes.get(height, set())
        total_stake = sum(stakes.values())
        if total_stake <= 0:
            return False
        voted_stake = sum(stakes.get(v, 0.0) for v in voted)
        if voted_stake >= self.threshold * total_stake:
            self.finalized_height = height
            print(f"[finality] checkpoint #{height} FINALIZADO "
                  f"({voted_stake:.1f}/{total_stake:.1f} stake)")
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "interval": self.interval,
            "threshold": self.threshold,
            "finalized_height": self.finalized_height,
            "votes": {str(k): list(v) for k, v in self.votes.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Finality":
        f = cls(d.get("interval", FINALITY_INTERVAL),
                d.get("threshold", FINALITY_THRESHOLD))
        f.finalized_height = d.get("finalized_height", -1)
        f.votes = {int(k): set(v) for k, v in d.get("votes", {}).items()}
        return f


# ==================================================================
# Slashing
# ==================================================================
@dataclass
class SlashingEvidence:
    validator: str
    reason: str
    block_index: int
    invalid_block: dict
    reporter: str
    reporter_public_key: str
    reporter_signature: str

    def canonical(self) -> dict:
        return {
            "validator": self.validator,
            "reason": self.reason,
            "block_index": self.block_index,
            "block_hash": self.invalid_block.get("hash", ""),
            "reporter": self.reporter,
        }

    def verify(self) -> bool:
        try:
            blk = Block.from_dict(self.invalid_block)
        except Exception:
            return False
        if blk.verify():
            return False
        if self.validator != blk.validator:
            return False
        if self.block_index != blk.index:
            return False
        derived = WalletManager.address_from_public_key(self.reporter_public_key)
        if derived != self.reporter:
            return False
        return WalletManager.verify_signature(
            self.reporter_public_key, self.canonical(), self.reporter_signature
        )

    @staticmethod
    def build(invalid_block: Block, reason: str,
              reporter_sk: str, reporter_pk: str) -> "SlashingEvidence":
        reporter_addr = WalletManager.address_from_public_key(reporter_pk)
        ev = SlashingEvidence(
            validator=invalid_block.validator,
            reason=reason,
            block_index=invalid_block.index,
            invalid_block=invalid_block.to_dict(),
            reporter=reporter_addr,
            reporter_public_key=reporter_pk,
            reporter_signature="",
        )
        ev.reporter_signature = WalletManager.sign_transaction(reporter_sk, ev.canonical())
        return ev

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SlashingEvidence":
        return cls(**d)


# ==================================================================
# Blockchain
# ==================================================================
class Blockchain:
    def __init__(self, db_path: str = DB_PATH, node_identity: Optional[dict] = None,
                 genesis_allocations: Optional[Dict[str, Dict[str, float]]] = None,
                 regulator_address: str = ""):
        self.db_path = db_path
        self.node_identity = node_identity
        self.genesis_allocations = dict(genesis_allocations or GENESIS_ALLOCATIONS)

        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.registry = AssetRegistry()
        if regulator_address:
            self.registry.regulator = regulator_address
        self.slashed: Set[str] = set()
        self.finality = Finality()
        self.lock = threading.RLock()

        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            self._rebuild_state()
            self._bootstrap_native_asset()
            print("[chain] gênese RWA criada e persistida.")
        else:
            print(f"[chain] {len(self.chain)} blocos carregados de {self.db_path}")

    # ---------------- Bootstrap: ativo nativo ----------------
    def _bootstrap_native_asset(self):
        """Registra o ativo nativo BRN se ainda não existir."""
        if NATIVE_ASSET in self.registry.assets:
            return
        genesis = self.node_identity["address"] if self.node_identity else "brn1" + "0" * 40
        a = AssetDefinition(
            asset_id=NATIVE_ASSET,
            name="BRN — Moeda de liquidação da rede",
            symbol="BRN",
            asset_type="currency",
            decimals=8,
            issuer=genesis,
            transfer_agent=genesis,
            transfer_restricted=False,   # BRN circula livremente
            custodian="",
            max_supply=0,
        )
        self.registry.add_asset(a)
        self._persist_registry()

    # ---------------- SQLite ----------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    idx INTEGER PRIMARY KEY, hash TEXT NOT NULL,
                    validator TEXT, timestamp REAL, data TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(hash)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mempool (
                    tx_hash TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS slashing (
                    validator TEXT PRIMARY KEY, reason TEXT,
                    block_idx INTEGER, ts REAL, evidence TEXT)""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS faucet_claims (
                    address TEXT PRIMARY KEY, last_claim REAL NOT NULL)""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS finality (
                    id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS registry (
                    id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
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

    def _persist_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO registry (id, data) VALUES (1, ?)",
                (json.dumps(self.registry.to_dict()),),
            )
            conn.commit()

    def _load_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM registry WHERE id = 1").fetchone()
        if row:
            try:
                self.registry = AssetRegistry.from_dict(json.loads(row[0]))
                if not self.registry.regulator:
                    self.registry.regulator = REGULATOR_ADDRESS
            except Exception:
                pass

    def _persist_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO finality (id, data) VALUES (1, ?)",
                (json.dumps(self.finality.to_dict()),),
            )
            conn.commit()

    def _load_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM finality WHERE id = 1").fetchone()
        if row:
            try:
                self.finality = Finality.from_dict(json.loads(row[0]))
            except Exception:
                pass

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

    # ---------------- Mempool ----------------
    def _mempool_add(self, tx: dict):
        txh = Transaction.hash(tx)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mempool (tx_hash, ts, data) VALUES (?, ?, ?)",
                (txh, time.time(), json.dumps(tx)),
            )
            conn.commit()

    def _mempool_remove(self, tx: dict):
        txh = Transaction.hash(tx)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool WHERE tx_hash = ?", (txh,))
            conn.commit()

    def _mempool_clear(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool")
            conn.commit()

    def _mempool_load(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
        self.pending = []
        for (data,) in rows:
            try:
                tx = json.loads(data)
                if Transaction.verify_signature(tx):
                    self.pending.append(tx)
            except Exception:
                continue
        if self.pending:
            print(f"[mempool] {len(self.pending)} tx recarregadas do disco.")

    # ---------------- Slashing ----------------
    def _load_slashed(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT validator FROM slashing").fetchall()
        self.slashed = {r[0] for r in rows}

    def _slash(self, validator: str, reason: str, block_idx: int = -1,
               evidence: Optional[SlashingEvidence] = None):
        if evidence is not None and not evidence.verify():
            print("[slash] evidência inválida — ignorando.")
            return False
        if not validator or validator == "genesis":
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO slashing (validator, reason, block_idx, ts, evidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (validator, reason, block_idx, time.time(),
                 json.dumps(evidence.to_dict()) if evidence else None))
            conn.commit()
        self.slashed.add(validator)
        if validator in self.state.balances:
            self.state.balances[validator] = {}
        self.state.nonces[validator] = self.state.nonce(validator) + 1
        print(f"[slash] validador {validator[:16]}… banido ({reason})")
        return True

    def submit_slashing_evidence(self, evidence: SlashingEvidence) -> bool:
        if not evidence.verify():
            return False
        return self._slash(evidence.validator, evidence.reason,
                           evidence.block_index, evidence)

    # ---------------- Faucet ----------------
    def faucet(self, to_address: str, private_key_hex: str,
               public_key_hex: str) -> dict:
        if not FAUCET_ADDRESS:
            return {"ok": False, "msg": "Faucet desativado nesta rede."}
        if FAUCET_ADDRESS != WalletManager.address_from_public_key(public_key_hex):
            return {"ok": False, "msg": "Chave do faucet não corresponde."}

        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT last_claim FROM faucet_claims WHERE address = ?",
                (to_address,)).fetchone()
        if row and (now - row[0]) < FAUCET_COOLDOWN:
            remaining = int(FAUCET_COOLDOWN - (now - row[0]))
            return {"ok": False, "msg": f"Aguarde {remaining}s para pedir novamente."}

        tx = Transaction.build(
            tx_type="transfer", asset_id=NATIVE_ASSET,
            sender_address=FAUCET_ADDRESS, receiver_address=to_address,
            amount=FAUCET_AMOUNT,
            nonce=self.state.nonce(FAUCET_ADDRESS),
            private_key_hex=private_key_hex,
            public_key_hex=public_key_hex,
        )
        r = self.add_transaction(tx)
        if not r.get("ok"):
            return r

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO faucet_claims (address, last_claim) "
                "VALUES (?, ?)", (to_address, now))
            conn.commit()
        return {"ok": True, "msg": f"Faucet enviou {FAUCET_AMOUNT} BRN.", "tx": tx}

    # ---------------- Carga ----------------
    def _load_from_db(self) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
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
            self._mempool_clear()
            return False

        self._load_slashed()
        self._load_finality()
        self._load_registry()
        self._rebuild_state()
        self._mempool_load()
        return True

    def _rebuild_state(self):
        self.state = State()
        for addr, alloc in self.genesis_allocations.items():
            for asset_id, amt in alloc.items():
                self.state.credit(addr, asset_id, amt)
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self._apply_tx_to_state(self.state, tx)
            self.state.credit(blk.validator, NATIVE_ASSET, BLOCK_REWARD)

    def _create_genesis(self):
        genesis = Block(
            index=0, timestamp=time.time(), previous_hash="0" * 64,
            transactions=[], validator="genesis")
        genesis.finalize()
        self.chain.append(genesis)

    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    # ---------------- Aplicação de tx no State ----------------
    def _apply_tx_to_state(self, state: State, tx: dict) -> bool:
        t = tx["type"]
        sender = tx["from"]
        receiver = tx["to"]
        asset_id = tx["asset_id"]
        amount = float(tx["amount"])

        if t == "transfer":
            if state.available(sender, asset_id) < amount:
                return False
            state.debit(sender, asset_id, amount)
            state.credit(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t == "issue":
            a = self.registry.get_asset(asset_id)
            if not a:
                return False
            if a.max_supply > 0 and state.total_supply.get(asset_id, 0) + amount > a.max_supply:
                return False
            state.credit(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t == "redeem":
            if state.available(sender, asset_id) < amount:
                return False
            state.debit(sender, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t in ("freeze", "unfreeze"):
            if t == "freeze":
                if state.available(receiver, asset_id) < amount:
                    return False
                state.freeze(receiver, asset_id, amount)
            else:
                state.unfreeze(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t in ("kyc_register", "kyc_revoke"):
            # O estado de KYC vive no registry, aplicado no add_block
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t == "asset_create":
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t == "asset_update":
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        if t == "dividend":
            # amount = total do dividendo em NATIVE_ASSET.
            # snapshot = lista de holders do asset_id no momento do anúncio.
            snap = tx["metadata"].get("snapshot", {})
            total_shares = sum(snap.values())
            if total_shares <= 0:
                return False
            for holder, shares in snap.items():
                payout = amount * (shares / total_shares)
                if payout > 0 and state.available(sender, NATIVE_ASSET) >= payout:
                    state.debit(sender, NATIVE_ASSET, payout)
                    state.credit(holder, NATIVE_ASSET, payout)
            state.nonces[sender] = state.nonce(sender) + 1
            return True

        return False

    # ---------------- Mempool (validação) ----------------
    def add_transaction(self, tx: dict) -> dict:
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura inválida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereço não corresponde à chave pública."}
            if tx["from"] in self.slashed:
                return {"ok": False, "msg": "Remetente banido (slashing)."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False, "msg": "Nonce inválido (double-spend)."}
            if tx["type"] not in Transaction.VALID_TYPES:
                return {"ok": False, "msg": "Tipo de transação desconhecido."}

            ok, why = self._validate_tx(tx)
            if not ok:
                return {"ok": False, "msg": why}

            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Duplicada na mempool."}
            self.pending.append(tx)
            self._mempool_add(tx)
            return {"ok": True, "msg": "Transação aceita."}

    def _validate_tx(self, tx: dict) -> tuple[bool, str]:
        t = tx["type"]
        asset_id = tx["asset_id"]
        sender = tx["from"]
        receiver = tx["to"]
        amount = float(tx["amount"])
        md = tx.get("metadata", {})

        if t == "transfer":
            if asset_id not in self.registry.assets:
                return False, f"ativo '{asset_id}' não registrado"
            recv_after = self.state.balance(receiver, asset_id) + amount
            return self.registry.validate_transfer(
                asset_id, sender, receiver, amount, recv_after)

        if t == "issue":
            if not self.registry.can_issue(asset_id, sender):
                return False, "remetente não é o emissor do ativo"
            a = self.registry.get_asset(asset_id)
            if a.max_supply > 0 and self.state.total_supply.get(asset_id, 0) + amount > a.max_supply:
                return False, "excederia o max_supply"
            return True, "ok"

        if t == "redeem":
            a = self.registry.get_asset(asset_id)
            if not a:
                return False, "ativo não existe"
            if sender not in (a.issuer, a.transfer_agent):
                return False, "só emissor/agente pode resgatar"
            if self.state.available(receiver, asset_id) < amount:
                return False, "holder não possui o saldo"
            return True, "ok"

        if t == "freeze":
            if not self.registry.can_freeze(asset_id, sender):
                return False, "sem permissão para congelar"
            return True, "ok"

        if t == "unfreeze":
            if not self.registry.can_freeze(asset_id, sender):
                return False, "sem permissão para descongelar"
            return True, "ok"

        if t == "kyc_register":
            if not self.registry.can_manage_kyc(asset_id, sender):
                return False, "sem permissão para gerenciar KYC"
            status = md.get("status", "approved")
            level = md.get("level", "basic")
            if status not in KYC_STATUS or level not in KYC_LEVELS:
                return False, "status/level inválidos"
            return True, "ok"

        if t == "kyc_revoke":
            if not self.registry.can_manage_kyc(asset_id, sender):
                return False, "sem permissão para gerenciar KYC"
            return True, "ok"

        if t == "asset_create":
            new_id = md.get("asset_id", "")
            if not new_id or new_id in self.registry.assets:
                return False, "asset_id ausente ou já existe"
            if md.get("asset_type") not in ASSET_TYPES:
                return False, "asset_type inválido"
            if md.get("issuer") != sender:
                return False, "issuer deve ser o remetente"
            return True, "ok"

        if t == "asset_update":
            if not self.registry.can_issue(asset_id, sender):
                return False, "só o emissor pode atualizar"
            return True, "ok"

        if t == "dividend":
            if not self.registry.can_issue(asset_id, sender):
                return False, "só o emissor pode pagar dividendos"
            if self.state.available(sender, NATIVE_ASSET) < amount:
                return False, "emissor sem saldo em BRN para o dividendo"
            snap = md.get("snapshot", {})
            if not isinstance(snap, dict) or not snap:
                return False, "snapshot de holders ausente"
            return True, "ok"

        return False, "tipo não suportado"

    # ---------------- Aplicação no registry (só no commit do bloco) ----------------
    def _apply_registry_ops(self, tx: dict):
        t = tx["type"]
        md = tx.get("metadata", {})
        if t == "asset_create":
            a = AssetDefinition(
                asset_id=md["asset_id"],
                name=md.get("name", md["asset_id"]),
                symbol=md.get("symbol", md["asset_id"]),
                asset_type=md["asset_type"],
                decimals=int(md.get("decimals", 2)),
                issuer=md["issuer"],
                transfer_agent=md.get("transfer_agent", md["issuer"]),
                custodian=md.get("custodian", ""),
                isin=md.get("isin", ""),
                max_supply=float(md.get("max_supply", 0)),
                transfer_restricted=bool(md.get("transfer_restricted", True)),
                legal_doc_hash=md.get("legal_doc_hash", ""),
                metadata=md.get("metadata", {}),
            )
            self.registry.add_asset(a)
            self._persist_registry()

        elif t == "asset_update":
            self.registry.update_asset(tx["asset_id"], md)
            self._persist_registry()

        elif t == "kyc_register":
            rec = ComplianceRecord(
                address=tx["to"],
                status=md.get("status", "approved"),
                level=md.get("level", "basic"),
                jurisdiction=md.get("jurisdiction", ""),
                verified_by=tx["from"],
                verified_at=time.time(),
                expires_at=float(md.get("expires_at", 0)),
                restrictions=list(md.get("restrictions", [])),
                metadata=md.get("extra", {}),
            )
            self.registry.set_compliance(rec)
            self._persist_registry()

        elif t == "kyc_revoke":
            rec = self.registry.get_compliance(tx["to"])
            if rec:
                rec.status = "revoked"
                self._persist_registry()

        elif t == "freeze":
            # Congelamento administrativo do ativo inteiro:
            if md.get("freeze_asset"):
                a = self.registry.get_asset(tx["asset_id"])
                if a:
                    a.frozen = True
                    self._persist_registry()

        elif t == "unfreeze":
            if md.get("unfreeze_asset"):
                a = self.registry.get_asset(tx["asset_id"])
                if a:
                    a.frozen = False
                    self._persist_registry()

    # ---------------- Seleção de validador ----------------
    def _select_validator(self) -> Optional[str]:
        eligible = {
            a: b.get(NATIVE_ASSET, 0.0) for a, b in self.state.balances.items()
            if b.get(NATIVE_ASSET, 0.0) >= MIN_STAKE and a not in self.slashed
        }
        if not eligible:
            if self.node_identity and self.node_identity["address"] not in self.slashed:
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

    # ---------------- Produção de bloco ----------------
    def produce_block(self) -> Optional[Block]:
        with self.lock:
            if not self.node_identity:
                return None
            if self.node_identity["address"] in self.slashed:
                return None

            validator = self._select_validator()
            if validator != self.node_identity["address"]:
                return None

            temp = self.state.copy()
            chosen = []
            for tx in sorted(self.pending, key=lambda t: (t["from"], t["nonce"])):
                if not Transaction.verify_signature(tx):
                    continue
                # aplica em cópia pra não sujar o estado real
                if tx["nonce"] != temp.nonce(tx["from"]):
                    continue
                if not self._apply_tx_to_state(temp, tx):
                    continue
                chosen.append(tx)

            block = Block(
                index=self.last_block.index + 1,
                timestamp=time.time(),
                previous_hash=self.last_block.hash,
                transactions=chosen,
                validator=self.node_identity["address"],
                validator_public_key=self.node_identity["public_key"],
                registry_snapshot=self.registry.to_dict(),
            )
            block.finalize()
            block.sign(self.node_identity["spend_secret_key"])
            if not block.verify():
                return None

            # Commit real
            for tx in chosen:
                self._apply_tx_to_state(self.state, tx)
                self._apply_registry_ops(tx)
                self._mempool_remove(tx)
            self.state.credit(block.validator, NATIVE_ASSET, BLOCK_REWARD)

            self.pending = [t for t in self.pending if t not in chosen]
            self.chain.append(block)
            self._persist_block(block)

            if self.finality.is_checkpoint(block.index):
                self.finality.vote(block.index, block.validator)
                stakes = {a: b.get(NATIVE_ASSET, 0.0) for a, b in self.state.balances.items()}
                if self.finality.try_finalize(block.index, stakes):
                    self._persist_finality()
            return block

    # ---------------- Verificação ----------------
    def _find_invalid_block(self, chain: List[Block]) -> Optional[Block]:
        for i, blk in enumerate(chain):
            if not blk.verify():
                return blk
            if i == 0:
                continue
            prev = chain[i - 1]
            if blk.previous_hash != prev.hash or blk.index != prev.index + 1:
                return blk
        return None

    def _verify_chain_structure(self, chain: List[Block]) -> bool:
        return self._find_invalid_block(chain) is None

    def _fork_score(self, chain: List[Block]) -> float:
        score = 0.0
        for blk in chain[1:]:
            score += 1.0 + self.state.balance(blk.validator, NATIVE_ASSET)
        return score

    # ---------------- Substituição de cadeia ----------------
    def replace_chain(self, new_chain: List[dict]) -> bool:
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception as e:
            print(f"[chain] erro ao desserializar: {e}")
            return False

        with self.lock:
            if len(blocks) <= self.finality.finalized_height:
                return False
            for i in range(min(self.finality.finalized_height + 1, len(self.chain))):
                if i >= len(blocks) or blocks[i].hash != self.chain[i].hash:
                    return False

            invalid = self._find_invalid_block(blocks)
            if invalid is not None:
                self._slash(invalid.validator,
                            f"bloco inválido #{invalid.index}",
                            block_idx=invalid.index)
                return False

            new_score = self._fork_score(blocks)
            cur_score = self._fork_score(self.chain)
            if new_score < cur_score:
                return False
            if new_score == cur_score and len(blocks) <= len(self.chain):
                return False

            # Reconstrói estado + registry
            new_state = State()
            new_reg = AssetRegistry()
            new_reg.regulator = self.registry.regulator
            new_reg.add_asset(AssetDefinition(
                asset_id=NATIVE_ASSET, name="BRN", symbol="BRN",
                asset_type="currency", decimals=8,
                issuer=blocks[1].validator if len(blocks) > 1 else "genesis",
                transfer_agent=blocks[1].validator if len(blocks) > 1 else "genesis",
                transfer_restricted=False))

            for addr, alloc in self.genesis_allocations.items():
                for asset_id, amt in alloc.items():
                    new_state.credit(addr, asset_id, amt)

            for blk in blocks[1:]:
                for tx in blk.transactions:
                    if not Transaction.verify_signature(tx):
                        return False
                    if not self._apply_tx_to_state(new_state, tx):
                        return False
                    # aplica ops de registry em cima do new_reg
                    saved = self.registry
                    self.registry = new_reg
                    self._apply_registry_ops(tx)
                    new_reg = self.registry
                    self.registry = saved
                new_state.credit(blk.validator, NATIVE_ASSET, BLOCK_REWARD)

            self.chain = blocks
            self.state = new_state
            self.registry = new_reg
            self._persist_registry()
            confirmed = {Transaction.hash(tx) for blk in blocks for tx in blk.transactions}
            self.pending = [t for t in self.pending if Transaction.hash(t) not in confirmed]
            self._replace_all_in_db(blocks)
            self._mempool_clear()
            for t in self.pending:
                self._mempool_add(t)
            print(f"[chain] cadeia substituída → altura {len(blocks)}")
            return True

    # ---------------- API de conveniência ----------------
    def submit_asset_create(self, **kw) -> dict:
        """Helper para criar um ativo (issuer assina)."""
        required = ["asset_id", "name", "symbol", "asset_type",
                    "issuer", "private_key", "public_key", "nonce"]
        for r in required:
            if r not in kw:
                return {"ok": False, "msg": f"faltando '{r}'"}
        md = {k: kw[k] for k in kw if k not in
              ("private_key", "public_key", "nonce")}
        tx = Transaction.build(
            tx_type="asset_create", asset_id=kw["asset_id"],
            sender_address=kw["issuer"], receiver_address=kw["issuer"],
            amount=0, nonce=kw["nonce"],
            private_key_hex=kw["private_key"],
            public_key_hex=kw["public_key"],
            metadata=md)
        return self.add_transaction(tx)

    def submit_dividend(self, asset_id: str, issuer: str, total_amount: float,
                        private_key: str, public_key: str, nonce: int) -> dict:
        # Snapshot dos holders do ativo
        holders = {addr: bals.get(asset_id, 0.0)
                   for addr, bals in self.state.balances.items()
                   if bals.get(asset_id, 0.0) > 0}
        if not holders:
            return {"ok": False, "msg": "sem holders do ativo"}
        tx = Transaction.build(
            tx_type="dividend", asset_id=asset_id,
            sender_address=issuer, receiver_address=issuer,
            amount=total_amount, nonce=nonce,
            private_key_hex=private_key, public_key_hex=public_key,
            metadata={"snapshot": holders})
        return self.add_transaction(tx)

    # ---------------- Serialização ----------------
    def to_dict(self) -> dict:
        return {
            "network_id": NETWORK_ID,
            "length": len(self.chain),
            "chain": [b.to_dict() for b in self.chain],
        }

    def slashing_report(self) -> List[dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT validator, reason, block_idx, ts, evidence "
                "FROM slashing ORDER BY ts DESC").fetchall()
        return [{"validator": r[0], "reason": r[1], "block_index": r[2],
                 "timestamp": r[3],
                 "evidence": json.loads(r[4]) if r[4] else None} for r in rows]

    def finality_report(self) -> dict:
        return {
            "finalized_height": self.finality.finalized_height,
            "interval": self.finality.interval,
            "threshold": self.finality.threshold,
            "checkpoint_pending": {str(k): list(v) for k, v in self.finality.votes.items()},
        }

    def portfolio(self, addr: str) -> dict:
        """Retorna os saldos do endereço + metadados dos ativos."""
        out = {}
        for asset_id, amount in self.state.balances.get(addr, {}).items():
            a = self.registry.get_asset(asset_id)
            out[asset_id] = {
                "amount": amount,
                "available": self.state.available(addr, asset_id),
                "frozen": self.state.frozen.get(addr, {}).get(asset_id, 0.0),
                "asset": a.to_dict() if a else None,
                "compliance": (self.registry.get_compliance(addr).to_dict()
                               if self.registry.get_compliance(addr) else None),
            }
        return out

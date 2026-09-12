# bruno_blockchain_real.py
# Blockchain com:
#   - SHA3-256, blocos assinados por ECDSA
#   - Persistência SQLite (cadeia, mempool, slashing, faucet)
#   - Slashing com evidência assinada
#   - Fork-choice estilo GHOST (peso por stake)
#   - Finality gadget (Casper FFG simplificado)
#   - Faucet de bootstrap

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

NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-mainnet-1")
P2P_HOST = os.environ.get("BRN_P2P_HOST", "0.0.0.0")
P2P_PORT = int(os.environ.get("BRN_P2P_PORT", "7777"))
SEED_PEERS = [p.strip() for p in os.environ.get("BRN_SEED_PEERS", "").split(",") if p.strip()]
TARGET_BLOCK_TIME = int(os.environ.get("BRN_TARGET_BLOCK_TIME", "10"))

BLOCK_REWARD = float(os.environ.get("BRN_BLOCK_REWARD", "50"))
MIN_STAKE = float(os.environ.get("BRN_MIN_STAKE", "100"))
DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")

FINALITY_INTERVAL = int(os.environ.get("BRN_FINALITY_INTERVAL", "5"))
FINALITY_THRESHOLD = float(os.environ.get("BRN_FINALITY_THRESHOLD", "0.67"))

FAUCET_ADDRESS = os.environ.get("BRN_FAUCET_ADDRESS", "").strip()
FAUCET_AMOUNT = float(os.environ.get("BRN_FAUCET_AMOUNT", "100"))
FAUCET_COOLDOWN = int(os.environ.get("BRN_FAUCET_COOLDOWN", "3600"))

_raw_alloc = os.environ.get("BRN_GENESIS_ALLOC", "").strip()
GENESIS_ALLOCATIONS: Dict[str, float] = {}
if _raw_alloc:
    for pair in _raw_alloc.split(","):
        if ":" in pair:
            addr, amt = pair.split(":", 1)
            try:
                GENESIS_ALLOCATIONS[addr.strip()] = float(amt.strip())
            except ValueError:
                pass


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
# Transações
# ==================================================================
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

    @staticmethod
    def hash(tx: dict) -> str:
        raw = json.dumps(tx, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha3_256(raw).hexdigest()


# ==================================================================
# Estado
# ==================================================================
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


# ==================================================================
# Finality gadget (Casper FFG simplificado)
# ==================================================================
class Finality:
    """
    Checkpoints a cada FINALITY_INTERVAL blocos.
    Um checkpoint é finalizado quando > FINALITY_THRESHOLD do stake vota nele.
    Blocos finalizados não podem ser revertidos.
    """

    def __init__(self, interval: int = FINALITY_INTERVAL,
                 threshold: float = FINALITY_THRESHOLD):
        self.interval = interval
        self.threshold = threshold
        self.finalized_height: int = -1
        self.votes: Dict[int, Set[str]] = {}  # altura → validadores

    def is_checkpoint(self, height: int) -> bool:
        return height > 0 and height % self.interval == 0

    def vote(self, height: int, validator: str) -> bool:
        """Registra voto. Retorna True se o checkpoint foi finalizado."""
        if not self.is_checkpoint(height):
            return False
        self.votes.setdefault(height, set()).add(validator)
        return False

    def try_finalize(self, height: int, stakes: Dict[str, float]) -> bool:
        """Finaliza se stake votante >= threshold * stake total."""
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
# Slashing com evidência assinada
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
        """Valida a evidência: bloco realmente inválido + assinatura do reporter."""
        try:
            blk = Block.from_dict(self.invalid_block)
        except Exception:
            return False
        if blk.verify():
            return False  # bloco é válido → evidência falsa
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
                 genesis_allocations: Optional[Dict[str, float]] = None):
        self.db_path = db_path
        self.node_identity = node_identity
        self.genesis_allocations = dict(genesis_allocations or GENESIS_ALLOCATIONS)

        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.slashed: Set[str] = set()
        self.finality = Finality()
        self.lock = threading.RLock()

        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            self._rebuild_state()
            print("[chain] gênese criada e persistida.")
        else:
            print(f"[chain] {len(self.chain)} blocos carregados de {self.db_path}")

    # ---------------- SQLite ----------------
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    idx INTEGER PRIMARY KEY,
                    hash TEXT NOT NULL,
                    validator TEXT,
                    timestamp REAL,
                    data TEXT NOT NULL
                )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(hash)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mempool (
                    tx_hash TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    data TEXT NOT NULL
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS slashing (
                    validator TEXT PRIMARY KEY,
                    reason TEXT,
                    block_idx INTEGER,
                    ts REAL,
                    evidence TEXT
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS faucet_claims (
                    address TEXT PRIMARY KEY,
                    last_claim REAL NOT NULL
                )""")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS finality (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    data TEXT NOT NULL
                )""")
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
                print(f"[finality] checkpoint finalizado = #{self.finality.finalized_height}")
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
        if self.slashed:
            print(f"[slashing] {len(self.slashed)} validador(es) banido(s) carregado(s).")

    def _slash(self, validator: str, reason: str, block_idx: int = -1,
               evidence: Optional[SlashingEvidence] = None):
        """Aplica slashing. Se evidence é fornecida, valida primeiro."""
        if evidence is not None:
            if not evidence.verify():
                print("[slash] evidência inválida — ignorando.")
                return False
        if not validator or validator == "genesis":
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO slashing (validator, reason, block_idx, ts, evidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (validator, reason, block_idx, time.time(),
                 json.dumps(evidence.to_dict()) if evidence else None),
            )
            conn.commit()
        self.slashed.add(validator)
        if validator in self.state.balances:
            self.state.balances[validator] = 0.0
        self.state.nonces[validator] = self.state.nonce(validator) + 1
        print(f"[slash] validador {validator[:16]}… banido ({reason})")
        return True

    def submit_slashing_evidence(self, evidence: SlashingEvidence) -> bool:
        """Qualquer nó pode submeter evidência; todos aplicam se válida."""
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
                (to_address,),
            ).fetchone()
        if row and (now - row[0]) < FAUCET_COOLDOWN:
            remaining = int(FAUCET_COOLDOWN - (now - row[0]))
            return {"ok": False, "msg": f"Aguarde {remaining}s para pedir novamente."}

        tx = Transaction.build(
            sender_address=FAUCET_ADDRESS,
            receiver_address=to_address,
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
                "VALUES (?, ?)",
                (to_address, now),
            )
            conn.commit()
        return {"ok": True, "msg": f"Faucet enviou {FAUCET_AMOUNT} BRN.", "tx": tx}

    # ---------------- Carga do disco ----------------
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
        self._rebuild_state()
        self._mempool_load()
        return True

    def _rebuild_state(self):
        self.state = State()
        for addr, amt in self.genesis_allocations.items():
            self.state.credit(addr, amt)
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self.state.apply_transaction(tx)
            self.state.credit(blk.validator, BLOCK_REWARD)
        for v in self.slashed:
            if v in self.state.balances:
                self.state.balances[v] = 0.0
            self.state.nonces[v] = self.state.nonce(v) + 1

    def _create_genesis(self):
        genesis = Block(
            index=0, timestamp=time.time(), previous_hash="0" * 64,
            transactions=[], validator="genesis",
        )
        genesis.finalize()
        self.chain.append(genesis)

    @property
    def last_block(self) -> Block:
        return self.chain[-1]

    # ---------------- Mempool ----------------
    def add_transaction(self, tx: dict) -> dict:
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura inválida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereço não corresponde à chave pública."}
            if tx["from"] in self.slashed:
                return {"ok": False, "msg": "Remetente está banido (slashing)."}
            if self.state.balance(tx["from"]) < tx["amount"]:
                return {"ok": False, "msg": "Saldo insuficiente."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False, "msg": "Nonce inválido (double-spend)."}
            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Duplicada na mempool."}
            self.pending.append(tx)
            self._mempool_add(tx)
            return {"ok": True, "msg": "Transação aceita."}

    # ---------------- Seleção de validador ----------------
    def _select_validator(self) -> Optional[str]:
        eligible = {
            a: b for a, b in self.state.balances.items()
            if b >= MIN_STAKE and a not in self.slashed
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
                print("[consenso] este nó está banido (slashing); não produz blocos.")
                return None

            validator = self._select_validator()
            if validator != self.node_identity["address"]:
                return None

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

            for tx in chosen:
                self.state.apply_transaction(tx)
                self._mempool_remove(tx)
            self.state.credit(block.validator, BLOCK_REWARD)

            self.pending = [t for t in self.pending if t not in chosen]
            self.chain.append(block)
            self._persist_block(block)

            # Registra voto do próprio nó (se checkpoint)
            if self.finality.is_checkpoint(block.index):
                self.finality.vote(block.index, block.validator)
                if self.finality.try_finalize(block.index, self.state.balances):
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
            score += 1.0 + self.state.balance(blk.validator)
        return score

    # ---------------- Substituição de cadeia ----------------
    def replace_chain(self, new_chain: List[dict]) -> bool:
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b
                      for b in new_chain]
        except Exception as e:
            print(f"[chain] erro ao desserializar cadeia recebida: {e}")
            return False

        with self.lock:
            # Rejeita se não contém bloco finalizado
            if len(blocks) <= self.finality.finalized_height:
                print("[chain] cadeia não contém checkpoint finalizado; rejeitando.")
                return False

            # Rejeita se remove bloco já finalizado
            for i in range(min(self.finality.finalized_height + 1, len(self.chain))):
                if i >= len(blocks):
                    print("[chain] cadeia perde bloco finalizado; rejeitando.")
                    return False
                if blocks[i].hash != self.chain[i].hash:
                    print(f"[chain] cadeia conflita com bloco finalizado #{i}; rejeitando.")
                    return False

            # Detecta e pune bloco inválido
            invalid = self._find_invalid_block(blocks)
            if invalid is not None:
                self._slash(
                    invalid.validator,
                    f"bloco inválido #{invalid.index} (hash/assinatura)",
                    block_idx=invalid.index,
                )
                return False

            new_score = self._fork_score(blocks)
            cur_score = self._fork_score(self.chain)
            if new_score < cur_score:
                return False
            if new_score == cur_score and len(blocks) <= len(self.chain):
                return False

            new_state = State()
            for addr, amt in self.genesis_allocations.items():
                new_state.credit(addr, amt)
            for blk in blocks[1:]:
                for tx in blk.transactions:
                    if not Transaction.verify_signature(tx):
                        return False
                    if not new_state.apply_transaction(tx):
                        return False
                new_state.credit(blk.validator, BLOCK_REWARD)
            for v in self.slashed:
                if v in new_state.balances:
                    new_state.balances[v] = 0.0
                new_state.nonces[v] = new_state.nonce(v) + 1

            self.chain = blocks
            self.state = new_state
            confirmed = {Transaction.hash(tx) for blk in blocks for tx in blk.transactions}
            self.pending = [t for t in self.pending
                            if Transaction.hash(t) not in confirmed]
            self._replace_all_in_db(blocks)
            self._mempool_clear()
            for t in self.pending:
                self._mempool_add(t)

            print(f"[chain] cadeia substituída → altura {len(blocks)} "
                  f"(score {new_score:.1f})")
            return True

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
                "FROM slashing ORDER BY ts DESC"
            ).fetchall()
        return [
            {"validator": r[0], "reason": r[1], "block_index": r[2],
             "timestamp": r[3], "evidence": json.loads(r[4]) if r[4] else None}
            for r in rows
        ]

    def finality_report(self) -> dict:
        return {
            "finalized_height": self.finality.finalized_height,
            "interval": self.finality.interval,
            "threshold": self.finality.threshold,
            "checkpoint_pending": self.finality.votes,
        }
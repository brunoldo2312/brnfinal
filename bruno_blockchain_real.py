import hashlib
import json
import time
import secrets
import threading
import sqlite3
import os
import math
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Set, Tuple
from cripto_wallet import WalletManager
from assets import (AssetRegistry, AssetDefinition, ComplianceRecord,
                    TransferRule, ASSET_TYPES, KYC_STATUS, KYC_LEVELS)

NETWORK_ID = os.environ.get("BRN_NETWORK_ID", "brn-rwa-1")
BLOCK_REWARD = float(os.environ.get("BRN_BLOCK_REWARD", "1"))
MIN_STAKE = float(os.environ.get("BRN_MIN_STAKE", "100"))
DB_PATH = os.environ.get("BRN_DB_PATH", "blockchain.db")
FINALITY_INTERVAL = int(os.environ.get("BRN_FINALITY_INTERVAL", "5"))
FINALITY_THRESHOLD = float(os.environ.get("BRN_FINALITY_THRESHOLD", "0.67"))
FAUCET_ADDRESS = os.environ.get("BRN_FAUCET_ADDRESS", "").strip()
FAUCET_AMOUNT = float(os.environ.get("BRN_FAUCET_AMOUNT", "100"))
FAUCET_COOLDOWN = int(os.environ.get("BRN_FAUCET_COOLDOWN", "3600"))
REGULATOR_ADDRESS = os.environ.get("BRN_REGULATOR_ADDRESS", "").strip()
NATIVE_ASSET = "BRN"
NATIVE_ASSET_ISSUER = "brn1" + "0" * 40

POW_INITIAL_DIFFICULTY = int(os.environ.get("BRN_POW_DIFFICULTY", "18"))
POW_ADJUST_INTERVAL    = int(os.environ.get("BRN_POW_ADJUST_INTERVAL", "10"))
POW_TARGET_TIME        = float(os.environ.get("BRN_POW_TARGET_TIME", "10"))
POW_MAX_ADJUST_FACTOR  = 4
POW_MIN_DIFFICULTY     = 1

# ---------- MEV protection ----------
MEV_COMMIT_MIN_BLOCKS   = int(os.environ.get("BRN_MEV_MIN_BLOCKS", "2"))
MEV_COMMIT_MAX_BLOCKS   = int(os.environ.get("BRN_MEV_MAX_BLOCKS", "20"))
MEV_MAX_PENDING_COMMITS = int(os.environ.get("BRN_MEV_MAX_COMMITS", "20"))

_raw = os.environ.get("BRN_GENESIS_ALLOC", "").strip()
GENESIS_ALLOCATIONS: Dict[str, Dict[str, float]] = {}
if _raw:
    for pair in _raw.split(","):
        parts = pair.split(":")
        if len(parts) == 3:
            try:
                GENESIS_ALLOCATIONS.setdefault(parts[0].strip(), {})[parts[1].strip()] = float(parts[2].strip())
            except ValueError:
                pass


# =====================================================================
# Block
# =====================================================================
@dataclass
class Block:
    index: int
    timestamp: float
    previous_hash: str
    transactions: List[dict]
    miner: str
    difficulty: int = POW_INITIAL_DIFFICULTY
    nonce: int = 0
    miner_public_key: str = ""
    signature: str = ""
    hash: str = ""
    registry_snapshot: Optional[dict] = None

    def calculate_hash(self):
        body = {"index": self.index, "timestamp": self.timestamp,
                "previous_hash": self.previous_hash,
                "transactions": self.transactions,
                "miner": self.miner, "difficulty": self.difficulty,
                "nonce": self.nonce, "network_id": NETWORK_ID,
                "registry_hash": self._registry_hash()}
        return hashlib.sha3_256(json.dumps(body, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()

    def _registry_hash(self):
        if not self.registry_snapshot: return ""
        return hashlib.sha3_256(json.dumps(self.registry_snapshot,
                                sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()

    def target(self): return "0" * self.difficulty
    def finalize(self): self.hash = self.calculate_hash()

    def mine(self):
        t = self.target()
        while True:
            h = self.calculate_hash()
            if h.startswith(t):
                self.hash = h; return self.nonce
            self.nonce += 1

    def sign(self, sk_hex):
        if not self.hash: self.finalize()
        self.signature = WalletManager.sign_transaction(
            sk_hex, {"block_hash": self.hash, "index": self.index})

    def verify_pow(self):
        return self.hash == self.calculate_hash() and self.hash.startswith(self.target())

    def verify(self):
        if not self.verify_pow(): return False
        if self.index == 0: return True
        if self.miner_public_key and self.signature:
            if WalletManager.address_from_public_key(self.miner_public_key) != self.miner:
                return False
            if not WalletManager.verify_signature(self.miner_public_key,
                    {"block_hash": self.hash, "index": self.index}, self.signature):
                return False
        return True

    def to_dict(self): return asdict(self)

    def to_wire_dict(self):
        d = self.to_dict(); d["height"] = d.pop("index"); return d

    def to_wire_header(self):
        return {"height": self.index, "hash": self.hash,
                "previous_hash": self.previous_hash, "timestamp": self.timestamp,
                "difficulty": self.difficulty, "nonce": self.nonce,
                "miner": self.miner}

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        if "height" in d and "index" not in d:
            d["index"] = d.pop("height")
        allowed = {"index","timestamp","previous_hash","transactions","miner",
                   "difficulty","nonce","miner_public_key","signature",
                   "hash","registry_snapshot"}
        return cls(**{k: v for k, v in d.items() if k in allowed})


# =====================================================================
# Transaction
# =====================================================================
class Transaction:
    REQUIRED_FIELDS = {"type", "asset_id", "from", "to", "amount",
                       "nonce", "public_key", "signature"}
    VALID_TYPES = {
        "transfer", "issue", "redeem", "freeze", "unfreeze",
        "kyc_register", "kyc_revoke", "asset_create", "asset_update", "dividend",
        "order_place", "order_cancel",
        "order_commit", "order_reveal",
    }
    CANONICAL_FIELDS = {"type", "asset_id", "from", "to", "amount",
                        "nonce", "public_key", "timestamp", "metadata"}

    @staticmethod
    def build(tx_type, asset_id, sender_address, receiver_address, amount,
              nonce, private_key_hex, public_key_hex, metadata=None):
        if tx_type not in Transaction.VALID_TYPES:
            raise ValueError(f"Tipo invalido: {tx_type}")
        if amount < 0: raise ValueError("Valor negativo.")
        tx = {"type": tx_type, "asset_id": asset_id, "from": sender_address,
              "to": receiver_address, "amount": float(amount),
              "nonce": int(nonce), "public_key": public_key_hex,
              "timestamp": time.time(), "metadata": metadata or {}}
        tx["signature"] = WalletManager.sign_transaction(private_key_hex, tx)
        return tx

    @staticmethod
    def sanitize(tx: dict) -> dict:
        return {k: v for k, v in tx.items()
                if k in Transaction.CANONICAL_FIELDS or k == "signature"}

    @staticmethod
    def verify_signature(tx):
        if not Transaction.REQUIRED_FIELDS.issubset(tx.keys()): return False
        payload = {k: v for k, v in tx.items() if k != "signature"}
        return WalletManager.verify_signature(tx["public_key"], payload, tx["signature"])

    @staticmethod
    def derived_address(tx):
        return WalletManager.address_from_public_key(tx["public_key"])

    @staticmethod
    def hash(tx):
        clean = {k: v for k, v in tx.items()
                 if k in Transaction.CANONICAL_FIELDS or k == "signature"}
        return hashlib.sha3_256(json.dumps(clean, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()


# =====================================================================
# State
# =====================================================================
class State:
    def __init__(self):
        self.balances: Dict[str, Dict[str, float]] = {}
        self.nonces: Dict[str, int] = {}
        self.frozen: Dict[str, Dict[str, float]] = {}
        self.total_supply: Dict[str, float] = {}
        # CLOB
        self.orders: Dict[str, dict] = {}
        self.order_books: Dict[str, dict] = {}
        self.trades: List[dict] = []
        # MEV
        self.commits: Dict[str, dict] = {}

    def balance(self, addr, asset_id=NATIVE_ASSET):
        return self.balances.get(addr, {}).get(asset_id, 0.0)

    def available(self, addr, asset_id):
        return self.balance(addr, asset_id) - self.frozen.get(addr, {}).get(asset_id, 0.0)

    def nonce(self, addr): return self.nonces.get(addr, 0)

    def credit(self, addr, asset_id, amount):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) + amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) + amount

    def debit(self, addr, asset_id, amount):
        self.balances.setdefault(addr, {})
        self.balances[addr][asset_id] = self.balances[addr].get(asset_id, 0.0) - amount
        self.total_supply[asset_id] = self.total_supply.get(asset_id, 0.0) - amount

    def freeze(self, addr, asset_id, amount):
        self.frozen.setdefault(addr, {})
        self.frozen[addr][asset_id] = self.frozen[addr].get(asset_id, 0.0) + amount

    def unfreeze(self, addr, asset_id, amount):
        self.frozen.setdefault(addr, {})
        self.frozen[addr][asset_id] = max(0.0,
            self.frozen[addr].get(asset_id, 0.0) - amount)

    def copy(self):
        s = State()
        s.balances = {a: dict(v) for a, v in self.balances.items()}
        s.nonces = dict(self.nonces)
        s.frozen = {a: dict(v) for a, v in self.frozen.items()}
        s.total_supply = dict(self.total_supply)
        s.orders = {k: dict(v) for k, v in self.orders.items()}
        s.order_books = {k: {"bids": list(v["bids"]), "asks": list(v["asks"])}
                         for k, v in self.order_books.items()}
        s.trades = [dict(t) for t in self.trades]
        s.commits = {k: dict(v) for k, v in self.commits.items()}
        return s


# =====================================================================
# Finality
# =====================================================================
class Finality:
    def __init__(self, interval=FINALITY_INTERVAL, threshold=FINALITY_THRESHOLD):
        self.interval = interval; self.threshold = threshold
        self.finalized_height = -1; self.votes = {}
    def update_by_depth(self, tip_height, confirmations=6):
        nf = max(-1, tip_height - confirmations)
        if nf > self.finalized_height:
            self.finalized_height = nf; return True
        return False
    def to_dict(self):
        return {"interval": self.interval, "threshold": self.threshold,
                "finalized_height": self.finalized_height,
                "votes": {str(k): list(v) for k, v in self.votes.items()}}
    @classmethod
    def from_dict(cls, d):
        f = cls(d.get("interval", FINALITY_INTERVAL),
                d.get("threshold", FINALITY_THRESHOLD))
        f.finalized_height = d.get("finalized_height", -1)
        f.votes = {int(k): set(v) for k, v in d.get("votes", {}).items()}
        return f


# =====================================================================
# Slashing
# =====================================================================
@dataclass
class SlashingEvidence:
    validator: str; reason: str; block_index: int; invalid_block: dict
    reporter: str; reporter_public_key: str; reporter_signature: str
    def canonical(self):
        return {"validator": self.validator, "reason": self.reason,
                "block_index": self.block_index,
                "block_hash": self.invalid_block.get("hash", ""),
                "reporter": self.reporter}
    def verify(self):
        try: blk = Block.from_dict(self.invalid_block)
        except Exception: return False
        if blk.verify(): return False
        if self.validator != blk.miner: return False
        if self.block_index != blk.index: return False
        if WalletManager.address_from_public_key(self.reporter_public_key) != self.reporter:
            return False
        return WalletManager.verify_signature(self.reporter_public_key,
            self.canonical(), self.reporter_signature)
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, d): return cls(**d)


# =====================================================================
# BlockchainDB (adaptador p2p)
# =====================================================================
class BlockchainDB:
    def __init__(self, blockchain): self.bc = blockchain
    def height(self): return self.bc.height
    def tip_hash(self): return self.bc.tip_hash
    def headers(self, start, count):
        chain = self.bc.chain
        return [b.to_wire_header() for b in chain[start:start + count]]
    def get_block(self, height):
        chain = self.bc.chain
        if 0 <= height < len(chain):
            return chain[height].to_wire_dict()
        return None
    def has_mempool(self, txid):
        return any(Transaction.hash(tx) == txid for tx in self.bc.pending)
    def get_mempool_tx(self, txid):
        for tx in self.bc.pending:
            if Transaction.hash(tx) == txid: return dict(tx)
        return None
    def tx_id(self, tx): return Transaction.hash(tx)


# =====================================================================
# Blockchain
# =====================================================================
class Blockchain:
    def __init__(self, db_path=DB_PATH, node_identity=None,
                 genesis_allocations=None, regulator_address=""):
        self.db_path = db_path
        self.node_identity = node_identity
        self.genesis_allocations = dict(genesis_allocations or GENESIS_ALLOCATIONS)
        self.chain: List[Block] = []
        self.pending: List[dict] = []
        self.state = State()
        self.registry = AssetRegistry()
        if regulator_address: self.registry.regulator = regulator_address
        self.slashed: Set[str] = set()
        self.finality = Finality()
        self.lock = threading.RLock()
        self._init_db()
        if not self._load_from_db():
            self._create_genesis()
            self._persist_block(self.chain[0])
            self._rebuild_state()
            self._bootstrap_native_asset()
            print("[chain] genese criada.")
        else:
            print(f"[chain] {len(self.chain)} blocos carregados.")
        self.db = BlockchainDB(self)

    # ------------------------------------------------------------------
    # DB
    # ------------------------------------------------------------------
    def _bootstrap_native_asset(self):
        if NATIVE_ASSET in self.registry.assets: return
        self.registry.add_asset(AssetDefinition(
            asset_id=NATIVE_ASSET, name="BRN", symbol="BRN",
            asset_type="currency", decimals=8,
            issuer=NATIVE_ASSET_ISSUER, transfer_agent=NATIVE_ASSET_ISSUER,
            transfer_restricted=False, max_supply=0))
        self._persist_registry()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS blocks (
                idx INTEGER PRIMARY KEY, hash TEXT NOT NULL,
                validator TEXT, timestamp REAL, data TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(hash)")
            conn.execute("""CREATE TABLE IF NOT EXISTS mempool (
                tx_hash TEXT PRIMARY KEY, ts REAL NOT NULL, data TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS slashing (
                validator TEXT PRIMARY KEY, reason TEXT,
                block_idx INTEGER, ts REAL, evidence TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS faucet_claims (
                address TEXT PRIMARY KEY, last_claim REAL NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS finality (
                id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS registry (
                id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)""")
            conn.commit()

    def _persist_block(self, block):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO blocks VALUES (?,?,?,?,?)",
                         (block.index, block.hash, block.miner,
                          block.timestamp, json.dumps(block.to_dict())))
            conn.commit()

    def _persist_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO registry VALUES (1, ?)",
                         (json.dumps(self.registry.to_dict()),))
            conn.commit()

    def _persist_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO finality VALUES (1, ?)",
                         (json.dumps(self.finality.to_dict()),))
            conn.commit()

    def _load_registry(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM registry WHERE id=1").fetchone()
        if row:
            try: self.registry = AssetRegistry.from_dict(json.loads(row[0]))
            except Exception: pass

    def _load_finality(self):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT data FROM finality WHERE id=1").fetchone()
        if row:
            try: self.finality = Finality.from_dict(json.loads(row[0]))
            except Exception: pass

    def _load_slashed(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT validator FROM slashing").fetchall()
        self.slashed = {r[0] for r in rows}

    def _mempool_add(self, tx):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO mempool VALUES (?,?,?)",
                         (Transaction.hash(tx), time.time(), json.dumps(tx)))
            conn.commit()

    def _mempool_remove(self, tx):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool WHERE tx_hash=?",
                         (Transaction.hash(tx),))
            conn.commit()

    def _mempool_clear(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM mempool"); conn.commit()

    def _mempool_load(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT data FROM mempool ORDER BY ts ASC").fetchall()
        self.pending = []
        for (data,) in rows:
            try:
                tx = Transaction.sanitize(json.loads(data))
                if Transaction.verify_signature(tx):
                    self.pending.append(tx)
            except Exception:
                continue

    def _load_from_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT data FROM blocks ORDER BY idx ASC").fetchall()
        except sqlite3.Error:
            return False
        if not rows: return False
        self.chain = [Block.from_dict(json.loads(r[0])) for r in rows]
        self._load_slashed(); self._load_finality(); self._load_registry()
        self._rebuild_state(); self._mempool_load()
        return True

    def _create_genesis(self):
        g = Block(index=0, timestamp=time.time(), previous_hash="0" * 64,
                  transactions=[], miner="genesis",
                  difficulty=POW_INITIAL_DIFFICULTY)
        g.finalize()
        self.chain.append(g)

    @property
    def last_block(self): return self.chain[-1]
    @property
    def height(self): return self.last_block.index
    @property
    def tip_hash(self): return self.last_block.hash
    @property
    def mempool(self): return self.pending

    # ------------------------------------------------------------------
    # Difficulty
    # ------------------------------------------------------------------
    def _next_difficulty(self):
        chain = self.chain
        if len(chain) < POW_ADJUST_INTERVAL or len(chain) % POW_ADJUST_INTERVAL != 0:
            return chain[-1].difficulty
        window = chain[-POW_ADJUST_INTERVAL:]
        elapsed = window[-1].timestamp - window[0].timestamp
        if elapsed <= 0: return chain[-1].difficulty + 1
        expected = POW_TARGET_TIME * (POW_ADJUST_INTERVAL - 1)
        ratio = expected / elapsed
        ratio = max(1 / POW_MAX_ADJUST_FACTOR, min(POW_MAX_ADJUST_FACTOR, ratio))
        return max(POW_MIN_DIFFICULTY, int(chain[-1].difficulty * ratio))

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------
    def _rebuild_state(self):
        self.state = State()
        for addr, alloc in self.genesis_allocations.items():
            for asset_id, amt in alloc.items():
                self.state.credit(addr, asset_id, amt)
        for blk in self.chain[1:]:
            for tx in blk.transactions:
                self._apply_tx_to_state(self.state, tx)
            self.state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
            self._expire_commits(self.state)

    # ==================================================================
    # CLOB
    # ==================================================================
    @staticmethod
    def _pair_id(base: str, quote: str) -> str:
        a, b = sorted([base, quote])
        return f"{a}-{b}"

    @staticmethod
    def _order_id(tx_hash: str) -> str:
        return hashlib.sha3_256(f"order:{tx_hash}".encode()).hexdigest()

    def _get_or_create_book(self, state, pair_id):
        if pair_id not in state.order_books:
            state.order_books[pair_id] = {"bids": [], "asks": []}
        return state.order_books[pair_id]

    def _insert_order_sorted(self, state, book, order, side):
        lst = book["bids"] if side == "buy" else book["asks"]
        if order["order_id"] in lst:
            lst.remove(order["order_id"])
        idx = 0
        for i, oid in enumerate(lst):
            existing = state.orders.get(oid)
            if not existing: continue
            if side == "buy":
                if (existing["price"] < order["price"] or
                    (existing["price"] == order["price"] and
                     existing["created_at"] > order["created_at"])):
                    idx = i; break
            else:
                if (existing["price"] > order["price"] or
                    (existing["price"] == order["price"] and
                     existing["created_at"] > order["created_at"])):
                    idx = i; break
            idx = i + 1
        lst.insert(idx, order["order_id"])

    def _remove_from_book(self, state, book, order):
        lst = book["bids"] if order["side"] == "buy" else book["asks"]
        if order["order_id"] in lst:
            lst.remove(order["order_id"])

    def _clean_book(self, state, book):
        for side in ("bids", "asks"):
            lst = book[side]
            while lst:
                oid = lst[0]
                o = state.orders.get(oid)
                if not o or o["status"] not in ("open", "partial") or o["filled"] >= o["amount"]:
                    lst.pop(0)
                else:
                    break

    def _execute_fill(self, state, buy_order, sell_order, fill_amount, trade_price):
        cost = trade_price * fill_amount
        buyer = buy_order["owner"]
        seller = sell_order["owner"]
        base = buy_order["base"]
        quote = buy_order["quote"]
        buy_price = buy_order["price"]

        state.unfreeze(buyer, quote, buy_price * fill_amount)
        state.debit(buyer, quote, cost)
        state.credit(buyer, base, fill_amount)

        state.unfreeze(seller, base, fill_amount)
        state.debit(seller, base, fill_amount)
        state.credit(seller, quote, cost)

        buy_order["filled"] += fill_amount
        sell_order["filled"] += fill_amount

        tid = hashlib.sha3_256(
            f"{buy_order['order_id']}:{sell_order['order_id']}:{fill_amount}:{time.time()}".encode()
        ).hexdigest()[:32]
        state.trades.append({
            "trade_id": tid,
            "pair": buy_order["pair"],
            "base": base, "quote": quote,
            "price": trade_price, "amount": fill_amount, "cost": cost,
            "buyer": buyer, "seller": seller,
            "buy_order_id": buy_order["order_id"],
            "sell_order_id": sell_order["order_id"],
            "timestamp": time.time(),
        })

    def _run_matching(self, state, new_order):
        pair = new_order["pair"]
        book = self._get_or_create_book(state, pair)
        self._clean_book(state, book)

        if new_order["side"] == "buy":
            while new_order["filled"] < new_order["amount"] and book["asks"]:
                best_id = book["asks"][0]
                best = state.orders.get(best_id)
                if not best or best["status"] not in ("open", "partial"):
                    book["asks"].pop(0); continue
                if best["price"] > new_order["price"]:
                    break
                fill = min(new_order["amount"] - new_order["filled"],
                           best["amount"] - best["filled"])
                if fill <= 0:
                    book["asks"].pop(0); continue
                trade_price = best["price"]
                self._execute_fill(state, new_order, best, fill, trade_price)
                if best["filled"] >= best["amount"]:
                    best["status"] = "filled"
                    book["asks"].pop(0)
                else:
                    best["status"] = "partial"
            if new_order["filled"] < new_order["amount"]:
                new_order["status"] = "partial" if new_order["filled"] > 0 else "open"
                self._insert_order_sorted(state, book, new_order, "buy")
            else:
                new_order["status"] = "filled"
        else:
            while new_order["filled"] < new_order["amount"] and book["bids"]:
                best_id = book["bids"][0]
                best = state.orders.get(best_id)
                if not best or best["status"] not in ("open", "partial"):
                    book["bids"].pop(0); continue
                if best["price"] < new_order["price"]:
                    break
                fill = min(new_order["amount"] - new_order["filled"],
                           best["amount"] - best["filled"])
                if fill <= 0:
                    book["bids"].pop(0); continue
                trade_price = best["price"]
                self._execute_fill(state, best, new_order, fill, trade_price)
                if best["filled"] >= best["amount"]:
                    best["status"] = "filled"
                    book["bids"].pop(0)
                else:
                    best["status"] = "partial"
            if new_order["filled"] < new_order["amount"]:
                new_order["status"] = "partial" if new_order["filled"] > 0 else "open"
                self._insert_order_sorted(state, book, new_order, "sell")
            else:
                new_order["status"] = "filled"

    # ==================================================================
    # Operações CLOB
    # ==================================================================
    def _op_order_place(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]
        base = tx["asset_id"]
        quote = md.get("quote", "")
        side = md.get("side", "").lower()
        price = float(md.get("price", 0))
        amount = float(tx["amount"])

        if side not in ("buy", "sell"): return False
        if not quote or quote == base: return False
        if price <= 0 or amount <= 0: return False
        if base not in self.registry.assets: return False
        if quote not in self.registry.assets: return False

        pair = self._pair_id(base, quote)

        if side == "buy":
            if state.available(owner, quote) < price * amount:
                return False
            state.freeze(owner, quote, price * amount)
        else:
            if state.available(owner, base) < amount:
                return False
            state.freeze(owner, base, amount)

        tx_hash = Transaction.hash(tx)
        order_id = self._order_id(tx_hash)
        order = {
            "order_id": order_id,
            "owner": owner,
            "pair": pair,
            "base": base, "quote": quote,
            "side": side,
            "price": price,
            "amount": amount,
            "filled": 0.0,
            "created_at": tx["timestamp"],
            "status": "open",
        }
        state.orders[order_id] = order
        self._run_matching(state, order)
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _op_order_cancel(self, state, tx):
        md = tx.get("metadata", {})
        order_id = md.get("order_id", "")
        owner = tx["from"]
        order = state.orders.get(order_id)
        if not order: return False
        if order["owner"] != owner: return False
        if order["status"] not in ("open", "partial"): return False

        remaining = order["amount"] - order["filled"]
        if remaining <= 0:
            order["status"] = "filled"; return False

        if order["side"] == "buy":
            state.unfreeze(owner, order["quote"], order["price"] * remaining)
        else:
            state.unfreeze(owner, order["base"], remaining)

        order["status"] = "cancelled"
        book = state.order_books.get(order["pair"])
        if book:
            self._remove_from_book(state, book, order)
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    # ==================================================================
    # MEV — commit / reveal
    # ==================================================================
    def _commit_hash(self, side, base, quote, price, amount, salt, owner):
        payload = f"{side}:{base}:{quote}:{price}:{amount}:{salt}:{owner}"
        return hashlib.sha3_256(payload.encode()).hexdigest()

    def _op_order_commit(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]
        commit_hash = md.get("commit_hash", "")
        side = md.get("side", "").lower()
        base = md.get("base", "")
        quote = md.get("quote", "")
        frozen_amount = float(tx["amount"])

        if side not in ("buy", "sell"): return False
        if len(commit_hash) != 64: return False
        if not base or not quote or base == quote: return False
        if base not in self.registry.assets: return False
        if quote not in self.registry.assets: return False
        if commit_hash in state.commits: return False
        if frozen_amount <= 0: return False

        pending = [c for c in state.commits.values()
                   if c["owner"] == owner and c["status"] == "pending"]
        if len(pending) >= MEV_MAX_PENDING_COMMITS: return False

        if side == "buy":
            if state.available(owner, quote) < frozen_amount: return False
            state.freeze(owner, quote, frozen_amount)
            frozen_asset = quote
        else:
            if state.available(owner, base) < frozen_amount: return False
            state.freeze(owner, base, frozen_amount)
            frozen_asset = base

        cur_block = self.last_block.index + 1
        state.commits[commit_hash] = {
            "commit_hash": commit_hash,
            "owner": owner,
            "side": side,
            "base": base, "quote": quote,
            "frozen_asset": frozen_asset,
            "frozen_amount": frozen_amount,
            "created_block": cur_block,
            "created_at": tx["timestamp"],
            "status": "pending",
            "order_id": None,
        }
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _op_order_reveal(self, state, tx):
        md = tx.get("metadata", {})
        owner = tx["from"]
        commit_hash = md.get("commit_hash", "")
        salt = md.get("salt", "")
        base = tx["asset_id"]
        quote = md.get("quote", "")
        side = md.get("side", "").lower()
        price = float(md.get("price", 0))
        amount = float(tx["amount"])

        c = state.commits.get(commit_hash)
        if not c or c["status"] != "pending": return False
        if c["owner"] != owner: return False
        if c["side"] != side: return False
        if c["base"] != base or c["quote"] != quote: return False
        if price <= 0 or amount <= 0: return False

        cur_block = self.last_block.index + 1
        delta = cur_block - c["created_block"]
        if delta < MEV_COMMIT_MIN_BLOCKS: return False
        if delta > MEV_COMMIT_MAX_BLOCKS: return False

        expected = self._commit_hash(side, base, quote, price, amount, salt, owner)
        if expected != commit_hash: return False

        required = price * amount if side == "buy" else amount
        if required > c["frozen_amount"]: return False

        state.unfreeze(owner, c["frozen_asset"], c["frozen_amount"])
        if side == "buy":
            if state.available(owner, quote) < price * amount: return False
            state.freeze(owner, quote, price * amount)
        else:
            if state.available(owner, base) < amount: return False
            state.freeze(owner, base, amount)

        pair = self._pair_id(base, quote)
        order_id = self._order_id(Transaction.hash(tx))
        order = {
            "order_id": order_id,
            "owner": owner,
            "pair": pair,
            "base": base, "quote": quote,
            "side": side,
            "price": price, "amount": amount, "filled": 0.0,
            "created_at": tx["timestamp"],
            "status": "open",
            "commit_hash": commit_hash,
        }
        state.orders[order_id] = order
        self._run_matching(state, order)

        c["status"] = "revealed"
        c["order_id"] = order_id
        state.nonces[owner] = state.nonce(owner) + 1
        return True

    def _expire_commits(self, state):
        cur_block = self.last_block.index + 1
        for ch, c in list(state.commits.items()):
            if c["status"] != "pending": continue
            if cur_block - c["created_block"] > MEV_COMMIT_MAX_BLOCKS:
                state.unfreeze(c["owner"], c["frozen_asset"], c["frozen_amount"])
                c["status"] = "expired"

    # ==================================================================
    # Aplicação de tx
    # ==================================================================
    def _apply_tx_to_state(self, state, tx):
        t = tx["type"]
        sender, receiver = tx["from"], tx["to"]
        asset_id = tx["asset_id"]
        amount = float(tx["amount"])

        if t == "transfer":
            if state.available(sender, asset_id) < amount: return False
            state.debit(sender, asset_id, amount)
            state.credit(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True
        if t == "issue":
            a = self.registry.get_asset(asset_id)
            if not a: return False
            if a.max_supply > 0 and state.total_supply.get(asset_id, 0) + amount > a.max_supply:
                return False
            state.credit(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True
        if t == "redeem":
            if state.available(sender, asset_id) < amount: return False
            state.debit(sender, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True
        if t in ("freeze", "unfreeze"):
            if t == "freeze":
                if state.available(receiver, asset_id) < amount: return False
                state.freeze(receiver, asset_id, amount)
            else:
                state.unfreeze(receiver, asset_id, amount)
            state.nonces[sender] = state.nonce(sender) + 1
            return True
        if t in ("kyc_register","kyc_revoke","asset_create","asset_update","dividend"):
            state.nonces[sender] = state.nonce(sender) + 1
            return True
        if t == "order_place":  return self._op_order_place(state, tx)
        if t == "order_cancel": return self._op_order_cancel(state, tx)
        if t == "order_commit": return self._op_order_commit(state, tx)
        if t == "order_reveal": return self._op_order_reveal(state, tx)
        return False

    def _apply_registry_ops(self, tx, persist=True):
        t = tx["type"]; md = tx.get("metadata", {})
        if t == "asset_create":
            a = AssetDefinition(
                asset_id=md["asset_id"], name=md.get("name", md["asset_id"]),
                symbol=md.get("symbol", md["asset_id"]),
                asset_type=md["asset_type"], decimals=int(md.get("decimals", 2)),
                issuer=md["issuer"],
                transfer_agent=md.get("transfer_agent", md["issuer"]),
                custodian=md.get("custodian", ""), isin=md.get("isin", ""),
                max_supply=float(md.get("max_supply", 0)),
                transfer_restricted=bool(md.get("transfer_restricted", True)),
                legal_doc_hash=md.get("legal_doc_hash", ""),
                metadata=md.get("metadata", {}))
            self.registry.add_asset(a)
            if persist: self._persist_registry()
        elif t == "asset_update":
            self.registry.update_asset(tx["asset_id"], md)
            if persist: self._persist_registry()
        elif t == "kyc_register":
            rec = ComplianceRecord(
                address=tx["to"], status=md.get("status", "approved"),
                level=md.get("level", "basic"),
                jurisdiction=md.get("jurisdiction", ""),
                verified_by=tx["from"], verified_at=time.time(),
                expires_at=float(md.get("expires_at", 0)),
                restrictions=list(md.get("restrictions", [])),
                metadata=md.get("extra", {}))
            self.registry.set_compliance(rec)
            if persist: self._persist_registry()
        elif t == "kyc_revoke":
            rec = self.registry.get_compliance(tx["to"])
            if rec:
                rec.status = "revoked"
                if persist: self._persist_registry()

    # ==================================================================
    # Validação
    # ==================================================================
    def _validate_tx(self, tx, state=None):
        state = state or self.state
        t = tx["type"]; asset_id = tx["asset_id"]
        sender, receiver = tx["from"], tx["to"]
        amount = float(tx["amount"]); md = tx.get("metadata", {})

        if t == "transfer":
            if asset_id not in self.registry.assets:
                return False, f"ativo '{asset_id}' nao registrado"
            recv_after = state.balance(receiver, asset_id) + amount
            return self.registry.validate_transfer(asset_id, sender, receiver,
                                                    amount, recv_after)
        if t == "issue":
            if not self.registry.can_issue(asset_id, sender):
                return False, "nao e emissor"
            a = self.registry.get_asset(asset_id)
            if a.max_supply > 0 and state.total_supply.get(asset_id, 0) + amount > a.max_supply:
                return False, "max_supply excedido"
            return True, "ok"
        if t == "redeem":
            a = self.registry.get_asset(asset_id)
            if not a: return False, "ativo inexistente"
            if sender not in (a.issuer, a.transfer_agent):
                return False, "sem permissao"
            if state.available(receiver, asset_id) < amount:
                return False, "saldo insuficiente"
            return True, "ok"
        if t in ("freeze", "unfreeze"):
            if not self.registry.can_freeze(asset_id, sender):
                return False, "sem permissao"
            return True, "ok"
        if t == "kyc_register":
            if not self.registry.can_manage_kyc(asset_id, sender):
                return False, "sem permissao KYC"
            return True, "ok"
        if t == "kyc_revoke":
            if not self.registry.can_manage_kyc(asset_id, sender):
                return False, "sem permissao KYC"
            return True, "ok"
        if t == "asset_create":
            new_id = md.get("asset_id", "")
            if not new_id or new_id in self.registry.assets:
                return False, "asset_id invalido"
            if md.get("asset_type") not in ASSET_TYPES:
                return False, "asset_type invalido"
            if md.get("issuer") != sender:
                return False, "issuer != sender"
            return True, "ok"
        if t == "asset_update":
            if not self.registry.can_issue(asset_id, sender):
                return False, "so emissor atualiza"
            return True, "ok"
        if t == "dividend":
            if not self.registry.can_issue(asset_id, sender):
                return False, "so emissor paga dividendos"
            if state.available(sender, NATIVE_ASSET) < amount:
                return False, "sem saldo BRN"
            return True, "ok"

        if t == "order_place":
            quote = md.get("quote", "")
            side = md.get("side", "").lower()
            price = float(md.get("price", 0))
            if side not in ("buy", "sell"): return False, "side invalido"
            if not quote or quote == asset_id: return False, "quote invalido"
            if price <= 0: return False, "price invalido"
            if amount <= 0: return False, "amount invalido"
            if asset_id not in self.registry.assets:
                return False, f"'{asset_id}' nao registrado"
            if quote not in self.registry.assets:
                return False, f"'{quote}' nao registrado"
            if side == "buy":
                if state.available(sender, quote) < price * amount:
                    return False, "saldo quote insuficiente"
            else:
                if state.available(sender, asset_id) < amount:
                    return False, "saldo base insuficiente"
            return True, "ok"
        if t == "order_cancel":
            oid = md.get("order_id", "")
            o = state.orders.get(oid)
            if not o: return False, "ordem inexistente"
            if o["owner"] != sender: return False, "ordem nao e sua"
            if o["status"] not in ("open", "partial"):
                return False, f"ordem {o['status']}"
            return True, "ok"

        if t == "order_commit":
            ch = md.get("commit_hash", "")
            side = md.get("side", "").lower()
            base = md.get("base", "")
            quote = md.get("quote", "")
            if side not in ("buy", "sell"): return False, "side invalido"
            if len(ch) != 64: return False, "commit_hash invalido"
            if not base or not quote or base == quote: return False, "par invalido"
            if base not in self.registry.assets: return False, f"'{base}' nao registrado"
            if quote not in self.registry.assets: return False, f"'{quote}' nao registrado"
            if ch in state.commits: return False, "commit duplicado"
            frozen = float(tx["amount"])
            if frozen <= 0: return False, "valor invalido"
            if side == "buy":
                if state.available(sender, quote) < frozen:
                    return False, "saldo quote insuficiente"
            else:
                if state.available(sender, base) < frozen:
                    return False, "saldo base insuficiente"
            return True, "ok"

        if t == "order_reveal":
            ch = md.get("commit_hash", "")
            c = state.commits.get(ch)
            if not c: return False, "commit inexistente"
            if c["owner"] != sender: return False, "commit nao e seu"
            if c["status"] != "pending": return False, f"commit {c['status']}"
            delta = (self.last_block.index + 1) - c["created_block"]
            if delta < MEV_COMMIT_MIN_BLOCKS:
                return False, f"aguarde {MEV_COMMIT_MIN_BLOCKS - delta} blocos"
            if delta > MEV_COMMIT_MAX_BLOCKS:
                return False, "commit expirado"
            side = md.get("side", "").lower()
            if side != c["side"]: return False, "side nao bate"
            if tx["asset_id"] != c["base"]: return False, "base nao bate"
            if md.get("quote", "") != c["quote"]: return False, "quote nao bate"
            price = float(md.get("price", 0)); amt = float(tx["amount"])
            if price <= 0 or amt <= 0: return False, "valores invalidos"
            required = price * amt if side == "buy" else amt
            if required > c["frozen_amount"]:
                return False, "valor maior que o commitado"
            expected = self._commit_hash(side, c["base"], c["quote"],
                                          price, amt, md.get("salt", ""), sender)
            if expected != ch: return False, "hash nao corresponde"
            return True, "ok"

        return False, "tipo nao suportado"

    def add_transaction(self, tx):
        tx = Transaction.sanitize(tx)
        with self.lock:
            if not Transaction.verify_signature(tx):
                return {"ok": False, "msg": "Assinatura invalida."}
            if Transaction.derived_address(tx) != tx["from"]:
                return {"ok": False, "msg": "Endereco != chave publica."}
            if tx["from"] in self.slashed:
                return {"ok": False, "msg": "Remetente banido."}
            if tx["nonce"] != self.state.nonce(tx["from"]):
                return {"ok": False,
                        "msg": f"Nonce invalido (esperado {self.state.nonce(tx['from'])})."}
            if tx["type"] not in Transaction.VALID_TYPES:
                return {"ok": False, "msg": "Tipo desconhecido."}
            ok, why = self._validate_tx(tx)
            if not ok: return {"ok": False, "msg": why}
            for p in self.pending:
                if p["from"] == tx["from"] and p["nonce"] == tx["nonce"]:
                    return {"ok": False, "msg": "Duplicada."}
            self.pending.append(tx)
            self._mempool_add(tx)
            return {"ok": True, "msg": "Aceita.", "tx_hash": Transaction.hash(tx)}

    def submit_tx(self, tx):
        r = self.add_transaction(tx)
        return r.get("ok", False), r.get("msg", "")

    # ==================================================================
    # Mineração
    # ==================================================================
    def produce_block(self):
        with self.lock:
            if not self.node_identity: return None
            if self.node_identity["address"] in self.slashed: return None

            temp = self.state.copy()
            self._expire_commits(temp)

            real_reg = self.registry
            self.registry = AssetRegistry.from_dict(self.registry.to_dict())
            chosen = []
            try:
                for tx in sorted(self.pending,
                                 key=lambda t: (t["timestamp"], Transaction.hash(t))):
                    if not Transaction.verify_signature(tx): continue
                    if tx["nonce"] != temp.nonce(tx["from"]): continue
                    ok, _ = self._validate_tx(tx, state=temp)
                    if not ok: continue
                    if not self._apply_tx_to_state(temp, tx): continue
                    self._apply_registry_ops(tx, persist=False)
                    chosen.append(tx)
            finally:
                self.registry = real_reg

            difficulty = self._next_difficulty()
            block = Block(
                index=self.last_block.index + 1, timestamp=time.time(),
                previous_hash=self.last_block.hash, transactions=chosen,
                miner=self.node_identity["address"], difficulty=difficulty,
                miner_public_key=self.node_identity["public_key"],
                registry_snapshot=self.registry.to_dict())

            print(f"[miner] bloco #{block.index} diff={difficulty} txs={len(chosen)}")
            block.mine()
            print(f"[miner] OK nonce={block.nonce} hash={block.hash[:16]}...")

            try: block.sign(self.node_identity["spend_secret_key"])
            except Exception: pass
            if not block.verify(): return None

            for tx in chosen:
                self._apply_tx_to_state(self.state, tx)
                self._apply_registry_ops(tx, persist=True)
                self._mempool_remove(tx)
            self.state.credit(block.miner, NATIVE_ASSET, BLOCK_REWARD)
            self.pending = [t for t in self.pending if t not in chosen]
            self._expire_commits(self.state)

            self.chain.append(block)
            self._persist_block(block)
            if self.finality.update_by_depth(block.index):
                self._persist_finality()
            return block

    # ==================================================================
    # Aceitação
    # ==================================================================
    def accept_block(self, block):
        if isinstance(block, Block): block = block.to_dict()
        try: blk = Block.from_dict(block)
        except Exception as e: return False, f"desserializar: {e}"

        with self.lock:
            prev = self.last_block
            if blk.hash == prev.hash: return True, "ja temos"
            if blk.index <= prev.index: return False, "altura antiga"
            if blk.previous_hash != prev.hash or blk.index != prev.index + 1:
                return False, "fora de sequencia"
            if not blk.verify():
                self._slash(blk.miner, f"PoW invalida #{blk.index}", block_idx=blk.index)
                return False, "PoW invalida"
            if blk.difficulty != self._next_difficulty():
                return False, f"dificuldade errada ({blk.difficulty})"

            sanitized = [Transaction.sanitize(tx) for tx in blk.transactions]
            temp = self.state.copy()
            self._expire_commits(temp)
            real_reg = self.registry
            self.registry = AssetRegistry.from_dict(self.registry.to_dict())
            try:
                for tx in sanitized:
                    if not Transaction.verify_signature(tx): return False, "assinatura tx"
                    if tx["nonce"] != temp.nonce(tx["from"]): return False, "nonce tx"
                    ok, why = self._validate_tx(tx, state=temp)
                    if not ok: return False, why
                    if not self._apply_tx_to_state(temp, tx): return False, "apply tx"
                    self._apply_registry_ops(tx, persist=False)
                committed_reg = self.registry
            finally:
                self.registry = real_reg

            for tx in sanitized:
                self._apply_tx_to_state(self.state, tx)
                self._apply_registry_ops(tx, persist=True)
                self._mempool_remove(tx)
            self.state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
            hashes = {Transaction.hash(tx) for tx in sanitized}
            self.pending = [t for t in self.pending if Transaction.hash(t) not in hashes]
            self._expire_commits(self.state)

            self.chain.append(blk)
            self._persist_block(blk)
            self.registry = committed_reg
            self._persist_registry()
            if self.finality.update_by_depth(blk.index):
                self._persist_finality()
            print(f"[p2p] bloco #{blk.index} aceito ({blk.hash[:12]}...)")
            return True, "ok"

    # ==================================================================
    # Chain checks
    # ==================================================================
    def _find_invalid_block(self, chain):
        for i, blk in enumerate(chain):
            if not blk.verify(): return blk
            if i == 0: continue
            prev = chain[i - 1]
            if blk.previous_hash != prev.hash or blk.index != prev.index + 1:
                return blk
        return None

    def _chainwork(self, chain):
        return sum(2 ** max(1, b.difficulty) for b in chain)
    def _fork_score(self, chain): return self._chainwork(chain)

    def _replace_all_in_db(self, blocks):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM blocks")
            for blk in blocks:
                conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?)",
                             (blk.index, blk.hash, blk.miner,
                              blk.timestamp, json.dumps(blk.to_dict())))
            conn.commit()

    def replace_chain(self, new_chain):
        try:
            blocks = [Block.from_dict(b) if isinstance(b, dict) else b for b in new_chain]
        except Exception as e:
            print(f"[chain] erro desserializar: {e}"); return False
        with self.lock:
            if len(blocks) <= self.finality.finalized_height: return False
            for i in range(min(self.finality.finalized_height + 1, len(self.chain))):
                if i >= len(blocks) or blocks[i].hash != self.chain[i].hash:
                    return False
            invalid = self._find_invalid_block(blocks)
            if invalid:
                self._slash(invalid.miner, f"bloco invalido #{invalid.index}",
                            block_idx=invalid.index)
                return False
            if self._fork_score(blocks) < self._fork_score(self.chain): return False
            if self._fork_score(blocks) == self._fork_score(self.chain) and \
               len(blocks) <= len(self.chain): return False

            new_state = State()
            new_reg = AssetRegistry()
            new_reg.regulator = self.registry.regulator
            new_reg.add_asset(AssetDefinition(
                asset_id=NATIVE_ASSET, name="BRN", symbol="BRN",
                asset_type="currency", decimals=8,
                issuer=NATIVE_ASSET_ISSUER, transfer_agent=NATIVE_ASSET_ISSUER,
                transfer_restricted=False))
            for addr, alloc in self.genesis_allocations.items():
                for asset_id, amt in alloc.items():
                    new_state.credit(addr, asset_id, amt)

            real_reg = self.registry
            self.registry = new_reg
            try:
                for blk in blocks[1:]:
                    for raw_tx in blk.transactions:
                        tx = Transaction.sanitize(raw_tx)
                        if not Transaction.verify_signature(tx): return False
                        if not self._apply_tx_to_state(new_state, tx): return False
                        self._apply_registry_ops(tx, persist=False)
                    new_state.credit(blk.miner, NATIVE_ASSET, BLOCK_REWARD)
                    # expira commits após cada bloco
                    cur = blk.index
                    for ch, c in list(new_state.commits.items()):
                        if c["status"] != "pending": continue
                        if cur - c["created_block"] > MEV_COMMIT_MAX_BLOCKS:
                            new_state.unfreeze(c["owner"], c["frozen_asset"],
                                               c["frozen_amount"])
                            c["status"] = "expired"
                committed_reg = self.registry
            except Exception as e:
                print(f"[chain] erro replay: {e}"); return False
            finally:
                self.registry = real_reg

            self.chain = blocks
            self.state = new_state
            self.registry = committed_reg
            self._persist_registry()
            self._replace_all_in_db(blocks)

            confirmed = {Transaction.hash(tx) for blk in blocks for tx in blk.transactions}
            self.pending = [t for t in self.pending if Transaction.hash(t) not in confirmed]
            self._mempool_clear()
            for t in self.pending: self._mempool_add(t)
            print(f"[chain] substituida -> altura {len(blocks)}")
            return True

    # ==================================================================
    # Slashing / Faucet
    # ==================================================================
    def _slash(self, validator, reason, block_idx=-1, evidence=None):
        if not validator or validator == "genesis": return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO slashing VALUES (?,?,?,?,?)",
                         (validator, reason, block_idx, time.time(),
                          json.dumps(evidence.to_dict()) if evidence else None))
            conn.commit()
        self.slashed.add(validator)
        if validator in self.state.balances:
            self.state.balances[validator] = {}
        self.state.nonces[validator] = self.state.nonce(validator) + 1
        print(f"[slash] {validator[:16]}... banido ({reason})")
        return True

    def faucet(self, to_address, private_key_hex, public_key_hex):
        if not FAUCET_ADDRESS: return {"ok": False, "msg": "Faucet desativado."}
        if FAUCET_ADDRESS != WalletManager.address_from_public_key(public_key_hex):
            return {"ok": False, "msg": "Chave nao corresponde."}
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT last_claim FROM faucet_claims WHERE address=?",
                               (to_address,)).fetchone()
        if row and (now - row[0]) < FAUCET_COOLDOWN:
            return {"ok": False, "msg": f"Aguarde {int(FAUCET_COOLDOWN-(now-row[0]))}s."}
        tx = Transaction.build(
            tx_type="transfer", asset_id=NATIVE_ASSET,
            sender_address=FAUCET_ADDRESS, receiver_address=to_address,
            amount=FAUCET_AMOUNT, nonce=self.state.nonce(FAUCET_ADDRESS),
            private_key_hex=private_key_hex, public_key_hex=public_key_hex)
        r = self.add_transaction(tx)
        if not r.get("ok"): return r
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO faucet_claims VALUES (?,?)",
                         (to_address, now))
            conn.commit()
        return {"ok": True, "msg": f"{FAUCET_AMOUNT} BRN enfileirados.", "tx": tx}

    # ==================================================================
    # APIs para carteira
    # ==================================================================
    def order_book(self, base: str, quote: str, depth: int = 10) -> dict:
        pair = self._pair_id(base, quote)
        book = self.state.order_books.get(pair, {"bids": [], "asks": []})
        bids, asks = [], []
        for oid in book["bids"][:depth]:
            o = self.state.orders.get(oid)
            if o and o["status"] in ("open", "partial"):
                bids.append({"price": o["price"],
                             "amount": o["amount"] - o["filled"],
                             "owner": o["owner"][:12] + "..."})
        for oid in book["asks"][:depth]:
            o = self.state.orders.get(oid)
            if o and o["status"] in ("open", "partial"):
                asks.append({"price": o["price"],
                             "amount": o["amount"] - o["filled"],
                             "owner": o["owner"][:12] + "..."})
        best_bid = bids[0]["price"] if bids else 0.0
        best_ask = asks[0]["price"] if asks else 0.0
        spread = (best_ask - best_bid) if (best_bid and best_ask) else 0.0
        mid = (best_bid + best_ask) / 2 if (best_bid and best_ask) else 0.0
        return {"pair": pair, "bids": bids, "asks": asks,
                "best_bid": best_bid, "best_ask": best_ask,
                "spread": spread, "mid": mid}

    def my_orders(self, addr: str, status_in=None) -> list:
        status_in = status_in or ("open", "partial")
        out = []
        for o in self.state.orders.values():
            if o["owner"] != addr: continue
            if o["status"] not in status_in: continue
            out.append({
                "order_id": o["order_id"],
                "pair": o["pair"], "side": o["side"],
                "price": o["price"], "amount": o["amount"],
                "filled": o["filled"],
                "remaining": o["amount"] - o["filled"],
                "status": o["status"],
                "created_at": o["created_at"],
            })
        out.sort(key=lambda x: x["created_at"], reverse=True)
        return out

    def my_trades(self, addr: str, limit: int = 50) -> list:
        out = []
        for t in self.state.trades:
            if t["buyer"] == addr or t["seller"] == addr:
                role = "buy" if t["buyer"] == addr else "sell"
                out.append({**t, "role": role})
        out.sort(key=lambda x: x["timestamp"], reverse=True)
        return out[:limit]

    def my_commits(self, addr: str, status_in=None) -> list:
        status_in = status_in or ("pending", "revealed", "expired")
        cur_block = self.last_block.index
        out = []
        for c in self.state.commits.values():
            if c["owner"] != addr: continue
            if c["status"] not in status_in: continue
            elapsed = cur_block - c["created_block"]
            out.append({
                "commit_hash": c["commit_hash"][:16] + "...",
                "commit_hash_full": c["commit_hash"],
                "side": c["side"],
                "base": c["base"], "quote": c["quote"],
                "frozen_asset": c["frozen_asset"],
                "frozen_amount": c["frozen_amount"],
                "created_block": c["created_block"],
                "blocks_elapsed": elapsed,
                "blocks_until_ready": max(0, MEV_COMMIT_MIN_BLOCKS - elapsed),
                "blocks_until_expire": max(0, MEV_COMMIT_MAX_BLOCKS - elapsed),
                "status": c["status"],
                "order_id": c["order_id"],
            })
        out.sort(key=lambda x: x["created_block"], reverse=True)
        return out

    def portfolio(self, addr):
        out = {}
        for asset_id, amount in self.state.balances.get(addr, {}).items():
            if amount == 0: continue
            a = self.registry.get_asset(asset_id)
            out[asset_id] = {
                "amount": amount,
                "available": self.state.available(addr, asset_id),
                "frozen": self.state.frozen.get(addr, {}).get(asset_id, 0.0),
                "asset": a.to_dict() if a else None,
            }
        return out

    def place_order(self, identity: dict, base: str, quote: str,
                    side: str, price: float, amount: float) -> dict:
        addr = identity["address"]
        tx = Transaction.build(
            tx_type="order_place",
            asset_id=base,
            sender_address=addr,
            receiver_address=addr,
            amount=amount,
            nonce=self.state.nonce(addr),
            private_key_hex=identity["spend_secret_key"],
            public_key_hex=identity["public_key"],
            metadata={"quote": quote, "side": side, "price": float(price)})
        return self.add_transaction(tx)

    def cancel_order(self, identity: dict, order_id: str) -> dict:
        addr = identity["address"]
        tx = Transaction.build(
            tx_type="order_cancel",
            asset_id=NATIVE_ASSET,
            sender_address=addr,
            receiver_address=addr,
            amount=0,
            nonce=self.state.nonce(addr),
            private_key_hex=identity["spend_secret_key"],
            public_key_hex=identity["public_key"],
            metadata={"order_id": order_id})
        return self.add_transaction(tx)

    def commit_order(self, identity: dict, base: str, quote: str, side: str,
                     price: float, amount: float, salt: str = None) -> dict:
        if salt is None:
            salt = secrets.token_hex(16)
        addr = identity["address"]
        frozen = (price * amount) if side == "buy" else amount
        ch = self._commit_hash(side, base, quote, price, amount, salt, addr)
        tx = Transaction.build(
            tx_type="order_commit",
            asset_id=base,
            sender_address=addr,
            receiver_address=addr,
            amount=frozen,
            nonce=self.state.nonce(addr),
            private_key_hex=identity["spend_secret_key"],
            public_key_hex=identity["public_key"],
            metadata={"commit_hash": ch, "side": side,
                      "base": base, "quote": quote})
        r = self.add_transaction(tx)
        if r.get("ok"):
            r["commit_hash"] = ch
            r["salt"] = salt
            r["price"] = price
            r["amount"] = amount
            r["side"] = side
            r["base"] = base
            r["quote"] = quote
        return r

    def reveal_order(self, identity: dict, commit_hash: str, salt: str,
                     base: str, quote: str, side: str,
                     price: float, amount: float) -> dict:
        addr = identity["address"]
        tx = Transaction.build(
            tx_type="order_reveal",
            asset_id=base,
            sender_address=addr,
            receiver_address=addr,
            amount=amount,
            nonce=self.state.nonce(addr),
            private_key_hex=identity["spend_secret_key"],
            public_key_hex=identity["public_key"],
            metadata={"commit_hash": commit_hash, "salt": salt,
                      "side": side, "quote": quote, "price": price})
        return self.add_transaction(tx)

    def slashing_report(self):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT validator, reason, block_idx, ts, evidence "
                                "FROM slashing ORDER BY ts DESC").fetchall()
        return [{"validator": r[0], "reason": r[1], "block_index": r[2],
                 "timestamp": r[3],
                 "evidence": json.loads(r[4]) if r[4] else None} for r in rows]

    def finality_report(self):
        return {"finalized_height": self.finality.finalized_height,
                "interval": self.finality.interval,
                "threshold": self.finality.threshold}
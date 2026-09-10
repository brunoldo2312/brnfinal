import hashlib
import time
import json
import os
import sqlite3
import secrets
import socket
import threading
import sys
from decimal import Decimal, InvalidOperation
import webview
from cripto_wallet import WalletManager
from cripto_db import BlockchainDB


def _harden_console_encoding():
    """Evita que emojis nos logs derrubem threads em consoles Windows (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


_harden_console_encoding()

COIN_NAME = "Bruno"
COIN_SYMBOL = "BRN"
BLOCK_REWARD = 1.0
GENESIS_ADDRESS = "brn1111cd943fa71e1f91dcd62f52fc6138bc845ab"
GENESIS_AMOUNT = 100000.0
GENESIS_TIMESTAMP = 1700000000.0
GENESIS_NONCE = 171419
GENESIS_HASH = "0000d904d5f2954d5c9aa43eafed2f885ad17c5047605ed6e385458b590fcaff"
GENESIS_DIFFICULTY = 4
DIFFICULTY_ADJUSTMENT_INTERVAL = 5
TARGET_BLOCK_TIME = 10.0
MAX_DIFFICULTY = 8
MAX_BLOCK_TRANSACTIONS = 1_000
MAX_FUTURE_SECONDS = 120
MONERO_FORK_NETWORK_ID = [0xAA, 0xBB, 0xCC, 0xDD, 0x11, 0x22, 0x33, 0x44]
DISCOVERY_UDP_PORT = 6010
DISCOVERY_INTERVAL_SECONDS = 5

BOOTSTRAP_PEERS_FILE = "bootstrap_peers.json"
DEFAULT_BOOTSTRAP_PEERS = [
    ("192.168.0.17", 6001)
]


def _load_bootstrap_peers():
    try:
        with open(BOOTSTRAP_PEERS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        peers = [(str(item["ip"]).strip(), int(item["port"])) for item in raw]
        peers = [(ip, port) for ip, port in peers if ip]
        return peers or list(DEFAULT_BOOTSTRAP_PEERS)
    except (OSError, ValueError, KeyError, TypeError):
        return list(DEFAULT_BOOTSTRAP_PEERS)


BOOTSTRAP_PEERS = _load_bootstrap_peers()

class BrunoBlock:
    def __init__(self, index, previous_hash, transactions, difficulty=4, nonce=0, timestamp=None, block_hash=None):
        self.index = int(index)
        self.timestamp = float(timestamp) if timestamp is not None else time.time()
        self.previous_hash = str(previous_hash)
        self.transactions = transactions if isinstance(transactions, list) else json.loads(transactions)
        self.difficulty = int(difficulty)
        self.nonce = int(nonce)
        self.network_id = MONERO_FORK_NETWORK_ID
        self.hash = str(block_hash) if block_hash else self.calculate_hash()

    def calculate_hash(self) -> str:
        block_string = json.dumps({
            "index": self.index, "timestamp": self.timestamp, "previous_hash": self.previous_hash,
            "transactions": self.transactions, "difficulty": self.difficulty, "nonce": self.nonce, "network_id": self.network_id
        }, sort_keys=True).encode()
        return hashlib.sha256(block_string).hexdigest()

    def mine_block(self, stop_event=None):
        target = "0" * self.difficulty
        while self.hash[:self.difficulty] != target:
            if stop_event and stop_event.is_set():
                return False
            self.nonce += 1
            self.hash = self.calculate_hash()
        return True

    def to_dict(self):
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "transactions": self.transactions,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
            "hash": self.hash
        }

class CriptoAPI:
    def __init__(self, node_port):
        self.p2p_port = node_port
        self.db_path = f"blockchain_node_{node_port}.db"
        self.mempool_path = f"mempool_node_{node_port}.json"
        self.db = BlockchainDB(self.db_path)
        self.mempool = []
        self.mempool_lock = threading.Lock()
        self.orphan_txs = []
        self.orphan_lock = threading.Lock()
        self.orphan_path = f"mempool_node_{node_port}.orphan.json"
        self.connected_peers = set()
        self.peers_lock = threading.Lock()
        self.chain_lock = threading.RLock()
        
        self.is_mining = False
        self.mining_stop_event = threading.Event()
        self.miner_thread = None
        
        self._init_database()
        self._load_mempool()
        self._load_orphans()
        
        self.server_thread = threading.Thread(target=self._start_p2p_server, daemon=True)
        self.server_thread.start()
        
        threading.Thread(target=self._run_bootstrap_discovery, daemon=True).start()
        threading.Thread(target=self._peer_maintenance_loop, daemon=True).start()
        threading.Thread(target=self._start_discovery_listener, daemon=True).start()
        threading.Thread(target=self._discovery_announce_loop, daemon=True).start()

    def _init_database(self):
        self._converge_to_shared_genesis()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS blocks (
                    id_index INTEGER PRIMARY KEY, timestamp REAL, previous_hash TEXT,
                    transactions TEXT, difficulty INTEGER, nonce INTEGER, hash TEXT
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM blocks')
            if cursor.fetchone()[0] == 0:
                genesis = self._make_genesis()
                self.db.insert_block(genesis)

    def _converge_to_shared_genesis(self):
        if not os.path.exists(self.db_path):
            return
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='blocks'")
            if not cursor.fetchone():
                return
            cursor.execute('SELECT hash FROM blocks WHERE id_index = 0')
            row = cursor.fetchone()
            if not row:
                return
            if str(row[0]) == GENESIS_HASH:
                return
        except sqlite3.Error:
            return
        finally:
            if conn is not None:
                conn.close()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{self.db_path}.divergent-{stamp}.bak"
        try:
            os.replace(self.db_path, backup)
            print(f"🔁 Gênese divergente detectada. Banco antigo salvo em {backup}.")
            for extra in (self.mempool_path, self.orphan_path):
                if os.path.exists(extra):
                    os.replace(extra, f"{extra}.divergent-{stamp}.bak")
            print("✅ Nó reiniciado na gênese padrão da rede — agora compartilha a MESMA blockchain.")
        except OSError as e:
            print(f"⚠️ Não foi possível convergir a gênese automaticamente: {e}")

    @staticmethod
    def _make_genesis():
        return BrunoBlock(
            0, "0",
            [{"sender": "SISTEMA", "receiver": GENESIS_ADDRESS, "amount": GENESIS_AMOUNT}],
            difficulty=GENESIS_DIFFICULTY,
            nonce=GENESIS_NONCE,
            timestamp=GENESIS_TIMESTAMP,
            block_hash=GENESIS_HASH,
        )

    def _load_mempool(self):
        try:
            with open(self.mempool_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, list):
                return
            confirmed = {
                item.get("txid", self._transaction_id(item))
                for block in self.db.get_raw_chain() for item in block["transactions"]
                if item.get("sender") != "SISTEMA"
            }
            with self.mempool_lock:
                self.mempool = [
                    tx for tx in raw
                    if isinstance(tx, dict) and tx.get("txid", self._transaction_id(tx)) not in confirmed
                ]
        except (OSError, ValueError, KeyError, TypeError):
            with self.mempool_lock:
                self.mempool = []

    def _save_mempool(self):
        try:
            with open(self.mempool_path, "w", encoding="utf-8") as f:
                json.dump(self.mempool, f)
        except (OSError, TypeError, ValueError):
            pass

    def _prune_mempool(self):
        confirmed = {
            item.get("txid", self._transaction_id(item))
            for block in self.db.get_raw_chain() for item in block["transactions"]
            if item.get("sender") != "SISTEMA"
        }
        with self.mempool_lock:
            before = len(self.mempool)
            self.mempool = [
                tx for tx in self.mempool
                if tx.get("txid", self._transaction_id(tx)) not in confirmed
            ]
            if len(self.mempool) != before:
                self._save_mempool()

    def _load_orphans(self):
        try:
            with open(self.orphan_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            with self.orphan_lock:
                self.orphan_txs = [tx for tx in raw if isinstance(tx, dict)] if isinstance(raw, list) else []
        except (OSError, ValueError, TypeError):
            with self.orphan_lock:
                self.orphan_txs = []

    def _save_orphans(self):
        try:
            with open(self.orphan_path, "w", encoding="utf-8") as f:
                json.dump(self.orphan_txs, f)
        except (OSError, TypeError, ValueError):
            pass

    def _accept_broadcast_tx(self, tx_data, from_ip):
        if not self._verify_tx_structure(tx_data):
            return
        try:
            txid = self._transaction_id(tx_data)
        except (ValueError, KeyError, TypeError):
            return
        confirmed = {
            item.get("txid", self._transaction_id(item))
            for block in self.db.get_raw_chain() for item in block["transactions"]
            if item.get("sender") != "SISTEMA"
        }
        if txid in confirmed:
            return
        with self.mempool_lock:
            if any(item.get("txid", self._transaction_id(item)) == txid for item in self.mempool):
                return
        if self._available_balance(tx_data["sender"], txid) < Decimal(str(tx_data["amount"])):
            with self.orphan_lock:
                if not any(o.get("txid") == txid for o in self.orphan_txs):
                    self.orphan_txs.append({**tx_data, "txid": txid})
                    if len(self.orphan_txs) > 500:
                        self.orphan_txs = self.orphan_txs[-500:]
                    self._save_orphans()
            threading.Thread(target=self._sync_from_all_peers, daemon=True).start()
            return
        with self.mempool_lock:
            self.mempool.append(tx_data)
            self._save_mempool()
        threading.Thread(target=self._relay_transaction, args=(tx_data, from_ip), daemon=True).start()

    def _promote_orphans(self):
        with self.orphan_lock:
            candidates = list(self.orphan_txs)
        if not candidates:
            return
        promoted = []
        for tx in candidates:
            txid = tx.get("txid", self._transaction_id(tx))
            confirmed = {
                item.get("txid", self._transaction_id(item))
                for block in self.db.get_raw_chain() for item in block["transactions"]
                if item.get("sender") != "SISTEMA"
            }
            if txid in confirmed:
                continue
            with self.mempool_lock:
                if any(item.get("txid", self._transaction_id(item)) == txid for item in self.mempool):
                    continue
            if self._available_balance(tx["sender"], txid) >= Decimal(str(tx["amount"])):
                clean = {k: v for k, v in tx.items() if k != "txid"}
                clean["txid"] = txid
                promoted.append(clean)
        if promoted:
            with self.mempool_lock:
                for tx in promoted:
                    if not any(item.get("txid") == tx["txid"] for item in self.mempool):
                        self.mempool.append(tx)
                self._save_mempool()
            with self.orphan_lock:
                promoted_ids = {tx["txid"] for tx in promoted}
                self.orphan_txs = [o for o in self.orphan_txs if o.get("txid") not in promoted_ids]
                self._save_orphans()
            for tx in promoted:
                threading.Thread(target=self._relay_transaction, args=(tx, ""), daemon=True).start()

    def _sync_from_all_peers(self):
        with self.peers_lock:
            targets = list(self.connected_peers)
        for ip, port in targets:
            try:
                self.connect_and_sync(ip, port)
            except Exception:
                pass
        self._promote_orphans()

    def get_p2p_port(self):
        return self.p2p_port

    def _calculate_next_difficulty(self) -> int:
        chain = self.db.get_raw_chain()
        if len(chain) < DIFFICULTY_ADJUSTMENT_INTERVAL + 1:
            return chain[-1]["difficulty"]
        
        latest_block = chain[-1]
        if latest_block["index"] % DIFFICULTY_ADJUSTMENT_INTERVAL != 0:
            return latest_block["difficulty"]
            
        prev_adjustment_block = chain[-DIFFICULTY_ADJUSTMENT_INTERVAL]
        time_expected = TARGET_BLOCK_TIME * DIFFICULTY_ADJUSTMENT_INTERVAL
        time_taken = latest_block["timestamp"] - prev_adjustment_block["timestamp"]
        
        current_diff = latest_block["difficulty"]
        if time_taken < (time_expected / 2):
            return current_diff + 1
        elif time_taken > (time_expected * 2):
            return max(1, current_diff - 1)
        return current_diff

    def _receive_all(self, sock, buffer_size=4096, max_size=1_048_576):
        data = b""
        try:
            while True:
                chunk = sock.recv(buffer_size)
                if not chunk:
                    break
                data += chunk
                if len(data) > max_size:
                    raise ValueError(f"Mensagem excede tamanho maximo de {max_size} bytes")
        except socket.timeout:
            pass
        except Exception as e:
            print(f"Erro ao receber dados: {e}")
        return data.decode('utf-8', errors='ignore')

    def _request_peer(self, ip, port, message, timeout=5.0):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((str(ip), int(port)))
            s.sendall(message.encode('utf-8'))
            s.shutdown(socket.SHUT_WR)
            return self._receive_all(s)
        finally:
            s.close()

    def _start_p2p_server(self):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('0.0.0.0', self.p2p_port))
        server_socket.listen(10)
        while True:
            try:
                client_conn, client_addr = server_socket.accept()
                client_conn.settimeout(5.0)
                data = self._receive_all(client_conn)

                if data == "GET_HEIGHT":
                    client_conn.sendall(str(len(self.db.get_raw_chain())).encode('utf-8'))
                elif data == "GET_CHAIN":
                    client_conn.sendall(json.dumps(self.db.get_raw_chain()).encode('utf-8'))
                elif data.startswith("ANNOUNCE_PEER:"):
                    threading.Thread(
                        target=self._handle_peer_announcement,
                        args=(client_addr[0], data),
                        daemon=True,
                    ).start()
                elif data.startswith("BROADCAST_TX:"):
                    tx_data = json.loads(data.split(":", 1)[1])
                    self._accept_broadcast_tx(tx_data, client_addr[0])
                elif data.startswith("SYNC_CHAIN:"):
                    incoming_chain = json.loads(data.split(":", 1)[1])
                    self._resolve_consensus(incoming_chain)
                    self._prune_mempool()
                    self._promote_orphans()
                client_conn.close()
            except Exception:
                pass

    def _local_ip_addresses(self):
        ips = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                ips.add(info[4][0])
        except Exception:
            pass
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(1.0)
            probe.connect(("8.8.8.8", 80))
            ips.add(probe.getsockname()[0])
            probe.close()
        except Exception:
            pass
        ips.add("127.0.0.1")
        return ips

    def _primary_lan_ip(self):
        """Retorna o melhor IP LAN desta máquina para anunciar via UDP broadcast."""
        for ip in self._local_ip_addresses():
            if "." in ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
        return "127.0.0.1"

    def _is_self(self, ip, port):
        try:
            if int(port) != int(self.p2p_port):
                return False
        except (ValueError, TypeError):
            return False
        ip = str(ip).strip()
        if ip in ("127.0.0.1", "localhost", "0.0.0.0"):
            return True
        return ip in self._local_ip_addresses()

    def get_local_ips(self):
        port = self.get_p2p_port()
        lan = sorted(
            ip for ip in self._local_ip_addresses()
            if "." in ip and not ip.startswith("127.") and not ip.startswith("169.254.")
        )
        return {
            "port": port,
            "loopback": f"127.0.0.1:{port}",
            "lan_addresses": [f"{ip}:{port}" for ip in lan],
        }

    def get_connected_peers(self):
        with self.peers_lock:
            peers = sorted(self.connected_peers, key=lambda p: (p[0], int(p[1])))
        return {
            "count": len(peers),
            "peers": [{"ip": ip, "port": int(port)} for ip, port in peers],
        }

    def _add_peer(self, ip, port):
        with self.peers_lock:
            self.connected_peers.add((str(ip).strip(), int(port)))

    def _remove_peer(self, ip, port):
        with self.peers_lock:
            self.connected_peers.discard((str(ip).strip(), int(port)))

    def _ping_peer(self, ip, port):
        try:
            self._request_peer(ip, port, "GET_HEIGHT", timeout=3.0)
            return True
        except Exception:
            return False

    def _handle_peer_announcement(self, peer_ip, data):
        try:
            announced_port = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            return
        if not announced_port or self._is_self(peer_ip, announced_port):
            return
        if self._ping_peer(peer_ip, announced_port):
            self._add_peer(peer_ip, announced_port)

    # ============================================================
    # DESCOBERTA AUTOMÁTICA NA LAN (UDP broadcast) — métodos que
    # faltavam no arquivo original e causavam o AttributeError.
    # ============================================================
    def _start_discovery_listener(self):
        """Escuta broadcasts UDP na porta DISCOVERY_UDP_PORT para descobrir
        outros nós na mesma rede local e registrá-los automaticamente."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_UDP_PORT))
        except OSError as e:
            print(f"[discovery] Nao foi possivel escutar na porta UDP {DISCOVERY_UDP_PORT}: {e}")
            return
        print(f"[discovery] Escutando broadcasts UDP na porta {DISCOVERY_UDP_PORT}.")
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                try:
                    info = json.loads(data.decode("utf-8", errors="ignore"))
                except (ValueError, TypeError):
                    continue
                peer_ip = str(info.get("ip") or addr[0]).strip()
                try:
                    peer_port = int(info.get("port"))
                except (ValueError, TypeError):
                    continue
                if not peer_ip or not peer_port:
                    continue
                if self._is_self(peer_ip, peer_port):
                    continue
                with self.peers_lock:
                    already = (peer_ip, peer_port) in self.connected_peers
                if already:
                    continue
                threading.Thread(
                    target=self._handle_peer_announcement,
                    args=(peer_ip, f"ANNOUNCE_PEER:{peer_port}"),
                    daemon=True,
                ).start()
            except Exception:
                continue

    def _discovery_announce_loop(self):
        """Anuncia periodicamente a própria porta via UDP broadcast (255.255.255.255)
        para que os outros nós da LAN nos encontrem sem digitar IP manualmente."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        print(f"[discovery] Anunciando este no na porta {self.p2p_port} via UDP broadcast.")
        while True:
            try:
                payload = json.dumps({
                    "ip": self._primary_lan_ip(),
                    "port": self.p2p_port,
                }).encode("utf-8")
                sock.sendto(payload, ("255.255.255.255", DISCOVERY_UDP_PORT))
            except Exception:
                pass
            time.sleep(DISCOVERY_INTERVAL_SECONDS)

    def _run_bootstrap_discovery(self):
        time.sleep(2)
        for ip, port in BOOTSTRAP_PEERS:
            if self._is_self(ip, port):
                continue
            try:
                self.connect_and_sync(ip, port)
            except Exception:
                pass

    def _peer_maintenance_loop(self):
        while True:
            time.sleep(15)
            try:
                with self.peers_lock:
                    current = list(self.connected_peers)
                for ip, port in current:
                    if not self._ping_peer(ip, port):
                        self._remove_peer(ip, port)
                for ip, port in BOOTSTRAP_PEERS:
                    if self._is_self(ip, port):
                        continue
                    with self.peers_lock:
                        already = (str(ip).strip(), int(port)) in self.connected_peers
                    if not already:
                        self.connect_and_sync(ip, port)
                with self.orphan_lock:
                    has_orphans = bool(self.orphan_txs)
                if has_orphans:
                    self._sync_from_all_peers()
            except Exception:
                pass

    def _broadcast_transaction_to_network(self, tx):
        with self.peers_lock:
            targets = list(self.connected_peers)
        for ip, port in targets:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect((ip, int(port)))
                s.sendall(f"BROADCAST_TX:{json.dumps(tx)}".encode('utf-8'))
                s.close()
            except Exception:
                self._remove_peer(ip, port)

    def _relay_transaction(self, tx, exclude_ip):
        with self.peers_lock:
            targets = [(ip, port) for ip, port in self.connected_peers if ip != exclude_ip]
        for ip, port in targets:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect((ip, int(port)))
                s.sendall(f"BROADCAST_TX:{json.dumps(tx)}".encode('utf-8'))
                s.close()
            except Exception:
                self._remove_peer(ip, port)

    def connect_and_sync(self, ip, port):
        try:
            port = int(port)
            if self._is_self(ip, port):
                return {
                    "status": "erro",
                    "message": (
                        f"{ip}:{port} é este próprio nó. Para sincronizar com o OUTRO "
                        "computador, informe o IP de rede dele (ex.: 192.168.0.x) e a porta "
                        "em que o nó dele está rodando — não 127.0.0.1."
                    ),
                }
            remote_height = int(self._request_peer(ip, port, "GET_HEIGHT", timeout=5.0))

            self._add_peer(ip, port)

            try:
                s_ann = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s_ann.settimeout(3.0)
                s_ann.connect((str(ip), port))
                s_ann.sendall(f"ANNOUNCE_PEER:{self.p2p_port}".encode('utf-8'))
                s_ann.close()
            except Exception:
                pass

            local_height = len(self.db.get_raw_chain())
            if remote_height <= local_height:
                pushed = False
                if remote_height < local_height:
                    try:
                        s_push = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s_push.settimeout(5.0)
                        s_push.connect((str(ip), port))
                        s_push.sendall(f"SYNC_CHAIN:{json.dumps(self.db.get_raw_chain())}".encode('utf-8'))
                        s_push.close()
                        pushed = True
                    except Exception:
                        pass
                msg = (f"Conectado a {ip}:{port}. Cadeia local ({local_height} blocos) enviada ao par."
                       if pushed else
                       f"Conectado a {ip}:{port}. Sua blockchain já está atualizada.")
                return {"status": "sucesso", "message": msg}

            response = self._request_peer(ip, port, "GET_CHAIN", timeout=15.0)
            ok, result = self._resolve_consensus(json.loads(response))
            self._prune_mempool()
            self._promote_orphans()
            return {"status": "sucesso" if ok else "erro", "message": result}
        except Exception as e:
            self._remove_peer(ip, port)
            return {"status": "erro", "message": str(e)}

    def _validate_address(self, address):
        if not isinstance(address, str) or not address.startswith("brn1") or len(address) != 44:
            raise ValueError(f"Tamanho ou formato de endereco invalido: {address}")
        try:
            int(address[4:], 16)
        except ValueError:
            raise ValueError("Endereco contem caracteres hexadecimais invalidos")
        return True

    @staticmethod
    def _canonical_amount(value) -> float:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("Quantia inválida.")
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -8:
            raise ValueError("Quantia inválida; use até 8 casas decimais.")
        return float(amount)

    @staticmethod
    def _transaction_id(tx):
        signed = {key: tx[key] for key in ("sender", "receiver", "amount", "timestamp", "public_key", "signature")}
        return hashlib.sha256(json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _available_balance(self, address, ignore_tx_id=None):
        balance = Decimal(str(self.get_balance(address).get("balance", 0)))
        with self.mempool_lock:
            for tx in self.mempool:
                if tx.get("sender") == address and tx.get("txid") != ignore_tx_id:
                    balance -= Decimal(str(tx["amount"]))
        return balance

    def _verify_tx_structure(self, tx) -> bool:
        if not isinstance(tx, dict):
            return False
        
        required_keys = ["sender", "receiver", "amount", "timestamp", "public_key", "signature"]
        if not all(k in tx for k in required_keys):
            return False
        try:
            self._validate_address(tx["sender"])
            self._validate_address(tx["receiver"])
            amount = self._canonical_amount(tx["amount"])
            timestamp = float(tx["timestamp"])
            if not timestamp or timestamp > time.time() + MAX_FUTURE_SECONDS:
                return False
            if WalletManager.address_from_public_key(tx["public_key"]) != tx["sender"]:
                return False
            payload = {"sender": tx["sender"], "receiver": tx["receiver"], "amount": amount, "timestamp": timestamp}
            return WalletManager.verify_signature(tx["public_key"], payload, tx["signature"])
        except (ValueError, TypeError, KeyError):
            return False

    def _validate_transaction(self, tx, include_mempool=False):
        if not self._verify_tx_structure(tx):
            return False, "Transação inválida ou assinatura não confere."
        try:
            txid = self._transaction_id(tx)
            known_txids = {
                item.get("txid", self._transaction_id(item))
                for block in self.db.get_raw_chain() for item in block["transactions"]
                if item.get("sender") != "SISTEMA"
            }
            if txid in known_txids:
                return False, "Transação já confirmada."
            with self.mempool_lock:
                if any(item.get("txid", self._transaction_id(item)) == txid for item in self.mempool):
                    return False, "Transação já está na mempool."
            if include_mempool and self._available_balance(tx["sender"], txid) < Decimal(str(tx["amount"])):
                return False, "Saldo disponível insuficiente."
            return True, "OK"
        except (ValueError, KeyError, TypeError):
            return False, "Transação inválida."

    def _validate_block(self, block_data):
        required = {"index", "timestamp", "previous_hash", "transactions", "difficulty", "nonce", "hash"}
        if not isinstance(block_data, dict) or not required.issubset(block_data) or not isinstance(block_data["transactions"], list):
            return False, "Estrutura do bloco inválida"
        if not 1 <= int(block_data["difficulty"]) <= MAX_DIFFICULTY or len(block_data["transactions"]) > MAX_BLOCK_TRANSACTIONS:
            return False, "Limites do bloco inválidos"
        if float(block_data["timestamp"]) > time.time() + MAX_FUTURE_SECONDS:
            return False, "Timestamp futuro inválido"
        block = BrunoBlock(
            block_data["index"], block_data["previous_hash"], block_data["transactions"],
            block_data["difficulty"], block_data["nonce"], block_data["timestamp"], block_data["hash"]
        )
        if block.calculate_hash() != block_data["hash"]:
            return False, "Hash nao corresponde"
        
        target = "0" * block_data["difficulty"]
        if not block_data["hash"].startswith(target):
            return False, "Prova de Trabalho invalida"
            
        for position, tx in enumerate(block_data["transactions"]):
            if tx.get("sender") == "SISTEMA":
                expected_amount = GENESIS_AMOUNT if block.index == 0 else BLOCK_REWARD
                is_genesis = block.index == 0 and tx.get("receiver") == GENESIS_ADDRESS
                if position != 0 or tx.get("amount") != expected_amount or (not is_genesis and not self._is_valid_system_reward(tx)):
                    return False, "Recompensa de mineração inválida"
                if block.index == 0 and not is_genesis:
                    return False, "Bloco gênese inválido"
            elif not self._verify_tx_structure(tx):
                return False, f"Transacao invalida detectada no bloco: {tx}"
                
        return True, "OK"

    def _is_valid_system_reward(self, tx):
        try:
            self._validate_address(tx.get("receiver"))
            return set(tx) == {"sender", "receiver", "amount"}
        except ValueError:
            return False

    def _validate_chain_transactions(self, chain):
        balances = {}
        seen_txids = set()
        for block in chain:
            for tx in block["transactions"]:
                receiver = tx.get("receiver")
                amount = Decimal(str(tx.get("amount", 0)))
                if tx.get("sender") == "SISTEMA":
                    balances[receiver] = balances.get(receiver, Decimal("0")) + amount
                    continue
                if not self._verify_tx_structure(tx):
                    return False, "Transação inválida no ledger remoto."
                txid = self._transaction_id(tx)
                if txid in seen_txids:
                    return False, "Transação duplicada no ledger remoto."
                seen_txids.add(txid)
                sender = tx["sender"]
                if balances.get(sender, Decimal("0")) < amount:
                    return False, "Gasto sem saldo no ledger remoto."
                balances[sender] -= amount
                balances[receiver] = balances.get(receiver, Decimal("0")) + amount
        return True, "OK"

    def _resolve_consensus(self, remote_chain):
        if len(remote_chain) <= len(self.db.get_raw_chain()):
            return True, "Cadeia local ja e dominante."
            
        if not isinstance(remote_chain, list) or not remote_chain:
            return False, "Cadeia remota inválida."
        if remote_chain[0].get("index") != 0 or remote_chain[0].get("previous_hash") != "0":
            return False, "Gênese remota inválida."
        if str(remote_chain[0].get("hash")) != GENESIS_HASH:
            return False, "Gênese remota pertence a outra rede (hash diferente). Sincronização recusada para manter uma única blockchain."
        for i in range(1, len(remote_chain)):
            if remote_chain[i]["index"] != remote_chain[i - 1]["index"] + 1 or remote_chain[i]["timestamp"] < remote_chain[i - 1]["timestamp"]:
                return False, "Ordem de blocos inválida na cadeia remota."
            if remote_chain[i]["previous_hash"] != remote_chain[i-1]["hash"]:
                return False, "Hashes corrompidos na cadeia remota."
                
        for block_data in remote_chain:
            is_valid, message = self._validate_block(block_data)
            if not is_valid:
                return False, f"Bloco #{block_data['index']} rejeitado: {message}"
        is_valid, message = self._validate_chain_transactions(remote_chain)
        if not is_valid:
            return False, f"Cadeia remota rejeitada: {message}"
                
        with self.chain_lock:
            self.db.replace_chain(remote_chain)
        return True, f"Sincronizado com sucesso para {len(remote_chain)} blocos."

    def save_encrypted_wallet(self, filename, password, address, spend_secret_key, public_key=""):
        return WalletManager.save_encrypted_wallet(filename, password, address, spend_secret_key, public_key)

    def load_encrypted_wallet(self, filename, password):
        return WalletManager.load_encrypted_wallet(filename, password)

    def get_full_chain(self):
        with self.mempool_lock:
            mempool_size = len(self.mempool)
            mempool_txs = [
                {
                    "txid": tx.get("txid", ""),
                    "sender": tx.get("sender", ""),
                    "receiver": tx.get("receiver", ""),
                    "amount": float(Decimal(str(tx.get("amount", 0)))),
                    "timestamp": float(tx.get("timestamp", 0)),
                }
                for tx in self.mempool
            ]
        chain = self.db.get_raw_chain()
        local_genesis = str(chain[0]["hash"]) if chain else ""
        return {
            "chain": chain, 
            "length": len(chain), 
            "mempool_size": mempool_size,
            "mempool": mempool_txs,
            "is_mining": self.is_mining,
            "current_difficulty": chain[-1]["difficulty"] if chain else 4,
            "genesis_hash": local_genesis,
            "genesis_ok": local_genesis == GENESIS_HASH,
            "network_genesis": GENESIS_HASH,
        }

    def generate_wallet(self):
        return WalletManager.generate_keypair()

    def get_balance(self, address):
        try:
            self._validate_address(address)
            balance = Decimal("0")
            for b in self.db.get_raw_chain():
                for tx in b["transactions"]:
                    if tx.get("sender") == address:
                        balance -= Decimal(str(tx.get("amount", 0)))
                    if tx.get("receiver") == address:
                        balance += Decimal(str(tx.get("amount", 0)))
            pending_out = Decimal("0")
            pending_in = Decimal("0")
            with self.mempool_lock:
                for tx in self.mempool:
                    amount = Decimal(str(tx.get("amount", 0)))
                    if tx.get("sender") == address:
                        pending_out += amount
                    if tx.get("receiver") == address:
                        pending_in += amount
            return {
                "address": address,
                "balance": float(balance),
                "pending_out": float(pending_out),
                "pending_in": float(pending_in),
                "available": float(balance - pending_out),
            }
        except ValueError as e:
            return {"status": "erro", "message": str(e)}

    def get_mempool(self):
        with self.mempool_lock:
            pending = list(self.mempool)
        return {
            "count": len(pending),
            "transactions": [
                {
                    "txid": tx.get("txid", ""),
                    "sender": tx.get("sender", ""),
                    "receiver": tx.get("receiver", ""),
                    "amount": float(Decimal(str(tx.get("amount", 0)))),
                    "timestamp": float(tx.get("timestamp", 0)),
                }
                for tx in pending
            ],
        }

    def get_transaction_history(self, address):
        try:
            self._validate_address(address)
        except ValueError as e:
            return {"status": "erro", "message": str(e)}

        def _amount(tx):
            try:
                return float(Decimal(str(tx.get("amount", 0))))
            except (InvalidOperation, ValueError):
                return 0.0

        entries = []
        chain = self.db.get_raw_chain()
        chain_len = len(chain)
        for block in chain:
            block_index = block["index"]
            block_ts = float(block.get("timestamp", 0) or 0)
            confirmations = max(0, chain_len - block_index)
            for tx in block["transactions"]:
                sender = tx.get("sender")
                receiver = tx.get("receiver")
                ts = float(tx.get("timestamp") or block_ts)
                if receiver == address:
                    if sender == "SISTEMA":
                        tx_type = "genese" if block_index == 0 else "mineracao"
                        counterparty = "SISTEMA (Rede)"
                    else:
                        tx_type = "recebido"
                        counterparty = sender
                    entries.append({
                        "direction": "entrada", "type": tx_type, "amount": _amount(tx),
                        "counterparty": counterparty, "timestamp": ts, "block_index": block_index,
                        "confirmations": confirmations, "status": "confirmada", "txid": tx.get("txid", ""),
                    })
                elif sender == address:
                    entries.append({
                        "direction": "saida", "type": "enviado", "amount": _amount(tx),
                        "counterparty": receiver, "timestamp": ts, "block_index": block_index,
                        "confirmations": confirmations, "status": "confirmada", "txid": tx.get("txid", ""),
                    })

        with self.mempool_lock:
            pending = list(self.mempool)
        for tx in pending:
            sender = tx.get("sender")
            receiver = tx.get("receiver")
            ts = float(tx.get("timestamp") or 0)
            if receiver == address:
                entries.append({
                    "direction": "entrada", "type": "recebido", "amount": _amount(tx),
                    "counterparty": sender, "timestamp": ts, "block_index": None,
                    "confirmations": 0, "status": "pendente", "txid": tx.get("txid", ""),
                })
            elif sender == address:
                entries.append({
                    "direction": "saida", "type": "enviado", "amount": _amount(tx),
                    "counterparty": receiver, "timestamp": ts, "block_index": None,
                    "confirmations": 0, "status": "pendente", "txid": tx.get("txid", ""),
                })

        entries.sort(
            key=lambda e: (e["timestamp"], e["block_index"] if e["block_index"] is not None else float("inf")),
            reverse=True,
        )
        return {"status": "sucesso", "address": address, "history": entries}

    def send_funds(self, sender, receiver, amount, spend_secret_key, public_key):
        try:
            self._validate_address(sender)
            self._validate_address(receiver)
            sender, receiver = str(sender).strip(), str(receiver).strip()
            amount = self._canonical_amount(amount)
                
            tx_payload = {
                "sender": str(sender).strip(),
                "receiver": str(receiver).strip(),
                "amount": amount,
                "timestamp": time.time()
            }
            
            signature = WalletManager.sign_transaction(spend_secret_key, tx_payload)
            full_tx = {
                **tx_payload,
                "public_key": public_key,
                "signature": signature
            }
            full_tx["txid"] = self._transaction_id(full_tx)
            valid, message = self._validate_transaction(full_tx, include_mempool=True)
            if not valid:
                return {"status": "erro", "message": message}
            
            with self.mempool_lock:
                self.mempool.append(full_tx)
                self._save_mempool()
                
            threading.Thread(target=self._broadcast_transaction_to_network, args=(full_tx,), daemon=True).start()
            return {"status": "sucesso", "message": "Transacao assinada e enviada a mempool!"}
        except Exception as e:
            return {"status": "erro", "message": str(e)}

    def toggle_continuous_mining(self, miner_address):
        if self.is_mining:
            self.is_mining = False
            self.mining_stop_event.set()
            return {"status": "sucesso", "message": "Mineracao continua pausada.", "is_mining": False}
        else:
            try:
                self._validate_address(miner_address)
                self.is_mining = True
                self.mining_stop_event.clear()
                self.miner_thread = threading.Thread(target=self._continuous_mining_loop, args=(miner_address,), daemon=True)
                self.miner_thread.start()
                return {"status": "sucesso", "message": "Mineracao continua iniciada!", "is_mining": True}
            except Exception as e:
                return {"status": "erro", "message": str(e)}

    def _continuous_mining_loop(self, miner_address):
        print(f"⛏️ Loop de mineracao continua ativo para: {miner_address}")
        while self.is_mining and not self.mining_stop_event.is_set():
            try:
                local_chain = self.db.get_raw_chain()
                last_block = local_chain[-1]
                next_difficulty = self._calculate_next_difficulty()
                
                with self.mempool_lock:
                    pending = list(self.mempool)
                    bloco_txs = [
                        {"sender": "SISTEMA", "receiver": str(miner_address).strip(), "amount": BLOCK_REWARD}
                    ] + pending
                    
                new_block = BrunoBlock(
                    last_block["index"] + 1,
                    last_block["hash"],
                    bloco_txs,
                    difficulty=next_difficulty
                )
                
                success = new_block.mine_block(stop_event=self.mining_stop_event)
                if success and self.is_mining:
                    with self.chain_lock:
                        current_tip = self.db.get_raw_chain()[-1]
                        if current_tip["hash"] != new_block.previous_hash:
                            continue
                        self.db.insert_block(new_block)
                    with self.mempool_lock:
                        self.mempool = [tx for tx in self.mempool if tx not in pending]
                        self._save_mempool()
                    
                    raw_chain_json = json.dumps(self.db.get_raw_chain())
                    for ip, port in list(self.connected_peers):
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(3.0)
                            s.connect((ip, int(port)))
                            s.sendall(f"SYNC_CHAIN:{raw_chain_json}".encode('utf-8'))
                            s.close()
                        except Exception:
                            pass
                    print(f"✅ Bloco #{new_block.index} minerado (Diff: {next_difficulty}). Hash: {new_block.hash[:16]}...")
            except Exception as e:
                print(f"⚠️ Erro no loop de mineracao: {e}")
                time.sleep(2)
        print("🛑 Loop de mineracao continua desligado.")

if __name__ == '__main__':
    p2p_port = 6001
    if len(sys.argv) > 1:
        try:
            p2p_port = int(sys.argv[1])
        except ValueError:
            pass
    api_local = CriptoAPI(p2p_port)
    webview.create_window(title=f"Carteira Nativa {COIN_NAME} (Porta: {p2p_port})", url="index.html", js_api=api_local, width=740, height=800, resizable=True)
    webview.start()

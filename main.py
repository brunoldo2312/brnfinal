"""
BRN Node — Blockchain PoW + P2P + AMM (BRN/USDC) + Mineração.
Junta: bruno_blockchain_real.py + p2p.py + cripto_wallet + assets.
"""
import asyncio
import os
import sys
import time
import json

from bruno_blockchain_real import (
    Blockchain, Transaction, NATIVE_ASSET, DB_PATH,
)
from p2p import PeerManager

try:
    from cripto_wallet import WalletManager
except ImportError:
    WalletManager = None


# =====================================================================
# Configuração (via variáveis de ambiente)
# =====================================================================
PORT             = int(os.environ.get("BRN_P2P_PORT", "6001"))
MINER_INTERVAL   = float(os.environ.get("BRN_MINER_INTERVAL", "0.5"))
STATUS_INTERVAL  = float(os.environ.get("BRN_STATUS_INTERVAL", "15"))
MINER_ENABLED    = os.environ.get("BRN_MINE", "1") == "1"
INTERACTIVE      = os.environ.get("BRN_INTERACTIVE", "0") == "1"


# =====================================================================
# Identidade
# =====================================================================
def load_identity():
    """Carrega ou gera identidade do nó."""
    if WalletManager is None:
        raise RuntimeError("cripto_wallet nao disponivel.")

    if hasattr(WalletManager, "load_node_identity"):
        return WalletManager.load_node_identity()

    sk = os.environ.get("BRN_NODE_SK")
    pk = os.environ.get("BRN_NODE_PK")
    if not sk or not pk:
        if hasattr(WalletManager, "generate_keypair"):
            sk, pk = WalletManager.generate_keypair()
            print("[main] identidade nova gerada (guarde as chaves!)")
        else:
            raise RuntimeError("WalletManager sem generate_keypair.")
    addr = WalletManager.address_from_public_key(pk)
    return {"address": addr, "public_key": pk, "spend_secret_key": sk}


# =====================================================================
# Loops
# =====================================================================
async def mining_loop(chain: Blockchain, pm: PeerManager, stop: asyncio.Event):
    if not MINER_ENABLED:
        print("[miner] mineracao desativada (BRN_MINE=0)")
        return
    loop = asyncio.get_event_loop()
    while not stop.is_set():
        block = await loop.run_in_executor(None, chain.produce_block)
        if block is None:
            await asyncio.sleep(1)
            continue
        try:
            await pm.broadcast({
                "type": "inv_block",
                "height": block.index,
                "hash": block.hash,
            })
        except Exception as e:
            print(f"[main] broadcast falhou: {e}")
        await asyncio.sleep(MINER_INTERVAL)


async def status_loop(chain: Blockchain, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=STATUS_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass
        try:
            h = chain.height
            fh = chain.finality.finalized_height
            mp = len(chain.pending)
            pools = list(chain.state.pools.keys())
            pool_info = ""
            if pools:
                pid = pools[0]
                p = chain.state.pools[pid]
                pool_info = (f" | pool {pid}: "
                             f"{p['reserve_a']:.2f}/{p['reserve_b']:.2f}")
            print(f"[status] altura={h} final={fh} mempool={mp}{pool_info}")
        except Exception as e:
            print(f"[status] erro: {e}")


# =====================================================================
# AMM — helpers de alto nível (para CLI / demo)
# =====================================================================
def create_pool(chain, identity, asset_a, amount_a, asset_b, amount_b,
                fee_bps=30):
    addr = identity["address"]
    tx = Transaction.build(
        tx_type="pool_create",
        asset_id=asset_a,
        sender_address=addr,
        receiver_address=addr,
        amount=amount_a,
        nonce=chain.state.nonce(addr),
        private_key_hex=identity["spend_secret_key"],
        public_key_hex=identity["public_key"],
        metadata={"asset_b": asset_b, "amount_b": amount_b, "fee_bps": fee_bps},
    )
    return chain.add_transaction(tx)


def add_liquidity(chain, identity, asset_a, amount_a, asset_b, amount_b,
                  min_lp=0.0):
    addr = identity["address"]
    tx = Transaction.build(
        tx_type="liquidity_add",
        asset_id=asset_a,
        sender_address=addr,
        receiver_address=addr,
        amount=amount_a,
        nonce=chain.state.nonce(addr),
        private_key_hex=identity["spend_secret_key"],
        public_key_hex=identity["public_key"],
        metadata={"asset_b": asset_b, "amount_b": amount_b, "min_lp": min_lp},
    )
    return chain.add_transaction(tx)


def remove_liquidity(chain, identity, pool_id, lp_amount, min_a=0.0, min_b=0.0):
    addr = identity["address"]
    tx = Transaction.build(
        tx_type="liquidity_remove",
        asset_id=f"LP-{pool_id}",
        sender_address=addr,
        receiver_address=addr,
        amount=lp_amount,
        nonce=chain.state.nonce(addr),
        private_key_hex=identity["spend_secret_key"],
        public_key_hex=identity["public_key"],
        metadata={"min_a": min_a, "min_b": min_b},
    )
    return chain.add_transaction(tx)


def swap(chain, identity, asset_in, amount_in, asset_out, min_out=0.0):
    addr = identity["address"]
    tx = Transaction.build(
        tx_type="swap",
        asset_id=asset_in,
        sender_address=addr,
        receiver_address=addr,
        amount=amount_in,
        nonce=chain.state.nonce(addr),
        private_key_hex=identity["spend_secret_key"],
        public_key_hex=identity["public_key"],
        metadata={"asset_out": asset_out, "min_out": min_out},
    )
    return chain.add_transaction(tx)


def print_pools(chain):
    pools = chain.list_pools()
    if not pools:
        print("[pools] nenhum pool criado.")
        return
    for pid, p in pools.items():
        print(f"[pool] {pid}  "
              f"reservas: {p['reserve_a']:.4f} {p['asset_a']} / "
              f"{p['reserve_b']:.4f} {p['asset_b']}  "
              f"fee: {p['fee_bps']/100:.2f}%  "
              f"lp_supply: {p['lp_supply']:.4f}")


def print_quote(chain, asset_in, asset_out, amount):
    q = chain.quote_swap(asset_in, asset_out, amount)
    if not q.get("ok"):
        print(f"[quote] {q.get('msg')}")
        return
    print(f"[quote] {amount} {asset_in} → {q['amount_out']:.6f} {asset_out} "
          f"(exec_price={q['exec_price']:.6f}, spot={q['spot_price']:.6f})")


# =====================================================================
# CLI interativa (opcional: BRN_INTERACTIVE=1)
# =====================================================================
async def interactive_loop(chain: Blockchain, identity, stop: asyncio.Event):
    loop = asyncio.get_event_loop()
    print("\n[cli] comandos: pools | portfolio | quote <in> <out> <amt> | "
          "swap <in> <out> <amt> <min_out> | "
          "addliq <a> <amt_a> <b> <amt_b> | "
          "rmliq <pool_id> <lp_amt> | "
          "height | slashing | finality | help | quit\n")

    while not stop.is_set():
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            break
        if not line:
            await asyncio.sleep(0.2)
            continue
        parts = line.strip().split()
        if not parts:
            continue
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit"):
                stop.set()
                break
            elif cmd == "help":
                print("pools | portfolio | quote <in> <out> <amt> | "
                      "swap <in> <out> <amt> <min_out> | "
                      "addliq <a> <amt_a> <b> <amt_b> | "
                      "rmliq <pool_id> <lp_amt> | "
                      "height | slashing | finality | quit")
            elif cmd == "pools":
                print_pools(chain)
            elif cmd == "portfolio":
                print(json.dumps(chain.portfolio(identity["address"]),
                                 indent=2, default=str))
            elif cmd == "height":
                print(f"[chain] altura={chain.height} tip={chain.tip_hash[:16]}")
            elif cmd == "slashing":
                print(json.dumps(chain.slashing_report(), indent=2, default=str))
            elif cmd == "finality":
                print(json.dumps(chain.finality_report(), indent=2, default=str))
            elif cmd == "quote" and len(parts) == 4:
                print_quote(chain, parts[1], parts[2], float(parts[3]))
            elif cmd == "swap" and len(parts) == 5:
                r = swap(chain, identity, parts[1], float(parts[3]),
                         parts[2], float(parts[4]))
                print(f"[swap] {r}")
            elif cmd == "addliq" and len(parts) == 5:
                r = add_liquidity(chain, identity, parts[1], float(parts[2]),
                                  parts[3], float(parts[4]))
                print(f"[addliq] {r}")
            elif cmd == "rmliq" and len(parts) == 3:
                r = remove_liquidity(chain, identity, parts[1], float(parts[2]))
                print(f"[rmliq] {r}")
            else:
                print("[cli] comando desconhecido. Digite 'help'.")
        except Exception as e:
            print(f"[cli] erro: {e}")


# =====================================================================
# Orquestração
# =====================================================================
async def main():
    identity = load_identity()
    print(f"[main] endereco do no: {identity['address']}")
    print(f"[main] porta P2P: {PORT} | mine={MINER_ENABLED} | "
          f"interactive={INTERACTIVE}")

    chain = Blockchain(node_identity=identity)
    pm = PeerManager(chain, port=PORT)

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(pm.start()),
        asyncio.create_task(mining_loop(chain, pm, stop)),
        asyncio.create_task(status_loop(chain, stop)),
    ]
    if INTERACTIVE:
        tasks.append(asyncio.create_task(interactive_loop(chain, identity, stop)))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("\n[main] encerrando...")
        stop.set()
        try:
            await pm.stop()
        except Exception:
            pass
        for t in tasks:
            t.cancel()
        print("[main] finalizado.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
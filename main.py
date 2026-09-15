"""
main.py — CLI interativa sobre o Node (P2P + PoW + CLOB + MEV + NGROK).
"""
import json
import os
import sys
import time

from node import Node, BASE, QUOTE


# =====================================================================
# Cabeçalho com URL do NGROK
# =====================================================================
def wait_ngrok(node: Node, timeout: float = 10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if node.public_endpoint:
            return True
        time.sleep(0.2)
    return False


def print_header(node: Node):
    print("=" * 72)
    print(" BRN Node — CLI interativa (CLOB + MEV + PoW + P2P)")
    print("=" * 72)
    print(f" Endereço:       {node.identity['address']}")
    print(f" Porta P2P:      {node.pm.port}")
    print(f" Par padrão:     {BASE}/{QUOTE}")
    print(f" Minerando:      {'sim' if node.enable_miner else 'não'}")
    if node.public_endpoint:
        h, p = node.public_endpoint
        print(f" Endpoint NGROK: {h}:{p}")
        print(f" Peers devem usar:")
        print(f"   BRN_HARDCODED_SEEDS={h}:{p}")
    elif os.environ.get("BRN_NGROK", "0") == "1":
        print(f" Endpoint NGROK: aguardando... (veja logs)")
    else:
        print(f" Endpoint NGROK: desativado (BRN_NGROK=0)")
    print("=" * 72)


# =====================================================================
# CLI
# =====================================================================
COMMANDS_HELP = """
Comandos:

  ---- mercado (MEV commit-reveal) ----
  commit <side> <price> <amount>         cria commit (auto-reveal)
  reveal <hash> <salt> <side> <p> <amt>  reveal manual
  commits                                meus commits
  place  <side> <price> <amount>         ordem direta (SEM MEV)
  cancel <order_id>                      cancela ordem
  book [depth]                           order book
  orders                                 minhas ordens abertas
  trades                                 meus trades

  ---- carteira ----
  portfolio                              saldos
  height                                 altura atual

  ---- rede ----
  ngrok                                  endpoint NGROK
  peers                                  peers conectados

  ---- diversos ----
  help | quit
"""


def cli_loop(node: Node):
    chain = node.chain
    identity = node.identity

    print(COMMANDS_HELP)

    while node.running:
        try:
            line = input("brn> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd in ("quit", "exit"):
                break

            elif cmd == "help":
                print(COMMANDS_HELP)

            # ------------------- CLOB + MEV -------------------
            elif cmd == "commit" and len(parts) == 4:
                side, price, amount = parts[1], float(parts[2]), float(parts[3])
                r = node.commit_order(side, price, amount)
                if r.get("ok"):
                    print(f"[commit] ok | hash={r['commit_hash']}")
                    print(f"  salt: {r['salt']}")
                    print(f"  auto-reveal em ~2 blocos")
                else:
                    print(f"[commit] erro: {r.get('msg')}")

            elif cmd == "reveal" and len(parts) == 6:
                ch, salt, side = parts[1], parts[2], parts[3]
                price, amount = float(parts[4]), float(parts[5])
                r = node.reveal_order(ch, salt, side, price, amount)
                print(f"[reveal] {r}")

            elif cmd == "commits":
                lst = node.my_commits()
                if not lst:
                    print("  (sem commits)")
                for c in lst:
                    print(f"  {c['status']:>9} | {c['side']:>4} | "
                          f"pronto em {c['blocks_until_ready']}b | "
                          f"expira em {c['blocks_until_expire']}b | "
                          f"{c['commit_hash']}")

            elif cmd == "place" and len(parts) == 4:
                side, price, amount = parts[1], float(parts[2]), float(parts[3])
                r = node.place_order(side, price, amount)
                print(f"[place] {r}")

            elif cmd == "cancel" and len(parts) == 2:
                r = node.cancel_order(parts[1])
                print(f"[cancel] {r}")

            elif cmd == "book":
                depth = int(parts[1]) if len(parts) > 1 else 8
                b = node.order_book(depth=depth)
                print(f"  --- ASKS ({QUOTE} → {BASE}) ---")
                for a in reversed(b["asks"]):
                    print(f"   {a['price']:>12.6f}  "
                          f"{a['amount']:>12.4f}  {a['owner']}")
                print(f"   mid={b['mid']:.6f}   spread={b['spread']:.6f}")
                print(f"  --- BIDS ({QUOTE} → {BASE}) ---")
                for x in b["bids"]:
                    print(f"   {x['price']:>12.6f}  "
                          f"{x['amount']:>12.4f}  {x['owner']}")

            elif cmd == "orders":
                lst = node.my_orders()
                if not lst:
                    print("  (sem ordens abertas)")
                for o in lst:
                    print(f"  {o['side']:>4} {o['price']:.6f} "
                          f"rem={o['remaining']:.4f} "
                          f"| {o['order_id'][:16]}... [{o['status']}]")

            elif cmd == "trades":
                lst = node.my_trades(limit=10)
                if not lst:
                    print("  (sem trades)")
                for t in lst:
                    print(f"  {t['role']:>4} {t['price']:.6f} "
                          f"{t['amount']:.4f} = {t['cost']:.4f} {t['quote']}")

            # ------------------- carteira -------------------
            elif cmd == "portfolio":
                pf = node.portfolio()
                print(json.dumps(pf, indent=2, default=str))

            elif cmd == "height":
                print(f"  altura={chain.height}  tip={chain.tip_hash[:16]}")

            # ------------------- rede -------------------
            elif cmd == "ngrok":
                if node.public_endpoint:
                    h, p = node.public_endpoint
                    print(f"  NGROK ativo: {h}:{p}")
                    print(f"  Peers devem usar: "
                          f"BRN_HARDCODED_SEEDS={h}:{p}")
                else:
                    print("  NGROK inativo (BRN_NGROK=0 ou subindo)")

            elif cmd == "peers":
                if not node.pm.peers:
                    print("  (sem peers)")
                for k, peer in node.pm.peers.items():
                    ago = int(time.time() - peer.last_seen)
                    direc = "out" if peer.outbound else "in "
                    print(f"  {direc} {k[0]}:{k[1]}  h={peer.height}  "
                          f"last={ago}s")

            else:
                print(f"[cli] comando desconhecido: {cmd}")
        except Exception as e:
            print(f"[cli] erro: {e}")


# =====================================================================
# Main
# =====================================================================
def main():
    node = Node()
    print("[main] subindo nó...")
    node.start()

    # espera ngrok, se ativo
    if os.environ.get("BRN_NGROK", "0") == "1":
        wait_ngrok(node, timeout=10.0)

    print_header(node)
    try:
        cli_loop(node)
    finally:
        print("\n[main] encerrando...")
        node.stop()
        time.sleep(0.5)
        print("[main] finalizado.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
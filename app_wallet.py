# app_wallet.py — GUI desktop da carteira RWA (pywebview + node rodando).
import json
import os
import webview
import requests
from cripto_wallet import WalletManager

API = "http://localhost:5000"
AUTH = ("admin", os.environ.get("BRN_WEB_PASS", "admin123"))


class Api:
    def generate_wallet(self):
        return WalletManager.generate_keypair()

    def portfolio(self, address):
        try:
            r = requests.get(f"{API}/api/portfolio/{address}",
                             auth=AUTH, timeout=5)
            return r.json().get("portfolio", {})
        except Exception as e:
            return {"erro": str(e)}

    def transfer(self, sender, to, asset_id, amount, sk, pk):
        try:
            # pega nonce atual no servidor
            r = requests.get(f"{API}/api/portfolio/{sender}",
                             auth=AUTH, timeout=5)
            data = r.json().get("portfolio", {})
            nonce = 0  # provisório; para produção calcule o próximo nonce
            return {"ok": False, "msg": "Envio pela GUI ainda não implementado. Use o CLI."}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def save_wallet(self, filename, password, address, sk, pk):
        return WalletManager.save_encrypted_wallet(filename, password,
                                                   address, sk, pk)

    def load_wallet(self, filename, password):
        return WalletManager.load_encrypted_wallet(filename, password)

    def faucet(self, address):
        try:
            r = requests.post(f"{API}/api/faucet", auth=AUTH,
                              json={"address": address}, timeout=5)
            return r.json()
        except Exception as e:
            return {"ok": False, "msg": str(e)}


if __name__ == "__main__":
    api = Api()
    webview.create_window("BRN RWA — Carteira", "index.html",
                          js_api=api, width=980, height=820)
    webview.start()
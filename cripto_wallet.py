# cripto_wallet.py
# Criptografia profissional: ECDSA (SECP256k1) + AES-256-GCM + Argon2id
import hashlib
import json
import secrets
import base64
import os
from pathlib import Path

from ecdsa import SigningKey, VerifyingKey, SECP256k1, BadSignatureError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from argon2.low_level import hash_secret_raw, Type


class WalletManager:
    # Parâmetros Argon2id (OWASP 2025)
    ARGON2_TIME_COST = 3
    ARGON2_MEMORY_COST = 65536   # 64 MiB
    ARGON2_PARALLELISM = 4
    ARGON2_HASH_LEN = 32
    ARGON2_SALT_LEN = 16
    GCM_NONCE_LEN = 12

    # ---------------- Chaves / Endereços ----------------
    @staticmethod
    def address_from_public_key(public_key_hex: str) -> str:
        public_key = bytes.fromhex(public_key_hex)
        VerifyingKey.from_string(public_key, curve=SECP256k1)  # valida
        digest = hashlib.sha256(public_key).hexdigest()
        return f"brn1{digest[:40]}"

    @staticmethod
    def generate_keypair() -> dict:
        sk = SigningKey.generate(curve=SECP256k1)
        vk = sk.verifying_key
        sk_hex = sk.to_string().hex()
        vk_hex = vk.to_string().hex()
        address = WalletManager.address_from_public_key(vk_hex)
        return {
            "address": address,
            "spend_secret_key": sk_hex,
            "public_key": vk_hex,
        }

    # ---------------- Assinaturas digitais ----------------
    @staticmethod
    def sign_transaction(private_key_hex: str, message_dict: dict) -> str:
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        msg_bytes = json.dumps(message_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = sk.sign_deterministic(msg_bytes, hashfunc=hashlib.sha256)
        return signature.hex()

    @staticmethod
    def verify_signature(public_key_hex: str, message_dict: dict, signature_hex: str) -> bool:
        try:
            vk = VerifyingKey.from_string(bytes.fromhex(public_key_hex), curve=SECP256k1)
            msg_bytes = json.dumps(message_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return vk.verify(bytes.fromhex(signature_hex), msg_bytes, hashfunc=hashlib.sha256)
        except (BadSignatureError, ValueError, Exception):
            return False

    # ---------------- Persistência ----------------
    @staticmethod
    def _wallet_dir() -> Path:
        d = Path.cwd() / "wallets"
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    @classmethod
    def _wallet_path(cls, filename: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Nome de arquivo de carteira inválido.")
        if not safe_name.endswith(".wallet"):
            safe_name += ".wallet"
        return cls._wallet_dir() / safe_name

    # ---------------- Save / Load ----------------
    @classmethod
    def save_encrypted_wallet(cls, filename, password, address,
                              spend_secret_key, public_key=""):
        try:
            if not isinstance(password, str) or len(password) < 12:
                return {"status": "erro",
                        "message": "Use uma senha com pelo menos 12 caracteres."}

            filename = cls._wallet_path(filename)
            wallet_data = {
                "address": address,
                "spend_secret_key": spend_secret_key,
                "public_key": public_key,
            }
            raw_json = json.dumps(wallet_data).encode("utf-8")

            salt = secrets.token_bytes(cls.ARGON2_SALT_LEN)
            key = hash_secret_raw(
                secret=password.encode(),
                salt=salt,
                time_cost=cls.ARGON2_TIME_COST,
                memory_cost=cls.ARGON2_MEMORY_COST,
                parallelism=cls.ARGON2_PARALLELISM,
                hash_len=cls.ARGON2_HASH_LEN,
                type=Type.ID,
            )

            nonce = secrets.token_bytes(cls.GCM_NONCE_LEN)
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, raw_json, None)

            payload = {
                "version": 2,
                "method": "aes-256-gcm-argon2id",
                "argon2": {
                    "time_cost": cls.ARGON2_TIME_COST,
                    "memory_cost": cls.ARGON2_MEMORY_COST,
                    "parallelism": cls.ARGON2_PARALLELISM,
                    "hash_len": cls.ARGON2_HASH_LEN,
                },
                "salt": base64.b64encode(salt).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            }

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            try:
                os.chmod(filename, 0o600)
            except OSError:
                pass

            return {"status": "sucesso",
                    "message": f"Carteira salva com seguranca em {filename}"}
        except Exception as e:
            return {"status": "erro", "message": str(e)}

    @classmethod
    def load_encrypted_wallet(cls, filename, password):
        try:
            filename = cls._wallet_path(filename)
            if not filename.exists():
                return {"status": "erro", "message": "Arquivo nao encontrado."}

            with open(filename, "r", encoding="utf-8") as f:
                payload = json.load(f)

            if payload.get("method") != "aes-256-gcm-argon2id":
                return {"status": "erro",
                        "message": "Formato antigo/inseguro. Reexporte a carteira."}

            salt = base64.b64decode(payload["salt"])
            nonce = base64.b64decode(payload["nonce"])
            ciphertext = base64.b64decode(payload["ciphertext"])
            p = payload["argon2"]

            key = hash_secret_raw(
                secret=password.encode(),
                salt=salt,
                time_cost=p["time_cost"],
                memory_cost=p["memory_cost"],
                parallelism=p["parallelism"],
                hash_len=p["hash_len"],
                type=Type.ID,
            )

            aesgcm = AESGCM(key)
            try:
                decrypted = json.loads(aesgcm.decrypt(nonce, ciphertext, None).decode())
            except Exception:
                return {"status": "erro", "message": "Senha incorreta."}

            return {
                "status": "sucesso",
                "address": decrypted["address"],
                "spend_secret_key": decrypted["spend_secret_key"],
                "public_key": decrypted.get("public_key", ""),
            }
        except Exception as e:
            return {"status": "erro", "message": f"Erro ao carregar carteira: {e}"}
import hashlib
import json
import secrets
import base64
import os
from pathlib import Path
from ecdsa import SigningKey, VerifyingKey, SECP256k1, BadSignatureError

try:
    from cryptography.fernet import Fernet, InvalidToken
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False
    print("⚠️ AVISO: Instale 'cryptography' para seguranca adequada: pip install cryptography")

class WalletManager:
    PBKDF2_ITERATIONS = 600_000

    @staticmethod
    def address_from_public_key(public_key_hex: str) -> str:
        """Deriva o único endereço BRN válido para uma chave pública."""
        public_key = bytes.fromhex(public_key_hex)
        # Também valida o formato da chave secp256k1 antes de aceitá-la.
        VerifyingKey.from_string(public_key, curve=SECP256k1)
        return f"brn1{hashlib.sha256(public_key).hexdigest()[:40]}"

    @staticmethod
    def generate_keypair():
        """Gera par de chaves ECDSA (SECP256k1)"""
        sk = SigningKey.generate(curve=SECP256k1)
        vk = sk.verifying_key
        sk_hex = sk.to_string().hex()
        vk_hex = vk.to_string().hex()
        
        # Endereço derivado do hash SHA256 da chave publica
        address = WalletManager.address_from_public_key(vk_hex)
        return {
            "address": address,
            "spend_secret_key": sk_hex,
            "public_key": vk_hex
        }

    @staticmethod
    def sign_transaction(private_key_hex: str, message_dict: dict) -> str:
        """Assina digitalmente uma transacao"""
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        msg_bytes = json.dumps(message_dict, sort_keys=True).encode('utf-8')
        signature = sk.sign_deterministic(msg_bytes, hashfunc=hashlib.sha256)
        return signature.hex()

    @staticmethod
    def verify_signature(public_key_hex: str, message_dict: dict, signature_hex: str) -> bool:
        """Valida a assinatura digital de uma transacao"""
        try:
            vk = VerifyingKey.from_string(bytes.fromhex(public_key_hex), curve=SECP256k1)
            msg_bytes = json.dumps(message_dict, sort_keys=True).encode('utf-8')
            signature = bytes.fromhex(signature_hex)
            try:
                return vk.verify(signature, msg_bytes, hashfunc=hashlib.sha256)
            except BadSignatureError:
                # Mantém legíveis as transações criadas por versões anteriores.
                return vk.verify(signature, msg_bytes)
        except (BadSignatureError, Exception):
            return False

    @staticmethod
    def _wallet_path(filename: str) -> Path:
        """Evita que a interface grave carteiras em caminhos arbitrários."""
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Nome de arquivo de carteira inválido.")
        if not safe_name.endswith(".wallet"):
            safe_name += ".wallet"
        wallet_dir = Path.cwd() / "wallets"
        wallet_dir.mkdir(mode=0o700, exist_ok=True)
        return wallet_dir / safe_name

    @classmethod
    def save_encrypted_wallet(cls, filename, password, address, spend_secret_key, public_key=""):
        try:
            if not FERNET_AVAILABLE:
                return {"status": "erro", "message": "A biblioteca cryptography é obrigatória para salvar carteiras com segurança."}
            if not isinstance(password, str) or len(password) < 12:
                return {"status": "erro", "message": "Use uma senha com pelo menos 12 caracteres."}
            filename = cls._wallet_path(filename)
            
            wallet_data = {
                "address": address, 
                "spend_secret_key": spend_secret_key,
                "public_key": public_key
            }
            raw_json = json.dumps(wallet_data).encode('utf-8')
            
            salt = secrets.token_bytes(16)
            key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, cls.PBKDF2_ITERATIONS)
            ciphertext = Fernet(base64.urlsafe_b64encode(key)).encrypt(raw_json)
            file_payload = {
                "method": "fernet-pbkdf2-sha256",
                "iterations": cls.PBKDF2_ITERATIONS,
                "salt": base64.b64encode(salt).decode('utf-8'),
                "ciphertext": base64.b64encode(ciphertext).decode('utf-8')
            }
                
            with open(filename, "w") as f:
                json.dump(file_payload, f)
            return {"status": "sucesso", "message": f"Carteira salva com segurança em {filename}"}
        except Exception as e:
            return {"status": "erro", "message": str(e)}

    @classmethod
    def load_encrypted_wallet(cls, filename, password):
        try:
            filename = cls._wallet_path(filename)
            if not filename.exists():
                # Compatibilidade de leitura: backups antigos na pasta do app
                # podem ser importados uma vez e então reexportados no cofre.
                legacy_file = Path.cwd() / filename.name
                if legacy_file.exists():
                    filename = legacy_file
                else:
                    return {"status": "erro", "message": "Arquivo nao encontrado."}
                
            with open(filename, "r") as f:
                file_payload = json.load(f)
                
            method = file_payload.get("method", "xor")
            salt = base64.b64decode(file_payload["salt"])
            ciphertext = base64.b64decode(file_payload["ciphertext"])
            
            if method in {"fernet", "fernet-pbkdf2-sha256"} and FERNET_AVAILABLE:
                iterations = int(file_payload.get("iterations", 100_000))
                key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
                key_b64 = base64.urlsafe_b64encode(key)
                try:
                    cipher = Fernet(key_b64)
                    decrypted_data = json.loads(cipher.decrypt(ciphertext).decode('utf-8'))
                except InvalidToken:
                    return {"status": "erro", "message": "Senha incorreta."}
            else:
                return {"status": "erro", "message": "Formato de carteira antigo ou inseguro. Crie um novo backup criptografado."}

            return {
                "status": "sucesso",
                "address": decrypted_data["address"],
                "spend_secret_key": decrypted_data["spend_secret_key"],
                "public_key": decrypted_data.get("public_key", "")
            }
        except Exception as e:
            return {"status": "erro", "message": f"Erro ao carregar carteira: {str(e)}"}

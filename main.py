# main.py
import os
from cripto_wallet import WalletManager

def menu():
    print("\n=== BRN Bruno ===")
    print("1 - Gerar nova carteira")
    print("2 - Salvar carteira cifrada")
    print("3 - Carregar carteira")
    print("4 - Sair")
    return input("Escolha: ").strip()

def main():
    while True:
        op = menu()
        if op == "1":
            w = WalletManager.generate_keypair()
            print(json_pretty(w))
        elif op == "2":
            name = input("Nome do arquivo (.wallet): ")
            pwd  = input("Senha (>=12 chars): ")
            addr = input("Endereço: ")
            sk   = input("Chave privada (hex): ")
            pk   = input("Chave pública (hex): ")
            print(WalletManager.save_encrypted_wallet(name, pwd, addr, sk, pk))
        elif op == "3":
            name = input("Nome do arquivo (.wallet): ")
            pwd  = input("Senha: ")
            print(WalletManager.load_encrypted_wallet(name, pwd))
        elif op == "4":
            break
        else:
            print("Opção inválida.")

def json_pretty(d):
    import json
    return json.dumps(d, indent=2)

if __name__ == "__main__":
    main()
# main.py - Executor do no BRN + tunel Ngrok
import os
import sys
import subprocess
import threading
import time

# ---- Configuracao do Ngrok ----
NGROK_AUTHTOKEN = os.environ.get(
    "NGROK_AUTHTOKEN",
    "3J8xHeVX46aXrOZeXnKrVVFMLTr_6tcrWjc6EaxX218rXJwJ4",
)
NGROK_PORT = 8080  # porta do explorer.py

# ---- Porta do no BRN (default 6001) ----
BRN_PORT = sys.argv[1] if len(sys.argv) > 1 else "6001"


def _ngrok_worker():
    """Aplica o token e sobe o tunel. Roda em thread separada."""
    try:
        # 1. Aplica o token (idempotente, pode rodar varias vezes)
        subprocess.run(
            ["ngrok", "config", "add-authtoken", NGROK_AUTHTOKEN],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 2. Sobe o tunel para a porta do explorer
        subprocess.Popen(
            ["ngrok", "http", str(NGROK_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        print(f"[ngrok] Tunel aberto para a porta {NGROK_PORT}")
        print(f"[ngrok] Painel local: http://127.0.0.1:4040")

    except FileNotFoundError:
        print("[ngrok] Binario 'ngrok' nao encontrado no PATH.")
        print("[ngrok] Rode instalar_ngrok.bat primeiro ou baixe em https://ngrok.com/download")
    except subprocess.CalledProcessError as e:
        print(f"[ngrok] Falha ao configurar authtoken: {e}")
    except Exception as e:
        print(f"[ngrok] Erro ao iniciar tunel: {e}")


def iniciar_ngrok():
    """Dispara o Ngrok em thread daemon, sem travar a GUI."""
    t = threading.Thread(target=_ngrok_worker, daemon=True)
    t.start()


def main():
    # Garante que roda a partir da pasta do projeto
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    print(f"[BRN] Iniciando no na porta {BRN_PORT} ...")

    # Sobe o Ngrok primeiro (em background) para nao perder tempo
    iniciar_ngrok()

    # Espera curtinhas para o ngrok configurar antes do explorer subir
    time.sleep(1)

    # Executa o programa principal do BRN como se fosse __main__
    import runpy
    sys.argv = ["bruno_blockchain_real.py", BRN_PORT]
    runpy.run_path("bruno_blockchain_real.py", run_name="__main__")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
install.py — roda Bruno.py e empacota o resultado em brn-estavel.tar.gz.
Uso:  python install.py
Requer: Bruno.py no mesmo diretório.
"""
from __future__ import annotations
import hashlib
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRUNO = HERE / "Bruno.py"
PROJECT = HERE / "brn-estavel"
TARBALL = HERE / "brn-estavel.tar.gz"

# Diretórios/arquivos que NÃO entram no tarball
EXCLUDE = {
    "__pycache__", ".venv", "venv", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "data",
    "wallet.brn", "wallet.brn.tmp",
}


def run_bruno() -> None:
    if not BRUNO.exists():
        sys.exit(f"Bruno.py não encontrado em {HERE}")
    print(f"→ Rodando {BRUNO.name}…")
    subprocess.run([sys.executable, str(BRUNO)], cwd=HERE, check=True)


def make_tarball() -> str:
    if not PROJECT.exists():
        sys.exit(f"Projeto não foi gerado em {PROJECT}")
    print(f"→ Criando {TARBALL.name}…")
    with tarfile.open(TARBALL, "w:gz") as tar:
        for path in sorted(PROJECT.rglob("*")):
            if any(part in EXCLUDE for part in path.parts):
                continue
            tar.add(path, arcname=path.relative_to(HERE))
    digest = hashlib.sha256(TARBALL.read_bytes()).hexdigest()
    return digest


def main() -> None:
    run_bruno()
    digest = make_tarball()
    size_kb = TARBALL.stat().st_size / 1024
    print()
    print(f"  arquivo: {TARBALL.name}")
    print(f"  tamanho: {size_kb:.1f} KB")
    print(f"  sha256:  {digest}")
    print()
    print("Para extrair em outra máquina:")
    print(f"  tar -xzf {TARBALL.name}")
    print("  cd brn-estavel && python -m venv .venv && "
          "source .venv/bin/activate && pip install -r requirements.txt")


if __name__ == "__main__":
    main()
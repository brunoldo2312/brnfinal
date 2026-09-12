# tests/test_blockchain.py
# Testes críticos para lógica financeira.

import os
import tempfile
import pytest

# Configura senha antes de importar (node não é importado aqui)
os.environ.setdefault("BRN_MASTER_PASSWORD", "x" * 24)

from cripto_wallet import WalletManager
from bruno_blockchain_real import (
    Blockchain, Block, Transaction, State, SlashingEvidence,
)


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


@pytest.fixture
def alice():
    return WalletManager.generate_keypair()


@pytest.fixture
def bob():
    return WalletManager.generate_keypair()


# ------------------------------------------------------------------
# Estado
# ------------------------------------------------------------------
def test_apply_transaction_sufficient(alice, bob):
    s = State()
    s.credit(alice["address"], 100)
    tx = {"from": alice["address"], "to": bob["address"],
          "amount": 50, "nonce": 0}
    assert s.apply_transaction(tx) is True
    assert s.balance(alice["address"]) == 50
    assert s.balance(bob["address"]) == 50
    assert s.nonce(alice["address"]) == 1


def test_apply_transaction_insufficient(alice, bob):
    s = State()
    s.credit(alice["address"], 10)
    tx = {"from": alice["address"], "to": bob["address"],
          "amount": 100, "nonce": 0}
    assert s.apply_transaction(tx) is False
    assert s.balance(alice["address"]) == 10


def test_double_spend_rejected(alice, bob):
    s = State()
    s.credit(alice["address"], 100)
    tx = {"from": alice["address"], "to": bob["address"],
          "amount": 50, "nonce": 0}
    assert s.apply_transaction(tx) is True
    # Repete o mesmo nonce → deve falhar
    assert s.apply_transaction(tx) is False


# ------------------------------------------------------------------
# Assinaturas
# ------------------------------------------------------------------
def test_valid_signature(alice, bob):
    tx = Transaction.build(alice["address"], bob["address"], 10, 0,
                           alice["spend_secret_key"], alice["public_key"])
    assert Transaction.verify_signature(tx) is True


def test_invalid_signature(alice, bob):
    tx = Transaction.build(alice["address"], bob["address"], 10, 0,
                           alice["spend_secret_key"], alice["public_key"])
    tx["signature"] = "ff" * 32
    assert Transaction.verify_signature(tx) is False


def test_tampered_amount_fails(alice, bob):
    tx = Transaction.build(alice["address"], bob["address"], 10, 0,
                           alice["spend_secret_key"], alice["public_key"])
    tx["amount"] = 999999.0
    assert Transaction.verify_signature(tx) is False


# ------------------------------------------------------------------
# Bloco
# ------------------------------------------------------------------
def test_block_sign_and_verify(alice):
    blk = Block(index=1, timestamp=1.0, previous_hash="0" * 64,
                transactions=[], validator=alice["address"],
                validator_public_key=alice["public_key"])
    blk.finalize()
    blk.sign(alice["spend_secret_key"])
    assert blk.verify() is True


def test_block_tamper_fails(alice):
    blk = Block(index=1, timestamp=1.0, previous_hash="0" * 64,
                transactions=[], validator=alice["address"],
                validator_public_key=alice["public_key"])
    blk.finalize()
    blk.sign(alice["spend_secret_key"])
    blk.transactions = [{"fake": True}]
    assert blk.verify() is False


# ------------------------------------------------------------------
# Blockchain
# ------------------------------------------------------------------
def test_genesis_created(tmp_db):
    bc = Blockchain(db_path=tmp_db)
    assert len(bc.chain) == 1
    assert bc.chain[0].index == 0


def test_persist_and_reload(tmp_db, alice):
    bc = Blockchain(db_path=tmp_db, node_identity=alice,
                    genesis_allocations={alice["address"]: 1000.0})
    assert bc.state.balance(alice["address"]) == 1000.0

    # Reabre o mesmo DB
    bc2 = Blockchain(db_path=tmp_db)
    assert bc2.state.balance(alice["address"]) == 1000.0


def test_add_and_confirm_transaction(tmp_db, alice, bob):
    bc = Blockchain(db_path=tmp_db, node_identity=alice,
                    genesis_allocations={alice["address"]: 1000.0})
    tx = Transaction.build(alice["address"], bob["address"], 100, 0,
                           alice["spend_secret_key"], alice["public_key"])
    r = bc.add_transaction(tx)
    assert r["ok"] is True

    blk = bc.produce_block()
    assert blk is not None
    assert bc.state.balance(bob["address"]) == 100.0


# ------------------------------------------------------------------
# Slashing
# ------------------------------------------------------------------
def test_slashing_evidence_valid(tmp_db, alice):
    # Bloco deliberadamente inválido (assinatura corrompida)
    bad = Block(index=1, timestamp=1.0, previous_hash="0" * 64,
                transactions=[], validator=alice["address"],
                validator_public_key=alice["public_key"])
    bad.finalize()
    bad.sign(alice["spend_secret_key"])
    bad.signature = "00" * 32  # corrompe

    # Outro nó reporta
    reporter = WalletManager.generate_keypair()
    ev = SlashingEvidence.build(
        invalid_block=bad, reason="test",
        reporter_sk=reporter["spend_secret_key"],
        reporter_pk=reporter["public_key"],
    )
    assert ev.verify() is True

    bc = Blockchain(db_path=tmp_db)
    assert bc.submit_slashing_evidence(ev) is True
    assert alice["address"] in bc.slashed


def test_slashing_evidence_fake_rejected(tmp_db, alice):
    # Bloco válido — evidência deve ser rejeitada
    good = Block(index=1, timestamp=1.0, previous_hash="0" * 64,
                 transactions=[], validator=alice["address"],
                 validator_public_key=alice["public_key"])
    good.finalize()
    good.sign(alice["spend_secret_key"])

    reporter = WalletManager.generate_keypair()
    ev = SlashingEvidence.build(
        invalid_block=good, reason="fake",
        reporter_sk=reporter["spend_secret_key"],
        reporter_pk=reporter["public_key"],
    )
    assert ev.verify() is False
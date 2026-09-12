# assets.py — Registro de ativos do mundo real (RWA) + KYC/AML.
#
# Conceitos:
#   AssetDefinition  → metadados legais/financeiros do ativo tokenizado
#   ComplianceRecord → status KYC/AML de um endereço
#   AssetRegistry    → regras de negócio (pode emitir, transferir, congelar)

from __future__ import annotations
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# ==================================================================
# Tipos de ativo
# ==================================================================
ASSET_TYPES = {
    "currency",     # moeda nativa (BRN) ou stablecoin
    "equity",       # ação tokenizada
    "bond",         # título de dívida
    "fund",         # cota de fundo
    "real_estate",  # imóvel / REIT
    "commodity",    # ouro, petróleo, grãos
    "invoice",      # recebível / duplicata
    "carbon",       # crédito de carbono
    "other",
}

KYC_STATUS = {"pending", "approved", "rejected", "expired", "revoked"}
KYC_LEVELS = {"basic", "accredited", "institutional"}


# ==================================================================
# Definição do ativo
# ==================================================================
@dataclass
class AssetDefinition:
    asset_id: str                 # "BRN", "AAPL", "RE-001"
    name: str                     # "Apple Inc. Tokenizado"
    symbol: str                   # "AAPL"
    asset_type: str               # ver ASSET_TYPES
    decimals: int                 # 2, 8, 18
    issuer: str                   # endereço emissor (brn1…)
    transfer_agent: str           # quem aprova KYC
    custodian: str = ""           # custodiante legal (CNPJ, nome)
    isin: str = ""                # código ISO 6166
    total_supply: float = 0.0
    max_supply: float = 0.0       # 0 = sem limite
    transfer_restricted: bool = True   # exige KYC
    legal_doc_hash: str = ""      # SHA3 do prospecto
    created_at: float = field(default_factory=time.time)
    frozen: bool = False          # congela TODAS as transferências
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AssetDefinition":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ==================================================================
# Registro de compliance (KYC/AML)
# ==================================================================
@dataclass
class ComplianceRecord:
    address: str
    status: str = "pending"        # ver KYC_STATUS
    level: str = "basic"           # ver KYC_LEVELS
    jurisdiction: str = ""         # "BR", "US", "EU"
    verified_by: str = ""          # endereço que aprovou
    verified_at: float = 0.0
    expires_at: float = 0.0        # 0 = sem expiração
    restrictions: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def is_valid(self, now: Optional[float] = None) -> bool:
        if self.status != "approved":
            return False
        if self.expires_at and (now or time.time()) > self.expires_at:
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ComplianceRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ==================================================================
# Regras de negócio por ativo
# ==================================================================
@dataclass
class TransferRule:
    """Regra opcional aplicada a transferências de um ativo."""
    require_kyc_sender: bool = True
    require_kyc_receiver: bool = True
    min_kyc_level: str = "basic"
    allowed_jurisdictions: List[str] = field(default_factory=list)  # vazio = todas
    blocked_jurisdictions: List[str] = field(default_factory=list)
    max_holding_per_address: float = 0.0     # 0 = sem limite
    lockup_until: float = 0.0                # 0 = sem lockup
    min_transfer_amount: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TransferRule":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class AssetRegistry:
    """
    Guarda definições de ativos, registros de compliance e regras.
    NÃO guarda saldos — isso é responsabilidade do State da blockchain.
    """

    def __init__(self):
        self.assets: Dict[str, AssetDefinition] = {}
        self.compliance: Dict[str, ComplianceRecord] = {}   # address -> record
        self.rules: Dict[str, TransferRule] = {}            # asset_id -> rule
        self.regulator: str = ""                            # endereço regulador

    # ---------------- Ativos ----------------
    def add_asset(self, asset: AssetDefinition) -> bool:
        if asset.asset_id in self.assets:
            return False
        if asset.asset_type not in ASSET_TYPES:
            return False
        if not asset.issuer or not asset.issuer.startswith("brn1"):
            return False
        self.assets[asset.asset_id] = asset
        if asset.asset_id not in self.rules:
            self.rules[asset.asset_id] = TransferRule()
        return True

    def update_asset(self, asset_id: str, updates: dict) -> bool:
        a = self.assets.get(asset_id)
        if not a:
            return False
        for k, v in updates.items():
            if hasattr(a, k) and k not in ("asset_id", "created_at"):
                setattr(a, k, v)
        return True

    def get_asset(self, asset_id: str) -> Optional[AssetDefinition]:
        return self.assets.get(asset_id)

    # ---------------- Compliance ----------------
    def set_compliance(self, rec: ComplianceRecord) -> bool:
        if rec.status not in KYC_STATUS or rec.level not in KYC_LEVELS:
            return False
        self.compliance[rec.address] = rec
        return True

    def get_compliance(self, addr: str) -> Optional[ComplianceRecord]:
        return self.compliance.get(addr)

    def is_kyc_valid(self, addr: str, min_level: str = "basic") -> bool:
        rec = self.compliance.get(addr)
        if not rec or not rec.is_valid():
            return False
        order = {"basic": 0, "accredited": 1, "institutional": 2}
        return order.get(rec.level, -1) >= order.get(min_level, 0)

    # ---------------- Regras ----------------
    def set_rule(self, asset_id: str, rule: TransferRule) -> bool:
        if asset_id not in self.assets:
            return False
        self.rules[asset_id] = rule
        return True

    def get_rule(self, asset_id: str) -> TransferRule:
        return self.rules.get(asset_id, TransferRule())

    # ---------------- Permissões ----------------
    def can_issue(self, asset_id: str, addr: str) -> bool:
        a = self.assets.get(asset_id)
        return bool(a and a.issuer == addr)

    def can_manage_kyc(self, asset_id: str, addr: str) -> bool:
        a = self.assets.get(asset_id)
        if not a:
            return False
        return addr in (a.issuer, a.transfer_agent) or addr == self.regulator

    def can_freeze(self, asset_id: str, addr: str) -> bool:
        a = self.assets.get(asset_id)
        if not a:
            return False
        return addr in (a.issuer, a.transfer_agent, self.regulator)

    # ---------------- Validação de transferência ----------------
    def validate_transfer(self, asset_id: str, sender: str, receiver: str,
                          amount: float,
                          receiver_balance_after: float) -> tuple[bool, str]:
        a = self.assets.get(asset_id)
        if not a:
            return False, f"ativo '{asset_id}' não existe"
        if a.frozen:
            return False, "ativo congelado pelo emissor/regulador"
        if amount <= 0:
            return False, "valor deve ser positivo"

        rule = self.get_rule(asset_id)
        if rule.lockup_until and time.time() < rule.lockup_until:
            return False, f"lockup ativo até {rule.lockup_until:.0f}"
        if amount < rule.min_transfer_amount:
            return False, f"abaixo do mínimo ({rule.min_transfer_amount})"

        if a.transfer_restricted:
            if rule.require_kyc_sender and not self.is_kyc_valid(sender, rule.min_kyc_level):
                return False, f"remetente sem KYC válido ({rule.min_kyc_level})"
            if rule.require_kyc_receiver and not self.is_kyc_valid(receiver, rule.min_kyc_level):
                return False, f"destinatário sem KYC válido ({rule.min_kyc_level})"

            rec = self.compliance.get(receiver)
            if rec:
                if rule.allowed_jurisdictions and rec.jurisdiction not in rule.allowed_jurisdictions:
                    return False, f"jurisdição {rec.jurisdiction} não permitida"
                if rec.jurisdiction in rule.blocked_jurisdictions:
                    return False, f"jurisdição {rec.jurisdiction} bloqueada"
                if "block_transfer" in rec.restrictions:
                    return False, "destinatário com restrição de transferência"

        if rule.max_holding_per_address > 0 and receiver_balance_after > rule.max_holding_per_address:
            return False, f"destinatário excederia o máximo ({rule.max_holding_per_address})"

        return True, "ok"

    # ---------------- Serialização ----------------
    def to_dict(self) -> dict:
        return {
            "assets": {k: v.to_dict() for k, v in self.assets.items()},
            "compliance": {k: v.to_dict() for k, v in self.compliance.items()},
            "rules": {k: v.to_dict() for k, v in self.rules.items()},
            "regulator": self.regulator,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AssetRegistry":
        r = cls()
        r.regulator = d.get("regulator", "")
        for k, v in d.get("assets", {}).items():
            r.assets[k] = AssetDefinition.from_dict(v)
        for k, v in d.get("compliance", {}).items():
            r.compliance[k] = ComplianceRecord.from_dict(v)
        for k, v in d.get("rules", {}).items():
            r.rules[k] = TransferRule.from_dict(v)
        return r

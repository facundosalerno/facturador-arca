from datetime import date
from dataclasses import dataclass
from typing import List
from src.const import IVACondicion, CbteTipo, AlicuotaIVAId
from src.webservices.padron import PersonaInfo

@dataclass
class ClientePlurals:
    cuit: str
    descripcion: str
    colaboradores: int
    imp_neto: float
    bonificacion_pct: float
    iva: float
    items: List[ItemFacturadoPlurals]
    iva_cond: IVACondicion
    cbte_tipo: CbteTipo
    last_adjustment: date
    padron: PersonaInfo | None = None

    @property
    def imp_neto_efectivo(self) -> float:
        return round(self.imp_neto * (1 - self.bonificacion_pct / 100), 2)

    @property
    def imp_iva(self) -> float:
        return round(self.imp_neto_efectivo * self.iva / 100, 2)

    @property
    def imp_total(self) -> float:
        return round(self.imp_neto_efectivo + self.imp_iva, 2)

    @property
    def alicuota_iva_id(self) -> AlicuotaIVAId:
        return AlicuotaIVAId.from_pct(self.iva)

@dataclass
class ItemFacturadoPlurals:
    imp_neto: float
    colaboradores: int
    detalle: str | None = None
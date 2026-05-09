from datetime import date
from dataclasses import dataclass
from typing import List
from src.const import IVACondicion, CbteTipo

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

@dataclass
class ItemFacturadoPlurals:
    imp_neto: float
    colaboradores: int
    detalle: str | None = None
from __future__ import annotations

import os
from enum import IntEnum, StrEnum

class Mode(StrEnum):
    HOMOLOGACION = "homologacion"
    PRODUCCION = "produccion"

# ---------------------------------------------------------------------------
# valores definidos por ARCA en el manual WSFEv1
# ---------------------------------------------------------------------------

class CbteTipo(IntEnum):
    FACTURA_A = 1
    FACTURA_B = 6
    FACTURA_C = 11

    @classmethod
    def from_str(cls, value: str) -> CbteTipo:
        letters = {"A": cls.FACTURA_A, "B": cls.FACTURA_B, "C": cls.FACTURA_C}
        if not value.upper() in letters:
            raise ValueError(f"Unsupported cbte type: {value}. Supported: {list(letters)}")
        return letters[value.upper()]


class EmisionTipo(StrEnum):
    CAE  = "CAE"   # autorización online al momento de emitir
    CAEA = "CAEA"  # autorización anticipada (batch, sin conexión al momento de emitir)

    @classmethod
    def from_raw(cls, value: str) -> EmisionTipo:
        # AFIP devuelve strings descriptivos como "CAE - Ri Iva", "CAEA - Ri Iva", etc.
        # Solo nos interesa el prefijo para determinar el tipo.
        if value.startswith("CAEA"):
            return cls.CAEA
        if value.startswith("CAE"):
            return cls.CAE
        raise ValueError(f"EmisionTipo desconocido: {value!r}")


class Concepto(IntEnum):
    PRODUCTOS = 1
    SERVICIOS = 2
    PRODUCTOS_Y_SERVICIOS = 3


class DocTipo(IntEnum):
    CUIT = 80
    DNI = 96
    CONSUMIDOR_FINAL = 99


class AlicuotaIVAId(IntEnum):
    """IDs internos de ARCA para cada alícuota de IVA."""
    PCT_0 = 3
    PCT_10_5 = 4
    PCT_21 = 5
    PCT_27 = 6

    @classmethod
    def from_pct(cls, pct: float) -> AlicuotaIVAId:
        mapping = {0.0: cls.PCT_0, 10.5: cls.PCT_10_5, 21.0: cls.PCT_21, 27.0: cls.PCT_27}
        if pct not in mapping:
            raise ValueError(f"Unsupported IVA rate: {pct}%. Supported: {list(mapping)}")
        return mapping[pct]


class IVACondicion(IntEnum):
    """CondicionIVAReceptorId — obligatorio desde RG 5616."""
    RESPONSABLE_INSCRIPTO = 1
    EXENTO = 4
    CONSUMIDOR_FINAL = 5
    MONOTRIBUTISTA = 6

    @classmethod
    def from_str(cls, value: str) -> IVACondicion:
        letters = {"RI": cls.RESPONSABLE_INSCRIPTO, "E": cls.EXENTO, "CF": cls.CONSUMIDOR_FINAL, "M": cls.MONOTRIBUTISTA}
        if not value.upper() in letters:
            raise ValueError(f"Unsupported cbte type: {value}. Supported: {list(letters)}")
        return letters[value.upper()]


class FacturaResultado(str):
    APROBADO = "A"
    RECHAZADO = "R"
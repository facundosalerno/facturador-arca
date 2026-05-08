from typing import List
import pandas as pd
from pathlib import Path
from datetime import date
from dateutil.relativedelta import relativedelta
from src.input.interface import ClientePlurals, ItemFacturadoPlurals
from src.const import IVACondicion, CbteTipo

SHEET_NAME = "Clientes"
MESES_AJUSTE = 3

COLUMN_MAP = {
    # "columna en el excel": "campo en ClientePlurals / ItemFacturadoPlurals"
    "CUIT": "cuit",
    "EMPRESA": "descripcion",
    "Detalle": "detalle",
    "Colaboradores": "colaboradores",
    "Importe neto": "imp_neto",
    "Descuento %": "bonificacion_pct",
    "Último ajuste": "last_adjustment",
    "IVA": "iva",
    "Cond IVA": "iva_cond",
    "Factura": "cbte_tipo"
}

# Columnas mergeadas en el Excel: tienen valor solo en la primera fila del grupo CUIT
GROUPED_COLS = ["cuit", "iva", "iva_cond", "cbte_tipo"]


def _parse_cuit(value) -> str:
    cuit = str(int(float(value)))
    if len(cuit) != 11:
        raise ValueError(f"CUIT inválido (debe tener 11 dígitos): '{cuit}'")
    return cuit


def read_clientes(path: Path, con_ajuste: bool | None = None) -> List[ClientePlurals]:
    df = pd.read_excel(path, sheet_name=SHEET_NAME)
    df = df.rename(columns=COLUMN_MAP)

    # Las columnas mergeadas aparecen como NaN en las sub-filas; las propagamos hacia abajo
    df[GROUPED_COLS] = df[GROUPED_COLS].ffill()

    # Eliminar filas vacías al final del sheet (sin CUIT ni después del ffill)
    df = df.dropna(subset=["cuit"])

    if con_ajuste is not None:
        adjustment_month = date.today() - relativedelta(months=MESES_AJUSTE)
        dates = pd.to_datetime(df["last_adjustment"])
        is_adjustment = (dates.dt.year == adjustment_month.year) & (dates.dt.month == adjustment_month.month)
        df = df[is_adjustment if con_ajuste else ~is_adjustment]

    # Eliminar filas sin datos requeridos por item
    df = df.dropna(subset=["colaboradores", "imp_neto", "bonificacion_pct"])  # pyright: ignore[reportCallIssue]

    result = []
    for cuit_raw, group in df.groupby("cuit", sort=False):
        first = group.iloc[0]

        items = [
            ItemFacturadoPlurals(
                imp_neto=float(row["imp_neto"]),                                                                   # pyright: ignore[reportArgumentType]
                bonificacion_pct=float(row["bonificacion_pct"]),                                                   # pyright: ignore[reportArgumentType]
                colaboradores=int(row["colaboradores"]),                                                           # pyright: ignore[reportArgumentType]
                detalle=str(row["detalle"]) if pd.notna(row["detalle"]) and str(row["detalle"]) != "-" else None,  # pyright: ignore[reportGeneralTypeIssues]
            )
            for _, row in group.iterrows()
        ]

        result.append(ClientePlurals(
            cuit=_parse_cuit(cuit_raw),
            descripcion=str(first["descripcion"]),
            iva=float(first["iva"]),                                            # pyright: ignore[reportArgumentType]
            iva_cond=IVACondicion.from_str(first["iva_cond"]),            # pyright: ignore[reportArgumentType]
            cbte_tipo=CbteTipo.from_str(first["cbte_tipo"]),              # pyright: ignore[reportArgumentType]
            last_adjustment=pd.to_datetime(first["last_adjustment"]).date(),    # pyright: ignore[reportAttributeAccessIssue]
            items=items,
            # Agregados de los items
            colaboradores=sum(item.colaboradores for item in items),
            imp_neto=sum(item.imp_neto for item in items),
            bonificacion_pct=float(first["bonificacion_pct"]),                  # pyright: ignore[reportArgumentType]
        ))

    return result
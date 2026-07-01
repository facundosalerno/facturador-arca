from typing import List
import pandas as pd
from pathlib import Path
from datetime import date
from dateutil.relativedelta import relativedelta
from src.input.interface import ClientePlurals, ItemFacturadoPlurals
from src.const import IVACondicion, CbteTipo
from src.webservices.padron import PersonaInfo

SHEET_CLIENTES = "Clientes"
SHEET_PADRON = "Padron"

MESES_AJUSTE = 3

COLUMN_MAP_CLIENTES = {
    # "columna en el excel": "campo en ClientePlurals / ItemFacturadoPlurals"
    "CUIT": "cuit",
    "EMPRESA": "descripcion",
    "Detalle": "detalle",
    "Herramientas contratadas": "tools",
    "Colaboradores": "colaboradores",
    "Importe neto": "imp_neto",
    "Descuento %": "bonificacion_pct",
    "Último ajuste": "last_adjustment",
    "IVA": "iva",
    "Cond IVA": "iva_cond",
    "Factura": "cbte_tipo",
    "Done": "done",
}

# Columnas mergeadas en el Excel: tienen valor solo en la primera fila del grupo CUIT
GROUPED_COLS_CLIENTES = ["cuit", "iva", "iva_cond", "cbte_tipo"]

COLUMN_MAP_PADRON = {
    "Razon social": "razon_social",
    "Cuit": "cuit",
    "Domicilio": "domicilio",
    "Condicion IVA": "condicion_iva",
}


def _parse_cuit(value) -> str:
    cuit = str(int(float(value)))
    if len(cuit) != 11:
        raise ValueError(f"CUIT inválido (debe tener 11 dígitos): '{cuit}'")
    return cuit


def _parse_done(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "n")
    return False


def _read_padron_local(path: Path) -> dict[str, PersonaInfo]:
    try:
        df = pd.read_excel(path, sheet_name=SHEET_PADRON)
    except Exception:
        return {}
    df = df.rename(columns=COLUMN_MAP_PADRON)
    df = df.dropna(subset=list(COLUMN_MAP_PADRON.values()))
    result = {}
    for _, row in df.iterrows():
        cuit = _parse_cuit(row["cuit"])
        result[cuit] = PersonaInfo(
            cuit=cuit,
            razon_social=str(row["razon_social"]),
            domicilio=str(row["domicilio"]),
            condicion_iva=IVACondicion.from_str(row["condicion_iva"]),
        )
    return result


def read_clientes(path: Path, con_ajuste: bool | None = None, done: bool | None = None, cuit: str | None = None) -> List[ClientePlurals]:
    padron_local = _read_padron_local(path)
    df = pd.read_excel(path, sheet_name=SHEET_CLIENTES)
    df = df.rename(columns=COLUMN_MAP_CLIENTES)

    # Las columnas mergeadas aparecen como NaN en las sub-filas; las propagamos hacia abajo
    df[GROUPED_COLS_CLIENTES] = df[GROUPED_COLS_CLIENTES].ffill()

    # Convertir "tools" a entero positivo; cualquier otro valor queda NaN y lo descarta el dropna
    # Es un chequeo para no mostrar clientes que aun no tienen tools contratadas
    df["tools"] = pd.to_numeric(df["tools"], errors="coerce").where(lambda x: x > 0)

    # Eliminar filas vacías al final del sheet (sin CUIT ni después del ffill)
    df = df.dropna(subset=[col for col in COLUMN_MAP_CLIENTES.values() if col not in ("detalle", "done")])

    if cuit is not None:
        df = df[df["cuit"].map(_parse_cuit) == cuit]

    if done is not None:
        is_done = df["done"].map(_parse_done)
        df = df[is_done if done else ~is_done]

    if con_ajuste is not None:
        adjustment_month = date.today() - relativedelta(months=MESES_AJUSTE)
        dates = pd.to_datetime(df["last_adjustment"])
        is_adjustment = (dates.dt.year == adjustment_month.year) & (dates.dt.month == adjustment_month.month)  # pyright: ignore[reportAttributeAccessIssue]
        df = df[is_adjustment if con_ajuste else ~is_adjustment]

    result = []
    for cuit_raw, group in df.groupby("cuit", sort=False):  # pyright: ignore[reportAttributeAccessIssue]
        first = group.iloc[0]

        items = [
            ItemFacturadoPlurals(
                imp_neto=float(row["imp_neto"]),                                                                   # pyright: ignore[reportArgumentType]
                colaboradores=int(row["colaboradores"]),                                                           # pyright: ignore[reportArgumentType]
                detalle=str(row["detalle"]) if pd.notna(row["detalle"]) and str(row["detalle"]) != "-" else None,  # pyright: ignore[reportGeneralTypeIssues]
            )
            for _, row in group.iterrows()
        ]

        parsed_cuit = _parse_cuit(cuit_raw)
        result.append(ClientePlurals(
            cuit=parsed_cuit,
            descripcion=str(first["descripcion"]),
            padron=padron_local.get(parsed_cuit),
            iva=float(first["iva"]) * 100,                                      # pyright: ignore[reportArgumentType]
            iva_cond=IVACondicion.from_str(first["iva_cond"]),            # pyright: ignore[reportArgumentType]
            cbte_tipo=CbteTipo.from_str(first["cbte_tipo"]),              # pyright: ignore[reportArgumentType]
            last_adjustment=pd.to_datetime(first["last_adjustment"]).date(),    # pyright: ignore[reportAttributeAccessIssue]
            items=items,
            # Agregados de los items
            colaboradores=sum(item.colaboradores for item in items),
            imp_neto=sum(item.imp_neto for item in items),
            bonificacion_pct=float(first["bonificacion_pct"]) * 100,            # pyright: ignore[reportArgumentType]
        ))

    return result
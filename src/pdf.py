"""Generate a PDF invoice (Factura A/B/C) from CAEResultado + Factura."""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import qrcode
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.webservices.padron import PersonaInfo
from src.webservices.wsfe import CAEResultado, CbteTipo, SolicitudFactura

NAVY    = colors.HexColor("#1B2440")
GRAY_BG = colors.HexColor("#F7F8FA")
GRAY_LN = colors.HexColor("#D0D5DD")
TEXT    = colors.HexColor("#101828")
MUTED   = colors.HexColor("#667085")

_LETTER = {CbteTipo.FACTURA_A: "A", CbteTipo.FACTURA_B: "B", CbteTipo.FACTURA_C: "C"}

# Código de comprobante ARCA por tipo
_CBTE_COD = {CbteTipo.FACTURA_A: "01", CbteTipo.FACTURA_B: "06", CbteTipo.FACTURA_C: "11"}


class CopiaComprobante(str, Enum):
    ORIGINAL    = "ORIGINAL"
    DUPLICADO   = "DUPLICADO"
    TRIPLICADO  = "TRIPLICADO"


class CondicionVenta(str, Enum):
    TRANSFERENCIA_BANCARIA = "Transferencia Bancaria"
    CONTADO                = "Contado"
    CUENTA_CORRIENTE       = "Cuenta Corriente"
    CHEQUE                 = "Cheque"
    OTRO                   = "Otro"


class UnidadMedida(str, Enum):
    UNIDADES = "unidades"
    HORAS    = "horas"
    MESES    = "meses"
    KG       = "kg"
    LITROS   = "litros"
    OTRO     = "otro"


@dataclass
class InvoiceItem:
    descripcion: str
    cantidad: float = 1.0
    precio_unitario: float = 0.0
    bonificacion_pct: float = 0.0
    unidad_medida: UnidadMedida = UnidadMedida.UNIDADES
    codigo: str = ""

    @property
    def subtotal(self) -> float:
        return round(self.cantidad * self.precio_unitario * (1 - self.bonificacion_pct / 100), 2)


def _qr_image(cuit: str, req: SolicitudFactura, result: CAEResultado, size_mm: float) -> Image:
    """Generate ARCA QR code image per spec: https://www.arca.gob.ar/fe/qr/?p=<base64-json>."""
    payload = {
        "ver": 1,
        "fecha": req.fecha.strftime("%Y-%m-%d"),
        "cuit": int(cuit.replace("-", "")),
        "ptoVta": req.pto_vta.nro,
        "tipoCmp": int(req.cbte_tipo),
        "nroCmp": result.cbte_nro,
        "importe": round(req.imp_neto * (1 + req.iva / 100), 2),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": 80,
        "nroDocRec": int(req.receptor_cuit.replace("-", "")),
        "tipoCodAut": "E",
        "codAut": int(result.cae),
    }
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    url = f"https://www.arca.gob.ar/fe/qr/?p={encoded}"

    qr = qrcode.QRCode(error_correction=qrcode.ERROR_CORRECT_M, box_size=10, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return Image(buf, width=size_mm * mm, height=size_mm * mm)


def _fmt_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[:4]}"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def generate_invoice_pdf(
    *,
    cuit_emisor: str,
    req: SolicitudFactura,
    result: CAEResultado,
    output_path: Path,
    emisor: PersonaInfo,
    receptor: PersonaInfo,
    logo_path: Path,
    condicion_venta: CondicionVenta = CondicionVenta.TRANSFERENCIA_BANCARIA,
    items: Optional[list[InvoiceItem]] = None,
    otros_tributos: float = 0.0,
) -> None:
    margin   = 20 * mm
    usable_w = A4[0] - 2 * margin

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin,  bottomMargin=margin,
    )

    def ps(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, **kw)

    letter    = _LETTER[req.cbte_tipo]
    cbte_cod  = _CBTE_COD[req.cbte_tipo]
    fecha     = req.fecha.strftime("%d/%m/%Y")
    cae_vto   = _fmt_date(result.cae_fch_vto)
    imp_iva   = round(req.imp_neto * req.iva / 100, 2)
    imp_total = round(req.imp_neto + imp_iva + otros_tributos, 2)

    # Si no se pasan items, construir uno desde imp_neto
    if items is None:
        items = [InvoiceItem(
            descripcion="Servicios profesionales",
            cantidad=1.0,
            precio_unitario=req.imp_neto,
            unidad_medida=UnidadMedida.UNIDADES,
        )]

    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    badge_inner = Table(
        [
            [_p(letter, ps("badge_l", fontSize=22, fontName="Helvetica-Bold",
                           textColor=NAVY, alignment=TA_CENTER, leading=26))],
            [_p(f"COD. {cbte_cod}", ps("badge_cod", fontSize=6, textColor=MUTED,
                                        alignment=TA_CENTER, leading=8))],
        ],
        colWidths=[16 * mm],
        style=TableStyle([
            ("BOX",          (0, 0), (-1, -1), 1.5, NAVY),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ]),
    )
    story.append(Table(
        [[Image(str(logo_path), width=12 * mm, height=12 * mm),
          _p("plurals", ps("co", fontSize=20, fontName="Helvetica-Bold", textColor=NAVY, leading=24)),
          badge_inner]],
        colWidths=[14 * mm, usable_w - 14 * mm - 20 * mm, 20 * mm],
        style=TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",  (1, 0), (1, 0), 5),
            ("ALIGN",        (2, 0), (2, 0), "CENTER"),
        ]),
    ))
    story.append(Spacer(1, 2 * mm))

    # Emisor sub-info
    emisor_sub = ps("sub_l", fontSize=8, textColor=MUTED)
    emisor_lines = []
    emisor_lines.append(_p(emisor.razon_social, ps("em_rs", fontSize=9, fontName="Helvetica-Bold", textColor=NAVY, leading=12)))
    emisor_lines.append(_p(f"CUIT {cuit_emisor} · Responsable Inscripto", emisor_sub))
    emisor_lines.append(_p(emisor.domicilio, emisor_sub))
    assert emisor.ingresos_brutos, "ingresos_brutos es requerida para el emisor"
    emisor_lines.append(_p(f"Ingresos Brutos: {emisor.ingresos_brutos}", emisor_sub))
    assert emisor.fecha_inicio_actividades, "fecha_inicio_actividades es requerida para el emisor"
    emisor_lines.append(_p(f"Fecha de Inicio de Actividades: {emisor.fecha_inicio_actividades.strftime('%d/%m/%Y')}", emisor_sub))

    left_col_w = 14 * mm + (usable_w - 14 * mm - 20 * mm)
    right_lbl_s = ps("sub_r", fontSize=8, fontName="Helvetica-Bold", textColor=NAVY, alignment=TA_CENTER)
    for i, line in enumerate(emisor_lines):
        story.append(Table(
            [[line, _p("FACTURA " + letter if i == 0 else "", right_lbl_s)]],
            colWidths=[left_col_w, 20 * mm],
            style=TableStyle([
                ("LEFTPADDING",  (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
            ]),
        ))
        if i == 0 and len(emisor_lines) > 1:
            story.append(Spacer(1, 1 * mm))
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1.5, color=NAVY))
    story.append(Spacer(1, 4 * mm))

    # ── Período facturado + Vto. pago ─────────────────────────────────────────
    lbl   = ps("lbl",   fontSize=7, textColor=MUTED, leading=10)
    val   = ps("val",   fontSize=9, textColor=TEXT,  leading=12)
    val_b = ps("val_b", fontSize=9, fontName="Helvetica-Bold", textColor=TEXT, leading=12)
    lbl_b = ps("lbl_b", fontSize=7, fontName="Helvetica-Bold", textColor=MUTED, leading=10)

    periodo_desde = req.serv_desde.strftime("%d/%m/%Y")
    periodo_hasta = req.serv_hasta.strftime("%d/%m/%Y")
    vto_pago_str  = req.vto_pago.strftime("%d/%m/%Y")

    story.append(Table(
        [[
            _p("Período Facturado Desde:", lbl_b),
            _p(periodo_desde, val_b),
            _p("Hasta:", lbl_b),
            _p(periodo_hasta, val_b),
            _p("Fecha de Vto. para el pago:", lbl_b),
            _p(vto_pago_str, val_b),
        ]],
        colWidths=[usable_w * 0.20, usable_w * 0.13, usable_w * 0.07,
                   usable_w * 0.13, usable_w * 0.25, usable_w * 0.22],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]),
    ))
    story.append(Spacer(1, 4 * mm))

    # ── Meta + Receptor ───────────────────────────────────────────────────────
    meta = Table(
        [
            [_p("Punto de Venta", lbl), _p(f"{req.pto_vta.nro:05d}", val)],
            [_p("Comp. Nro.",     lbl), _p(f"{result.cbte_nro:08d}", val_b)],
            [_p("Fecha de Emisión", lbl), _p(fecha,     val)],
            [_p("C.A.E. N°",           lbl), _p(result.cae,  val)],
            [_p("Fecha de Vto. del C.A.E.",      lbl), _p(cae_vto,     val)],
        ],
        colWidths=[30 * mm, usable_w * 0.42 - 30 * mm],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ]),
    )

    receptor_rows = [
        [_p("RECEPTOR", ps("r_hdr", fontSize=7, fontName="Helvetica-Bold", textColor=MUTED))],
    ]
    receptor_rows.append([_p(receptor.razon_social, val_b)])
    iva_label = req.receptor_iva_cond.name.replace("_", " ").title()
    receptor_rows.append([_p(f"CUIT {req.receptor_cuit} · {iva_label}", val_b if not receptor else val)])
    receptor_rows.append([_p(receptor.domicilio, val)])
    receptor_rows.append([_p(f"Condición de venta: {condicion_venta.value}", val)])

    receptor_box = Table(
        receptor_rows,
        colWidths=[usable_w * 0.58 - 4 * mm],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 4 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
            ("TOPPADDING",   (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
            ("BACKGROUND",   (0, 0), (-1, -1), GRAY_BG),
            ("BOX",          (0, 0), (-1, -1), 0.5, GRAY_LN),
        ]),
    )
    story.append(Table(
        [[meta, receptor_box]],
        colWidths=[usable_w * 0.42, usable_w * 0.58],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ]),
    ))
    story.append(Spacer(1, 8 * mm))

    # ── Items ─────────────────────────────────────────────────────────────────
    th   = ps("th",   fontSize=7, fontName="Helvetica-Bold", textColor=colors.white, leading=10)
    th_r = ps("th_r", fontSize=7, fontName="Helvetica-Bold", textColor=colors.white,
              alignment=TA_RIGHT, leading=10)
    td   = ps("td",   fontSize=8, textColor=TEXT, leading=11)
    td_r = ps("td_r", fontSize=8, textColor=TEXT, alignment=TA_RIGHT, leading=11)
    td_c = ps("td_c", fontSize=8, textColor=TEXT, alignment=TA_CENTER, leading=11)

    # Columnas: Cód | Descripción | Cant. | U.med | P.Unit | %Bonif | Subtotal | Alic.IVA | Sub c/IVA
    cw = [
        usable_w * 0.05,  # Código
        usable_w * 0.23,  # Descripción
        usable_w * 0.07,  # Cant.
        usable_w * 0.10,  # U. medida
        usable_w * 0.13,  # Precio unit.
        usable_w * 0.09,  # % Bonif
        usable_w * 0.12,  # Subtotal
        usable_w * 0.08,  # Alíc. IVA
        usable_w * 0.13,  # Subtotal c/IVA
    ]
    header_row = [
        _p("Cód.",            th),
        _p("Producto / Servicio", th),
        _p("Cant.",           th_r),
        _p("U. medida",       th_r),
        _p("Precio Unit.",    th_r),
        _p("% Bonif",         th_r),
        _p("Subtotal",        th_r),
        _p("Alíc. IVA",       th_r),
        _p("Subtotal c/IVA",  th_r),
    ]
    item_rows = [header_row]
    for item in items:
        sub_c_iva = round(item.subtotal * (1 + req.iva / 100), 2)
        item_rows.append([
            _p(item.codigo,                                td),
            _p(item.descripcion,                           td),
            _p(f"{item.cantidad:,.2f}",                    td_r),
            _p(item.unidad_medida.value,                   td_c),
            _p(f"$ {item.precio_unitario:,.2f}",           td_r),
            _p(f"{item.bonificacion_pct:.2f}",             td_r),
            _p(f"$ {item.subtotal:,.2f}",                  td_r),
            _p(f"{req.iva:.0f}%",                  td_c),
            _p(f"$ {sub_c_iva:,.2f}",                      td_r),
        ])

    items_table_style = TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), NAVY),
        ("LINEBELOW",    (0, 0), (-1, -1), 0.4, GRAY_LN),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ])
    story.append(Table(item_rows, colWidths=cw, style=items_table_style))
    story.append(Spacer(1, 3 * mm))

    # ── Totals ────────────────────────────────────────────────────────────────
    tot_l  = ps("tot_l",  fontSize=8,  textColor=MUTED,          alignment=TA_RIGHT, leading=12)
    tot_v  = ps("tot_v",  fontSize=8,  textColor=TEXT,           alignment=TA_RIGHT, leading=12)
    tot_bl = ps("tot_bl", fontSize=10, fontName="Helvetica-Bold", textColor=colors.white,
                alignment=TA_RIGHT, leading=14)
    tot_bv = ps("tot_bv", fontSize=10, fontName="Helvetica-Bold", textColor=colors.white,
                alignment=TA_RIGHT, leading=14)

    # Desglose IVA por alícuota — mostrar todas, 0 si no aplica
    iva_rates = [27.0, 21.0, 10.5, 5.0, 2.5, 0.0]
    iva_rows = []
    for rate in iva_rates:
        amount = round(req.imp_neto * rate / 100, 2) if rate == req.iva else 0.0
        iva_rows.append([
            _p(f"IVA {rate:.1f}%:" if rate != int(rate) else f"IVA {rate:.0f}%:", tot_l),
            _p(f"$ {amount:,.2f}", tot_v),
        ])

    tot_col = 52 * mm
    totals_data = [
        [_p("Importe Neto Gravado:", tot_l), _p(f"$ {req.imp_neto:,.2f}", tot_v)],
        *iva_rows,
        [_p("Importe Otros Tributos:", tot_l), _p(f"$ {otros_tributos:,.2f}", tot_v)],
        [_p("Importe Total:",          tot_bl), _p(f"$ {imp_total:,.2f}",    tot_bv)],
    ]
    total_row_idx = len(totals_data) - 1
    totals = Table(
        totals_data,
        colWidths=[tot_col, tot_col],
        style=TableStyle([
            ("TOPPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
            ("LEFTPADDING",  (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("BACKGROUND",   (0, total_row_idx), (-1, total_row_idx), NAVY),
            ("LINEABOVE",    (0, 1), (-1, 1), 0.4, GRAY_LN),
            ("LINEABOVE",    (0, total_row_idx), (-1, total_row_idx), 1.5, NAVY),
        ]),
    )
    story.append(Table(
        [[Spacer(usable_w - 2 * tot_col, 1), totals]],
        colWidths=[usable_w - 2 * tot_col, 2 * tot_col],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ]),
    ))

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GRAY_LN))
    story.append(Spacer(1, 4 * mm))

    footer_s   = ps("footer",   fontSize=7, textColor=MUTED, alignment=TA_CENTER, leading=10)
    footer_b   = ps("footer_b", fontSize=7, fontName="Helvetica-Bold", textColor=NAVY,
                    alignment=TA_CENTER, leading=10)
    qr_img = _qr_image(cuit_emisor, req, result, size_mm=36)
    story.append(Table(
        [[qr_img, Table(
            [
                [_p(f"C.A.E. N°: {result.cae}", footer_b)],
                [_p(f"Fecha de Vto. del C.A.E.: {cae_vto}", footer_b)],
                [_p("Comprobante Autorizado", footer_s)],
                [_p("Pág. 1/1", footer_s)],
            ],
            colWidths=[usable_w - 32 * mm],
            style=TableStyle([
                ("LEFTPADDING",  (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING",   (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
                ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ]),
        )]],
        colWidths=[32 * mm, usable_w - 32 * mm],
        style=TableStyle([
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]),
    ))

    doc.build(story)

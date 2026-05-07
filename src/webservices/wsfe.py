"""
WSFEv1 - WebService de Facturacion Electronica
"""

from __future__ import annotations

from typing import Any, Dict, List
from logging import Logger
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

import requests

from src.auth.wsaa import TicketAccess
from src.const import CbteTipo, Concepto, DocTipo, AlicuotaIVAId, IVACondicion, FacturaResultado, Mode

SERVICE_ID = "wsfe"

# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

WSFE_URLS = {
    Mode.HOMOLOGACION: "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    Mode.PRODUCCION: "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
}

NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "ar": "http://ar.gov.afip.dif.FEV1/",
}


@dataclass
class PingResult:
    app_server: str
    db_server: str
    auth_server: str

@dataclass
class PtoVta:
    nro: int
    emision_tipo: CbteTipo
    bloqueado: str
    fch_baja: str

@dataclass
class SolicitudFactura:
    pto_vta: PtoVta
    cbte_tipo: CbteTipo
    cbte_nro: int
    receptor_cuit: str
    imp_neto: float
    iva: float
    receptor_iva_cond: IVACondicion
    concepto: Concepto = Concepto.SERVICIOS
    fecha: date = field(default_factory=date.today)
    serv_desde: date = field(default_factory=date.today)
    serv_hasta: date = field(default_factory=date.today)
    vto_pago: date = field(default_factory=date.today)


@dataclass
class CAEResultado:
    cbte_nro: int
    resultado: FacturaResultado
    cae: str
    cae_fch_vto: str
    observations: list[str]

@dataclass
class Comprobante:
    pto_vta: int
    cbte_tipo: CbteTipo
    cbte_nro: int

class Wsfe:
    def __init__(
        self,
        mode: Mode,
        ta: TicketAccess,
        log: Logger
    ) -> None:
        self.mode = mode
        self.ta = ta
        self.log = log


    def ping(self) -> PingResult:
        root = self._post_soap("FEDummy", "<ar:FEDummy/>")
        result = root.find(".//ar:FEDummyResult", NS)
        if result is None:
            raise RuntimeError("error respuesta FEDummy no incluye FEDummyResult")
        return PingResult(
            app_server=result.findtext("ar:AppServer", "", NS),
            db_server=result.findtext("ar:DbServer", "", NS),
            auth_server=result.findtext("ar:AuthServer", "", NS),
        )


    def get_ptos_venta(self, cuit: str) -> List[PtoVta]:
        assert self.mode == Mode.PRODUCCION, ""
        body = (
            "<ar:FEParamGetPtosVenta>"
            "<ar:Auth>"
            f"<ar:Token>{self.ta.token}</ar:Token>"
            f"<ar:Sign>{self.ta.sign}</ar:Sign>"
            f"<ar:Cuit>{cuit}</ar:Cuit>"
            "</ar:Auth>"
            "</ar:FEParamGetPtosVenta>"
        )
        root = self._post_soap("FEParamGetPtosVenta", body)
        result = root.find(".//ar:FEParamGetPtosVentaResult", NS)
        if result is None:
            raise RuntimeError(
                f"Missing FEParamGetPtosVentaResult in response:\n{ET.tostring(root, encoding='unicode')}"
            )

        error_codes = {err.findtext("ar:Code", "", NS) for err in result.findall("ar:Errors/ar:Err", NS)}
        real_errors = [
            f"{err.findtext('ar:Code', '', NS)}: {err.findtext('ar:Msg', '', NS)}"
            for err in result.findall("ar:Errors/ar:Err", NS)
            if err.findtext("ar:Code", "", NS) != "602"
        ]
        if real_errors:
            raise RuntimeError("error FEParamGetPtosVenta: " + "; ".join(real_errors))

        if "602" in error_codes:
            raise RuntimeError("no existen puntos de venta RECE registrado para este CUIT")

        return [
            PtoVta(
                nro = int(pv.findtext("ar:Nro", "", NS)),
                emision_tipo = CbteTipo(pv.findtext("ar:EmisionTipo", "", NS)), # TODO esto no estoy seguro que devuelve
                bloqueado = pv.findtext("ar:Bloqueado", "", NS),
                fch_baja = pv.findtext("ar:FchBaja", "", NS),
            )
            for pv in result.findall("ar:ResultGet/ar:PtoVenta", NS)
        ]

    def cae_solicitar(self, cuit: str, req: SolicitudFactura) -> CAEResultado:
        aliciva_id = AlicuotaIVAId.from_pct(req.iva)
        imp_iva = round(req.imp_neto * req.iva / 100, 2)
        imp_total = round(req.imp_neto + imp_iva, 2)
        fmt = "%Y%m%d"

        serv_fields = (
            f"<ar:FchServDesde>{req.serv_desde.strftime(fmt)}</ar:FchServDesde>"
            f"<ar:FchServHasta>{req.serv_hasta.strftime(fmt)}</ar:FchServHasta>"
            f"<ar:FchVtoPago>{req.vto_pago.strftime(fmt)}</ar:FchVtoPago>"
        ) if req.concepto in (Concepto.SERVICIOS, Concepto.PRODUCTOS_Y_SERVICIOS) else ""

        body = (
            "<ar:FECAESolicitar>"
            "<ar:Auth>"
            f"<ar:Token>{self.ta.token}</ar:Token>"
            f"<ar:Sign>{self.ta.sign}</ar:Sign>"
            f"<ar:Cuit>{cuit}</ar:Cuit>"
            "</ar:Auth>"
            "<ar:FeCAEReq>"
            "<ar:FeCabReq>"
            "<ar:CantReg>1</ar:CantReg>"
            f"<ar:PtoVta>{req.pto_vta.nro}</ar:PtoVta>"
            f"<ar:CbteTipo>{req.cbte_tipo}</ar:CbteTipo>"
            "</ar:FeCabReq>"
            "<ar:FeDetReq>"
            "<ar:FECAEDetRequest>"
            f"<ar:Concepto>{req.concepto}</ar:Concepto>"
            f"<ar:DocTipo>{DocTipo.CUIT}</ar:DocTipo>"
            f"<ar:DocNro>{req.receptor_cuit}</ar:DocNro>"
            f"<ar:CbteDesde>{req.cbte_nro}</ar:CbteDesde>"
            f"<ar:CbteHasta>{req.cbte_nro}</ar:CbteHasta>"
            f"<ar:CbteFch>{req.fecha.strftime(fmt)}</ar:CbteFch>"
            f"<ar:ImpTotal>{imp_total:.2f}</ar:ImpTotal>"
            "<ar:ImpTotConc>0.00</ar:ImpTotConc>"
            f"<ar:ImpNeto>{req.imp_neto:.2f}</ar:ImpNeto>"
            "<ar:ImpOpEx>0.00</ar:ImpOpEx>"
            f"<ar:ImpIVA>{imp_iva:.2f}</ar:ImpIVA>"
            "<ar:ImpTrib>0.00</ar:ImpTrib>"
            f"<ar:CondicionIVAReceptorId>{req.receptor_iva_cond}</ar:CondicionIVAReceptorId>"
            + serv_fields +
            "<ar:MonId>PES</ar:MonId>"
            "<ar:MonCotiz>1</ar:MonCotiz>"
            "<ar:Iva>"
            "<ar:AlicIva>"
            f"<ar:Id>{aliciva_id}</ar:Id>"
            f"<ar:BaseImp>{req.imp_neto:.2f}</ar:BaseImp>"
            f"<ar:Importe>{imp_iva:.2f}</ar:Importe>"
            "</ar:AlicIva>"
            "</ar:Iva>"
            "</ar:FECAEDetRequest>"
            "</ar:FeDetReq>"
            "</ar:FeCAEReq>"
            "</ar:FECAESolicitar>"
        )
        root = self._post_soap("FECAESolicitar", body)
        det = root.find(".//ar:FECAEDetResponse", NS)
        if det is None:
            fault = root.find(".//soap:Fault", NS)
            raise RuntimeError(f"error FECAESolicitar no devolvio detalles: {ET.tostring(fault, encoding='unicode') if fault is not None else 'unknown'}")

        errors = [
            f"{e.findtext('ar:Code', '', NS)}: {e.findtext('ar:Msg', '', NS)}"
            for e in root.findall(".//ar:Errors/ar:Err", NS)
        ]
        if errors:
            raise RuntimeError("error FECAESolicitar: " + "; ".join(errors))

        resultado = det.findtext("ar:Resultado", "", NS)
        observations = [
            f"{o.findtext('ar:Code', '', NS)}: {o.findtext('ar:Msg', '', NS)}"
            for o in det.findall("ar:Observaciones/ar:Obs", NS)
        ]
        if resultado == FacturaResultado.RECHAZADO:
            raise RuntimeError("factura rechazada: " + ("; ".join(observations) or "none"))

        return CAEResultado(
            cbte_nro=int(det.findtext("ar:CbteDesde", "0", NS)),
            resultado=FacturaResultado(resultado),
            cae=det.findtext("ar:CAE", "", NS),
            cae_fch_vto=det.findtext("ar:CAEFchVto", "", NS),
            observations=observations,
        )


    def ultimo_cbte_autorizado(self, cuit: str, pto_vta: PtoVta, cbte_tipo: CbteTipo) -> Comprobante:
        body = (
            "<ar:FECompUltimoAutorizado>"
            "<ar:Auth>"
            f"<ar:Token>{self.ta.token}</ar:Token>"
            f"<ar:Sign>{self.ta.sign}</ar:Sign>"
            f"<ar:Cuit>{cuit}</ar:Cuit>"
            "</ar:Auth>"
            f"<ar:PtoVta>{pto_vta.nro}</ar:PtoVta>"
            f"<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"
            "</ar:FECompUltimoAutorizado>"
        )
        root = self._post_soap("FECompUltimoAutorizado", body)
        result = root.find(".//ar:FECompUltimoAutorizadoResult", NS)
        if result is None:
            # Puede venir un soap:Fault en lugar del result
            fault = root.find(".//soap:Fault", NS)
            raise RuntimeError(f"error FECompUltimoAutorizado no devolvio resultado: {ET.tostring(fault, encoding='unicode') if fault is not None else 'unknown'}")

        errors = [
            f"{err.findtext('ar:Code', '', NS)}: {err.findtext('ar:Msg', '', NS)}"
            for err in result.findall("ar:Errors/ar:Err", NS)
        ]
        if errors:
            raise RuntimeError("error FECompUltimoAutorizado: " + "; ".join(errors))

        return Comprobante(
            pto_vta = int(result.findtext("ar:PtoVta", "", NS)),
            cbte_tipo = CbteTipo(int(result.findtext("ar:CbteTipo", "", NS))),
            cbte_nro = int(result.findtext("ar:CbteNro", "", NS)),
        )

    def _post_soap(self, action: str, body_xml: str) -> ET.Element:
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:ar="http://ar.gov.afip.dif.FEV1/">'
            "<soapenv:Header/>"
            f"<soapenv:Body>{body_xml}</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        response = requests.post(
            WSFE_URLS[self.mode],
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f"http://ar.gov.afip.dif.FEV1/{action}",
            },
            timeout=30,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        return root
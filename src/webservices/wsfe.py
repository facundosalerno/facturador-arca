"""
WSFEv1 - WebService de Facturacion Electronica
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import groupby
from logging import Logger
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree as ET

from src.auth.wsaa import TicketAccess
from src.client.session import AfipSession
from src.const import CbteTipo, EmisionTipo, Concepto, DocTipo, AlicuotaIVAId, IVACondicion, FacturaResultado, Mode

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
    emision_tipo: EmisionTipo
    bloqueado: str
    fch_baja: str

@dataclass
class SolicitudFactura:
    pto_vta: PtoVta
    cbte_tipo: CbteTipo
    cbte_nro: int
    receptor_cuit: str
    imp_neto: float
    imp_iva: float
    imp_total: float
    alicuota_iva_id: AlicuotaIVAId
    receptor_iva_cond: IVACondicion
    concepto: Concepto = Concepto.SERVICIOS
    fecha: date = field(default_factory=date.today)
    serv_desde: date = field(default_factory=date.today)
    serv_hasta: date = field(default_factory=date.today)
    vto_pago: date = field(default_factory=date.today)

@dataclass
class CAEBtachResultado:
    resultados: dict[int, CAEResultado]
    error: Exception | None

@dataclass
class CAEResultado:
    cbte_nro: int
    resultado: FacturaResultado
    cae: str
    cae_fch_vto: str
    observations: list[str]

@dataclass
class Comprobante:
    pto_vta_nro: int
    cbte_tipo: CbteTipo
    cbte_nro: int

@dataclass
class ComprobanteDetalle:
    pto_vta_nro: int
    cbte_tipo: CbteTipo
    cbte_nro: int
    cbte_fch: date
    doc_nro: str
    imp_total: float
    imp_neto: float
    imp_iva: float
    resultado: FacturaResultado
    cae: str
    cae_fch_vto: str
    serv_desde: date | None
    serv_hasta: date | None
    vto_pago: date | None

def _parse_date_opt(raw: str) -> date | None:
    if not raw or len(raw) != 8:
        return None
    return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))


class Wsfe:
    def __init__(
        self,
        mode: Mode,
        ta: TicketAccess,
        log: Logger,
        session: AfipSession,
        batch_size: int = 10,
    ) -> None:
        self.mode = mode
        self.ta = ta
        self.log = log
        self.session = session
        self.batch_size = batch_size


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


    def get_ptos_venta(self, cuit: str) -> list[PtoVta]:
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
                emision_tipo = EmisionTipo.from_raw(pv.findtext("ar:EmisionTipo", "", NS)),
                bloqueado = pv.findtext("ar:Bloqueado", "", NS),
                fch_baja = pv.findtext("ar:FchBaja", "", NS),
            )
            for pv in result.findall("ar:ResultGet/ar:PtoVenta", NS)
        ]

    def _build_det_request(self, req: SolicitudFactura) -> str:
        fmt = "%Y%m%d"
        serv_fields = (
            f"<ar:FchServDesde>{req.serv_desde.strftime(fmt)}</ar:FchServDesde>"
            f"<ar:FchServHasta>{req.serv_hasta.strftime(fmt)}</ar:FchServHasta>"
            f"<ar:FchVtoPago>{req.vto_pago.strftime(fmt)}</ar:FchVtoPago>"
        ) if req.concepto in (Concepto.SERVICIOS, Concepto.PRODUCTOS_Y_SERVICIOS) else ""

        return (
            "<ar:FECAEDetRequest>"
                f"<ar:Concepto>{req.concepto}</ar:Concepto>"
                f"<ar:DocTipo>{DocTipo.CUIT}</ar:DocTipo>"
                f"<ar:DocNro>{req.receptor_cuit}</ar:DocNro>"
                f"<ar:CbteDesde>{req.cbte_nro}</ar:CbteDesde>"
                f"<ar:CbteHasta>{req.cbte_nro}</ar:CbteHasta>"
                f"<ar:CbteFch>{req.fecha.strftime(fmt)}</ar:CbteFch>"
                f"<ar:ImpTotal>{req.imp_total:.2f}</ar:ImpTotal>"
                "<ar:ImpTotConc>0.00</ar:ImpTotConc>"
                f"<ar:ImpNeto>{req.imp_neto:.2f}</ar:ImpNeto>"
                "<ar:ImpOpEx>0.00</ar:ImpOpEx>"
                f"<ar:ImpIVA>{req.imp_iva:.2f}</ar:ImpIVA>"
                "<ar:ImpTrib>0.00</ar:ImpTrib>"
                f"<ar:CondicionIVAReceptorId>{req.receptor_iva_cond}</ar:CondicionIVAReceptorId>"
                + serv_fields +
                "<ar:MonId>PES</ar:MonId>"
                "<ar:MonCotiz>1</ar:MonCotiz>"
                "<ar:Iva>"
                    "<ar:AlicIva>"
                        f"<ar:Id>{req.alicuota_iva_id}</ar:Id>"
                        f"<ar:BaseImp>{req.imp_neto:.2f}</ar:BaseImp>"
                        f"<ar:Importe>{req.imp_iva:.2f}</ar:Importe>"
                    "</ar:AlicIva>"
                "</ar:Iva>"
            "</ar:FECAEDetRequest>"
        )

    def _cae_solicitar_batch(self, cuit: str, reqs: list[SolicitudFactura]) -> list[CAEResultado]:
        first = reqs[0]
        body = (
            "<ar:FECAESolicitar>"
                "<ar:Auth>"
                    f"<ar:Token>{self.ta.token}</ar:Token>"
                    f"<ar:Sign>{self.ta.sign}</ar:Sign>"
                    f"<ar:Cuit>{cuit}</ar:Cuit>"
                "</ar:Auth>"
                "<ar:FeCAEReq>"
                    "<ar:FeCabReq>"
                        f"<ar:CantReg>{len(reqs)}</ar:CantReg>"
                        f"<ar:PtoVta>{first.pto_vta.nro}</ar:PtoVta>"
                        f"<ar:CbteTipo>{first.cbte_tipo}</ar:CbteTipo>"
                    "</ar:FeCabReq>"
                    "<ar:FeDetReq>"
                    + "".join(self._build_det_request(r) for r in reqs) +
                    "</ar:FeDetReq>"
                "</ar:FeCAEReq>"
            "</ar:FECAESolicitar>"
        )
        root = self._post_soap("FECAESolicitar", body)

        errors = [
            f"{e.findtext('ar:Code', '', NS)}: {e.findtext('ar:Msg', '', NS)}"
            for e in root.findall(".//ar:Errors/ar:Err", NS)
        ]
        if errors:
            raise RuntimeError("error FECAESolicitar: " + "; ".join(errors))

        dets = root.findall(".//ar:FECAEDetResponse", NS)
        if not dets:
            fault = root.find(".//soap:Fault", NS)
            raise RuntimeError(f"error FECAESolicitar no devolvio detalles: {ET.tostring(fault, encoding='unicode') if fault is not None else 'unknown'}")

        results = []
        for det in dets:
            resultado = det.findtext("ar:Resultado", "", NS)
            observations = [
                f"{o.findtext('ar:Code', '', NS)}: {o.findtext('ar:Msg', '', NS)}"
                for o in det.findall("ar:Observaciones/ar:Obs", NS)
            ]
            if resultado == FacturaResultado.RECHAZADO:
                cbte = det.findtext("ar:CbteDesde", "?", NS)
                raise RuntimeError(f"factura {cbte} rechazada: " + ("; ".join(observations) or "none"))
            results.append(CAEResultado(
                cbte_nro=int(det.findtext("ar:CbteDesde", "0", NS)),
                resultado=FacturaResultado(resultado),
                cae=det.findtext("ar:CAE", "", NS),
                cae_fch_vto=det.findtext("ar:CAEFchVto", "", NS),
                observations=observations,
            ))
        return results

    def cae_solicitar(self, cuit: str, reqs: list[SolicitudFactura]) -> CAEBtachResultado:
        seen: set[tuple[int, int, int]] = set()
        duplicates: list[tuple[int, int, int]] = []
        for r in reqs:
            key = (r.pto_vta.nro, r.cbte_tipo, r.cbte_nro)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        if duplicates:
            return CAEBtachResultado(resultados={}, error=ValueError(f"cbte_nro duplicado en la solicitud: {duplicates}"))

        resultados: dict[int, CAEResultado] = {}
        key_fn = lambda r: (r.pto_vta.nro, r.cbte_tipo)
        # El header de cada batch requiere un único pto_vta y cbte_tipo, por lo que hay que
        # agrupar por esos campos antes de batchar. El sort previo es necesario porque
        # groupby solo agrupa elementos consecutivos con la misma clave.
        for _, group in groupby(sorted(reqs, key=key_fn), key=key_fn):
            group_list = list(group)
            for i in range(0, len(group_list), self.batch_size):
                chunk = group_list[i:i + self.batch_size]
                self.log.debug(f"Enviando batch de {len(chunk)} comprobante(s) (pto_vta={chunk[0].pto_vta.nro}, cbte_tipo={chunk[0].cbte_tipo})")
                try:
                    for resultado in self._cae_solicitar_batch(cuit, chunk):
                        resultados[resultado.cbte_nro] = resultado
                except Exception as e:
                    return CAEBtachResultado(resultados=resultados, error=e)
        return CAEBtachResultado(resultados=resultados, error=None)


    def consultar_cbte(self, cuit: str, pto_vta_nro: int, cbte_tipo: CbteTipo, cbte_nro: int) -> ComprobanteDetalle | None:
        """Consulta un comprobante específico. Devuelve None si no existe."""
        body = (
            "<ar:FECompConsultar>"
                "<ar:Auth>"
                    f"<ar:Token>{self.ta.token}</ar:Token>"
                    f"<ar:Sign>{self.ta.sign}</ar:Sign>"
                    f"<ar:Cuit>{cuit}</ar:Cuit>"
                "</ar:Auth>"
                "<ar:FeCompConsReq>"
                    f"<ar:CbteTipo>{cbte_tipo}</ar:CbteTipo>"
                    f"<ar:PtoVta>{pto_vta_nro}</ar:PtoVta>"
                    f"<ar:CbteNro>{cbte_nro}</ar:CbteNro>"
                "</ar:FeCompConsReq>"
            "</ar:FECompConsultar>"
        )
        root = self._post_soap("FECompConsultar", body)
        result = root.find(".//ar:FECompConsultarResult", NS)
        if result is None:
            raise RuntimeError(f"Missing FECompConsultarResult in response:\n{ET.tostring(root, encoding='unicode')}")

        errors = [
            f"{err.findtext('ar:Code', '', NS)}: {err.findtext('ar:Msg', '', NS)}"
            for err in result.findall("ar:Errors/ar:Err", NS)
        ]
        # Código 10016: el comprobante consultado no existe
        if any("10016" in e for e in errors):
            return None
        if errors:
            raise RuntimeError("error FECompConsultar: " + "; ".join(errors))

        det = result.find("ar:ResultGet", NS)
        if det is None:
            raise RuntimeError("FECompConsultar no devolvio ResultGet")

        cbte_fch = _parse_date_opt(det.findtext("ar:CbteFch", "", NS))
        assert cbte_fch, "FECompConsultar no devolvio CbteFch"

        return ComprobanteDetalle(
            pto_vta_nro = int(det.findtext("ar:PtoVta", "0", NS)),
            cbte_tipo = CbteTipo(int(det.findtext("ar:CbteTipo", "0", NS))),
            cbte_nro = int(det.findtext("ar:CbteDesde", "0", NS)),
            cbte_fch = cbte_fch,
            doc_nro = det.findtext("ar:DocNro", "", NS),
            imp_total = float(det.findtext("ar:ImpTotal", "0", NS)),
            imp_neto = float(det.findtext("ar:ImpNeto", "0", NS)),
            imp_iva = float(det.findtext("ar:ImpIVA", "0", NS)),
            resultado = FacturaResultado(det.findtext("ar:Resultado", "", NS)),
            cae = det.findtext("ar:CodAutorizacion", "", NS),
            cae_fch_vto = det.findtext("ar:FchVto", "", NS),
            serv_desde = _parse_date_opt(det.findtext("ar:FchServDesde", "", NS)),
            serv_hasta = _parse_date_opt(det.findtext("ar:FchServHasta", "", NS)),
            vto_pago = _parse_date_opt(det.findtext("ar:FchVtoPago", "", NS)),
        )

    def consultar_cbtes(self, cuit: str, pto_vta_nro: int, cbte_tipo: CbteTipo, cbte_nros: list[int], workers: int = 10) -> list[ComprobanteDetalle]:
        """Consulta múltiples comprobantes en paralelo. Omite los que no existen."""
        results: list[ComprobanteDetalle] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self.consultar_cbte, cuit, pto_vta_nro, cbte_tipo, nro): nro
                for nro in cbte_nros
            }
            for future in as_completed(futures):
                det = future.result()
                if det is not None:
                    results.append(det)
        return sorted(results, key=lambda c: c.cbte_nro, reverse=True)

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
            pto_vta_nro = int(result.findtext("ar:PtoVta", "", NS)),
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
        response = self.session.post(
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
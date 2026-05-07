"""
ws_sr_constancia_inscripcion — Consulta al padron razón social y domicilio fiscal por CUIT
"""

from __future__ import annotations

from datetime import date
from logging import Logger
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from src.client.session import AfipSession
from src.const import Mode
from src.auth.wsaa import TicketAccess
from src.webservices.wsfe import IVACondicion

SERVICE_ID = "ws_sr_constancia_inscripcion"

PADRON_URLS = {
    Mode.HOMOLOGACION: "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
    Mode.PRODUCCION: "https://aws.arca.gob.ar/sr-padron/webservices/personaServiceA5",
}


@dataclass
class PersonaInfo:
    cuit: str
    razon_social: str
    domicilio: str
    condicion_iva: IVACondicion
    fecha_inicio_actividades: date | None = None # Solo definida manualmente para la persona emisora
    ingresos_brutos: str | None = None # Solo definida manualmente para la persona emisora

class Padron:
    def __init__(
        self,
        mode: Mode,
        ta: TicketAccess,
        log: Logger,
        session: AfipSession,
    ) -> None:
        self.mode = mode
        self.ta = ta
        self.log = log
        self.session = session

    def get_persona(self, cuit_representada: str, cuit_consulta: str) -> PersonaInfo:
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:a5="http://a5.soap.ws.server.puc.sr/">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            "<a5:getPersona_v2>"
            f"<token>{self.ta.token}</token>"
            f"<sign>{self.ta.sign}</sign>"
            f"<cuitRepresentada>{cuit_representada}</cuitRepresentada>"
            f"<idPersona>{cuit_consulta}</idPersona>"
            "</a5:getPersona_v2>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )
        resp = self.session.post(
            PADRON_URLS[self.mode],
            data=envelope.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            timeout=30,
        )
        if resp.status_code >= 400:
            try:
                fault = next(
                    (el.text for el in ET.fromstring(resp.text).iter()
                    if el.tag.endswith("faultstring") and el.text),
                    None,
                )
            except ET.ParseError:
                fault = None
            raise RuntimeError(
                f"Padron HTTP {resp.status_code}: {fault or resp.text[:800]}"
            )
        root = ET.fromstring(resp.text)

        err = root.find(".//errorConstancia")
        if err is not None:
            desc = err.findtext("descripcion") or ET.tostring(err, encoding="unicode")
            raise RuntimeError(f"Padron error para CUIT {cuit_consulta}: {desc}")

        dg = root.find(".//datosGenerales")
        if dg is None:
            raise RuntimeError(
                f"Sin datosGenerales en padron para CUIT {cuit_consulta}:\n"
                f"{ET.tostring(root, encoding='unicode')}"
            )

        tipo = (dg.findtext("tipoPersona") or "").upper()
        apellido = dg.findtext("apellido") or ""
        nombre = dg.findtext("nombre") or ""
        razon_social = f"{nombre} {apellido}".strip() if tipo == "FISICA" else apellido

        persona_return = root.find(".//personaReturn") or root
        drg = persona_return.find("datosRegimenGeneral")
        dmt = persona_return.find("datosMonotributo")

        if dmt is not None:
            condicion_iva = IVACondicion.MONOTRIBUTISTA
        elif drg is not None:
            condicion_iva = IVACondicion.RESPONSABLE_INSCRIPTO
        else:
            raise Exception("no se puede determinar la condicion frente al IVA")

        return PersonaInfo(
            cuit=cuit_consulta,
            razon_social=razon_social,
            domicilio=self._fmt_domicilio(dg.find("domicilioFiscal")),
            condicion_iva=condicion_iva,
        )

    def _fmt_domicilio(self, el: ET.Element | None) -> str:
        if el is None:
            return ""
        parts = [
            el.findtext("direccion") or "",
            el.findtext("localidad") or "",
            el.findtext("descripcionProvincia") or "",
        ]
        cp = el.findtext("codPostal") or ""
        if cp:
            parts.append(f"CP {cp}")
        return ", ".join(p for p in parts if p)
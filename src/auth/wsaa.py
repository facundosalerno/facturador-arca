"""
WSAA - ARCA/AFIP WebService de Autenticacion y Autorizacion.
"""

from __future__ import annotations

import base64
import random
from logging import Logger
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509 import load_pem_x509_certificate

from src.client.session import AfipSession
from src.const import Mode

WSAA_URLS = {
    Mode.HOMOLOGACION: "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    Mode.PRODUCCION: "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}


@dataclass
class TicketAccess:
    token: str
    sign: str
    expiration: datetime

    def is_valid(self) -> bool:
        return datetime.now(timezone.utc) < self.expiration - timedelta(minutes=10)


@dataclass
class CertificateInfo:
    subject: str
    issuer: str
    serial_number: str
    not_valid_before: datetime
    not_valid_after: datetime


class Wsaa:
    def __init__(
        self,
        mode: Mode,
        cert_path: Path,
        private_key_path: Path,
        cache_path: Path,
        log: Logger,
        session: AfipSession,
    ) -> None:
        self.mode = mode
        self.cert_path = cert_path
        self.private_key_path = private_key_path
        self.cache_path = cache_path
        self.log = log
        self.session = session


    def get_certificate_info(self) -> CertificateInfo:
        cert = load_pem_x509_certificate(self.cert_path.read_bytes())
        return CertificateInfo(
            subject=cert.subject.rfc4514_string(),
            issuer=cert.issuer.rfc4514_string(),
            serial_number=str(cert.serial_number),
            not_valid_before=cert.not_valid_before_utc,
            not_valid_after=cert.not_valid_after_utc,
        )

    def get_ticket_access(self, service: str) -> TicketAccess:
        
        ta_path = self.cache_path / f"ta_{service}_{self.mode.value}.xml"
        cached = self._load_cached_ta(ta_path)
        if cached:
            return cached

        payload = self._build_login_ticket_request(service)
        cms_b64 = self._sign_cms(payload)
        envelope = self._login_cms_soap_envelope(cms_b64)

        response = self.session.post(
            WSAA_URLS[self.mode],
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": "",
            },
            timeout=30,
        )

        if response.status_code >= 400:
            # WSAA devuelve sus errores como SOAP Fault con HTTP 500. El detalle suele
            # estar en faultstring (p.ej. "computador fuera de tiempo", "El CEE ya posee
            # un TA valido", "Certificado no emitido por AC", "CN no autorizado").
            fault_msg = self._extract_fault(response.text)
            raise RuntimeError(f"error WSAA loginCms HTTP {response.status_code}: {fault_msg or response.text}")

        if ta_path:
            # Guardamos la respuesta cruda; al releerla parseamos y validamos vigencia.
            ta_path.parent.mkdir(parents=True, exist_ok=True)
            ta_path.write_text(response.text, encoding="utf-8")

        return self._parse_login_response(response.text)


    def _build_login_ticket_request(self, service: str) -> bytes:
        now = datetime.now(timezone.utc)
        # xs:dateTime requiere offset con dos puntos (-03:00), que es lo que produce
        # isoformat(); strftime("%z") devuelve "+0000" y el schema lo rechaza.
        unique_id = str(random.randint(0, 2**31 - 1))
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<loginTicketRequest version="1.0">'
            "<header>"
            f"<uniqueId>{unique_id}</uniqueId>"
            f"<generationTime>{(now - timedelta(minutes=10)).isoformat(timespec='seconds')}</generationTime>"
            f"<expirationTime>{(now + timedelta(minutes=10)).isoformat(timespec='seconds')}</expirationTime>"
            "</header>"
            f"<service>{service}</service>"
            "</loginTicketRequest>"
        )
        return xml.encode("utf-8")


    def _sign_cms(self, payload: bytes) -> str:
        private_key = serialization.load_pem_private_key(self.private_key_path.read_bytes(), password=None)
        certificate = load_pem_x509_certificate(self.cert_path.read_bytes())

        cms = (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(payload)
            .add_signer(certificate, private_key, hashes.SHA256())  # pyright: ignore[reportArgumentType]
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
        )
        return base64.b64encode(cms).decode("ascii")


    def _login_cms_soap_envelope(self, cms_b64: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:wsaa="http://wsaa.view.sua.dvadac.desein.afip.gov">'
            "<soapenv:Header/>"
            "<soapenv:Body>"
            "<wsaa:loginCms>"
            f"<wsaa:in0>{cms_b64}</wsaa:in0>"
            "</wsaa:loginCms>"
            "</soapenv:Body>"
            "</soapenv:Envelope>"
        )


    def _parse_login_response(self, soap_response: str) -> TicketAccess:
        root = ET.fromstring(soap_response)
        # El loginCmsReturn viene como CDATA con el XML del LoginTicketResponse anidado.
        return_node = next(
            (el for el in root.iter() if el.tag.endswith("loginCmsReturn")), None
        )
        if return_node is None or not return_node.text:
            raise RuntimeError(f"respuesta WSAA inesperada:\n{soap_response}")

        inner = ET.fromstring(return_node.text)
        token = inner.findtext(".//token")
        sign = inner.findtext(".//sign")
        expiration_str = inner.findtext(".//expirationTime")
        if not token or not sign or not expiration_str:
            raise RuntimeError(f"respuesta incompleta LoginTicketResponse:\n{return_node.text}")

        expiration = datetime.fromisoformat(expiration_str)
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        return TicketAccess(token=token, sign=sign, expiration=expiration)


    def _load_cached_ta(self, ta_path: Path) -> TicketAccess | None:
        if not ta_path.exists():
            return None
        try:
            ta = self._parse_login_response(ta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return ta if ta.is_valid() else None


    def _extract_fault(self, soap_response: str) -> str | None:
        try:
            root = ET.fromstring(soap_response)
        except ET.ParseError:
            return None
        fault_string = next(
            (el.text for el in root.iter() if el.tag.endswith("faultstring") and el.text),
            None,
        )
        return fault_string

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.const import Mode
from src.utils import ensure_input, ask, print_obj
from src.log import create_logger
from src.auth.wsaa import Wsaa
from src.pdf import generate_invoice_pdf, ItemFactura, UnidadMedida
from src.const import CbteTipo, IVACondicion
from src.webservices.padron import SERVICE_ID as PADRON_SERVICE_ID, Padron, PersonaInfo
from src.webservices.wsfe import SERVICE_ID as WSFE_SERVICE_ID, Wsfe, SolicitudFactura, PtoVta


def main():
    load_dotenv()
    log = create_logger(level="DEBUG")
    mode = Mode(os.environ["ARCA_ENV"])
    cuit = os.environ["ARCA_CUIT"]

    emisor = PersonaInfo(
        cuit = cuit,
        razon_social = "TU_RAZON_SOCIAL",
        domicilio = "TU_DOMICILIO_FISCAL",
        condicion_iva = IVACondicion.RESPONSABLE_INSCRIPTO,
        fecha_inicio_actividades = date(2022, 7, 20),
        ingresos_brutos = cuit
    )

    # TODO esto deberia manejarse de otra forma
    receptor_cuit= os.environ["ARCA_RECEPTOR_CUIT"]
    imp_neto = float(os.environ.get("ARCA_IMP_NETO", "100.00"))
    iva = float(os.environ.get("ARCA_IVA", "21.0"))

    log.info(f"== Ambiente: {mode} ==")

    log.info("Iniciando intercambio de claves con WSAA...")
    auth = Wsaa(
        mode = mode,
        cert_path = Path(os.environ["ARCA_CERTIFICATE"]).expanduser(),
        private_key_path=Path(os.environ["ARCA_PRIVATE_KEY"]).expanduser(),
        log = log
    )

    cert = auth.get_certificate_info()

    log.debug("Leyendo certificado")
    log.debug(f"   Signed by:  {cert.subject}")
    log.debug(f"   Issuer:     {cert.issuer}")
    log.debug(f"   Cert valid: {cert.not_valid_before.date()} -> {cert.not_valid_after.date()}")

    ta_wsfe = auth.get_ticket_access(service=WSFE_SERVICE_ID, ta_path=Path(os.environ["ARCA_WSFE_TA"]))

    log.debug(f"   TA expires: {ta_wsfe.expiration.isoformat()}\n")

    log.debug("Obteniendo puntos de venta...")

    fe = Wsfe(mode=mode, ta=ta_wsfe, log=log)

    cbte_tipo = CbteTipo.from_str(os.environ["ARCA_CBTE_TIPO"])

    if mode == Mode.HOMOLOGACION:
        nro = ensure_input(prompt="En homologacion debe seleccionar el punto de venta manualmente: ",  type=int, parser=int)
        pto_vta = PtoVta(nro=nro, emision_tipo=cbte_tipo, bloqueado="", fch_baja="") # TODO ver que datos mockear
    else:
        ptos = fe.get_ptos_venta(emisor.cuit)
        for pv in ptos:
            log.info(pv)
        nro = ensure_input(prompt="Seleccione un punto de venta: ", type=int, parser=int)
        pto_vta = next(pv for pv in ptos if pv.nro == nro)
        assert pto_vta, "el punto de venta seleccionado no existe"

    log.debug(f"Consultando el ultimo punto de venta")
    ultimo_cbt = fe.ultimo_cbte_autorizado(emisor.cuit, pto_vta=pto_vta, cbte_tipo=cbte_tipo)
    proximo_cbt_nro = ultimo_cbt.cbte_nro + 1

    ta_padron = auth.get_ticket_access(service=PADRON_SERVICE_ID, ta_path=Path(os.environ["ARCA_PADRON_TA"]))
    p = Padron(mode=mode, ta=ta_padron, log=log)

    if mode == Mode.HOMOLOGACION:
        log.info("En homologacion se seleccionara un CUIT ficticio automaticamente")
        receptor = p.get_persona(emisor.cuit, "27015942210")
    else:
        receptor = p.get_persona(emisor.cuit, receptor_cuit)

    today = date.today()
    
    req = SolicitudFactura(
        pto_vta = pto_vta,
        cbte_tipo = cbte_tipo,
        cbte_nro = proximo_cbt_nro,
        receptor_cuit = receptor_cuit,
        imp_neto = imp_neto,
        iva = iva,
        receptor_iva_cond = receptor.condicion_iva,
        fecha = today,
        # TODO las siguientes fields hay que trabajarlas un poco mas
        serv_desde = today.replace(day=1),
        serv_hasta = today,
        vto_pago = today,
    )
    log.info(f"{print_obj(req)}\n")
    log.info(f"Emisor: {print_obj(emisor)}\n")
    log.info(f"Receptor: {print_obj(receptor)}\n")
    if not ask(prompt="Continuar [y/N]: ", default=False):
        log.info("Abortando")
        return

    cae_result = fe.cae_solicitar(emisor.cuit, req)

    log.info(f"Resultado CAE: {print_obj(cae_result)}")

    pdf_path = Path(os.environ["ARCA_FACTURA_PATH"].format(cbte_nro=cae_result.cbte_nro))
    logo_path = Path(os.environ["ARCA_FACTURA_LOGO"])
    generate_invoice_pdf(
        cuit_emisor = emisor.cuit,
        req = req,
        result = cae_result,
        output_path = pdf_path,
        emisor = emisor,
        receptor = receptor,
        logo_path = logo_path,
        items = [ItemFactura(
            descripcion="50 Licencias",
            cantidad=1.0,
            precio_unitario=req.imp_neto,
            unidad_medida=UnidadMedida.UNIDADES,
            codigo = "1",
        )]
    )
    log.info(f"Factura electronica generada exitosamente en {pdf_path}")


if __name__ == "__main__":
    main()
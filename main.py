from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path
from dotenv import load_dotenv
from logging import Logger

from src.client.session import AfipSession
from src.const import Mode
from src.log import create_logger
from src.auth.wsaa import Wsaa
from src.pdf import generate_invoice_pdf, ItemFactura, UnidadMedida
from src.const import CbteTipo, IVACondicion, EmisionTipo
from src.webservices.padron import SERVICE_ID as PADRON_SERVICE_ID, Padron, PersonaInfo
from src.webservices.wsfe import SERVICE_ID as WSFE_SERVICE_ID, Wsfe, SolicitudFactura, PtoVta
from src.input.excel import read_clientes
from src.ui import FacturadorApp



def ver_clientes(app: FacturadorApp, log: Logger) -> None:
    path = os.environ["EXCEL_CLIENTES"]
    clientes = read_clientes(Path(path).expanduser())
    log.info(f"{len(clientes)} clientes leidos del Excel {path}")

    back_to_menu = threading.Event()

    def on_open():
        app.toggle_log(show=False)

    def on_close():
        app.toggle_log(show=True)
        if back_to_menu.is_set():
            app.toggle_confirm_bar(show=True)

    def on_link():
        app.toggle_log(show=False)
        if back_to_menu.is_set():
            app.toggle_confirm_bar(show=False)

    app.show_table_sync(
        label="Tabla de clientes",
        columns=["CUIT", "Empresa", "Colaboradores", "Imp. Neto", "Bonif. %", "IVA %", "Cond. IVA", "Tipo Cbte", "Últ. Ajuste"],
        rows=[
            (
                c.cuit,
                f"▸ {c.descripcion}" if len(c.items) > 1 else c.descripcion,
                str(c.colaboradores),
                f"${c.imp_neto:,.2f}",
                f"{c.bonificacion_pct:.1f}%",
                f"{c.iva:.1f}%",
                c.iva_cond.name.replace("_", " "),
                c.cbte_tipo.name.replace("FACTURA_", ""),
                str(c.last_adjustment),
            )
            for c in clientes
        ],
        sub_columns=["Detalle", "Colaboradores", "Imp. Neto", "Bonif. %"],
        sub_rows=[
            [
                (
                    item.detalle or "-",
                    str(item.colaboradores),
                    f"${item.imp_neto:,.2f}",
                    f"{item.bonificacion_pct:.1f}%",
                )
                for item in c.items
            ] if len(c.items) > 1 else []
            for c in clientes
        ],
        on_open=on_open,
        on_close=on_close,
        on_link=on_link
    )
    back_to_menu.set()
    app.ask_sync("", yes_label="Volver al menu", no_label=None)
    app.call_from_thread(app.toggle_table, show=False)


def ver_facturas(app: FacturadorApp, log: Logger) -> None:
    log.info("Ver facturas: no implementado")
    app.ask_sync("", yes_label="Volver al menu", no_label=None)


def generar_facturas(app: FacturadorApp, log) -> None:
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

    log.info(f"Ambiente: {mode}")

    session = AfipSession()

    log.info("Iniciando intercambio de claves con WSAA")
    auth = Wsaa(
        mode = mode,
        cert_path = Path(os.environ["ARCA_CERTIFICATE"]).expanduser(),
        private_key_path = Path(os.environ["ARCA_PRIVATE_KEY"]).expanduser(),
        cache_path = Path(os.environ["ARCA_CACHE"]).expanduser(),
        log = log,
        session = session,
    )

    cert = auth.get_certificate_info()

    mode_color = "yellow" if mode == Mode.HOMOLOGACION else "bold red"
    proceed = app.ask_sync((
        f"[bold]Ambiente:[/bold] [{mode_color}]{mode.value.upper()}[/]\n\n"
        f"[bold]Certificado:[/bold]\n"
        f"  Subject:  {cert.subject}\n"
        f"  Issuer:   {cert.issuer}\n"
        f"  Vigencia: {cert.not_valid_before.date()} \u2192 {cert.not_valid_after.date()}"
    ))
    if not proceed:
        app.exit()
        return

    log.debug("Solicitando ticket de acceso para WSFE")
    ta_wsfe = auth.get_ticket_access(service=WSFE_SERVICE_ID)
    log.debug(f"Ticket de acceso WSFE obtenido (expira: {ta_wsfe.expiration.isoformat()})")

    fe = Wsfe(mode=mode, ta=ta_wsfe, log=log, session=session)

    cbte_tipo = CbteTipo.from_str(os.environ["ARCA_CBTE_TIPO"])

    if mode == Mode.HOMOLOGACION:
        nro = app.input_sync(prompt="En homologacion debe seleccionar el punto de venta manualmente: ", type=int, parser=int)
        pto_vta = PtoVta(nro=nro, emision_tipo=EmisionTipo.CAE, bloqueado="N", fch_baja="NULL")
    else:
        log.info("Consultando puntos de venta habilitados")
        ptos = fe.get_ptos_venta(emisor.cuit)
        log.info(f"Puntos de venta obtenidos: {[pv.nro for pv in ptos]}")
        nro = app.input_sync(prompt="Seleccione un punto de venta: ", type=int, parser=int)
        pto_vta = next((pv for pv in ptos if pv.nro == nro), None)
        assert pto_vta, "el punto de venta seleccionado no existe"

    log.debug(f"Consultando el ultimo comprobante autorizado (pto_vta={pto_vta.nro}, cbte_tipo={cbte_tipo})")
    ultimo_cbt = fe.ultimo_cbte_autorizado(emisor.cuit, pto_vta=pto_vta, cbte_tipo=cbte_tipo)
    proximo_cbt_nro = ultimo_cbt.cbte_nro + 1
    log.debug(f"Ultimo comprobante: {ultimo_cbt.cbte_nro} -> proximo: {proximo_cbt_nro}")

    
    log.debug("Leyendo excel de clientes")
    clientes = read_clientes(Path(os.environ["EXCEL_CLIENTES"]).expanduser())
    log.info(f"{len(clientes)} clientes leídos del Excel")
    app.show_table_sync(
        label="Tabla de clientes",
        columns=["CUIT", "Empresa", "Colaboradores", "Imp. Neto", "Bonif. %", "IVA %", "Cond. IVA", "Tipo Cbte", "Últ. Ajuste"],
        rows=[
            (
                c.cuit,
                f"▸ {c.descripcion}" if len(c.items) > 1 else c.descripcion,
                str(c.colaboradores),
                f"${c.imp_neto:,.2f}",
                f"{c.bonificacion_pct:.1f}%",
                f"{c.iva:.1f}%",
                c.iva_cond.name,
                c.cbte_tipo.name,
                str(c.last_adjustment),
            )
            for c in clientes
        ],
        sub_rows=[
            [
                (
                    item.detalle or "-",
                    str(item.colaboradores),
                    f"${item.imp_neto:,.2f}",
                    f"{item.bonificacion_pct:.1f}%",
                )
                for item in c.items
            ] if len(c.items) > 1 else []
            for c in clientes
        ],
        sub_columns=["Detalle", "Colaboradores", "Imp. Neto", "Bonif. %"],
    )
    if not app.ask_sync("¿Continuar con la facturación?"):
        return
    return

    log.debug("Solicitando ticket de acceso para Padron")
    ta_padron = auth.get_ticket_access(service=PADRON_SERVICE_ID)
    log.debug(f"Ticket de acceso Padron obtenido (expira: {ta_padron.expiration.isoformat()})")

    p = Padron(mode=mode, ta=ta_padron, log=log, session=session)

    if mode == Mode.HOMOLOGACION:
        log.info("En homologacion se usara un CUIT ficticio automaticamente")
        receptor = p.get_persona(emisor.cuit, "27015942210")
    else:
        log.info(f"Consultando datos del receptor (CUIT: {receptor_cuit})")
        receptor = p.get_persona(emisor.cuit, receptor_cuit)
        log.info(f"Receptor obtenido: {receptor.razon_social}")

    receptor.condicion_iva = IVACondicion.CONSUMIDOR_FINAL

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
    log.info(
        f"Solicitud:"
        f"\n  cbte_tipo:        {req.cbte_tipo.name}"
        f"\n  cbte_nro:         {req.cbte_nro}"
        f"\n  pto_vta:          {req.pto_vta.nro} ({req.pto_vta.emision_tipo.name})"
        f"\n  receptor_cuit:    {req.receptor_cuit}"
        f"\n  receptor_iva:     {req.receptor_iva_cond.name}"
        f"\n  concepto:         {req.concepto.name}"
        f"\n  imp_neto:         ${req.imp_neto:.2f}"
        f"\n  iva:              {req.iva}%"
        f"\n  fecha:            {req.fecha}"
        f"\n  serv_desde:       {req.serv_desde}"
        f"\n  serv_hasta:       {req.serv_hasta}"
        f"\n  vto_pago:         {req.vto_pago}"
        "\n"
    )
    log.info(
        f"Emisor:"
        f"\n  cuit:                    {emisor.cuit}"
        f"\n  razon_social:            {emisor.razon_social}"
        f"\n  domicilio:               {emisor.domicilio}"
        f"\n  condicion_iva:           {emisor.condicion_iva.name}"
        f"\n  fecha_inicio_actividades:{emisor.fecha_inicio_actividades}"
        f"\n  ingresos_brutos:         {emisor.ingresos_brutos}"
        "\n"
    )
    log.info(
        f"Receptor:"
        f"\n  cuit:          {receptor.cuit}"
        f"\n  razon_social:  {receptor.razon_social}"
        f"\n  domicilio:     {receptor.domicilio}"
        f"\n  condicion_iva: {receptor.condicion_iva.name}"
        "\n"
    )
    if not app.ask_sync(prompt="Continuar [y/N]: ", default=False):
        log.info("Abortando")
        return

    log.info("Solicitando CAE a WSFE")
    cae_result = fe.cae_solicitar(emisor.cuit, [req])[req.cbte_nro]
    log.info(
        f"CAE obtenido exitosamente:"
        f"\n  cbte_nro:     {cae_result.cbte_nro}"
        f"\n  resultado:    {cae_result.resultado}"
        f"\n  cae:          {cae_result.cae}"
        f"\n  cae_fch_vto:  {cae_result.cae_fch_vto}"
        f"\n  observations: {cae_result.observations}"
        "\n"
    )

    pdf_path = Path(os.environ["ARCA_FACTURA_PATH"].format(cbte_nro=cae_result.cbte_nro))
    log.info(f"Generando PDF de la factura en {pdf_path}")
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





def main(app: FacturadorApp) -> None:
    load_dotenv()
    log = create_logger(level="DEBUG", handler=app.get_log_handler())

    while True:
        selection = app.wait_for_menu_sync()

        if selection == "menu-clientes":
            ver_clientes(app, log)
        elif selection == "menu-facturas":
            ver_facturas(app, log)
        elif selection == "menu-generar":
            generar_facturas(app, log)
        elif selection == "quit":
            app.exit()
            return


if __name__ == "__main__":
    app = FacturadorApp()
    app.set_worker(lambda: main(app))
    app.run()
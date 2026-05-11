from __future__ import annotations

import calendar
import os
from datetime import date, datetime, timedelta
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
from src.utils import format_exception



def ver_clientes(app: FacturadorApp, log: Logger) -> None:
    log.debug("Leyendo excel de clientes")
    path = os.environ["EXCEL_CLIENTES"]
    clientes = read_clientes(Path(path).expanduser())
    log.info(f"{len(clientes)} clientes leidos del Excel {path}")

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
                    f"{c.bonificacion_pct:.1f}%",
                )
                for item in c.items
            ] if len(c.items) > 1 else []
            for c in clientes
        ],
    )
    app.ask_sync("", yes_label="Volver al menu", no_label=None)


def validar_pdf(app: FacturadorApp, log: Logger) -> None:
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.pdf_utils.reader import PdfFileReader

    def parse_path(s: str) -> Path:
        p = Path(s).expanduser()
        if not p.exists():
            raise FileNotFoundError(p)
        return p

    path = app.input_sync(prompt="Ruta del PDF a validar: ", type=Path, parser=parse_path)
    log.info(f"Validando: {path}")

    try:
        with open(path, "rb") as f:
            reader = PdfFileReader(f)
            sigs = reader.embedded_signatures
            if not sigs:
                log.warning("El PDF no contiene firmas digitales")
            else:
                for i, sig in enumerate(sigs, 1):
                    status = validate_pdf_signature(sig)
                    log.info(f"Firma #{i} — campo: {sig.field_name}")
                    log.info(f"Sin modificaciones: {'OK' if status.intact else 'FALLO'}")
                    log.info(f"Firma válida:       {'OK' if status.valid else 'FALLO'}")
                    if status.signing_cert is not None:
                        log.info(f"  Firmante: {status.signing_cert.subject.human_friendly}")
    except Exception as e:
        log.error(f"Error al validar: {e}")

    app.ask_sync("", yes_label="Volver al menu", no_label=None)


def ver_facturas(app: FacturadorApp, log: Logger) -> None:
    log.info("Ver facturas: no implementado")
    app.ask_sync("", yes_label="Volver al menu", no_label=None)


def generar_facturas(app: FacturadorApp, log: Logger) -> None:
    mode = Mode(os.environ["ARCA_ENV"])
    #cuit = os.environ["ARCA_CUIT"]

    emisor = PersonaInfo(
        cuit = os.environ["ARCA_CUIT"],
        razon_social = os.environ["ARCA_RAZON_SOCIAL"],
        domicilio = os.environ["ARCA_RAZON_DOMICILIO"],
        condicion_iva = IVACondicion.from_str(os.environ["ARCA_CONDICION_IVA"]),
        fecha_inicio_actividades = datetime.strptime(os.environ["ARCA_FECHA_INICIO_ACTIVIDADES"], "%d/%m/%Y").date(),
        ingresos_brutos = os.environ["ARCA_CUIT"]
    )

    # TODO esto deberia manejarse de otra forma
    #receptor_cuit= os.environ["ARCA_RECEPTOR_CUIT"]
    #imp_neto = float(os.environ.get("ARCA_IMP_NETO", "100.00"))
    #iva = float(os.environ.get("ARCA_IVA", "21.0"))
    #cbte_tipo = CbteTipo.from_str(os.environ["ARCA_CBTE_TIPO"])

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

    assert emisor.fecha_inicio_actividades

    mode_color = "yellow" if mode == Mode.HOMOLOGACION else "bold red"
    proceed = app.ask_sync((
        f"[bold]Ambiente:[/bold] [{mode_color}]{mode.value.upper()}[/]\n\n"
        f"[bold]Representando a:[/bold]\n"
        f"{emisor.razon_social}\n"
        f"{emisor.cuit}\n"
        f"{emisor.condicion_iva.name.replace('_', ' ')}\n"
        f"{emisor.domicilio}\n"
        f"Ingresos Brutos: {emisor.ingresos_brutos}\n"
        f"Fecha de Inicio de Actividades: {emisor.fecha_inicio_actividades.strftime('%d %B %Y')}\n\n"
        f"[bold]Certificado:[/bold]\n"
        f"Subject:  {cert.subject}\n"
        f"Issuer:   {cert.issuer}\n"
        f"Vigencia: {cert.not_valid_before.date()} \u2192 {cert.not_valid_after.date()}"
    ))
    if not proceed:
        app.exit()
        return

    log.debug("Solicitando ticket de acceso para WSFE")
    ta_wsfe = auth.get_ticket_access(service=WSFE_SERVICE_ID)
    log.debug(f"Ticket de acceso WSFE obtenido (expira: {ta_wsfe.expiration.isoformat()})")

    fe = Wsfe(mode=mode, ta=ta_wsfe, log=log, session=session)

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

    log.debug("Leyendo excel de clientes")
    path = os.environ["EXCEL_CLIENTES"]
    clientes = read_clientes(Path(os.environ["EXCEL_CLIENTES"]).expanduser())
    log.info(f"{len(clientes)} clientes leidos del Excel {path}")

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
                    f"{c.bonificacion_pct:.1f}%",
                )
                for item in c.items
            ] if len(c.items) > 1 else []
            for c in clientes
        ],
    )

    # Necesitamos agrupar por tipo de comprobante a realizar (factura A la mayoria pero pueden haber facturas B). 
    # Cada numero de comprobante esta asociado a un punto de venta y un tipo de factura, por lo que podrian haberse
    # hecho 900 comprobantes de tipo A en el punto de venta 2 pero solo 4 comprobantes de tipo B en el mismo punto de venta
    cbtes: set[CbteTipo] = set()
    cuits_receptores: set[str] = set()
    for cliente in clientes:
        cbtes.add(cliente.cbte_tipo)
        assert not cliente.cuit in cuits_receptores, f"cuit repetido: {cliente}"
        cuits_receptores.add(cliente.cuit)

    proximo_cbt_nros: dict[CbteTipo, int] = {}
    for cbte in cbtes:
        log.debug(f"Consultando el ultimo comprobante autorizado (pto_vta={pto_vta.nro}, cbte_tipo={cbte})")
        ultimo_cbt = fe.ultimo_cbte_autorizado(emisor.cuit, pto_vta=pto_vta, cbte_tipo=cbte)
        proximo_cbt_nro = ultimo_cbt.cbte_nro + 1
        proximo_cbt_nros[cbte] = proximo_cbt_nro
        log.debug(f"Ultimo comprobante: {ultimo_cbt.cbte_nro} -> proximo: {proximo_cbt_nro}")

    log.debug("Solicitando ticket de acceso para Padron")
    ta_padron = auth.get_ticket_access(service=PADRON_SERVICE_ID)
    log.debug(f"Ticket de acceso Padron obtenido (expira: {ta_padron.expiration.isoformat()})")

    p = Padron(mode=mode, ta=ta_padron, log=log, session=session)

    if mode == Mode.HOMOLOGACION:
        log.info("En homologacion se usara un CUIT ficticio automaticamente")
        # Mockeamos los datos para tener un aproximado de como se veria la factura
        random_persona = p.get_persona(emisor.cuit, "27015942210")
        receptores = {c.cuit: PersonaInfo(
            cuit = c.cuit,
            razon_social = c.descripcion,
            domicilio = random_persona.domicilio,
            condicion_iva = c.iva_cond,
        ) for c in clientes}
        log.info(f"Se generaron {len(receptores)} receptores ficticios")
    else:
        log.info(f"Consultando datos de {len(cuits_receptores)} receptores")
        receptores = p.get_personas(emisor.cuit, list(cuits_receptores))
        log.info(f"Receptores obtenidos {len(receptores)}")

    today = date.today()
    solicitudes: dict[str, SolicitudFactura] = {}

    # Se acomodan las condiciones ante el iva. Por ejemplo, a un monotributista le va a salir que la 
    # condicion ante el iva es "MONOTRIBUTISTA" pero si le hacemos factura B tenemos que usar "CONSUMIDOR_FINAL".
    # Ademas se hacen chequeos de consistencia antes de presentar la tabla definitiva de clientes a facturar
    for cliente in clientes:
        assert cliente.cuit in receptores, f"error de consistencia: {cliente}"
        receptor = receptores[cliente.cuit]

        # Rompemos en estos casos por que si (hasta no tener un cliente con estas condiciones no vamos a manejar esto ya que no esta probado)
        assert cliente.iva_cond not in [IVACondicion.CONSUMIDOR_FINAL, IVACondicion.MONOTRIBUTISTA], f"cliente no soportado: {cliente}"
        assert receptor.condicion_iva not in [IVACondicion.CONSUMIDOR_FINAL, IVACondicion.MONOTRIBUTISTA], f"receptor no soportado: {receptor}"

        if cliente.iva_cond == IVACondicion.RESPONSABLE_INSCRIPTO:
            assert cliente.iva == 21.0, f"error de consistencia: {cliente}"
            assert cliente.cbte_tipo == CbteTipo.FACTURA_A, f"error de consistencia: {cliente}"
        if cliente.iva_cond == IVACondicion.EXENTO:
            # No se si esto debe ser asi para todos los casos pero tenemos un caso solo que si
            assert cliente.iva == 0.0, f"error de consistencia: {cliente}"
            assert cliente.cbte_tipo == CbteTipo.FACTURA_B, f"error de consistencia: {cliente}"


        if cliente.iva_cond != receptor.condicion_iva:
            # Esto no se si esta bien pero en algunos casos probablemente si. Por ejemplo si tuvieramos un monotributista le podemos hacer factura como
            # consumidor final y en ese caso, el pdaron nos diria condicion ante el iva 'MONOTRIBUTISTA' pero utilizariamos lo que tenemos anotado en el excel
            # que es hacer una factura como 'CONSUMIDOR FINAL'
            log.warning(f"El cliente {cliente.cuit} {receptor.razon_social} tiene condicion ante el IVA {receptor.condicion_iva} pero fue marcado en el excel como {cliente.iva_cond} (se utilizara esta ultima)")
            receptor.condicion_iva = cliente.iva_cond

        assert cliente.imp_neto == sum(map(lambda i: i.imp_neto, cliente.items)), f"error de consistencia: {cliente}"
        assert cliente.colaboradores == sum(map(lambda i: i.colaboradores, cliente.items)), f"error de consistencia: {cliente}"
        assert not cliente.cuit in solicitudes, f"error de consistencia: {cliente}"
        solicitudes[cliente.cuit] = SolicitudFactura(
            pto_vta = pto_vta,
            cbte_tipo = cliente.cbte_tipo,
            cbte_nro = proximo_cbt_nros[cliente.cbte_tipo], # Este campo (cbte_nro) se puede pisar
            receptor_cuit = cliente.cuit,
            imp_neto = cliente.imp_neto_efectivo,
            imp_iva = cliente.imp_iva,
            imp_total = cliente.imp_total,
            alicuota_iva_id = cliente.alicuota_iva_id,
            receptor_iva_cond = cliente.iva_cond,
            fecha = today,
            serv_desde = today.replace(day=1),
            serv_hasta = today.replace(day=calendar.monthrange(today.year, today.month)[1]),
            vto_pago = today + timedelta(days=30),
        )
        proximo_cbt_nros[cliente.cbte_tipo] += 1

    app.show_table_sync(
        label="Tabla de facturas a realizar",
        columns=["Cbte Nro", "Tipo Cbte", "CUIT", "Empresa", "Imp. Neto", "IVA", "Imp. Total", "Cond. IVA", "Serv. Desde", "Serv. Hasta", "Vto. Pago"],
        rows=[
            (
                str(s.cbte_nro),
                s.cbte_tipo.name.replace("FACTURA_", ""),
                s.receptor_cuit,
                receptores[s.receptor_cuit].razon_social,
                f"${s.imp_neto:,.2f}",
                f"${s.imp_iva:,.2f}",
                f"${s.imp_total:,.2f}",
                s.receptor_iva_cond.name.replace("_", " "),
                str(s.serv_desde),
                str(s.serv_hasta),
                str(s.vto_pago),
            )
            for s in solicitudes.values()
        ],
        sub_columns=[],
        sub_rows=[[] for _ in solicitudes],
    )

    if not app.ask_sync("Continuar con la facturacion?"):
        return


    # Hacemos facturacion cliente por cliente sin bached para mejor control y siplicidad del codigo (no necesitamos baches)
    log.info("Solicitando CAE a WSFE")

    invoice_path = os.environ["ARCA_FACTURA_PATH"]
    logo_path = Path(os.environ["ARCA_FACTURA_LOGO"])

    # Cada vez que se skipea un cliente tenemos que anotarlo para decrementar el numero de comprobante
    skipped_cbt_nros: dict[CbteTipo, int] = {}

    # Cliente: datos del excel
    for i, cliente in enumerate(clientes):
        # Receptor: datos del cliente sacados del padron (la mayoria de los datos de aca tienen prioridad sobre los del cliente en caso de estar repetidos, ejemplo razon social o domicilio)
        assert cliente.cuit in receptores, f"error de consistencia: {cliente}"
        receptor = receptores[cliente.cuit]
        
        # Solicitud: datos de la factura reelevantes para ARCA (hay cosas que a ARCA no le interesan pero que en el pdf debemos poner como por ejemplo los items)
        assert cliente.cuit in solicitudes, f"error de consistencia: {cliente}"
        solicitud = solicitudes[cliente.cuit]

        if not cliente.cbte_tipo in skipped_cbt_nros:
            skipped_cbt_nros[cliente.cbte_tipo] = 0

        log.info(f"Procesando cliente {receptor.cuit} - {receptor.razon_social} ({i+1}/{len(clientes)})")

        proceed: bool = app.ask_sync(f"[bold]Cliente:[/bold] {receptor.cuit} - {receptor.razon_social} - ${solicitud.imp_total:,.2f}", yes_label="Continuar", no_label="Omitir")
        if not proceed:
            log.info("Cliente omitido")
            skipped_cbt_nros[cliente.cbte_tipo] += 1
            continue
        
        # Si se omiten N clientes se debe decrementar en N unidades los numeros de comprobantes de los siguientes
        solicitud.cbte_nro -= skipped_cbt_nros[cliente.cbte_tipo]

        # Solicitamos la factura solo para este cliente
        cae_batch_result = fe.cae_solicitar(emisor.cuit, [solicitud])

        if cae_batch_result.error:
            log.error(f"Error de facturacion para el cliente {cliente.cuit}: {format_exception(cae_batch_result.error)}")
            continue

        # CAE Result: si ARCA aprobo o no la factura
        assert solicitud.cbte_nro in cae_batch_result.resultados, f"error de consistencia: {solicitud}"
        cae_result = cae_batch_result.resultados[solicitud.cbte_nro]

        pdf_path = Path(invoice_path.format(
            cbte_nro = cae_result.cbte_nro,
            razon_social_receptor = receptor.razon_social.
                replace(" ", "_").
                replace(".", "").
                replace("/", "_"),
            cuit_receptor = receptor.cuit
        ))
        log.debug(f"Generando factura {pdf_path}")
        generate_invoice_pdf(
            cuit_emisor = emisor.cuit,
            factura = solicitud,
            cliente = cliente,
            result = cae_result,
            output_path = pdf_path,
            emisor = emisor,
            receptor = receptor,
            logo_path = logo_path,
            items = [ItemFactura(
                descripcion=f"{item.colaboradores} Licencias",
                cantidad=1.0,
                precio_unitario=item.imp_neto,
                unidad_medida=UnidadMedida.UNIDADES,
                codigo = str(i),
            ) for i, item in enumerate(cliente.items)],
            cert_path = Path(os.environ["ARCA_CERTIFICATE"]).expanduser(),
            key_path = Path(os.environ["ARCA_PRIVATE_KEY"]).expanduser(),
        )

    log.info("Facturacion completada")
    app.ask_sync("", yes_label="Volver al menu", no_label=None)





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
        elif selection == "menu-validar":
            validar_pdf(app, log)
        elif selection == "quit":
            app.exit()
            return


if __name__ == "__main__":
    app = FacturadorApp()
    app.set_worker(lambda: main(app))
    app.run()
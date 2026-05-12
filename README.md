# Facturador ARCA

TUI interna para emisión de facturas electrónicas en Argentina vía los web services de ARCA.

Cubre autenticación con certificado digital (WSAA), consulta al padrón (ws_sr_constancia_inscripcion), autorización de comprobantes (WSFEv1) y generación de PDFs firmados digitalmente.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![Textual](https://img.shields.io/badge/TUI-Textual-purple)

## Stack

| | |
|---|---|
| TUI | [Textual](https://github.com/Textualize/textual) |
| Web services | `requests` + `xml.etree` |
| Criptografía | `cryptography` |
| PDF | `reportlab` + `pyhanko` |
| Excel | `pandas` + `openpyxl` |

## Instalación

Requiere Python 3.11+ y un certificado digital emitido por ARCA.

```bash
git clone https://github.com/facundosalerno/facturador-arca
cd facturador-arca
pip install -r requirements.txt
cp .env.example .env
```

Completar `.env` con las credenciales. Ver `.env.example` para las variables disponibles.

Para obtener el certificado digital, seguir el [manual de ARCA](https://www.afip.gob.ar/ws/).

## Uso

```bash
python main.py
```

- **Ver clientes** — tabla del Excel con filtros por estado y ajuste
- **Ver facturas** — últimos comprobantes autorizados; permite regenerar PDFs históricos
- **Generar facturas** — solicita CAE, consulta el padrón y genera los PDFs
- **Validar PDF** — verifica la firma digital de un comprobante

## Excel de clientes

Hoja llamada `Clientes` con las columnas definidas en [src/input/excel.py](src/input/excel.py). Soporta celdas mergeadas para agrupar múltiples ítems bajo un mismo CUIT.

## Recursos

- [Documentación WSFEv1](https://www.afip.gob.ar/ws/)
- [Tablas de constantes ARCA](https://www.afip.gob.ar/fe/ayuda/tablas.asp)
- [Verificador de comprobantes](https://servicioscf.afip.gob.ar/publico/comprobantes/cae.aspx)
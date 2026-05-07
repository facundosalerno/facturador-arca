import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context


class _AfipSSLAdapter(HTTPAdapter):
    # El servidor de producción de AFIP (servicios1.afip.gov.ar) usa DHE con una clave
    # de 1024 bits, que OpenSSL moderno rechaza por ser insegura (mínimo esperado: 2048).
    # El servidor de homologación no tiene este problema porque usa ECDHE.
    # SECLEVEL=1 baja el umbral mínimo aceptado para claves DH, permitiendo la conexión.

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


class AfipSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.mount("https://", _AfipSSLAdapter())

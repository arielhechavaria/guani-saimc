"""`saimc.ingestion.whatsapp_health` — estado de la sesión de WAHA.

Cierra el hueco N-02 (`docs/requisitos.md` §02): «Ya ocurrió: la sesión de WAHA
cayó a `FAILED` y toda la ingesta automática paró en silencio». Hasta este
slice no había ningún chequeo, así que la única forma de enterarse era notar
que la cola dejaba de crecer.

Igual que `whatsapp_webhook`, la forma de la respuesta se fija contra lo que
documenta WAHA y no contra una instancia real. Es una calibración razonada: si
la instancia del operador devuelve otra cosa, el parser degrada a
«desconocido» en vez de mentir, y se ajusta con la respuesta real en la mano.
"""

import httpx
import pytest

from saimc.ingestion import whatsapp_health


class TestParseoDelEstado:
    """Puro, sin red: qué significa cada cuerpo de respuesta."""

    @pytest.mark.parametrize(
        ("estado", "esperado"),
        [
            ("WORKING", "working"),
            ("working", "working"),
            ("FAILED", "failed"),
            ("STOPPED", "stopped"),
            ("STARTING", "starting"),
            ("SCAN_QR_CODE", "scan_qr"),
        ],
    )
    def test_reconoce_los_estados_de_waha(self, estado: str, esperado: str) -> None:
        assert whatsapp_health.parse_status({"status": estado}) == esperado

    def test_un_estado_que_no_conocemos_no_se_inventa(self) -> None:
        """Mejor «desconocido» que afirmar que anda o que se cayó."""
        assert whatsapp_health.parse_status({"status": "ALGO_NUEVO"}) == "unknown"

    @pytest.mark.parametrize("cuerpo", [None, [], "texto", {}, {"status": 5}])
    def test_un_cuerpo_sin_forma_reconocible_es_desconocido(
        self, cuerpo: object
    ) -> None:
        assert whatsapp_health.parse_status(cuerpo) == "unknown"

    def test_tolera_la_sesion_envuelta_en_una_lista(self) -> None:
        """WAHA lista sesiones en `/api/sessions`; si algún día se consulta
        así, la primera entrada sirve igual."""
        assert whatsapp_health.parse_status([{"status": "WORKING"}]) == "working"


class TestSaludDeLaSesion:
    def test_sin_configuracion_no_llama_ni_afirma(self) -> None:
        salud = whatsapp_health.session_health(
            base_url=None, api_key=None, session="default"
        )

        assert salud.configured is False
        assert salud.status == "unknown"

    def test_una_sesion_trabajando_esta_sana(self) -> None:
        transporte = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "WORKING"})
        )

        salud = whatsapp_health.session_health(
            base_url="http://waha:3000",
            api_key="k",
            session="default",
            transport=transporte,
        )

        assert salud.status == "working"
        assert salud.healthy is True

    def test_una_sesion_caida_no_esta_sana(self) -> None:
        transporte = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "FAILED"})
        )

        salud = whatsapp_health.session_health(
            base_url="http://waha:3000",
            api_key="k",
            session="default",
            transport=transporte,
        )

        assert salud.status == "failed"
        assert salud.healthy is False

    def test_waha_apagado_degrada_sin_levantar(self) -> None:
        """Que WAHA no responda es exactamente el caso que hay que mostrar, no
        un error que rompa `/control`."""

        def _cae(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("sin conexión")

        salud = whatsapp_health.session_health(
            base_url="http://waha:3000",
            api_key="k",
            session="default",
            transport=httpx.MockTransport(_cae),
        )

        assert salud.status == "unreachable"
        assert salud.healthy is False
        assert salud.configured is True

    def test_un_status_http_de_error_degrada(self) -> None:
        transporte = httpx.MockTransport(lambda request: httpx.Response(500))

        salud = whatsapp_health.session_health(
            base_url="http://waha:3000",
            api_key="k",
            session="default",
            transport=transporte,
        )

        assert salud.status == "unreachable"

    def test_consulta_la_sesion_configurada(self) -> None:
        vistas: list[str] = []

        def _registrar(request: httpx.Request) -> httpx.Response:
            vistas.append(str(request.url))
            return httpx.Response(200, json={"status": "WORKING"})

        whatsapp_health.session_health(
            base_url="http://waha:3000/",
            api_key="k",
            session="otra",
            transport=httpx.MockTransport(_registrar),
        )

        assert "otra" in vistas[0]
        # La barra de más en `base_url` no puede producir una URL doble.
        assert "//api" not in vistas[0].replace("http://", "")

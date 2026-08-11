"""Redacción de datos personales — casos sintéticos.

Igual que `test_extraction.py`: los casos están escritos a mano y la respuesta
correcta se define acá, antes e independientemente de correr el redactor.

La regla que se está implementando es de `CLAUDE.md`: «Nada de datos
personales: no capturamos ni almacenamos teléfonos, direcciones exactas ni
datos de contacto de anunciantes» (riesgo §10.6 del whitepaper).

Los casos negativos importan tanto como los positivos: un redactor que se coma
los precios destruye la única señal que el sistema existe para medir.
"""

import pytest

from saimc.privacy import PHONE_PLACEHOLDER, redact_personal_data


class TestRedactaTelefonos:
    """Formatos observados en el corpus real al 2026-08-11."""

    @pytest.mark.parametrize(
        "texto",
        [
            # Encabezado de chat de WhatsApp tal como lo deja el OCR.
            "3:26 >» -* $ sl Eo ya +53 5 5550101 Hoy, 9:22 a. m.",
            # Mismo número sin separadores internos.
            "contacto +5355550101 disponible",
            # Sin prefijo de país: móvil cubano suelto, 8 dígitos desde 5.
            "Escribir solo por watsap 55550202",
            # Pegado a un hashtag: no hay frontera de palabra antes del 5,
            # por eso el patrón usa lookbehind de dígito y no `\\b`.
            "#Santiago_de_Cuba #llame_escriba_55550303 #Sigue_nuestra_pagina",
        ],
    )
    def test_el_numero_desaparece_y_deja_marca(self, texto: str) -> None:
        resultado = redact_personal_data(texto)
        assert PHONE_PLACEHOLDER in resultado
        # Ningún run de 8 dígitos que empiece en 5 sobrevive.
        assert not any(
            resultado[i : i + 8].isdigit() and resultado[i] == "5"
            for i in range(len(resultado) - 7)
        )

    def test_conserva_el_resto_del_texto(self) -> None:
        resultado = redact_personal_data("Pollo a 420 cup, watsap 55550202, Santiago")
        assert "Pollo a 420 cup" in resultado
        assert "Santiago" in resultado

    def test_redacta_varios_numeros_en_el_mismo_texto(self) -> None:
        resultado = redact_personal_data("llame 55550303 o escriba +53 5 5550101")
        assert resultado.count(PHONE_PLACEHOLDER) == 2


class TestNoTocaLosPrecios:
    """Un falso positivo acá corrompe el dato que sostiene todo el proyecto."""

    @pytest.mark.parametrize(
        "texto",
        [
            "Sacos Arroz Argentino (Importado) 25Kg. $17,500 Cup.Sale $318.20 Lb",
            "Sacos de azúcar blanca (Importado) 25Kg $23,500.Sale 427.27Lb",
            "Azúcar Blanca a 420 cup por libra",
            "Pasta para fregar 500 g 1760.00",
            "Suavizante d ropa pomo 1lt 2320.00cup",
            "Cepillo de dientes 250.00 cup",
            "Pastillitas de pollo ahumado a 30 cup",
        ],
    )
    def test_texto_de_precios_queda_intacto(self, texto: str) -> None:
        assert redact_personal_data(texto) == texto


class TestContrato:
    def test_es_idempotente(self) -> None:
        """Correr el backfill dos veces no debe degradar el texto."""
        una = redact_personal_data("watsap 55550202 pollo a 420")
        assert redact_personal_data(una) == una

    def test_tolera_none_y_vacio(self) -> None:
        assert redact_personal_data(None) is None
        assert redact_personal_data("") == ""

    def test_texto_sin_telefono_devuelve_el_mismo_objeto(self) -> None:
        texto = "Arroz a 730 cup en Santiago de Cuba"
        assert redact_personal_data(texto) == texto

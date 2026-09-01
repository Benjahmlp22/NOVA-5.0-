"""El cerebro rápido de la nube: cuándo se enciende y cómo se le habla.

Lo primero que se comprueba es que NO se enciende solo. Todo lo demás de
este módulo es una comodidad; eso es una promesa sobre los datos de
Benja.
"""

from __future__ import annotations

import json

from nova.app import elegir_cerebro
from nova.llm.remoto import ClienteRemoto, a_openai, leer_clave

# ── Cuándo se enciende ───────────────────────────────────────────────

def test_sin_clave_no_se_enciende_jamas():
    """Ni pidiéndolo. Sin clave no hay nada que encender."""
    for preferencia in ("", "rapido", "local"):
        assert not elegir_cerebro(
            preferencia=preferencia, hay_clave=False, auto=True, apretado=True
        )


def test_no_se_enciende_solo_aunque_haya_un_juego():
    """Que tus datos salgan del PC no puede ser un efecto secundario."""
    assert not elegir_cerebro(preferencia="", hay_clave=True, auto=False, apretado=True)


def test_se_enciende_si_el_lo_pide():
    assert elegir_cerebro(preferencia="rapido", hay_clave=True, apretado=False)


def test_modo_local_gana_aunque_falte_vram():
    """Si ha dicho «modo local», es local. Aunque vaya lenta."""
    assert not elegir_cerebro(
        preferencia="local", hay_clave=True, auto=True, apretado=True
    )


def test_el_automatico_solo_actua_si_se_ha_activado_a_mano():
    assert elegir_cerebro(preferencia="", hay_clave=True, auto=True, apretado=True)
    assert not elegir_cerebro(preferencia="", hay_clave=True, auto=True, apretado=False)


# ── La clave ─────────────────────────────────────────────────────────

def test_la_clave_sale_del_entorno_o_de_un_fichero(tmp_path):
    fichero = tmp_path / "groq.key"
    assert leer_clave(fichero, "") == ""

    fichero.write_text("  gsk_de_mentira  \n", encoding="utf-8")
    assert leer_clave(fichero, "") == "gsk_de_mentira"
    # El entorno manda sobre el fichero.
    assert leer_clave(fichero, "gsk_del_entorno") == "gsk_del_entorno"


def test_sin_clave_el_cliente_se_declara_no_disponible():
    cliente = ClienteRemoto("https://ejemplo", "modelo", "")
    assert not cliente.disponible
    assert not cliente.available()


# ── Traducción del dialecto ──────────────────────────────────────────
#
# `Agent` construye la conversación con la forma de Ollama. OpenAI exige
# un `id` en cada llamada y que la respuesta lo cite, y los argumentos
# como cadena JSON. Sin traducir, la API contesta 400 y NOVA se queda
# muda justo en el modo que se supone que es el bueno.

def test_las_llamadas_llevan_id_y_la_respuesta_lo_cita():
    traducido = a_openai([
        {"role": "system", "content": "eres NOVA"},
        {"role": "user", "content": "qué hora es"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "pc_hora", "arguments": {"zona": "es"}}}],
        },
        {"role": "tool", "name": "pc_hora", "content": "Son las seis."},
    ])

    llamada = traducido[2]["tool_calls"][0]
    assert llamada["type"] == "function"
    assert llamada["id"]
    # Argumentos como CADENA, no como objeto.
    assert json.loads(llamada["function"]["arguments"]) == {"zona": "es"}
    # Y el mensaje de herramienta cita ese mismo id.
    assert traducido[3]["tool_call_id"] == llamada["id"]
    assert "name" not in traducido[3]


def test_varias_llamadas_se_emparejan_en_orden():
    traducido = a_openai([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "a", "arguments": {}}},
                {"function": {"name": "b", "arguments": {}}},
            ],
        },
        {"role": "tool", "name": "a", "content": "hecho a"},
        {"role": "tool", "name": "b", "content": "hecho b"},
    ])
    ids = [c["id"] for c in traducido[0]["tool_calls"]]
    assert [traducido[1]["tool_call_id"], traducido[2]["tool_call_id"]] == ids


def test_una_conversacion_normal_no_se_toca():
    original = [
        {"role": "system", "content": "eres NOVA"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "dime"},
    ]
    assert a_openai(original) == original


# ── Lo que enseñó la clave de verdad ─────────────────────────────────
#
# El cerebro remoto se escribió a ciegas, sin clave. En cuanto hubo una,
# el 01/09, salieron tres cosas a la primera llamada.

def test_los_opcionales_pueden_llegar_nulos():
    """Groq valida el esquema y devuelve 400 si un opcional llega null.

    Los modelos grandes rellenan TODOS los huecos y ponen `null` en los
    que no aplican. Medido: gpt-oss-120b mandaba {"nombre": null} para
    codigo.proyectos y el turno entero se caía con
    «`/nombre`: expected string, but got null».
    """
    from nova.llm.remoto import relajar_esquemas

    original = [{
        "type": "function",
        "function": {
            "name": "codigo_ver",
            "description": "lee un archivo",
            "parameters": {
                "type": "object",
                "properties": {
                    "proyecto": {"type": "string"},
                    "archivo": {"type": "string"},
                    "desde": {"type": "integer"},
                },
                "required": ["proyecto", "archivo"],
            },
        },
    }]
    props = relajar_esquemas(original)[0]["function"]["parameters"]["properties"]

    # El opcional admite null...
    assert props["desde"]["type"] == ["integer", "null"]
    # ...y los obligatorios NO: mandarlos nulos sigue siendo un error.
    assert props["proyecto"]["type"] == "string"
    assert props["archivo"]["type"] == "string"


def test_relajar_no_toca_el_catalogo_original():
    """Ollama no valida nada y le vale el esquema estricto: esto es sólo
    para salir a la nube."""
    from nova.llm.remoto import relajar_esquemas

    original = [{
        "type": "function",
        "function": {
            "name": "x",
            "parameters": {"type": "object", "properties": {"a": {"type": "string"}}},
        },
    }]
    relajar_esquemas(original)
    assert original[0]["function"]["parameters"]["properties"]["a"]["type"] == "string"


# ── El 429 casi nunca es "se te acabó" ───────────────────────────────
#
# Cabeceras reales de la capa gratuita, leídas el 02/09: 1000 peticiones
# y 8000 TOKENS POR MINUTO. Un turno de NOVA gasta 3849 (3683 de
# entrada, casi todo el catálogo), así que caben dos por minuto y el
# tercero se pasa. Pero el cubo se rellena en 615 ms: esperar y repetir
# sale infinitamente mejor que caerse al modelo pequeño.

def test_entiende_los_formatos_de_espera():
    from nova.llm.remoto import _segundos

    assert _segundos("615ms") == 0.615      # lo que devuelve Groq
    assert _segundos("1m26.4s") == 86.4
    assert _segundos("2.5s") == 2.5
    assert _segundos("30") == 30.0          # retry-after, en segundos pelados
    assert _segundos("basura") is None
    assert _segundos(None) is None
    assert _segundos("") is None


class _RespuestaFalsa:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


def test_ante_una_espera_corta_repite(monkeypatch):
    from nova.llm.remoto import ClienteRemoto

    dormido = []
    monkeypatch.setattr("nova.llm.remoto.time.sleep", dormido.append)
    c = ClienteRemoto("https://x", "m", "clave")

    assert c._esperar_y_reintentar(_RespuestaFalsa({"x-ratelimit-reset-tokens": "615ms"}))
    assert dormido and dormido[0] < 1.0


def test_ante_una_cuota_de_verdad_se_rinde(monkeypatch):
    """Si pide esperar mucho, es una cuota real: mejor el modelo de casa
    que dejar a Benja mirando el orbe."""
    from nova.llm.remoto import ClienteRemoto

    dormido = []
    monkeypatch.setattr("nova.llm.remoto.time.sleep", dormido.append)
    c = ClienteRemoto("https://x", "m", "clave")

    assert not c._esperar_y_reintentar(_RespuestaFalsa({"retry-after": "3600"}))
    assert not dormido


def test_sin_cabecera_no_se_inventa_la_espera(monkeypatch):
    from nova.llm.remoto import ClienteRemoto

    monkeypatch.setattr("nova.llm.remoto.time.sleep", lambda s: None)
    assert not ClienteRemoto("https://x", "m", "k")._esperar_y_reintentar(
        _RespuestaFalsa({})
    )


def test_modo_rapido_de_serie_pero_local_sigue_ganando():
    """`siempre` es "arranca en rápido"; decir "modo local" es explícito."""
    assert elegir_cerebro(preferencia="", hay_clave=True, siempre=True)
    assert not elegir_cerebro(preferencia="local", hay_clave=True, siempre=True)
    # Y sin clave da igual lo que ponga.
    assert not elegir_cerebro(preferencia="", hay_clave=False, siempre=True)


# ── Sólo las herramientas que vienen a cuento ────────────────────────
#
# El catálogo entero son 3683 tokens de entrada por turno, y el límite
# gratuito son 8000 POR MINUTO: salían dos turnos y el tercero se comía
# un 429 con 23 segundos de espera. Recortar descripciones no servía
# (200 tokens de 4400); lo que pesa es el número de herramientas.

def _catalogo():
    from nova.tools import build_registry
    return build_registry().llm_schemas()


def test_la_herramienta_que_toca_entra_siempre():
    from nova.llm.remoto import elegir_herramientas

    casos = [
        ("hazme un juego en html", "codigo_escribir"),
        ("prueba los tests de nodika", "codigo_probar"),
        ("cambia el color en serpiente-nova", "codigo_editar"),
        ("abre discord", "app_open"),
        ("baja el volumen de spotify", "app_volume"),
        ("recuerdame sacar la basura", "recordatorio_crear"),
        ("busca en internet quien gano", "web_search"),
        ("que tengo en pantalla", "pantalla_leer"),
    ]
    todas = _catalogo()
    for frase, esperada in casos:
        elegidas = [t["function"]["name"] for t in elegir_herramientas(todas, frase)]
        assert esperada in elegidas, f"{frase!r} -> falta {esperada}"


def test_recorta_de_verdad():
    import json

    from nova.llm.remoto import TOPE_HERRAMIENTAS, elegir_herramientas

    todas = _catalogo()
    pocas = elegir_herramientas(todas, "abre discord")
    assert len(pocas) == TOPE_HERRAMIENTAS < len(todas)
    # Y el ahorro es el que se buscaba: menos de la mitad.
    assert len(json.dumps(pocas)) < len(json.dumps(todas)) / 2


def test_un_catalogo_pequeno_se_manda_entero():
    from nova.llm.remoto import elegir_herramientas

    tres = _catalogo()[:3]
    assert elegir_herramientas(tres, "lo que sea") == tres


def test_la_eleccion_es_reproducible():
    """Ante el empate manda el orden del registro, no el azar: si no, no
    se puede probar ni depurar."""
    from nova.llm.remoto import elegir_herramientas

    todas = _catalogo()
    a = [t["function"]["name"] for t in elegir_herramientas(todas, "cosa rara sin relacion")]
    b = [t["function"]["name"] for t in elegir_herramientas(todas, "cosa rara sin relacion")]
    assert a == b

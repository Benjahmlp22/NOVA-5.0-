"""La máquina de estados de la escucha, sin micrófono ni modelos.

Se prueba `_entender`, que es donde se decide todo lo importante: si la
llamada era de verdad, si es un comando o una despedida, y qué texto se
manda al cerebro. La captura de audio y los modelos se inyectan.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from nova.voice.listener import VoiceListener
from nova.voice.transcriptor import Transcripcion


class TranscriptorFalso:
    """Devuelve el guion que se le dé, en orden.

    `no_habla` y `logprob` se pueden forzar para probar el filtro de
    confianza, que es lo que separa "me han dicho algo" de "ha sonado
    algo" — sin él, NOVA responde al audio de un juego de fondo.
    """

    def __init__(self, *textos: str, no_habla: float = 0.0, logprob: float = -0.2) -> None:
        self.textos = list(textos)
        self.no_habla = no_habla
        self.logprob = logprob
        self.recibido: list[int] = []

    def transcribir_detallado(self, senal) -> Transcripcion:  # noqa: ANN001
        self.recibido.append(len(senal))
        texto = self.textos.pop(0) if self.textos else ""
        return Transcripcion(texto, self.no_habla, self.logprob)

    def transcribir(self, senal) -> str:  # noqa: ANN001
        return self.transcribir_detallado(senal).texto

    def cargar(self) -> bool:
        return True

    def precalentar(self) -> None:
        pass


def _oyente(*textos: str, no_habla: float = 0.0, logprob: float = -0.2, **kw):
    eventos: list[tuple[str, object]] = []
    oyente = VoiceListener(
        Path("/no/existe"),
        "nova",
        transcriptor=TranscriptorFalso(*textos, no_habla=no_habla, logprob=logprob),
        on_wake=lambda con_comando: eventos.append(("wake", con_comando)),
        on_command=lambda t: eventos.append(("comando", t)),
        on_sleep=lambda m: eventos.append(("dormir", m)),
        **kw,
    )
    return oyente, eventos


def _audio(segundos: float = 1.0) -> np.ndarray:
    """Algo que pase el filtro de "hay señal" y no sean ceros."""
    n = int(16000 * segundos)
    return (0.05 * np.sin(np.linspace(0, 400, n))).astype(np.float32)


# ── La etapa 2 confirma la llamada ───────────────────────────────────

def test_el_nombre_solo_despierta_sin_comando():
    oyente, eventos = _oyente("NOVA.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == [("wake", False)]
    assert oyente.awake


def test_nombre_y_orden_del_tiron_llegan_juntos():
    """El pre-roll hace que la orden entera esté en el audio, no sólo el nombre."""
    oyente, eventos = _oyente("NOVA, abre Discord.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == [("wake", True), ("comando", "abre discord")]


def test_el_aviso_de_wake_dice_si_viene_orden_pegada():
    """Sin ese dato, NOVA saluda y contesta a la vez, hablándose encima."""
    oyente, eventos = _oyente("NOVA, qué hora es.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos[0] == ("wake", True)


def test_falsa_alarma_de_la_etapa_1_se_descarta_en_silencio():
    """«no va a funcionar el mando» suena igual que el nombre.

    La etapa 1 abre la puerta de más a propósito; si la etapa 2 no ve el
    nombre, NOVA vuelve a dormir sin haber hecho ruido — el chime no ha
    sonado todavía.
    """
    oyente, eventos = _oyente("No va a funcionar el mando.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []
    assert not oyente.awake


def test_la_novia_no_es_el_nombre():
    """Contener las letras no basta: tiene que ser palabra suelta."""
    oyente, eventos = _oyente("La novia de mi hermano viene mañana.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []


def test_transcripcion_vacia_no_despierta():
    oyente, eventos = _oyente("")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []


# ── Conversación continua ────────────────────────────────────────────

def test_despierta_no_hace_falta_repetir_el_nombre():
    oyente, eventos = _oyente("cierra chrome")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("comando", "cierra chrome")]


def test_repetir_el_nombre_estando_despierta_no_estorba():
    """«NOVA, cierra chrome» en el segundo turno: el nombre se quita."""
    oyente, eventos = _oyente("NOVA, cierra Chrome.")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("comando", "cierra chrome")]


@pytest.mark.parametrize("despedida", [
    "hasta luego", "adiós", "eso es todo", "nos vemos", "gracias NOVA",
])
def test_las_despedidas_cierran_el_turno_sin_mandar_comando(despedida):
    oyente, eventos = _oyente(despedida)
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("dormir", "despedida")]
    assert not oyente.awake


def test_un_comando_normal_no_se_confunde_con_despedida():
    oyente, eventos = _oyente("dime cuanto queda de bateria")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos[0][0] == "comando"


# ── Pre-roll ─────────────────────────────────────────────────────────

def test_el_preroll_guarda_el_ultimo_segundo():
    oyente, _ = _oyente(preroll_s=1.0)
    # 50 ms por bloque → 20 bloques en un segundo.
    assert oyente._preroll.maxlen == 20


def test_el_preroll_nunca_es_cero():
    """Con 0 se perdería el principio de cada orden, que es el bug de NOVA4."""
    oyente, _ = _oyente(preroll_s=0.0)
    assert oyente._preroll.maxlen >= 1


def test_al_dejar_de_hablar_se_tira_lo_capturado_mientras_hablaba():
    """NOVA no puede oírse a sí misma. Era el bug de "responde sin parar".

    El pre-roll seguía llenándose durante el mute, y lo que hay en ese
    segundo es la voz de NOVA. Al soltar el mute entraba como comando:
    se oía, contestaba, se volvía a oír, y así indefinidamente.
    """
    oyente, _ = _oyente()
    oyente.mute()
    oyente._preroll.append(_audio(0.05))   # esto es NOVA hablando
    assert len(oyente._preroll) == 1

    oyente.unmute()
    assert len(oyente._preroll) == 0
    assert not oyente._muted.is_set()


# ── Lo que suena no siempre es alguien hablándole a NOVA ─────────────

def test_lo_que_no_es_habla_se_descarta():
    """Whisper devuelve SIEMPRE alguna frase, también con música o un juego."""
    oyente, eventos = _oyente("Suscríbete al canal", no_habla=0.95)
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == []


def test_lo_entendido_sin_confianza_se_descarta():
    oyente, eventos = _oyente("abre discord", logprob=-2.5)
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == []


def test_una_llamada_dudosa_tampoco_despierta():
    oyente, eventos = _oyente("NOVA, abre Discord", no_habla=0.9)
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []
    assert not oyente.awake


def test_una_palabra_suelta_no_es_una_orden():
    """Trozos de conversación ajena o de la tele, estando despierta."""
    oyente, eventos = _oyente("vale")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    # "vale" sí pasa: es una confirmación válida.
    assert eventos == [("comando", "vale")]

    oyente2, eventos2 = _oyente("mando")
    oyente2._awake = True
    oyente2._entender(_audio(), exige_nombre=False)
    assert eventos2 == []


def test_el_reloj_de_seguir_despierta_cuenta_desde_el_ultimo_turno():
    """Contarlo desde el último RUIDO la dejaba despierta para siempre.

    Con una tele de fondo el umbral se cruza cada dos por tres, y
    despierta responde a lo que oiga.
    """
    import time

    oyente, _ = _oyente("abre discord")
    oyente._awake = True
    antes = oyente._ultimo_turno
    oyente._entender(_audio(), exige_nombre=False)
    assert oyente._ultimo_turno > antes
    assert oyente._ultimo_turno <= time.monotonic()


def test_marcar_turno_reinicia_el_reloj():
    oyente, _ = _oyente()
    antes = oyente._ultimo_turno
    oyente.marcar_turno()
    assert oyente._ultimo_turno > antes


# ── Quitar el nombre ─────────────────────────────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("nova", ""),
    ("nova abre discord", "abre discord"),
    ("oye nova que hora es", "que hora es"),
    ("nova, cierra chrome.", "cierra chrome"),
])
def test_quitar_nombre(texto, esperado):
    oyente, _ = _oyente()
    assert oyente._quitar_nombre(texto) == esperado


@pytest.mark.parametrize("texto", [
    "no va a funcionar el mando",
    "la novia de mi hermano",
    "esto no va a salir bien",
    "abre discord",
])
def test_quitar_nombre_devuelve_none_si_no_esta(texto):
    oyente, _ = _oyente()
    assert oyente._quitar_nombre(texto) is None


# ── Dormirse ─────────────────────────────────────────────────────────

def test_sleep_now_solo_avisa_si_estaba_despierta():
    oyente, eventos = _oyente()
    oyente.sleep_now("silencio")
    assert eventos == []

    oyente._awake = True
    oyente.sleep_now("silencio")
    assert eventos == [("dormir", "silencio")]


# ── La ventana de seguimiento ────────────────────────────────────────
#
# La conversación continua no puede significar "micro abierto veinte
# segundos a todo lo que se diga en la habitación". En un log real NOVA
# despertó bien y luego procesó "se despertó", "no quiero nada, cállate"
# y "dormite" como órdenes — eran intentos de pararla.

def test_recien_hablado_se_le_sigue_sin_nombrarla():
    oyente, _ = _oyente()
    oyente._awake = True
    oyente.marcar_turno()
    assert oyente._en_seguimiento()


def test_pasado_el_hueco_hay_que_volver_a_nombrarla():
    import time

    oyente, _ = _oyente(seguimiento_s=0.05)
    oyente._awake = True
    oyente.marcar_turno()
    time.sleep(0.1)
    assert not oyente._en_seguimiento()


def test_sin_ningun_turno_todavia_no_hay_seguimiento():
    """Recién arrancada, nadie le ha hablado: nada está en curso."""
    oyente, _ = _oyente()
    assert not oyente._en_seguimiento()


@pytest.mark.parametrize("frase", [
    "cállate", "duérmete", "dormite", "silencio", "para ya", "déjame",
])
def test_mandarla_callar_funciona(frase):
    """Salieron de un log real: las probó y NOVA siguió a lo suyo."""
    oyente, eventos = _oyente(frase)
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("dormir", "despedida")]
    assert not oyente.awake


# ── Cortarla hablando ────────────────────────────────────────────────
#
# Sin esto había que esperar a que terminara la frase para poder
# corregirla, que es justo cuando más ganas dan de cortarla. Lo difícil
# no es detectar voz: es no confundir la voz de NOVA con la tuya.

def _oyente_hablando(**kw):
    oyente, eventos = _oyente(**kw)
    oyente.mute()
    return oyente, eventos


def test_hablarle_por_encima_la_calla():
    oyente, _ = _oyente_hablando()
    oyente._umbral = 0.004
    oyente.nivel_salida(0.0)          # NOVA en una pausa
    fuerte = 0.004 * 3
    for _ in range(3):
        salta = oyente._me_estan_interrumpiendo(fuerte)
    assert salta


def test_su_propia_voz_no_la_interrumpe():
    """Con altavoces, el micro recoge a NOVA. Sin esto se cortaría sola."""
    oyente, _ = _oyente_hablando()
    oyente._umbral = 0.004
    oyente.nivel_salida(0.3)          # NOVA sonando fuerte
    for _ in range(10):
        salta = oyente._me_estan_interrumpiendo(0.5)
    assert not salta


def test_un_ruido_flojo_no_corta_una_frase():
    oyente, _ = _oyente_hablando()
    oyente._umbral = 0.004
    oyente.nivel_salida(0.0)
    for _ in range(10):
        salta = oyente._me_estan_interrumpiendo(0.005)  # apenas sobre el umbral
    assert not salta


def test_hace_falta_voz_sostenida_no_un_golpe():
    """Un golpe en la mesa dura menos que "no, espera"."""
    oyente, _ = _oyente_hablando()
    oyente._umbral = 0.004
    oyente.nivel_salida(0.0)
    assert not oyente._me_estan_interrumpiendo(0.05)   # un solo bloque
    assert not oyente._me_estan_interrumpiendo(0.05)   # dos
    assert oyente._me_estan_interrumpiendo(0.05)       # 150 ms ya sí


def test_se_puede_apagar_del_todo():
    oyente, _ = _oyente_hablando(interrumpir=False)
    oyente._umbral = 0.004
    oyente.nivel_salida(0.0)
    for _ in range(20):
        assert not oyente._me_estan_interrumpiendo(0.5)


# ── Preguntar y dormirse antes de la respuesta ───────────────────────
#
# El absurdo que había: NOVA pedía permiso, el usuario se paraba a
# pensar, y a los 20 s de silencio ella se dormía. Contestar "sí" a una
# pregunta que te acaba de hacer no puede exigir volver a nombrarla.

def test_mientras_espera_respuesta_no_se_le_agota_el_tiempo():
    oyente, _ = _oyente(awake_timeout_s=20.0)
    oyente._awake = True
    oyente._ultimo_turno = time.monotonic() - 999   # hace un siglo del turno
    assert oyente._esperando_algo() is False

    oyente.esperar_respuesta(True)
    assert oyente._esperando_algo() is True
    # Y le puedes contestar sin repetir el nombre.
    assert oyente._en_seguimiento() is True


def test_al_contestar_se_suelta_la_espera():
    oyente, _ = _oyente()
    oyente._awake = True
    oyente.esperar_respuesta(True)
    oyente.esperar_respuesta(False)
    assert oyente._esperando_algo() is False


def test_la_espera_tiene_tope():
    """Una pregunta sin contestar no puede dejar el micro abierto siempre."""
    oyente, _ = _oyente(espera_respuesta_s=0.0)
    oyente._awake = True
    oyente.esperar_respuesta(True)
    assert oyente._esperando_algo() is False


def test_dormirse_a_la_fuerza_cancela_la_espera():
    oyente, eventos = _oyente()
    oyente._awake = True
    oyente.esperar_respuesta(True)
    oyente.sleep_now("adios")
    assert oyente._esperando_algo() is False
    assert ("dormir", "adios") in eventos


# ── Ensordecer: que no te escuche hasta que tú digas ─────────────────

def test_ensordecer_la_duerme_y_lo_avisa():
    oyente, eventos = _oyente()
    oyente._awake = True
    oyente.ensordecer(True)
    assert oyente.sordo
    assert ("dormir", "sordo") in eventos


def test_volver_a_oir_no_la_despierta_sola():
    """Devolverle el oído no es lo mismo que llamarla: sigue haciendo
    falta decir su nombre."""
    oyente, _ = _oyente()
    oyente.ensordecer(True)
    oyente.ensordecer(False)
    assert not oyente.sordo
    assert not oyente.awake


def test_sorda_no_es_lo_mismo_que_muda_por_estar_hablando():
    """`_muted` lo pone ella mientras habla y dura un instante; `sordo`
    lo pones tú y dura hasta que lo quites."""
    oyente, _ = _oyente()
    oyente.mute()
    assert not oyente.sordo
    oyente.unmute()
    oyente.ensordecer(True)
    assert oyente.sordo

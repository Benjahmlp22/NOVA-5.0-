"""La máquina de estados de la voz, sin micrófono de por medio."""

from __future__ import annotations

import json

from nova.voice.listener import SAMPLE_RATE, VoiceListener
from nova.voice.speaker import limpiar_para_voz


class FakeRec:
    """Reconocedor de mentira: se le da el guion de lo que 'oye'."""

    def __init__(self, pasos: list[tuple[str, str]]) -> None:
        self.pasos = list(pasos)
        self._ultimo = ("silencio", "")
        self.reseteos = 0

    def AcceptWaveform(self, data):  # noqa: ANN001, N802
        self._ultimo = self.pasos.pop(0) if self.pasos else ("silencio", "")
        return self._ultimo[0] == "final"

    def Result(self):  # noqa: N802
        return json.dumps({"text": self._ultimo[1]})

    def PartialResult(self):  # noqa: N802
        texto = self._ultimo[1] if self._ultimo[0] == "parcial" else ""
        return json.dumps({"partial": texto})

    def Reset(self):  # noqa: N802
        self.reseteos += 1


def _listener(**kw):
    eventos = []
    oyente = VoiceListener(
        model_path="/no/existe",
        wake_word="nova",
        on_wake=lambda: eventos.append(("wake", "")),
        on_command=lambda t: eventos.append(("comando", t)),
        on_sleep=lambda m: eventos.append(("dormir", m)),
        on_partial=lambda t: eventos.append(("parcial", t)),
        **kw,
    )
    return oyente, eventos


def _dar(oyente, rec, n=1, data=b"\x00" * 100):
    for _ in range(n):
        oyente._feed(rec, data)


def test_ignora_conversacion_sin_wake_word():
    oyente, eventos = _listener()
    rec = FakeRec([("final", "hola que tal todo bien")])
    _dar(oyente, rec)
    assert eventos == []
    assert not oyente.awake


def test_despierta_con_el_parcial_sin_esperar_al_final():
    """Esperar al final de frase añadía ~1 s de retraso perceptible."""
    oyente, eventos = _listener()
    rec = FakeRec([("parcial", "oye nova")])
    _dar(oyente, rec)
    assert eventos == [("wake", "")]
    assert oyente.awake
    assert rec.reseteos == 1  # el wake no se cuela en el comando


def test_wake_y_comando_de_una_tacada():
    oyente, eventos = _listener()
    rec = FakeRec([("final", "nova abre chrome")])
    _dar(oyente, rec)
    assert eventos == [("wake", ""), ("comando", "abre chrome")]
    # Conversación continua: un comando no la vuelve a dormir.
    assert oyente.awake


def test_comando_despues_de_despertar():
    oyente, eventos = _listener()
    rec = FakeRec([("final", "nova"), ("final", "captura la pantalla")])
    _dar(oyente, rec, 2)
    assert ("comando", "captura la pantalla") in eventos
    assert oyente.awake


def test_conversacion_continua_sin_repetir_wake():
    """Tras un comando, NOVA sigue despierta: el siguiente turno no
    necesita repetir "nova"."""
    oyente, eventos = _listener()
    rec = FakeRec([
        ("final", "nova"),
        ("final", "abre chrome"),
        ("final", "y ahora captura la pantalla"),
    ])
    _dar(oyente, rec, 3)
    assert eventos.count(("wake", "")) == 1
    assert ("comando", "abre chrome") in eventos
    assert ("comando", "y ahora captura la pantalla") in eventos
    assert oyente.awake


def test_repetir_wake_estando_despierta_no_la_duerme():
    """Bug real visto en vivo: el usuario decía 'NOVA' para comprobar si
    le hacía caso ('¿NOVA? ¿me oyes?'), y al no traer comando detrás la
    dormía en silencio — así que la orden real que venía justo después
    caía en saco roto. Ahora se queda despierta y vuelve a avisar."""
    oyente, eventos = _listener()
    rec = FakeRec([
        ("final", "nova"),         # despierta
        ("final", "nova"),         # lo repite, sin nada detrás
        ("final", "abre chrome"),  # la orden de verdad
    ])
    _dar(oyente, rec, 3)
    assert eventos.count(("wake", "")) == 2
    assert not any(e[0] == "dormir" for e in eventos)
    assert ("comando", "abre chrome") in eventos
    assert oyente.awake


def test_no_va_se_reconoce_como_nova():
    """Vosk transcribe "nova" mal muy a menudo, casi siempre como "no va"
    (dos palabras). Sin esto, el wake word fallaba la mayoría de veces."""
    oyente, eventos = _listener()
    rec = FakeRec([("final", "no va abre chrome")])
    _dar(oyente, rec)
    assert eventos == [("wake", ""), ("comando", "abre chrome")]


def test_no_va_repetido_no_corrompe_comando_real():
    """El recorte de la mención repetida de "nova" en medio de un comando
    NO debe tocar un "no va" real dentro de una frase — solo pasa por el
    patrón estrecho, no el amplio de detección."""
    oyente, eventos = _listener()
    rec = FakeRec([
        ("final", "nova"),
        ("final", "dile a juan que no va a funcionar el mando"),
    ])
    _dar(oyente, rec, 2)
    assert ("comando", "dile a juan que no va a funcionar el mando") in eventos


def test_despedida_cierra_sin_mandar_comando():
    oyente, eventos = _listener()
    rec = FakeRec([("final", "nova"), ("final", "vale adios")])
    _dar(oyente, rec, 2)
    assert ("dormir", "despedida") in eventos
    assert not any(e[0] == "comando" for e in eventos)


def test_varias_despedidas_reconocidas():
    for frase in ["hasta luego", "eso es todo", "despues hablamos", "vete", "chao"]:
        oyente, eventos = _listener()
        rec = FakeRec([("final", "nova"), ("final", frase)])
        _dar(oyente, rec, 2)
        assert ("dormir", "despedida") in eventos, frase


def test_comando_normal_no_se_confunde_con_despedida():
    oyente, eventos = _listener()
    rec = FakeRec([("final", "nova"), ("final", "abre el bloc de notas")])
    _dar(oyente, rec, 2)
    assert ("comando", "abre el bloc de notas") in eventos


def test_silencio_prolongado_duerme():
    oyente, eventos = _listener(awake_timeout_s=1.0)
    rec = FakeRec([("final", "nova")] + [("silencio", "")] * 40)
    _dar(oyente, rec)
    assert oyente.awake
    # 1 s de audio a 16 kHz/16 bits = 32000 bytes.
    _dar(oyente, rec, 3, data=b"\x00" * (SAMPLE_RATE * 2))
    assert ("dormir", "silencio") in eventos
    assert not oyente.awake


def test_parciales_solo_cuando_esta_despierta():
    oyente, eventos = _listener()
    rec = FakeRec([("parcial", "hola"), ("final", "nova"), ("parcial", "abre chr")])
    _dar(oyente, rec)                      # parcial sin wake → nada
    assert eventos == []
    _dar(oyente, rec, 2)
    assert ("parcial", "abre chr") in eventos


def test_mute_ignora_audio():
    oyente, _ = _listener()
    oyente.mute()
    assert oyente._muted.is_set()
    oyente.unmute()
    assert not oyente._muted.is_set()


def test_limpiar_para_voz_quita_lo_que_se_lee_mal():
    sucio = "**Listo**, guardado en `C:\\Users\\Benja\\data\\x.png` — mira https://ejemplo.com"
    limpio = limpiar_para_voz(sucio)
    assert "**" not in limpio
    assert "C:\\" not in limpio
    assert "https" not in limpio
    assert "Listo" in limpio

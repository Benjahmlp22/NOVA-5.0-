"""Servicios que no son una app: son una web.

Netflix, YouTube o Gmail no tienen ejecutable en un PC normal.  Antes,
pedirle "abre Netflix" acababa en "no lo encuentro instalado", que es
verdad y no sirve de nada: lo que querías era verlo.

La lista es corta a propósito.  No pretende cubrir internet, sino las
cosas que uno pide por voz esperando que se abran, y donde decir "no lo
tienes" sería una respuesta tonta.
"""

from __future__ import annotations

WEBS: dict[str, tuple[str, str]] = {
    # clave normalizada: (nombre bonito, url)
    "netflix": ("Netflix", "https://www.netflix.com"),
    "youtube": ("YouTube", "https://www.youtube.com"),
    "twitch": ("Twitch", "https://www.twitch.tv"),
    "gmail": ("Gmail", "https://mail.google.com"),
    "correo": ("Gmail", "https://mail.google.com"),
    "drive": ("Google Drive", "https://drive.google.com"),
    "google drive": ("Google Drive", "https://drive.google.com"),
    "maps": ("Google Maps", "https://maps.google.com"),
    "google maps": ("Google Maps", "https://maps.google.com"),
    "chatgpt": ("ChatGPT", "https://chat.openai.com"),
    "wikipedia": ("Wikipedia", "https://es.wikipedia.org"),
    "amazon": ("Amazon", "https://www.amazon.es"),
    "twitter": ("X", "https://x.com"),
    "x": ("X", "https://x.com"),
    "instagram": ("Instagram", "https://www.instagram.com"),
    "tiktok": ("TikTok", "https://www.tiktok.com"),
    "reddit": ("Reddit", "https://www.reddit.com"),
    "github": ("GitHub", "https://github.com"),
    "hbo": ("HBO Max", "https://www.max.com"),
    "hbo max": ("HBO Max", "https://www.max.com"),
    "max": ("HBO Max", "https://www.max.com"),
    "disney": ("Disney+", "https://www.disneyplus.com"),
    "disney plus": ("Disney+", "https://www.disneyplus.com"),
    "prime video": ("Prime Video", "https://www.primevideo.com"),
    "spotify web": ("Spotify", "https://open.spotify.com"),
    "whatsapp web": ("WhatsApp Web", "https://web.whatsapp.com"),
    "outlook": ("Outlook", "https://outlook.live.com"),
    "aliexpress": ("AliExpress", "https://es.aliexpress.com"),
    "pccomponentes": ("PcComponentes", "https://www.pccomponentes.com"),
    "wallapop": ("Wallapop", "https://es.wallapop.com"),
}

# Servicios que TAMBIÉN existen como app. Si está instalada se abre la
# app; si no, la web. Estar en esta lista sólo añade el plan B.
TAMBIEN_APP = frozenset({"spotify", "whatsapp", "discord", "steam", "twitch", "netflix"})


def web_de(nombre: str) -> tuple[str, str] | None:
    """(nombre bonito, url) si eso se puede abrir en el navegador."""
    clave = (nombre or "").strip().lower()
    if clave in WEBS:
        return WEBS[clave]
    # "abre la web de netflix", "ponme netflix en el navegador"
    for palabra, destino in WEBS.items():
        if palabra in clave.split() or clave.endswith(f" {palabra}"):
            return destino
    return None

"""La suite de NOVA no necesita red; cualquier conexión real es un error."""
import socket

import pytest


@pytest.fixture(autouse=True)
def sin_red_real(monkeypatch):
    # Un proxy del entorno de CI no debe modificar clientes con transporte falso.
    for nombre in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(nombre, raising=False)

    def prohibida(*args, **kwargs):
        raise AssertionError("Este test intentó abrir una conexión real")

    monkeypatch.setattr(socket.socket, "connect", prohibida)

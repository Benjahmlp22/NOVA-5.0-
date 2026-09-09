"""Medir NOVA y Ollama por separado; sin inferir consumo de tamaños en disco.

python -m bench.medir_energia --pid 1234 --segundos 600 --salida profundo.csv
Requiere psutil. No guarda audio, ventanas, comandos ni nombres de usuario.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path

import psutil


def percentil(valores, proporcion):
    orden = sorted(valores)
    # Rango más próximo: con pocas muestras el p95 tampoco debe quedar
    # artificialmente por debajo de casi todas las lecturas.
    return orden[max(0, min(len(orden) - 1, math.ceil(len(orden) * proporcion) - 1))]


def medir(pid: int, segundos: float, salida: Path):
    raiz = psutil.Process(pid)
    raiz_creada = raiz.create_time()
    anterior = {}
    registros = []
    inicio = previo = time.perf_counter()
    logicos = psutil.cpu_count() or 1
    while time.perf_counter() - inicio <= segundos:
        ahora = time.perf_counter()
        intervalo = ahora - previo
        try:
            if raiz.create_time() != raiz_creada or not raiz.is_running():
                break
            nova = [raiz, *raiz.children(recursive=True)]
        except psutil.Error:
            break
        pids_nova = {p.pid for p in nova}
        ollama = []
        for proceso in psutil.process_iter(["name"]):
            if (proceso.info["name"] or "").lower().startswith("ollama") and proceso.pid not in pids_nova:
                ollama.append(proceso)
        actuales = {}
        for grupo, procesos in (("nova", nova), ("ollama", ollama)):
            cpu = rss = uss = denegados = 0
            for proceso in procesos:
                try:
                    clave = (proceso.pid, proceso.create_time())
                    tiempos = proceso.cpu_times()
                    acumulado = tiempos.user + tiempos.system
                    # Un trabajador que nace durante la ventana ya gastó CPU desde
                    # su creación; uno existente al iniciar el ensayo sólo se ceba.
                    base = anterior.get(clave, 0.0 if anterior else acumulado)
                    cpu += max(0.0, acumulado - base)
                    actuales[clave] = acumulado
                    rss += proceso.memory_info().rss
                    try:
                        uss += proceso.memory_full_info().uss
                    except (psutil.Error, AttributeError):
                        denegados += 1
                except psutil.Error:
                    denegados += 1
            if registros or anterior:
                registros.append({
                    "segundo": round(ahora - inicio, 3), "grupo": grupo,
                    "cpu_1_hilo_pct": round(cpu / intervalo * 100, 3),
                    "cpu_equipo_pct": round(cpu / intervalo / logicos * 100, 3),
                    "rss_mib": round(rss / 2 ** 20, 2),
                    "uss_mib": "" if denegados else round(uss / 2 ** 20, 2),
                    "procesos": len(procesos), "lecturas_incompletas": denegados,
                })
        anterior = actuales
        previo = ahora
        time.sleep(1.0)  # El observador es externo; no duerme el hilo del micrófono.
    if not registros:
        raise RuntimeError("No se recogieron muestras; comprueba el PID y la duración")
    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(registros[0]))
        escritor.writeheader()
        escritor.writerows(registros)
    resumen = {"hilos_logicos": logicos, "duracion_s": round(previo - inicio, 3),
               "advertencia": "RSS suma páginas compartidas; USS vacío significa falta de permisos. Procesos que nacen y mueren entre muestras pueden escapar al muestreo; validar con WPR/ETW si hace falta precisión por ráfaga."}
    for grupo in ("nova", "ollama"):
        filas = [r for r in registros if r["grupo"] == grupo]
        resumen[grupo] = {}
        for campo in ("cpu_equipo_pct", "cpu_1_hilo_pct", "rss_mib", "uss_mib"):
            valores = [r[campo] for r in filas if r[campo] != ""]
            if valores:
                resumen[grupo][campo] = {"media": statistics.mean(valores),
                    "p95": percentil(valores, .95), "maximo": max(valores)}
    salida.with_suffix(".json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--segundos", type=float, default=600)
    parser.add_argument("--salida", type=Path, required=True)
    args = parser.parse_args()
    if args.segundos < 2:
        parser.error("Mide al menos dos segundos; para reposo se proponen diez minutos")
    medir(args.pid, args.segundos, args.salida)


if __name__ == "__main__":
    main()

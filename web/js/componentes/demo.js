/* La demo de conversación: una charla escrita a máquina, en bucle.
 *
 * Es una GRABACIÓN, no NOVA funcionando. La página no ejecuta nada del
 * asistente: no hay micrófono, no hay modelo y no hay servidor. Por eso
 * el guion está aquí escrito a mano y por eso el HTML lo dice en un
 * `<figcaption>` — enseñar algo que parece un producto vivo cuando no
 * lo está es engañar, y el producto ni siquiera se puede descargar
 * todavía.
 *
 * Los turnos salen de casos reales del proyecto, con sus respuestas
 * tal cual las da NOVA.
 */

import { bus, TEMAS } from '../lib/bus.js';
import { vigilarVisibilidad } from '../lib/visible.js';

const GUION = [
  {
    quien: 'tú',
    texto: 'Nova, baja el volumen de Spotify.',
    estado: 'escucha',
  },
  {
    quien: 'nova',
    texto: 'Spotify al 30%.',
    accion: 'app.volume',
    estado: 'hablando',
  },
  {
    quien: 'tú',
    texto: '¿Cuánto ocupan mis descargas?',
    estado: 'escucha',
  },
  {
    quien: 'nova',
    texto: 'En Descargas tienes 627 archivos y 64 carpetas, 16,1 gigas en total. Sobre todo mp3, zip y exe.',
    accion: 'carpeta.resumen',
    estado: 'hablando',
  },
  {
    quien: 'tú',
    texto: 'Búscame la imagen de League of Legends que descargué.',
    estado: 'escucha',
  },
  {
    quien: 'nova',
    texto: 'Es Captura de pantalla 2026-02-17.png, en Screenshots. Tengo 2 más parecidas.',
    accion: 'imagen.buscar',
    estado: 'hablando',
  },
];

const MS_POR_LETRA = 22;
const PAUSA_ENTRE_TURNOS = 900;
const PAUSA_PENSANDO = 700;
const PAUSA_ANTES_DE_REPETIR = 3200;
const TURNOS_A_LA_VISTA = 4;

export function montarDemo(nodo) {
  const cuerpo = nodo.querySelector('.demo__cuerpo');
  const indicador = nodo.querySelector('.demo__estado');
  if (!cuerpo) return;

  const quieto = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let paso = 0;
  let temporizador = null;
  let soltarEspera = null;
  let corriendo = false;
  let cancelado = false;

  /* Una espera que se puede cancelar.
   *
   * El primer intento era `setTimeout(resolver, ms)` a secas y
   * `clearTimeout` para pararlo. No vale: `clearTimeout` no resuelve la
   * promesa, sólo impide que se resuelva NUNCA. El bucle se quedaba
   * colgado para siempre en ese `await`, con `corriendo` en true, y al
   * volver a mirar la demo ya no arrancaba: 0 turnos, para siempre.
   *
   * Guardando el `resolver` se le puede llamar a mano al parar, y
   * entonces el bucle continúa, ve `cancelado` y sale de verdad.
   */
  const esperar = (ms) =>
    new Promise((resolver) => {
      soltarEspera = resolver;
      temporizador = setTimeout(() => { soltarEspera = null; resolver(); }, ms);
    });

  function anunciar(estado) {
    if (indicador) {
      indicador.dataset.estado = estado;
      indicador.textContent = estado;
    }
    // El orbe escucha esto. La demo no sabe que el orbe existe.
    bus.emit(TEMAS.ESTADO, estado);
  }

  function crearTurno({ quien }) {
    const fila = document.createElement('div');
    fila.className = `demo__turno demo__turno--${quien === 'nova' ? 'nova' : 'usuario'}`;

    const etiqueta = document.createElement('span');
    etiqueta.className = 'demo__quien';
    etiqueta.textContent = quien;

    const texto = document.createElement('p');
    texto.className = 'demo__texto';

    fila.append(etiqueta, texto);
    cuerpo.append(fila);

    // Se tiran los turnos viejos en vez de dejarlos crecer sin fin: si
    // no, tras un rato con la página abierta la caja tendría cientos de
    // nodos que nadie va a leer.
    while (cuerpo.children.length > TURNOS_A_LA_VISTA) {
      cuerpo.firstElementChild.remove();
    }

    return { fila, texto };
  }

  async function escribir(destino, contenido) {
    if (quieto) { destino.textContent = contenido; return; }
    for (let i = 1; i <= contenido.length; i += 1) {
      if (cancelado) return;
      destino.textContent = contenido.slice(0, i);
      await esperar(MS_POR_LETRA);
    }
  }

  async function bucle() {
    if (corriendo) return;
    corriendo = true;

    while (!cancelado) {
      const turno = GUION[paso];
      const { fila, texto } = crearTurno(turno);

      if (turno.quien === 'nova') {
        anunciar('pensando');
        await esperar(PAUSA_PENSANDO);
        if (cancelado) break;
      } else {
        anunciar('escucha');
      }

      fila.classList.add('escribiendo');
      anunciar(turno.estado);
      await escribir(texto, turno.texto);
      fila.classList.remove('escribiendo');
      if (cancelado) break;

      if (turno.accion) {
        const accion = document.createElement('span');
        accion.className = 'demo__accion';
        accion.textContent = `· ${turno.accion}`;
        texto.after(accion);
      }

      paso = (paso + 1) % GUION.length;
      const ultimo = paso === 0;
      anunciar(ultimo ? 'dormida' : 'escucha');
      await esperar(ultimo ? PAUSA_ANTES_DE_REPETIR : PAUSA_ENTRE_TURNOS);

      if (ultimo) cuerpo.replaceChildren();
    }

    corriendo = false;
  }

  function parar() {
    cancelado = true;
    clearTimeout(temporizador);
    // Y se suelta la espera en curso, si la hay: si no, el bucle se
    // queda dormido en ese `await` y `corriendo` nunca vuelve a false.
    if (soltarEspera) {
      const soltar = soltarEspera;
      soltarEspera = null;
      soltar();
    }
  }

  /* No se escribe sola en una caja que nadie mira: gasta batería y,
   * peor, cuando el usuario llegue se encontrará la conversación por
   * la mitad en vez de por el principio. */
  const dejarDeVigilar = vigilarVisibilidad(nodo, (visible) => {
    if (visible && !corriendo) { cancelado = false; bucle(); }
    if (!visible) parar();
  });

  return () => { parar(); dejarDeVigilar(); };
}

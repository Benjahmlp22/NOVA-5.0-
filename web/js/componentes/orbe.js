/* El orbe: el mismo que ves en el escritorio cuando NOVA está activa.
 *
 * Cinco barras de ecualizador dentro de un círculo, y el color dice el
 * estado. Está copiado de `nova/ui/orb.py` a propósito, incluidas las
 * decisiones raras que allí están medidas:
 *
 *   - Verde puro para "hablando", no menta. El menta se quedaba a 40°
 *     de tono del azul de "escuchando" y a tamaño pequeño los dos se
 *     leían igual.
 *   - "Pensando" sí lleva animación inventada porque no hay audio que
 *     enseñar: es un latido, no una mentira.
 *
 * Escucha el bus, no a nadie en concreto: no sabe que existe la demo.
 */

import { bus, TEMAS } from '../lib/bus.js';
import { vigilarVisibilidad } from '../lib/visible.js';

const BARRAS = 5;

const COLORES = {
  dormida: '#5a5c62',
  escucha: '#4da3ff',
  pensando: '#f0f0f2',
  hablando: '#4cd964',
};

export function montarOrbe(nodo) {
  const lienzo = nodo.querySelector('.orbe__lienzo');
  if (!lienzo) return;

  const ctx = lienzo.getContext('2d', { alpha: true });
  const quieto = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let estado = 'escucha';
  let fase = 0;
  let animacion = null;
  let aLaVista = true;

  /* En pantallas densas hay que dibujar a más resolución de la que
   * ocupa, o se ve borroso. Se limita a 2: por encima no se distingue
   * y sí se nota el coste. */
  function ajustarTamano() {
    const densidad = Math.min(window.devicePixelRatio || 1, 2);
    const caja = lienzo.getBoundingClientRect();
    lienzo.width = Math.round(caja.width * densidad);
    lienzo.height = Math.round(caja.height * densidad);
    ctx.setTransform(densidad, 0, 0, densidad, 0, 0);
  }

  function pintar() {
    const ancho = lienzo.width / (Math.min(window.devicePixelRatio || 1, 2));
    const alto = lienzo.height / (Math.min(window.devicePixelRatio || 1, 2));
    const cx = ancho / 2;
    const cy = alto / 2;
    const radio = Math.min(cx, cy) - 6;
    const color = COLORES[estado] ?? COLORES.dormida;

    ctx.clearRect(0, 0, ancho, alto);

    // Halo, sólo cuando está activa.
    if (estado === 'escucha' || estado === 'hablando') {
      ctx.beginPath();
      ctx.arc(cx, cy, radio + 6, 0, Math.PI * 2);
      ctx.fillStyle = `${color}1f`;
      ctx.fill();
    }

    // Cuerpo oscuro: el fondo es negro, el orbe no puede ser un foco.
    ctx.beginPath();
    ctx.arc(cx, cy, radio, 0, Math.PI * 2);
    ctx.fillStyle = '#0c0c0e';
    ctx.fill();
    ctx.strokeStyle = `${color}70`;
    ctx.lineWidth = 1.4;
    ctx.stroke();

    // Las barras.
    const anchoBarra = radio * 0.13;
    const hueco = anchoBarra;
    const total = BARRAS * anchoBarra + (BARRAS - 1) * hueco;
    const x0 = cx - total / 2;

    ctx.fillStyle = color;
    for (let i = 0; i < BARRAS; i += 1) {
      let altura;
      if (estado === 'dormida') {
        altura = radio * 0.2 + (quieto ? 0 : radio * 0.06 * Math.sin(fase + i * 0.9));
      } else if (estado === 'pensando') {
        altura = radio * 0.26 + radio * 0.34 * Math.abs(Math.sin(fase + i * 0.55));
      } else {
        // Escuchando o hablando: onda con vida, más alta en el centro.
        const centro = 1 - Math.abs(i - (BARRAS - 1) / 2) / BARRAS;
        altura = radio * 0.2 + radio * 0.55 * centro * (0.55 + 0.45 * Math.sin(fase * 1.8 + i));
      }
      const x = x0 + i * (anchoBarra + hueco);
      const y = cy - altura / 2;
      ctx.beginPath();
      ctx.roundRect(x, y, anchoBarra, altura, anchoBarra / 2);
      ctx.fill();
    }
  }

  function bucle() {
    fase += 0.045;
    pintar();
    animacion = requestAnimationFrame(bucle);
  }

  function arrancar() {
    if (animacion !== null) return;
    if (quieto) { pintar(); return; }   // un fotograma y ya
    animacion = requestAnimationFrame(bucle);
  }

  function parar() {
    if (animacion === null) return;
    cancelAnimationFrame(animacion);
    animacion = null;
  }

  /* Dos motivos para parar, y los dos importan en un portátil:
   * que el orbe no esté a la vista, y que la pestaña esté de fondo. */
  const dejarDeVigilar = vigilarVisibilidad(nodo, (visible) => {
    aLaVista = visible;
    visible && !document.hidden ? arrancar() : parar();
  });

  function alCambiarPestana() {
    document.hidden || !aLaVista ? parar() : arrancar();
  }

  const dejarDeEscuchar = bus.on(TEMAS.ESTADO, (nuevo) => {
    estado = nuevo;
    if (quieto) pintar();
  });

  // `ResizeObserver` y no el evento `resize`: también salta cuando
  // cambia el tamaño por CSS sin que la ventana se mueva.
  const observadorTamano = new ResizeObserver(() => { ajustarTamano(); pintar(); });
  observadorTamano.observe(lienzo);

  document.addEventListener('visibilitychange', alCambiarPestana);

  ajustarTamano();
  arrancar();

  return () => {
    parar();
    dejarDeVigilar();
    dejarDeEscuchar();
    observadorTamano.disconnect();
    document.removeEventListener('visibilitychange', alCambiarPestana);
  };
}

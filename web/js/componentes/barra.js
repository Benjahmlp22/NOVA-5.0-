/* La barra de arriba: marca el borde al bajar y resalta la sección en
 * la que estás.
 *
 * Las dos cosas con IntersectionObserver y ninguna escuchando el
 * scroll. Un `scroll` con `getBoundingClientRect()` dentro fuerza al
 * navegador a recalcular la maquetación cientos de veces por segundo, y
 * es la causa habitual de que una página vaya a tirones justo mientras
 * la recorres.
 */

export function montarBarra(nodo) {
  /* ── El borde al despegarse de arriba ─────────────────────────
   * El truco es un centinela invisible en lo más alto del documento:
   * cuando deja de verse, es que ya no estamos arriba del todo. */
  const centinela = document.createElement('div');
  centinela.setAttribute('aria-hidden', 'true');
  centinela.style.cssText = 'position:absolute;top:0;height:1px;width:1px;';
  document.body.prepend(centinela);

  const observadorTope = new IntersectionObserver(
    ([entrada]) => nodo.classList.toggle('desplazada', !entrada.isIntersecting),
  );
  observadorTope.observe(centinela);

  /* ── Qué enlace va resaltado ─────────────────────────────────── */
  const enlaces = new Map();
  for (const enlace of nodo.querySelectorAll('.barra__enlace[href^="#"]')) {
    const seccion = document.querySelector(enlace.getAttribute('href'));
    if (seccion) enlaces.set(seccion, enlace);
  }

  const observadorSecciones = new IntersectionObserver(
    (entradas) => {
      for (const entrada of entradas) {
        const enlace = enlaces.get(entrada.target);
        if (!enlace) continue;
        if (entrada.isIntersecting) {
          enlaces.forEach((otro) => otro.removeAttribute('aria-current'));
          enlace.setAttribute('aria-current', 'true');
        }
      }
    },
    // Una banda estrecha por el centro de la pantalla: así la sección
    // "activa" es la que estás mirando, no la que asoma por abajo.
    { rootMargin: '-45% 0px -45% 0px' },
  );

  enlaces.forEach((_, seccion) => observadorSecciones.observe(seccion));

  return () => {
    observadorTope.disconnect();
    observadorSecciones.disconnect();
    centinela.remove();
  };
}

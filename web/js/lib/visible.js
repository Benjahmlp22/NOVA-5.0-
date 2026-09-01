/* Saber si algo está a la vista, sin escuchar el scroll.
 *
 * Un `addEventListener('scroll')` se dispara cientos de veces por
 * segundo y, si dentro se llama a `getBoundingClientRect()`, obliga al
 * navegador a recalcular la maquetación en cada una. Es la causa
 * clásica de que una página vaya a tirones justo al hacer scroll.
 *
 * `IntersectionObserver` hace lo mismo fuera del hilo principal y sólo
 * avisa cuando algo cruza el borde de verdad.
 */

/** Añade la clase `visible` la primera vez que entra, y deja de mirar. */
export function revelarAlEntrar(nodos, { margen = '0px 0px -12% 0px' } = {}) {
  const lista = [...nodos];
  if (!lista.length) return () => {};

  // Sin soporte, todo visible: mejor sin animación que invisible.
  if (!('IntersectionObserver' in window)) {
    lista.forEach((n) => n.classList.add('visible'));
    return () => {};
  }

  const observador = new IntersectionObserver(
    (entradas) => {
      for (const entrada of entradas) {
        if (!entrada.isIntersecting) continue;
        entrada.target.classList.add('visible');
        // Una vez revelado ya no interesa: dejar de observarlo ahorra
        // trabajo el resto de la sesión.
        observador.unobserve(entrada.target);
      }
    },
    { rootMargin: margen, threshold: 0.05 },
  );

  lista.forEach((n) => observador.observe(n));
  return () => observador.disconnect();
}

/**
 * Llama a `alCambiar(visible)` cada vez que el nodo entra o sale.
 *
 * Es lo que permite parar una animación de canvas cuando nadie la mira.
 * Un `requestAnimationFrame` corriendo fuera de pantalla gasta batería
 * y CPU para dibujar en un sitio que nadie ve.
 */
export function vigilarVisibilidad(nodo, alCambiar) {
  if (!('IntersectionObserver' in window)) {
    alCambiar(true);
    return () => {};
  }

  const observador = new IntersectionObserver(
    ([entrada]) => alCambiar(entrada.isIntersecting),
    { threshold: 0.01 },
  );

  observador.observe(nodo);
  return () => observador.disconnect();
}

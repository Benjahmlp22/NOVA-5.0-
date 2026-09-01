/* El bloque de descarga, que todavía no descarga nada.
 *
 * Patrón Estrategia: cada estado posible es una entrada de `ESTADOS`
 * con su texto y su comportamiento. Cuando haya una versión que bajar,
 * se cambia `ESTADO_ACTUAL` (o el `data-estado` del HTML) y ya está —
 * no hay que tocar la lógica ni buscar condicionales repartidos.
 *
 * Lo que NO hace, a propósito: el botón está `disabled` de verdad en el
 * HTML, no sólo pintado de gris. Un botón que parece pulsable y no
 * descarga nada es una mentira pequeña, y las mentiras pequeñas en una
 * página de descarga son las que hacen que la gente no se fíe de un
 * .exe que va a ejecutar con permisos.
 */

const ESTADOS = {
  'sin-version': {
    boton: 'Todavía no hay descarga',
    activo: false,
    aviso:
      'NOVA funciona, pero aún no hay un instalador. Está en obras y hay ' +
      'cosas por verificar en uso real antes de ponerla en manos de nadie.',
    porque:
      'Mientras tanto, el código está publicado y se puede correr desde ' +
      'las fuentes. Si quieres enterarte cuando salga la primera versión, ' +
      'vigila las releases en GitHub.',
  },

  /* Preparado para cuando toque. La página no necesita cambios de
   * lógica: sólo el data-estado y la URL. */
  disponible: {
    boton: 'Descargar para Windows',
    activo: true,
    aviso: 'Windows 10 y 11, 64 bits.',
    porque: 'Local y gratis. Ni cuenta, ni suscripción, ni datos saliendo del PC.',
  },
};

const POR_DEFECTO = 'sin-version';

export function montarDescarga(nodo) {
  const clave = nodo.dataset.estado || POR_DEFECTO;
  const estado = ESTADOS[clave] ?? ESTADOS[POR_DEFECTO];

  const boton = nodo.querySelector('[data-papel="boton"]');
  const aviso = nodo.querySelector('[data-papel="aviso"]');
  const porque = nodo.querySelector('[data-papel="porque"]');

  if (boton) {
    boton.textContent = estado.boton;
    boton.disabled = !estado.activo;
    // `aria-disabled` además del atributo: algunos lectores de pantalla
    // se saltan los botones deshabilitados, y aquí el texto ("todavía
    // no hay descarga") es justo lo que hay que oír.
    boton.setAttribute('aria-disabled', String(!estado.activo));

    if (estado.activo && estado.url) {
      boton.addEventListener('click', () => { window.location.href = estado.url; });
    }
  }

  if (aviso) aviso.textContent = estado.aviso;
  if (porque) porque.textContent = estado.porque;
}

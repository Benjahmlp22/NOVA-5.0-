/* Bus de eventos — patrón Observador.
 *
 * Sirve para que dos componentes se coordinen sin conocerse. Aquí lo
 * usan la demo de conversación y el orbe: cuando la demo cambia de
 * estado, el orbe cambia de color, y ninguno de los dos tiene una
 * referencia al otro.
 *
 * Sin esto, la demo tendría que ir a buscar el orbe por el DOM y
 * llamarle a un método. Entonces borrar el orbe del HTML rompería la
 * demo, que es exactamente lo que no queremos en una página que va a
 * seguir creciendo.
 */

const canales = new Map();

export const bus = {
  /** Escucha un tema. Devuelve la función para dejar de escuchar. */
  on(tema, escucha) {
    if (!canales.has(tema)) canales.set(tema, new Set());
    canales.get(tema).add(escucha);
    return () => canales.get(tema)?.delete(escucha);
  },

  /** Avisa a todo el que escuche ese tema. */
  emit(tema, dato) {
    const oyentes = canales.get(tema);
    if (!oyentes) return;
    // Sobre una copia: un oyente puede darse de baja dentro de su
    // propia llamada, y modificar el Set mientras se recorre se salta
    // al siguiente sin avisar.
    for (const escucha of [...oyentes]) {
      try {
        escucha(dato);
      } catch (error) {
        // Un componente roto no puede dejar mudos a los demás.
        console.error(`fallo escuchando "${tema}"`, error);
      }
    }
  },
};

/** Los temas, en un solo sitio: así una errata es un fallo y no un
 *  silencio raro que nadie encuentra. */
export const TEMAS = {
  ESTADO: 'nova:estado',   // 'dormida' | 'escucha' | 'pensando' | 'hablando'
};

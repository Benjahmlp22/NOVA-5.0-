/* Registro de componentes — patrón Registro + Factoría.
 *
 * El HTML dice QUÉ quiere con `data-componente="orbe"`, y aquí se
 * decide QUIÉN lo monta. Añadir un componente nuevo es registrarlo; no
 * hay que tocar el arranque ni buscar nada por `querySelector` suelto
 * repartido por el código.
 *
 * Y lo que no está en el HTML no se monta. Si mañana quitas la demo de
 * la página, su módulo simplemente no se ejecuta: no hay que acordarse
 * de ir a borrar la llamada.
 */

const fabricas = new Map();

/** Apunta cómo se monta un componente. */
export function registrar(nombre, montar) {
  fabricas.set(nombre, montar);
}

/**
 * Monta todo lo que el HTML pida dentro de `raiz`.
 * Devuelve las funciones de desmontaje que devuelvan los componentes.
 */
export function montarTodo(raiz = document) {
  const desmontar = [];

  for (const nodo of raiz.querySelectorAll('[data-componente]')) {
    const nombre = nodo.dataset.componente;
    const fabrica = fabricas.get(nombre);

    if (!fabrica) {
      console.warn(`no hay componente registrado con el nombre "${nombre}"`);
      continue;
    }

    try {
      const limpiar = fabrica(nodo);
      if (typeof limpiar === 'function') desmontar.push(limpiar);
    } catch (error) {
      // Uno que falle al montar no puede dejar la página a medias: los
      // demás se montan igual.
      console.error(`no pude montar "${nombre}"`, error);
    }
  }

  return () => desmontar.forEach((f) => f());
}

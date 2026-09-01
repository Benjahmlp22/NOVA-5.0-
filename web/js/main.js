/* Punto de entrada.
 *
 * Lo único que hace: decir qué componentes existen y mandar montarlos.
 * Toda la lógica vive en su módulo; aquí no hay ni un `querySelector`
 * suelto, y por eso este archivo no crece cuando la página crece.
 *
 * Va como `<script type="module" defer>`: no bloquea el pintado, y para
 * cuando se ejecuta el HTML ya está completo — no hace falta esperar a
 * `DOMContentLoaded`.
 */

import { montarTodo, registrar } from './lib/registro.js';
import { revelarAlEntrar } from './lib/visible.js';

import { montarBarra } from './componentes/barra.js';
import { montarDemo } from './componentes/demo.js';
import { montarDescarga } from './componentes/descarga.js';
import { montarOrbe } from './componentes/orbe.js';

registrar('barra', montarBarra);
registrar('orbe', montarOrbe);
registrar('demo', montarDemo);
registrar('descarga', montarDescarga);

montarTodo();
revelarAlEntrar(document.querySelectorAll('[data-revelar]'));

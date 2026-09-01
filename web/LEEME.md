# La web de NOVA

Página de presentación. Estática pura: no hay build, no hay
dependencias y no hay nada que instalar. Se abre con cualquier servidor
de archivos.

```bash
python -m http.server 8199 --directory web
```

No vale abrir `index.html` con doble clic: los módulos de JavaScript
necesitan `http://`, con `file://` el navegador los bloquea.

## Cómo está partido

```
index.html          sólo estructura: ni un estilo ni un script dentro
css/
  tokens.css        las variables. Los colores son los MISMOS que
                    nova/ui/panel.py, no unos parecidos
  base.css          reinicio, tipografía y las capas de grano y viñeta
  layout.css        rejillas y secciones: dónde van las cosas
  componentes.css   cómo se ve cada bloque
js/
  main.js           registra los componentes y manda montarlos. Nada más
  lib/
    bus.js          patrón Observador: la demo avisa del estado y el
                    orbe cambia de color sin que se conozcan
    registro.js     patrón Registro: el HTML pide con data-componente y
                    aquí se decide quién lo monta
    visible.js      IntersectionObserver, para no escuchar el scroll
  componentes/
    barra.js        borde al bajar y enlace resaltado
    orbe.js         el orbe del programa, en canvas
    demo.js         la conversación escrita a máquina
    descarga.js     patrón Estrategia: un estado, un comportamiento
```

## Añadir un componente

Tres pasos y ninguno toca los que ya están:

1. `js/componentes/loquesea.js` que exporte una función `montar(nodo)`.
   Si devuelve otra función, se usará para desmontarlo.
2. Registrarlo en `main.js`.
3. Poner `data-componente="loquesea"` en el HTML.

Lo que no esté en el HTML no se monta: si mañana quitas la demo de la
página, su módulo simplemente no se ejecuta.

## Cuando haya versión que descargar

En `js/componentes/descarga.js` está la estrategia `disponible`. Se le
añade la `url` y se cambia el `data-estado` del bloque en el HTML:

```html
<div class="descarga" data-componente="descarga" data-estado="disponible">
```

Nada más. No hay lógica que tocar.

Mientras tanto el botón está `disabled` de verdad, no sólo pintado de
gris. Un botón que parece pulsable y no descarga nada es una mentira
pequeña, y en una página de descarga son justo las que hacen que la
gente no se fíe del ejecutable.

## Lo que se hizo por rendimiento, y lo que costó comprobarlo

Son 51 KB en total, sin comprimir. Cero webfonts, cero imágenes, cero
dependencias.

**El orbe se para cuando no se ve.** Medido con el orbe fuera de
pantalla: **0 peticiones de `requestAnimationFrame` por segundo**. Una
animación de canvas corriendo fuera de la vista gasta batería para
dibujar donde nadie mira.

**Nada escucha el evento `scroll`.** Ni la barra ni los revelados. Un
`scroll` con `getBoundingClientRect()` dentro obliga al navegador a
recalcular la maquetación cientos de veces por segundo.

**El grano no es un `repeating-conic-gradient`.** Es lo que suele
recomendarse y no sirve: un cono repetido dibuja radios desde su centro,
así que a pantalla completa se veía un aspa en mitad de la página. Se
cambió por ruido fractal de SVG en un data URI, unos 230 bytes dentro
del propio CSS.

**Las secciones de abajo llevan `content-visibility: auto`**, para que
el navegador no maquete lo que aún no se ve.

## Probarla con el panel del navegador oculto

Aviso, porque cuesta una hora si no se sabe: con el panel oculto,
`document.hidden` es `true`, y entonces **ni corre `requestAnimationFrame`
ni disparan los `IntersectionObserver`**. El orbe sale en negro y la demo
no escribe — y las dos cosas son la página funcionando *bien*, porque es
exactamente lo que se le pidió.

Para probar la lógica en esas condiciones hay que sustituir
`window.IntersectionObserver` por uno falso que diga «visible», y leer el
DOM en vez de mirar capturas de pantalla.

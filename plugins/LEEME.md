# Escribir un plugin para NOVA

Un plugin es **una carpeta** con un `plugin.json` y, si hace falta, un
`plugin.py`. Se instala copiándola en `data/plugins/`. No hay build, ni
empaquetado, ni registro en ningún sitio.

## El más simple: sólo cambiar cómo habla

`data/plugins/pirata/plugin.json`

```json
{
  "id": "pirata",
  "nombre": "Pirata",
  "descripcion": "Habla como un bucanero. No sirve para nada y es lo mejor que tiene.",
  "version": "1.0.0",
  "autor": "tú",
  "permisos": ["personalidad", "frases"],
  "personalidad": "## Cómo hablas ahora\n- Como un pirata de novela, pero SIN pasarte: sigues diciendo una o dos frases.\n- Llamas «grumete» al usuario.",
  "frases": {
    "saludo": ["¡Arr!", "¿Qué se te ofrece, grumete?"],
    "hecho": ["Hecho está.", "A la orden."]
  }
}
```

Ya está. Abres el panel («abre los plugins»), le das a activar, y la
frase siguiente ya sale distinta — sin reiniciar NOVA.

Un plugin así **no puede hacerte daño**: son datos, no código.

## Lo que puedes poner en el manifiesto

| campo | para qué |
|---|---|
| `id` | único. Prefija las herramientas que traiga |
| `nombre`, `descripcion` | lo que se ve en el panel |
| `permisos` | **obligatorio declarar lo que hace** |
| `personalidad` | se añade al final del prompt de NOVA |
| `frases` | expresiones suyas, por momento |
| `voz` | qué voz pide (`"Microsoft Pablo"`) |

Los permisos que existen: `personalidad`, `voz`, `frases`,
`herramientas`, `archivos`, `red`, `pantalla`, `microfono`, `sistema`.

**La lista de permisos tiene que ser verdad.** Si cambias la
personalidad sin pedir ese permiso, NOVA se niega a activarlo: esa lista
es lo único que el usuario va a leer.

## Con código

Añade un `plugin.py` y pide el permiso `herramientas`:

```python
def cuanto_falta(para: str) -> dict:
    """Devuelve un dict. No hace falta importar nada de NOVA."""
    return {"ok": True, "message": f"Para {para} faltan tres días."}

HERRAMIENTAS = [
    {
        "name": "cuanto_falta",
        "description": "Cuántos días faltan para algo",
        "handler": cuanto_falta,
        "schema": {
            "type": "object",
            "properties": {"para": {"type": "string"}},
            "required": ["para"],
        },
        "responde_sola": True,
    },
]
```

El nombre se prefija solo con el `id`, así que ésa acaba llamándose
`pirata.cuanto_falta`. Dos plugins no pueden pisarse una herramienta ni
pisar una de NOVA.

`responde_sola` significa que tu `message` YA es la respuesta completa y
sale tal cual, sin que el modelo la reescriba. Ponlo si informas de
algo; no lo pongas si devuelves material en bruto que hay que resumir.

Si tu función revienta, NOVA lo dice y sigue: no tumba el turno.

Mira `plugins/programadora/` como ejemplo completo.

## Lo que deberías saber antes de publicar uno

Cuando alguien active tu plugin, **tu código corre con SUS permisos**.
NOVA no puede encerrarlo — Python no tiene forma de hacerlo.

Lo que sí hace, y lo verá quien lo instale:

- **Enseña tu código entero** en el panel antes de activar.
- **Avisa de lo que huele mal**: `eval`, `exec`, `subprocess`, `socket`,
  `shutil.rmtree`, `getattr` con nombre calculado…
- Marca en ámbar los permisos delicados.
- **Por voz no se activa.** Hay que abrir el panel y darle al botón.

Así que: importa lo mínimo, no uses `eval`, y si de verdad necesitas red
o tocar archivos, pide el permiso y explica en la `descripcion` para
qué. La gente lo va a leer.

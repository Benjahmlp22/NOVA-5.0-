## Qué cambia y por qué

<!-- El porqué, no el qué: el qué ya está en el diff. -->

## Qué se midió

<!-- Si tocas un default, una latencia o el comportamiento del modelo,
     aquí van los números. Antes y después.

     Dos avisos, que ya costaron tiempo:
     - No cronometres desde fuera: el ruido de la máquina es mayor que
       casi cualquier efecto.
     - Repite antes de concluir: el modelo va a temperatura 0.6.

     Si el cambio no necesita medida (documentación, un renombrado),
     ponlo y ya. -->

## Comprobado

- [ ] `ruff check .` limpio
- [ ] `pytest tests -q` en verde
- [ ] `bench\bench_herramientas.py` — sólo si tocaste herramientas o el prompt
- [ ] **Probado hablándole de verdad.** Los tests no oyen.

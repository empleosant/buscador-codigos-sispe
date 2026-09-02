# Nota de traspaso

Estado del proyecto y cómo seguir. Se actualiza al cerrar cada sesión.

Última actualización: 2 de septiembre de 2026.

---

## Lo primero: cómo se trabaja aquí

**`gh` está autenticado y funcionando, y hay Python en local.** Si alguna nota
anterior decía que no los hay y que había que subir los archivos por el editor
web de GitHub, era falso: seguir esa instrucción cuesta una sesión entera de
subidas a mano.

```bash
python3 evaluar.py     # aciertos: 40 consultas con su código correcto
python3 estres.py      # robustez: 8 comprobaciones del buscador
```

Ninguna de las dos gasta cuota de IA: sustituyen Streamlit y Gemini por
versiones de mentira y solo prueban la búsqueda local. Pásalas antes de subir
nada. Las mismas corren solas en GitHub con cada push (pestaña Actions, y un
tic verde o un aspa roja junto a cada commit).

`app.py` se despliega solo desde `main` en
<https://buscador-codigos-sispe.streamlit.app>. Las claves de la IA viven en
los Secrets de Streamlit Cloud, **no en el repositorio**: en local la app
arranca sin IA y solo responde el buscador del catálogo.

---

## Qué se hizo el 1 de septiembre

Nueve commits, todos desplegados y medidos.

- Dos arreglos de móvil que se creían subidos y no lo estaban.
- La desambiguación se caía: no era lentitud, era un `AttributeError` al leer
  la respuesta cuando el modelo devolvía la lista sin el objeto que la envuelve.
- El modelo degradado duraba una hora por un tropiezo de dos décimas. Ahora
  cinco minutos.
- El hallazgo de fondo: **`ESPERA_MAXIMA` nunca cortó nada**, porque el plazo
  de httpx mide silencio, no duración. El tope real de un intento lo pone
  ahora `PLAZO_INTENTO` con un reloj propio.

La peor consulta pasó de 30,6 s a 6,1 s.

## Qué se hizo el 2 de septiembre

Los tres pendientes que quedaban.

- **La lupa** se queda solo en el botón. En móvil salían dos seguidas, porque
  ahí el botón se pinta como icono al no caber la palabra "Buscar".
- **`primer_trozo` pasa a `respuesta_modelo`.** Desde que ningún proveedor usa
  streaming, la respuesta llega entera de una vez y medir "el primer trozo" de
  uno solo no significa nada. El rótulo se compara por subcadena y estaba
  escrito a mano en cuatro sitios; ahora es la constante `SUBTRAMO`.
- **Fusión de las dos llamadas**, apagada de fábrica. Ver abajo.

De paso quedaron dichas dos cosas que ya no eran verdad: la comprobación de
`ESPERA_TOTAL` **no corta nada hoy** (`entregas` vale siempre 1 desde que
Mistral perdió el streaming; se deja puesta porque es la guarda que impide
tirar una respuesta buena ya recibida), y el "0,5-0,6 s" que la cabecera daba
como tiempo actual era una medida de la época del streaming.

---

## Lo que está a medias: la fusión de las dos llamadas

### Qué es

Había dos viajes **secuenciales** al modelo. El paso 1 (`INTERPRETE`) traducía
la frase de la calle a vocabulario oficial, y con esa traducción se rebuscaba
en el catálogo para darle mejores candidatos al paso 2 (`INSTRUCCIONES`), que
es el que elige. El segundo no podía empezar hasta que contestaba el primero.

El paso 1 se justificaba en que el buscador literal no encontraba la ocupación
buena. Los números dicen otra cosa: `evaluar.py` mide la cobertura **sin IA**
—`busca()` sobre el texto tal y como se escribió— y el código correcto entra
en la lista de 24 candidatos en **40 casos de 40**. Si ya está dentro, el paso
1 no lo mete: lo reordena, y cobra una llamada entera por hacerlo.

La red para cuando los candidatos no sirven ya estaba puesta: `otros_terminos`
(regla 9), que hace lo mismo que el paso 1 pero solo cuando hace falta.

Consulta corriente: de dos llamadas a una. Difícil: de tres a dos.

### Cómo está ahora

`UNA_LLAMADA = False`. **Apagado.** Con el interruptor apagado el motor y los
dos prompts son idénticos letra a letra a los del 1 de septiembre, verificado
contra el commit `67de5c1`. Lo único que le llega hoy a quien usa la
herramienta a diario es la lupa.

El refuerzo del prompt que hace falta sin paso 1 va en `REFUERZO_UNA_LLAMADA`,
un anexo que **solo** se pega cuando el modo está encendido. Así lo apagado es
lo de siempre y lo encendido es la propuesta entera: la tanda responde la
pregunta que importa, que es si esto se queda o se vuelve atrás.

### Cómo se decide: la prueba masiva

Entra con `?mantenimiento=1` → Herramientas → Prueba masiva. Fija proveedor y
modelo en ajustes (si no, lanza en cascada y el resultado no es comparable).
Con las mismas consultas, lanza **dos tandas seguidas**:

| Tanda | Casilla "Una sola llamada al modelo" | Qué es |
|---|---|---|
| A | desmarcada | lo de siempre, tu línea de referencia |
| B | marcada | la propuesta |

Se pueden lanzar seguidas sin recargar: el modo entra en la clave del caché,
así que la segunda tanda **no** sale del caché de la primera. Cada fila del CSV
lleva la columna `una_llamada` con "sí" o "no".

Qué mirar, en este orden:

1. **Acierto.** Cuántas veces `elegido_1` es el código correcto. Los segundos
   no valen nada si el acierto baja.
2. **Segundos.** Ahí debería verse el ahorro de la llamada que sobra.
3. **La columna `relevos`.** Responde la *otra* pregunta pendiente, la de
   `PLAZO_INTENTO = 4`: si empiezan a salir `TimeoutError` con el modelo bueno
   respondiendo bien justo después, 4 s se quedó corto y hay que subirlo a 5
   o 6.

**Cómo decidir.** Si el acierto de B empata o mejora, `UNA_LLAMADA = True` y
sale para todos. Si baja, se queda en `False` y no se ha perdido nada: el paso
1 sigue entero.

### Dónde sospechar si baja el acierto

`INTERPRETE` devolvía 2-3 lecturas distintas, cada una con su **grupo CNO**, y
fundía las búsquedas con pesos. `otros_terminos` hace **una sola búsqueda, sin
filtro de grupo**. En el prompt se piden explícitamente términos de las dos
funciones cuando la descripción mezcla dos ("cobro en caja y repongo"), pero no
es lo mismo que dos búsquedas ponderadas. Mira primero esas consultas.

---

## Comprobado el 2 de septiembre con el navegador

Sobre la app levantada en local, en escritorio (1280×900) y móvil (390×844):

- El campo de búsqueda ya no lleva lupa; el botón sí en móvil (texto a `0px` y
  el icono en `::after`) y conserva la palabra "Buscar" en escritorio.
- **Una sola lupa** en la cabecera del móvil, en un renglón y sin desborde
  lateral.
- La casilla "Una sola llamada al modelo (de fábrica, no)" aparece desmarcada,
  y al marcarla el aviso de cuota pasa de "dos o tres" a "una o dos" llamadas
  y el panel avisa de "Ajustes fuera de fábrica: una sola llamada".

**Lo que no se pudo comprobar en local, y por qué:** sin claves de API no se
puede ejecutar el circuito con IA, así que el pintado de las tarjetas de
resultado, el panel de tiempos y el CSV descargado quedan sin verificar por esa
vía. Se comprobó que la app de hoy y la del 1 de septiembre (`67de5c1`) se
comportan **igual** en ese entorno, así que no hay regresión detectable ahí.
El circuito completo solo se puede medir desplegado.

Sin `secrets.toml`, `_credenciales()` (`app.py`) levanta
`StreamlitSecretNotFoundError` en vez de seguir sin credenciales, a diferencia
de la lectura de claves de IA, que sí lo protege. En Streamlit Cloud no salta
porque allí hay Secrets. Para probar en local basta un `.streamlit/secrets.toml`
vacío, que ya está en `.gitignore`.

---

## En mantenimiento no se aprende

Un refuerzo se guarda cada vez que el modelo elige un primero distinto del que
ponía el catálogo, y `busca()` lo lee **para todo el mundo**, sumando 14 puntos
por palabra coincidente. Es de los pesos que mueven el orden de resultados.

En uso normal eso es lo que se quiere y no ha cambiado. Pero en mantenimiento
se trastea a propósito —modelos que no son el de producción, modos de llamada,
consultas raras— y lo que un modelo pequeño elija mal ahí no puede acabar en el
vocabulario de la oficina. Desde el 2 de septiembre, ni las búsquedas sueltas ni
la prueba masiva escriben en el Gist compartido cuando se entra con
`?mantenimiento=1`.

El corte está en el punto de **escritura**, no donde se generan los refuerzos,
para que dé igual cuántos caminos los produzcan.

**Para enseñarle algo a la herramienta, hazlo desde el buscador normal.**

---

## Lo siguiente

1. **La prueba masiva.** Decide la fusión y, de paso, si `PLAZO_INTENTO` se
   queda en 4 s o sube a 5 o 6.
2. Según el resultado, `UNA_LLAMADA = True` o se queda en `False`.

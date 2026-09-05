# Nota de traspaso

Estado del proyecto y cómo seguir. Se actualiza al cerrar cada sesión.

Última actualización: 5 de septiembre de 2026.

---

## Lo primero: cómo se trabaja aquí

**`gh` está autenticado y funcionando, y hay Python en local.** Si alguna nota
anterior decía que no los hay y que había que subir los archivos por el editor
web de GitHub, era falso: seguir esa instrucción cuesta una sesión entera de
subidas a mano.

```bash
python3 evaluar.py     # aciertos: 40 consultas con su código correcto
python3 estres.py      # robustez: 10 comprobaciones del buscador
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

## Qué se hizo el 5 de septiembre

La queja: "a veces va lenta, otras engancha rapidísimo, sobre todo con
Gemini". Antes de tocar nada se lanzó la prueba masiva **en cascada**, que es
lo que ve la oficina (no con proveedor fijado, que es lo que se hace para
comparar modos). 40 casos, una llamada, pausa de 12 s. El CSV es
`tanda_A_2026-09-05_cascada_antes.csv`, entregado en la conversación.

| | Tanda A · 05/09 · cascada · antes del cambio |
|---|---|
| Acierto | 37 de 40 (92 %) |
| Media / mediana (36 con IA) | 3,9 s / 2,0 s |
| p90 / máximo | 10,5 s / 13,7 s |
| Consultas de más de 5 s | 8 (7 de más de 8 s) |
| Relevos | 10, **todos `TimeoutError` a los 5,0 s**, ninguno por error |
| Sin afinar o con error | 0 |

Lo que dice el CSV, y es lo que explica la queja:

- **Gemini sano es rápido y estable**: gemini-3.5-flash-lite contestó en
  1,0-1,4 s en 14 de sus 17 llamadas sanas; gemini-3.1-flash-lite en
  1,3-2,6 s en 14 de 14.
- **Pero una de cada cinco peticiones se queda colgada** y no vuelve en 5 s.
  No es lentitud del modelo: el reintento que sale detrás vuelve en el
  segundo de siempre. Es esa petición concreta, atascada en algún servidor.
- **La cascada convierte un colgado en 6-13 s de espera**: 5 s muertos más
  el relevo, y si el segundo modelo también se cuelga (pasó dos veces),
  otros 5 más Mistral.
- **El castigo de 5 minutos empeora la racha**: un solo timeout de 3.5 en la
  consulta 14 dejó las 16 siguientes en 3.1-flash-lite, que en esta tanda se
  colgó más (8 timeouts de ~24 llamadas frente a 2 de ~19 en 3.5). Está
  medido, no arreglado: ver pendientes.

**El cambio: petición de respaldo.** `_con_plazo` ya no espera el plazo
entero para rendirse. A los `PLAZO_RESPALDO` segundos (2,5) sin respuesta
lanza una segunda petición idéntica en paralelo, sin tirar la primera, y
entrega la que vuelva antes. Un error que llega antes de los 2,5 s (429, 400)
se releva como siempre y no lanza nada. `PLAZO_INTENTO` sube de 5 a 6 para
que al respaldo le queden 3,5 s, no 2,5. Cada respaldo queda en la columna
`relevos` como `modelo:2.5s:Respaldo`, así que en la siguiente tanda se ve
cuántas veces hace falta y cuántas llamadas cuesta (previsto: una de cada
cuatro o cinco consultas, que son justo las que hoy tardan 6 s o más).

Lo cubre una prueba nueva en `estres.py` ("El respaldo no pisa una respuesta
buena"): seis situaciones con llamadas de mentira, sin tocar la IA. Las dos
baterías pasan (39 de 40 con el pendiente de siempre; 10 de 10).

**Lo que falta medir**, y es lo primero al abrir la próxima sesión: la tanda
B, con el cambio desplegado, con los mismos ajustes que la A (cascada, una
llamada, 40 casos, pausa 12 s). Compara acierto (no debe moverse: el modelo y
el prompt son los mismos), media, p90, y en `relevos` cuántos `Respaldo` y
cuántos `TimeoutError` quedan. Si la media no baja de 3,9 s hacia 2-2,5 s, el
respaldo no está haciendo lo que dice y hay que mirar si los colgados son
por petición (lo que se supone) o por modelo (entonces el respaldo debería
ir al modelo siguiente, no al mismo).

Tres fallos de acierto de la tanda A, por si merecen `casos.csv` o no:
"lleva las facturas y las nóminas" salió 41121012 (personal) con el correcto
en segunda posición; "auxiliar administrativa facturación" salió 43091029 con
el correcto segundo; "teleoperadora de atención al cliente" salió 44241016
TELEOPERADORES, que es discutible que esté mal. Decisión de Álvaro.

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

### Resultado de la tanda (2 de septiembre)

Las 40 consultas de `casos.csv`, Gemini fijado en `gemini-3.5-flash-lite`, una
tanda tras otra, sin refuerzos del Gist en las dos.

| | A · dos llamadas | B · una llamada |
|---|---|---|
| Acierto | 33 de 40 (82 %) | **34 de 40 (85 %)** |
| Media | 2,2 s | **1,7 s** |
| Mediana | 2,2 s | **1,4 s** |
| Timeouts | 1 | 2 |
| Amplían la búsqueda | 1 | 5 |
| Piden aclaración | 11 | 7 |

Solo cinco consultas cambian. B acierta tres que A falla, las tres por elección
(pladur, organizar eventos, cuidadora en residencia). B falla dos que A acierta,
las dos por `TimeoutError` a los 4,0 s con el proveedor fijado, no por elegir
mal. En elección pura, 36 contra 33.

**Decisión: `UNA_LLAMADA = True` y `PLAZO_INTENTO = 5`**, desde el 2 de
septiembre. Los CSV de las dos tandas están entregados en la conversación de ese
día.

### Cómo estaba antes

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

## ESPERA_MAXIMA no puede bajar de 10: Google rechaza plazos menores

Descubierto el 2 de septiembre lanzando la prueba masiva con clave real desde
fuera de Streamlit Cloud. Con `ESPERA_MAXIMA = 8`, **todas** las llamadas a
Gemini fallaban con `ClientError` en medio segundo:

```
400 INVALID_ARGUMENT. Manually set deadline 8s is too short.
Minimum allowed deadline is 10s.
```

`google-genai` 2.21 manda el plazo de `HttpOptions` también al servidor, como
cabecero `X-Server-Timeout`, y Google no admite menos de 10 s. Con un SDK más
antiguo no se notaba: por eso la app desplegada seguía funcionando. Pero
`requirements.txt` pide `google-genai>=2.20` sin fijar versión, así que el
primer reinicio que reinstalara dependencias habría dejado a toda la oficina
en Mistral sin aviso.

Está en 10 desde `8612ed9`. **No lo bajes.** El corte real de un intento lo
sigue haciendo `PLAZO_INTENTO` con reloj propio, por debajo de esto.

Mejora pendiente, a decidir: fijar la versión del SDK en `requirements.txt`
(`google-genai==2.21.0`) para que Cloud instale siempre lo mismo que se probó.

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

1. ~~La prueba masiva.~~ Hecha el 2 de septiembre: una llamada encendida y
   `PLAZO_INTENTO` en 5.
2. ~~Vigilar la columna `relevos`.~~ Vigilada el 5 de septiembre: 10
   `TimeoutError` en 40 consultas con el modelo bueno respondiendo justo
   después. Respuesta: el respaldo (arriba), no subir el plazo a secas.
3. **Tanda B** con el respaldo desplegado, mismos ajustes que la A. Es lo
   que dice si el cambio se queda.
4. **El castigo de 5 minutos por un timeout.** Con el respaldo, que una
   pareja entera se cuelgue es raro, así que degradar ahí puede tener
   sentido. Pero medido el 05/09, 3.1-flash-lite se colgó más que 3.5, y
   quedarse 5 minutos en él por un timeout suelto de 3.5 empeoró la racha.
   Opciones, a decidir con la tanda B en la mano: no degradar por
   `TimeoutError` (solo por errores que responde Google), o acortar
   `CADUCIDAD_DEGRADACION` para los timeouts.
5. **`PAUSAS_429 = (4, 10, 20)`** duerme hasta 34 s con el mismo modelo ante
   un tope por minuto antes de relevar. Hoy no saltó ninguno, pero el día que
   la oficina pase de 15 peticiones por minuto será la siguiente "lentitud".
   Con cupo por modelo, lo barato es pasar al modelo siguiente al primer 429
   y dormir solo cuando no quede ninguno.
6. Rotar la clave de Gemini que se usó para la tanda del 2 de septiembre
   (quedó en el chat).

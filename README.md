# Codificador de ocupaciones

Herramienta de apoyo para localizar códigos del catálogo oficial de
ocupaciones antes de grabarlos.

## Archivos

```
app.py                            el motor. No contiene vocabulario.
vocabulario.json                  palabras vacías y sinónimos base
ocupaciones_sispe_ultraligero.txt catálogo oficial (nombre exacto)
terminos_ampliados.txt            jerga por ocupación (lo genera enriquecer.py)
requirements.txt                  dependencias
.streamlit/config.toml            colores del tema
enriquecer.py                     genera terminos_ampliados.txt (no lo usa la app)
motor_pruebas.py                  carga app.py sin la interfaz (lo usan las pruebas)
evaluar.py                        aciertos: 40 consultas con su código correcto
casos.csv                         los 40 casos (referencia, se edita a mano)
informe_evaluacion.csv            salida de --informe, regenerable, no versionado
estres.py                         robustez: que nada se rompa por lo bajo
TRASPASO.md                       estado del proyecto y qué toca ahora
scripts/despertar.py              despertador (no lo usa la app)
.github/workflows/…               programa el despertador
```

## Las cuatro capas de vocabulario

De más estable a más viva. Ninguna se toca desde `app.py`.

| Capa | Dónde | Quién la mantiene |
|---|---|---|
| Palabras vacías y sinónimos base | `vocabulario.json` | tú, a mano |
| Jerga de cada ocupación | `terminos_ampliados.txt` | `enriquecer.py`, una vez |
| Jerga traducida sobre la marcha | `lexico.json` (Gist) | la IA, sola |
| Correcciones de orden | `refuerzos.json` (Gist) | el uso diario |

## Secrets de Streamlit

```toml
GEMINI_API_KEY = "..."
MISTRAL_API_KEY = "..."     # opcional: segundo escalón de la cascada
OPENROUTER_API_KEY = "..."  # opcional: tercer escalón (sin probar aún)
GIST_ID = "..."        # opcional: activa el aprendizaje compartido
GITHUB_TOKEN = "..."   # token classic, solo con permiso "gist"
```

## Cascada de proveedores de IA

La constante `ORDEN` de `app.py` fija el orden: `gemini → mistral →
openrouter`. Se recorre de izquierda a derecha, se salta el que no tenga
clave en los Secrets, y si ninguno responde se enseña la búsqueda local sin
afinar. Un proveedor solo se aparta durante la sesión si el error dice
expresamente que su cupo del DÍA está agotado; un tope por minuto no aparta
ni degrada nada: esa consulta pasa al siguiente y la próxima vuelve a
intentarlo. Orden decidido con la tanda del 26/08/2026: con los 40 casos,
Gemini acierta el 84 % en primera posición y Mistral (ministral-3b) el 65 %,
pero Mistral respondió las 40 sin un fallo y con máximo de 4,6 s.

**Plazos y respaldo.** Cada intento contra un modelo tiene `PLAZO_INTENTO`
segundos (6) de reloj propio. Si a los `PLAZO_RESPALDO` segundos (2,5) no ha
contestado, sale una segunda petición idéntica en paralelo y se entrega la
que vuelva antes; la primera no se cancela porque no se puede. Medido el
05/09/2026: Gemini sano contesta en 1,0-1,4 s, pero una de cada cinco
peticiones se queda colgada y no vuelve nunca, y esperar el plazo entero para
descubrirlo costaba de 5 a 13 s por consulta. Cada respaldo queda anotado en
la columna `relevos` como `modelo:2.5s:Respaldo`. Un error que llega antes
del respaldo (cupo, petición mal formada) no lanza nada: se releva como
siempre.

## Modo mantenimiento

Añade `?mantenimiento=1` a la dirección para ver correcciones manuales,
diccionarios aprendidos y diagnóstico. Sin ese parámetro la herramienta se ve
limpia.

## Dónde se toca cada cosa

- **Modelo de IA**: bloque `PROVEEDORES`. Es una lista con relevo automático.
- **Orden de la cascada**: constante `ORDEN` (`gemini`, `mistral`, `openrouter`).
- **Vocabulario**: `vocabulario.json`. Si falta, la app arranca en modo mínimo.
- **Colores**: `TOKENS_CLARO` y `TOKENS_OSCURO` en `app.py`. Los interpolan las
  dos hojas de estilo (la de la página y la del marco de tarjetas), así que un
  color se cambia en un sitio y vale en los dos. `.streamlit/config.toml` sigue
  aparte porque Streamlit lo lee antes de que `app.py` exista: es el único
  duplicado que queda.
- **Movimiento**: `MOVIMIENTO`. Dos curvas (`--entrada` para lo que aparece,
  `--salida` para lo que responde a un gesto), tres duraciones, y `--paso`, el
  retraso entre tarjeta y tarjeta de la entrada escalonada. Con seis tarjetas el
  último retraso es de 225 ms; por encima empieza a notarse como lentitud.

## Las dos baterías de pruebas

Antes de subir cualquier cambio en `vocabulario.json` o en el buscador, las dos.
Ninguna llama a la IA ni gasta cuota; entre las dos tardan unos segundos.

```
python evaluar.py && python estres.py
```

**`evaluar.py` — aciertos.** Pasa los 40 casos de `casos.csv`. Dice si el
código correcto sale donde debe.

- `--detalle` enseña los tres primeros de cada caso.
- `--informe` vuelca a `informe_evaluacion.csv` lo que devuelve el motor:
  esperado, estado, en qué posición salió el código correcto y los tres
  primeros resultados. No toca `casos.csv`.

`casos.csv` es la referencia y **solo se edita a mano**. Existió un flag
`--actualizar` que la reescribía con la salida del propio motor: eso convertía
en «correcto» lo que el buscador contestara ese día, de modo que una regresión
quedaba consagrada como verdad en la siguiente pasada. Ya no existe; si alguien
lo usa, el script aborta y explica por qué.

**Cada consulta real que falle debería acabar en `casos.csv`**, con el código
comprobado contra el catálogo oficial o contra un caso ya grabado,
nunca copiado de lo que contesta el motor. La columna `tope` admite 1 (tiene que
salir el primero) o 3 (basta con que esté entre los tres primeros).

**`estres.py` — robustez.** No afirma qué código es correcto: comprueba que el
buscador se comporta con sensatez pase lo que pase. Diez pruebas: que no
reviente con basura, que ninguna respuesta del modelo tumbe la app, que dé
igual escribir con acentos o sin ellos, que el singular encuentre lo que el
catálogo guarda en plural, que cada ocupación se encuentre a sí misma, que
ningún código salga inventado, que dos consultas iguales den lo mismo, que la
segunda mitad de una consulta coordinada cuente, que el respaldo de una
llamada colgada se comporte como promete (con llamadas de mentira, sin tocar
la IA) y que la búsqueda siga siendo rápida.

- `--detalle` enseña cada caso que falla.
- `--rapido` salta las dos pruebas que recorren el catálogo entero.

Hace falta porque `evaluar.py` no lo ve todo. El 21/08/2026 un cambio en el
lematizador dejó 216 ocupaciones inalcanzables desde el singular y `evaluar.py`
solo detectó dos casos raros. La prueba de convergencia lo canta entero.

### La marca de corte

Las dos baterías cargan `app.py` hasta la línea `# === FIN DEL MOTOR ===` para
probar el buscador sin dibujar pantalla. **No borres esa línea ni la muevas.**
Si desaparece, `motor_pruebas.py` avisa por pantalla en vez de fallar en
silencio.

## Garantía sobre los datos

Ninguna denominación procede del modelo: se toma del catálogo a partir del
código. Los códigos inexistentes se descartan antes de mostrarse.

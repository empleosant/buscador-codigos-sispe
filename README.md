# Codificador de ocupaciones SISPE

Herramienta de apoyo para localizar códigos del catálogo SISPE antes de
grabarlos en SilcoiWeb.

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
GIST_ID = "..."        # opcional: activa el aprendizaje compartido
GITHUB_TOKEN = "..."   # token classic, solo con permiso "gist"
```

## Modo mantenimiento

Añade `?mantenimiento=1` a la dirección para ver correcciones manuales,
diccionarios aprendidos y diagnóstico. Sin ese parámetro la herramienta se ve
limpia.

## Dónde se toca cada cosa

- **Modelo de IA**: bloque `PROVEEDORES`. Es una lista con relevo automático.
- **Proveedor**: constante `PROVEEDOR` (`gemini`, `groq`, `mistral`).
- **Vocabulario**: `vocabulario.json`. Si falta, la app arranca en modo mínimo.

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
comprobado contra el catálogo oficial o contra un caso ya grabado en SilcoiWeb,
nunca copiado de lo que contesta el motor. La columna `tope` admite 1 (tiene que
salir el primero) o 3 (basta con que esté entre los tres primeros).

**`estres.py` — robustez.** No afirma qué código es correcto: comprueba que el
buscador se comporta con sensatez pase lo que pase. Ocho pruebas: que no
reviente con basura, que dé igual escribir con acentos o sin ellos, que el
singular encuentre lo que el catálogo guarda en plural, que cada ocupación se
encuentre a sí misma, que ningún código salga inventado, que dos consultas
iguales den lo mismo, que la segunda mitad de una consulta coordinada cuente y
que la búsqueda siga siendo rápida.

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

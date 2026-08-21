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

## Garantía sobre los datos

Ninguna denominación procede del modelo: se toma del catálogo a partir del
código. Los códigos inexistentes se descartan antes de mostrarse.

# Codificador de ocupaciones SISPE

Herramienta de apoyo para localizar códigos de ocupación del catálogo SISPE
antes de grabarlos en SilcoiWeb.

## Archivos del repositorio

```
app.py
requirements.txt
ocupaciones_sispe_ultraligero.txt   <- el catálogo, mismo nombre exacto
```

## Puesta en marcha en Streamlit Cloud

1. Sube los tres archivos al repositorio de GitHub.
2. En Streamlit Cloud: **Settings → Secrets** y añade:

```toml
GEMINI_API_KEY = "tu_clave"
```

3. Reinicia la app (**Reboot**).

## Qué cambió respecto a la versión anterior

| Cambio | Efecto |
|---|---|
| Índice invertido en lugar de recorrer el catálogo entero | prefiltro de ~800 ms a ~2 ms |
| `thinking_level="minimal"` | Gemini 3.x razona por defecto; es la mayor fuente de espera |
| Respuesta en streaming | el texto aparece según se genera |
| 15 candidatos en lugar de 25 | menos tokens de entrada, menos latencia |
| Cliente y catálogo cacheados | no se reconstruyen en cada interacción |
| Modo sin IA | resultados del catálogo al instante, sin consumo de API |
| Validación de códigos | avisa si el modelo devuelve un código inexistente |
| Consulta directa por código de 8 cifras | respuesta local, sin llamada a la API |

## Mantenimiento

El diccionario `SINONIMOS` en `app.py` traduce lenguaje coloquial y nombres de
plataformas al vocabulario del catálogo. Se amplía añadiendo una línea:

```python
"palabra_que_dice_la_persona": "términos que aparecen en el catálogo",
```

No hace falta tocar nada más: el índice se reconstruye al arrancar.

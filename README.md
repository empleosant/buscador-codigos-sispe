# Codificador de ocupaciones SISPE

Herramienta de apoyo para localizar códigos de ocupación del catálogo SISPE
antes de grabarlos en SilcoiWeb.

## Archivos del repositorio

```
app.py
requirements.txt
ocupaciones_sispe_ultraligero.txt   <- el catálogo, con ese nombre exacto
```

## Puesta en marcha en Streamlit Cloud

1. Sube los tres archivos al repositorio de GitHub.
2. En Streamlit Cloud: **Settings → Secrets** y añade:

```toml
GEMINI_API_KEY = "tu_clave"
```

3. Reinicia la app (**Reboot**).

## Cómo se usa

- Escribe el puesto o las funciones y pulsa Enter.
- Escribe un código de 8 cifras para consultar su denominación oficial.
- El bloque **Copiar códigos** tiene un botón de copia para pegar en SilcoiWeb.
- En **Ajustes** puedes desactivar la IA (resultados instantáneos del catálogo),
  descargar las consultas de la sesión en CSV y empezar de nuevo.

## Mantenimiento

El diccionario `SINONIMOS` traduce lenguaje coloquial y nombres de plataformas
al vocabulario del catálogo. Se amplía añadiendo una línea:

```python
"palabra_que_dice_la_persona": "términos que aparecen en el catálogo",
```

Las claves y los valores van **sin acentos y en minúscula**: el buscador
normaliza el texto antes de comparar. No hace falta tocar nada más.

## Garantía sobre los datos

Ninguna denominación procede del modelo: se toma siempre del catálogo a partir
del código. Si el modelo devolviera un código inexistente, se descarta antes de
mostrarlo.

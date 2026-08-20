# Codificador de ocupaciones SISPE

Herramienta de apoyo para localizar códigos de ocupación del catálogo SISPE
antes de grabarlos en SilcoiWeb.

## Archivos del repositorio

```
app.py                            la aplicación
requirements.txt                  dependencias
ocupaciones_sispe_ultraligero.txt catálogo oficial (nombre exacto)
terminos_ampliados.txt            vocabulario coloquial por ocupación (opcional)
enriquecer.py                     genera el archivo anterior (no lo usa la app)
```

## Puesta en marcha en Streamlit Cloud

En **Settings → Secrets**:

```toml
GEMINI_API_KEY = "tu_clave"
```

## Cómo se usa

- Escribe el puesto o las funciones y pulsa Enter.
- Escribe un código de 8 cifras para consultar su denominación oficial.
- En **Ajustes**: apagar la IA, probar la conexión, descargar la sesión en CSV
  y ver los términos que la app ha aprendido.

## Dónde se toca cada cosa

- **Modelo de IA**: bloque `PROVEEDORES`, arriba del archivo. Es una lista: si
  uno agota cuota o desaparece, la app pasa al siguiente sola.
- **Proveedor**: la constante `PROVEEDOR` admite `gemini`, `groq` o `mistral`.
  Cambiando esa palabra y la clave en Secrets, la app funciona igual.
- **Vocabulario**: `SINONIMOS` para lo que quieras fijar a mano;
  `terminos_ampliados.txt` para el vocabulario masivo generado por el script.
- **Palabras que estorban**: `VACIAS`.

## Regenerar el vocabulario

Se ejecuta fuera de la app, una vez, en local o en Google Colab:

```
pip install google-genai
export GEMINI_API_KEY=tu_clave
python enriquecer.py
```

Son unas 148 llamadas. Es reanudable: si se corta, se relanza y sigue donde
estaba. Para rehacerlo desde cero, borra `terminos_ampliados.txt` antes.

## Garantía sobre los datos

Ninguna denominación procede del modelo: se toma siempre del catálogo a partir
del código. Si el modelo devuelve un código inexistente, se descarta antes de
mostrarlo.

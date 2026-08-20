"""
Enriquecedor del catálogo SISPE.

Se ejecuta UNA VEZ, en tu ordenador, no en la app. Recorre las 2.218
ocupaciones y le pide al modelo, en lotes, las palabras que diría una persona
normal para nombrar cada oficio: coloquialismos, marcas, herramientas,
materiales y tareas.

El resultado es un archivo de texto que se sube al repositorio junto al
catálogo. A partir de ahí la app busca también por esas palabras, sin gastar
ni una llamada.

USO
    pip install google-genai
    set GEMINI_API_KEY=tu_clave          (Windows)
    export GEMINI_API_KEY=tu_clave       (Linux/Mac)
    python enriquecer.py

Es reanudable: si lo cortas o se agota la cuota, vuelve a lanzarlo y sigue
donde lo dejó.
"""

import json
import os
import re
import sys
import time

from google import genai
from google.genai import types

CATALOGO = "ocupaciones_sispe_ultraligero.txt"
SALIDA = "terminos_ampliados.txt"
MODELO = "gemini-3.5-flash-lite"
LOTE = 15            # ocupaciones por llamada (con lotes grandes responde peor)
PAUSA = 4.5          # segundos entre llamadas (15 por minuto en el tramo gratuito)

INSTRUCCIONES = """Eres experto en el catálogo de ocupaciones del SEPE y conoces
cómo habla la gente de su trabajo en una oficina de empleo.

Para cada ocupación devuelve entre 8 y 14 palabras que una persona usaría al
contar ese trabajo y que un buscador de texto NO encontraría partiendo de la
denominación oficial.

SÍ valen:
- marcas y nombres comerciales (pladur, glovo, mercadona, bobcat)
- jerga del oficio (trasdosado, ferralla, kelly, picking, comandas)
- herramientas y maquinaria (transpaleta, radial, carretilla, flejadora)
- materiales (yeso, escayola, perfileria, fibra)
- tareas concretas (planchar, encintar, atornillar, desbrozar)

NO valen, y son motivo de una respuesta inútil:
- variantes de género o plural: camarera, camareras, dependienta. El buscador ya
  las resuelve solo. NUNCA las incluyas.
- sinónimos de las palabras que ya están en la denominación: si dice
  "prefabricados", no valen "prefabricado", "panel" ni "bloque".
- palabras genéricas de cualquier oficio: trabajo, tareas, obra, empresa,
  cliente, servicio, atención, montaje, control.

Reglas de formato: palabras sueltas, minúsculas, sin acentos, separadas por
espacios. Sin comas, sin frases, sin explicaciones.

EJEMPLOS RESUELTOS
71991021 COLOCADORES DE PREFABRICADOS LIGEROS (CONSTRUCCION)
pladur trasdosado yeso laminado tabique perfileria encintar juntas atornillar

51201027 CAMAREROS DE BARRA Y/O DEPENDIENTES DE CAFETERIA
cafetera tirar canas tapas terraza bandeja comandas desayunos pinchos bar

98111024 MOZOS DE CARGA Y DESCARGA, ALMACEN Y/O MERCADO DE ABASTOS
carretilla transpaleta palet picking flejadora estanterias descargar furgon

Responde SOLO con un JSON así:
{"12345678": "palabra palabra palabra", "87654321": "palabra palabra"}
"""


def normaliza(t):
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    ).lower()


def carga_catalogo():
    filas = []
    with open(CATALOGO, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if ":" in linea:
                codigo, denom = linea.split(":", 1)
                filas.append((codigo.strip(), denom.strip()))
    return filas


def ya_hechos():
    hechos = set()
    if os.path.exists(SALIDA):
        with open(SALIDA, encoding="utf-8") as f:
            for linea in f:
                if ":" in linea:
                    hechos.add(linea.split(":", 1)[0].strip())
    return hechos


GENERICAS = {
    "trabajo", "trabajos", "tareas", "tarea", "obra", "obras", "empresa",
    "cliente", "clientes", "servicio", "servicios", "atencion", "montaje",
    "control", "general", "personal", "profesional", "sector", "actividad",
    "funciones", "labores", "puesto", "trabajador", "trabajadora",
}


def limpia(texto, denom):
    """Palabras nuevas, sin acentos, sin repetir lo que ya dice la denominación."""
    presentes = set(re.findall(r"\w+", normaliza(denom))) | GENERICAS
    fuera = []
    for w in re.findall(r"[a-zñ]+", normaliza(texto)):
        if len(w) > 2 and w not in presentes and w not in fuera:
            fuera.append(w)
    return " ".join(fuera[:14])


def pide_lote(cliente, lote):
    listado = "\n".join(f"{c}:{d}" for c, d in lote)
    cfg = dict(
        system_instruction=INSTRUCCIONES,
        max_output_tokens=4096,
        response_mime_type="application/json",
    )
    try:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
    except Exception:  # noqa: BLE001
        pass

    r = cliente.models.generate_content(
        model=MODELO, contents=listado,
        config=types.GenerateContentConfig(**cfg),
    )
    bruto = re.sub(r"^```(?:json)?|```$", "", (r.text or "").strip(), flags=re.M)
    return json.loads(bruto)


def main():
    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        sys.exit("Falta la variable de entorno GEMINI_API_KEY.")

    if not os.path.exists(CATALOGO):
        sys.exit(f"No encuentro {CATALOGO} en esta carpeta.")

    catalogo = carga_catalogo()
    hechos = ya_hechos()
    pendientes = [(c, d) for c, d in catalogo if c not in hechos]

    print(f"Catálogo: {len(catalogo)} ocupaciones")
    print(f"Ya resueltas: {len(hechos)}")
    print(f"Pendientes: {len(pendientes)}  ->  {-(-len(pendientes) // LOTE)} llamadas\n")

    if not pendientes:
        print("Nada que hacer. El archivo ya está completo.")
        return

    denominaciones = dict(catalogo)
    escritas = 0

    for i in range(0, len(pendientes), LOTE):
        lote = pendientes[i:i + LOTE]
        etiqueta = f"[{i // LOTE + 1}/{-(-len(pendientes) // LOTE)}]"

        try:
            datos = pide_lote(genai.Client(api_key=clave), lote)
        except Exception as e:  # noqa: BLE001
            texto = str(e)
            if "429" in texto or "RESOURCE_EXHAUSTED" in texto:
                print(f"\n{etiqueta} Cuota agotada por hoy.")
                print(f"Llevas {len(hechos) + escritas} ocupaciones en {SALIDA}.")
                print("Vuelve a lanzar el script mañana y seguirá desde aquí.")
                return
            print(f"{etiqueta} Error, se salta el lote: {type(e).__name__}")
            time.sleep(PAUSA)
            continue

        with open(SALIDA, "a", encoding="utf-8") as f:
            for codigo, terminos in datos.items():
                codigo = str(codigo).strip()
                if codigo not in denominaciones or codigo in hechos:
                    continue          # el modelo no puede inventarse códigos
                fila = limpia(str(terminos), denominaciones[codigo])
                if fila:
                    f.write(f"{codigo}:{fila}\n")
                    hechos.add(codigo)
                    escritas += 1

        print(f"{etiqueta} {escritas} ocupaciones escritas", flush=True)
        time.sleep(PAUSA)

    print(f"\nHecho. {SALIDA} tiene {len(hechos)} ocupaciones.")
    print("Súbelo al repositorio junto a app.py y al catálogo.")


if __name__ == "__main__":
    main()

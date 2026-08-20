"""
Codificador de ocupaciones SISPE
Interfaz de apoyo para localizar codigos oficiales antes de grabarlos en SilcoiWeb.
"""

import os
import re
import csv
import io
import json
import math
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

import streamlit as st

try:
    from google import genai
    from google.genai import types
except ImportError:                     # noqa: S110
    genai = types = None

try:
    from openai import OpenAI
except ImportError:                     # noqa: S110
    OpenAI = None

CATALOGO = "ocupaciones_sispe_ultraligero.txt"
N_CANDIDATOS = 12

# ---------------------------------------------------------------------------
# PROVEEDOR DE IA
# Cambia esta única línea para migrar: "gemini", "groq" o "mistral".
# La clave correspondiente va en los Secrets de Streamlit.
# ---------------------------------------------------------------------------
PROVEEDOR = "gemini"

PROVEEDORES = {
    # El primero es el preferido. Si agota cuota o no existe, se pasa al
    # siguiente automáticamente. Cuotas gratuitas al día, agosto 2026:
    #   gemini-3.5-flash-lite  500      gemini-3.1-flash-lite  500
    #   gemini-3.6-flash        20  <-- por eso no va el primero
    "gemini": {
        "clave": "GEMINI_API_KEY",
        "modelos": [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-3.6-flash",
        ],
    },
    "groq": {
        "clave": "GROQ_API_KEY",
        "modelos": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "url": "https://api.groq.com/openai/v1",
    },
    "mistral": {
        "clave": "MISTRAL_API_KEY",
        "modelos": ["mistral-small-latest"],
        "url": "https://api.mistral.ai/v1",
    },
}

AJUSTES = PROVEEDORES[PROVEEDOR]
MODELOS = AJUSTES["modelos"]


def modelo_actual():
    return MODELOS[min(st.session_state.get("modelo_ok", 0), len(MODELOS) - 1)]


def sin_cuota(e):
    t = str(e)
    return "429" in t or "RESOURCE_EXHAUSTED" in t or "quota" in t.lower()

st.set_page_config(
    page_title="Codificador de ocupaciones",
    page_icon="◉",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# ESTILO
# ---------------------------------------------------------------------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;700&display=swap');

:root{
  --tinta:#0E1116;
  --cobalto:#1F3BFF;
  --coral:#FF5A36;
  --hueso:#F3F1EC;
  --humo:#6B6F76;
  --borde:#E4E0D8;
}

.stApp{ background:var(--hueso); }
html,body,[class*="css"],.stMarkdown{ font-family:'Inter',system-ui,sans-serif; color:var(--tinta); }
.block-container{ padding-top:1.6rem; padding-bottom:5rem; max-width:760px; }
#MainMenu, footer, header[data-testid="stHeader"]{ visibility:hidden; height:0; }

/* ---------- Cabecera ---------- */
.hero{
  background:var(--tinta); border-radius:22px; padding:1.9rem 1.8rem 1.6rem;
  position:relative; overflow:hidden; margin-bottom:1.4rem;
}
.hero::after{
  content:""; position:absolute; right:-70px; top:-70px; width:230px; height:230px;
  border-radius:50%; background:radial-gradient(circle at 30% 30%, var(--cobalto), transparent 68%);
  opacity:.55;
}
.hero .eyebrow{
  font-family:'JetBrains Mono',monospace; font-size:.66rem; letter-spacing:.2em;
  text-transform:uppercase; color:var(--coral); margin-bottom:.55rem;
}
.hero h1{
  font-family:'Space Grotesk',sans-serif; font-weight:700;
  font-size:clamp(1.8rem,5vw,2.6rem); line-height:1.05; letter-spacing:-.035em;
  color:#fff; margin:0;
}
.hero p{
  color:#A8ADB6; font-size:.95rem; margin:.7rem 0 0; max-width:33ch; position:relative; z-index:1;
}

/* ---------- Consulta ---------- */
.consulta{
  font-family:'Space Grotesk',sans-serif; font-size:1.32rem; font-weight:500;
  letter-spacing:-.02em; line-height:1.3; margin:2.2rem 0 1rem;
  padding-left:.9rem; border-left:3px solid var(--coral);
}

/* ---------- Tarjeta de resultado ---------- */
.tarjeta{
  background:#fff; border:1px solid var(--borde); border-radius:18px;
  padding:1.15rem 1.3rem; margin-bottom:.7rem;
  transition:transform .16s ease, box-shadow .16s ease;
}
.tarjeta:hover{ transform:translateY(-2px); box-shadow:0 10px 26px rgba(14,17,22,.07); }
.tarjeta.top{ border:1.5px solid var(--tinta); }

.fila{ display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap; }
.orden{
  font-family:'JetBrains Mono',monospace; font-size:.7rem; color:var(--humo);
  border:1px solid var(--borde); border-radius:99px; padding:.12rem .5rem;
}
.codigo{
  font-family:'JetBrains Mono',monospace; font-weight:700;
  font-size:clamp(1.35rem,4.4vw,1.7rem); letter-spacing:.1em; color:var(--cobalto);
}
.tarjeta.top .codigo{ color:var(--tinta); }
.denominacion{
  font-family:'Space Grotesk',sans-serif; font-weight:500; font-size:1.02rem;
  line-height:1.35; letter-spacing:-.01em; margin:.45rem 0 .55rem;
}
.motivo{ font-size:.86rem; color:var(--humo); line-height:1.5; }

.etiqueta{
  display:inline-block; font-family:'JetBrains Mono',monospace; font-size:.66rem;
  letter-spacing:.09em; text-transform:uppercase; padding:.2rem .55rem;
  border-radius:99px; background:var(--hueso); border:1px solid var(--borde); color:var(--humo);
}
.etiqueta.destacada{ background:var(--coral); border-color:var(--coral); color:#fff; }

/* ---------- Pregunta de desambiguacion ---------- */
.pregunta{
  background:#fff; border:1px dashed var(--cobalto); border-radius:18px;
  padding:1.05rem 1.25rem; margin:.9rem 0 .4rem;
}
.pregunta .titulo{
  font-family:'JetBrains Mono',monospace; font-size:.66rem; letter-spacing:.16em;
  text-transform:uppercase; color:var(--cobalto); margin-bottom:.45rem;
}
.pregunta .texto{ font-size:.97rem; line-height:1.45; }

.seccion{
  font-family:'JetBrains Mono',monospace; font-size:.65rem; letter-spacing:.16em;
  text-transform:uppercase; color:var(--humo); margin:1.5rem 0 .6rem;
}
.nota{ font-size:.78rem; color:var(--humo); margin-top:.4rem; }

/* Barra de progreso mientras responde el modelo */
div[data-testid="stProgress"]{ margin:0 0 1rem; }
div[data-testid="stProgress"] p{
  font-family:'JetBrains Mono',monospace !important; font-size:.63rem !important;
  letter-spacing:.16em; text-transform:uppercase; color:var(--humo) !important;
}
div[data-testid="stProgress"] div[role="progressbar"] > div{
  background-color:var(--borde);
}
div[data-testid="stProgress"] div[role="progressbar"] > div > div{
  background-color:var(--coral) !important; background-image:none !important;
}

/* ---------- Controles nativos ---------- */
div[data-testid="stChatInput"] textarea{ font-family:'Inter',sans-serif !important; font-size:.98rem !important; }
div[data-testid="stChatInput"]{ border-radius:16px; }
.stButton button{
  border-radius:99px; border:1px solid var(--borde); background:#fff; color:var(--tinta);
  font-family:'Inter',sans-serif; font-size:.83rem; font-weight:500; padding:.35rem 1rem;
  transition:all .15s ease;
}
.stButton button:hover{ border-color:var(--tinta); background:var(--tinta); color:#fff; }
div[data-testid="stExpander"]{ border:none; background:transparent; }
div[data-testid="stExpander"] summary{ font-size:.83rem; color:var(--humo); }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# CATALOGO
# ---------------------------------------------------------------------------

VACIAS = {
    "de", "del", "la", "el", "los", "las", "en", "y", "o", "con", "para", "por",
    "un", "una", "al", "sin", "que", "su", "mas", "general", "asalariados",
    "otros", "otras", "clasificados", "anteriormente", "tanto", "cuenta",
    "trabajado", "trabajo", "anos", "experiencia", "he", "soy", "estuve",
    # ruido de la propia consulta
    "dame", "dime", "busca", "buscar", "quiero", "necesito", "ocupacion",
    "ocupaciones", "codigo", "codigos", "puesto", "persona", "personas",
    "gente", "alguien", "senor", "senora", "chico", "chica", "tiene", "tenia",
    "hacia", "hace", "estado", "sido", "una", "unos", "unas", "casa", "casas",
    "sus", "mis", "tus", "este", "esta", "esto", "ese", "esa", "muy", "bien",
    "algo", "cosas", "cosa", "tipo", "tipos", "ahora", "antes", "despues",
}

SINONIMOS = {
    # Solo hace falta una entrada cuando la palabra de la persona NO comparte
    # raíz con la del catálogo. Género y plural ya los resuelve raiz():
    # camarera→camarer→camareros, peluquera→peluquer→peluqueros, etc.

    # plataformas y marcas
    "uber": "conductores automoviles taxis furgonetas taxistas",
    "cabify": "conductores automoviles taxis furgonetas taxistas",
    "vtc": "conductores automoviles taxis furgonetas taxistas",
    "bolt": "conductores automoviles taxis furgonetas taxistas",
    "glovo": "repartidores motocicleta ciclomotor reparto",
    "uber eats": "repartidores motocicleta ciclomotor reparto",
    "just eat": "repartidores motocicleta ciclomotor reparto",
    "deliveroo": "repartidores motocicleta ciclomotor reparto",
    "rider": "repartidores motocicleta ciclomotor reparto",
    "amazon": "mozos carga descarga almacen preparadores pedidos",

    # coloquialismos de oficio
    "carretillero": "carretillas elevadoras operadores",
    "kelly": "camareros piso habitaciones",
    "interna": "hogar internos domicilio",
    "interno": "hogar internos domicilio",
    "informatico": "tecnicos equipos informaticos redes microinformaticos",
    "desarrollador": "programadores aplicaciones informaticas software",
    "teleoperadora": "teleoperadores telefonistas",
    "teleoperador": "teleoperadores telefonistas",
    "chofer": "conductores",
    "chapista": "chapistas carroceria",

    # oficina: la persona nombra la tarea, el catálogo nombra el puesto
    "administrativa": "empleados administrativos oficina",
    "administrativo": "empleados administrativos oficina",
    "facturacion": "contabilidad administrativos empleados",
    "facturas": "contabilidad administrativos empleados",
    "nominas": "contabilidad administrativos empleados",
    "contabilidad": "contabilidad administrativos empleados",
    "atencion al cliente": "atencion cliente empleados administrativos",
    "secretaria": "secretarios direccion administrativos",
    "archivo": "archivo administrativos empleados",

    # idiomas: el catálogo dice "idiomas", nunca el idioma concreto
    "ingles": "idiomas profesores",
    "frances": "idiomas profesores",
    "aleman": "idiomas profesores",
    "italiano": "idiomas profesores",
    "chino": "idiomas profesores",
    "ele": "idiomas profesores",

    # entorno de trabajo: domicilio frente a institución
    "casa": "domiciliarios domicilio hogar",
    "domicilio": "domiciliarios hogar",
    "particular": "hogar domiciliarios",
    "hogar": "hogar domiciliarios",
    "residencia": "instituciones dependencia",
    "geriatrico": "instituciones dependencia",

    # personas atendidas
    "mayores": "mayores dependencia domiciliarios asistentes",
    "anciano": "mayores dependencia domiciliarios asistentes",
    "ancianos": "mayores dependencia domiciliarios asistentes",
    "abuelo": "mayores dependencia domiciliarios asistentes",
    "abuela": "mayores dependencia domiciliarios asistentes",
    "tercera edad": "mayores dependencia domiciliarios asistentes",
    "dependencia": "dependencia domiciliarios asistentes",
}

NIVELES = {
    "10": "Dirección",
    "20": "Mandos intermedios",
    "30": "Jefes de equipo",
    "00": "Técnicos / Sin categoría",
    "70": "Auxiliares",
    "80": "Peones",
    "90": "Aprendices",
}


def normaliza(t):
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    ).lower().strip()


def raiz(w):
    if len(w) > 5 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s"):
        w = w[:-1]
    if len(w) > 4 and w[-1] in "aoe":
        w = w[:-1]
    return w


@st.cache_resource(show_spinner=False)
def carga_indice():
    if not os.path.exists(CATALOGO):
        return {"ok": False, "registros": []}

    registros, inv, inv_raiz = [], defaultdict(list), defaultdict(list)
    with open(CATALOGO, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if ":" not in linea:
                continue
            codigo, denom = linea.split(":", 1)
            tokens = [
                t for t in re.findall(r"\w+", normaliza(denom))
                if len(t) > 2 and t not in VACIAS
            ]
            registros.append({
                "codigo": codigo.strip(),
                "denom": denom.strip(),
                "palabras": set(tokens),
                "raices": {raiz(t) for t in tokens},
                "cabeza": {raiz(t) for t in tokens[:3]},
            })

    n = max(1, len(registros))
    for i, r in enumerate(registros):
        for w in r["palabras"]:
            inv[w].append(i)
        for w in r["raices"]:
            inv_raiz[w].append(i)

    trigramas = defaultdict(set)
    for w in inv_raiz:
        for j in range(len(w) - 2):
            trigramas[w[j:j + 3]].add(w)

    return {
        "ok": True,
        "registros": registros,
        "por_codigo": {r["codigo"]: r["denom"] for r in registros},
        "inv": inv,
        "inv_raiz": inv_raiz,
        "idf": {w: math.log(1 + n / len(ix)) for w, ix in inv.items()},
        "idf_raiz": {w: math.log(1 + n / len(ix)) for w, ix in inv_raiz.items()},
        "trigramas": trigramas,
        "vocab_raiz": list(inv_raiz.keys()),
    }


IDX = carga_indice()

if not IDX["ok"]:
    st.error(
        f"Falta el archivo **{CATALOGO}**. Súbelo al repositorio, junto a app.py, "
        "con ese nombre exacto."
    )
    st.stop()


def parecidas(palabra, umbral=0.84, tope=3):
    posibles = set()
    for j in range(len(palabra) - 2):
        posibles |= IDX["trigramas"].get(palabra[j:j + 3], set())
    salida = []
    for c in posibles:
        if abs(len(c) - len(palabra)) > 3:
            continue
        r = SequenceMatcher(None, palabra, c).ratio()
        if r >= umbral:
            salida.append((r, c))
    salida.sort(reverse=True)
    return salida[:tope]


def desconocidas(consulta):
    """Palabras con contenido que el catálogo no reconoce de ninguna forma.

    Son la señal de que hay jerga, una marca comercial o un tecnicismo:
    'pladur', 'glovo', 'kelly'. El diccionario nunca las tendrá todas.
    """
    q = normaliza(consulta)
    fuera = []
    for w in re.findall(r"\w+", q):
        if len(w) <= 3 or w in VACIAS or w in fuera:
            continue
        r = raiz(w)
        if w in IDX["inv"] or r in IDX["inv_raiz"]:
            continue
        if any(v.startswith(r) or r.startswith(v) for v in IDX["vocab_raiz"] if len(v) > 3):
            continue
        if any(clave in q for clave in SINONIMOS if w in clave or clave in w):
            continue
        fuera.append(w)
    return fuera


def busca(consulta, tope=20):
    q = normaliza(consulta)
    terminos = {}
    for w in re.findall(r"\w+", q):
        if len(w) > 2 and w not in VACIAS:
            terminos[w] = 1.0
    for clave, expansion in SINONIMOS.items():
        if clave in q:
            for w in re.findall(r"\w+", normaliza(expansion)):
                terminos.setdefault(w, 0.6)
    if not terminos:
        return []

    originales = {raiz(w) for w, peso in terminos.items() if peso == 1.0}
    puntos, cubierto = defaultdict(float), defaultdict(set)

    def suma(i, valor, termino):
        puntos[i] += valor
        cubierto[i].add(termino)

    for w, peso in terminos.items():
        r = raiz(w)
        encontrado = False
        if w in IDX["inv"]:
            encontrado = True
            k = IDX["idf"][w] * peso * 3.0
            for i in IDX["inv"][w]:
                suma(i, k, r)
        if r in IDX["inv_raiz"]:
            encontrado = True
            k = IDX["idf_raiz"][r] * peso * 2.2
            for i in IDX["inv_raiz"][r]:
                suma(i, k, r)
        if len(r) > 3:
            for v in IDX["vocab_raiz"]:
                if v != r and (v.startswith(r) or r.startswith(v)):
                    k = IDX["idf_raiz"][v] * peso * 1.0
                    for i in IDX["inv_raiz"][v]:
                        suma(i, k, r)
        if not encontrado and len(r) > 4:
            for ratio, c in parecidas(r):
                k = IDX["idf_raiz"][c] * peso * ratio * 1.4
                for i in IDX["inv_raiz"][c]:
                    suma(i, k, r)

    n_term = max(1, len(originales))
    n_total = max(1, len({raiz(w) for w in terminos}))
    resultados = []
    for i, valor in puntos.items():
        reg = IDX["registros"][i]
        nucleo = 1.0 + 0.5 * len(cubierto[i] & reg["cabeza"])
        propios = len(cubierto[i] & originales)
        # premia encajar con varias palabras a la vez, sean propias o expandidas
        cobertura = (
            0.55
            + 0.30 * min(1.0, len(cubierto[i]) / n_total)
            + 0.15 * min(1.0, propios / n_term)
        )
        resultados.append((valor * nucleo * cobertura, reg["codigo"], reg["denom"]))
    resultados.sort(reverse=True)
    return resultados[:tope]


# ---------------------------------------------------------------------------
# MODELO
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def cliente():
    nombre = AJUSTES["clave"]
    clave = st.secrets.get(nombre) or os.environ.get(nombre)
    if not clave:
        return None
    if PROVEEDOR == "gemini":
        return genai.Client(api_key=clave) if genai else None
    return OpenAI(api_key=clave, base_url=AJUSTES["url"]) if OpenAI else None


INSTRUCCIONES = """Eres un técnico de codificación de ocupaciones para SilcoiWeb (SEPE).

Recibes la descripción de un puesto y una lista cerrada de ocupaciones candidatas.
Selecciona entre 3 y 5, de mayor a menor afinidad.

REGLAS
1. Usa únicamente códigos y denominaciones literales de la lista de candidatos. No inventes ni modifiques ninguno.
2. Nivel profesional: 90 aprendices (sin experiencia) / 00 técnicos o sin categoría (estándar con experiencia) / 10 dirección / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.
3. El campo "motivo" explica en menos de 10 palabras por qué encaja, en español con acentuación correcta.
4. No propongas ocupaciones de dirección, jefatura ni mando (niveles 10, 20, 30) salvo que la descripción diga expresamente que dirigía equipos, centros o departamentos.
5. Respeta el entorno de trabajo que indique la descripción: domicilio particular frente a institución, centro o residencia. Si dice "en su casa" o "a domicilio", descarta las ocupaciones que digan "en instituciones".
6. Rellena "pregunta" solo si faltan datos para decidir entre dos ocupaciones; si no, déjalo vacío.

Responde solo con este JSON:
{"ocupaciones":[{"codigo":"12345678","denominacion":"...","nivel":"00","motivo":"..."}],"pregunta":""}
"""


def _configuraciones():
    """Solo Gemini. De la más rápida a la más lenta; la que sirva se recuerda."""
    base = dict(
        system_instruction=INSTRUCCIONES,
        max_output_tokens=900,
        response_mime_type="application/json",
    )
    opciones = []
    for nivel in ("minimal", "low"):
        try:
            opciones.append(
                {**base, "thinking_config": types.ThinkingConfig(thinking_level=nivel)}
            )
        except Exception:  # noqa: BLE001  SDK sin thinking_level
            break
    opciones.append(base)
    return opciones


def _flujo_gemini(cli, prompt):
    opciones = _configuraciones()
    ultimo = None
    for m in range(st.session_state.get("modelo_ok", 0), len(MODELOS)):
        for i in range(st.session_state.get("cfg", 0), len(opciones)):
            emitido = False
            try:
                flujo = cli.models.generate_content_stream(
                    model=MODELOS[m], contents=prompt,
                    config=types.GenerateContentConfig(**opciones[i]),
                )
                for trozo in flujo:
                    if not emitido:
                        st.session_state["modelo_ok"] = m
                        st.session_state["cfg"] = i
                        emitido = True
                    if getattr(trozo, "text", None):
                        yield trozo.text
                return
            except Exception as e:  # noqa: BLE001
                if emitido:   # no reintentar a medio texto: duplicaría contenido
                    raise
                ultimo = e
                if sin_cuota(e):
                    break     # cuota agotada: cambiar de modelo, no de ajuste
    raise ultimo


def _flujo_openai(cli, prompt):
    """Groq, Mistral y cualquier otro compatible con el formato de OpenAI."""
    flujo = cli.chat.completions.create(
        model=modelo_actual(),
        messages=[
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": prompt},
        ],
        max_tokens=900,
        temperature=0,
        response_format={"type": "json_object"},
        stream=True,
    )
    for trozo in flujo:
        if not trozo.choices:
            continue
        texto = trozo.choices[0].delta.content
        if texto:
            yield texto


TRADUCTOR = """Eres experto en la Clasificación Nacional de Ocupaciones española.
Recibes palabras coloquiales, marcas comerciales o jerga de oficio.
Devuelve SOLO entre 6 y 12 palabras sueltas separadas por espacios: los términos
que usaría la clasificación oficial para esa actividad (nombre formal del oficio,
materiales, herramientas, tareas). Sin comas, sin frases, sin explicaciones.

Ejemplo. Entrada: pladur
Salida: prefabricados ligeros colocadores escayolista tabiques yeso laminado construccion
"""


def pregunta_corta(cli, sistema, prompt, maximo=220):
    """Una respuesta breve, sin streaming. Sirve para traducir vocabulario."""
    if PROVEEDOR == "gemini":
        cfg = dict(system_instruction=sistema, max_output_tokens=maximo)
        try:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
        except Exception:  # noqa: BLE001
            pass
        r = cli.models.generate_content(
            model=modelo_actual(), contents=prompt,
            config=types.GenerateContentConfig(**cfg),
        )
        return (getattr(r, "text", "") or "").strip()

    r = cli.chat.completions.create(
        model=modelo_actual(),
        messages=[
            {"role": "system", "content": sistema},
            {"role": "user", "content": prompt},
        ],
        max_tokens=maximo,
        temperature=0,
    )
    return (r.choices[0].message.content or "").strip()


def traduce_jerga(cli, palabras, contexto):
    """Convierte jerga en vocabulario del catálogo. Se recuerda en la sesión."""
    clave = " ".join(sorted(palabras))
    lexico = st.session_state["lexico"]
    if clave in lexico:
        return lexico[clave]
    try:
        bruto = pregunta_corta(
            cli, TRADUCTOR,
            f"Entrada: {clave}\nContexto en el que aparece: {contexto}",
        )
    except Exception:  # noqa: BLE001
        return ""
    limpio = " ".join(re.findall(r"[a-zñáéíóúü]+", normaliza(bruto))[:14])
    lexico[clave] = limpio
    return limpio


def flujo_modelo(cli, texto, candidatos):
    """Devuelve fragmentos de texto según llegan."""
    prompt = f"CANDIDATOS (única fuente válida):\n{candidatos}\n\nDESCRIPCIÓN: {texto}"
    if PROVEEDOR == "gemini":
        yield from _flujo_gemini(cli, prompt)
    else:
        yield from _flujo_openai(cli, prompt)


def objetos_parciales(bruto):
    """Extrae las ocupaciones ya completas de un JSON aún a medio llegar."""
    inicio = bruto.find("[")
    if inicio == -1:
        return []
    salida, prof, arranque = [], 0, None
    cadena = escape = False
    for i in range(inicio + 1, len(bruto)):
        c = bruto[i]
        if cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                cadena = False
            continue
        if c == '"':
            cadena = True
        elif c == "{":
            if prof == 0:
                arranque = i
            prof += 1
        elif c == "}":
            prof -= 1
            if prof == 0 and arranque is not None:
                try:
                    salida.append(json.loads(bruto[arranque:i + 1]))
                except Exception:  # noqa: BLE001
                    pass
                arranque = None
        elif c == "]" and prof == 0:
            break
    return salida


def verifica(lista):
    """Solo sobreviven los códigos que existen en el catálogo oficial."""
    limpias, descartadas = [], 0
    vistos = set()
    for o in lista or []:
        codigo = str(o.get("codigo", "")).strip()
        if codigo in vistos:
            continue
        if codigo in IDX["por_codigo"]:
            vistos.add(codigo)
            nivel = str(o.get("nivel", "00")).strip()[:2] or "00"
            limpias.append({
                "codigo": codigo,
                "denominacion": IDX["por_codigo"][codigo],   # siempre la oficial
                "nivel": nivel,
                "nivel_texto": NIVELES.get(nivel, "Técnicos / Sin categoría"),
                "motivo": str(o.get("motivo", "")).strip(),
            })
        elif codigo:
            descartadas += 1
    return limpias[:5], descartadas


def interpreta(bruto):
    texto = re.sub(r"^```(?:json)?|```$", "", (bruto or "").strip(), flags=re.MULTILINE)
    datos = {}
    try:
        datos = json.loads(texto)
    except Exception:  # noqa: BLE001
        bloque = re.search(r"\{.*\}", texto, re.S)
        if bloque:
            try:
                datos = json.loads(bloque.group())
            except Exception:  # noqa: BLE001
                datos = {}
    if not datos:
        ocupaciones, descartadas = verifica(objetos_parciales(texto))
        return {"ocupaciones": ocupaciones, "pregunta": "", "descartadas": descartadas}

    ocupaciones, descartadas = verifica(datos.get("ocupaciones"))
    return {
        "ocupaciones": ocupaciones,
        "pregunta": str(datos.get("pregunta", "") or "").strip(),
        "descartadas": descartadas,
    }


# ---------------------------------------------------------------------------
# RENDER
# ---------------------------------------------------------------------------

def pinta_tarjeta(i, o, destacada=False):
    clase = "tarjeta top" if destacada else "tarjeta"
    etiqueta = "etiqueta destacada" if destacada else "etiqueta"
    motivo = f'<div class="motivo">{o["motivo"]}</div>' if o["motivo"] else ""
    st.markdown(
        f'<div class="{clase}">'
        f'  <div class="fila">'
        f'    <span class="orden">{i:02d}</span>'
        f'    <span class="codigo">{o["codigo"]}</span>'
        f'    <span class="{etiqueta}">Nivel {o["nivel"]} &middot; {o["nivel_texto"]}</span>'
        f'  </div>'
        f'  <div class="denominacion">{o["denominacion"]}</div>'
        f'  {motivo}'
        f'</div>',
        unsafe_allow_html=True,
    )


def pinta_resultado(payload, estado=None, avance=0.06):
    if estado:
        st.progress(min(avance, 0.95), text=estado)
    if payload.get("aviso"):
        st.info(payload["aviso"])
        return

    ocupaciones = payload.get("ocupaciones", [])
    if not ocupaciones:
        st.info(
            "No encuentro coincidencias claras. Prueba con el nombre del puesto "
            "o con una función concreta: *reparto en moto*, *atención telefónica*, "
            "*carretilla elevadora*."
        )
        return

    for i, o in enumerate(ocupaciones, 1):
        pinta_tarjeta(i, o, destacada=(i == 1))

    if payload.get("pregunta"):
        st.markdown(
            f'<div class="pregunta"><div class="titulo">Pregunta para la persona</div>'
            f'<div class="texto">{payload["pregunta"]}</div></div>',
            unsafe_allow_html=True,
        )

    if estado:
        return

    otras = payload.get("otras", [])
    if otras:
        with st.expander("Ver otras ocupaciones del catálogo"):
            for cod, den in otras:
                st.markdown(
                    f'<div style="padding:.35rem 0;border-bottom:1px solid var(--borde)">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-weight:700;'
                    f'letter-spacing:.08em">{cod}</span> &nbsp; '
                    f'<span style="font-size:.9rem">{den}</span></div>',
                    unsafe_allow_html=True,
                )

    if payload.get("fallo"):
        st.markdown(
            '<div class="nota">La IA no ha respondido: estas son las coincidencias '
            'del catálogo, sin afinar.</div>',
            unsafe_allow_html=True,
        )
        with st.expander("Ver el motivo"):
            st.code(payload["fallo"], language=None)

    if payload.get("descartadas"):
        st.markdown(
            f'<div class="nota">Se {"ha" if payload["descartadas"] == 1 else "han"} descartado '
            f'{payload["descartadas"]} '
            f'{"sugerencia que no figuraba" if payload["descartadas"] == 1 else "sugerencias que no figuraban"} '
            f'en el catálogo oficial.</div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# LOGICA DE CONSULTA
# ---------------------------------------------------------------------------

def _basica(encontrados, motivo=""):
    return [{
        "codigo": c, "denominacion": d, "nivel": "00",
        "nivel_texto": NIVELES["00"], "motivo": motivo,
    } for _, c, d in encontrados[:5]]


def resuelve(texto, zona, usar_ia=True):
    """Pinta resultados desde el primer instante y los va afinando."""
    codigo = texto.strip()
    if re.fullmatch(r"\d{8}", codigo):
        if codigo in IDX["por_codigo"]:
            payload = {"ocupaciones": [{
                "codigo": codigo,
                "denominacion": IDX["por_codigo"][codigo],
                "nivel": "00",
                "nivel_texto": NIVELES["00"],
                "motivo": "Consulta directa por código.",
            }]}
        else:
            payload = {"aviso": f"El código {codigo} no figura en el catálogo oficial."}
        with zona.container():
            pinta_resultado(payload)
        return payload

    encontrados = busca(texto, tope=N_CANDIDATOS)
    if not encontrados:
        payload = {"ocupaciones": []}
        with zona.container():
            pinta_resultado(payload)
        return payload

    memoria = st.session_state["cache"]
    clave = normaliza(texto)
    if clave in memoria:
        with zona.container():
            pinta_resultado(memoria[clave])
        return memoria[clave]

    provisional = {
        "ocupaciones": _basica(encontrados),
        "otras": [(c, d) for _, c, d in encontrados[5:12]],
    }
    cli = cliente() if usar_ia else None

    if cli is None:
        with zona.container():
            pinta_resultado(provisional)
        return provisional

    # Resultados del catálogo mientras el modelo responde
    with zona.container():
        pinta_resultado(provisional, estado="Afinando el resultado")

    # Si la consulta trae jerga o marcas, se traduce a vocabulario del catálogo
    jerga = desconocidas(texto)
    if jerga:
        with zona.container():
            pinta_resultado(provisional, estado="Interpretando el oficio")
        extra = traduce_jerga(cli, jerga, texto)
        if extra:
            mejores = busca(f"{texto} {extra}", tope=N_CANDIDATOS)
            if mejores:
                encontrados = mejores
                provisional = {
                    "ocupaciones": _basica(encontrados),
                    "otras": [(c, d) for _, c, d in encontrados[5:12]],
                }
                with zona.container():
                    pinta_resultado(provisional, estado="Afinando el resultado")

    bruto, mostradas = "", 0
    try:
        for trozo in flujo_modelo(cli, texto, "\n".join(f"{c}:{d}" for _, c, d in encontrados)):
            bruto += trozo
            listas, _ = verifica(objetos_parciales(bruto))
            if len(listas) > mostradas:
                mostradas = len(listas)
                with zona.container():
                    pinta_resultado(
                        {"ocupaciones": listas},
                        estado="Afinando el resultado",
                        avance=0.06 + 0.19 * mostradas,
                    )
    except Exception as e:  # noqa: BLE001
        zona.empty()
        provisional["fallo"] = f"{type(e).__name__}: {e}"
        pinta_resultado(provisional)
        return provisional

    payload = interpreta(bruto)
    if not payload["ocupaciones"]:
        payload["ocupaciones"] = provisional["ocupaciones"]
    elegidos = {o["codigo"] for o in payload["ocupaciones"]}
    payload["otras"] = [(c, d) for _, c, d in encontrados if c not in elegidos][:7]

    zona.empty()          # retira el bloque provisional antes del definitivo
    pinta_resultado(payload)
    memoria[clave] = payload
    return payload



# ---------------------------------------------------------------------------
# INTERFAZ
# ---------------------------------------------------------------------------

st.session_state.setdefault("historial", [])
st.session_state.setdefault("pendiente", None)
st.session_state.setdefault("usar_ia", True)
st.session_state.setdefault("cache", {})
st.session_state.setdefault("lexico", {})
st.session_state.setdefault("modelo_ok", 0)

st.markdown(
    '<div class="hero">'
    '<div class="eyebrow">Catálogo SISPE</div>'
    '<h1>Codificador<br>de ocupaciones</h1>'
    '<p>Describe el puesto y obtén los códigos oficiales listos para grabar en SilcoiWeb.</p>'
    '</div>',
    unsafe_allow_html=True,
)

_, ajustes = st.columns([3, 1])
with ajustes:
    with st.popover("Ajustes", use_container_width=True):
        st.session_state["usar_ia"] = st.toggle(
            "Afinar con IA", value=st.session_state["usar_ia"],
            help="Desactivado, muestra las coincidencias del catálogo al instante.",
        )
        if st.session_state["historial"]:
            buffer = io.StringIO()
            escritor = csv.writer(buffer, delimiter=";")
            escritor.writerow(["consulta", "codigos"])
            for consulta, payload in st.session_state["historial"]:
                escritor.writerow([
                    consulta,
                    " | ".join(o["codigo"] for o in payload.get("ocupaciones", [])),
                ])
            st.download_button(
                "Descargar sesión",
                buffer.getvalue().encode("utf-8-sig"),
                file_name="codificaciones.csv",
                mime="text/csv",
                use_container_width=True,
            )
            if st.button("Empezar de nuevo", use_container_width=True):
                st.session_state["historial"] = []
                st.rerun()
        if st.button("Probar la conexión con la IA", use_container_width=True):
            prueba = cliente()
            if prueba is None:
                st.error(f"No hay clave {AJUSTES['clave']} en los Secrets.")
            else:
                try:
                    eco = pregunta_corta(
                        prueba, "Responde únicamente con la palabra ok.",
                        "ok", maximo=200,
                    )
                    st.success(f"{modelo_actual()}: {eco[:60] or '(respuesta vacía)'}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"{type(e).__name__}: {e}")

        if st.session_state.get("lexico"):
            st.markdown("**Términos aprendidos**")
            st.caption("Pégalos en SINONIMOS para no volver a traducirlos.")
            st.code(
                "\n".join(
                    f'"{k}": "{v}",' for k, v in st.session_state["lexico"].items() if v
                ),
                language=None,
            )
        st.caption(
            f"{len(IDX['registros'])} ocupaciones cargadas. Describe solo el puesto: "
            "sin nombres, DNI ni datos identificativos de la persona."
        )

# Ejemplos, solo mientras no hay consultas
if not st.session_state["historial"]:
    st.markdown('<div class="seccion">Prueba con</div>', unsafe_allow_html=True)
    ejemplos = [
        "Camarera de barra en cafetería",
        "Reparto en moto para Glovo",
        "Auxiliar administrativa: facturación y teléfono",
        "Carretillero en almacén",
    ]
    for fila in (ejemplos[:2], ejemplos[2:]):
        cols = st.columns(len(fila))
        for col, ej in zip(cols, fila):
            if col.button(ej, use_container_width=True, key=f"ej_{ej}"):
                st.session_state["pendiente"] = ej
                st.rerun()

for consulta, payload in st.session_state["historial"]:
    st.markdown(f'<div class="consulta">{consulta}</div>', unsafe_allow_html=True)
    pinta_resultado(payload)

entrada = st.chat_input("Puesto, funciones o experiencia. También admite un código de 8 cifras.")
entrada = entrada or st.session_state.pop("pendiente", None)

if entrada:
    st.markdown(f'<div class="consulta">{entrada}</div>', unsafe_allow_html=True)
    zona = st.empty()
    payload = resuelve(entrada, zona, usar_ia=st.session_state["usar_ia"])
    st.session_state["historial"].append((entrada, payload))

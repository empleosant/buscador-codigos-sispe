"""
Codificador de ocupaciones SISPE
Interfaz de apoyo para localizar codigos oficiales antes de grabarlos en SilcoiWeb.
"""

import os
import re
import csv
import time
import io
import json
import math
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

import urllib.error
import urllib.request

import streamlit as st
import streamlit.components.v1 as components

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
# Opcional: lo genera enriquecer.py una sola vez. Si está, la app busca también
# por el vocabulario coloquial de cada ocupación.
AMPLIADO = "terminos_ampliados.txt"
N_CANDIDATOS = 12
ESPERA_MAXIMA = 45      # segundos antes de rendirse con el modelo

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
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# ESTILO
# ---------------------------------------------------------------------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&display=swap');

:root{
  --tinta:#1B2B33;
  --verde:#0E7C6B;
  --verde-claro:#D8EFE9;
  --verde-oscuro:#0B5A4E;
  --fondo:#EEF2F4;
  --humo:#6E818A;
  --tenue:#9AAAB2;
  --linea:#DDE5E9;
}

.stApp{ background:var(--fondo); }
html,body,[class*="css"],.stMarkdown{ font-family:Figtree,system-ui,sans-serif; color:var(--tinta); }
.block-container{ padding-top:2rem; padding-bottom:3rem; max-width:1180px; }
#MainMenu, footer, header[data-testid="stHeader"]{ visibility:hidden; height:0; }

/* ---------- Portada ---------- */
.portada{ text-align:center; padding:3.5rem 0 1.2rem; animation:surgir .5s ease both; }
.portada h1{
  font-size:clamp(2rem,4.5vw,2.9rem); font-weight:700; letter-spacing:-.03em;
  color:var(--tinta) !important; margin:0 0 .5rem;
}
.portada h1 span{ color:var(--verde) !important; }
.marca span{ color:var(--verde) !important; }
.portada p{ color:var(--humo); font-size:1.02rem; margin:0; }

/* ---------- Columna izquierda ---------- */
.marca-sub{ color:var(--tenue); font-size:.82rem; margin:-.2rem 0 1.4rem .1rem; }

/* El título de la columna izquierda es un botón que vuelve a la portada */
.st-key-marca button{
  background:transparent !important; box-shadow:none !important; border:none !important;
  padding:0 !important; justify-content:flex-start !important;
}
.st-key-marca button p{
  font-size:1.35rem !important; font-weight:700 !important; letter-spacing:-.02em;
  color:var(--tinta) !important; margin:0 !important; text-align:left !important;
}
.st-key-marca button:hover p{ color:var(--verde) !important; }

/* ---------- Consulta ---------- */
.consulta{
  font-size:1.05rem; font-weight:600; letter-spacing:-.01em; color:var(--tinta);
  margin:0 0 .8rem; display:flex; align-items:center; gap:.5rem;
}
.consulta::before{
  content:""; width:6px; height:6px; border-radius:50%;
  background:var(--verde); flex:none;
}

/* ---------- Bloques ---------- */
.pregunta{
  background:var(--verde-claro); border-radius:16px; padding:1rem 1.15rem; margin:.2rem 0 .6rem;
}
.pregunta .titulo{
  font-size:.7rem; font-weight:600; letter-spacing:.1em; text-transform:uppercase;
  color:var(--verde-oscuro); margin-bottom:.35rem;
}
.pregunta .texto{ font-size:.97rem; line-height:1.45; color:var(--verde-oscuro); }

.seccion{
  font-size:.72rem; font-weight:600; letter-spacing:.11em; text-transform:uppercase;
  color:var(--tenue); margin:1.4rem 0 .55rem;
}
.nota{ font-size:.79rem; color:var(--tenue); margin:.3rem 0 .5rem; }
.separa{ height:1px; background:var(--linea); margin:2rem 0 1.2rem; }

/* ---------- Transiciones ---------- */
@keyframes surgir{ from{ opacity:0; transform:translateY(14px);} to{ opacity:1; transform:none;} }
@keyframes entrar-lado{ from{ opacity:0; transform:translateX(18px);} to{ opacity:1; transform:none;} }
.panel-izq{ animation:surgir .45s ease both; }
.panel-der{ animation:entrar-lado .45s cubic-bezier(.22,.9,.3,1) both; }
.consulta, .pregunta{ animation:surgir .3s ease both; }
@media (prefers-reduced-motion:reduce){
  .portada,.panel-izq,.panel-der,.consulta,.pregunta{ animation:none; }
}

/* ---------- Controles ---------- */
/* Streamlit escribe "Press Enter to submit form" encima del marcador */
div[data-testid="InputInstructions"]{ display:none !important; }

/* El marco del campo lo pinta BaseWeb, no el input */
div[data-baseweb="input"], div[data-baseweb="base-input"]{
  background:#fff !important; border:none !important; border-radius:26px !important;
  box-shadow:0 1px 3px rgba(27,43,51,.07) !important;
}
div[data-baseweb="textarea"], div[data-baseweb="base-input"] textarea{
  background:#fff !important; border:none !important; border-radius:20px !important;
  box-shadow:0 1px 3px rgba(27,43,51,.07) !important;
}
/* El rojo por defecto de Streamlit sale por el borde del contenedor */
div[data-baseweb="input"], div[data-baseweb="textarea"],
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within,
div[data-baseweb="input"]:hover, div[data-baseweb="textarea"]:hover{
  border-color:transparent !important; outline:none !important;
}
div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within{
  box-shadow:0 0 0 2px var(--verde) !important;
}
div[data-testid="stTextArea"] textarea{
  font-family:Figtree,sans-serif !important; font-size:1rem !important;
  line-height:1.5 !important; color:var(--tinta) !important;
  padding:.85rem 1.15rem !important; resize:none !important;
}
div[data-testid="stTextArea"] textarea::placeholder{ color:var(--tenue) !important; }
div[data-testid="stTextArea"] textarea:focus{ box-shadow:none !important; }

div[data-testid="stTextInput"] input{
  background:#fff !important; border:none !important; border-radius:30px !important;
  padding:1.1rem 1.5rem !important; font-size:1.02rem !important; color:var(--tinta) !important;
  box-shadow:none !important; height:auto !important;
}
div[data-testid="stTextInput"] input:focus{ box-shadow:none !important; }
div[data-testid="stTextInput"] input:focus{
  box-shadow:0 0 0 2px var(--verde) !important;
}
div[data-testid="stTextInput"] input::placeholder{ color:var(--tenue) !important; }

.stButton button, .stFormSubmitButton button{
  border-radius:22px; border:none; background:#fff; color:var(--humo);
  font-family:Figtree,sans-serif; font-size:.86rem; font-weight:500; padding:.5rem 1.1rem;
  box-shadow:0 1px 2px rgba(27,43,51,.06); transition:all .16s ease;
}
.stButton button:hover, .stFormSubmitButton button:hover{
  background:var(--verde); color:#fff; box-shadow:0 2px 8px rgba(14,124,107,.25);
}
.stFormSubmitButton button{ background:var(--verde); color:#fff; font-weight:600; }
.stFormSubmitButton button:hover{ background:var(--verde-oscuro); color:#fff; }

div[data-testid="stProgress"]{ margin:0 0 1rem; }
div[data-testid="stProgress"] p{
  font-family:Figtree,sans-serif !important; font-size:.72rem !important; font-weight:600;
  letter-spacing:.1em; text-transform:uppercase; color:var(--tenue) !important;
}
div[data-testid="stProgress"] div[role="progressbar"] > div{ background-color:var(--linea); }
div[data-testid="stProgress"] div[role="progressbar"] > div > div{
  background-color:var(--verde) !important; background-image:none !important;
}
.st-key-reinicio button{
  width:46px !important; height:46px !important; border-radius:50% !important;
  padding:0 !important; font-size:1.25rem !important; line-height:1 !important;
  color:var(--humo) !important; background:#fff !important;
  box-shadow:0 1px 3px rgba(27,43,51,.09) !important;
}
.st-key-reinicio button:hover{
  background:var(--verde) !important; color:#fff !important; transform:rotate(-90deg);
}
.st-key-reinicio button p{ font-size:1.25rem !important; }
.reinicio-pie{
  display:flex; align-items:center; gap:.7rem; margin-top:1.6rem;
  font-size:.8rem; color:var(--tenue);
}

div[data-testid="stExpander"]{ border:none; background:transparent; }
div[data-testid="stExpander"] summary{ font-size:.84rem; color:var(--humo); }
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
    "poner", "pongo", "ponia", "hacer", "hago", "llevar", "llevaba", "dar",
    "daba", "estar", "estaba", "ser", "era", "tenido", "hecho", "todo", "toda",
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
    "pladur": "colocadores prefabricados ligeros escayolistas tabiques construccion",
    "escayolista": "escayolistas prefabricados ligeros construccion",
    "ferrallista": "ferrallistas armaduras hormigon construccion",
    "gruista": "operadores grua",
    "encofrador": "encofradores hormigon construccion",
    "solador": "soladores alicatadores pavimentos",
    "alicatador": "alicatadores soladores revestimientos",
    "matarife": "matarifes matanza carniceros",
    "camillero": "celadores sanitarios auxiliares",
    "reponedora": "reponedores comercio",

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


# ---------------------------------------------------------------------------
# DICCIONARIO COMPARTIDO
# Los términos que la IA traduce sobre la marcha se guardan en un Gist de
# GitHub, de modo que lo que descubre una persona lo aprovechan todas.
# Es opcional: sin GIST_ID y GITHUB_TOKEN en los Secrets, la app funciona
# igual, pero cada sesión empieza de cero.
# ---------------------------------------------------------------------------

ARCHIVO_GIST = "lexico.json"


def _credenciales():
    gist = st.secrets.get("GIST_ID") or os.environ.get("GIST_ID")
    token = st.secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return (gist, token) if gist and token else (None, None)


def _peticion(url, token, datos=None, metodo="GET"):
    cuerpo = json.dumps(datos).encode("utf-8") if datos is not None else None
    p = urllib.request.Request(url, data=cuerpo, method=metodo)
    p.add_header("Authorization", f"Bearer {token}")
    p.add_header("Accept", "application/vnd.github+json")
    if cuerpo:
        p.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(p, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


@st.cache_data(ttl=300, show_spinner=False)
def lexico_compartido():
    """Términos que ya han aprendido otras personas. Se refresca cada 5 min."""
    gist, token = _credenciales()
    if not gist:
        return {}
    try:
        datos = _peticion(f"https://api.github.com/gists/{gist}", token)
        contenido = datos["files"][ARCHIVO_GIST]["content"]
        return {str(k): str(v) for k, v in json.loads(contenido).items()}
    except Exception:  # noqa: BLE001  sin conexión o gist vacío
        return {}


def guarda_termino(clave, valor):
    """Añade un término al diccionario compartido, sin pisar lo de los demás."""
    gist, token = _credenciales()
    if not gist:
        return False
    try:
        actual = dict(lexico_compartido())
        if actual.get(clave) == valor:
            return True
        actual[clave] = valor
        _peticion(
            f"https://api.github.com/gists/{gist}", token,
            datos={"files": {ARCHIVO_GIST: {
                "content": json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True)
            }}},
            metodo="PATCH",
        )
        lexico_compartido.clear()      # que el resto lo vea en el próximo refresco
        return True
    except Exception:  # noqa: BLE001
        return False


def prueba_gist():
    """Escribe y vuelve a leer un término. Devuelve (correcto, explicación)."""
    gist, token = _credenciales()
    if not gist:
        return False, "No hay GIST_ID o GITHUB_TOKEN en los Secrets."

    marca = f"_prueba_{int(time.time())}"
    try:
        actual = dict(lexico_compartido())
        antes = len(actual)
        actual[marca] = "comprobacion"
        _peticion(
            f"https://api.github.com/gists/{gist}", token,
            datos={"files": {ARCHIVO_GIST: {
                "content": json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True)
            }}},
            metodo="PATCH",
        )
    except urllib.error.HTTPError as e:
        pistas = {
            401: "el token no vale o está revocado",
            403: "al token le falta el permiso «gist»",
            404: "el GIST_ID no existe o no es tuyo",
        }
        return False, f"Al escribir: {e.code}, {pistas.get(e.code, 'error de GitHub')}."
    except Exception as e:  # noqa: BLE001
        return False, f"Al escribir: {type(e).__name__}: {e}"

    lexico_compartido.clear()
    try:
        vuelta = lexico_compartido()
    except Exception as e:  # noqa: BLE001
        return False, f"Al releer: {type(e).__name__}: {e}"

    if marca not in vuelta:
        return False, "Se escribió, pero al releer no aparece. Revisa el nombre del archivo."

    # Limpieza: se retira la marca de prueba
    del vuelta[marca]
    try:
        _peticion(
            f"https://api.github.com/gists/{gist}", token,
            datos={"files": {ARCHIVO_GIST: {
                "content": json.dumps(vuelta, ensure_ascii=False, indent=1, sort_keys=True)
            }}},
            metodo="PATCH",
        )
        lexico_compartido.clear()
    except Exception:  # noqa: BLE001
        pass

    return True, f"Escritura y lectura correctas. {antes} términos guardados."


def diccionario():
    """Los sinónimos del código más lo aprendido por todo el equipo."""
    fusion = dict(lexico_compartido())
    fusion.update(SINONIMOS)           # lo fijado a mano manda
    return fusion


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

    ampliado = {}
    if os.path.exists(AMPLIADO):
        with open(AMPLIADO, "r", encoding="utf-8") as f:
            for linea in f:
                if ":" in linea:
                    cod, terms = linea.split(":", 1)
                    ampliado[cod.strip()] = terms.strip()

    registros, inv, inv_raiz = [], defaultdict(list), defaultdict(list)
    inv_extra = defaultdict(list)
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
            propias = {raiz(t) for t in tokens}
            sueltas = {
                raiz(t)
                for t in re.findall(r"\w+", normaliza(ampliado.get(codigo.strip(), "")))
                if len(t) > 2 and t not in VACIAS
            }
            registros.append({
                "codigo": codigo.strip(),
                "denom": denom.strip(),
                "palabras": set(tokens),
                "raices": propias,
                "cabeza": {raiz(t) for t in tokens[:3]},
                "extra": sueltas - propias,
            })

    n = max(1, len(registros))
    for i, r in enumerate(registros):
        for w in r["palabras"]:
            inv[w].append(i)
        for w in r["raices"]:
            inv_raiz[w].append(i)
        for w in r["extra"]:
            inv_extra[w].append(i)

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
        "inv_extra": inv_extra,
        "idf_extra": {w: math.log(1 + n / len(ix)) for w, ix in inv_extra.items()},
        "ampliado": len(ampliado),
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
        if w in IDX["inv"] or r in IDX["inv_raiz"] or r in IDX["inv_extra"]:
            continue
        if any(v.startswith(r) or r.startswith(v) for v in IDX["vocab_raiz"] if len(v) > 3):
            continue
        vocabulario = diccionario()
        if w in vocabulario or any(w in c.split() for c in vocabulario if " " in c):
            continue
        fuera.append(w)
    return fuera


def busca(consulta, tope=20):
    q = normaliza(consulta)
    terminos = {}
    for w in re.findall(r"\w+", q):
        if len(w) > 2 and w not in VACIAS:
            terminos[w] = 1.0
    palabras_q = set(re.findall(r"\w+", q))
    for clave, expansion in diccionario().items():
        # clave de una palabra: coincidencia exacta ("ele" no debe saltar con
        # "elementos"). Clave de varias: basta con que aparezca la expresión.
        if (clave in palabras_q) if " " not in clave else (clave in q):
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
        if r in IDX["inv_extra"]:                             # vocabulario coloquial
            encontrado = True
            k = IDX["idf_extra"][r] * peso * 1.6
            for i in IDX["inv_extra"][r]:
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
        if not genai:
            return None
        try:
            return genai.Client(
                api_key=clave,
                http_options=types.HttpOptions(timeout=ESPERA_MAXIMA * 1000),
            )
        except Exception:  # noqa: BLE001  SDK antiguo sin http_options
            return genai.Client(api_key=clave)
    if not OpenAI:
        return None
    return OpenAI(api_key=clave, base_url=AJUSTES["url"], timeout=ESPERA_MAXIMA)


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
        max_output_tokens=2048,
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
        max_tokens=2048,
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


TRADUCTOR = """Eres experto en la Clasificación Nacional de Ocupaciones (CNO)
y en el catálogo de ocupaciones del SEPE.

Recibes jerga de oficio, marcas comerciales o nombres coloquiales.
Devuelve SOLO palabras sueltas separadas por espacios, entre 8 y 14. Empieza por
las palabras que compondrían la DENOMINACIÓN OFICIAL de esa ocupación en la
clasificación y sigue con materiales, herramientas y tareas.
Sin comas, sin frases, sin explicaciones, sin mayúsculas.

Entrada: kelly
Salida: camareros piso hosteleria limpieza habitaciones hoteles alojamiento

Entrada: ferrallista
Salida: ferrallistas armaduras hormigon armado construccion montadores hierro obra

Entrada: teleco de campo
Salida: instaladores reparadores equipos telecomunicaciones lineas antenas redes
"""


def pregunta_corta(cli, sistema, prompt, maximo=2048):
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
    """Convierte jerga en vocabulario del catálogo. Devuelve (términos, error)."""
    clave = " ".join(sorted(palabras))
    lexico = st.session_state["lexico"]
    if clave in lexico:
        return lexico[clave], ""
    try:
        bruto = pregunta_corta(
            cli, TRADUCTOR,
            f"Entrada: {clave}\nContexto en el que aparece: {contexto}",
        )
    except Exception as e:  # noqa: BLE001
        return "", f"Traducción de «{clave}»: {type(e).__name__}: {e}"

    limpio = " ".join(re.findall(r"[a-zñáéíóúü]+", normaliza(bruto))[:14])
    if not limpio:
        return "", f"Traducción de «{clave}»: el modelo devolvió una respuesta vacía."
    lexico[clave] = limpio
    # No se guarda ahora: publicarlo son dos viajes a GitHub y el usuario
    # estaría esperando. Se encola y se envía cuando ya ve el resultado.
    st.session_state.setdefault("por_guardar", []).append((clave, limpio))
    return limpio, ""


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

def bajar():
    """Lleva la vista al final una sola vez, y se aparta si el usuario se mueve.

    Streamlit no desplaza solo. El desplazamiento es instantáneo a propósito:
    una animación larga compite con el usuario si decide subir mientras tanto.
    """
    components.html(
        f"""
        <script>
          (function () {{
            const marca = "{time.time()}";
            const ventana = window.parent;
            const doc = ventana.document;
            let cancelado = false;

            // Cualquier gesto del usuario tiene prioridad sobre el automatismo
            const parar = function () {{ cancelado = true; }};
            ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (ev) {{
              ventana.addEventListener(ev, parar, {{ once: true, passive: true }});
            }});

            setTimeout(function () {{
              if (cancelado) return;
              const zonas = [
                doc.querySelector('[data-testid="stMain"]'),
                doc.querySelector('section.main'),
                doc.querySelector('[data-testid="stAppViewContainer"] > section'),
                doc.scrollingElement,
              ];
              for (const z of zonas) {{
                if (z && z.scrollHeight > z.clientHeight + 40) {{
                  z.scrollTo({{ top: z.scrollHeight, behavior: "auto" }});
                  return;                       // solo una zona, sin peleas
                }}
              }}
            }}, 120);
          }})();
        </script>
        """,
        height=0,
    )


ESTILO_TARJETAS = """
@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;600;700&display=swap');
*{ box-sizing:border-box; }
body{
  margin:0; background:transparent; font-family:Figtree,system-ui,sans-serif;
  --tinta:#1B2B33; --verde:#0E7C6B; --verde-claro:#D8EFE9; --verde-oscuro:#0B5A4E;
  --humo:#6E818A; --tenue:#9AAAB2; --gris:#E4EAED; --gris-texto:#3F5560;
  color:var(--tinta);
}
.tarjeta{
  background:#fff; border-radius:16px; padding:.9rem 1.05rem; margin-bottom:.6rem;
  box-shadow:0 1px 3px rgba(27,43,51,.07);
  animation:entrar .34s cubic-bezier(.22,.9,.3,1) both;
}
.tarjeta:last-child{ margin-bottom:0; }
.tarjeta:nth-of-type(2){ animation-delay:.05s; }
.tarjeta:nth-of-type(3){ animation-delay:.1s; }
@keyframes entrar{ from{ opacity:0; transform:translateY(9px);} to{ opacity:1; transform:none;} }
@media (prefers-reduced-motion:reduce){ .tarjeta{ animation:none; } }

.fila{ display:flex; align-items:center; gap:9px; flex-wrap:wrap; }
.orden{ display:none; }
.codigo{
  font-size:1.28rem; font-weight:700; letter-spacing:.045em; color:var(--verde);
  font-variant-numeric:tabular-nums;
}
.tarjeta.top .codigo{ color:var(--verde); }
.tarjeta:not(.top) .codigo{ color:#43606B; }
.copiar{
  font-family:Figtree,sans-serif; font-size:.72rem; font-weight:500;
  color:var(--humo); background:#F1F5F7; border:none; border-radius:20px;
  padding:.25rem .7rem; cursor:pointer; transition:all .15s ease; white-space:nowrap;
}
.copiar:hover{ background:var(--verde); color:#fff; }
.copiar.hecho{ background:var(--verde-oscuro); color:#fff; }
.denominacion{ font-size:.94rem; font-weight:600; line-height:1.35; margin:.4rem 0 .2rem; }
.motivo{ font-size:.85rem; color:var(--humo); line-height:1.45; }
.etiqueta{
  display:inline-block; font-size:.7rem; font-weight:600; padding:.2rem .6rem;
  border-radius:20px; background:var(--gris); color:var(--gris-texto);
}
.etiqueta.destacada{ background:var(--verde-claro); color:var(--verde-oscuro); }
"""

GUION_COPIAR = """
function copiar(boton, codigo){
  const listo = function(){
    const antes = boton.textContent;
    boton.textContent = 'Copiado';
    boton.classList.add('hecho');
    setTimeout(function(){ boton.textContent = antes; boton.classList.remove('hecho'); }, 1400);
  };
  if (navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(codigo).then(listo).catch(function(){ viejo(codigo, listo); });
  } else {
    viejo(codigo, listo);
  }
}
function viejo(codigo, listo){
  const caja = document.createElement('textarea');
  caja.value = codigo;
  caja.style.position = 'fixed';
  caja.style.opacity = '0';
  document.body.appendChild(caja);
  caja.select();
  try { document.execCommand('copy'); listo(); } catch (e) {}
  document.body.removeChild(caja);
}
function alto(){
  parent.postMessage(
    {type:'streamlit:setFrameHeight', height: document.documentElement.scrollHeight + 4},
    '*'
  );
}
document.querySelectorAll('.copiar').forEach(function (b) {
  b.addEventListener('click', function () { copiar(b, b.dataset.cod); });
});
window.addEventListener('load', alto);
setTimeout(alto, 60); setTimeout(alto, 400); setTimeout(alto, 1200);
"""


def pinta_tarjetas(ocupaciones):
    """Todas las fichas de un resultado, en un solo marco con botón de copiar."""
    if not ocupaciones:
        return

    trozos = []
    for i, o in enumerate(ocupaciones, 1):
        clase = "tarjeta top" if i == 1 else "tarjeta"
        etiqueta = "etiqueta destacada" if i == 1 else "etiqueta"
        motivo = f'<div class="motivo">{o["motivo"]}</div>' if o.get("motivo") else ""
        trozos.append(
            f'<div class="{clase}">'
            f'  <div class="fila">'
            f'    <span class="orden">{i:02d}</span>'
            f'    <span class="codigo">{o["codigo"]}</span>'
            f'    <button class="copiar" data-cod="{o["codigo"]}">Copiar</button>'
            f'    <span class="{etiqueta}">Nivel {o["nivel"]} &middot; {o["nivel_texto"]}</span>'
            f'  </div>'
            f'  <div class="denominacion">{o["denominacion"]}</div>'
            f'  {motivo}'
            f'</div>'
        )

    # Altura del marco. Medida sobre el diseño real, no a ojo:
    #   relleno 36,8 + bordes 2 + fila del código 27 + márgenes del título 16
    #   + 22 por línea de denominación + 21 si hay motivo + 11,2 de separación.
    def mide(o):
        lineas = 1 + len(o["denominacion"]) // 58
        return 104 + 22 * (lineas - 1) + (21 if o.get("motivo") else 0) + 11

    estimada = sum(mide(o) for o in ocupaciones) - 11 + 6

    components.html(
        f"<style>{ESTILO_TARJETAS}</style>{''.join(trozos)}"
        f"<script>{GUION_COPIAR}</script>",
        height=estimada,
    )


def pinta_resultado(payload, estado=None, avance=0.06, interactivo=False, consulta=""):
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

    pinta_tarjetas(ocupaciones)

    if payload.get("pregunta"):
        st.markdown(
            f'<div class="pregunta"><div class="titulo">Pregunta para la persona</div>'
            f'<div class="texto">{payload["pregunta"]}</div></div>',
            unsafe_allow_html=True,
        )
        if interactivo:
            si, no, _ = st.columns([1, 1, 3])
            if si.button("Sí", key="resp_si", use_container_width=True):
                st.session_state["respuesta"] = (consulta, payload["pregunta"], True)
                st.rerun()
            if no.button("No", key="resp_no", use_container_width=True):
                st.session_state["respuesta"] = (consulta, payload["pregunta"], False)
                st.rerun()

    if estado:
        return

    if payload.get("interpretado"):
        origen, destino = payload["interpretado"]
        st.markdown(
            f'<div class="nota">Interpretado <b>{origen}</b> como: {destino}</div>',
            unsafe_allow_html=True,
        )

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


def resuelve(texto, zona, usar_ia=True, contexto="", busqueda=None):
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

    encontrados = busca(busqueda or texto, tope=N_CANDIDATOS)
    if not encontrados:
        payload = {"ocupaciones": []}
        with zona.container():
            pinta_resultado(payload)
        return payload

    memoria = st.session_state["cache"]
    clave = normaliza(texto + contexto)
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
    interpretado, aviso = None, ""
    jerga = desconocidas(texto)

    # Traducir cuesta un viaje extra al modelo. Solo merece la pena si la jerga
    # es una parte importante de la consulta; si es una palabra suelta entre
    # otras que el catálogo sí entiende, la búsqueda ya suele ser buena.
    contenido = [
        w for w in re.findall(r"\w+", normaliza(texto))
        if len(w) > 3 and w not in VACIAS
    ]
    pesa = bool(jerga) and len(jerga) >= max(1, len(contenido) * 0.5)

    if jerga and pesa:
        with zona.container():
            pinta_resultado(provisional, estado="Interpretando el oficio")
        extra, error = traduce_jerga(cli, jerga, texto)
        if error:
            aviso = error
            provisional["fallo"] = error
        if extra:
            interpretado = (" ".join(jerga), extra)
            mejores = busca(f"{texto} {extra}", tope=N_CANDIDATOS + 6)
            if mejores:
                encontrados = mejores
                provisional = {
                    "ocupaciones": _basica(encontrados),
                    "otras": [(c, d) for _, c, d in encontrados[5:12]],
                    "interpretado": interpretado,
                }
                with zona.container():
                    pinta_resultado(provisional, estado="Afinando el resultado")

    bruto, mostradas, avance = "", 0, 0.10
    arranque = time.perf_counter()
    try:
        peticion = texto + contexto
        for trozo in flujo_modelo(cli, peticion, "\n".join(f"{c}:{d}" for _, c, d in encontrados)):
            bruto += trozo
            transcurrido = time.perf_counter() - arranque
            if transcurrido > ESPERA_MAXIMA:
                raise TimeoutError(
                    f"El modelo ha tardado más de {ESPERA_MAXIMA} segundos."
                )
            listas, _ = verifica(objetos_parciales(bruto))
            # avanza con lo que llega y, si no llega nada, con el reloj
            nuevo = max(
                0.10 + 0.17 * len(listas),
                min(0.10 + transcurrido / (ESPERA_MAXIMA * 1.6), 0.9),
            )
            if len(listas) > mostradas or nuevo - avance > 0.03:
                mostradas, avance = len(listas), nuevo
                with zona.container():
                    pinta_resultado(
                        {"ocupaciones": listas} if listas else provisional,
                        estado="Afinando el resultado",
                        avance=avance,
                    )
    except Exception as e:  # noqa: BLE001
        zona.empty()
        provisional["fallo"] = f"{type(e).__name__}: {e}"
        pinta_resultado(provisional)
        return provisional

    payload = interpreta(bruto)
    if interpretado:
        payload["interpretado"] = interpretado
    if aviso:
        payload["fallo"] = aviso
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

st.session_state.setdefault("actual", None)
st.session_state.setdefault("registro", [])
st.session_state.setdefault("pendiente", None)
st.session_state.setdefault("usar_ia", True)
st.session_state.setdefault("cache", {})
st.session_state.setdefault("lexico", {})
st.session_state.setdefault("modelo_ok", 0)
st.session_state.setdefault("respuesta", None)
st.session_state.setdefault("por_guardar", [])

EJEMPLOS = [
    "Camarera de barra en cafetería",
    "Reparto en moto para Glovo",
    "Auxiliar administrativa: facturación",
    "Carretillero en almacén",
]


def caja_busqueda(clave, etiqueta="Buscar"):
    """Cuadro de texto con envío al pulsar Enter."""
    with st.form(clave, clear_on_submit=True, border=False):
        texto = st.text_input(
            "Consulta", label_visibility="collapsed",
            placeholder=(
                "Describe el puesto: qué hacía, dónde y con qué. "
                "También admite un código de 8 cifras."
            ),
        )
        enviado = st.form_submit_button(etiqueta, use_container_width=True)
    return texto.strip() if (enviado and texto.strip()) else None


def panel_ajustes():
    with st.popover("Ajustes", use_container_width=True):
        st.session_state["usar_ia"] = st.toggle(
            "Afinar con IA", value=st.session_state["usar_ia"],
            help="Desactivado, muestra las coincidencias del catálogo al instante.",
        )
        if st.session_state["registro"]:
            buffer = io.StringIO()
            escritor = csv.writer(buffer, delimiter=";")
            escritor.writerow(["consulta", "codigos"])
            for fila in st.session_state["registro"]:
                escritor.writerow(fila)
            st.download_button(
                "Descargar sesión", buffer.getvalue().encode("utf-8-sig"),
                file_name="codificaciones.csv", mime="text/csv",
                use_container_width=True,
            )

        if st.button("Probar la conexión con la IA", use_container_width=True):
            prueba = cliente()
            if prueba is None:
                st.error(f"No hay clave {AJUSTES['clave']} en los Secrets.")
            else:
                try:
                    eco = pregunta_corta(
                        prueba, "Responde únicamente con la palabra ok.",
                        "ok", maximo=2048,
                    )
                    st.success(f"{modelo_actual()}: {eco[:60] or '(respuesta vacía)'}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"{type(e).__name__}: {e}")

        compartido = lexico_compartido()
        gist_activo, _ = _credenciales()
        if gist_activo:
            st.markdown("**Diccionario compartido**")
            st.caption(
                f"{len(compartido)} términos aprendidos entre todas. "
                "Se guardan solos al aparecer."
            )
            if st.button("Comprobar que guarda", use_container_width=True):
                correcto, detalle = prueba_gist()
                (st.success if correcto else st.error)(detalle)
        elif st.session_state.get("lexico"):
            st.markdown("**Términos aprendidos**")
            st.caption("Solo en esta sesión. Pégalos en SINONIMOS para conservarlos.")

        vistos = {**compartido, **st.session_state.get("lexico", {})}
        if vistos:
            st.code(
                "\n".join(f'"{k}": "{v}",' for k, v in sorted(vistos.items()) if v),
                language=None,
            )

        st.caption(
            f"{len(IDX['registros'])} ocupaciones cargadas"
            + (f", {IDX['ampliado']} con vocabulario ampliado. " if IDX.get("ampliado")
               else " (sin vocabulario ampliado). ")
            + "Describe solo el puesto: sin nombres, DNI ni datos identificativos."
        )


# ---------------------------------------------------------------------------
# Respuesta a una pregunta de desambiguación
# ---------------------------------------------------------------------------

entrada, contexto, busqueda, rotulo = None, "", None, None

respuesta = st.session_state.pop("respuesta", None)
if respuesta:
    original, pregunta, afirmativa = respuesta
    entrada = original
    rotulo = f"{original}  ·  {'Sí' if afirmativa else 'No'}"
    contexto = (
        f"\n\nACLARACIÓN: a la pregunta «{pregunta}» la persona responde "
        f"{'SÍ' if afirmativa else 'NO'}. Ten en cuenta esta respuesta y no "
        f"vuelvas a plantear la misma duda."
    )
    if afirmativa:
        busqueda = f"{original} {pregunta}"

if not entrada:
    entrada = st.session_state.pop("pendiente", None)

# ---------------------------------------------------------------------------
# Portada: buscador centrado mientras no hay resultados
# ---------------------------------------------------------------------------

portada = st.empty()

if not st.session_state["actual"] and not entrada:
    with portada.container():
        st.markdown(
            '<div class="portada"><h1>Codificador de <span>ocupaciones</span></h1>'
            '<p>Describe el puesto y obtén los códigos oficiales del catálogo SISPE.</p></div>',
            unsafe_allow_html=True,
        )
        _, centro, _ = st.columns([0.5, 4, 0.5])
        with centro:
            entrada = caja_busqueda("inicio", "Buscar")
            # Si ya hay consulta, no se crean más controles: la vista de
            # trabajo los volvería a crear y Streamlit rechaza los duplicados.
            if not entrada:
                st.markdown('<div class="seccion">Prueba con</div>', unsafe_allow_html=True)
                for fila in (EJEMPLOS[:2], EJEMPLOS[2:]):
                    cols = st.columns(len(fila))
                    for col, ej in zip(cols, fila):
                        if col.button(ej, use_container_width=True, key=f"ej_{ej}"):
                            st.session_state["pendiente"] = ej
                            st.rerun()
                st.write("")
                _, ajus, _ = st.columns([1, 1, 1])
                with ajus:
                    panel_ajustes()

    if not entrada:
        st.stop()

    portada.empty()      # se retira la portada y entra la vista de trabajo

# ---------------------------------------------------------------------------
# Vista de trabajo: buscador a la izquierda, resultados a la derecha
# ---------------------------------------------------------------------------

izquierda, derecha = st.columns([1, 1.45], gap="large")

with izquierda:
    if st.button("Codificador de ocupaciones", key="marca", use_container_width=True):
        st.session_state["actual"] = None
        st.rerun()
    st.markdown(
        '<div class="marca-sub">Catálogo SISPE · SilcoiWeb</div>',
        unsafe_allow_html=True,
    )
    nueva_consulta = caja_busqueda("lateral", "Buscar")
    if nueva_consulta and not entrada:
        entrada = nueva_consulta
        contexto, busqueda, rotulo = "", None, None

    st.markdown('<div class="seccion">Prueba con</div>', unsafe_allow_html=True)
    for ej in EJEMPLOS:
        if st.button(ej, use_container_width=True, key=f"lat_{ej}"):
            st.session_state["pendiente"] = ej
            st.rerun()

    st.markdown('<div class="separa"></div>', unsafe_allow_html=True)
    panel_ajustes()

with derecha:
    st.markdown('<div class="panel-der"></div>', unsafe_allow_html=True)

    if entrada:
        st.markdown(
            f'<div class="consulta">{rotulo or entrada}</div>', unsafe_allow_html=True
        )
        zona = st.empty()
        payload = resuelve(
            entrada, zona,
            usar_ia=st.session_state["usar_ia"],
            contexto=contexto, busqueda=busqueda,
        )
        st.session_state["actual"] = (rotulo or entrada, payload)
        st.session_state["registro"].append((
            rotulo or entrada,
            " | ".join(o["codigo"] for o in payload.get("ocupaciones", [])),
        ))
        st.rerun()

    elif st.session_state["actual"]:
        consulta, payload = st.session_state["actual"]
        st.markdown(f'<div class="consulta">{consulta}</div>', unsafe_allow_html=True)
        pinta_resultado(payload, interactivo=True, consulta=consulta)

        st.write("")
        vuelta, texto, _ = st.columns([1, 4, 2])
        with vuelta:
            if st.button("↺", key="reinicio", help="Nueva búsqueda"):
                st.session_state["actual"] = None
                st.rerun()
        with texto:
            st.markdown(
                '<div class="reinicio-pie">Empezar una búsqueda nueva</div>',
                unsafe_allow_html=True,
            )

# Con el resultado ya en pantalla, se publica lo aprendido para el resto
for clave, valor in st.session_state.pop("por_guardar", []):
    guarda_termino(clave, valor)

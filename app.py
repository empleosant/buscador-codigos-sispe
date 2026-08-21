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
N_CANDIDATOS = 16
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
@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700&display=swap');

:root{
  --negro:#0A0A0A;
  --rojo:#D1122E;          /* rojo corporativo; ajústalo si tienes el exacto */
  --rojo-oscuro:#A50E24;
  --texto:#1A1A1A;
  --suave:#6B6B6B;
  --tenue:#9A9A9A;
  --linea:#D8D8D8;
  --gris:#F2F2F2;
}

.stApp{ background:#fff; }
html,body,[class*="css"],.stMarkdown{
  font-family:'Libre Franklin',system-ui,sans-serif; color:var(--texto);
}
.block-container{ padding:0 0 1.5rem; max-width:1180px; }
#MainMenu, footer, header[data-testid="stHeader"]{ visibility:hidden; height:0; }
[data-testid="stHeaderActionElements"]{ display:none !important; }
h1 > a, h2 > a, h3 > a, .stMarkdown a.anchor-link{ display:none !important; }
div[data-testid="InputInstructions"]{ display:none !important; }

/* ---------- Banda de cabecera ---------- */
.st-key-cabecera{
  background:var(--negro); padding:1.15rem 2rem 1.25rem; margin-bottom:1.1rem;
}
.rotulo{
  color:#8A8A8A; font-size:.62rem; font-weight:600; letter-spacing:.16em;
  text-transform:uppercase; margin:0 0 .1rem;
}
.rotulo span{ color:var(--rojo); }

/* El título es un botón: vuelve al inicio y limpia el resultado */
.st-key-marca button{
  background:transparent !important; border:none !important; box-shadow:none !important;
  padding:0 !important; justify-content:flex-start !important; margin-bottom:.55rem;
}
.st-key-marca button p{
  color:#fff !important; font-size:1.5rem !important; font-weight:700 !important;
  letter-spacing:-.025em; margin:0 !important; text-align:left !important;
  border-bottom:2px solid transparent; transition:border-color .15s ease;
}
.st-key-marca button:hover p{ border-bottom-color:var(--rojo); }

/* ---------- Campo de búsqueda: recto y pegado al botón ---------- */
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="base-input"],
.st-key-cabecera div[data-testid="stTextInput"] input,
.st-key-cabecera div[data-testid="stTextInput"] input:focus,
.st-key-cabecera div[data-testid="stTextInput"] input:hover{
  background:transparent !important; border:none !important;
  box-shadow:none !important; outline:none !important;
}
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="input"]{
  background:#fff !important; border:1px solid #fff !important;
  border-radius:0 !important; box-shadow:none !important;
}
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within{
  border-color:var(--rojo) !important; box-shadow:0 0 0 2px var(--rojo) !important;
}
div[data-testid="stTextInput"] input{
  padding:.72rem 1rem !important; font-size:.96rem !important;
  color:var(--texto) !important; font-family:'Libre Franklin',sans-serif !important;
}
div[data-testid="stTextInput"] input::placeholder{ color:var(--tenue) !important; }

/* ---------- Botones ---------- */
.st-key-buscar button{
  background:var(--rojo) !important; color:#fff !important; border:none !important;
  border-radius:0 !important; font-weight:700 !important; font-size:.95rem !important;
  padding:.72rem 1rem !important; min-height:46px !important; letter-spacing:.01em;
  transition:background .15s ease;
}
.st-key-buscar button:hover{ background:var(--rojo-oscuro) !important; }
.st-key-buscar button p{ color:#fff !important; font-weight:700 !important; }

.stButton button{
  background:#fff; color:var(--texto); border:1px solid var(--negro);
  border-radius:0; font-family:'Libre Franklin',sans-serif; font-size:.83rem;
  font-weight:500; padding:.42rem .9rem; transition:all .15s ease;
}
.stButton button:hover{ background:var(--negro); color:#fff; border-color:var(--negro); }

/* ---------- Cuerpo ---------- */
.cuerpo{ padding:0 2rem; }
.consulta{
  font-size:1rem; font-weight:700; letter-spacing:-.015em; color:var(--texto);
  margin:.1rem 0 .8rem; padding-bottom:.5rem; border-bottom:2px solid var(--negro);
}
.seccion{
  font-size:.68rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--suave); margin:1.6rem 0 .7rem;
}
/* La pregunta y sus botones forman un solo bloque */
.st-key-pregunta{
  background:var(--gris); border-left:4px solid var(--negro);
  padding:.85rem 1.15rem 1rem; margin:.3rem 0 .2rem;
}
.pregunta-titulo{
  font-size:.66rem; font-weight:700; letter-spacing:.13em; text-transform:uppercase;
  color:var(--rojo); margin-bottom:.3rem;
}
.pregunta-texto{
  font-size:1rem; line-height:1.4; color:var(--texto); margin-bottom:.75rem;
}
.st-key-pregunta .stButton button{
  background:#fff; border:1px solid var(--negro); font-weight:600;
  padding:.4rem .6rem; min-height:38px;
}
.st-key-pregunta .stButton button:hover{ background:var(--negro); color:#fff; }
.nota{ font-size:.79rem; color:var(--suave); margin:.4rem 0 .5rem; }
.separa{ height:1px; background:var(--linea); margin:1.2rem 0 1rem; }

/* ---------- Progreso ---------- */
div[data-testid="stProgress"]{ margin:0 0 1rem; }
div[data-testid="stProgress"] p{
  font-family:'Libre Franklin',sans-serif !important; font-size:.68rem !important;
  font-weight:700; letter-spacing:.14em; text-transform:uppercase; color:var(--suave) !important;
}
div[data-testid="stProgress"] div[role="progressbar"] > div{ background-color:var(--linea); }
div[data-testid="stProgress"] div[role="progressbar"] > div > div{
  background-color:var(--rojo) !important; background-image:none !important;
}
/* Botón circular de nueva búsqueda */
/* El centrado hay que aplicarlo también a los envoltorios que mete
   Streamlit alrededor del botón, no solo al contenedor exterior. */
.st-key-reinicio,
.st-key-reinicio > div,
.st-key-reinicio [data-testid="stTooltipHoverTarget"],
.st-key-reinicio [data-testid="stElementToolbar"]{
  display:flex !important; justify-content:center !important; width:100% !important;
}
.st-key-reinicio button{
  width:72px !important; height:72px !important; min-height:72px !important;
  border-radius:50% !important; padding:0 !important;
  border:2px solid var(--negro) !important; background:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:all .22s ease;
}
.st-key-reinicio button p{
  font-size:2rem !important; line-height:1 !important; margin:0 !important;
  color:var(--negro) !important;
}
.st-key-reinicio button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important;
  transform:rotate(-90deg);
}
.st-key-reinicio button:hover p{ color:#fff !important; }
.pie-nueva{
  text-align:center; font-size:.85rem; color:var(--suave); margin:.6rem 0 0;
}

/* Ajustes: círculo blanco al lado del botón Buscar */
.st-key-ajustes button{
  width:46px !important; height:46px !important; min-height:46px !important;
  border-radius:50% !important; padding:0 !important;
  background:#fff !important; border:1px solid #fff !important; color:var(--negro) !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:all .18s ease;
}
.st-key-ajustes button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important; color:#fff !important;
}
.st-key-ajustes button span,
.st-key-ajustes button p{ font-size:1.4rem !important; margin:0 !important; }

div[data-testid="stExpander"]{ border:none; background:transparent; }
div[data-testid="stExpander"] summary{ font-size:.85rem; color:var(--suave); }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# CATALOGO
# ---------------------------------------------------------------------------

def normaliza(t):
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    ).lower().strip()


# ---------------------------------------------------------------------------
# VOCABULARIO
# El buscador tiene cuatro capas de vocabulario, de más estable a más viva:
#   1. vocabulario.json        palabras vacías y sinónimos base (este archivo)
#   2. terminos_ampliados.txt  jerga de cada ocupación (lo genera enriquecer.py)
#   3. lexico.json   (Gist)    jerga traducida por la IA sobre la marcha
#   4. refuerzos.json (Gist)   correcciones de orden aprendidas con el uso
# Ninguna se toca desde app.py: este archivo solo contiene el motor.
# ---------------------------------------------------------------------------

VOCABULARIO = "vocabulario.json"

# Mínimo imprescindible por si falta el archivo: la app arranca igualmente.
VACIAS_MINIMAS = {
    "de", "del", "la", "el", "los", "las", "en", "y", "o", "con", "para",
    "por", "un", "una", "al", "sin", "que", "su", "general", "persona",
    "personas", "dame", "dime", "codigo", "puesto", "trabajo",
}


@st.cache_resource(show_spinner=False)
def carga_vocabulario():
    if os.path.exists(VOCABULARIO):
        try:
            with open(VOCABULARIO, "r", encoding="utf-8") as f:
                datos = json.load(f)
            vacias = {normaliza(w) for w in datos.get("vacias", []) if w}
            sinonimos = {
                normaliza(k): str(v)
                for k, v in (datos.get("sinonimos") or {}).items() if k and v
            }
            if vacias or sinonimos:
                return vacias or set(VACIAS_MINIMAS), sinonimos
        except Exception:  # noqa: BLE001  archivo mal formado
            pass
    return set(VACIAS_MINIMAS), {}


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
ARCHIVO_REFUERZOS = "refuerzos.json"


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
def _lee_gist(archivo):
    gist, token = _credenciales()
    if not gist:
        return {}
    try:
        datos = _peticion(f"https://api.github.com/gists/{gist}", token)
        contenido = datos["files"][archivo]["content"]
        return {str(k): str(v) for k, v in json.loads(contenido).items()}
    except Exception:  # noqa: BLE001  sin conexión, archivo ausente o vacío
        return {}


def _escribe_gist(archivo, datos):
    gist, token = _credenciales()
    _peticion(
        f"https://api.github.com/gists/{gist}", token,
        datos={"files": {archivo: {
            "content": json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True)
        }}},
        metodo="PATCH",
    )
    _lee_gist.clear()


def lexico_compartido():
    """Jerga traducida que ya han aprendido otras personas."""
    return _lee_gist(ARCHIVO_GIST)


def refuerzos_compartidos():
    """Palabras que la práctica ha asociado a una ocupación concreta.

    Corrigen los errores de ORDEN, que son los que el diccionario de jerga
    no ve: palabras que el catálogo sí conoce pero que apuntaban a la
    ocupación equivocada.
    """
    return _lee_gist(ARCHIVO_REFUERZOS)


def guarda_termino(clave, valor):
    """Añade jerga traducida al diccionario compartido."""
    gist, _ = _credenciales()
    if not gist:
        return False
    try:
        actual = dict(lexico_compartido())
        if actual.get(clave) == valor:
            return True
        actual[clave] = valor
        _escribe_gist(ARCHIVO_GIST, actual)
        return True
    except Exception:  # noqa: BLE001
        return False


def guarda_refuerzo(codigo, palabras):
    """Asocia palabras de la consulta a la ocupación que resultó correcta."""
    gist, _ = _credenciales()
    if not gist or codigo not in IDX["por_codigo"]:
        return False
    nuevas = [w for w in palabras if len(w) > 2]
    if not nuevas:
        return False
    try:
        actual = dict(refuerzos_compartidos())
        previas = actual.get(codigo, "").split()
        fusion = list(dict.fromkeys(previas + nuevas))[:24]
        if fusion == previas:
            return True
        actual[codigo] = " ".join(fusion)
        _escribe_gist(ARCHIVO_REFUERZOS, actual)
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

    _lee_gist.clear()
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
        _lee_gist.clear()
    except Exception:  # noqa: BLE001
        pass

    return True, f"Escritura y lectura correctas. {antes} términos guardados."


def diccionario():
    """Los sinónimos del código más lo aprendido por todo el equipo."""
    fusion = dict(lexico_compartido())
    fusion.update(SINONIMOS)           # lo fijado a mano manda
    return fusion


def raiz(w):
    """Lematizador mínimo para español.

    Neutraliza plural, género y el sufijo de agente -or, que es el que
    separaba 'solados' (lo que dice la persona) de 'soladores' (lo que dice
    el catálogo). Sin esa regla, ambas palabras no se encontraban nunca.
    """
    if len(w) > 5 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s"):
        w = w[:-1]
    if len(w) > 5 and w.endswith("or"):      # solador -> solad, montador -> montad
        w = w[:-2]
    if len(w) > 4 and w[-1] in "aoe":
        w = w[:-1]
    return w


VACIAS, SINONIMOS = carga_vocabulario()


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
        "posicion": {r["codigo"]: i for i, r in enumerate(registros)},
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


def busca(consulta, tope=20, grupos=None):
    q = normaliza(consulta)
    terminos = {}
    # Las primeras palabras pesan más: tanto el modelo como las personas
    # ponen delante el nombre del oficio y detrás los complementos.
    contadas, primero = 0, None
    for w in re.findall(r"\w+", q):
        if len(w) > 2 and w not in VACIAS and w not in terminos:
            contadas += 1
            if contadas == 1:
                primero = raiz(w)
            terminos[w] = 1.0 if contadas <= 4 else 0.7
    palabras_q = set(re.findall(r"\w+", q))
    for clave, expansion in diccionario().items():
        # clave de una palabra: coincidencia exacta ("ele" no debe saltar con
        # "elementos"). Clave de varias: basta con que aparezca la expresión.
        if (clave in palabras_q) if " " not in clave else (clave in q):
            for w in re.findall(r"\w+", normaliza(expansion)):
                terminos.setdefault(w, 0.85)
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

    # Refuerzos aprendidos: si la consulta contiene palabras que la práctica
    # ha asociado a una ocupación, esa ocupación sube. Es lo que corrige los
    # errores de orden, y funciona aunque la palabra ya exista en el catálogo.
    stems_consulta = {raiz(w) for w in re.findall(r"\w+", q) if len(w) > 2}
    for codigo, aprendidas in refuerzos_compartidos().items():
        i = IDX["posicion"].get(codigo)
        if i is None:
            continue
        comunes = stems_consulta & {raiz(w) for w in aprendidas.split()}
        if comunes:
            suma(i, 14.0 * len(comunes), next(iter(comunes)))

    n_term = max(1, len(originales))
    n_total = max(1, len({raiz(w) for w in terminos}))
    resultados = []
    for i, valor in puntos.items():
        reg = IDX["registros"][i]
        nucleo = 1.0 + 0.5 * len(cubierto[i] & reg["cabeza"])
        # La primera palabra suele ser el nombre del oficio: si además es la
        # cabeza de la denominación, es casi seguro que es esa ocupación.
        if primero and primero in reg["cabeza"]:
            nucleo *= 1.8
        # La familia profesional que indica el modelo pesa, pero no excluye:
        # una clasificación errónea no debe dejar la búsqueda sin resultados.
        familia = 1.0
        if grupos:
            familia = 1.7 if reg["codigo"][0] in grupos else 0.45

        propios = len(cubierto[i] & originales)
        # premia encajar con varias palabras a la vez, sean propias o expandidas
        cobertura = (
            0.55
            + 0.30 * min(1.0, len(cubierto[i]) / n_total)
            + 0.15 * min(1.0, propios / n_term)
        )
        resultados.append(
            (valor * nucleo * cobertura * familia, reg["codigo"], reg["denom"])
        )
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
2. Los candidatos llegan ordenados por coincidencia de palabras, NO por acierto. Ese orden es solo una pista: elige siempre la ocupación cuya denominación describa la actividad real, aunque esté al final de la lista. Desconfía de las coincidencias de palabra suelta entre sectores distintos (por ejemplo "montar" o "pisos" significan cosas muy diferentes en construcción y en calzado).
3. Devuelve SIEMPRE entre 3 y 5 ocupaciones, aunque dudes. La duda se expresa en "pregunta", nunca acortando la lista.
4. Nivel profesional: 90 aprendices (sin experiencia) / 00 técnicos o sin categoría (estándar con experiencia) / 10 dirección / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.
5. El campo "motivo" explica en menos de 10 palabras por qué encaja, en español con acentuación correcta.
6. No propongas ocupaciones de dirección, jefatura ni mando (niveles 10, 20, 30) salvo que la descripción diga expresamente que dirigía equipos, centros o departamentos.
7. Respeta el entorno de trabajo que indique la descripción: domicilio particular frente a institución, centro o residencia. Si dice "en su casa" o "a domicilio", descarta las ocupaciones que digan "en instituciones".
8. Rellena "pregunta" solo si faltan datos para decidir entre las DOS PRIMERAS ocupaciones de tu lista; si no, déjalo vacío. No preguntes por algo que la persona ya ha dicho ni por algo que solo confirme la primera opción: la respuesta tiene que servir para descartar una de las dos. Requisitos de la pregunta:
   - Se responde con SÍ o con NO. Nunca uses "¿hacía A o hacía B?": quien responde solo tiene dos botones.
   - Va dirigida a la persona atendida y se le va a leer en voz alta. Escríbela en lenguaje llano, con las palabras que usaría cualquiera: nada de terminología del catálogo, ni sustantivos abstractos, ni "realizaba tareas de". Verbos corrientes y cosas concretas.
   - Corta: quince palabras como mucho.
   Mal: "¿Se dedica al pulido de suelos o a otra actividad de construcción?"
   Mal: "¿Realizaba la colocación de baldosas o azulejos en suelos y paredes?"
   Bien: "¿Ponía baldosas o azulejos?"
   Bien: "¿Usaba una máquina para pulir el suelo?"
   Bien: "¿Trabajaba dentro de casas de particulares?"
9. IMPORTANTE. Si ninguna de las candidatas describe con precisión la actividad, rellena "otros_terminos" con entre 6 y 10 palabras sueltas del vocabulario de la clasificación que deberían buscarse en su lugar (el nombre formal del oficio, herramientas, materiales). Se hará una segunda búsqueda con ellas. Si alguna candidata sí encaja, deja "otros_terminos" vacío.

Responde solo con este JSON:
{"ocupaciones":[{"codigo":"12345678","denominacion":"...","nivel":"00","motivo":"..."}],"pregunta":"","otros_terminos":""}
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


INTERPRETE = """Eres experto en el catálogo de ocupaciones del SEPE (CNO).

Lees la descripción de un puesto escrita por un orientador laboral, con las
palabras de la persona atendida, y respondes con el VOCABULARIO OFICIAL que
usaría la clasificación para ese oficio.

Responde SOLO con este JSON:
{"terminos":"...","grupos":"7"}

- "terminos": entre 8 y 14 palabras sueltas separadas por espacios, en
  minúsculas y sin acentos. Empieza por el nombre formal del oficio en plural
  tal y como aparecería en la clasificación y sigue con materiales,
  herramientas y tareas. No repitas las palabras coloquiales de la persona.
- "grupos": uno o dos dígitos separados por espacio, el gran grupo de la CNO
  al que pertenece el oficio:
  1 dirección · 2 técnicos y profesionales científicos · 3 técnicos de apoyo
  4 empleados de oficina · 5 restauración, servicios personales y comercio
  6 agricultura y pesca · 7 artesanos y cualificados de industria y construcción
  8 operadores de instalaciones y maquinaria · 9 ocupaciones elementales

Si el texto es una pregunta, ignora la forma y quédate con el oficio.

Entrada: para una persona que monta suelos
Salida: {"terminos":"soladores alicatadores pavimentos baldosas ceramica gres mortero solados","grupos":"7"}

Entrada: una persona que limpia habitaciones de hotel
Salida: {"terminos":"camareros piso hosteleria habitaciones limpieza alojamiento ropa cama","grupos":"9 5"}

Entrada: dime el codigo de quien pintaba casas
Salida: {"terminos":"pintores empapeladores brocha rodillo esmalte paredes techos","grupos":"7"}
"""


def interpreta_consulta(cli, texto):
    """Traduce la consulta a vocabulario del catálogo antes de buscar.

    Es la diferencia entre elegir bien y elegir entre malos candidatos: sin
    esta pasada, el modelo solo ve la lista que ya ha decidido el buscador.
    """
    clave = normaliza(texto)
    memoria = st.session_state.setdefault("interpretaciones", {})
    if clave in memoria:
        return memoria[clave]
    try:
        bruto = pregunta_corta(cli, INTERPRETE, texto)
    except Exception:  # noqa: BLE001
        return "", ()

    datos = {}
    try:
        bloque = re.search(r"\{.*\}", bruto, re.S)
        datos = json.loads(bloque.group()) if bloque else {}
    except Exception:  # noqa: BLE001
        datos = {}

    terminos = " ".join(
        re.findall(r"[a-zñáéíóúü]+", normaliza(str(datos.get("terminos", ""))))[:14]
    )
    grupos = tuple(re.findall(r"[1-9]", str(datos.get("grupos", ""))))[:2]
    if not terminos:      # si no vino JSON, se aprovecha el texto suelto
        terminos = " ".join(re.findall(r"[a-zñáéíóúü]+", normaliza(bruto))[:14])

    memoria[clave] = (terminos, grupos)
    return terminos, grupos


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
    return limpias[:6], descartadas


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
    sugeridos = " ".join(
        re.findall(r"[a-zñáéíóúü]+", normaliza(str(datos.get("otros_terminos", "") or "")))[:12]
    )
    return {
        "ocupaciones": ocupaciones,
        "pregunta": str(datos.get("pregunta", "") or "").strip(),
        "descartadas": descartadas,
        "mas_terminos": sugeridos,
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
@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700&display=swap');
*{ box-sizing:border-box; }
body{
  margin:0; background:transparent; font-family:'Libre Franklin',system-ui,sans-serif;
  --negro:#0A0A0A; --rojo:#D1122E; --texto:#1A1A1A; --suave:#6B6B6B;
  --linea:#D8D8D8; --gris:#F2F2F2;
  color:var(--texto);
}
.rejilla{
  display:grid; grid-template-columns:repeat(2,1fr); gap:.45rem; align-items:start;
}
@media (max-width:760px){ .rejilla{ grid-template-columns:1fr; } }

.tarjeta{
  background:#fff; border:1px solid var(--linea); border-left:4px solid #C9C9C9;
  padding:.62rem .95rem;
  animation:entrar .38s cubic-bezier(.2,.85,.3,1) both;
}
.tarjeta.top{ border-left-color:var(--rojo); }

/* Relleno: viene del catálogo, no del modelo. Se distingue sin esconderse. */
.tarjeta.relleno{ background:#FAFAFA; border-left-color:#E4E4E4; }
.tarjeta.relleno .codigo{ color:#5A5A5A; }
.tarjeta.relleno .denominacion{ font-weight:500; color:#3A3A3A; }
.tarjeta.relleno .orden{ color:#A8A8A8; }
.tarjeta:nth-child(1){ animation-delay:0s; }
.tarjeta:nth-child(2){ animation-delay:.07s; }
.tarjeta:nth-child(3){ animation-delay:.14s; }
.tarjeta:nth-child(4){ animation-delay:.21s; }
.tarjeta:nth-child(5){ animation-delay:.28s; }
.tarjeta:nth-child(6){ animation-delay:.35s; }
.tarjeta:nth-child(n+7){ animation-delay:.4s; }
@keyframes entrar{
  from{ opacity:0; transform:translateY(14px) scale(.985); }
  to{ opacity:1; transform:none; }
}
@media (prefers-reduced-motion:reduce){ .tarjeta{ animation:none; } }

.fila{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.orden{
  font-size:.72rem; font-weight:700; color:var(--suave);
  font-variant-numeric:tabular-nums; letter-spacing:.04em;
}
.codigo{
  font-size:1.18rem; font-weight:700; letter-spacing:.045em; color:var(--negro);
  font-variant-numeric:tabular-nums;
}
.copiar{
  font-family:'Libre Franklin',sans-serif; font-size:.7rem; font-weight:600;
  color:var(--texto); background:#fff; border:1px solid var(--negro); border-radius:0;
  padding:.24rem .7rem; cursor:pointer; transition:all .15s ease; white-space:nowrap;
}
.copiar:hover{ background:var(--negro); color:#fff; }
.copiar.hecho{ background:var(--rojo); border-color:var(--rojo); color:#fff; }
.denominacion{ font-size:.9rem; font-weight:600; line-height:1.3; margin:.3rem 0 .12rem; }
.motivo{ font-size:.79rem; color:var(--suave); line-height:1.35; }
.etiqueta{
  display:inline-block; font-size:.65rem; font-weight:700; letter-spacing:.09em;
  text-transform:uppercase; padding:.22rem .55rem; border-radius:0;
  background:var(--gris); color:var(--suave);
}
.etiqueta.destacada{ background:var(--rojo); color:#fff; }
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
        clases = ["tarjeta"]
        if i == 1:
            clases.append("top")
        if o.get("relleno"):
            clases.append("relleno")
        clase = " ".join(clases)
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
    # En dos columnas manda la ficha más alta de cada fila. La denominación
    # cabe en menos caracteres por línea al tener la mitad de ancho.
    # Cada columna mide unos 540 px y la denominación va en mayúsculas: caben
    # unos 52 caracteres por línea. Con 32 se contaban líneas de más y sobraba
    # hueco bajo las fichas.
    def mide(o):
        lineas = 1 + len(o["denominacion"]) // 52
        return 74 + 18 * (lineas - 1) + (17 if o.get("motivo") else 0)

    alturas = [mide(o) for o in ocupaciones]
    filas = [alturas[i:i + 2] for i in range(0, len(alturas), 2)]
    estimada = sum(max(f) for f in filas) + 7 * (len(filas) - 1) + 6

    components.html(
        f"<style>{ESTILO_TARJETAS}</style>"
        f"<div class=\"rejilla\">{''.join(trozos)}</div>"
        f"<script>{GUION_COPIAR}</script>",
        height=estimada,
    )


def pinta_resultado(payload, estado=None, avance=0.06, interactivo=False, consulta=""):
    if estado:
        # Mientras se afina solo se ve la barra. Los resultados aparecen
        # completos de una vez, no goteando ficha a ficha.
        st.progress(min(avance, 0.95), text=estado)
        return
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
        try:
            caja = st.container(key="pregunta")
        except TypeError:          # Streamlit sin claves en contenedores
            caja = st.container()
        with caja:
            st.markdown(
                '<div class="pregunta-titulo">Pregunta para la persona</div>'
                f'<div class="pregunta-texto">{payload["pregunta"]}</div>',
                unsafe_allow_html=True,
            )
            if interactivo:
                si, no, _ = st.columns([1, 1, 4], gap="small")
                if si.button("Sí", key="resp_si", use_container_width=True):
                    st.session_state["respuesta"] = (consulta, payload["pregunta"], True)
                    st.rerun()
                if no.button("No", key="resp_no", use_container_width=True):
                    st.session_state["respuesta"] = (consulta, payload["pregunta"], False)
                    st.rerun()

    if estado:
        return

    if payload.get("interpretado") and MANTENIMIENTO:
        origen, destino = payload["interpretado"]
        st.markdown(
            f'<div class="nota">Interpretado <b>{origen}</b> como: {destino}</div>',
            unsafe_allow_html=True,
        )

    otras = payload.get("otras", [])
    if otras:
        with st.expander("Ver otras ocupaciones del catálogo"):
            arranque = len(payload.get("ocupaciones", [])) + 1
            for orden, (cod, den) in enumerate(otras, arranque):
                st.markdown(
                    f'<div style="padding:.35rem 0;border-bottom:1px solid var(--borde)">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-weight:700;'
                    f'letter-spacing:.08em">{cod}</span> &nbsp; '
                    f'<span style="font-size:.9rem">{den}</span></div>',
                    unsafe_allow_html=True,
                )

    if payload.get("fallo"):
        st.markdown(
            '<div class="nota">Resultados del catálogo, sin afinar.</div>',
            unsafe_allow_html=True,
        )
        if MANTENIMIENTO:
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
    cli = cliente() if usar_ia else None

    # Sin coincidencias y sin IA no hay nada más que hacer. Con IA, sí: es
    # justo el caso en el que más falta hace, así que no se rinde aquí.
    if not encontrados and cli is None:
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

    if cli is None:
        with zona.container():
            pinta_resultado(provisional)
        return provisional

    # Resultados del catálogo mientras el modelo responde
    with zona.container():
        pinta_resultado(provisional, estado="Afinando el resultado")

    # Si la consulta trae jerga o marcas, se traduce a vocabulario del catálogo
    # Primero se le pregunta al modelo qué oficio es esto, y se busca con sus
    # palabras además de las de la persona.
    interpretado, aviso = None, ""
    with zona.container():
        pinta_resultado({}, estado="Interpretando el oficio", avance=0.12)
    oficiales, grupos = interpreta_consulta(cli, texto)
    if oficiales:
        # Primero solo con el vocabulario oficial: las palabras coloquiales de
        # la persona ensucian la búsqueda ("casas" arrastra a construcción,
        # "monta" a calzado). Solo si eso no basta se mezclan las dos.
        mejores = busca(oficiales, tope=N_CANDIDATOS + 4, grupos=grupos)
        if len(mejores) < 3:
            mejores = busca(
                f"{busqueda or texto} {oficiales}", tope=N_CANDIDATOS + 4, grupos=grupos
            )
        if mejores:
            encontrados = mejores
            interpretado = ("la consulta", oficiales)
            provisional = {
                "ocupaciones": _basica(encontrados),
                "otras": [(c, d) for _, c, d in encontrados[5:12]],
            }

    if not encontrados:
        payload = {"ocupaciones": []}
        zona.empty()
        pinta_resultado(payload)
        return payload

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

    def consulta_al_modelo(candidatos, etiqueta):
        """Una pasada completa: streaming, barra y lectura del JSON."""
        bruto, avance = "", 0.10
        arranque = time.perf_counter()
        for trozo in flujo_modelo(cli, texto + contexto, candidatos):
            bruto += trozo
            transcurrido = time.perf_counter() - arranque
            if transcurrido > ESPERA_MAXIMA:
                raise TimeoutError(
                    f"El modelo ha tardado más de {ESPERA_MAXIMA} segundos."
                )
            nuevo = min(0.10 + transcurrido / (ESPERA_MAXIMA * 1.4), 0.92)
            if nuevo - avance > 0.04:
                avance = nuevo
                with zona.container():
                    pinta_resultado({}, estado=etiqueta, avance=avance)
        return interpreta(bruto)

    try:
        lista = "\n".join(f"{c}:{d}" for _, c, d in encontrados)
        payload = consulta_al_modelo(lista, "Afinando el resultado")

        # Si el modelo avisa de que ninguna candidata encaja, propone términos
        # y se repite la búsqueda con ellos. Es la vía que rescata ocupaciones
        # que no comparten ni una palabra con lo que escribe la persona.
        if payload.get("mas_terminos"):
            with zona.container():
                pinta_resultado({}, estado="Ampliando la búsqueda", avance=0.45)
            ampliados = busca(f"{busqueda or texto} {payload['mas_terminos']}",
                              tope=N_CANDIDATOS + 6)
            if ampliados:
                encontrados = ampliados
                interpretado = ("la descripción", payload["mas_terminos"])
                segunda = consulta_al_modelo(
                    "\n".join(f"{c}:{d}" for _, c, d in ampliados),
                    "Afinando el resultado",
                )
                if segunda["ocupaciones"]:
                    payload = segunda
    except Exception as e:  # noqa: BLE001
        zona.empty()
        provisional["fallo"] = f"{type(e).__name__}: {e}"
        pinta_resultado(provisional)
        return provisional

    # Si el modelo ha ascendido una ocupación que el catálogo no tenía la
    # primera, eso es una corrección: se guarda para la próxima vez.
    if payload["ocupaciones"] and encontrados:
        elegido = payload["ocupaciones"][0]["codigo"]
        if elegido != encontrados[0][1]:
            palabras = [
                raiz(w) for w in re.findall(r"\w+", normaliza(texto))
                if len(w) > 3 and w not in VACIAS
            ]
            st.session_state.setdefault("refuerzos_por_guardar", []).append(
                (elegido, palabras)
            )

    if interpretado:
        payload["interpretado"] = interpretado
    if aviso:
        payload["fallo"] = aviso
    if not payload["ocupaciones"]:
        payload["ocupaciones"] = provisional["ocupaciones"]

    # El prompt pide entre 3 y 5, pero conviene garantizarlo: una sola ficha
    # deja la pantalla coja y esconde alternativas que sí valdrían.
    ya = {o["codigo"] for o in payload["ocupaciones"]}
    for _, codigo, denom in encontrados:
        if len(payload["ocupaciones"]) >= 6:
            break
        if codigo in ya:
            continue
        payload["ocupaciones"].append({
            "codigo": codigo,
            "denominacion": denom,
            "nivel": "00",
            "nivel_texto": NIVELES["00"],
            "motivo": "",
            "relleno": True,        # viene del catálogo, no del modelo
        })
        ya.add(codigo)
    elegidos = {o["codigo"] for o in payload["ocupaciones"]}
    payload["otras"] = [(c, d) for _, c, d in encontrados if c not in elegidos][:8]

    zona.empty()          # retira el bloque provisional antes del definitivo
    pinta_resultado(payload)
    memoria[clave] = payload
    return payload



# ---------------------------------------------------------------------------
# INTERFAZ
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MODO MANTENIMIENTO
# La herramienta enseña códigos; su mantenimiento no le interesa a quien la
# usa. Todo lo interno —correcciones, diccionarios, diagnóstico— solo aparece
# si abres la app añadiendo  ?mantenimiento=1  al final de la dirección.
# ---------------------------------------------------------------------------

try:
    MANTENIMIENTO = st.query_params.get("mantenimiento") == "1"
except Exception:  # noqa: BLE001  versiones antiguas de Streamlit
    MANTENIMIENTO = False

st.session_state.setdefault("actual", None)
st.session_state.setdefault("registro", [])
st.session_state.setdefault("pendiente", None)
st.session_state.setdefault("usar_ia", True)
st.session_state.setdefault("cache", {})
st.session_state.setdefault("lexico", {})
st.session_state.setdefault("modelo_ok", 0)
st.session_state.setdefault("respuesta", None)
st.session_state.setdefault("por_guardar", [])
st.session_state.setdefault("refuerzos_por_guardar", [])
st.session_state.setdefault("ultima", "")
st.session_state.setdefault("consulta", "")

EJEMPLOS = [
    "Una persona que limpia habitaciones de hotel",
    "Una persona que conduce autobuses",
    "Una persona que monta placa de pladur",
    "Una persona que organiza eventos para empresas",
    "Una persona que reparte comida en moto",
    "Una persona que cuida a mayores en su casa",
    "Una persona que atiende la barra de un bar",
    "Una persona que lleva las facturas y las nóminas",
    "Una persona que maneja carretilla en un almacén",
    "Una persona que corta el pelo en una peluquería",
]


def panel_ajustes():
    # la clave permite darle forma circular desde el CSS
    with st.popover(":material/tune:", use_container_width=True):
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

        if not MANTENIMIENTO:
            st.caption(
                f"{len(IDX['registros'])} ocupaciones del catálogo oficial. "
                "Describe solo el puesto: sin nombres, DNI ni datos "
                "identificativos de la persona."
            )
            return

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

        refuerzos = refuerzos_compartidos()
        if refuerzos:
            st.markdown("**Correcciones aprendidas**")
            st.caption(f"{len(refuerzos)} ocupaciones con vocabulario reforzado.")
            st.code(
                "\n".join(
                    f"{c}  {IDX['por_codigo'].get(c, '')[:34]}  ←  {t}"
                    for c, t in sorted(refuerzos.items())
                ),
                language=None,
            )

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


def usar_ejemplo(texto):
    """Lanza un ejemplo dejando el estado limpio.

    Vaciar el cajón aquí es imprescindible: si se quedara el texto anterior,
    al redibujar la página el control de duplicados lo tomaría por una
    consulta nueva y lanzaría una segunda búsqueda encima de la del ejemplo.
    """
    st.session_state["pendiente"] = texto
    st.session_state["consulta"] = ""
    st.session_state["actual"] = None
    st.session_state["ultima"] = ""


def empezar_de_nuevo():
    """Vuelve al estado inicial y vacía el cuadro de texto.

    Va como retrollamada del botón: Streamlit no permite modificar el valor
    de un campo una vez creado dentro de la misma pasada, pero sí desde aquí.
    """
    st.session_state["actual"] = None
    st.session_state["consulta"] = ""
    st.session_state["ultima"] = ""


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
# Banda de cabecera: el buscador vive aquí y no se mueve nunca
# ---------------------------------------------------------------------------

try:
    banda = st.container(key="cabecera")
except TypeError:          # Streamlit anterior a la versión con claves
    banda = st.container()

with banda:
    st.markdown(
        '<div class="rotulo">Catálogo SISPE <span>&middot;</span> SilcoiWeb</div>',
        unsafe_allow_html=True,
    )
    st.button("Codificador de ocupaciones", key="marca", on_click=empezar_de_nuevo)
    # Sin st.form: así el botón de ajustes puede convivir en la misma fila.
    # El campo de texto ya reejecuta la app al pulsar Enter.
    campo, boton, ajustes = st.columns([6, 1.1, 0.6], gap="small")
    with campo:
        texto = st.text_input(
            "Consulta", label_visibility="collapsed", key="consulta",
            placeholder="Describe el puesto: qué hacía, dónde y con qué. También un código de 8 cifras.",
        )
    with boton:
        buscar = st.button("Buscar", key="buscar", use_container_width=True)
    with ajustes:
        panel_ajustes()

    escrito = (texto or "").strip()
    if escrito and not entrada:
        # Se lanza al pulsar Buscar o al pulsar Enter, pero no se repite sola
        # mientras el texto siga en el campo.
        if buscar or escrito != st.session_state.get("ultima", ""):
            entrada = escrito
            contexto, busqueda, rotulo = "", None, None

if entrada:
    st.session_state["ultima"] = entrada

# ---------------------------------------------------------------------------
# Cuerpo
# ---------------------------------------------------------------------------

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

    ocupaciones = payload.get("ocupaciones", [])
    if MANTENIMIENTO and len(ocupaciones) > 1 and _credenciales()[0]:
        st.markdown(
            '<div class="seccion">¿Cuál era la correcta?</div>', unsafe_allow_html=True
        )
        cols = st.columns(len(ocupaciones))
        for col, o in zip(cols, ocupaciones):
            with col:
                if st.button(o["codigo"], key=f"ok_{o['codigo']}",
                             use_container_width=True):
                    palabras = [
                        raiz(w) for w in re.findall(r"\w+", normaliza(consulta))
                        if len(w) > 3 and w not in VACIAS
                    ]
                    if guarda_refuerzo(o["codigo"], palabras):
                        st.success(
                            f"Aprendido: esas palabras llevarán a {o['codigo']}."
                        )
                    else:
                        st.warning("No se ha podido guardar la corrección.")

    st.markdown('<div class="separa"></div>', unsafe_allow_html=True)
    st.button("↺", key="reinicio", help="Nueva búsqueda", on_click=empezar_de_nuevo)
    st.markdown('<div class="pie-nueva">Nueva búsqueda</div>', unsafe_allow_html=True)

else:
    st.markdown('<div class="seccion">Prueba con</div>', unsafe_allow_html=True)
    arranque = "Una persona que "
    for i in range(0, len(EJEMPLOS), 2):
        fila = EJEMPLOS[i:i + 2]
        cols = st.columns(2, gap="small")
        for col, ej in zip(cols, fila):
            # el oficio en negrita, el arranque de la frase en peso normal
            rotulo = (
                f"{arranque}**{ej[len(arranque):]}**"
                if ej.startswith(arranque) else f"**{ej}**"
            )
            col.button(
                rotulo, use_container_width=True, key=f"ej_{i}_{ej[-14:]}",
                on_click=usar_ejemplo, args=(ej,),
            )

# Con el resultado ya en pantalla, se publica lo aprendido para el resto
for clave, valor in st.session_state.pop("por_guardar", []):
    guarda_termino(clave, valor)

for codigo, palabras in st.session_state.pop("refuerzos_por_guardar", []):
    guarda_refuerzo(codigo, palabras)

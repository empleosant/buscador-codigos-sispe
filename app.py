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
AMPLIADO = "terminos_ampliados.txt"
N_CANDIDATOS = 16
ESPERA_MAXIMA = 45

# ---------------------------------------------------------------------------
# PROVEEDOR DE IA
# ---------------------------------------------------------------------------
PROVEEDOR = "gemini"

PROVEEDORES = {
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
# ESTILO FLUIDO Y ADAPTATIVO
# ---------------------------------------------------------------------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700&family=JetBrains+Mono:wght@600;700&display=swap');

:root{
  --negro:#0A0A0A;
  --rojo:#D1122E;
  --rojo-oscuro:#A50E24;
  --texto:#1A1A1A;
  --suave:#555555;
  --tenue:#8E8E93;
  --linea:#E2E8F0;
  --gris:#F1F5F9;
}

.stApp{ background:#FAFAFA; }
html,body,[class*="css"],.stMarkdown{
  font-family:'Libre Franklin',system-ui,sans-serif; color:var(--texto);
}
.block-container{ padding:0 1rem .5rem !important; max-width:1200px; }
#MainMenu, footer, header[data-testid="stHeader"]{ visibility:hidden; height:0; }
[data-testid="stHeaderActionElements"]{ display:none !important; }
h1 > a, h2 > a, h3 > a, .stMarkdown a.anchor-link{ display:none !important; }
div[data-testid="InputInstructions"]{ display:none !important; }

/* ---------- Cabecera fluida ---------- */
.st-key-cabecera{
  background:var(--negro);
  padding:clamp(0.6rem, 1.2vh, 0.9rem) clamp(1rem, 2vw, 1.8rem);
  margin-bottom:clamp(0.3rem, 0.8vh, 0.6rem);
  box-shadow:0 2px 10px rgba(0,0,0,0.06);
}
.rotulo{
  color:#8A8A8A; font-size:clamp(0.58rem, 0.65vw, 0.66rem); font-weight:600;
  letter-spacing:.16em; text-transform:uppercase; margin:0 0 .1rem;
}
.rotulo span{ color:var(--rojo); font-weight:700; }

/* Título */
.st-key-marca button{
  background:transparent !important; border:none !important; box-shadow:none !important;
  padding:0 !important; justify-content:flex-start !important; margin-bottom:.4rem;
}
.st-key-marca button p{
  color:#fff !important; font-size:clamp(1.25rem, 1.5vw, 1.5rem) !important;
  font-weight:700 !important; letter-spacing:-.025em; margin:0 !important;
  text-align:left !important; border-bottom:2px solid transparent; transition:border-color .15s ease;
}
.st-key-marca button:hover p{ border-bottom-color:var(--rojo); }

/* Campo de búsqueda */
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="base-input"],
.st-key-cabecera div[data-testid="stTextInput"] input,
.st-key-cabecera div[data-testid="stTextInput"] input:focus,
.st-key-cabecera div[data-testid="stTextInput"] input:hover{
  background:transparent !important; border:none !important;
  box-shadow:none !important; outline:none !important;
}
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="input"]{
  background:#fff !important; border:1px solid #fff !important;
  border-radius:4px 0 0 4px !important; box-shadow:none !important;
}
.st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within{
  border-color:var(--rojo) !important; box-shadow:0 0 0 2px var(--rojo) !important;
}
div[data-testid="stTextInput"] input{
  padding:clamp(0.45rem, 0.9vh, 0.65rem) clamp(0.7rem, 1vw, 1rem) !important;
  font-size:clamp(0.88rem, 0.95vw, 0.98rem) !important;
  color:var(--texto) !important; font-family:'Libre Franklin',sans-serif !important;
}

/* Botones */
.st-key-buscar button{
  background:var(--rojo) !important; color:#fff !important; border:none !important;
  border-radius:0 4px 4px 0 !important; font-weight:700 !important;
  font-size:clamp(0.84rem, 0.9vw, 0.92rem) !important;
  padding:clamp(0.45rem, 0.9vh, 0.65rem) 1rem !important;
  min-height:clamp(38px, 4.2vh, 46px) !important; letter-spacing:.02em;
  transition:background .15s ease;
}
.st-key-buscar button:hover{ background:var(--rojo-oscuro) !important; }
.st-key-buscar button p{ color:#fff !important; font-weight:700 !important; }

.st-key-ajustes button{
  width:clamp(38px, 4.2vh, 46px) !important; height:clamp(38px, 4.2vh, 46px) !important;
  min-height:clamp(38px, 4.2vh, 46px) !important;
  border-radius:4px !important; padding:0 !important;
  background:#1A1A1A !important; border:1px solid #333 !important; color:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:all .18s ease;
}
.st-key-ajustes button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important; color:#fff !important;
}

/* Consulta activa */
.consulta-box{
  border-bottom:2px solid var(--negro); padding-bottom:.25rem;
  margin:0 0 clamp(0.3rem, 0.8vh, 0.6rem);
}
.consulta-texto{
  font-size:clamp(0.96rem, 1.1vw, 1.1rem); font-weight:700;
  letter-spacing:-.015em; color:var(--texto);
}
.seccion{
  font-size:.65rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--suave); margin:1.2rem 0 .6rem;
}

/* Pregunta interactiva con botones adaptativos */
.st-key-pregunta{
  background:#fff; border:1px solid var(--linea); border-left:4px solid var(--rojo);
  border-radius:4px; padding:clamp(0.6rem, 1.1vh, 0.85rem) clamp(0.8rem, 1.2vw, 1.1rem);
  margin:.3rem 0 .5rem; box-shadow:0 1px 6px rgba(0,0,0,0.03);
}
.pregunta-titulo{
  font-size:.62rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--rojo); margin-bottom:.2rem;
}
.pregunta-texto{
  font-size:clamp(0.92rem, 1vw, 1.02rem); line-height:1.35; font-weight:600;
  color:var(--texto); margin-bottom:.55rem;
}
.st-key-pregunta .stButton button{
  background:#fff; border:1px solid var(--negro); font-weight:600; border-radius:4px;
  padding:.4rem .85rem; min-height:38px; font-size:.85rem; transition:all .15s ease;
  white-space:normal !important; height:auto !important;
}
.st-key-pregunta .stButton button:hover{
  background:var(--negro); color:#fff; border-color:var(--negro);
}

.nota{ font-size:.76rem; color:var(--suave); margin:.2rem 0; }
.separa{ height:1px; background:var(--linea); margin:clamp(0.3rem, 0.7vh, 0.5rem) 0; }

/* Botón de reinicio */
.st-key-reinicio,
.st-key-reinicio > div,
.st-key-reinicio [data-testid="stTooltipHoverTarget"],
.st-key-reinicio [data-testid="stElementToolbar"]{
  display:flex !important; justify-content:center !important; width:100% !important;
}
.st-key-reinicio button{
  width:clamp(42px, 4.8vh, 50px) !important; height:clamp(42px, 4.8vh, 50px) !important;
  min-height:clamp(42px, 4.8vh, 50px) !important;
  border-radius:50% !important; padding:0 !important;
  border:2px solid var(--negro) !important; background:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:all .2s cubic-bezier(.2,.85,.3,1); box-shadow:0 2px 8px rgba(0,0,0,0.05);
}
.st-key-reinicio button p{
  font-size:clamp(1.3rem, 1.6vw, 1.6rem) !important; line-height:1 !important;
  margin:0 !important; color:var(--negro) !important;
}
.st-key-reinicio button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important;
  transform:rotate(-90deg) scale(1.05);
}
.st-key-reinicio button:hover p{ color:#fff !important; }
.pie-nueva{
  text-align:center; font-size:.76rem; font-weight:600; color:var(--suave); margin:.25rem 0 0;
}

div[data-testid="stExpander"]{ border:none; background:transparent; margin-top:.15rem; }
div[data-testid="stExpander"] summary{ font-size:.82rem; color:var(--suave); padding:.15rem 0; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# CATALOGO
# ---------------------------------------------------------------------------

def normaliza(t):
    t = re.sub(r"[/\\_\-]+", " ", t)
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    ).lower().strip()


VOCABULARIO = "vocabulario.json"

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
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
    return _lee_gist(ARCHIVO_GIST)


def refuerzos_compartidos():
    return _lee_gist(ARCHIVO_REFUERZOS)


def guarda_termino(clave, valor):
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
        return False, "Se escribió, pero al releer no aparece."

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
    fusion = dict(lexico_compartido())
    fusion.update(SINONIMOS)
    return fusion


def raiz(w):
    if len(w) > 6 and (w.endswith("ando") or w.endswith("iendo")):
        w = w[:-4]
    elif len(w) > 5 and (w.endswith("aba") or w.endswith("ado") or w.endswith("ido")):
        w = w[:-3]
    elif len(w) > 5 and w.endswith("dor"):
        w = w[:-3]
    elif len(w) > 5 and w.endswith("or"):
        w = w[:-2]
    elif len(w) > 5 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s"):
        w = w[:-1]
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
    st.error(f"Falta el archivo **{CATALOGO}**.")
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


def busca(consulta, tope=20, grupos=None):
    q = normaliza(consulta)
    terminos = {}
    cabezas = set()

    clausulas = [
        c.strip()
        for c in re.split(r"\s+(?:y|e|o|ademas|tambien)\s+", q)
        if c.strip()
    ]
    if not clausulas:
        clausulas = [q]

    for clausula in clausulas:
        contadas = 0
        for w in re.findall(r"\w+", clausula):
            if len(w) > 2 and w not in VACIAS and w not in terminos:
                contadas += 1
                if contadas == 1:
                    cabezas.add(raiz(w))
                terminos[w] = 1.0 if contadas <= 3 else 0.7

    palabras_q = set(re.findall(r"\w+", q))
    for clave, expansion in diccionario().items():
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
        if r in IDX["inv_extra"]:
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
        if cabezas and (cabezas & reg["cabeza"]):
            nucleo *= 1.8
        familia = 1.0
        if grupos:
            familia = 1.7 if reg["codigo"][0] in grupos else 0.45

        propios = len(cubierto[i] & originales)
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
        except Exception:  # noqa: BLE001
            return genai.Client(api_key=clave)
    if not OpenAI:
        return None
    return OpenAI(api_key=clave, base_url=AJUSTES["url"], timeout=ESPERA_MAXIMA)


INSTRUCCIONES = """Eres un técnico de codificación de ocupaciones para SilcoiWeb (SEPE).

Recibes la descripción de un puesto y una lista cerrada de ocupaciones candidatas.
Selecciona entre 3 y 5, de mayor a menor afinidad.

REGLAS
1. Usa únicamente códigos y denominaciones literales de la lista de candidatos. No inventes ni modifiques ninguno.
2. Los candidatos llegan ordenados por coincidencia de palabras, NO por acierto. Ese orden es solo una pista: elige siempre la ocupación cuya denominación describa la actividad real, aunque esté al final de la lista.
3. Devuelve SIEMPRE entre 3 y 5 ocupaciones, aunque dudes.
4. Nivel profesional: 90 aprendices (sin experiencia) / 00 técnicos o sin categoría (estándar con experiencia) / 10 dirección / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.
5. El campo "motivo" explica en menos de 10 palabras por qué encaja, en español con acentuación correcta.
6. No propongas ocupaciones de dirección, jefatura ni mando (niveles 10, 20, 30) salvo que la descripción diga expresamente que dirigía equipos, centros o departamentos.
7. Respeta el entorno de trabajo que indique la descripción: domicilio particular frente a institución, centro o residencia.
8. PREGUNTA Y OPCIONES: Rellena "pregunta" y "opciones" solo si hay ambigüedad para decidir entre las DOS PRIMERAS ocupaciones de tu lista; si no, déjalos vacíos.
   - "pregunta": formula una duda directa para la persona atendida (máximo 15 palabras).
   - "opciones": lista con 2 opciones cortas y concretas que representen cada rama o tarea (ej. ["Atención en caja / mostrador", "Cocina y preparación de comida"], ["En casas particulares", "En residencias"], o ["Sí", "No"]).
   - NUNCA uses preguntas con botones de Sí/No cuando la duda sea elegir entre dos áreas o tareas: en esos casos pon los nombres de las tareas en "opciones".
9. IMPORTANTE: Si ninguna de las candidatas describe con precisión la actividad, rellena "otros_terminos" con entre 6 y 10 palabras sueltas del vocabulario oficial de la CNO que deberían buscarse.

Responde solo con este JSON:
{"ocupaciones":[{"codigo":"12345678","denominacion":"...","nivel":"00","motivo":"..."}],"pregunta":"","opciones":[],"otros_terminos":""}
"""


def _configuraciones():
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
        except Exception:  # noqa: BLE001
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
                if emitido:
                    raise
                ultimo = e
                if sin_cuota(e):
                    break
    raise ultimo


def _flujo_openai(cli, prompt):
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


INTERPRETE = """Eres experto en el catálogo de ocupaciones del SEPE (CNO).

Lees la descripción de un puesto escrita por un orientador laboral, con las
palabras de la persona atendida, y devuelves el VOCABULARIO OFICIAL de los
oficios que podría estar describiendo.

Una descripción corriente admite varias lecturas: "cuidado de niños en una
escuela" puede ser guardería, comedor escolar o tiempo libre. Devuelve entre
2 y 3 lecturas distintas, de más a menos probable.

Si la descripción incluye dos funciones distintas o tareas combinadas
(ej. "cobro en caja y repongo", "conduzco y reparto"), genera una lectura
específica para cada una de las actividades.

Responde SOLO con este JSON:
{"lecturas":[{"terminos":"...","grupos":"5"},{"terminos":"...","grupos":"3"}]}
"""


def interpreta_consulta(cli, texto):
    clave = normaliza(texto)
    memoria = st.session_state.setdefault("interpretaciones", {})
    if clave in memoria:
        return memoria[clave]
    try:
        cfg = dict(system_instruction=INTERPRETE, max_output_tokens=2048)
        if PROVEEDOR == "gemini":
            try:
                cfg["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
            except Exception:  # noqa: BLE001
                pass
            r = cli.models.generate_content(
                model=modelo_actual(), contents=texto,
                config=types.GenerateContentConfig(**cfg),
            )
            bruto = (getattr(r, "text", "") or "").strip()
        else:
            r = cli.chat.completions.create(
                model=modelo_actual(),
                messages=[{"role": "system", "content": INTERPRETE}, {"role": "user", "content": texto}],
                max_tokens=2048, temperature=0,
            )
            bruto = (r.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        return []

    datos = {}
    try:
        bloque = re.search(r"\{.*\}", bruto, re.S)
        datos = json.loads(bloque.group()) if bloque else {}
    except Exception:  # noqa: BLE001
        datos = {}

    crudas = datos.get("lecturas")
    if not isinstance(crudas, list):
        crudas = [datos] if datos.get("terminos") else []

    lecturas = []
    for l in crudas[:3]:
        terminos = " ".join(
            re.findall(r"[a-zñáéíóúü]+", normaliza(str(l.get("terminos", ""))))[:12]
        )
        if terminos:
            grupos = tuple(re.findall(r"[1-9]", str(l.get("grupos", ""))))[:2]
            lecturas.append((terminos, grupos))

    if not lecturas:
        suelto = " ".join(re.findall(r"[a-zñáéíóúü]+", normaliza(bruto))[:14])
        if suelto:
            lecturas = [(suelto, ())]

    memoria[clave] = lecturas
    return lecturas


def flujo_modelo(cli, texto, candidatos):
    prompt = f"CANDIDATOS (única fuente válida):\n{candidatos}\n\nDESCRIPCIÓN: {texto}"
    if PROVEEDOR == "gemini":
        yield from _flujo_gemini(cli, prompt)
    else:
        yield from _flujo_openai(cli, prompt)


def objetos_parciales(bruto):
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
                "denominacion": IDX["por_codigo"][codigo],
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
        return {"ocupaciones": ocupaciones, "pregunta": "", "opciones": [], "descartadas": descartadas}

    ocupaciones, descartadas = verifica(datos.get("ocupaciones"))
    sugeridos = " ".join(
        re.findall(r"[a-zñáéíóúü]+", normaliza(str(datos.get("otros_terminos", "") or "")))[:12]
    )
    pregunta = str(datos.get("pregunta", "") or "").strip()
    raw_opciones = datos.get("opciones", [])
    opciones = []
    if isinstance(raw_opciones, list):
        opciones = [str(o).strip() for o in raw_opciones if str(o).strip()][:3]
    if pregunta and not opciones:
        opciones = ["Sí", "No"]

    return {
        "ocupaciones": ocupaciones,
        "pregunta": pregunta,
        "opciones": opciones,
        "descartadas": descartadas,
        "mas_terminos": sugeridos,
    }


# ---------------------------------------------------------------------------
# TARJETAS FLUIDAS
# ---------------------------------------------------------------------------

ESTILO_TARJETAS = """
@import url('https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700&family=JetBrains+Mono:wght@600;700&display=swap');
*{ box-sizing:border-box; }
body{
  margin:0; background:transparent; font-family:'Libre Franklin',system-ui,sans-serif;
  --negro:#0A0A0A; --rojo:#D1122E; --texto:#1A1A1A; --suave:#555555;
  --linea:#E2E8F0; --gris:#F1F5F9;
  color:var(--texto);
}
.rejilla{
  display:grid; grid-template-columns:repeat(2,1fr);
  gap:clamp(0.4rem, 0.9vh, 0.65rem); align-items:stretch;
}
@media (max-width:760px){ .rejilla{ grid-template-columns:1fr; } }

.tarjeta{
  background:#fff; border:1px solid var(--linea); border-left:4px solid #CBD5E1;
  border-radius:4px;
  padding:clamp(0.55rem, 1.1vh, 0.8rem) clamp(0.75rem, 1.2vw, 1rem);
  display:flex; flex-direction:column; justify-content:space-between;
  transition:transform .12s ease, box-shadow .12s ease;
  box-shadow:0 1px 3px rgba(0,0,0,0.03); cursor:pointer;
}
.tarjeta:hover{
  transform:translateY(-1px); box-shadow:0 3px 10px rgba(0,0,0,0.07); border-color:#CBD5E1;
}
.tarjeta.top{
  border-left-color:var(--rojo); background:#FFFFFF;
  box-shadow:0 2px 6px rgba(209,18,46,0.06);
}
.tarjeta.relleno{
  background:#FAFAFA; border-left-color:#E2E8F0; opacity:.92;
}

.fila{
  display:flex; align-items:center; justify-content:space-between;
  gap:8px; margin-bottom:clamp(0.15rem, 0.4vh, 0.25rem);
}
.identificador{ display:flex; align-items:center; gap:8px; }
.orden{
  font-size:clamp(0.68rem, 0.75vw, 0.74rem); font-weight:700; color:var(--suave);
  font-family:'JetBrains Mono',monospace;
}
.codigo{
  font-size:clamp(1.1rem, 1.25vw, 1.25rem); font-weight:700;
  letter-spacing:.03em; color:var(--negro); font-family:'JetBrains Mono',monospace;
}

.copiar{
  font-family:'Libre Franklin',sans-serif; font-size:clamp(0.7rem, 0.78vw, 0.76rem);
  font-weight:600; color:var(--texto); background:#fff; border:1px solid #C4C4C4;
  border-radius:3px; padding:.22rem .65rem; cursor:pointer;
  transition:all .15s ease; white-space:nowrap;
}
.copiar:hover{ background:var(--negro); color:#fff; border-color:var(--negro); }
.copiar.hecho{ background:var(--rojo); border-color:var(--rojo); color:#fff; }

.denominacion{
  font-size:clamp(0.88rem, 0.98vw, 0.95rem); font-weight:600;
  line-height:1.28; color:var(--texto); margin:0 0 .2rem;
}
.motivo{
  font-size:clamp(0.76rem, 0.85vw, 0.82rem); color:var(--suave);
  line-height:1.26; margin-bottom:.35rem;
}

.etiquetas-fila{
  display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-top:auto; padding-top:.25rem;
}
.etiqueta{
  display:inline-flex; align-items:center; font-size:clamp(0.6rem, 0.68vw, 0.66rem);
  font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  padding:.14rem .45rem; border-radius:3px; background:var(--gris); color:var(--suave);
}
.etiqueta.recomendada{ background:var(--rojo); color:#fff; }
.etiqueta.mando{ background:#FFF7ED; color:#C2410C; border:1px solid #FFEDD5; }
"""

GUION_INTERACTIVO = """
function copiarTexto(texto, boton){
  navigator.clipboard.writeText(texto).then(() => {
    boton.textContent = 'Copiado';
    boton.classList.add('hecho');
    setTimeout(() => { boton.textContent = 'Copiar'; boton.classList.remove('hecho'); }, 1400);
  }).catch(() => {
    const caja = document.createElement('textarea');
    caja.value = texto;
    document.body.appendChild(caja);
    caja.select();
    document.execCommand('copy');
    document.body.removeChild(caja);
    boton.textContent = 'Copiado';
    boton.classList.add('hecho');
    setTimeout(() => { boton.textContent = 'Copiar'; boton.classList.remove('hecho'); }, 1400);
  });
}

function alto(){
  parent.postMessage(
    {type:'streamlit:setFrameHeight', height: document.documentElement.scrollHeight + 4},
    '*'
  );
}

document.querySelectorAll('.copiar').forEach(b => {
  b.addEventListener('click', (e) => {
    e.stopPropagation();
    copiarTexto(b.dataset.cod, b);
  });
});

document.querySelectorAll('.tarjeta').forEach(t => {
  t.addEventListener('click', () => {
    const btn = t.querySelector('.copiar');
    if (btn) btn.click();
  });
});

window.addEventListener('keydown', (e) => {
  if (['1','2','3','4','5','6'].includes(e.key) && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
    const idx = parseInt(e.key) - 1;
    const btns = document.querySelectorAll('.copiar');
    if (btns[idx]) btns[idx].click();
  }
});

const observador = new ResizeObserver(() => alto());
observador.observe(document.body);
window.addEventListener('load', alto);
"""


def pinta_tarjetas(ocupaciones):
    if not ocupaciones:
        return

    trozos = []
    for i, o in enumerate(ocupaciones, 1):
        es_primera = (i == 1 and not o.get("relleno"))
        es_mando = o.get("nivel") in ("10", "20", "30")

        clases = ["tarjeta"]
        if es_primera:
            clases.append("top")
        if o.get("relleno"):
            clases.append("relleno")
        clase = " ".join(clases)

        etiquetas_html = []
        if es_primera:
            etiquetas_html.append('<span class="etiqueta recomendada">★ Recomendada</span>')

        etiqueta_clase = "etiqueta mando" if es_mando else "etiqueta"
        etiquetas_html.append(
            f'<span class="{etiqueta_clase}">Nivel {o["nivel"]} &middot; {o["nivel_texto"]}</span>'
        )

        motivo_html = f'<div class="motivo">{o["motivo"]}</div>' if o.get("motivo") else ""

        trozos.append(
            f'<div class="{clase}">'
            f'  <div>'
            f'    <div class="fila">'
            f'      <div class="identificador">'
            f'        <span class="orden">{i:02d}</span>'
            f'        <span class="codigo">{o["codigo"]}</span>'
            f'      </div>'
            f'      <button class="copiar" data-cod="{o["codigo"]}">Copiar</button>'
            f'    </div>'
            f'    <div class="denominacion">{o["denominacion"]}</div>'
            f'    {motivo_html}'
            f'  </div>'
            f'  <div class="etiquetas-fila">{"".join(etiquetas_html)}</div>'
            f'</div>'
        )

    def mide(o):
        lineas_denom = max(1, math.ceil(len(o["denominacion"]) / 46))
        lineas_motivo = max(1, math.ceil(len(o["motivo"]) / 44)) if o.get("motivo") else 0
        h_denom = lineas_denom * 20
        h_motivo = (lineas_motivo * 17 + 4) if lineas_motivo else 0
        h_base = 64
        return h_base + h_denom + h_motivo

    alturas = [mide(o) for o in ocupaciones]
    filas = [alturas[i:i + 2] for i in range(0, len(alturas), 2)]
    estimada = sum(max(f) for f in filas) + 10 * max(0, len(filas) - 1) + 14

    components.html(
        f"<style>{ESTILO_TARJETAS}</style>"
        f"<div class=\"rejilla\">{''.join(trozos)}</div>"
        f"<script>{GUION_INTERACTIVO}</script>",
        height=estimada,
    )


def pinta_resultado(payload, estado=None, avance=0.06, interactivo=False, consulta=""):
    if estado:
        st.progress(min(avance, 0.95), text=estado)
        return
    if payload.get("aviso"):
        st.info(payload["aviso"])
        return

    ocupaciones = payload.get("ocupaciones", [])
    if not ocupaciones:
        st.info("No encuentro coincidencias claras. Prueba con el nombre del puesto o función concreta.")
        return

    pinta_tarjetas(ocupaciones)

    if payload.get("pregunta"):
        try:
            caja = st.container(key="pregunta")
        except TypeError:
            caja = st.container()
        with caja:
            st.markdown(
                '<div class="pregunta-titulo">Pregunta para la persona</div>'
                f'<div class="pregunta-texto">{payload["pregunta"]}</div>',
                unsafe_allow_html=True,
            )
            if interactivo:
                opciones = payload.get("opciones") or ["Sí", "No"]
                cols = st.columns(len(opciones), gap="small")
                for idx, opc in enumerate(opciones):
                    with cols[idx]:
                        if st.button(opc, key=f"resp_opt_{idx}", use_container_width=True):
                            st.session_state["respuesta"] = (consulta, payload["pregunta"], opc)
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
                    f'<div style="padding:.2rem 0;border-bottom:1px solid var(--linea)">'
                    f'<span style="font-family:JetBrains Mono,monospace;font-weight:700;'
                    f'font-size:.84rem;letter-spacing:.04em">{cod}</span> &nbsp; '
                    f'<span style="font-size:.84rem">{den}</span></div>',
                    unsafe_allow_html=True,
                )

    if payload.get("fallo"):
        st.markdown('<div class="nota">Resultados del catálogo, sin afinar.</div>', unsafe_allow_html=True)
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

    with zona.container():
        pinta_resultado(provisional, estado="Afinando el resultado")

    interpretado, aviso = None, ""
    with zona.container():
        pinta_resultado({}, estado="Interpretando el oficio", avance=0.12)
    lecturas = interpreta_consulta(cli, texto)
    if lecturas:
        fundido, vistos = [], {}
        for orden, (terminos, grupos) in enumerate(lecturas):
            peso = (1.0, 0.88, 0.78)[min(orden, 2)]
            for puntos, c_cod, denom in busca(terminos, tope=12, grupos=grupos):
                if puntos * peso > vistos.get(c_cod, 0):
                    vistos[c_cod] = puntos * peso
                    fundido.append((puntos * peso, c_cod, denom))

        mejores = sorted(
            {c: (p, c, d) for p, c, d in sorted(fundido)}.values(), reverse=True
        )[:N_CANDIDATOS + 4]

        if len(mejores) < 3:
            mejores = busca(
                f"{busqueda or texto} {lecturas[0][0]}",
                tope=N_CANDIDATOS + 4, grupos=lecturas[0][1],
            )
        if mejores:
            encontrados = mejores
            interpretado = (
                "la consulta",
                " · ".join(t.split()[0] for t, _ in lecturas),
            )
            provisional = {
                "ocupaciones": _basica(encontrados),
                "otras": [(c, d) for _, c, d in encontrados[5:12]],
            }

    if not encontrados:
        payload = {"ocupaciones": []}
        zona.empty()
        pinta_resultado(payload)
        return payload

    def consulta_al_modelo(candidatos, etiqueta):
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

    ya = {o["codigo"] for o in payload["ocupaciones"]}
    for _, codigo_c, denom in encontrados:
        if len(payload["ocupaciones"]) >= 6:
            break
        if codigo_c in ya:
            continue
        payload["ocupaciones"].append({
            "codigo": codigo_c,
            "denominacion": denom,
            "nivel": "00",
            "nivel_texto": NIVELES["00"],
            "motivo": "",
            "relleno": True,
        })
        ya.add(codigo_c)
    elegidos = {o["codigo"] for o in payload["ocupaciones"]}
    payload["otras"] = [(c, d) for _, c, d in encontrados if c not in elegidos][:8]

    zona.empty()
    pinta_resultado(payload)
    memoria[clave] = payload
    return payload


# ---------------------------------------------------------------------------
# INTERFAZ
# ---------------------------------------------------------------------------

try:
    MANTENIMIENTO = st.query_params.get("mantenimiento") == "1"
except Exception:  # noqa: BLE001
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
                "Describe solo el puesto: sin datos identificativos."
            )
            return

        if st.button("Probar la conexión con la IA", use_container_width=True):
            prueba = cliente()
            if prueba is None:
                st.error(f"No hay clave {AJUSTES['clave']} en los Secrets.")
            else:
                try:
                    cfg = dict(system_instruction="Responde únicamente con la palabra ok.", max_output_tokens=2048)
                    r = prueba.models.generate_content(
                        model=modelo_actual(), contents="ok",
                        config=types.GenerateContentConfig(**cfg),
                    )
                    st.success(f"{modelo_actual()}: {(getattr(r, 'text', '') or '').strip()[:60]}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"{type(e).__name__}: {e}")

        compartido = lexico_compartido()
        gist_activo, _ = _credenciales()
        if gist_activo:
            st.markdown("**Diccionario compartido**")
            st.caption(f"{len(compartido)} términos aprendidos.")
            if st.button("Comprobar que guarda", use_container_width=True):
                correcto, detalle = prueba_gist()
                (st.success if correcto else st.error)(detalle)


def usar_ejemplo(texto_ejemplo):
    st.session_state["pendiente"] = texto_ejemplo
    st.session_state["consulta"] = ""
    st.session_state["actual"] = None
    st.session_state["ultima"] = ""


def empezar_de_nuevo():
    st.session_state["actual"] = None
    st.session_state["consulta"] = ""
    st.session_state["ultima"] = ""


# ---------------------------------------------------------------------------
# Desambiguación interactiva con opciones adaptativas
# ---------------------------------------------------------------------------

entrada, contexto, busqueda, rotulo = None, "", None, None

respuesta = st.session_state.pop("respuesta", None)
if respuesta:
    original, pregunta, eleccion = respuesta
    entrada = original
    rotulo = f"{original}  ·  {eleccion}"
    contexto = (
        f"\n\nACLARACIÓN: a la pregunta «{pregunta}» la persona respondió "
        f"«{eleccion}». Ten en cuenta esta aclaración para priorizar la opción adecuada "
        f"y no vuelvas a plantear la misma duda."
    )
    busqueda = f"{original} {eleccion}"

if not entrada:
    entrada = st.session_state.pop("pendiente", None)

# ---------------------------------------------------------------------------
# Banda de cabecera
# ---------------------------------------------------------------------------

try:
    banda = st.container(key="cabecera")
except TypeError:
    banda = st.container()

with banda:
    st.markdown(
        '<div class="rotulo">Catálogo SISPE <span>&middot;</span> SilcoiWeb</div>',
        unsafe_allow_html=True,
    )
    st.button("Codificador de ocupaciones", key="marca", on_click=empezar_de_nuevo)
    campo, boton, ajustes = st.columns([6.4, 1.1, 0.5], gap="small")
    with campo:
        texto = st.text_input(
            "Consulta", label_visibility="collapsed", key="consulta",
            placeholder="Describe el puesto o introduce un código de 8 cifras...",
        )
    with boton:
        buscar = st.button("Buscar", key="buscar", use_container_width=True)
    with ajustes:
        panel_ajustes()

    escrito = (texto or "").strip()
    if escrito and not entrada:
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
        f'<div class="consulta-box"><div class="consulta-texto">{rotulo or entrada}</div></div>',
        unsafe_allow_html=True,
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
    st.markdown(
        f'<div class="consulta-box"><div class="consulta-texto">{consulta}</div></div>',
        unsafe_allow_html=True,
    )
    pinta_resultado(payload, interactivo=True, consulta=consulta)

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
            rotulo_ej = (
                f"{arranque}**{ej[len(arranque):]}**"
                if ej.startswith(arranque) else f"**{ej}**"
            )
            col.button(
                rotulo_ej, use_container_width=True, key=f"ej_{i}_{ej[-14:]}",
                on_click=usar_ejemplo, args=(ej,),
            )

for clave, valor in st.session_state.pop("por_guardar", []):
    guarda_termino(clave, valor)

for codigo, palabras in st.session_state.pop("refuerzos_por_guardar", []):
    guarda_refuerzo(codigo, palabras)

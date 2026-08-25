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
import random
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
ENCAJE_MINIMO = 0.40  # cuánto del nombre de la ocupación debe explicar la
                      # consulta para fiarse de ella sin preguntar a la IA
VENTAJA_CLARA = 3.0   # cuántas veces debe superar el 1º del buscador al 2º
                      # para que mande él en lugar del modelo (sube para que
                      # mande menos, baja para que mande más)
ESPERA_MAXIMA = 30   # segundos por intento. A 45 una llamada atascada te dejaba
                     # mirando la pantalla; a 12 se cortaban llamadas que iban a
                     # terminar bien y caias al catalogo sin afinar.

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


def por_minuto(e):
    """Distingue el tope POR MINUTO del cupo diario agotado.

    Los dos llegan como 429 y con el mismo texto de "exceeded your quota", pero
    no son lo mismo: el de por minuto se pasa solo en unos segundos y lo unico
    sensato es esperar y reintentar. Tratarlo como cupo agotado degradaba el
    modelo para el resto de la sesion por un tropiezo de un minuto.
    """
    t = str(e).lower()
    if not sin_cuota(e):
        return False
    if "per day" in t or "requests per day" in t or "perday" in t:
        return False
    return True

st.set_page_config(
    page_title="Codificador de ocupaciones",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# ESTILO FLUIDO Y COMPACTO
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
.block-container{ padding:0 1rem .4rem !important; max-width:1200px; }
#MainMenu, footer, header[data-testid="stHeader"]{ visibility:hidden; height:0; }
[data-testid="stHeaderActionElements"]{ display:none !important; }
h1 > a, h2 > a, h3 > a, .stMarkdown a.anchor-link{ display:none !important; }
div[data-testid="InputInstructions"]{ display:none !important; }

/* Eliminación de márgenes fantasma entre iframe y contenedor */
div[data-testid="stCustomComponentV1"] {
  margin-bottom: 0px !important;
  padding-bottom: 0px !important;
}
div[data-testid="stCustomComponentV1"] iframe {
  margin-bottom: 0px !important;
  padding-bottom: 0px !important;
  display: block !important;
}

/* ---------- Cabecera fluida ---------- */
.st-key-cabecera{
  background:var(--negro);
  padding:clamp(0.55rem, 1vh, 0.8rem) clamp(1rem, 2vw, 1.8rem);
  margin-bottom:clamp(0.25rem, 0.6vh, 0.45rem);
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
  padding:0 !important; justify-content:flex-start !important; margin-bottom:.35rem;
}
.st-key-marca button p{
  color:#fff !important; font-size:clamp(1.2rem, 1.45vw, 1.45rem) !important;
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
  padding:clamp(0.42rem, 0.8vh, 0.6rem) clamp(0.7rem, 1vw, 1rem) !important;
  font-size:clamp(0.88rem, 0.95vw, 0.98rem) !important;
  color:var(--texto) !important; font-family:'Libre Franklin',sans-serif !important;
}

/* Botones */
.st-key-buscar button{
  background:var(--rojo) !important; color:#fff !important; border:none !important;
  border-radius:0 4px 4px 0 !important; font-weight:700 !important;
  font-size:clamp(0.84rem, 0.9vw, 0.92rem) !important;
  padding:clamp(0.42rem, 0.8vh, 0.6rem) 1rem !important;
  min-height:clamp(36px, 3.8vh, 44px) !important; letter-spacing:.02em;
  transition:background .15s ease;
}
.st-key-buscar button:hover{ background:var(--rojo-oscuro) !important; }
.st-key-buscar button p{ color:#fff !important; font-weight:700 !important; }

.st-key-ajustes button{
  width:clamp(36px, 3.8vh, 44px) !important; height:clamp(36px, 3.8vh, 44px) !important;
  min-height:clamp(36px, 3.8vh, 44px) !important;
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
  border-bottom:2px solid var(--negro); padding-bottom:.2rem;
  margin:0 0 clamp(0.25rem, 0.5vh, 0.4rem);
}
.consulta-texto{
  font-size:clamp(0.95rem, 1.05vw, 1.08rem); font-weight:700;
  letter-spacing:-.015em; color:var(--texto);
}
.seccion{
  font-size:.65rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--suave); margin:1rem 0 .5rem;
}

/* Pregunta interactiva centrada (arriba de las tarjetas) */
.st-key-pregunta{
  background:#fff; border:1px solid var(--linea); border-top:3px solid var(--rojo);
  border-radius:4px; padding:clamp(0.45rem, 0.8vh, 0.65rem) clamp(0.8rem, 1.2vw, 1.2rem);
  margin:0.2rem 0 0.45rem !important; box-shadow:0 1px 4px rgba(0,0,0,0.03);
  text-align:center !important;
}
.pregunta-titulo{
  font-size:.62rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--rojo); margin-bottom:.12rem; text-align:center !important;
}
.pregunta-texto{
  font-size:clamp(0.9rem, 0.98vw, 0.98rem); line-height:1.32; font-weight:600;
  color:var(--texto); margin-bottom:.42rem; text-align:center !important;
}
.st-key-pregunta div[data-testid="stHorizontalBlock"]{
  justify-content:center !important; align-items:center !important;
}
.st-key-pregunta .stButton button{
  background:#fff; border:1px solid var(--negro); font-weight:600; border-radius:4px;
  padding:.32rem .75rem; min-height:34px; font-size:.84rem; transition:all .15s ease;
  white-space:normal !important; height:auto !important;
}
.st-key-pregunta .stButton button:hover{
  background:var(--negro); color:#fff; border-color:var(--negro);
}

.nota{ font-size:.74rem; color:var(--suave); margin:.15rem 0; }
.separa{ height:1px; background:var(--linea); margin:clamp(0.25rem, 0.5vh, 0.4rem) 0; }

/* Botón de reinicio */
.st-key-reinicio,
.st-key-reinicio > div,
.st-key-reinicio [data-testid="stTooltipHoverTarget"],
.st-key-reinicio [data-testid="stElementToolbar"]{
  display:flex !important; justify-content:center !important; width:100% !important;
}
.st-key-reinicio button{
  width:clamp(40px, 4.4vh, 48px) !important; height:clamp(40px, 4.4vh, 48px) !important;
  min-height:clamp(40px, 4.4vh, 48px) !important;
  border-radius:50% !important; padding:0 !important;
  border:2px solid var(--negro) !important; background:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:all .2s cubic-bezier(.2,.85,.3,1); box-shadow:0 2px 6px rgba(0,0,0,0.05);
}
.st-key-reinicio button p{
  font-size:clamp(1.25rem, 1.5vw, 1.5rem) !important; line-height:1 !important;
  margin:0 !important; color:var(--negro) !important;
}
.st-key-reinicio button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important;
  transform:rotate(-90deg) scale(1.05);
}
.st-key-reinicio button:hover p{ color:#fff !important; }
.pie-nueva{
  text-align:center; font-size:.74rem; font-weight:600; color:var(--suave); margin:.2rem 0 0;
}

div[data-testid="stExpander"]{ border:none; background:transparent; margin-top:.1rem; }
div[data-testid="stExpander"] summary{ font-size:.8rem; color:var(--suave); padding:.1rem 0; }

/* ---------- Pantallas estrechas (móvil) ----------
   La herramienta se usa también desde el teléfono. Aquí no se rediseña nada:
   se le quita apretura al espacio, se deja que las columnas de la cabecera se
   apilen y se impide que un código de ocho cifras o una denominación larga
   desborden a lo ancho, que es lo que rompe la lectura en vertical. */
@media (max-width:760px){
  .block-container{ padding:0 .6rem .4rem !important; }

  .st-key-cabecera{ padding:.7rem .8rem; }
  .st-key-cabecera [data-testid="stHorizontalBlock"]{
    flex-wrap:wrap; gap:.4rem;
  }
  .st-key-cabecera [data-testid="stColumn"]{
    min-width:0;
  }

  .consulta-box{ padding:.5rem .6rem; }
  .consulta-texto{ font-size:.9rem; line-height:1.3; }

  /* Las otras ocupaciones: el código arriba y el nombre debajo, en vez de
     obligar a leer una línea larguísima con desplazamiento lateral. */
  .fila-otra{ flex-wrap:wrap; row-gap:2px; }
  .fila-otra .cod-otra{ flex:0 0 auto; }
  .fila-otra .den-otra{ flex:1 1 100%; white-space:normal; }

  /* Nada puede desbordar a lo ancho. */
  .stApp, .block-container{ overflow-x:hidden; }
}
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
    # Sale a GitHub. Se relee cada 5 minutos, asi que de vez en cuando una
    # busqueda paga este viaje sin que se note de donde viene.
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
    """Lematizador mínimo: número, después sufijo de agente, después género.

    Las tres fases son INDEPENDIENTES a propósito. Si se juntan en una cadena
    elif, el plural y el singular de la misma palabra dejan de lematizar
    igual: "montadores" se queda en "montador" (solo se aplica la regla de
    plural) mientras que "montador" llega a "mont". El catálogo está en plural
    y el ciudadano escribe en singular, así que dejan de encontrarse. Se probó
    el 21/08/2026 y rompía 216 nombres de agente del catálogo.

    Tampoco conviene meter aquí participios (-ado, -ido): en este catálogo
    "cuidado", "montado" o "trasdosado" son sustantivos, no formas verbales, y
    recortarlos los confunde con "cuidador" y "montador".

    El gerundio (-ando, -iendo) se probó y no aporta: el pase de
    interpretación ya normaliza la consulta antes de la búsqueda local.

    Antes de tocar esta función:  python evaluar.py && python estres.py
    """
    if len(w) > 5 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s"):
        w = w[:-1]
    if len(w) > 5 and w.endswith("or"):
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
8. PREGUNTA Y OPCIONES (DESAMBIGUACIÓN):
   - Rellena "pregunta" y "opciones" solo si hay duda para desempatar entre las DOS PRIMERAS ocupaciones. Si no hay duda, deja "pregunta": "" y "opciones": [].
   - La pregunta debe plantear una elección clara y directa (máximo 15 palabras).
   - El campo "opciones" DEBE contener una lista con las 2 alternativas concretas (ej. ["Atención en caja / mostrador", "Cocina y preparación de comida"], ["Casas particulares", "Residencias / Centros"], o ["Sí", "No"]). NUNCA dejes "opciones" vacío si hay "pregunta".
9. Si ninguna de las candidatas describe con precisión la actividad, rellena "otros_terminos" con entre 6 y 10 palabras sueltas de la CNO.

EJEMPLO DE RESPUESTA:
{"ocupaciones":[{"codigo":"51201027","denominacion":"CAMAREROS DE BARRA Y/O DEPENDIENTES DE CAFETERÍA","nivel":"00","motivo":"Atención en mostrador y servicio de comida rápida."},{"codigo":"93101024","denominacion":"PINCHES DE COCINA","nivel":"00","motivo":"Elaboración y preparación de alimentos en restauración."}],"pregunta":"¿A qué tarea dedicaba la mayor parte de su jornada?","opciones":["Atención en caja y mostrador","Preparación de comida en cocina"],"otros_terminos":""}
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


PAUSAS_429 = (4, 10, 20)   # segundos de espera ante un tope por minuto


def _flujo_gemini(cli, prompt):
    opciones = _configuraciones()
    ultimo = None
    espera = 0
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
                if por_minuto(e) and espera < len(PAUSAS_429):
                    # Tope por minuto: se espera y se vuelve a intentar con el
                    # MISMO modelo. No hay que cambiar de modelo por esto.
                    time.sleep(PAUSAS_429[espera])
                    espera += 1
                    continue
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


def limpia_opcion(texto):
    t = texto.strip().strip("¿?¡!.,;").strip()
    for _ in range(4):
        t = re.sub(
            r"^(?:la|el|los|las|un|una|unos|unas|en|a|al|del|de|para|con|por|su|sus)\s+",
            "", t, flags=re.IGNORECASE,
        ).strip()
    if len(t) > 44:
        t = t[:44].rsplit(" ", 1)[0]
        # Cortar por una palabra entera no basta: si el corte cae justo detras
        # de un conector queda "Gestion de contabilidad y", que no significa
        # nada. Se retrocede hasta que la ultima palabra tenga contenido.
        colgantes = {
            "y", "o", "u", "e", "de", "del", "al", "a", "en", "con", "por",
            "para", "sin", "sobre", "the", "la", "el", "los", "las", "un",
            "una", "mas", "más", "que", "su", "sus",
        }
        piezas = t.split()
        while len(piezas) > 1 and piezas[-1].lower().strip(",;") in colgantes:
            piezas.pop()
        t = " ".join(piezas) + "…"
    return t.capitalize()


def extraer_opciones(pregunta, opciones_modelo=None):
    if opciones_modelo and isinstance(opciones_modelo, list):
        limpias = [str(o).strip() for o in opciones_modelo if str(o).strip()]
        if len(limpias) >= 2 and set(limpias) != {"Sí", "No"}:
            return [limpia_opcion(x) for x in limpias[:3]]

    q = pregunta.strip().strip("¿?¡!").strip()
    fillers = [
        r"^su actividad principal consist[ií]a en\s+",
        r"^su tarea principal era\s+",
        r"^su labor principal era\s+",
        r"^su puesto era de\s+",
        r"^se dedicaba a\s+",
        r"^trabajaba en\s+",
        r"^realizaba tareas de\s+",
        r"^hac[ií]a funciones de\s+",
        r"^pasaba la mayor parte del tiempo en\s+",
        r"^se ocupaba de\s+",
        r"^hac[ií]a\s+",
        r"^era\s+",
        r"^realizaba\s+",
    ]
    q_limpia = q
    for f in fillers:
        q_limpia = re.sub(f, "", q_limpia, flags=re.IGNORECASE).strip()

    if " o " in q_limpia:
        partes = [p.strip() for p in re.split(r"\s+o\s+", q_limpia, maxsplit=1) if p.strip()]
        if len(partes) == 2:
            op1 = limpia_opcion(partes[0])
            op2 = limpia_opcion(partes[1])
            if op1 and op2 and op1.lower() != op2.lower():
                return [op1, op2]

    return ["Sí", "No"]


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
    opciones = extraer_opciones(pregunta, datos.get("opciones")) if pregunta else []

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
  margin:0; padding:0; background:transparent; font-family:'Libre Franklin',system-ui,sans-serif;
  --negro:#0A0A0A; --rojo:#D1122E; --texto:#1A1A1A; --suave:#555555;
  --linea:#E2E8F0; --gris:#F1F5F9;
  color:var(--texto); overflow:hidden;
}
.rejilla{
  display:grid; grid-template-columns:repeat(2,1fr);
  gap:6px; align-items:stretch;
}
@media (max-width:760px){ .rejilla{ grid-template-columns:1fr; } }

.tarjeta{
  background:#fff; border:1px solid var(--linea); border-left:4px solid #CBD5E1;
  border-radius:4px; padding:6px 12px; display:flex; flex-direction:column;
  justify-content:space-between; transition:transform .12s ease, box-shadow .12s ease;
  box-shadow:0 1px 3px rgba(0,0,0,0.03); cursor:pointer;
}
.tarjeta:hover{
  transform:translateY(-1px); box-shadow:0 3px 8px rgba(0,0,0,0.07); border-color:#CBD5E1;
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
  gap:8px; margin-bottom:2px;
}
.identificador{ display:flex; align-items:center; gap:8px; }
.orden{
  font-size:clamp(0.66rem, 0.72vw, 0.72rem); font-weight:700; color:var(--suave);
  font-family:'JetBrains Mono',monospace;
}
.codigo{
  font-size:clamp(1.05rem, 1.18vw, 1.2rem); font-weight:700;
  letter-spacing:.03em; color:var(--negro); font-family:'JetBrains Mono',monospace;
}

.copiar{
  font-family:'Libre Franklin',sans-serif; font-size:clamp(0.68rem, 0.74vw, 0.74rem);
  font-weight:600; color:var(--texto); background:#fff; border:1px solid #C4C4C4;
  border-radius:3px; padding:.2rem .6rem; cursor:pointer;
  transition:all .15s ease; white-space:nowrap;
}
.copiar:hover{ background:var(--negro); color:#fff; border-color:var(--negro); }
.copiar.hecho{ background:var(--rojo); border-color:var(--rojo); color:#fff; }

.denominacion{
  font-size:clamp(0.85rem, 0.94vw, 0.92rem); font-weight:600;
  line-height:1.25; color:var(--texto); margin:0 0 2px;
}
.motivo{
  font-size:clamp(0.74rem, 0.82vw, 0.8rem); color:var(--suave);
  line-height:1.24; margin-bottom:3px;
}

.etiquetas-fila{
  display:flex; align-items:center; gap:5px; flex-wrap:wrap; margin-top:auto; padding-top:2px;
}
.etiqueta{
  display:inline-flex; align-items:center; font-size:clamp(0.58rem, 0.65vw, 0.64rem);
  font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  padding:.12rem .4rem; border-radius:3px; background:var(--gris); color:var(--suave);
}
.etiqueta.recomendada{ background:var(--rojo); color:#fff; }
.etiqueta.mando{ background:#FFF7ED; color:#C2410C; border:1px solid #FFEDD5; }
"""

GUION_INTERACTIVO = """
// El alto del marco se calcula en el servidor suponiendo dos tarjetas por fila
// y 48 caracteres por linea. En el movil hay UNA columna y caben la mitad de
// letras, asi que el contenido crece y el marco lo recortaba. Aqui dentro si se
// conoce el ancho real: se mide lo dibujado y se corrige el alto del iframe.
function ajustaAlto(){
  try {
    var alto = document.documentElement.scrollHeight;
    var marco = window.frameElement;
    if (marco && alto > 0) {
      marco.style.height = (alto + 8) + 'px';
      marco.setAttribute('height', alto + 8);
    }
  } catch (e) { /* si el navegador no deja tocar el marco, se queda como estaba */ }
}
window.addEventListener('load', ajustaAlto);
window.addEventListener('resize', ajustaAlto);
setTimeout(ajustaAlto, 60);
setTimeout(ajustaAlto, 400);

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
    {type:'streamlit:setFrameHeight', height: document.documentElement.scrollHeight + 2},
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
        es_primera = (i == 1 and not o.get("provisional"))
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
        lineas_denom = max(1, math.ceil(len(o["denominacion"]) / 48))
        lineas_motivo = max(1, math.ceil(len(o["motivo"]) / 52)) if o.get("motivo") else 0
        h_denom = lineas_denom * 17
        h_motivo = (lineas_motivo * 15 + 2) if lineas_motivo else 0
        h_base = 54
        return h_base + h_denom + h_motivo

    alturas = [mide(o) for o in ocupaciones]
    filas = [alturas[i:i + 2] for i in range(0, len(alturas), 2)]
    estimada = sum(max(f) for f in filas) + 6 * max(0, len(filas) - 1) + 4

    # Permitir que el marco crezca: el guion lo ajusta al alto real una vez
    # dibujado, pero si el navegador no lo permite, mas vale que sobre sitio a
    # que se corte una tarjeta. Un hueco en blanco se perdona; un codigo
    # cortado por la mitad, no.
    estimada = int(estimada * 1.15) + 12

    components.html(
        f"<style>{ESTILO_TARJETAS}</style>"
        f"<div class=\"rejilla\">{''.join(trozos)}</div>"
        f"<script>{GUION_INTERACTIVO}</script>",
        height=estimada,
    )


def pinta_resultado(payload, estado=None, avance=0.06, interactivo=False, consulta=""):
    # Interruptor para la prueba masiva. resuelve() dibuja el resultado en
    # nueve puntos distintos; en una tanda de sesenta consultas eso llena la
    # pagina de tarjetas que nadie va a mirar. Cortando aqui se cortan los
    # nueve de una vez, y el circuito sigue siendo exactamente el mismo: se
    # calcula todo igual, solo que no se pinta.
    if st.session_state.get("silencio_pintado"):
        return

    if estado:
        st.progress(min(avance, 0.95), text=estado)
        # Antes se volvia aqui, asi que durante la espera solo se veia la barra.
        # Pero el catalogo ya ha respondido en el primer milisegundo y esos
        # resultados estaban calculados y guardados sin llegar a mostrarse. Si
        # los hay, se pintan bajo la barra y se sustituyen cuando el modelo
        # termina: la espera es la misma, pero deja de ser una pantalla vacia.
        if not payload.get("ocupaciones"):
            return
    if payload.get("aviso"):
        st.info(payload["aviso"])
        return

    ocupaciones = payload.get("ocupaciones", [])
    if not ocupaciones:
        st.info("No encuentro coincidencias claras. Prueba con el nombre del puesto o función concreta.")
        return

    # 1. PREGUNTA ARRIBA (antes de las tarjetas)
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
                opciones = extraer_opciones(payload.get("pregunta", ""), payload.get("opciones"))
                # El ancho se reparte segun lo que ocupa cada texto. Con el
                # limite en 44 caracteres hace falta algo mas de holgura que
                # antes, o el boton vuelve a cortar la frase por su cuenta.
                pesos = [max(1.2, len(opc) * 0.105) for opc in opciones]
                spacer = max(0.2, (9.0 - sum(pesos)) / 2.0)
                col_weights = [spacer] + pesos + [spacer]
                cols = st.columns(col_weights, gap="small")
                for idx, opc in enumerate(opciones):
                    with cols[idx + 1]:
                        # El identificador incluye la consulta: con uno fijo,
                        # dos busquedas dibujadas en la misma pantalla chocan y
                        # Streamlit aborta. No pasa buscando de una en una,
                        # pero si en la prueba masiva, donde tumbaba 18 de 40.
                        marca_op = f"resp_opt_{idx}_{abs(hash(consulta)) % 999983}"
                        if st.button(opc, key=marca_op, use_container_width=True):
                            st.session_state["respuesta"] = (consulta, payload["pregunta"], opc)
                            st.rerun()

    # 2. TARJETAS DE OCUPACIONES
    pinta_tarjetas(ocupaciones)

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
                    f'<div class="fila-otra" style="padding:.15rem 0;'
                    f'border-bottom:1px solid var(--linea);display:flex;gap:.5rem">'
                    f'<span class="cod-otra" style="font-family:JetBrains Mono,monospace;'
                    f'font-weight:700;font-size:.82rem;letter-spacing:.04em">{cod}</span>'
                    f'<span class="den-otra" style="font-size:.82rem">{den}</span></div>',
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

class cronometra:
    """Mide lo que tarda cada llamada al modelo y lo guarda para el panel.

    Solo observa: no cambia ni un resultado. Sirve para dejar de decidir a ojo
    dónde se va el tiempo. Se ve en el panel de ajustes con ?mantenimiento=1.
    """

    def __init__(self, etiqueta):
        self.etiqueta = etiqueta

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *_):
        segundos = time.perf_counter() - self.t0
        st.session_state.setdefault("tiempos", []).append((self.etiqueta, segundos))
        return False


def _basica(encontrados, motivo=""):
    # "provisional" marca lo que sale del catalogo sin que el modelo lo haya
    # revisado: ni durante la espera, ni cuando el modelo falla. Esas tarjetas
    # no llevan la marca de recomendada, porque nadie las ha recomendado.
    return [{
        "codigo": c, "denominacion": d, "nivel": "00",
        "nivel_texto": NIVELES["00"], "motivo": motivo,
        "provisional": True,
    } for _, c, d in encontrados[:5]]


def raices_de(texto):
    """Raices con contenido de un texto, con el mismo criterio que el indice."""
    return {
        raiz(p) for p in re.findall(r"[a-zñáéíóúü]+", normaliza(texto))
        if len(p) > 2 and p not in VACIAS
    }


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
    # Lo que devuelve el buscador para lo que ESCRIBIO la persona, antes de que
    # la IA reinterprete y sustituya `encontrados`. La decision de quien manda
    # tiene que tomarse sobre esto, no sobre la busqueda reescrita por el
    # modelo: ahi las puntuaciones se aplanan y la ventaja real desaparece.
    literales = encontrados[:2]
    _raices_consulta = raices_de(busqueda or texto)
    st.session_state["tiempos"] = []
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
        "ocupaciones": _basica(encontrados, "Resultado del catálogo, sin afinar todavía."),
        "otras": [(c, d) for _, c, d in encontrados[5:12]],
    }

    # ATAJO SIN IA.
    # Si el buscador saca al segundo mas de VENTAJA_CLARA veces, la persona ha
    # escrito practicamente el nombre de la ocupacion y no hay nada que
    # interpretar. Llamar al modelo ahi solo anade tres viajes a Google, entre
    # diez y treinta segundos de espera y consumo de cuota, para acabar en el
    # mismo sitio (o peor: para "montador de placa de pladur" proponia
    # escayolistas). Se contesta al instante con lo que dice el catalogo.
    # El resto de resultados sigue disponible en "Ver otras ocupaciones".
    if literales and not contexto:
        segundo_l = literales[1][0] if len(literales) > 1 else 0.0
        holgado = segundo_l <= 0 or (literales[0][0] / segundo_l) > VENTAJA_CLARA
        # Puntuar mucho NO es acertar. "limpiadora de casas" sacaba 14 veces al
        # segundo y devolvia OPERADORES DE MAQUINA LIMPIADORA DE METALES: el
        # catalogo veia la palabra "limpiadora" dentro del nombre de una
        # maquina. Por eso ahora se exige ademas que el candidato explique
        # TODAS las palabras de la consulta. Si sobra alguna sin explicar, hay
        # algo que interpretar y tiene que verlo el modelo.
        # Doble condicion, y las dos hacen falta:
        #   a) el candidato explica TODAS las palabras con contenido de la
        #      consulta, y
        #   b) la consulta cubre al menos el 40 % del candidato.
        # Sin (b), "limpiadora de casas" se colaba: "casas" es palabra vacia,
        # asi que la consulta se reducia a "limpiador" y encajaba con
        # OPERADORES DE MAQUINA LIMPIADORA DE METALES, del que solo explicaba
        # una palabra de cinco. Medido sobre 60 consultas reales: quedan seis
        # atajos, todos correctos, y desaparecen los dos disparates.
        propias = raices_de(literales[0][2])
        encaje_l = len(_raices_consulta & propias) / len(propias) if propias else 0
        explica = (
            bool(_raices_consulta)
            and _raices_consulta.issubset(propias)
            and encaje_l >= ENCAJE_MINIMO
        )
        if holgado and explica:
            _, cod_a, den_a = literales[0]
            atajo = {
                "ocupaciones": [{
                    "codigo": cod_a,
                    "denominacion": den_a,
                    "nivel": "00",
                    "nivel_texto": NIVELES["00"],
                    "motivo": "Coincidencia directa con lo que escribiste.",
                }],
                "otras": [(c, d) for _, c, d in encontrados[1:9]],
            }
            with zona.container():
                pinta_resultado(atajo)
            memoria[clave] = atajo
            return atajo

    if cli is None:
        with zona.container():
            pinta_resultado(provisional)
        return provisional

    with zona.container():
        # Antes se enseñaban aqui las tarjetas del catalogo mientras el modelo
        # trabajaba. Medido sobre 60 consultas reales, ese primer resultado es
        # disparatado a menudo ("ayudante de albañil" -> ayudantes de
        # hosteleria), y verlo dos segundos destruye la confianza en el que
        # sale despues. Como afinar tarda unos dos segundos, no compensa.
        pinta_resultado({}, estado="Afinando el resultado")

    interpretado, aviso = None, ""
    with zona.container():
        pinta_resultado({}, estado="Interpretando el oficio", avance=0.12)
    with cronometra("1. Interpretar el oficio"):
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
                "ocupaciones": _basica(encontrados, "Resultado del catálogo, sin afinar todavía."),
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
        with cronometra("2. Afinar el resultado"):
            payload = consulta_al_modelo(lista, "Afinando el resultado")

        if payload.get("mas_terminos"):
            with zona.container():
                pinta_resultado({}, estado="Ampliando la búsqueda", avance=0.45)
            ampliados = busca(f"{busqueda or texto} {payload['mas_terminos']}",
                              tope=N_CANDIDATOS + 6)
            if ampliados:
                encontrados = ampliados
                interpretado = ("la descripción", payload["mas_terminos"])
                with cronometra("3. Ampliar la búsqueda"):
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

    # Se muestran las ocupaciones que el modelo considera pertinentes. Ya no se
    # completan seis tarjetas siempre: rellenar el hueco con lo siguiente del
    # catalogo, sin relacion con lo buscado, restaba confianza en las buenas.
    # Lo descartado sigue a un clic, en "Ver otras ocupaciones del catalogo".
    #
    # Pero el buscador no puede quedar mudo. El modelo reinterpreta la consulta
    # y a veces se aleja de lo que se escribio: para "montador de placa de
    # pladur" ha llegado a proponer escayolistas y albañiles, que no estan ni
    # entre los seis mejores del catalogo. Por eso:
    #
    #   1) el mejor resultado del buscador entra SIEMPRE como tarjeta,
    #   2) y si ademas arrasa (VENTAJA_CLARA veces mas puntos que el segundo),
    #      se pone el primero y es el que lleva la marca de recomendada.
    #
    # Una ventaja aplastante significa que la persona escribio casi el nombre
    # exacto de la ocupacion; ahi interpretar sobra. Cuando la ventaja es corta
    # hay ambiguedad real y manda el modelo, que para eso esta.
    ya = {o["codigo"] for o in payload["ocupaciones"]}
    if literales:
        puntos, codigo_c, denom = literales[0]
        segundo = literales[1][0] if len(literales) > 1 else 0.0
        arrasa = segundo <= 0 or (puntos / segundo) > VENTAJA_CLARA

        # Solo se cuela si el catalogo explica lo que se escribio. Si no, se
        # estaria metiendo una ocupacion sin relacion entre las recomendadas,
        # que es justo lo que resta fiabilidad.
        propias_rs = raices_de(denom)
        encaje_rs = (len(_raices_consulta & propias_rs) / len(propias_rs)
                     if propias_rs else 0)
        merece = (
            bool(_raices_consulta)
            and _raices_consulta.issubset(propias_rs)
            and encaje_rs >= ENCAJE_MINIMO
        )
        if codigo_c not in ya and merece:
            tarjeta = {
                "codigo": codigo_c,
                "denominacion": denom,
                "nivel": "00",
                "nivel_texto": NIVELES["00"],
                "motivo": "Mejor coincidencia del catálogo con lo que escribiste.",
            }
            if arrasa:
                payload["ocupaciones"].insert(0, tarjeta)
            else:
                payload["ocupaciones"].append(tarjeta)
        elif (
            arrasa
            and codigo_c in ya
            and payload["ocupaciones"]
            and payload["ocupaciones"][0]["codigo"] != codigo_c
        ):
            # Solo se sube al primer puesto si la ocupacion ESTA entre las
            # tarjetas. Faltaba comprobarlo: cuando la red de seguridad no la
            # anadia por no explicar la consulta, este bloque la buscaba
            # igualmente y reventaba la consulta entera (StopIteration).
            i_mejor = next(
                i for i, o in enumerate(payload["ocupaciones"])
                if o["codigo"] == codigo_c
            )
            payload["ocupaciones"].insert(0, payload["ocupaciones"].pop(i_mejor))

    elegidos = {o["codigo"] for o in payload["ocupaciones"]}
    payload["otras"] = [(c, d) for _, c, d in encontrados if c not in elegidos][:8]

    zona.empty()
    pinta_resultado(payload)
    memoria[clave] = payload
    return payload


# === FIN DEL MOTOR ===
# No muevas esta línea ni la borres: las pruebas (evaluar.py, estres.py)
# cargan app.py hasta aquí para probar el buscador sin dibujar pantalla.
# Todo lo que vaya por debajo es interfaz y no se prueba.

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
st.session_state.setdefault("masiva_abierta", False)
# Nunca debe quedarse echado entre recargas: si la prueba se corta a medias,
# la herramienta se quedaria muda para el usuario normal.
st.session_state["silencio_pintado"] = False
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

        tiempos = st.session_state.get("tiempos", [])
        if tiempos:
            st.caption("Última consulta, segundo a segundo:")
            for etiqueta, seg in tiempos:
                st.caption(f"· {etiqueta}: **{seg:.1f} s**")
            st.caption(f"· Total esperando al modelo: **{sum(t for _, t in tiempos):.1f} s**")

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

            st.markdown("**Prueba masiva**")
            st.caption(
                "Lanza muchas consultas seguidas por el circuito completo, con "
                "IA incluida, y deja un registro descargable. Gasta cuota: cada "
                "consulta son dos o tres llamadas al modelo."
            )
            if st.button("Preparar la prueba", use_container_width=True):
                st.session_state["masiva_abierta"] = True
                st.rerun()


PUESTOS_HABITUALES = """Eres orientador laboral en una oficina de empleo de Madrid.

Escribe una lista de puestos de trabajo tal y como los diría una persona que viene a
buscar empleo, NO como los nombra un catálogo oficial. Es decir: "chico de almacén",
"ayudante de cocina", "reponedor de supermercado", "chapista", "teleoperadora",
"limpieza de portales" — lenguaje de la calle, no terminología administrativa.

REGLAS:
- Uno por línea, sin numerar, sin guiones, sin comillas, sin explicaciones.
- Reparte entre sectores distintos: hostelería, comercio, construcción, limpieza,
  cuidados, logística, administración, industria, transporte, atención al cliente.
- Mezcla formas de decirlo: unas con el nombre del oficio a secas, otras como una
  frase corta describiendo lo que hacía la persona.
- Sin tildes, como escribe la gente con prisa.
- Puestos reales del mercado de la Comunidad de Madrid, ni raros ni de laboratorio.

Devuelve solo la lista."""


def pide_puestos_habituales(cuantos):
    """Genera consultas parecidas a las que teclea la gente de verdad.

    Las denominaciones del catalogo sirven para comprobar convergencia, pero no
    se parecen a lo que escribe un demandante. Esto acerca la prueba al uso real.
    """
    cli = cliente()
    if cli is None:
        return []
    peticion = f"Dame {int(cuantos)} puestos distintos."
    try:
        cfg = dict(system_instruction=PUESTOS_HABITUALES, max_output_tokens=1600)
        if PROVEEDOR == "gemini":
            r = cli.models.generate_content(
                model=modelo_actual(), contents=peticion,
                config=types.GenerateContentConfig(**cfg),
            )
            bruto = (getattr(r, "text", "") or "")
        else:
            r = cli.chat.completions.create(
                model=modelo_actual(),
                messages=[{"role": "system", "content": PUESTOS_HABITUALES},
                          {"role": "user", "content": peticion}],
                max_tokens=1600,
            )
            bruto = r.choices[0].message.content or ""
    except Exception:  # noqa: BLE001
        return []
    limpias = []
    for linea in bruto.splitlines():
        t = re.sub(r"^[\s\-\*\d\.\)]+", "", linea).strip().strip('"').strip()
        if 3 < len(t) < 70:
            limpias.append(t)
    return limpias


def pantalla_masiva():
    """Prueba de estrés con IA de verdad, para el modo mantenimiento.

    Lanza consultas por el MISMO camino que usa una persona: llama a resuelve(),
    no a una copia. Si se replicara el circuito, mediríamos algo que no es lo que
    ve el usuario, que es justo el error que queremos evitar.

    Deja un registro con lo pedido y lo devuelto, para poder ver si el fallo está
    en interpretar la consulta o en elegir entre los candidatos.
    """
    st.markdown('<div class="seccion">Prueba masiva del buscador</div>',
                unsafe_allow_html=True)
    st.caption(
        "Cada consulta gasta dos o tres llamadas al modelo. Con 100 consultas "
        "se van entre 200 y 300, así que vigila la cuota del día."
    )

    por_defecto = ""
    try:
        with open("casos.csv", encoding="utf-8-sig") as f:
            filas = list(csv.DictReader(f, delimiter=";"))
        por_defecto = "\n".join(x["consulta"] for x in filas)
    except Exception:  # noqa: BLE001
        pass

    hay_resultados = bool(st.session_state.get("masiva_filas"))
    ajustes = st.expander("Ajustes de la prueba", expanded=not hay_resultados)

    with ajustes:
        origen = st.radio(
            "De dónde salen las consultas",
            ["Escritas a mano", "Puestos habituales, propuestos por la IA",
             "Ocupaciones del catálogo, al azar"],
            horizontal=True, key="masiva_origen",
        )

        if origen == "Escritas a mano":
            st.session_state.setdefault("masiva_texto", por_defecto)
            texto = st.text_area(
                "Consultas, una por línea", height=200, key="masiva_texto",
                help="Vienen cargados los casos de casos.csv. Borra y pega las "
                     "tuyas: el tope es cuántas líneas escribas aquí.",
            )
            consultas = [x.strip() for x in texto.splitlines() if x.strip()]
        elif origen == "Puestos habituales, propuestos por la IA":
            cuantos = st.number_input("Cuántos puestos pedir", 10, 200, 60, 10)
            if st.button("Pedir la lista", use_container_width=True):
                with st.spinner("Pidiendo la lista…"):
                    lista = pide_puestos_habituales(cuantos)
                if lista:
                    st.session_state["masiva_ia"] = lista
                    st.session_state["masiva_ia_texto"] = "\n".join(lista)
                else:
                    st.error("No he podido pedir la lista. Comprueba la conexión con la IA.")
            lista = st.session_state.get("masiva_ia", [])
            if lista:
                st.session_state.setdefault("masiva_ia_texto", "\n".join(lista))
                texto = st.text_area(
                    "Lista propuesta, editable", height=220, key="masiva_ia_texto",
                    help="Repásala antes de lanzar: quita lo que no se parezca a lo "
                         "que oyes en el mostrador y añade lo que eches de menos.",
                )
                consultas = [x.strip() for x in texto.splitlines() if x.strip()]
                st.caption(f"{len(consultas)} consultas listas.")
            else:
                consultas = []
                st.caption("Pide la lista para empezar. Es una sola llamada al modelo.")

        else:
            # Buscar cada ocupacion por su propio nombre oficial. Es la version con
            # IA de la prueba de convergencia que estres.py hace en local: si el
            # circuito completo no encuentra una ocupacion escribiendo su nombre
            # exacto, el problema es gordo y no es del vocabulario.
            cuantas = st.number_input(
                "Cuántas ocupaciones coger", 5, 300, 50, 5,
                help=f"El catálogo tiene {len(IDX['registros'])}. Se cogen al azar, "
                     "sin repetir, y se buscan por su denominación oficial.",
            )
            semilla = st.number_input(
                "Semilla", 0, 9999, 1, 1,
                help="Con la misma semilla salen las mismas ocupaciones, para poder "
                     "repetir la prueba después de un cambio y comparar.",
            )
            muestra = list(IDX["registros"])
            random.Random(int(semilla)).shuffle(muestra)
            consultas = [r["denom"] for r in muestra[: int(cuantas)]]
            st.caption(f"{len(consultas)} ocupaciones elegidas. Ejemplo: {consultas[0][:60]}")

        if st.session_state.get("masiva_n_previo") != len(consultas):
            st.session_state["masiva_n_previo"] = len(consultas)
            st.session_state.pop("masiva_tope", None)

        c1, c2, c3 = st.columns(3, gap="small")
        # Por defecto se lanzan TODAS. Antes venia puesto en 40 y, como ademas se
        # reiniciaba al cambiar la lista, cortaba las tandas grandes sin avisar.
        tope = c1.number_input(
            "Cuántas lanzar", 1, max(len(consultas), 1), max(len(consultas), 1),
            key="masiva_tope",
            help="Por defecto, todas las disponibles. Bájalo para hacer una cata "
                 "antes de gastar cuota en la tanda entera.",
        )
        pausa = c2.number_input(
            "Pausa entre consultas (s)", 0.0, 60.0, 12.0, 1.0,
            help="El plan gratuito admite 15 peticiones por minuto y cada consulta "
                 "gasta dos o tres. Con 12 segundos caben unas cinco por minuto, "
                 "que es el ritmo máximo sostenible. Bajarlo hace que la prueba se "
                 "caiga y midas resultados sin afinar.",
        )
        limpiar = c3.checkbox("Ignorar lo ya guardado", value=True,
                              help="Vacía la memoria de la sesión para que ninguna "
                                   "consulta se responda con un resultado antiguo.")

    lanzar, volver = st.columns(2, gap="small")
    arranca = lanzar.button("Lanzar", type="primary", use_container_width=True,
                            disabled=not consultas)
    if volver.button("Volver al buscador", use_container_width=True):
        st.session_state["masiva_abierta"] = False
        st.rerun()

    if arranca:
        if limpiar:
            st.session_state["cache"] = {}
            st.session_state["interpretaciones"] = {}

        aviso = st.empty()
        barra = st.progress(0.0)
        # resuelve() pinta el resultado completo de cada consulta: tarjetas,
        # marco y desplegable. En una tanda de 60 eso estira la pagina sin
        # parar. Se pinta igual, porque el circuito tiene que ser el mismo que
        # usa una persona, pero dentro de un desplegable CERRADO: asi no ocupa
        # sitio y, si alguna vez interesa mirar como va resolviendo, se abre.
        # (Un display:none por CSS no funcionaba: la clase no llegaba al cajon.)
        oculto = st.empty()
        filas = []
        st.session_state["silencio_pintado"] = True

        lote = consultas[: int(tope)]
        for i, consulta in enumerate(lote, 1):
            aviso.caption(f"**{i} de {len(lote)}** · {consulta[:70]}")
            barra.progress(i / len(lote), text="")

            locales = busca(consulta, tope=3)
            arranque = time.perf_counter()
            try:
                payload = resuelve(consulta, oculto, usar_ia=True) or {}
                error = ""
                # Si algo dentro creo un elemento de pantalla pese al silencio,
                # se descarta aqui para que no arrastre a la consulta siguiente.
                st.session_state.pop("respuesta", None)
            except Exception as e:  # noqa: BLE001
                payload, error = {}, f"{type(e).__name__}: {e}"[:160]
                st.session_state["silencio_pintado"] = True
            tardanza = time.perf_counter() - arranque

            # Las escrituras al diccionario compartido se descartan: una prueba
            # de cientos de consultas no debe ensuciar lo que aprenden todos.
            st.session_state["por_guardar"] = []
            st.session_state["refuerzos_por_guardar"] = []

            elegidas = payload.get("ocupaciones", []) or []
            codigos = [o["codigo"] for o in elegidas]
            top_local = locales[0][1] if locales else ""
            interpretado = payload.get("interpretado") or ("", "")
            tiempos = st.session_state.get("tiempos", [])

            filas.append({
                "consulta": consulta,
                "top_local": top_local,
                "denom_local": locales[0][2] if locales else "",
                "ventaja_local": (
                    round(locales[0][0] / locales[1][0], 2)
                    if len(locales) > 1 and locales[1][0] else ""
                ),
                "interpretado_como": interpretado[1] if len(interpretado) > 1 else "",
                "elegido_1": codigos[0] if codigos else "",
                "denom_1": elegidas[0]["denominacion"] if elegidas else "",
                "elegido_2": codigos[1] if len(codigos) > 1 else "",
                "elegido_3": codigos[2] if len(codigos) > 2 else "",
                "n_tarjetas": len(elegidas),
                "local_en_puesto": (codigos.index(top_local) + 1)
                                   if top_local in codigos else 0,
                "coincide_con_local": "si" if codigos[:1] == [top_local] else "no",
                "pregunta": payload.get("pregunta", ""),
                "sin_afinar": "si" if payload.get("fallo") else "no",
                # El motivo del fallo es EL dato para saber si es cuota, límite
                # por minuto o modelo caído. Sin él solo sabemos que falló.
                "motivo_fallo": str(payload.get("fallo", ""))[:200],
                "modelo": modelo_actual(),
                "error": error,
                "segundos": round(tardanza, 1),
                "detalle_tiempos": " | ".join(f"{n}:{t:.1f}" for n, t in tiempos),
            })

            # Solo hay que dar aire si la consulta ha gastado peticiones. Las
            # que resuelve el atajo no tocan el modelo, asi que esperar
            # despues de ellas es tiempo tirado: con el atajo resolviendo
            # cerca de la mitad, esto casi parte por dos lo que tarda la tanda.
            if pausa and tiempos:
                time.sleep(float(pausa))

        st.session_state["silencio_pintado"] = False
        st.session_state["masiva_filas"] = filas
        oculto.empty()
        aviso.empty()
        barra.empty()

    filas = st.session_state.get("masiva_filas", [])
    if filas:
        salida = io.StringIO()
        w = csv.DictWriter(salida, fieldnames=list(filas[0]), delimiter=";")
        w.writeheader()
        w.writerows(filas)

        distintos = sum(1 for f in filas if f["coincide_con_local"] == "no")
        fallidos = sum(1 for f in filas if f["sin_afinar"] == "si" or f["error"])
        preguntas = sum(1 for f in filas if f["pregunta"])
        medio = sum(f["segundos"] for f in filas) / len(filas)

        st.success(f"{len(filas)} consultas lanzadas.")
        a, b, c, d = st.columns(4)
        a.metric("El modelo cambia el 1º", f"{distintos}")
        b.metric("Sin afinar o con error", f"{fallidos}")
        c.metric("Piden aclaración", f"{preguntas}")
        d.metric("Media", f"{medio:.1f} s")
        st.caption(
            "«El modelo cambia el 1º» cuenta las veces que la recomendada no es "
            "la que el catálogo ponía primero. No significa que esté mal: "
            "significa que ahí decidió el modelo, y son las que hay que mirar."
        )

        st.download_button(
            "Descargar el registro", salida.getvalue().encode("utf-8-sig"),
            file_name="prueba_masiva.csv", mime="text/csv",
            use_container_width=True, type="primary",
        )
        with st.expander(f"Ver las {len(filas)} filas"):
            st.dataframe(filas, use_container_width=True, height=320)


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

# La prueba masiva ocupa la pantalla entera mientras esta abierta. Solo se llega
# con ?mantenimiento=1, asi que quien usa la herramienta a diario no la ve.
if MANTENIMIENTO and st.session_state["masiva_abierta"]:
    pantalla_masiva()

elif entrada:
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

# Estas dos escrituras van a la API de GitHub y ocurren AL TERMINAR la
# busqueda, cuando el usuario ya cree que ha acabado. No se veian en el panel
# porque solo se cronometraba a Gemini; aqui pueden irse varios segundos.
_pendientes = st.session_state.pop("por_guardar", [])
if _pendientes:
    with cronometra("4. Guardar términos aprendidos (GitHub)"):
        for clave, valor in _pendientes:
            guarda_termino(clave, valor)

_refuerzos = st.session_state.pop("refuerzos_por_guardar", [])
if _refuerzos:
    with cronometra("5. Guardar correcciones de orden (GitHub)"):
        for codigo, palabras in _refuerzos:
            guarda_refuerzo(codigo, palabras)

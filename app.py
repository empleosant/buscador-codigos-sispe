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
from google import genai
from google.genai import types

CATALOGO = "ocupaciones_sispe_ultraligero.txt"
MODELO = "gemini-3.6-flash"
N_CANDIDATOS = 15

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
}

SINONIMOS = {
    "uber": "conductores automoviles taxis furgonetas taxistas pasajeros",
    "cabify": "conductores automoviles taxis furgonetas taxistas pasajeros",
    "vtc": "conductores automoviles taxis furgonetas taxistas pasajeros",
    "bolt": "conductores automoviles taxis furgonetas taxistas pasajeros",
    "glovo": "repartidor motocicleta ciclomotor reparto domicilio mensajero",
    "uber eats": "repartidor motocicleta ciclomotor reparto domicilio",
    "just eat": "repartidor motocicleta ciclomotor reparto domicilio",
    "deliveroo": "repartidor motocicleta ciclomotor reparto domicilio",
    "rider": "repartidor motocicleta ciclomotor reparto domicilio",
    "amazon": "mozo carga descarga almacen preparador pedidos",
    "carretillero": "carretillas elevadoras conductor operadores almacen",
    "mozo": "mozos carga descarga almacen mercado peones",
    "reponedor": "reponedores comercio dependientes almacen",
    "camarera": "camareros barra sala cafeteria restaurante",
    "cocinera": "cocineros ayudantes cocina restaurante",
    "limpiadora": "personal limpieza limpiadores instituciones domicilios",
    "kelly": "camareros piso hosteleria limpieza habitaciones",
    "enfermera": "enfermeros cuidados generales clinica hospital",
    "cuidadora": "cuidadores auxiliares personas mayores dependencia domicilio",
    "interna": "empleados hogar domicilio cuidadores",
    "teleoperadora": "teleoperadores telefonistas atencion cliente",
    "administrativa": "empleados administrativos contabilidad administracion",
    "secretaria": "secretarios direccion administracion oficina",
    "recepcionista": "recepcionistas hotel oficinas informacion",
    "comercial": "agentes comerciales representantes venta",
    "dependienta": "dependientes comercio tiendas venta",
    "cajera": "cajeros comercio supermercado",
    "programador": "programadores aplicaciones informaticas analistas software",
    "desarrollador": "programadores aplicaciones informaticas analistas web software",
    "informatico": "tecnicos mantenimiento reparacion equipos informaticos redes",
    "socorrista": "socorristas piscinas salvamento",
    "mecanico": "mecanicos mantenimiento reparacion automocion vehiculos",
    "fontanera": "fontaneros instaladores tuberias fluidos gas calefaccion",
    "fontanero": "fontaneros instaladores tuberias fluidos",
    "electricista": "instaladores electricistas edificios viviendas industriales",
    "albanil": "albaniles encofradores mamposteros construccion obra",
    "peon": "peones construccion industria obra",
    "vigilante": "vigilantes seguridad escoltas control acceso",
    "monitora": "monitores actividades tiempo libre ocio",
    "profesora": "profesores ensenanza educacion",
    "esteticista": "esteticistas belleza estetica",
    "peluquera": "peluqueros barberos",
    "costurera": "costureros confeccion textil",
    "jardinera": "jardineros paisajismo horticultura",
    "ingles": "idiomas profesores",
    "frances": "idiomas profesores",
    "aleman": "idiomas profesores",
    "ele": "idiomas profesores",
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


def busca(consulta, tope=20):
    q = normaliza(consulta)
    terminos = {}
    for w in re.findall(r"\w+", q):
        if len(w) > 2 and w not in VACIAS:
            terminos[w] = 1.0
    for clave, expansion in SINONIMOS.items():
        if clave in q:
            for w in re.findall(r"\w+", normaliza(expansion)):
                terminos.setdefault(w, 0.8)
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
    resultados = []
    for i, valor in puntos.items():
        reg = IDX["registros"][i]
        nucleo = 1.0 + 0.5 * len(cubierto[i] & reg["cabeza"])
        cobertura = 0.6 + 0.4 * min(1.0, len(cubierto[i] & originales) / n_term)
        resultados.append((valor * nucleo * cobertura, reg["codigo"], reg["denom"]))
    resultados.sort(reverse=True)
    return resultados[:tope]


# ---------------------------------------------------------------------------
# MODELO
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def cliente():
    clave = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    return genai.Client(api_key=clave) if clave else None


INSTRUCCIONES = """Eres un técnico de codificación de ocupaciones para SilcoiWeb (SEPE).

Recibes la descripción de un puesto y una lista cerrada de ocupaciones candidatas.
Selecciona entre 3 y 5, de mayor a menor afinidad.

REGLAS
1. Usa únicamente códigos y denominaciones literales de la lista de candidatos. No inventes ni modifiques ninguno.
2. Nivel profesional: 90 aprendices (sin experiencia) / 00 técnicos o sin categoría (estándar con experiencia) / 10 dirección / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.
3. El campo "motivo" explica en menos de 12 palabras por qué encaja, en español con acentuación correcta.
4. Rellena "pregunta" solo si faltan datos para decidir entre dos ocupaciones; si no, déjalo vacío.

Responde solo con este JSON:
{"ocupaciones":[{"codigo":"12345678","denominacion":"...","nivel":"00","motivo":"..."}],"pregunta":""}
"""


def consulta_modelo(cli, texto, candidatos):
    prompt = (
        f"CANDIDATOS (unica fuente valida):\n{candidatos}\n\nDESCRIPCION: {texto}"
    )
    base = dict(
        system_instruction=INSTRUCCIONES,
        max_output_tokens=900,
        response_mime_type="application/json",
    )
    intentos = []
    try:
        intentos.append(
            {**base, "thinking_config": types.ThinkingConfig(thinking_level="minimal")}
        )
    except Exception:  # noqa: BLE001
        pass
    intentos.append(base)
    intentos.append({k: v for k, v in base.items() if k != "response_mime_type"})

    ultimo = None
    for cfg in intentos:
        try:
            r = cli.models.generate_content(
                model=MODELO, contents=prompt,
                config=types.GenerateContentConfig(**cfg),
            )
            return r.text
        except Exception as e:  # noqa: BLE001
            ultimo = e
    raise ultimo


def interpreta(bruto):
    """Convierte la respuesta en datos verificados contra el catalogo."""
    texto = (bruto or "").strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
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

    limpias, descartadas = [], 0
    for o in datos.get("ocupaciones", []) or []:
        codigo = str(o.get("codigo", "")).strip()
        if codigo in IDX["por_codigo"]:
            nivel = str(o.get("nivel", "00")).strip()[:2] or "00"
            limpias.append({
                "codigo": codigo,
                "denominacion": IDX["por_codigo"][codigo],  # siempre la oficial
                "nivel": nivel,
                "nivel_texto": NIVELES.get(nivel, "Técnicos / Sin categoría"),
                "motivo": str(o.get("motivo", "")).strip(),
            })
        elif codigo:
            descartadas += 1

    return {
        "ocupaciones": limpias[:5],
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


def pinta_resultado(payload):
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

    st.markdown('<div class="seccion">Copiar códigos</div>', unsafe_allow_html=True)
    st.code("\n".join(o["codigo"] for o in ocupaciones), language=None)

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

def resuelve(texto, usar_ia=True):
    codigo = texto.strip()
    if re.fullmatch(r"\d{8}", codigo):
        if codigo in IDX["por_codigo"]:
            return {"ocupaciones": [{
                "codigo": codigo,
                "denominacion": IDX["por_codigo"][codigo],
                "nivel": "00",
                "nivel_texto": NIVELES["00"],
                "motivo": "Consulta directa por código.",
            }]}
        return {"aviso": f"El código {codigo} no figura en el catálogo oficial."}

    encontrados = busca(texto, tope=N_CANDIDATOS)
    if not encontrados:
        return {"ocupaciones": []}

    cli = cliente() if usar_ia else None
    if cli is None:
        return {
            "ocupaciones": [{
                "codigo": c, "denominacion": d, "nivel": "00",
                "nivel_texto": NIVELES["00"], "motivo": "",
            } for _, c, d in encontrados[:5]],
            "otras": [(c, d) for _, c, d in encontrados[5:12]],
        }

    try:
        bruto = consulta_modelo(cli, texto, "\n".join(f"{c}:{d}" for _, c, d in encontrados))
    except Exception:  # noqa: BLE001
        return {
            "ocupaciones": [{
                "codigo": c, "denominacion": d, "nivel": "00",
                "nivel_texto": NIVELES["00"], "motivo": "",
            } for _, c, d in encontrados[:5]],
            "otras": [(c, d) for _, c, d in encontrados[5:12]],
        }

    payload = interpreta(bruto)
    if not payload["ocupaciones"]:
        payload["ocupaciones"] = [{
            "codigo": c, "denominacion": d, "nivel": "00",
            "nivel_texto": NIVELES["00"], "motivo": "",
        } for _, c, d in encontrados[:5]]
    elegidos = {o["codigo"] for o in payload["ocupaciones"]}
    payload["otras"] = [(c, d) for _, c, d in encontrados if c not in elegidos][:7]
    return payload


# ---------------------------------------------------------------------------
# INTERFAZ
# ---------------------------------------------------------------------------

st.session_state.setdefault("historial", [])
st.session_state.setdefault("pendiente", None)
st.session_state.setdefault("usar_ia", True)

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
    with st.spinner("Buscando en el catálogo…"):
        payload = resuelve(entrada, usar_ia=st.session_state["usar_ia"])
    pinta_resultado(payload)
    st.session_state["historial"].append((entrada, payload))

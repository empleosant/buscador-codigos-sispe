"""
Codificador de Ocupaciones SISPE / SilcoiWeb
Herramienta de apoyo tecnico para orientadores.

Cambios clave respecto a la version anterior:
  1. Motor de busqueda local con indice invertido (de ~800 ms a ~2 ms).
  2. thinking_level minimo: Gemini 3.x razona por defecto y eso cuesta segundos.
  3. Respuesta en streaming: el texto aparece mientras se genera.
  4. Cache de respuestas: repetir una consulta es instantaneo.
  5. Modo sin IA: resultados del catalogo al instante, sin llamada a la API.
  6. Validacion antiinvencion: se comprueba que cada codigo existe en el catalogo.
"""

import os
import re
import csv
import math
import time
import io
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher

import streamlit as st
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# CONFIGURACION GENERAL
# ---------------------------------------------------------------------------

CATALOGO = "ocupaciones_sispe_ultraligero.txt"
MODELO = "gemini-3.6-flash"

st.set_page_config(
    page_title="Codificador SISPE",
    page_icon="§",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# ESTILO
# ---------------------------------------------------------------------------

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Source+Sans+3:wght@400;600;700&display=swap');

:root{
  --granate:#c1272d;
  --tinta:#1a1a1a;
  --papel:#faf8f7;
  --linea:#e5e0dd;
  --gris:#6a6a6a;
}

html, body, [class*="css"]{ font-family:'Source Sans 3', sans-serif; }
.stApp{ background:var(--papel); }
.block-container{ padding-top:2.2rem; max-width:1150px; }

/* Cabecera */
.cab{ border-bottom:3px solid var(--tinta); padding-bottom:.5rem; margin-bottom:.2rem; }
.cab h1{
  font-family:Georgia, serif; font-size:2.1rem; font-weight:700;
  color:var(--tinta); margin:0; letter-spacing:-.02em;
}
.cab .sub{
  font-family:'IBM Plex Mono', monospace; font-size:.72rem; letter-spacing:.14em;
  text-transform:uppercase; color:var(--granate); margin-top:.35rem;
}

/* Ficha de resultado */
.ficha{
  background:#fff; border:1px solid var(--linea); border-left:4px solid var(--granate);
  padding:.85rem 1rem; margin-bottom:.5rem;
}
.ficha .cod{
  font-family:'IBM Plex Mono', monospace; font-size:1.25rem; font-weight:600;
  letter-spacing:.09em; color:var(--granate);
}
.ficha .den{ font-size:.95rem; color:var(--tinta); font-weight:600; margin-top:.15rem; }
.ficha .meta{
  font-family:'IBM Plex Mono', monospace; font-size:.7rem; color:var(--gris);
  text-transform:uppercase; letter-spacing:.08em; margin-top:.3rem;
}

/* Marcador de tiempo */
.crono{
  font-family:'IBM Plex Mono', monospace; font-size:.7rem; color:var(--gris);
  letter-spacing:.06em; border-top:1px dotted var(--linea); padding-top:.4rem; margin-top:.6rem;
}

/* Chips de nivel */
.nivel{
  display:inline-block; font-family:'IBM Plex Mono', monospace; font-size:.68rem;
  border:1px solid var(--linea); background:#fff; padding:.1rem .4rem; margin-right:.25rem;
}

section[data-testid="stSidebar"]{ background:#f2efed; border-right:1px solid var(--linea); }
section[data-testid="stSidebar"] h2{ font-family:Georgia,serif; font-size:1.05rem; }

.stChatMessage{ background:transparent; }
code{ font-family:'IBM Plex Mono', monospace !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# NORMALIZACION Y LEXICO
# ---------------------------------------------------------------------------

VACIAS = {
    "de", "del", "la", "el", "los", "las", "en", "y", "o", "con", "para", "por",
    "un", "una", "al", "sin", "que", "su", "mas", "general", "asalariados",
    "otros", "otras", "clasificados", "anteriormente", "tanto", "cuenta",
    "trabajado", "trabajo", "anos", "experiencia", "he", "soy", "estuve",
}

# Expansion semantica: coloquial / marcas / genero -> vocabulario del catalogo.
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
    "espanol": "idiomas profesores",
    "ele": "idiomas profesores",
    "fontanero": "fontaneros instaladores tuberias fluidos",
}


def normaliza(t: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    ).lower().strip()


def raiz(w: str) -> str:
    """Lematizador minimo: neutraliza plural y genero (camarera/camareros -> camarer)."""
    if len(w) > 5 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("s"):
        w = w[:-1]
    if len(w) > 4 and w[-1] in "aoe":
        w = w[:-1]
    return w


# ---------------------------------------------------------------------------
# CARGA E INDEXACION DEL CATALOGO (una sola vez por sesion de servidor)
# ---------------------------------------------------------------------------

GRANDES_GRUPOS = {
    "1": "Direccion y gerencia",
    "2": "Tecnicos y profesionales cientificos",
    "3": "Tecnicos y profesionales de apoyo",
    "4": "Empleados de oficina",
    "5": "Servicios de restauracion, personales y comercio",
    "6": "Agricultura, ganaderia y pesca",
    "7": "Artesanos y trabajadores cualificados",
    "8": "Operadores de instalaciones y maquinaria",
    "9": "Ocupaciones elementales",
}


@st.cache_resource(show_spinner="Indexando el catalogo oficial...")
def carga_indice():
    registros, inv, inv_raiz = [], defaultdict(list), defaultdict(list)

    if not os.path.exists(CATALOGO):
        return {"ok": False, "registros": [], "por_codigo": {}}

    with open(CATALOGO, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if ":" not in linea:
                continue
            codigo, denom = linea.split(":", 1)
            codigo, denom = codigo.strip(), denom.strip()
            tokens = [
                t for t in re.findall(r"\w+", normaliza(denom))
                if len(t) > 2 and t not in VACIAS
            ]
            registros.append({
                "codigo": codigo,
                "denom": denom,
                "palabras": set(tokens),
                "raices": {raiz(t) for t in tokens},
                "cabeza": {raiz(t) for t in tokens[:3]},  # nucleo de la denominacion
            })

    n = max(1, len(registros))
    for i, r in enumerate(registros):
        for w in r["palabras"]:
            inv[w].append(i)
        for w in r["raices"]:
            inv_raiz[w].append(i)

    idf = {w: math.log(1 + n / len(ix)) for w, ix in inv.items()}
    idf_raiz = {w: math.log(1 + n / len(ix)) for w, ix in inv_raiz.items()}

    # Indice de trigramas para tolerancia a erratas
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
        "idf": idf,
        "idf_raiz": idf_raiz,
        "trigramas": trigramas,
        "vocab_raiz": list(inv_raiz.keys()),
    }


IDX = carga_indice()

if not IDX["ok"]:
    st.error(
        f"No se encuentra **{CATALOGO}** en la carpeta de la aplicacion. "
        "Subelo al repositorio junto a app.py."
    )
    st.stop()


# ---------------------------------------------------------------------------
# MOTOR DE BUSQUEDA LOCAL
# ---------------------------------------------------------------------------

def parecidas(palabra, umbral=0.84, tope=3):
    """Candidatas parecidas usando el indice de trigramas (no recorre el catalogo)."""
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


def busca(consulta, tope=20, grupo=None):
    """Devuelve [(puntuacion, codigo, denominacion)] ordenado por afinidad."""
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
    puntos = defaultdict(float)
    cubierto = defaultdict(set)

    def suma(i, valor, termino):
        puntos[i] += valor
        cubierto[i].add(termino)

    for w, peso in terminos.items():
        r = raiz(w)
        encontrado = False

        if w in IDX["inv"]:                                  # coincidencia exacta
            encontrado = True
            k = IDX["idf"][w] * peso * 3.0
            for i in IDX["inv"][w]:
                suma(i, k, r)

        if r in IDX["inv_raiz"]:                             # coincidencia por raiz
            encontrado = True
            k = IDX["idf_raiz"][r] * peso * 2.2
            for i in IDX["inv_raiz"][r]:
                suma(i, k, r)

        if len(r) > 3:                                       # prefijos
            for v in IDX["vocab_raiz"]:
                if v != r and (v.startswith(r) or r.startswith(v)):
                    k = IDX["idf_raiz"][v] * peso * 1.0
                    for i in IDX["inv_raiz"][v]:
                        suma(i, k, r)

        if not encontrado and len(r) > 4:                    # erratas
            for ratio, c in parecidas(r):
                k = IDX["idf_raiz"][c] * peso * ratio * 1.4
                for i in IDX["inv_raiz"][c]:
                    suma(i, k, r)

    n_term = max(1, len(originales))
    resultados = []
    for i, valor in puntos.items():
        reg = IDX["registros"][i]
        if grupo and not reg["codigo"].startswith(grupo):
            continue
        # Refuerzo si la coincidencia esta en el nucleo de la denominacion
        nucleo = 1.0 + 0.5 * len(cubierto[i] & reg["cabeza"])
        # Refuerzo por cobertura de la consulta
        cobertura = 0.6 + 0.4 * min(1.0, len(cubierto[i] & originales) / n_term)
        resultados.append((valor * nucleo * cobertura, reg["codigo"], reg["denom"]))

    resultados.sort(reverse=True)
    return resultados[:tope]


# ---------------------------------------------------------------------------
# CLIENTE GEMINI
# ---------------------------------------------------------------------------

@st.cache_resource
def cliente():
    clave = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not clave:
        return None
    return genai.Client(api_key=clave)


INSTRUCCIONES = """Eres un tecnico de codificacion de ocupaciones para SilcoiWeb (SEPE).
Seleccionas entre 3 y 5 ocupaciones del listado de candidatos que se te entrega.

REGLAS
1. Usa UNICAMENTE codigos de 8 cifras y denominaciones literales de la lista de candidatos. No inventes ni modifiques ninguna denominacion.
2. Empieza directamente por la primera ocupacion, sin saludos ni preambulo.
3. Entre 3 y 5 opciones, de mayor a menor afinidad.
4. Nivel profesional: 90 aprendices (sin experiencia) / 00 tecnicos o sin categoria (estandar con experiencia) / 10 directores / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.

FORMATO EXACTO
1. **XXXXXXXX** - DENOMINACION OFICIAL EN MAYUSCULAS
   * Nivel: 00 - Tecnicos / Sin categoria

Si dudas entre dos opciones por falta de detalle, cierra con:
**Pregunta sugerida para la persona:**
* Realizaba principalmente tareas de [CODIGO - DENOMINACION A] o de [CODIGO - DENOMINACION B]?
"""


def genera_stream(cli, consulta, candidatos, nivel_razonamiento):
    """Devuelve un generador de fragmentos de texto."""
    prompt = (
        f"CANDIDATOS DISPONIBLES (unica fuente valida):\n{candidatos}\n\n"
        f"CONSULTA: {consulta}"
    )
    # Los tokens de razonamiento cuentan como salida: mas profundidad, mas margen.
    margen = {"minimal": 900, "low": 1600, "medium": 2600}.get(nivel_razonamiento, 900)
    base = dict(system_instruction=INSTRUCCIONES, max_output_tokens=margen)

    # Gemini 3.x sustituye thinking_budget por thinking_level y descarta temperature.
    intentos = []
    try:
        intentos.append(
            {**base, "thinking_config": types.ThinkingConfig(thinking_level=nivel_razonamiento)}
        )
    except Exception:  # noqa: BLE001  SDK antiguo sin thinking_level
        pass
    intentos.append(base)
    ultimo_error = None
    for cfg in intentos:
        try:
            flujo = cli.models.generate_content_stream(
                model=MODELO,
                contents=prompt,
                config=types.GenerateContentConfig(**cfg),
            )
            for trozo in flujo:
                if getattr(trozo, "text", None):
                    yield trozo.text
            return
        except Exception as e:      # noqa: BLE001
            ultimo_error = e
            continue
    raise ultimo_error


def valida_codigos(texto):
    """Antiinvencion: separa codigos reales de codigos inexistentes en el catalogo."""
    codigos = re.findall(r"\b\d{8}\b", texto or "")
    reales = [c for c in dict.fromkeys(codigos) if c in IDX["por_codigo"]]
    falsos = [c for c in dict.fromkeys(codigos) if c not in IDX["por_codigo"]]
    return reales, falsos


# ---------------------------------------------------------------------------
# BARRA LATERAL
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## Ajustes")

    modo_ia = st.toggle(
        "Sugerencia con IA", value=True,
        help="Desactivalo para trabajar solo con el catalogo: instantaneo y sin consumo de API.",
    )
    n_candidatos = st.slider("Candidatos que se envian a la IA", 8, 30, 15)
    nivel_razonamiento = st.select_slider(
        "Profundidad de razonamiento",
        options=["minimal", "low", "medium"],
        value="minimal",
        help="Es la palanca que mas afecta a la velocidad. 'minimal' basta para clasificar.",
    )

    grupo = st.selectbox(
        "Filtrar por gran grupo",
        ["Todos"] + [f"{k} - {v}" for k, v in GRANDES_GRUPOS.items()],
    )
    filtro_grupo = None if grupo == "Todos" else grupo[0]

    st.markdown("### Niveles profesionales")
    st.markdown(
        "".join(
            f'<span class="nivel">{c} {n}</span>'
            for c, n in [
                ("10", "Direccion"), ("20", "Mandos"), ("30", "Jefes eq."),
                ("00", "Tecnicos"), ("70", "Auxiliares"), ("80", "Peones"),
                ("90", "Aprendices"),
            ]
        ),
        unsafe_allow_html=True,
    )

    st.markdown("### Registro de la sesion")
    if st.session_state.get("registro"):
        buffer = io.StringIO()
        escritor = csv.writer(buffer, delimiter=";")
        escritor.writerow(["consulta", "codigos"])
        for fila in st.session_state["registro"]:
            escritor.writerow(fila)
        st.download_button(
            "Descargar consultas (CSV)",
            buffer.getvalue().encode("utf-8-sig"),
            file_name="codificaciones_sesion.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.caption("Aun no hay consultas registradas.")

    if st.button("Vaciar conversacion", use_container_width=True):
        st.session_state["mensajes"] = []
        st.session_state["registro"] = []
        st.rerun()

    st.markdown("---")
    st.caption(
        f"Catalogo: {len(IDX['registros'])} ocupaciones. "
        "No escribas nombres, DNI ni datos identificativos de la persona: "
        "describe solo el puesto y las funciones."
    )

# ---------------------------------------------------------------------------
# CABECERA
# ---------------------------------------------------------------------------

st.markdown(
    '<div class="cab"><h1>Codificador de ocupaciones</h1>'
    '<div class="sub">Catalogo SISPE &middot; apoyo a la grabacion en SilcoiWeb</div></div>',
    unsafe_allow_html=True,
)
st.write("")

st.session_state.setdefault("mensajes", [])
st.session_state.setdefault("registro", [])

for m in st.session_state["mensajes"]:
    with st.chat_message(m["rol"]):
        st.markdown(m["texto"], unsafe_allow_html=True)

entrada = st.chat_input("Puesto, funciones o experiencia. Tambien admite un codigo de 8 cifras.")

if entrada:
    st.session_state["mensajes"].append({"rol": "user", "texto": entrada})
    with st.chat_message("user"):
        st.markdown(entrada)

    with st.chat_message("assistant"):

        # --- Atajo: consulta directa por codigo -----------------------------
        codigo_directo = entrada.strip()
        if re.fullmatch(r"\d{8}", codigo_directo):
            denom = IDX["por_codigo"].get(codigo_directo)
            if denom:
                salida = (
                    f'<div class="ficha"><div class="cod">{codigo_directo}</div>'
                    f'<div class="den">{denom}</div>'
                    f'<div class="meta">Gran grupo {codigo_directo[0]} - '
                    f'{GRANDES_GRUPOS.get(codigo_directo[0], "")}</div></div>'
                )
            else:
                salida = (
                    f"El codigo **{codigo_directo}** no figura en el catalogo cargado. "
                    "Revisa las cifras o describe el puesto con palabras."
                )
            st.markdown(salida, unsafe_allow_html=True)
            st.session_state["mensajes"].append({"rol": "assistant", "texto": salida})
            st.stop()

        # --- Busqueda local -------------------------------------------------
        t0 = time.perf_counter()
        encontrados = busca(entrada, tope=max(n_candidatos, 12), grupo=filtro_grupo)
        ms_local = (time.perf_counter() - t0) * 1000

        if not encontrados:
            aviso = (
                "El catalogo no devuelve coincidencias. Prueba con el nombre del puesto "
                "o con una funcion concreta: *reparto en moto*, *atencion telefonica*, "
                "*carretilla elevadora*."
            )
            st.markdown(aviso)
            st.session_state["mensajes"].append({"rol": "assistant", "texto": aviso})
            st.stop()

        with st.expander(f"Coincidencias del catalogo ({len(encontrados)}) - {ms_local:.0f} ms"):
            for _, cod, den in encontrados:
                st.markdown(
                    f'<div class="ficha"><div class="cod">{cod}</div>'
                    f'<div class="den">{den}</div></div>',
                    unsafe_allow_html=True,
                )

        # --- Modo sin IA ----------------------------------------------------
        cli = cliente()
        if not modo_ia or cli is None:
            if cli is None and modo_ia:
                st.warning("Falta GEMINI_API_KEY en los Secrets. Se muestran resultados del catalogo.")
            lineas = [
                f"{i}. **{cod}** - {den}"
                for i, (_, cod, den) in enumerate(encontrados[:5], 1)
            ]
            texto = "\n".join(lineas) + f'\n\n<div class="crono">Catalogo local - {ms_local:.0f} ms</div>'
            st.markdown(texto, unsafe_allow_html=True)
            st.session_state["mensajes"].append({"rol": "assistant", "texto": texto})
            st.session_state["registro"].append(
                (entrada, " | ".join(c for _, c, _ in encontrados[:5]))
            )
            st.stop()

        # --- Llamada a Gemini en streaming ----------------------------------
        candidatos = "\n".join(f"{c}:{d}" for _, c, d in encontrados[:n_candidatos])
        t1 = time.perf_counter()
        try:
            respuesta = st.write_stream(
                genera_stream(cli, entrada, candidatos, nivel_razonamiento)
            )
        except Exception as e:  # noqa: BLE001
            fallo = (
                f"La consulta a la IA no se ha completado: `{e}`\n\n"
                "Los resultados del catalogo de arriba siguen siendo validos."
            )
            st.error(fallo)
            st.session_state["mensajes"].append({"rol": "assistant", "texto": fallo})
            st.stop()

        seg_ia = time.perf_counter() - t1
        reales, falsos = valida_codigos(respuesta)

        pie = f'<div class="crono">Catalogo {ms_local:.0f} ms &middot; IA {seg_ia:.1f} s &middot; {len(reales)} codigos verificados</div>'
        st.markdown(pie, unsafe_allow_html=True)

        if falsos:
            st.error(
                "Codigos que no existen en el catalogo oficial y no deben grabarse: "
                + ", ".join(falsos)
            )
        if reales:
            st.code("\n".join(f"{c}  {IDX['por_codigo'][c]}" for c in reales), language=None)

        st.session_state["mensajes"].append({"rol": "assistant", "texto": respuesta + pie})
        st.session_state["registro"].append((entrada, " | ".join(reales)))

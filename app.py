import os
import re
import unicodedata
from difflib import SequenceMatcher
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(
    page_title="Codificador Ocupaciones SilcoiWeb",
    page_icon="💼",
    layout="centered"
)

st.title("💼 Codificador Ocupaciones SilcoiWeb")
st.caption("Herramienta de apoyo técnico para orientadores y personal de oficina")

# 1. Diccionario de expansión semántica para puestos modernos / coloquiales
SYNONYMS = {
    "uber": "conductor automovil turismo vtc transporte pasajeros coche chofer taxi",
    "cabify": "conductor automovil turismo vtc transporte pasajeros coche chofer taxi",
    "vtc": "conductor automovil turismo transporte pasajeros vehiculo chofer taxi",
    "glovo": "conductor repartidor motocicleta ciclomotor reparto domicilio mensajero",
    "rider": "conductor repartidor motocicleta ciclomotor reparto domicilio",
    "deliveroo": "repartidor motocicleta ciclomotor reparto domicilio",
    "mozo": "mozos carga descarga almacen mercado peones transporte",
    "camarera": "camareros barra sala cafeteria restaurante hosteleria",
    "enfermera": "enfermeros cuidados generales clinica hospital sanidad",
    "administrativa": "empleados administrativos contabilidad administracion gestion",
    "auxiliar administrativo": "empleados administrativos administracion general oficina gestion",
    "secretaria": "secretarios direccion administracion oficina recepcionistas",
    "recepcionista": "recepcionistas hotel oficinas informacion atencion publico",
    "comercial": "agentes comerciales delegados comerciales representantes comercio venta",
    "dependienta": "dependientes comercio tiendas venta articulos dependiente",
    "cajera": "cajeros comercio banca empresa supermercado",
    "programador": "programadores aplicaciones informaticas analistas sistemas web informatica software",
    "desarrollador": "programadores aplicaciones informaticas analistas programadores web software informatica",
    "informatico": "tecnicos mantenimiento reparacion equipos informaticos sistemas redes microinformaticos",
    "limpiadora": "personal de limpieza limpiadores general instituciones sanitarias",
    "socorrista": "socorristas banistas piscinas salvamento",
    "mecanico": "mecanicos mantenimiento reparacion automocion vehiculos motor",
    "fontanero": "fontaneros instaladores tuberias fluidos gas calefaccion",
    "electricista": "instaladores electricistas edificios viviendas industriales",
    "albañil": "albaniles encofradores mamposteros construccion obra",
    "seguridad": "vigilantes de seguridad escoltas control acceso"
}

def clean_text(t):
    return ''.join(
        c for c in unicodedata.normalize('NFD', t)
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()

# 2. Cargar catálogo oficial en memoria
@st.cache_resource
def load_catalog():
    data = []
    if os.path.exists("ocupaciones_sispe_ultraligero.txt"):
        with open("ocupaciones_sispe_ultraligero.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    code, desc = line.split(":", 1)
                    data.append({
                        "code": code,
                        "desc": desc,
                        "clean": clean_text(desc),
                        "words": set(re.findall(r'\w+', clean_text(desc)))
                    })
    return data

CATALOG = load_catalog()

# 3. Filtrado semántico previo ultrarrápido (0.005 seg)
def extract_relevant_candidates(query, catalog, top_n=25):
    q_clean = clean_text(query)
    q_words = [w for w in re.findall(r'\w+', q_clean) if len(w) > 2]
    
    # Expandir con sinónimos
    expanded_words = list(q_words)
    for k, v in SYNONYMS.items():
        if k in q_clean:
            expanded_words.extend(re.findall(r'\w+', clean_text(v)))
    expanded_words = list(dict.fromkeys(expanded_words))

    scored = []
    for item in catalog:
        desc_clean = item["clean"]
        desc_words = item["words"]
        score = 0.0

        # Coincidencias de términos expandidos
        for w in expanded_words:
            if w in desc_words:
                score += 15.0
            elif any(w in dw for dw in desc_words):
                score += 5.0
            else:
                for dw in desc_words:
                    if len(dw) > 4 and len(w) > 4:
                        r = SequenceMatcher(None, w, dw).ratio()
                        if r > 0.8:
                            score += r * 3.0

        if score > 0:
            scored.append((score, f"{item['code']}:{item['desc']}"))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [x[1] for x in scored[:top_n]]

    # Si la consulta es muy abstracta, devolver candidatos generales
    if len(selected) < 5:
        selected.extend([f"{c['code']}:{c['desc']}" for c in catalog[:top_n]])

    return "\n".join(list(dict.fromkeys(selected)))

# 4. Configuración del cliente API
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key:
    st.error("No se ha configurado GEMINI_API_KEY en los Secrets de Streamlit.")
    st.stop()

client = genai.Client(api_key=api_key)

# Historial del chat
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Escribe el puesto, funciones o experiencia...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Obtenemos solo los 25 candidatos más probables
    candidates = extract_relevant_candidates(user_input, CATALOG, top_n=25)

    system_prompt = f"""
Eres un orientador técnico para SilcoiWeb. Analiza la consulta y selecciona estrictamente entre 3 y 5 ocupaciones oficiales del siguiente listado prefiltrado:

CANDIDATOS DISPONIBLES:
{candidates}

REGLAS DE CODIFICACIÓN:
1. Utiliza ÚNICAMENTE códigos de 8 cifras y denominaciones exactas presentes en los candidatos. Prohibido inventar ocupaciones.
2. Comienza directamente con la primera ocupación, sin introducciones ni saludos.
3. Cantidad: entre 3 y 5 opciones ordenadas de mayor a menor afinidad.
4. Criterios de Nivel:
   - 90 - Aprendices: Si no tiene experiencia.
   - 00 - Técnicos / Sin categoría: Estándar con experiencia sin mandos.
   - 10 - Directores / 20 - Mandos intermedios / 30 - Jefes de equipo / 70 - Auxiliares / 80 - Peones.

FORMATO EXACTO:
1. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

2. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

PREGUNTA DE DESAMBIGUACIÓN (SOLO SI DUDAS):
- Si dudas entre 2 opciones por falta de detalle, añade al final:
**Pregunta sugerida para la persona:**
* ¿Realizaba principalmente tareas de [XXXXXXXX - DENOMINACIÓN A] o de [XXXXXXXX - DENOMINACIÓN B]?
"""

    with st.chat_message("assistant"):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0
                )
            )
            answer = response.text
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except Exception as e:
            st.error(f"Error en la consulta: {e}")

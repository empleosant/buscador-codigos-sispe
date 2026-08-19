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

# Normalizar texto (sin tildes, minúsculas)
def clean_str(text):
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()

# Cargar el catálogo en memoria una sola vez al arrancar
@st.cache_resource
def get_catalog():
    data = []
    if os.path.exists("ocupaciones_sispe_ultraligero.txt"):
        with open("ocupaciones_sispe_ultraligero.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    code, desc = line.split(":", 1)
                    data.append((code, desc, clean_str(desc)))
    return data

CATALOG = get_catalog()

# Búsqueda instantánea en milisegundos
def find_top_matches(query, catalog, top_n=15):
    q_clean = clean_str(query)
    q_words = [w for w in re.findall(r'\w+', q_clean) if len(w) > 2]
    
    scored = []
    for code, desc, clean_desc in catalog:
        score = 0
        # Coincidencia por palabras clave
        for w in q_words:
            if w in clean_desc:
                score += 10
        # Similitud difusa
        ratio = SequenceMatcher(None, q_clean, clean_desc).ratio()
        score += ratio * 5
        
        if score > 0.5:
            scored.append((score, f"{code}:{desc}"))
            
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [item[1] for item in scored[:top_n]]
    
    # Fallback por si la búsqueda fue muy abstracta
    if not results:
        results = [f"{c}:{d}" for c, d, _ in catalog[:top_n]]
        
    return "\n".join(results)

# Cliente API
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key:
    st.error("Falta configurar GEMINI_API_KEY en Secrets.")
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

    # 1. Filtrado local en 0.01 segundos
    top_candidates = find_top_matches(user_input, CATALOG, top_n=12)

    # 2. Prompt ultra-compacto
    system_prompt = f"""
Eres un asistente de codificación ocupacional para SilcoiWeb.
Selecciona de 3 a 5 ocupaciones oficiales de 8 cifras basándote ÚNICAMENTE en estos candidatos:
{top_candidates}

REGLAS:
- Salida directa sin saludos ni texto previo.
- 90 - Aprendices (sin exp.) / 00 - Técnicos / Sin categoría (estándar con exp.) / 10 - Directores / 20 - Mandos intermedios / 30 - Jefes equipo / 70 - Auxiliares / 80 - Peones.

FORMATO:
1. **XXXXXXXX** - DENOMINACIÓN EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

2. **XXXXXXXX** - DENOMINACIÓN EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

(Si dudas entre 2 candidatos por falta de datos en la consulta, añade al final:
**Pregunta sugerida para la persona:**
* ¿Realizaba principalmente tareas de [XXXXXXXX - NOMBRE A] o de [XXXXXXXX - NOMBRE B]?)
"""

    with st.chat_message("assistant"):
        try:
            response_stream = client.models.generate_content_stream(
                model="gemini-3.6-flash",
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0
                )
            )
            
            def stream():
                for chunk in response_stream:
                    if chunk.text:
                        yield chunk.text

            full_response = st.write_stream(stream)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
        except Exception as e:
            st.error(f"Error al conectar con la API: {e}")

import os
import re
import unicodedata
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

# Función para normalizar texto (quitar tildes y mayúsculas)
def normalize(text):
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower()

# Cargar el catálogo en memoria una sola vez
@st.cache_data
def load_catalog_lines():
    if os.path.exists("ocupaciones_sispe_ultraligero.txt"):
        with open("ocupaciones_sispe_ultraligero.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

ALL_LINES = load_catalog_lines()

# Filtro rápido en memoria: extrae solo las ~40 ocupaciones más probables
def filter_relevant_lines(query, lines, max_results=50):
    tokens = [t for t in re.findall(r'\w+', normalize(query)) if len(t) > 2]
    if not tokens:
        return "\n".join(lines[:max_results])
    
    scored = []
    for line in lines:
        norm_line = normalize(line)
        score = sum(2 if token in norm_line else 0 for token in tokens)
        if score > 0:
            scored.append((score, line))
            
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [item[1] for item in scored[:max_results]]
    
    # Si la búsqueda es muy abierta, completar con una muestra general
    if len(selected) < 15:
        selected.extend(lines[:30])
        
    return "\n".join(list(dict.fromkeys(selected)))

# Obtener clave API
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not api_key:
    st.error("No se ha detectado GEMINI_API_KEY en los Secrets de Streamlit.")
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

    # Filtrar solo las ocupaciones relacionadas con la consulta
    relevant_catalog = filter_relevant_lines(user_input, ALL_LINES)

    system_prompt = f"""
Eres un asistente técnico para SilcoiWeb. Recibes un puesto laboral o funciones y seleccionas entre 3 y 5 ocupaciones oficiales de 8 cifras basándote estrictamente en el siguiente listado preseleccionado:

LISTADO DE OCUPACIONES DISPONIBLES:
{relevant_catalog}

REGLAS:
1. Solo códigos de 8 cifras y denominaciones exactas en MAYÚSCULAS extraídas del listado anterior.
2. Cero citas, sin saludos ni introducciones. Empieza directamente con el listado numerado.
3. Cantidad: entre 3 y 5 ocupaciones ordenadas de mayor a menor afinidad.
4. Nivel Profesional:
   - 90 - Aprendices: Sin experiencia.
   - 00 - Técnicos / Sin categoría: Con experiencia (estándar).
   - 10 - Directores y gerentes / 20 - Mandos intermedios / 30 - Jefes de equipo / 70 - Auxiliares / 80 - Peones.

FORMATO EXACTO:
1. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

2. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

PREGUNTA FINAL (SOLO SI DUDAS ENTRE OCUPACIONES):
- Si dudas por falta de concreción, añade al final:
**Pregunta sugerida para la persona:**
* ¿Realizaba principalmente tareas de [XXXXXXXX - DENOMINACIÓN A] o de [XXXXXXXX - DENOMINACIÓN B]?
"""

    with st.chat_message("assistant"):
        try:
            # Respuesta en tiempo real (Streaming)
            response_stream = client.models.generate_content_stream(
                model="gemini-3.6-flash",
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.1
                )
            )
            
            def stream_text():
                for chunk in response_stream:
                    yield chunk.text

            full_response = st.write_stream(stream_text)
            st.session_state.messages.append({"role": "assistant", "content": full_response})
        except Exception as e:
            st.error(f"Error al conectar con la API: {e}")

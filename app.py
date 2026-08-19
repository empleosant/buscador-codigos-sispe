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

# Cargar el catálogo en memoria
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

# Búsqueda difusa local ultra-rápida (0.01 seg)
def find_top_matches(query, catalog, top_n=15):
    q_clean = clean_str(query)
    q_words = [w for w in re.findall(r'\w+', q_clean) if len(w) > 2]
    
    scored = []
    for code, desc, clean_desc in catalog:
        score = 0
        for w in q_words:
            if w in clean_desc:
                score += 10
        ratio = SequenceMatcher(None, q_clean, clean_desc).ratio()
        score += ratio * 5
        
        if score > 0.5:
            scored.append((score, f"{code}:{desc}"))
            
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [item[1] for item in scored[:top_n]]
    
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

    top_candidates = find_top_matches(user_input, CATALOG, top_n=15)

    system_prompt = f"""
Eres un asistente para SilcoiWeb. Devuelve entre 3 y 5 ocupaciones de 8 cifras basadas ÚNICAMENTE en estos candidatos:
{top_candidates}

REGLAS:
- Salida directa sin saludos ni introducciones.
- Formato exacto por cada ocupación:
1. **XXXXXXXX** - DENOMINACIÓN EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

(Niveles: 90 - Aprendices si no tiene exp / 00 - Técnicos estándar con exp / 10 - Directores / 20 - Mandos / 30 - Jefes / 70 - Auxiliares / 80 - Peones).

- Si dudas entre 2 opciones por falta de datos, añade al final:
**Pregunta sugerida para la persona:**
* ¿Realizaba tareas de [XXXXXXXX - NOMBRE A] o de [XXXXXXXX - NOMBRE B]?
"""

    with st.chat_message("assistant"):
        try:
            # Desactivamos el tiempo de pensamiento (thinking_budget=0) para respuesta inmediata
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    thinking_config=types.ThinkingConfig(thinking_budget=0)
                )
            )
            answer = response.text
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except Exception as e:
            # Fallback en caso de incompatibilidad con thinking_budget en algún modelo
            try:
                response = client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=user_input,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.0
                    )
                )
                answer = response.text
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as ex:
                st.error(f"Error: {ex}")

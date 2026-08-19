import os
import re
import unicodedata
from difflib import SequenceMatcher
import streamlit as st

st.set_page_config(
    page_title="Codificador Ocupaciones SilcoiWeb",
    page_icon="💼",
    layout="centered"
)

st.title("💼 Codificador Ocupaciones SilcoiWeb")
st.caption("Herramienta de búsqueda instantánea para orientadores y personal de oficina")

# Normalizar texto (elimina tildes y pasa a minúsculas)
def normalize_text(text):
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower().strip()

# Cargar el catálogo en memoria RAM una sola vez al arrancar
@st.cache_resource
def load_sispe_catalog():
    catalog = []
    if os.path.exists("ocupaciones_sispe_ultraligero.txt"):
        with open("ocupaciones_sispe_ultraligero.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if ":" in line:
                    code, desc = line.split(":", 1)
                    catalog.append({
                        "code": code,
                        "desc": desc,
                        "norm": normalize_text(desc),
                        "words": set(re.findall(r'\w+', normalize_text(desc)))
                    })
    return catalog

CATALOG = load_sispe_catalog()

# Detección automática del nivel profesional según palabras clave
def detect_level(query_norm):
    if any(w in query_norm for w in ["sin experiencia", "aprendiz", "novato", "iniciacion"]):
        return "90 - Aprendices"
    if any(w in query_norm for w in ["director", "gerente", "directora", "gerencia"]):
        return "10 - Directores y gerentes"
    if any(w in query_norm for w in ["encargado", "encargada", "mando", "supervisor"]):
        return "20 - Mandos intermedios"
    if any(w in query_norm for w in ["jefe", "jefa", "coordinador", "coordinadora", "lider"]):
        return "30 - Jefes de equipo"
    if any(w in query_norm for w in ["auxiliar", "ayudante", "asistente"]):
        return "70 - Ayudantes y auxiliares"
    if any(w in query_norm for w in ["peon", "mozo", "limpiador", "limpiadora"]):
        return "80 - Peones"
    return "00 - Técnicos / Sin categoría"

# Motor de búsqueda instantáneo
def search_occupations(query, catalog, max_results=5):
    q_norm = normalize_text(query)
    q_words = [w for w in re.findall(r'\w+', q_norm) if len(w) > 2]
    
    scored = []
    for item in catalog:
        score = 0
        desc_norm = item["norm"]
        desc_words = item["words"]
        
        # Coincidencia exacta de frase
        if q_norm in desc_norm:
            score += 50
            
        # Coincidencia por palabras clave
        matched_words = sum(1 for w in q_words if w in desc_words or any(w in dw for dw in desc_words))
        score += matched_words * 15
        
        # Similitud difusa
        ratio = SequenceMatcher(None, q_norm, desc_norm).ratio()
        score += ratio * 20
        
        if score > 5:
            scored.append((score, item))
            
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:max_results]]

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

    # Procesamiento local inmediato (0.01 segundos)
    matches = search_occupations(user_input, CATALOG, max_results=5)
    sug_level = detect_level(normalize_text(user_input))

    if matches:
        output_lines = []
        for i, item in enumerate(matches, 1):
            output_lines.append(f"{i}. **{item['code']}** - {item['desc']}\n   * Nivel: {sug_level}")
            
        response_text = "\n\n".join(output_lines)
        
        # Pregunta de desambiguación si hay varias opciones competitivas
        if len(matches) >= 2 and sug_level == "00 - Técnicos / Sin categoría":
            response_text += f"\n\n**Pregunta sugerida para la persona:**\n* ¿Realizaba principalmente tareas de [{matches[0]['code']} - {matches[0]['desc']}] o de [{matches[1]['code']} - {matches[1]['desc']}]?"
    else:
        response_text = "No se encontraron ocupaciones exactas. Prueba con términos más generales o sinónimos."

    with st.chat_message("assistant"):
        st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})

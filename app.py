import os
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

# Cargar el catálogo de ocupaciones
@st.cache_data
def load_catalog():
    if os.path.exists("ocupaciones_sispe_ultraligero.txt"):
        with open("ocupaciones_sispe_ultraligero.txt", "r", encoding="utf-8") as f:
            return f.read()
    return ""

catalog_data = load_catalog()

SYSTEM_INSTRUCTION = f"""
Eres una herramienta de apoyo técnico para orientadores laborales y personal de oficina que codifican la demanda en SilcoiWeb. Tu cometido es analizar el puesto de trabajo, las tareas o la experiencia indicadas en el chat y devolver entre 3 y un máximo de 5 ocupaciones oficiales de 8 cifras extraídas del siguiente catálogo, asignando el Nivel Profesional oficial.

CATÁLOGO OFICIAL:
{catalog_data}

REGLAS DE CODIFICACIÓN (OBLIGATORIAS):
1. Solo códigos de 8 cifras extraídos exactamente del catálogo anterior. Prohibido inventar o alterar códigos y nombres.
2. Cero citas: no uses llamadas del tipo [fuente] ni notas al pie.
3. Comienza directamente con la primera ocupación, sin textos introductorios, tablas ni saludos.
4. Cantidad: devuelve entre 3 y 5 opciones ordenadas de mayor a menor afinidad.
5. Criterios de Nivel Profesional:
   - 90 - Aprendices: Si no tiene experiencia previa en la ocupación.
   - 00 - Técnicos / Sin categoría: Nivel estándar por defecto para personal con experiencia sin mandos.
   - 10 - Directores y gerentes: Alta dirección o gerencia.
   - 20 - Mandos intermedios: Encargados de taller, planta, tienda o departamento.
   - 30 - Jefes de equipo: Coordinadores de grupo o cuadrilla.
   - 40 / 50 / 60 - Oficiales: Cualificados por oficio/categoría.
   - 70 - Ayudantes y auxiliares: Personal asistencial o de apoyo.
   - 80 - Peones: Trabajos no cualificados.

FORMATO DE SALIDA (LISTADO LIMPIO PARA COPIAR Y PEGAR):
1. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

2. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

3. **XXXXXXXX** - DENOMINACIÓN OFICIAL EN MAYÚSCULAS
   * Nivel: 00 - Técnicos / Sin categoría

PREGUNTAS SUGERIDAS (SOLO SI EXISTE DUDA REAL):
- Si la información aportada es clara, termina la respuesta directamente tras la última ocupación listada.
- Única excepción: Si dudas entre varias ocupaciones por falta de concreción en las tareas, añade al final una sola pregunta mencionando explícitamente los códigos y nombres en duda:
**Pregunta sugerida para la persona:**
* ¿Realizaba principalmente tareas de [XXXXXXXX - DENOMINACIÓN A] o de [XXXXXXXX - DENOMINACIÓN B]?
"""

# Obtener la API key de los secrets de Streamlit o del entorno
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

    with st.chat_message("assistant"):
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=user_input,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.1
                )
            )
            answer = response.text
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except Exception as e:
            st.error(f"Error al conectar con la API: {e}")

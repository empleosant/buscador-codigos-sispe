"""
Codificador de ocupaciones
Interfaz de apoyo para localizar codigos oficiales del catalogo de ocupaciones antes de grabarlos.
"""

import os
import re
import csv
import time
import threading
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
N_CANDIDATOS = 24    # cuántas ocupaciones ve el modelo. Medido el 25/08/2026
                     # sobre los 40 casos: el código correcto está dentro de
                     # los 16 primeros en 37 casos, dentro de los 20 en 38 y
                     # dentro de los 30 en 39. La avería del buscador no es que
                     # no encuentre la ocupación buena, es que la ordena mal
                     # ("electricista de obra" la deja en el puesto 19), y
                     # recortar a 16 la tiraba de la lista antes de que el
                     # modelo pudiera verla. Veinticuatro líneas de catálogo son
                     # unos 500 tokens: irrelevante frente al tope de 250.000
                     # por minuto. Si al medir con la prueba masiva empeora,
                     # vuelve a 16.
UNA_LLAMADA = False  # ¿una sola llamada al modelo por consulta, o dos?
                     #
                     # Habia dos viajes SECUENCIALES: el paso 1 traducia la
                     # frase de la calle a vocabulario oficial (INTERPRETE) y
                     # con esa traduccion se rebuscaba en el catalogo para
                     # darle mejores candidatos al paso 2, que es el que elige
                     # (INSTRUCCIONES). Secuenciales de verdad: el segundo no
                     # puede empezar hasta que el primero contesta, porque los
                     # candidatos salen de su respuesta.
                     #
                     # Que el paso 1 exista se justificaba en que el buscador
                     # literal no encontraba la ocupacion buena. Pero eso hoy
                     # no es lo que dicen los numeros: evaluar.py mide la
                     # cobertura SIN IA -busca() sobre el texto tal cual se
                     # escribio- y el codigo correcto entra en la lista de 24
                     # candidatos en los 40 casos de 40. Si ya esta dentro, el
                     # paso 1 no lo mete: solo reordena, y se cobra una llamada
                     # entera por hacerlo.
                     #
                     # Ademas la red para cuando los candidatos NO sirven ya
                     # estaba puesta y probada: "otros_terminos" (regla 9), que
                     # es exactamente lo mismo que hacia el paso 1 -devolver
                     # vocabulario oficial- pero solo cuando hace falta y sin
                     # pagarlo en las consultas que van bien.
                     #
                     # Asi que la consulta corriente pasa de dos llamadas a
                     # una, y la dificil de tres a dos.
                     #
                     # Se deja en un interruptor y no cableado a proposito:
                     # esto SOLO lo decide la prueba masiva, comparando las dos
                     # tandas con la misma semilla. El modo va escrito en cada
                     # fila del CSV. Si el acierto baja, se vuelve a False.
ENCAJE_MINIMO = 0.40  # cuánto del nombre de la ocupación debe explicar la
                      # consulta para fiarse de ella sin preguntar a la IA
VENTAJA_CLARA = 3.0   # cuántas veces debe superar el 1º del buscador al 2º
                      # para que mande él en lugar del modelo (sube para que
                      # mande menos, baja para que mande más)
ESPERA_MAXIMA = 8    # segundos de silencio que aguanta el TRANSPORTE. OJO: no
                     # es el tope de un intento, aunque durante meses se creyo
                     # que si. Es lo que acaba en httpx, y el plazo de httpx
                     # mide el tiempo SIN RECIBIR DATOS, no lo que dura la
                     # respuesta. El tope de verdad de un intento es
                     # PLAZO_INTENTO, que lo impone _con_plazo con un reloj
                     # nuestro. Esto se queda como segunda linea, para el caso
                     # de una conexion que se queda muda del todo.
                     #
                     # Lo de abajo sigue siendo cierto y por eso se conserva:
                     # cuando la llamada va bien, la consulta entera se
                     # resuelve en 2-2,6 s. (El 0,5-0,6 s que ponia aqui era el
                     # primer trozo, y se midio cuando habia streaming; ya no
                     # hay nada comparable que medir: la respuesta llega
                     # entera de una vez.) Lo que se veia
                     # como "lentitud" no era eso: era una llamada suelta que se
                     # atasca, y con el corte en 30 s la persona esperaba media
                     # minuto a que el reloj venciera antes de que la cascada
                     # relevara al modelo siguiente (caso medido: 29,1 s, de los
                     # cuales 21,6 fueron el intento colgado). A 8 s hay margen
                     # de sobra sobre los 2,6 s reales y el relevo es rapido.
PLAZO_INTENTO = 4    # segundos de reloj para UN intento contra el modelo. Es
                     # el que impone _con_plazo, y desde el 01/09/2026 es el
                     # unico que corta de verdad: ESPERA_MAXIMA se lo queda
                     # httpx, que solo mide el tiempo SIN RECIBIR DATOS.
                     #
                     # Cuatro y no ocho, medido esa misma noche sobre todas las
                     # respuestas sanas de la tanda: el afinado nunca paso de
                     # 2,6 s (1,1 · 1,2 · 1,3 · 1,4 · 2,0 · 2,0 · 2,6 · 0,6) y
                     # el paso 1 no paso de 3,1 s. Con el corte en 8, tres de
                     # cada cuatro consultas relevaban y cada relevo se comia
                     # ocho segundos enteros de espera antes de pasar al
                     # siguiente modelo. Bajarlo a 4 no habria matado ni una
                     # sola de las respuestas buenas medidas y parte por la
                     # mitad lo que cuesta cada relevo.
                     #
                     # Si algun dia las respuestas sanas empiezan a pasar de
                     # 3 s, este numero se queda corto y hay que subirlo: se ve
                     # en la columna `relevos` del CSV, en cuanto empiecen a
                     # salir TimeoutError con el modelo bueno respondiendo bien
                     # justo despues.

ESPERA_TOTAL = 30    # segundos para la consulta ENTERA, relevos incluidos. Es
                     # un tope distinto del de arriba y no se puede unificar:
                     # con un solo numero, el tiempo gastado en el intento que
                     # se atasco contaba contra el intento bueno y mataba una
                     # respuesta que ya venia en camino. Con 30 s casi paso: la
                     # consulta medida termino en 29,1.

# Rotulo de la fila que mide, DENTRO de un tramo, lo que se fue en esperar al
# modelo. El tramo que la contiene ya incluye esos segundos, asi que todo el
# que suma tiempos tiene que descontarla o los cuenta dos veces.
#
# Va aqui, en una constante, y no escrito a mano en cada sitio: se compara por
# subcadena contra la etiqueta, y estaba repetido literal en cuatro puntos
# -el chip, _medida, el panel y el propio bucle que la escribe-. Cambiar el
# texto en tres de los cuatro no rompe nada de forma visible: simplemente los
# totales empiezan a salir del doble de lo que son.
SUBTRAMO = "respuesta del modelo"

# ---------------------------------------------------------------------------
# PROVEEDOR DE IA
# ---------------------------------------------------------------------------
PROVEEDOR = "gemini"   # el de fábrica. El panel de mantenimiento puede cambiarlo
                       # solo para esa sesión; al recargar vuelve a este.

PROVEEDORES = {
    "gemini": {
        # El cupo diario del plan gratuito es POR MODELO y se renueva a
        # medianoche del PACIFICO, no a medianoche de aqui: en España son
        # las 09:00 en invierno y las 10:00 en verano. Antes de esa hora,
        # un "hoy ya deberia haberse renovado" todavia es ayer para Google.
        #
        # 31/08/2026: los tres modelos anteriores (2.5-flash, 2.0-flash y
        # 1.5-flash) devolvian 404 NOT_FOUND. Google los ha cerrado para
        # claves nuevas antes de la fecha anunciada, asi que la rama entera
        # de Gemini estaba caida y todo se resolvia con Mistral sin avisar.
        #
        # Solo Flash-Lite. Los Flash completos (3.7, 3.6, 3.5) tienen 20
        # peticiones al DIA en el tramo gratuito: no llegan ni a media
        # mañana, y como cada consulta gasta dos llamadas se agotan en diez
        # busquedas. Los Lite dan 500 al dia y 15 por minuto cada uno, y el
        # cupo es por modelo, asi que encadenar los dos son ~1.000 diarias.
        "clave": "GEMINI_API_KEY",
        "modelos": [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        ],
    },
    "mistral": {
        # Los topes del plan gratuito son POR MODELO, no por cuenta.
        # Ministral 3B aguanta gran volumen y latencia baja; small/large van detrás como respaldo.
        "clave": "MISTRAL_API_KEY",
        "modelos": [
            "ministral-3b-latest",
            "ministral-8b-latest",
            "mistral-small-latest",
            "codestral-latest",
            "mistral-large-latest",
        ],
        "url": "https://api.mistral.ai/v1",
    },
    "openrouter": {
        # Tercer escalon, sin clave todavia. Mientras no haya OPENROUTER_API_KEY
        # en los Secrets, la cascada lo salta sin ruido. "openrouter/free" es su
        # enrutador automatico: elige el modelo gratuito que este disponible en
        # ese momento, que es lo que interesa aqui porque el catalogo gratuito
        # rota y fijar un modelo concreto es garantizar que un dia desaparezca.
        # Sin comprar creditos son 50 peticiones al dia: da para tapar un hueco,
        # no para sostener una jornada.
        "clave": "OPENROUTER_API_KEY",
        "modelos": ["openrouter/free"],
        "url": "https://openrouter.ai/api/v1",
    },
}

# ORDEN de la cascada. Se recorre de izquierda a derecha y se salta lo que no
# tenga clave puesta. Si al medir con la prueba masiva resulta que Mistral
# responde mejor, se cambia el orden AQUI y no hay que tocar nada mas.
ORDEN = ["gemini", "mistral", "openrouter"]

CASCADA = "cascada"   # el selector de mantenimiento usa este valor para decir
                      # "recorre el orden"; cualquier otro fija un proveedor.

CADUCIDAD_CASTIGO = 3600   # segundos que dura APARTAR UN PROVEEDOR. Solo se
                           # aparta por cupo del dia agotado (ver _quema), y un
                           # cupo diario no vuelve en cinco minutos: la hora es
                           # la espera correcta. Ver _castigo_vivo.

CADUCIDAD_DEGRADACION = 300   # segundos que dura BAJAR DE MODELO dentro de un
                              # proveedor. Mucho menos que apartarlo, y por una
                              # razon medida: el 01/09/2026 gemini-3.5-flash-lite
                              # fallo UNA vez, en 0,2 s, con un ServerError de
                              # Google, y esa hora de castigo dejo las siete
                              # consultas siguientes en gemini-3.1-flash-lite.
                              # Degradar no se decide por cupo -eso ya lo hace
                              # _quema- sino por cualquier tropiezo, y la mayoria
                              # son 5xx que pasan solos. Reintentar el bueno
                              # cuesta un intento perdido cada cinco minutos;
                              # no reintentarlo costaba una hora en el peor
                              # modelo por dos decimas de mala suerte.

# El proveedor no puede ser una constante de modulo: la cascada cambia de uno a
# otro en caliente y el panel de mantenimiento tiene que poder fijar uno para
# poder medir. Todo lo que dependa de el pasa por estas funciones.


def tiene_clave(prov):
    nombre = PROVEEDORES[prov]["clave"]
    try:
        if st.secrets.get(nombre):
            return True
    except Exception:  # noqa: BLE001
        pass                       # sin archivo de secrets, st.secrets revienta
    return bool(os.environ.get(nombre))


LIMITES_CASTIGO = {"modelo_desde": CADUCIDAD_DEGRADACION}


def _castigo_vivo(registro, prov):
    """¿Sigue en pie el castigo de ESE proveedor, o ya ha caducado?

    Apartar a un proveedor sin fecha era una condena perpetua. El cupo se
    renueva -el diario de Gemini a medianoche del Pacifico, el de por minuto
    en sesenta segundos- pero session_state no se entera de que ha pasado el
    dia: una pestaña abierta desde ayer seguia dando a Gemini por muerto y
    respondia Ministral 3B para siempre, que es justo lo que se veia.

    Con caducidad se vuelve a probar el bueno cada hora. Equivocarse cuesta
    una llamada perdida por hora; no caducar costaba quedarse en el peor
    modelo sin que nadie se enterase.
    """
    marca = st.session_state.get(registro, {}).get(prov)
    if marca is None:
        return False
    # Cada registro tiene su propio plazo: apartar un proveedor es cosa del
    # cupo del dia y dura una hora; bajar de modelo casi siempre es un 5xx de
    # paso y no merece mas que unos minutos.
    limite = LIMITES_CASTIGO.get(registro, CADUCIDAD_CASTIGO)
    if time.time() - marca > limite:
        st.session_state[registro].pop(prov, None)
        return False
    return True


def quemados():
    """Los apartados que TODAVIA lo estan, ya purgados los que han caducado."""
    return {p for p in list(st.session_state.get("agotados", {}))
            if _castigo_vivo("agotados", p)}


def degradados():
    """Proveedor -> modelo de respaldo en el que sigue clavado ahora mismo.

    La degradacion dentro de un proveedor es tan invisible como el apartado y
    engaña mas: el nombre del proveedor sigue siendo el bueno y lo unico que
    cambia es el modelo, que nadie mira.
    """
    fuera = {}
    for prov in list(st.session_state.get("modelo_ok", {})):
        i = _idx_modelo(prov)   # purga de paso lo que ya haya caducado
        if i:
            lista = PROVEEDORES[prov]["modelos"]
            fuera[prov] = lista[min(i, len(lista) - 1)]
    return fuera


def orden_proveedores():
    """Proveedores a intentar, en orden, para esta consulta.

    Un proveedor fijado a mano en mantenimiento apaga la cascada: si el relevo
    siguiera activo, una prueba comparativa podria acabar respondida por otro
    proveedor y estariamos midiendo algo distinto de lo que creemos.
    """
    elegido = st.session_state.get("proveedor", CASCADA)
    if elegido in PROVEEDORES:
        return [elegido]
    vivos = [p for p in ORDEN if tiene_clave(p)]
    apartados = quemados()
    # Si estan todos quemados se vuelve a intentar con todos: mas vale una
    # llamada perdida que dejar de afinar el resto de la sesion por un error
    # mal leido.
    return [p for p in vivos if p not in apartados] or vivos


def proveedor_actual():
    orden = orden_proveedores()
    return orden[0] if orden else PROVEEDOR


def ajustes_actual():
    return PROVEEDORES[proveedor_actual()]


def modelos_de(prov):
    """Modelos a recorrer en la cadena de relevo de ESE proveedor.

    Si en mantenimiento se ha fijado uno a mano, la cadena se queda en ese y
    solo en ese, por el mismo motivo que la cascada se apaga al fijar proveedor.
    """
    todos = PROVEEDORES[prov]["modelos"]
    fijo = st.session_state.get("modelo_fijo")
    return [fijo] if fijo in todos else todos


def _idx_modelo(prov):
    """El indice de la cadena de relevo es POR proveedor.

    Compartir un solo numero entre todos hacia que, tras degradar en Gemini,
    la cascada entrase en Mistral apuntando a un modelo que quiza ni existe en
    su lista.
    """
    if not _castigo_vivo("modelo_desde", prov):
        # La degradacion caduca igual que el apartado: si se bajo a un modelo
        # peor porque el bueno no tenia cupo, hay que volver a subir cuando el
        # cupo vuelva. Sin esto, un 429 de las siete de la mañana dejaba la
        # sesion en gemini-1.5-flash hasta que alguien recargase la pagina.
        st.session_state.setdefault("modelo_ok", {}).pop(prov, None)
        return 0
    return st.session_state.setdefault("modelo_ok", {}).get(prov, 0)


def _fija_modelo(prov, i):
    st.session_state.setdefault("modelo_ok", {})[prov] = i
    desde = st.session_state.setdefault("modelo_desde", {})
    if i:
        # setdefault, no asignacion: la hora es la de la PRIMERA degradacion.
        # Refrescarla en cada consulta seria no caducar nunca.
        desde.setdefault(prov, time.time())
    else:
        # El primero de la cadena no es una degradacion: nada que caducar.
        desde.pop(prov, None)


def _apunta_uso(prov, modelo):
    """Quien ha respondido de verdad. Sin esto el relevo es invisible: Gemini
    podria llevar una semana caido y la herramienta pareceria ir bien."""
    st.session_state["ultimo_proveedor"] = prov
    st.session_state["ultimo_modelo"] = modelo
    conteo = st.session_state.setdefault("uso_proveedor", {})
    conteo[prov] = conteo.get(prov, 0) + 1


def _quema(prov, e):
    """Aparta un proveedor del resto de la sesion, solo si el error dice
    expresamente que es el cupo del DIA. Ante la duda no se quema: confundir un
    tope por minuto con el cupo agotado nos dejaria sin el mejor proveedor
    durante horas por un tropiezo de sesenta segundos."""
    if sin_cuota(e) and not por_minuto(e):
        # setdefault por lo mismo que en _fija_modelo: la condena no se alarga
        # cada vez que se vuelve a tropezar con el mismo proveedor.
        st.session_state.setdefault("agotados", {}).setdefault(prov, time.time())


def modelo_actual():
    prov = proveedor_actual()
    m = modelos_de(prov)
    return m[min(_idx_modelo(prov), len(m) - 1)]


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

# ---------------------------------------------------------------------------
# TOKENS DE ESTILO
# ---------------------------------------------------------------------------
# El rojo corporativo estaba escrito tres veces: en el :root de la pagina, en
# el body del iframe de tarjetas y en .streamlit/config.toml. Tres copias que
# nadie sincroniza acaban separandose. Ahora vive aqui y los dos bloques de CSS
# lo interpolan. config.toml sigue aparte porque Streamlit lo lee antes de que
# este archivo exista, asi que ese sigue siendo el unico duplicado inevitable.
TOKENS_CLARO = {
    "negro": "#0A0A0A",
    "rojo": "#D1122E",
    "rojo-oscuro": "#A50E24",
    "texto": "#1A1A1A",
    "suave": "#555555",
    "tenue": "#8E8E93",
    "linea": "#E2E8F0",
    "linea-fuerte": "#CBD5E1",
    "gris": "#F1F5F9",
    "papel": "#FFFFFF",
}

TOKENS_OSCURO = {
    "negro": "#E8E8E8",
    "rojo": "#E8384F",
    "rojo-oscuro": "#F25B6E",
    "texto": "#E0E0E0",
    "suave": "#A0A0A0",
    "tenue": "#787878",
    "linea": "#2D3748",
    "linea-fuerte": "#4A5568",
    "gris": "#1A202C",
    "papel": "#1A202C",
}

# Escala de movimiento. Antes habia cinco temporizaciones sueltas repartidas
# por la hoja (.15s ease, .18s ease, .18s cubic-bezier(.2,.8,.2,1),
# .22s cubic-bezier(.2,.85,.3,1), .2s cubic-bezier(.16,1,.3,1)). Ninguna estaba
# mal; el problema era que fueran cinco. Lo que se lee como acabado es que todo
# se mueva con la misma ley.
#   --entrada  para lo que aparece o crece (frena al final)
#   --salida   para lo que responde a un gesto: hover, foco, pulsacion
#   --paso     retraso entre tarjeta y tarjeta en la entrada escalonada
MOVIMIENTO = {
    "entrada": "cubic-bezier(.16,1,.3,1)",
    "salida": "cubic-bezier(.4,0,1,1)",
    "rapido": "120ms",
    "medio": "200ms",
    "lento": "320ms",
    "paso": "45ms",
}


def _vars_css(selector, *dicts):
    cuerpo = "".join(f"--{k}:{v};" for d in dicts for k, v in d.items())
    return f"{selector}{{{cuerpo}}}"


# Las variables de la pagina: claras en :root y redefinidas en oscuro. Los
# tokens de movimiento no dependen del tema, asi que solo se emiten una vez.
ESTILO_VARIABLES = (
    _vars_css(":root", TOKENS_CLARO, MOVIMIENTO)
    + "@media (prefers-color-scheme:dark){"
    + _vars_css(":root", TOKENS_OSCURO)
    + "}"
)

# Las tipografias, en dos versiones, porque los dos documentos tienen reglas
# distintas y NO son intercambiables:
#
#   IMPORT_FUENTES  para la pagina. st.markdown pasa el texto por un parser de
#     Markdown, y este solo respeta el HTML si el bloque EMPIEZA por <pre>,
#     <script>, <style> o <textarea>. Si delante va cualquier otra cosa -- un
#     <link>, por ejemplo -- no se abre bloque HTML y la hoja entera se pinta
#     como texto en la pantalla. Ademas Streamlit sanea lo que le llega y <link>
#     no esta en la lista de etiquetas permitidas, asi que por ahi no hay salida:
#     en la pagina toca @import, con todo lo lento que es.
#     Y dentro del <style>, @import tiene que ir el PRIMERO: el navegador
#     descarta cualquier @import que venga despues de otra regla.
#
#   ENLACE_FUENTES  para el marco de tarjetas. components.html escribe un
#     documento de verdad, sin parser de Markdown y sin saneado, asi que ahi si
#     valen preconnect y link: se piden en paralelo y en cuanto el navegador ve
#     la cabecera. Es justo el documento donde importaba, porque el parpadeo de
#     la letra provisional era lo que hacia medir el alto con la fuente
#     equivocada y obligo al parche document.fonts.ready.
IMPORT_FUENTES = (
    "@import url('https://fonts.googleapis.com/css2?"
    "family=Libre+Franklin:wght@400;500;600;700&"
    "family=JetBrains+Mono:wght@600;700&display=swap');"
)

ENLACE_FUENTES = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    'family=Libre+Franklin:wght@400;500;600;700&'
    'family=JetBrains+Mono:wght@600;700&display=swap">'
)

st.set_page_config(
    page_title="Codificador de ocupaciones",
    page_icon="◉",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# ESTILO FLUIDO Y COMPACTO
# ---------------------------------------------------------------------------

st.markdown("<style>" + IMPORT_FUENTES + ESTILO_VARIABLES + """

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
/* Título */
.st-key-marca button{
  background:transparent !important; border:none !important; box-shadow:none !important;
  padding:0 !important; justify-content:flex-start !important; margin-bottom:.35rem;
}
.st-key-marca button p{
  color:#fff !important; font-size:clamp(1.2rem, 1.45vw, 1.45rem) !important;
  font-weight:700 !important; letter-spacing:-.025em; margin:0 !important;
  text-align:left !important; border-bottom:2px solid transparent; transition:border-color var(--rapido) var(--salida);
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
  transition:background var(--rapido) var(--salida);
}
.st-key-buscar button:hover{ background:var(--rojo-oscuro) !important; }
.st-key-buscar button p{ color:#fff !important; font-weight:700 !important; }

.st-key-ajustes button{
  width:clamp(36px, 3.8vh, 44px) !important; height:clamp(36px, 3.8vh, 44px) !important;
  min-height:clamp(36px, 3.8vh, 44px) !important;
  border-radius:4px !important; padding:0 !important;
  background:#1A1A1A !important; border:1px solid #333 !important; color:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:background var(--rapido) var(--salida), border-color var(--rapido) var(--salida);
}
.st-key-ajustes button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important; color:#fff !important;
}

/* Consulta activa (estilos en la sección de transiciones suaves) */

/* Pregunta interactiva centrada (arriba de las tarjetas) */
div[class*="st-key-caja_pregunta"], .st-key-pregunta{
  background:#fff !important; border:1px solid var(--linea) !important; border-top:3px solid var(--rojo) !important;
  border-radius:8px !important; padding:clamp(0.6rem, 1vh, 0.85rem) clamp(1rem, 2vw, 1.8rem) !important;
  margin:0.25rem auto 0.6rem !important; box-shadow:0 2px 8px rgba(0,0,0,0.04) !important;
  text-align:center !important; width:100% !important;
}
div[class*="st-key-caja_pregunta"] .pregunta-titulo, .st-key-pregunta .pregunta-titulo{
  font-size:.65rem !important; font-weight:700 !important; letter-spacing:.15em !important; text-transform:uppercase !important;
  color:var(--rojo) !important; margin-bottom:.18rem !important; text-align:center !important;
}
div[class*="st-key-caja_pregunta"] .pregunta-texto, .st-key-pregunta .pregunta-texto{
  font-size:clamp(0.92rem, 1vw, 1.02rem) !important; line-height:1.35 !important; font-weight:600 !important;
  color:var(--texto) !important; margin-bottom:.65rem !important; text-align:center !important;
}
div:has(> [data-testid="stHorizontalBlock"] [class*="st-key-resp_opt"]) [data-testid="stHorizontalBlock"],
div[class*="st-key-caja_pregunta"] [data-testid="stHorizontalBlock"],
.st-key-pregunta [data-testid="stHorizontalBlock"]{
  display:flex !important;
  justify-content:center !important;
  align-items:center !important;
  gap:0.75rem !important;
  flex-wrap:wrap !important;
  margin:0 auto !important;
  width:100% !important;
}
div:has(> [data-testid="stHorizontalBlock"] [class*="st-key-resp_opt"]) [data-testid="stColumn"],
div[class*="st-key-caja_pregunta"] [data-testid="stColumn"],
.st-key-pregunta [data-testid="stColumn"]{
  flex:0 1 auto !important;
  min-width:unset !important;
  width:auto !important;
  max-width:none !important;
  padding:0 !important;
  display:flex !important;
  justify-content:center !important;
}
div[class*="st-key-resp_opt"],
div[class*="st-key-caja_pregunta"] .stButton,
.st-key-pregunta .stButton{
  width:auto !important;
  display:inline-flex !important;
  justify-content:center !important;
}
div[class*="st-key-resp_opt"] button,
div[class*="st-key-caja_pregunta"] .stButton button,
.st-key-pregunta .stButton button{
  background:#fff !important; border:1.5px solid var(--negro) !important; font-weight:600 !important;
  border-radius:20px !important; padding:.42rem 1.25rem !important; min-height:36px !important;
  font-size:.85rem !important;
  transition:background var(--medio) var(--entrada), color var(--medio) var(--entrada),
             border-color var(--medio) var(--entrada), transform var(--medio) var(--entrada),
             box-shadow var(--medio) var(--entrada) !important;
  white-space:normal !important; word-break:normal !important; text-align:center !important;
  line-height:1.24 !important; max-width:420px !important;
  height:auto !important; width:auto !important;
  display:inline-flex !important; align-items:center !important; justify-content:center !important;
  box-shadow:0 1px 3px rgba(0,0,0,0.04) !important; cursor:pointer !important;
}
div[class*="st-key-resp_opt"] button:hover,
div[class*="st-key-caja_pregunta"] .stButton button:hover,
.st-key-pregunta .stButton button:hover{
  background:var(--negro) !important; color:#fff !important; border-color:var(--negro) !important;
  transform:translateY(-2px); box-shadow:0 4px 12px rgba(0,0,0,0.12) !important;
}
div[class*="st-key-resp_opt"] button:hover p, div[class*="st-key-resp_opt"] button:hover span,
div[class*="st-key-caja_pregunta"] .stButton button:hover p, .st-key-pregunta .stButton button:hover p,
div[class*="st-key-caja_pregunta"] .stButton button:hover span, .st-key-pregunta .stButton button:hover span{
  color:#fff !important;
}
div[class*="st-key-resp_opt"] button:active,
div[class*="st-key-caja_pregunta"] .stButton button:active,
.st-key-pregunta .stButton button:active{
  transform:translateY(0); box-shadow:0 1px 2px rgba(0,0,0,0.08) !important;
}

/* Barra de progreso con acento rojo */
.stProgress > div > div > div > div{
  background:linear-gradient(90deg, var(--rojo), #E63946) !important;
  border-radius:4px !important;
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
  width:clamp(42px, 4.5vh, 48px) !important; height:clamp(42px, 4.5vh, 48px) !important;
  min-height:clamp(42px, 4.5vh, 48px) !important;
  border-radius:50% !important; padding:0 !important;
  border:2px solid var(--negro) !important; background:#fff !important;
  display:flex !important; align-items:center !important; justify-content:center !important;
  transition:background var(--medio) var(--entrada), border-color var(--medio) var(--entrada),
              transform var(--medio) var(--entrada), box-shadow var(--medio) var(--entrada);
  box-shadow:0 2px 8px rgba(0,0,0,0.06);
}
.st-key-reinicio button p{
  font-size:clamp(1.25rem, 1.5vw, 1.5rem) !important; line-height:1 !important;
  margin:0 !important; color:var(--negro) !important;
}
.st-key-reinicio button:hover{
  background:var(--rojo) !important; border-color:var(--rojo) !important;
  transform:rotate(-90deg) scale(1.06);
  box-shadow:0 4px 14px rgba(209,18,46,0.22);
}
.st-key-reinicio button:hover p{ color:#fff !important; }
.pie-nueva{
  text-align:center; font-size:.74rem; font-weight:600; color:var(--suave); margin:.2rem 0 0;
}
.pie-firma{
  text-align:center; font-size:.72rem; color:var(--suave);
  margin:2.2rem 0 .6rem; padding-top:.9rem;
  border-top:1px solid rgba(0,0,0,.07);
}
.pie-firma a{ color:var(--suave); text-decoration:underline; text-underline-offset:2px; }
.pie-firma a:hover{ color:var(--rojo); }
div[data-testid="stExpander"]{ border:none; background:transparent; margin-top:.1rem; }
div[data-testid="stExpander"] summary{ font-size:.8rem; color:var(--suave); padding:.1rem 0; }

/* ====== MEJORA 1: Pantalla de bienvenida ====== */
.bienvenida{
  text-align:center; padding:clamp(1.4rem, 5vh, 3rem) 1rem clamp(1rem, 3vh, 2rem);
  animation:fadeIn var(--lento) var(--entrada);
}

/* ---------- La fila de oficios de la portada ----------
   Aqui habia un "◉" flotando. Ocupaba el sitio sin decir nada, y el sitio es
   la banda ancha y vacia que se ve nada mas abrir. Ahora son cinco bustos de
   oficio que se van relevando: la portada dice de un vistazo de que va la
   herramienta, en la misma escala de grises que todo lo demas y sin un detalle
   de mas. Es decoracion, no informacion: va con aria-hidden y lo que hay que
   leer lo sigue diciendo el texto de debajo.

   Todo es CSS, sin un solo guion. Nueve huecos, dos oficios por hueco, y cada
   capa lleva un animation-delay NEGATIVO: asi al abrir la pagina cada una ya
   esta colocada en su punto del ciclo, en vez de arrancar las dieciocho a la
   vez desde el mismo fotograma. El desfase entre huecos es lo que hace que la
   fila no parpadee entera de golpe, que es lo que la volveria un cartel
   luminoso.

   space-between y no center: asi el primero y el ultimo tocan los bordes y la
   fila ocupa el ancho entero sea cual sea la pantalla, en vez de quedarse en
   un grupito en medio de una banda vacia. */
.hero-fila{
  display:flex; width:100%; justify-content:space-between; align-items:center;
  gap:clamp(.2rem, 1vw, .9rem);
  /* Sitio para el zoom. El circulo crece casi la mitad de su tamaño y crece
     hacia los cuatro lados: sin este hueco se comia el texto de debajo, y los
     dos de los extremos se salian del contenedor y sacaban barra horizontal. */
  padding:clamp(.5rem, 1.6vh, 1.1rem) clamp(.4rem, 1.6vw, 1.6rem);
  margin-bottom:clamp(.6rem, 1.8vh, 1.1rem);
  --hero-base:clamp(44px, 8.8vw, 118px);
  /* Cuatro grises y en este orden: el circulo es el mas claro, la cara va
     por encima, la ropa por encima de la cara y la marca del oficio la mas
     oscura de todas. La primera version los tenia demasiado juntos y a
     tamaño de portada no se distinguia la cabeza del fondo. */
  --av-fondo:#E6ECF2;
  --av-piel:#CBD7E3;
  --av-cuerpo:#A6B4C4;
  --av-marca:#68788A;
}
/* Los de los extremos van mas pequeños y mas apagados. No es un adorno: es lo
   que convierte nueve circulos sueltos en una fila con centro. */
.hero-hueco{
  position:relative; flex:0 0 auto; aspect-ratio:1;
  width:calc(var(--hero-base) * var(--e, 1));
  opacity:var(--o, 1);
  transition:transform var(--medio) var(--entrada),
             opacity var(--medio) var(--entrada);
}
/* Zoom al pasar por encima, a la manera del dock: crece el de debajo del
   raton, crecen algo menos sus dos vecinos y el resto de la fila se encoge y
   se apaga para dejarles sitio. Sin ese encogimiento el efecto no se lee como
   una fila que reacciona, sino como un circulo que se ha estropeado.
   El vecino de la IZQUIERDA se alcanza con :has(): es la unica forma de mirar
   hacia atras en CSS, y evita tener que meter un guion aqui para esto.
   La fila no lleva a ningun sitio -es decoracion, va con aria-hidden-, asi que
   el cursor se queda como esta: el zoom es un guiño, no una promesa de clic. */
@media (hover:hover) and (prefers-reduced-motion:no-preference){
  .hero-fila:hover .hero-hueco{
    transform:scale(.93); opacity:calc(var(--o, 1) * .7);
  }
  .hero-fila .hero-hueco:hover + .hero-hueco,
  .hero-fila .hero-hueco:has(+ .hero-hueco:hover){
    transform:scale(1.15); opacity:var(--o, 1);
  }
  .hero-fila .hero-hueco:hover{ transform:scale(1.45); opacity:1; z-index:2; }
}
.hero-of{
  position:absolute; inset:0; width:100%; height:100%; opacity:0;
  animation:releva var(--vuelta, 8.4s) var(--entrada) infinite;
  /* Cinturon y tirantes. El recorte de verdad lo hace el clipPath de dentro
     del SVG; esto es por si algun dia Streamlit decide limpiar atributos al
     pintar el HTML. Si el clipPath sobrevive, esto no hace nada; si no,
     los hombros no se salen del circulo igualmente. */
  border-radius:50%; overflow:hidden;
}
/* Sale por abajo, aguanta media vuelta, se va por arriba. Entre el 88% y el
   90% la capa vuelve de arriba a abajo, que es el salto: se da mientras esta
   invisible, que es justo para lo que sirve ese tramo.
   Las dos capas de un hueco van desfasadas media vuelta exacta, asi que una
   entra al mismo ritmo al que la otra se va: el cruce es limpio y en ningun
   momento el circulo se queda vacio. */
@keyframes releva{
  0%, 42%  { opacity:1; transform:translateY(0) scale(1); }
  50%, 88% { opacity:0; transform:translateY(-10px) scale(.88); }
  90%      { opacity:0; transform:translateY(10px) scale(.88); }
  100%     { opacity:1; transform:translateY(0) scale(1); }
}
.av-fondo{ fill:var(--av-fondo); }
.av-cuerpo{ fill:var(--av-cuerpo); }
.av-piel{ fill:var(--av-piel); }
.av-marca{ fill:var(--av-marca); }
.av-sombra{ fill:var(--av-marca); opacity:.58; }
.av-linea{
  fill:none; stroke:var(--av-marca); stroke-width:4.5;
  stroke-linecap:round; stroke-linejoin:round;
}
/* En el movil no caben nueve sin quedar de sellos de correos. Se quedan los
   cinco de en medio, que ademas son los mas grandes y los menos apagados, y se
   vuelven a juntar en el centro: a lo ancho de un telefono, "todo el ancho"
   con cinco circulos son cinco circulos separados por nada. */
@media (max-width:700px){
  .hero-fila{
    --hero-base:clamp(52px, 15.5vw, 88px);
    justify-content:center; gap:clamp(.3rem, 1.8vw, .9rem);
  }
  .hero-hueco:nth-child(-n+2), .hero-hueco:nth-child(n+8){ display:none; }
}
/* La guarda general de movimiento reducido deja toda animacion en su ultimo
   fotograma, y el ultimo de "releva" es opacity:1: los tres oficios de cada
   hueco se verian apilados uno sobre otro. Quien pide menos movimiento se
   lleva una fila quieta de cinco, que es lo que tenia que llevarse. */
@media (prefers-reduced-motion:reduce){
  .hero-of{ opacity:0 !important; transform:none !important; }
  .hero-of:first-of-type{ opacity:1 !important; }
}
.bienvenida-texto{
  font-size:clamp(.88rem, 1vw, .96rem); color:var(--tenue);
  font-weight:500; line-height:1.5; max-width:360px; margin:0 auto;
}
.bienvenida-texto b{ color:var(--suave); font-weight:600; }

/* ====== MEJORA 2: Indicador de "pensando" con pulso ====== */
@keyframes pulso{
  0%,100%{ opacity:.4; }
  50%{ opacity:1; }
}
@keyframes puntos{
  0%{ content:''; }
  25%{ content:'.'; }
  50%{ content:'..'; }
  75%{ content:'...'; }
}
.pensando{
  text-align:center; padding:.8rem 0 .4rem; font-size:.82rem;
  font-weight:600; color:var(--suave); letter-spacing:.02em;
}
.pensando-icono{
  display:inline-block; animation:pulso 1.4s ease-in-out infinite;
  margin-right:.3rem; font-size:.9rem;
}
.pensando::after{
  content:'...'; animation:puntos 1.2s steps(4, end) infinite;
  display:inline;
}

/* ====== MEJORA 3: Chip de proveedor ====== */
.chip-proveedor{
  display:inline-flex; align-items:center; gap:4px;
  font-size:.62rem; font-weight:600; color:var(--tenue);
  background:var(--gris); border:1px solid var(--linea);
  border-radius:12px; padding:.15rem .55rem; margin:.2rem auto;
  letter-spacing:.03em;
}
.chip-proveedor-punto{
  width:5px; height:5px; border-radius:50%; background:#16A34A;
  display:inline-block;
}

/* ====== MEJORA 4: Transiciones suaves ====== */
@keyframes fadeIn{
  from{ opacity:0; transform:translateY(6px); }
  to{ opacity:1; transform:translateY(0); }
}
.consulta-box{
  border-bottom:2px solid var(--negro); padding-bottom:.2rem;
  margin:0 0 clamp(0.25rem, 0.5vh, 0.4rem);
  animation:fadeIn var(--medio) var(--entrada);
}
.consulta-texto{
  font-size:clamp(0.95rem, 1.05vw, 1.08rem); font-weight:700;
  letter-spacing:-.015em; color:var(--texto);
}
.seccion{
  font-size:.65rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase;
  color:var(--suave); margin:1rem 0 .5rem;
}

/* ====== MEJORA 6: Modo oscuro ====== */
@media (prefers-color-scheme:dark){
  /* Los tokens de esta rama se emiten desde TOKENS_OSCURO. Aqui solo quedan
     los componentes, que no son variables. */
  .stApp{ background:#111318 !important; }
  .block-container{ background:#111318 !important; }
  .st-key-cabecera{ background:#0D0D0D; }
  .st-key-cabecera div[data-testid="stTextInput"] div[data-baseweb="input"]{
    background:#1A1A1A !important; border-color:#333 !important;
  }
  div[data-testid="stTextInput"] input{ color:#E0E0E0 !important; }
  div[class*="st-key-caja_pregunta"], .st-key-pregunta{
    background:#1A202C !important; border-color:#2D3748 !important;
  }
  div[class*="st-key-resp_opt"] button,
  div[class*="st-key-caja_pregunta"] .stButton button,
  .st-key-pregunta .stButton button{
    background:#1A202C !important; color:#E0E0E0 !important; border-color:#4A5568 !important;
  }
  div[class*="st-key-resp_opt"] button:hover,
  div[class*="st-key-caja_pregunta"] .stButton button:hover,
  .st-key-pregunta .stButton button:hover{
    background:#E0E0E0 !important; color:#111 !important;
  }
  .st-key-reinicio button{
    background:#1A202C !important; border-color:#4A5568 !important;
  }
  .st-key-reinicio button:hover{
    background:var(--rojo) !important; border-color:var(--rojo) !important;
  }
  .pie-firma{ border-top-color:rgba(255,255,255,.08); }
  .consulta-box{ border-bottom-color:#E0E0E0; }
  .hero-fila{
    --av-fondo:#1F2733; --av-cuerpo:#333E4D;
    --av-piel:#46525F; --av-marca:#7D8A99;
  }
}

/* ---------- Pantallas estrechas (móvil) ----------
   La herramienta se usa también desde el teléfono. Aquí no se rediseña nada:
   se le quita apretura al espacio, se aprieta la cabecera en un solo renglón
   y se impide que un código de ocho cifras o una denominación larga
   desborden a lo ancho, que es lo que rompe la lectura en vertical. */
@media (max-width:760px){
  .block-container{ padding:0 .6rem .4rem !important; }

  .st-key-cabecera{ padding:.7rem .8rem; }

  /* Todo en un renglon: campo, lupa y ajustes. Al dejarlas apilarse, la
     cabecera se comia media pantalla antes de ensenar un solo resultado.
     Los !important no son adorno: Streamlit fija el ancho de cada stColumn
     con su propia regla flex y sin ellos gana ella. Un primer intento sin
     ponerlos dejo el boton en vertical y encima hizo crecer la cabecera. */
  .st-key-cabecera [data-testid="stHorizontalBlock"]{
    flex-wrap:nowrap !important; gap:.4rem;
  }
  .st-key-cabecera [data-testid="stColumn"]{
    min-width:0;
  }
  .st-key-cabecera [data-testid="stColumn"]:nth-child(1){
    flex:1 1 auto !important; width:auto !important;
  }
  .st-key-cabecera [data-testid="stColumn"]:nth-child(2){
    flex:0 0 3.4rem !important; width:3.4rem !important;
  }
  .st-key-cabecera [data-testid="stColumn"]:nth-child(3){
    flex:0 0 auto !important; width:auto !important;
  }

  /* El boton se queda en una lupa: la palabra "Buscar" no cabe en ese renglon
     sin partirse en dos lineas. Se tapa el texto con font-size:0 y se pinta el
     simbolo en ::after, con su tamano propio.

     La etiqueta de Python sigue diciendo "Buscar" A PROPOSITO: es lo que lee
     un lector de pantalla, y una lupa no se lee. Esto es cosa del CSS y solo
     de pantallas estrechas; en escritorio el boton sigue con su palabra. */
  .st-key-buscar button p{
    font-size:0 !important; line-height:0 !important;
  }
  .st-key-buscar button p::after{
    content:'🔍'; font-size:1.05rem; line-height:1;
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

/* ---------- Esqueletos de espera ----------
   Mientras el modelo afina, la rejilla no existía y de golpe ocupaba 300 px:
   todo lo que hay debajo daba un salto. Estas piezas reservan el hueco con la
   misma retícula y el mismo alto que las tarjetas de verdad.

   No enseñan ningún resultado, y eso es deliberado: los provisionales se
   quitaron de la espera porque el primero del catálogo se equivoca a menudo
   con el lenguaje de la calle y verlo dos segundos destruye la confianza en el
   que sale después. Un esqueleto no afirma nada, solo guarda el sitio. */
.rejilla-hueso{
  display:grid; grid-template-columns:repeat(2,1fr); gap:6px; margin-top:.15rem;
}
@media (max-width:760px){ .rejilla-hueso{ grid-template-columns:1fr; } }
.hueso{
  background:var(--papel); border:1px solid var(--linea);
  border-left:4px solid var(--linea); border-radius:6px;
  padding:8px 13px; display:flex; flex-direction:column; gap:7px;
  min-height:86px;
}
.hueso span{
  display:block; height:10px; border-radius:3px;
  background:linear-gradient(90deg, var(--gris) 0%, var(--linea) 50%, var(--gris) 100%);
  background-size:200% 100%;
  animation:brillo 1.4s linear infinite;
}
.hueso span:nth-child(1){ width:38%; height:15px; }
.hueso span:nth-child(2){ width:88%; }
.hueso span:nth-child(3){ width:62%; }
@keyframes brillo{
  0%{ background-position:200% 0; }
  100%{ background-position:-200% 0; }
}

/* ---------- Barra de espera ----------
   La anterior avanzaba contra ESPERA_MAXIMA (entonces 30 s), pero la cascada contesta
   entre 2 y 4,6 s: recorría del 10 % al 17 % y saltaba al final, que se lee
   como atascada. Esta no promete un porcentaje que nadie sabe. */
.barra-espera{
  height:5px; border-radius:3px; background:var(--gris);
  overflow:hidden; margin:.1rem 0 .55rem;
}
.barra-espera i{
  display:block; height:100%; width:38%; border-radius:3px;
  background:linear-gradient(90deg, var(--rojo), #E63946);
  animation:vaiven 1.15s var(--entrada) infinite;
}
@keyframes vaiven{
  0%{ transform:translateX(-100%); }
  100%{ transform:translateX(265%); }
}

/* ---------- Botones de ejemplo de la bienvenida ----------
   Eran los únicos botones sin estilo propio: salían con el aspecto de fábrica
   de Streamlit, y son lo primero que ve quien abre la herramienta. Mismo
   lenguaje que las tarjetas: filo izquierdo que se tiñe al pasar por encima. */
div[class*="st-key-ej_"] button{
  background:var(--papel) !important;
  border:1px solid var(--linea) !important;
  border-left:3px solid var(--linea-fuerte) !important;
  border-radius:6px !important;
  padding:.55rem .85rem !important;
  text-align:left !important;
  justify-content:flex-start !important;
  box-shadow:0 1px 3px rgba(0,0,0,.03) !important;
  transition:border-color var(--medio) var(--entrada),
             transform var(--medio) var(--entrada),
             box-shadow var(--medio) var(--entrada) !important;
}
div[class*="st-key-ej_"] button p{
  font-size:.83rem !important; font-weight:400 !important;
  color:var(--suave) !important; line-height:1.32 !important;
  text-align:left !important; margin:0 !important;
}
div[class*="st-key-ej_"] button p strong{
  color:var(--texto) !important; font-weight:600 !important;
}
div[class*="st-key-ej_"] button:hover{
  border-left-color:var(--rojo) !important;
  transform:translateY(-1px);
  box-shadow:0 4px 12px rgba(0,0,0,.07) !important;
}

/* ---------- Widgets propios de Streamlit en oscuro ----------
   config.toml fija un tema claro (backgroundColor #FFFFFF) y el modo oscuro de
   esta hoja va por prefers-color-scheme. En un equipo con el sistema en oscuro
   el fondo y las tarjetas se oscurecían y estos no: quedaba un sándwich. Y el
   peor sitio para que pase es el aviso de corte de la IA, que es justo cuando
   más falta hace que la herramienta parezca fiable. */
@media (prefers-color-scheme:dark){
  div[data-testid="stExpander"] summary{ color:var(--suave) !important; }
  div[data-testid="stExpander"] summary:hover{ color:var(--rojo) !important; }
  div[data-testid="stExpander"] details{
    background:transparent !important; border-color:var(--linea) !important;
  }
  div[data-testid="stAlert"], div[data-testid="stNotification"]{
    background:var(--gris) !important;
    border:1px solid var(--linea) !important;
    color:var(--texto) !important;
  }
  div[data-testid="stAlert"] p, div[data-testid="stNotification"] p{
    color:var(--texto) !important;
  }
  .stProgress > div > div > div{ background:var(--gris) !important; }
  .hueso{ background:#151920; }
  div[class*="st-key-ej_"] button{
    background:var(--gris) !important; border-color:var(--linea) !important;
  }
}

/* ---------- Movimiento reducido ----------
   Había seis @keyframes y tres eran infinitos: el icono que flota en la
   bienvenida, el pulso del estado y los puntos suspensivos. Cero guardas.
   Quien lo pide en su sistema se lleva la herramienta entera sin una sola
   animación; nada de lo de arriba depende del movimiento para entenderse. */
@media (prefers-reduced-motion:reduce){
  *, *::before, *::after{
    animation-duration:.001ms !important;
    animation-iteration-count:1 !important;
    transition-duration:.001ms !important;
    scroll-behavior:auto !important;
  }
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# CATALOGO
# ---------------------------------------------------------------------------

def normaliza(t):
    """Deja el texto en la forma con la que se compara todo: sin acentos, en
    minusculas, los separadores convertidos en espacio y UN solo espacio entre
    palabras.

    Lo ultimo hace falta porque no todo se compara palabra a palabra. Hay dos
    sitios que miran la cadena entera: las claves de varias palabras del
    diccionario, que se buscan con `clave in q`, y el bonus por coincidencia
    exacta, que compara la consulta con la denominacion.

    Sin colapsar los espacios, escribir dos seguidos cambiaba la respuesta. Con
    «teleoperadora de atencion al cliente» la clave «atencion al cliente» casaba
    y salia primero EMPLEADOS DEL AREA DE ATENCION AL CLIENTE; con espacios de
    mas dejaba de casar y salia TELEOPERADORES. La misma consulta y dos
    respuestas distintas, segun como de rapido escriba quien pregunta.

    Los guiones, barras y guiones bajos ya se colapsaban porque el patron de
    arriba lleva un +. A los espacios les faltaba lo mismo.
    """
    t = re.sub(r"[/\\_\-]+", " ", t)
    t = "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", t).lower().strip()


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
    q_norm = normaliza(q)
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

        den_norm = normaliza(reg["denom"])
        den_corta = normaliza(reg["denom"].split(",")[0].split("(")[0].strip())
        bonus_exacto = 1.0
        if q_norm == den_norm or q_norm == den_corta:
            bonus_exacto = 1.40
        elif den_corta and (den_corta in q_norm or q_norm in den_corta):
            bonus_exacto = 1.15

        if ", EN GENERAL" in reg["denom"]:
            bonus_exacto *= 1.12

        palabras_den = max(1, len(reg["denom"].split()))
        densidad = 1.0 / (0.90 + 0.10 * (palabras_den / 3.0))

        resultados.append(
            (valor * nucleo * cobertura * familia * bonus_exacto * densidad, reg["codigo"], reg["denom"])
        )
    resultados.sort(reverse=True)
    return resultados[:tope]


# ---------------------------------------------------------------------------
# MODELO
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _cliente(proveedor):
    """El proveedor es argumento para que forme parte de la clave de cache.

    Sin el, Streamlit guardaba UN solo cliente: cambiabas de proveedor en el
    panel y te seguia respondiendo el anterior, con lo que la comparacion
    medía dos veces lo mismo sin avisar.
    """
    ajustes = PROVEEDORES[proveedor]
    nombre = ajustes["clave"]
    clave = st.secrets.get(nombre) or os.environ.get(nombre)
    if not clave:
        return None
    if proveedor == "gemini":
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
    return OpenAI(api_key=clave, base_url=ajustes["url"], timeout=ESPERA_MAXIMA)


def cliente():
    return _cliente(proveedor_actual())


def _con_plazo(hacer, segundos):
    """Ejecuta `hacer()` y se rinde a los `segundos`, conteste o no.

    ESPERA_MAXIMA nunca ha acotado lo que creiamos. El SDK lo convierte bien de
    milisegundos a segundos y se lo pasa a httpx, pero el plazo de httpx es el
    tiempo maximo SIN RECIBIR DATOS, no lo que dura la respuesta: httpx no
    tiene ningun parametro de tiempo total. Un modelo que tarda veinte segundos
    en contestar, mientras mantenga viva la conexion, no lo dispara jamas.

    Medido el 01/09/2026, con el plazo puesto en 8: dos llamadas que acabaron
    BIEN tardaron 19,3 s y 22,6 s, y en toda la tanda no salto ni un solo error
    de tipo timeout. Todos los relevos decian ServerError, que es Google
    respondiendo. El corte no habia funcionado ni una vez, y por eso quitar el
    streaming no arreglo la cola: era este el fallo de fondo, no aquel.

    Un hilo es la unica forma de rendirse: una llamada HTTP bloqueada no se
    puede cancelar desde fuera. El hilo abandonado termina por su cuenta y su
    respuesta se tira; va como demonio para que no retenga al servidor.

    Dentro del hilo NO se toca nada de Streamlit. session_state no es para
    hilos secundarios: aqui solo se hace la llamada, y apuntar el uso, fijar el
    modelo y anotar el relevo se quedan fuera, donde estaban.
    """
    caja = {}

    def envuelve():
        try:
            caja["valor"] = hacer()
        except BaseException as e:  # noqa: BLE001
            # Se guarda y se relanza fuera, para que llegue al mismo `except`
            # de siempre con su tipo intacto: de eso dependen por_minuto() y
            # sin_cuota() para decidir si se espera, se releva o se aparta.
            caja["error"] = e

    hilo = threading.Thread(target=envuelve, daemon=True)
    hilo.start()
    hilo.join(segundos)
    if hilo.is_alive():
        # Ni "quota" ni "429" en el texto: sin_cuota() mira la cadena y esto no
        # es un problema de cupo, es una llamada que no vuelve.
        raise TimeoutError(f"Sin respuesta del modelo en {segundos} s.")
    if "error" in caja:
        raise caja["error"]
    return caja.get("valor")


INSTRUCCIONES = """Eres un técnico de codificación de ocupaciones.

Recibes la descripción de un puesto y una lista cerrada de ocupaciones candidatas.
Selecciona entre 3 y 5, de mayor a menor afinidad.

REGLAS
1. Usa únicamente códigos y denominaciones literales de la lista de candidatos. No inventes ni modifiques ninguno.
2. Los candidatos llegan ordenados por coincidencia de palabras, NO por acierto. Ese orden es solo una pista: elige siempre la ocupación cuya denominación describa la actividad real, aunque esté al final de la lista.
3. Devuelve SIEMPRE entre 3 y 5 ocupaciones, aunque dudes.
4. Nivel profesional: 90 aprendices (sin experiencia) / 00 técnicos o sin categoría (estándar con experiencia) / 10 dirección / 20 mandos intermedios / 30 jefes de equipo / 70 auxiliares / 80 peones.
5. El campo "motivo" explica en menos de 10 palabras por qué encaja, en español con acentuación correcta.
6. No propongas ocupaciones de dirección, jefatura ni mando (niveles 10, 20, 30 como Gobernantes, Mayordomos, Encargados, Gerentes) salvo que la descripción diga expresamente que dirigía personal o departamentos.
7. Respeta la naturaleza y función real del puesto:
   - Gestión de facturas, facturación, contabilidad, nóminas o presupuestos corresponden a EMPLEADOS ADMINISTRATIVOS (contabilidad 41111011, personal/nóminas 41121012, administrativo general 43091029). NUNCA selecciones COBRADORES DE FACTURAS, DEUDAS O ALQUILERES (44451017) salvo que se indique expresamente cobro a morosos o recaudación de deudas en calle.
   - Limpieza de habitaciones de hotel corresponde a CAMAREROS DE PISO (HOSTELERÍA) (92101027), no a mayordomos ni gobernantes.
   - Reparto o transporte de bombonas de butano, gas o paquetería corresponde a CONDUCTORES DE CAMIÓN / FURGONETA / MOTOCARRO / TRANSPORTISTAS (grupo 8: 84321042 / 84121035 / 84401012 / 84311012), NUNCA a operadores de sistemas de distribución de agua o tuberías de gas (grupo 3).
   - Tareas operativas de conducción o manejo de maquinaria (carretilla, autobús, camión) corresponden a CONDUCTORES-OPERADORES (grupo 8), no a técnicos de gestión.
   - Domicilio particular frente a institución, centro o residencia.
8. PREGUNTA Y OPCIONES (DESAMBIGUACIÓN):
   - Rellena "pregunta" y "opciones" SOLO cuando sea estrictamente necesario para diferenciar entre especialidades críticas. Si el oficio está claro desde el inicio y no hay duda, es OBLIGATORIO dejar "pregunta": "" y "opciones": []. No despliegues la batería de preguntas innecesariamente.
   - TONO DE LA PREGUNTA: Escríbela como si fueras un orientador laboral hablándole de tú a la persona que tienes delante, en lenguaje cercano y natural. Nada de fórmulas técnicas ni burocráticas. Ejemplos de buen tono:
     · "¿Cortabas pelo a hombres, a mujeres o a todo tipo de clientes?"
     · "¿Cuidabas a la persona en su casa o en una residencia?"
   - La pregunta debe ser breve, directa y fácil de entender.
   - El campo "opciones" DEBE contener un máximo de 3 alternativas y un mínimo de 1 alternativa breve (máximo 6 palabras por opción), escritas en tono natural: ["Pelo de hombre", "Pelo de mujer", "Unisex"]. Evita textos largos. NUNCA dejes "opciones" vacío si hay "pregunta".
9. Si ninguna de las candidatas describe con precisión la actividad, rellena "otros_terminos" con entre 6 y 10 palabras sueltas de la CNO.

EJEMPLO DE RESPUESTA:
{"ocupaciones":[{"codigo":"58111037","denominacion":"PELUQUEROS UNISEX","nivel":"00","motivo":"Corte y peinado general."},{"codigo":"58111028","denominacion":"PELUQUEROS DE SEÑORAS","nivel":"00","motivo":"Peluquería y estética femenina."},{"codigo":"58111019","denominacion":"PELUQUEROS DE CABALLEROS","nivel":"00","motivo":"Barbería y corte masculino."}],"pregunta":"¿Cortabas pelo a hombres, a mujeres o a todo tipo de clientes?","opciones":["Pelo de hombre y barbería","Pelo de mujer","De todo, unisex"],"otros_terminos":""}
"""


# Anexo que se pega a INSTRUCCIONES SOLO en el modo de una llamada.
#
# Sin paso 1, "otros_terminos" deja de ser una valvula rara y pasa a ser la
# unica via por la que puede entrar en la lista una ocupacion que el buscador
# no encontro con las palabras de la calle. Ahi la regla 9 a secas se queda
# corta, y ademas choca con la 3 ("devuelve SIEMPRE entre 3 y 5"): el modelo
# entiende que tiene que elegir de lo que hay y se calla los terminos.
#
# Va aparte y no dentro de INSTRUCCIONES para que el modo de dos llamadas sea
# EXACTAMENTE el de siempre, hasta la ultima coma del prompt. Si no, la tanda
# de comparacion mediria dos cambios a la vez -una llamada menos y un prompt
# distinto- y un empeoramiento no se sabria de cual de los dos viene.
REFUERZO_UNA_LLAMADA = """

AMPLIACIÓN DE LA REGLA 9 (importante en esta configuración)
Aquí no hay un paso previo que traduzca la frase a vocabulario oficial: "otros_terminos" es lo único que puede rescatar una consulta escrita en lenguaje de la calle. Con esas palabras se vuelve a buscar en el catálogo y se te consulta otra vez con candidatos mejores.
- Rellénalo SIEMPRE que ninguna candidata describa con precisión la actividad real, con entre 6 y 10 palabras del vocabulario OFICIAL de la CNO: los términos con los que el catálogo nombraría ese oficio, no las palabras que ha usado la persona.
- Rellenarlo NO te exime de la regla 3: devuelve igualmente las 3-5 candidatas menos malas, y además los términos.
- Si la descripción mezcla dos funciones distintas ("cobro en caja y repongo", "conduzco y reparto"), incluye términos oficiales de LAS DOS.
- Déjalo vacío solo cuando alguna candidata describa el puesto de verdad. Ante la duda, rellénalo: no cuesta nada y es lo único que puede traer a la lista una ocupación que no esté ya en ella.
"""


def instrucciones():
    """El prompt del modelo que elige, segun el modo de llamada."""
    return INSTRUCCIONES + (REFUERZO_UNA_LLAMADA if una_llamada() else "")


def _configuraciones():
    return [dict(
        system_instruction=instrucciones(),
        max_output_tokens=700,
        response_mime_type="application/json",
        # Sin esto el modelo razona antes de responder y son segundos por
        # consulta. Elegir entre 24 candidatos ya dados no lo necesita.
        thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
    )]


PAUSAS_429 = (4, 10, 20)   # segundos de espera ante un tope por minuto


def _flujo_gemini(cli, prompt, prov="gemini"):
    opciones = _configuraciones()
    modelos = modelos_de(prov)
    ultimo = None
    espera = 0
    for m in range(_idx_modelo(prov), len(modelos)):
        for i in range(st.session_state.get("cfg", 0), len(opciones)):
            emitido = False
            t_intento = time.perf_counter()
            try:
                # Llamada NORMAL, no en streaming. Medido el 01/09/2026: con
                # `generate_content_stream` el plazo de `HttpOptions` no se
                # aplicaba y una llamada atascada tardaba 30,7 s en dar el
                # primer trozo con el corte puesto en 8. Ademas, si el stream
                # arrancaba y se caia a mitad (503 a los 21,5 s), `emitido` ya
                # estaba puesto y el `raise` de abajo mataba la consulta sin
                # dejar relevar al modelo siguiente.
                # Aqui el streaming no aportaba nada: quien consume esto
                # acumula los trozos en una cadena y no pinta ni uno hasta que
                # termina. Se pierde una entrega progresiva que nadie veia y se
                # gana un corte que si funciona y un fallo que si releva.
                # El reloj lo ponemos nosotros: el de HttpOptions no acota
                # el total. Ver _con_plazo. Solo va dentro la llamada; todo lo
                # que toca session_state se queda aqui fuera.
                cfg_afinado = types.GenerateContentConfig(**opciones[i])
                r = _con_plazo(
                    lambda: cli.models.generate_content(
                        model=modelos[m], contents=prompt, config=cfg_afinado,
                    ),
                    PLAZO_INTENTO,
                )
                _fija_modelo(prov, m)
                st.session_state["cfg"] = i
                _apunta_uso(prov, modelos[m])
                uso = getattr(r, "usage_metadata", None)
                if uso is not None:
                    st.session_state["pensamiento"] = {
                        "entrada": getattr(uso, "prompt_token_count", None),
                        "pensados": getattr(uso, "thoughts_token_count", None),
                        "salida": getattr(uso, "candidates_token_count", None),
                    }
                emitido = True
                yield (getattr(r, "text", "") or "")
                return
            except Exception as e:  # noqa: BLE001
                if emitido:
                    raise
                # Lo que cuesta un intento fallido no se veia en ningun sitio:
                # el cronometro solo cuenta el tramo entero, asi que 29 s de
                # espera parecian "el modelo va lento" cuando eran 21 s de
                # intento colgado mas 2 s del modelo de relevo. Aqui queda
                # anotado modelo por modelo.
                st.session_state.setdefault("relevos", []).append(
                    f"{modelos[m]}:{time.perf_counter() - t_intento:.1f}s:"
                    f"{type(e).__name__}"
                )
                ultimo = e
                if por_minuto(e):
                    if espera < len(PAUSAS_429):
                        # Tope por minuto: se espera y se vuelve a intentar con
                        # el MISMO modelo. No hay que cambiar de modelo por esto.
                        time.sleep(PAUSAS_429[espera])
                        espera += 1
                        continue
                    # Agotados los reintentos, el tope por minuto NO degrada el
                    # modelo: es un atasco de sesenta segundos, no una cuota
                    # agotada. Se levanta y la cascada pasa la consulta al
                    # siguiente proveedor; la proxima volvera a intentar aqui
                    # con el mismo modelo. Antes esto degradaba en permanente y
                    # un minuto malo dejaba a toda la oficina en un modelo peor
                    # el resto del dia (visto en la tanda del 26/08/2026: acabo
                    # respondiendo gemini-3.6-flash, el de 20 al dia).
                    raise
                if sin_cuota(e):
                    # Cuota del dia agotada o modelo retirado: aqui SI se pasa
                    # al siguiente modelo de la cadena.
                    break
    raise ultimo


def _flujo_openai(cli, prompt, prov):
    """Mismo trato que el camino de Gemini: relevo de modelo y espera ante 429.

    Sin esto, un tope por minuto de Mistral se contaba como fallo del modelo y
    la consulta caia al catalogo sin afinar, que es justo lo que la prueba
    comparativa no debe confundir con "responde peor".
    """
    modelos = modelos_de(prov)
    ultimo = None
    m = _idx_modelo(prov)
    espera = 0
    while m < len(modelos):
        emitido = False
        t_intento = time.perf_counter()
        try:
            # Sin streaming y con el reloj puesto, igual que el camino de
            # Gemini desde el 01/09/2026 y por los mismos dos motivos.
            #
            # Uno: aqui el streaming tampoco aportaba nada. Quien consume esto
            # acumula los trozos en una cadena y no pinta ni uno hasta que
            # termina, asi que se entregaba a plazos algo que nadie veia llegar.
            #
            # Dos, y es el grave: en cuanto llegaba el primer trozo se ponia
            # `emitido`, y a partir de ahi un corte a mitad de respuesta subia
            # por el `raise` de abajo y mataba la consulta SIN dejar relevar al
            # modelo siguiente. Un fallo tardio valia por un fallo definitivo.
            #
            # El plazo del cliente de Mistral se queda en ESPERA_MAXIMA: es de
            # httpx y solo mide silencio. El tope real lo pone _con_plazo.
            r = _con_plazo(
                lambda: cli.chat.completions.create(
                    model=modelos[m],
                    messages=[
                        {"role": "system", "content": instrucciones()},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=2048,
                    temperature=0,
                    response_format={"type": "json_object"},
                ),
                PLAZO_INTENTO,
            )
            _fija_modelo(prov, m)
            _apunta_uso(prov, modelos[m])
            emitido = True
            yield (r.choices[0].message.content or "")
            return
        except Exception as e:  # noqa: BLE001
            if emitido:
                raise
            # Esta rama no anotaba nada: un fallo de Mistral era invisible en
            # el CSV y sus intentos perdidos se sumaban al tramo sin explicar
            # de donde salian. Mismo formato que Gemini.
            st.session_state.setdefault("relevos", []).append(
                f"{modelos[m]}:{time.perf_counter() - t_intento:.1f}s:"
                f"{type(e).__name__}"
            )
            ultimo = e
            if por_minuto(e):
                if espera < len(PAUSAS_429):
                    # Tope por minuto: se espera y se reintenta con el MISMO modelo.
                    time.sleep(PAUSAS_429[espera])
                    espera += 1
                    continue
                # Misma regla que en Gemini: un atasco por minuto no degrada
                # el modelo. Se levanta y decide la cascada.
                raise
            m += 1
    if ultimo:
        raise ultimo


INTERPRETE = """Eres experto en el catálogo oficial de ocupaciones (CNO).

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


def _interpreta_una(prov, texto):
    """Un intento contra UN proveedor. Devuelve el texto crudo o levanta."""
    c = _cliente(prov)
    if c is None:
        raise RuntimeError(f"Sin cliente para {prov}.")
    modelos = modelos_de(prov)
    modelo = modelos[min(_idx_modelo(prov), len(modelos) - 1)]
    if prov == "gemini":
        cfg = dict(
            system_instruction=INTERPRETE,
            max_output_tokens=512,
            response_mime_type="application/json",
            # Sin esto el modelo razona antes de contestar y son segundos por
            # consulta. Traducir la frase a vocabulario oficial no lo necesita.
            # Ademas los tokens de razonamiento cuentan contra el tope: con 256
            # podia gastarselos pensando y devolver vacio, y entonces la
            # busqueda se hacia con el reparto en bruto de palabras.
            thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
        )
        cfg_interprete = types.GenerateContentConfig(**cfg)
        r = _con_plazo(
            lambda: c.models.generate_content(
                model=modelo, contents=texto, config=cfg_interprete,
            ),
            PLAZO_INTENTO,
        )
        bruto = (getattr(r, "text", "") or "").strip()
    else:
        r = _con_plazo(
            lambda: c.chat.completions.create(
                model=modelo,
                messages=[{"role": "system", "content": INTERPRETE},
                          {"role": "user", "content": texto}],
                max_tokens=256,
                temperature=0,
                response_format={"type": "json_object"},
            ),
            PLAZO_INTENTO,
        )
        bruto = (r.choices[0].message.content or "").strip()
    _apunta_uso(prov, modelo)
    return bruto


def interpreta_consulta(texto):
    clave = normaliza(texto)
    memoria = st.session_state.setdefault("interpretaciones", {})
    # Repetir la misma consulta en la misma sesion NO vuelve a llamar al
    # modelo: sale de aqui en microsegundos. El panel marcaba entonces
    # "1. Interpretar el oficio: 0.0 s", que parece una llamada rapidisima y
    # es una lectura de diccionario. Confundir las dos cosas ya llevo una vez
    # a buscar la lentitud donde no estaba, asi que queda dicho en el panel.
    st.session_state["interprete_en_memoria"] = clave in memoria
    if clave in memoria:
        return memoria[clave]

    # Este paso tambien recorre la cascada. Si se quedara atado al primer
    # proveedor, con Gemini agotado la consulta llegaria sin interpretar al
    # siguiente y mediriamos mal: el modelo de relevo trabajaria con peores
    # candidatos que el primero, y pareceria peor de lo que es.
    bruto = None
    for prov in orden_proveedores():
        t_intento = time.perf_counter()
        try:
            bruto = _interpreta_una(prov, texto)
            break
        except Exception as e:  # noqa: BLE001
            # El paso 1 no dejaba rastro de sus intentos fallidos. Todo lo que
            # se gastaba aqui -intento colgado incluido- se sumaba al tramo
            # "1. Interpretar el oficio", y desde fuera una llamada lenta y un
            # intento muerto mas un relevo se ven exactamente igual.
            #
            # Medido el 01/09/2026: "monta placa de pladur" gasto 5,2 s en este
            # paso sin una sola linea en `relevos`. Con ese numero no se puede
            # decidir el plazo por intento: si son 5,2 s de llamada sana,
            # bajarlo la mata; si son 4 s de intento muerto mas 1 s de relevo,
            # bajarlo es justo lo que hay que hacer. Se anota para saberlo.
            #
            # Van marcados con "paso1:" para no mezclarlos con los del
            # afinado, que ya escriben en la misma lista.
            st.session_state.setdefault("relevos", []).append(
                f"paso1:{prov}:{time.perf_counter() - t_intento:.1f}s:"
                f"{type(e).__name__}"
            )
            _quema(prov, e)
            continue
    if bruto is None:
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


def flujo_modelo(texto, candidatos):
    """Recorre la cascada de proveedores hasta que uno empiece a responder.

    Solo se puede relevar ANTES de haber emitido nada. Si un proveedor se corta
    a mitad de la respuesta, cambiar a otro dejaria un JSON partido pegado al
    principio de otro, y eso es peor que un fallo limpio: seria un resultado
    incorrecto disfrazado de respuesta.
    """
    prompt = f"CANDIDATOS (única fuente válida):\n{candidatos}\n\nDESCRIPCIÓN: {texto}"
    ultimo = None
    for prov in orden_proveedores():
        c = _cliente(prov)
        if c is None:
            continue
        emitido = False
        try:
            if prov == "gemini":
                generador = _flujo_gemini(c, prompt, prov)
            else:
                generador = _flujo_openai(c, prompt, prov)
            for trozo in generador:
                emitido = True
                yield trozo
            return
        except Exception as e:  # noqa: BLE001
            if emitido:
                raise
            ultimo = e
            _quema(prov, e)
            # La cadena de relevo del proveedor caido queda degradada. El
            # siguiente tiene que empezar por su primer modelo, no heredar un
            # indice que no significa nada en su lista.
            st.session_state["cfg"] = 0
            continue
    if ultimo:
        raise ultimo
    raise RuntimeError("No hay ningún proveedor de IA con clave configurada.")


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

    # Desempaquetar fórmulas "Sí (texto)" / "No (texto)"
    m_parentesis = re.match(r'^(?:s[íi]|no)\s*\((.+?)\)$', t, flags=re.IGNORECASE)
    if m_parentesis and len(m_parentesis.group(1).strip()) > 2:
        t = m_parentesis.group(1).strip()

    # Eliminar repeticiones accidentales tipo "nóminas y nóminas" o "palabra palabra"
    t = re.sub(r'\b(\w{3,})\s+(?:y|e|o|u)\s+\1\b', r'\1', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(\w{3,})\s+\1\b', r'\1', t, flags=re.IGNORECASE)

    for _ in range(4):
        t = re.sub(
            r"^(?:la|el|los|las|un|una|unos|unas|en|a|al|del|de|para|con|por|su|sus)\s+",
            "", t, flags=re.IGNORECASE,
        ).strip()
    if len(t) > 75:
        t = t[:75].rsplit(" ", 1)[0]
        colgantes = {
            "y", "o", "u", "e", "de", "del", "al", "a", "en", "con", "por",
            "para", "sin", "sobre", "the", "la", "el", "los", "las", "un",
            "una", "mas", "más", "que", "su", "sus",
        }
        piezas = t.split()
        while len(piezas) > 1 and piezas[-1].lower().strip(",;") in colgantes:
            piezas.pop()
        t = " ".join(piezas)
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

    if " o " in q_limpia or "," in q_limpia:
        partes = [p.strip() for p in re.split(r",\s*|\s+o\s+", q_limpia) if p.strip()]
        if 2 <= len(partes) <= 3:
            ops = [limpia_opcion(p) for p in partes]
            if len(set(ops)) == len(ops) and all(len(x) > 1 for x in ops):
                return ops

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
    # El modelo contesta a veces con el ARRAY de ocupaciones a pelo, sin el
    # objeto que lo envuelve. json.loads lo traga tan contento y devuelve una
    # lista; como una lista no vacia es verdadera, se colaba entera por el
    # `if not datos` de aqui abajo y reventaba en el `datos.get()` siguiente
    # con AttributeError: 'list' object has no attribute 'get'.
    #
    # Medido el 01/09/2026 pulsando "Autobus interurbano" sobre "una persona
    # que conduce autobuses": relevos VACIO y total 1,0 s. No era lentitud ni
    # cuota, que es donde se estuvo buscando: el modelo respondia rapido y
    # bien y la respuesta se caia al abrirla. Sale mas en la via de
    # desambiguacion porque el prompt lleva la aclaracion pegada y ahi el
    # modelo devuelve la lista sola mas a menudo.
    #
    # Se envuelve y sigue el camino de siempre. Cualquier otra forma que no
    # sea un objeto (un numero, una cadena) se trata como respuesta vacia y
    # cae al rescate de objetos parciales, que para eso esta.
    if isinstance(datos, list):
        datos = {"ocupaciones": datos}
    elif not isinstance(datos, dict):
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

# El iframe es un documento aparte: no hereda ni el :root de la pagina ni sus
# tipografias. Los tokens salen del mismo diccionario para que no se separen, y
# las fuentes se piden con <link> desde pinta_tarjetas, no con @import.
ESTILO_TARJETAS = (
    _vars_css("body", TOKENS_CLARO, MOVIMIENTO)
    + "@media (prefers-color-scheme:dark){"
    + _vars_css("body", TOKENS_OSCURO)
    + "}"
    + """
*{ box-sizing:border-box; }
body{
  margin:0; padding:0; background:transparent; font-family:'Libre Franklin',system-ui,sans-serif;
  color:var(--texto); overflow:hidden;
}
.rejilla{
  display:grid; grid-template-columns:repeat(2,1fr);
  gap:6px; align-items:stretch;
}
@media (max-width:760px){ .rejilla{ grid-template-columns:1fr; } }

@keyframes entradaTarjeta{
  from{ opacity:0; transform:translateY(4px); }
  to{ opacity:1; transform:translateY(0); }
}

.tarjeta{
  background:var(--papel); border:1px solid var(--linea); border-left:4px solid var(--linea-fuerte);
  border-radius:6px; padding:7px 13px; display:flex; flex-direction:column;
  justify-content:space-between;
  transition:transform var(--medio) var(--entrada), box-shadow var(--medio) var(--entrada),
             border-color var(--medio) var(--entrada);
  box-shadow:0 1px 4px rgba(0,0,0,0.03);
  animation:entradaTarjeta var(--medio) var(--entrada) backwards;
  /* Entrada escalonada. --i lo pone pinta_tarjetas en el style de cada
     tarjeta. Las seis entraban a la vez y se leia como un bloque que aparece
     de golpe, no como una lista que llega. Con seis tarjetas el ultimo
     retraso es de 225 ms: por encima de ahi empieza a notarse como lentitud
     y deja de compensar. */
  animation-delay:calc(var(--i, 0) * var(--paso));
  /* La tarjeta entera copia el codigo al pulsarla, y desde que no hay boton
     es el unico gesto que hay: tiene que verse que es pulsable. La pista de
     al lado del codigo lo dice ademas con palabras. */
  cursor:pointer;
}
.tarjeta:hover{
  transform:translateY(-2px); box-shadow:0 5px 14px rgba(0,0,0,0.07); border-color:#CBD5E1;
}
.tarjeta.top{
  border-left-color:var(--rojo); background:#FFFFFF;
  box-shadow:0 2px 8px rgba(209,18,46,0.07);
}
.tarjeta.top:hover{
  box-shadow:0 6px 16px rgba(209,18,46,0.12);
}
.tarjeta.relleno{
  background:var(--gris); border-left-color:var(--linea); opacity:.92;
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
  letter-spacing:.04em; color:var(--negro); font-family:'JetBrains Mono',monospace;
}
/* La pista vive pegada al codigo, que es justo lo que se copia, y solo asoma
   al pasar por encima. Antes habia ademas un boton "Copiar" en la esquina:
   dos invitaciones a hacer lo mismo en una tarjeta que ya copiaba entera al
   pulsarla. Sobraba el boton, no la pista.
   Este hueco es tambien donde se confirma: al copiar dice "Copiado" en verde,
   asi que el aviso sale donde la persona ya estaba mirando. */
.pista{
  font-size:.62rem; font-weight:600; color:var(--suave); white-space:nowrap;
  opacity:0;
  transition:opacity var(--rapido) var(--salida), color var(--rapido) var(--salida);
}
.tarjeta:hover .pista, .tarjeta:focus-visible .pista{ opacity:1; }
.pista.hecho{ opacity:1; color:#16A34A; font-weight:700; }
.pista.fallo{ opacity:1; color:var(--rojo); font-weight:700; }
/* Sin raton no hay "pasar por encima": en el movil la pista se queda puesta o
   la tarjeta no dice en ninguna parte que se pueda pulsar. */
@media (hover:none){ .pista{ opacity:.7; } }
/* El foco va por dentro: la tarjeta lleva overflow:hidden por el ripple y un
   contorno por fuera se quedaria cortado. */
.tarjeta:focus-visible{ outline:2px solid var(--rojo); outline-offset:-2px; }

.denominacion{
  font-size:clamp(0.85rem, 0.94vw, 0.92rem); font-weight:600;
  line-height:1.26; color:var(--texto); margin:0 0 3px;
}
.motivo{
  font-size:clamp(0.74rem, 0.82vw, 0.8rem); color:var(--suave);
  line-height:1.24; margin-bottom:4px;
}

.etiquetas-fila{
  display:flex; align-items:center; gap:5px; flex-wrap:wrap; margin-top:auto; padding-top:3px;
}
.etiqueta{
  display:inline-flex; align-items:center; font-size:clamp(0.58rem, 0.65vw, 0.64rem);
  font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  padding:.14rem .42rem; border-radius:3px; background:var(--gris); color:var(--suave);
}
.etiqueta.recomendada{ background:linear-gradient(135deg, var(--rojo) 0%, #B90E26 100%); color:#fff; box-shadow:0 1px 3px rgba(209,18,46,0.25); }
.etiqueta.mando{ background:#FFF7ED; color:#C2410C; border:1px solid #FFEDD5; }
.etiqueta.directa{ background:#EFF6FF; color:#2563EB; border:1px solid #DBEAFE; }

/* MEJORA 7: Efecto ripple al copiar */
.tarjeta{ position:relative; overflow:hidden; }
@keyframes ripple{
  to{ transform:scale(4); opacity:0; }
}
.ripple-efecto{
  position:absolute; border-radius:50%; background:rgba(209,18,46,0.12);
  width:60px; height:60px; transform:scale(0); animation:ripple .5s ease-out forwards;
  pointer-events:none;
}

/* Modo oscuro de las tarjetas.
   Los tokens los emite TOKENS_OSCURO al principio de esta hoja, asi que
   .tarjeta ya cambia sola: usa var(--papel) y var(--linea-fuerte).
   Aqui abajo queda unicamente lo que NO es un token, es decir, los matices que
   solo tienen sentido sobre fondo oscuro. */
@media (prefers-color-scheme:dark){
  .tarjeta.top{ background:#1E1E2A; box-shadow:0 2px 8px rgba(232,56,79,0.1); }
  .tarjeta.relleno{ background:#151920; }
  .etiqueta{ background:#2D3748; color:var(--suave); }
  .pista.hecho{ color:#4ADE80; }
  .pista.fallo{ color:#F87171; }
  .etiqueta.directa{ background:#1E293B; color:#60A5FA; border-color:#1E3A5F; }
  .ripple-efecto{ background:rgba(232,56,79,0.15); }
}

/* El iframe es un documento aparte y no hereda la guarda de la pagina: aqui
   viven la entrada escalonada y el ripple, que son justo lo que sobra cuando
   alguien pide menos movimiento. */
@media (prefers-reduced-motion:reduce){
  *, *::before, *::after{
    animation-duration:.001ms !important;
    animation-iteration-count:1 !important;
    animation-delay:0ms !important;
    transition-duration:.001ms !important;
  }
}
"""
)

GUION_INTERACTIVO = """
// El alto del marco se calcula en el servidor suponiendo dos tarjetas por fila
// y 48 caracteres por linea. En el movil hay UNA columna y caben la mitad de
// letras, asi que el contenido crece y el marco lo recortaba. Aqui dentro si se
// conoce el ancho real, y se le pide a Streamlit el alto que hace falta.
//
// IMPORTANTE: un solo mecanismo. Antes habia dos -uno tocaba window.frameElement
// a mano y otro mandaba streamlit:setFrameHeight- y se disparaban el uno al
// otro: cambiar el alto del marco redibuja el cuerpo, el observador lo detecta
// y vuelve a pedir alto. Como ademas pedian valores distintos (+8 y +2), el
// marco oscilaba entre los dos sin parar y en el movil se veia como un scroll
// que no se detenia nunca.

// El aviso lo da la pista que hay junto al codigo. Antes lo daba el boton
// "Copiar" cambiando su propio texto; al quitarlo, la confirmacion se queda
// igual de cerca de lo que se acaba de copiar.
//
// Y avisa TAMBIEN cuando NO se ha podido copiar. En una herramienta cuyo unico
// fin es pegar el codigo en otro sitio, decir "Copiado" con el portapapeles
// vacio es peor que no decir nada: se pega lo de antes y nadie mira lo pegado.
function avisaCopiado(pista, bien){
  if (!pista) return;
  pista.textContent = bien ? '✓ Copiado' : 'No se ha podido copiar';
  pista.classList.remove('hecho', 'fallo');
  pista.classList.add(bien ? 'hecho' : 'fallo');
  clearTimeout(pista._vuelta);
  pista._vuelta = setTimeout(() => {
    pista.textContent = 'Pulsa para copiar';
    pista.classList.remove('hecho', 'fallo');
  }, bien ? 1400 : 2600);
}

// El camino de toda la vida, para navegadores viejos y para cuando el moderno
// dice que no. La caja se saca de la pantalla: si se deja donde cae, entra en
// la maquetacion y el marco de Streamlit se pone a recalcular el alto.
function copiaALaAntigua(texto, pista){
  const caja = document.createElement('textarea');
  caja.value = texto;
  caja.setAttribute('readonly', '');
  caja.style.position = 'fixed';
  caja.style.left = '-9999px';
  document.body.appendChild(caja);
  caja.select();
  let bien = false;
  try { bien = document.execCommand('copy'); } catch (e) { bien = false; }
  document.body.removeChild(caja);
  avisaCopiado(pista, bien);
}

function copiarTexto(texto, pista){
  // navigator.clipboard NO existe fuera de un contexto seguro (un http a
  // secas, o el archivo abierto a pelo desde el disco). Leerlo sin comprobar
  // levantaba un TypeError sincrono, que ni siquiera entra en el .catch de al
  // lado: no se copiaba nada y encima no se avisaba de nada.
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(texto)
      .then(() => avisaCopiado(pista, true))
      .catch(() => copiaALaAntigua(texto, pista));
    return;
  }
  copiaALaAntigua(texto, pista);
}

var ultimoAlto = 0;
var pedido = false;

function alto(){
  // NO se mide el documento. El cuerpo de este marco lleva overflow:hidden y
  // eso se propaga al viewport: documentElement.scrollHeight se queda clavado
  // en el alto de salida que le dio Streamlit y no crece nunca, por mucho que
  // crezca el contenido. En escritorio no se notaba porque la estimacion de
  // Python acierta con dos columnas; en el movil la rejilla pasa a una sola
  // columna, el contenido mide casi el doble y las tarjetas salian cortadas
  // por abajo. Se mide la rejilla, que es lo que si crece.
  var caja = document.querySelector('.rejilla') || document.querySelector('.rejilla-hueso');
  var h = caja ? caja.offsetTop + caja.offsetHeight
               : document.documentElement.scrollHeight;
  // Solo se avisa si el cambio es REAL. Sin este guardia, el propio
  // redimensionado del marco vuelve a disparar al observador y se entra en
  // bucle. Dos pixeles de margen absorben los redondeos del navegador.
  if (Math.abs(h - ultimoAlto) < 2) return;
  ultimoAlto = h;
  parent.postMessage(
    {type:'streamlit:setFrameHeight', height: h + 2},
    '*'
  );
}

// El observador puede dispararse muchas veces en un mismo repintado. Se agrupa
// todo en un unico aviso por fotograma para no inundar a Streamlit de mensajes.
function pideAlto(){
  if (pedido) return;
  pedido = true;
  requestAnimationFrame(function(){ pedido = false; alto(); });
}

// Copiar una tarjeta. El ripple sale donde se ha pulsado y, cuando no hay
// raton de por medio (teclado o atajo numerico), en el centro.
function copiaTarjeta(t, e){
  const rect = t.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'ripple-efecto';
  const x = (e && e.clientX) ? e.clientX - rect.left : rect.width / 2;
  const y = (e && e.clientY) ? e.clientY - rect.top : rect.height / 2;
  ripple.style.left = (x - 30) + 'px';
  ripple.style.top = (y - 30) + 'px';
  t.appendChild(ripple);
  setTimeout(() => ripple.remove(), 500);
  copiarTexto(t.dataset.cod, t.querySelector('.pista'));
}

document.querySelectorAll('.tarjeta').forEach(t => {
  t.addEventListener('click', (e) => copiaTarjeta(t, e));
  // La tarjeta es ya el unico control, asi que tiene que responder al teclado
  // como respondia el boton: se llega con el tabulador y se activa con Intro o
  // con espacio. Al espacio hay que pararle los pies o ademas hace scroll.
  t.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    copiaTarjeta(t);
  });
});

// Atajo 1-6 para copiar el codigo de esa posicion.
//
// Escuchaba solo en el window del marco, y ahi el foco casi nunca esta:
// despues de escribir en el buscador el foco se queda en el documento padre,
// asi que en uso normal la tecla no llegaba y el atajo estaba muerto salvo que
// antes pulsaras dentro de la zona de tarjetas. Se registra en los dos sitios.
function atajoCopiar(e){
  if (!['1','2','3','4','5','6'].includes(e.key)) return;
  var etiqueta = e.target && e.target.tagName;
  if (etiqueta === 'INPUT' || etiqueta === 'TEXTAREA') return;
  var tarjetas = document.querySelectorAll('.tarjeta');
  var tarjeta = tarjetas[parseInt(e.key, 10) - 1];
  if (tarjeta) { e.preventDefault(); copiaTarjeta(tarjeta); }
}

window.addEventListener('keydown', atajoCopiar);

// El padre puede estar en otro origen segun como sirva Streamlit el marco. Si
// no deja cruzar, se lanza y nos quedamos con el listener local: exactamente
// el comportamiento de antes, nada se rompe.
try {
  if (window.parent && window.parent !== window && window.parent.document) {
    window.parent.document.addEventListener('keydown', atajoCopiar);
  }
} catch (err) {
  /* origen distinto: se queda solo el listener del marco */
}

const observador = new ResizeObserver(pideAlto);
observador.observe(document.body);
// El cuerpo no cambia de alto cuando crece la rejilla -lo impide su propio
// overflow:hidden-, asi que hay que vigilar tambien a la rejilla o el
// observador no se entera de nada de lo que pasa dentro.
const rejillaVigilada = document.querySelector('.rejilla') || document.querySelector('.rejilla-hueso');
if (rejillaVigilada) observador.observe(rejillaVigilada);
window.addEventListener('load', pideAlto);
window.addEventListener('resize', pideAlto);
// Las tipografias se cargan desde fuera y al llegar cambian el alto del texto.
// Sin esto, la ultima medida se tomaba con la letra provisional y las tarjetas
// quedaban recortadas por abajo.
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(pideAlto);
}
setTimeout(pideAlto, 300);
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
        if o.get("motivo", "").startswith("Coincidencia directa"):
            etiquetas_html.append('<span class="etiqueta directa">⚡ Directa</span>')

        etiqueta_clase = "etiqueta mando" if es_mando else "etiqueta"
        etiquetas_html.append(
            f'<span class="{etiqueta_clase}">Nivel {o["nivel"]} &middot; {o["nivel_texto"]}</span>'
        )

        motivo_html = f'<div class="motivo">{o["motivo"]}</div>' if o.get("motivo") else ""

        # --i alimenta el animation-delay de la entrada escalonada.
        trozos.append(
            f'<div class="{clase}" style="--i:{i - 1}" data-cod="{o["codigo"]}"'
            f'     role="button" tabindex="0"'
            f'     aria-label="Copiar el código {o["codigo"]}">'
            f'  <div>'
            f'    <div class="fila">'
            f'      <div class="identificador">'
            f'        <span class="orden">{i:02d}</span>'
            f'        <span class="codigo">{o["codigo"]}</span>'
            f'        <span class="pista">Pulsa para copiar</span>'
            f'      </div>'
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
        ENLACE_FUENTES
        + f"<style>{ESTILO_TARJETAS}</style>"
        f"<div class=\"rejilla\">{''.join(trozos)}</div>"
        f"<script>{GUION_INTERACTIVO}</script>",
        height=estimada,
    )


def pinta_otras(otras, arranque=1, titulo="Ver otras ocupaciones del catálogo"):
    """Lista plegada con lo que ha devuelto el catálogo y no es tarjeta."""
    if not otras:
        return
    with st.expander(titulo):
        for orden, (cod, den) in enumerate(otras, arranque):
            st.markdown(
                f'<div class="fila-otra" style="padding:.15rem 0;'
                f'border-bottom:1px solid var(--linea);display:flex;gap:.5rem">'
                f'<span class="cod-otra" style="font-family:JetBrains Mono,monospace;'
                f'font-weight:700;font-size:.82rem;letter-spacing:.04em">{cod}</span>'
                f'<span class="den-otra" style="font-size:.82rem">{den}</span></div>',
                unsafe_allow_html=True,
            )


def pinta_resultado(payload, estado=None, interactivo=False, consulta=""):
    # Interruptor para la prueba masiva. resuelve() dibuja el resultado en
    # nueve puntos distintos; en una tanda de sesenta consultas eso llena la
    # pagina de tarjetas que nadie va a mirar. Cortando aqui se cortan los
    # nueve de una vez, y el circuito sigue siendo exactamente el mismo: se
    # calcula todo igual, solo que no se pinta.
    if st.session_state.get("silencio_pintado"):
        return

    if estado:
        # La barra ya no dice un porcentaje. La anterior avanzaba contra
        # ESPERA_MAXIMA (entonces 30 s) mientras la cascada contesta en 2 a 4,6 s: se
        # quedaba en el 17 % y saltaba al final, que se lee como atascada.
        st.markdown(
            f'<div class="pensando"><span class="pensando-icono">◉</span>{estado}</div>'
            f'<div class="barra-espera"><i></i></div>',
            unsafe_allow_html=True,
        )
        # Antes se volvia aqui, asi que durante la espera solo se veia la barra.
        # Pero el catalogo ya ha respondido en el primer milisegundo y esos
        # resultados estaban calculados y guardados sin llegar a mostrarse. Si
        # los hay, se pintan bajo la barra y se sustituyen cuando el modelo
        # termina: la espera es la misma, pero deja de ser una pantalla vacia.
        if not payload.get("ocupaciones"):
            # Y si no los hay, el hueco lo guardan los esqueletos. Sin ellos la
            # rejilla no existia y de pronto ocupaba 300 px, asi que todo lo de
            # debajo saltaba justo cuando llega la respuesta.
            st.markdown(
                '<div class="rejilla-hueso">'
                + '<div class="hueso"><span></span><span></span><span></span></div>' * 4
                + '</div>',
                unsafe_allow_html=True,
            )
            return
    if payload.get("aviso"):
        st.info(payload["aviso"])
        return

    # LA IA SE HA CAÍDO A MITAD.
    # Antes se pintaban aquí las tarjetas del catálogo en crudo con la
    # coletilla "sin afinar todavía". Es exactamente lo que se quitó de la
    # pantalla de espera, y por el mismo motivo: el primer resultado del
    # catálogo se equivoca a menudo con el lenguaje de la calle ("trabaja en
    # mcdonalds" devolvía personal de limpieza), y enseñarlo con forma de
    # respuesta hace que alguien copie un código equivocado. Se dice lo que ha
    # pasado, y el catálogo queda a un clic sin aspecto de recomendación.
    if payload.get("corte"):
        st.warning(payload["corte"])
        pinta_otras(payload.get("otras", []),
                    titulo="Ver lo que dice el catálogo, sin afinar")
        if MANTENIMIENTO and payload.get("fallo"):
            with st.expander("Ver el motivo"):
                st.code(payload["fallo"], language=None)
        return

    ocupaciones = payload.get("ocupaciones", [])
    if not ocupaciones:
        st.info("No encuentro coincidencias claras. Prueba con el nombre del puesto o función concreta.")
        return

    # 1. PREGUNTA ARRIBA (antes de las tarjetas)
    if payload.get("pregunta"):
        caja = st.container(key=f"caja_pregunta_{abs(hash(str(consulta) + str(payload.get('pregunta', '')))) % 999983}")
        with caja:
            st.markdown(
                '<div class="pregunta-titulo">Necesito un detalle más</div>'
                f'<div class="pregunta-texto">{payload["pregunta"]}</div>',
                unsafe_allow_html=True,
            )
            if interactivo:
                opciones = extraer_opciones(payload.get("pregunta", ""), payload.get("opciones"))
                cols = st.columns(len(opciones), gap="small")
                for idx, opc in enumerate(opciones):
                    with cols[idx]:
                        marca_op = f"resp_opt_{idx}_{abs(hash(str(consulta) + str(payload.get('pregunta', '')))) % 999983}"
                        if st.button(opc, key=marca_op, use_container_width=False):
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
        pinta_otras(otras, arranque=len(payload.get("ocupaciones", [])) + 1)

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

    # MEJORA 3: Chip discreto de proveedor/modelo
    prov = st.session_state.get("ultimo_proveedor", "")
    modelo = st.session_state.get("ultimo_modelo", "")
    tiempos = st.session_state.get("tiempos", [])
    if prov and modelo and not payload.get("fallo"):
        # Nombre legible: "gemini-2.5-flash-preview-05-20" → "Gemini Flash"
        partes = modelo.replace("-preview", "").replace("-latest", "").split("-")
        nombre_m = " ".join(p.capitalize() for p in partes if not p.isdigit() and len(p) > 1)
        if not nombre_m:
            nombre_m = modelo.split("-")[0].capitalize()
        # Sin filtrar, la fila de "respuesta del modelo" se sumaria al tramo
        # que ya la incluye y el chip enseñaria casi el doble de lo que tardo.
        total_s = sum(t for e, t in tiempos if SUBTRAMO not in e)
        tiempo_txt = f" · {total_s:.1f}s" if total_s > 0 else ""
        st.markdown(
            f'<div style="text-align:center">'
            f'<span class="chip-proveedor">'
            f'<span class="chip-proveedor-punto"></span>'
            f'{nombre_m}{tiempo_txt}'
            f'</span></div>',
            unsafe_allow_html=True,
        )
    elif any(o.get("motivo", "").startswith("Coincidencia directa") for o in payload.get("ocupaciones", [])):
        st.markdown(
            '<div style="text-align:center">'
            '<span class="chip-proveedor">'
            '<span class="chip-proveedor-punto" style="background:#3B82F6"></span>'
            'Coincidencia directa</span></div>',
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


def _medida():
    """Los números de la última consulta, listos para escribir en un CSV.

    Estaban solo en el panel, y eso obligaba a copiarlos a mano de la pantalla
    para poder compararlos. Aquí se recogen una vez y los usan tanto la
    descarga de sesión como la prueba masiva.
    """
    tiempos = st.session_state.get("tiempos", [])
    espera = next((t for e, t in tiempos if SUBTRAMO in e), None)
    # La "respuesta del modelo" esta DENTRO del tramo que la contiene: sumarla
    # seria contar dos veces los mismos segundos.
    total = sum(t for e, t in tiempos if SUBTRAMO not in e)
    pens = st.session_state.get("pensamiento") or {}
    return {
        "modelo": st.session_state.get("ultimo_modelo", ""),
        "respuesta_modelo": f"{espera:.1f}" if espera is not None else "",
        "total": f"{total:.1f}" if tiempos else "",
        "pensados": pens.get("pensados", ""),
        "relevos": " | ".join(st.session_state.get("relevos", [])),
    }


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


def n_candidatos():
    """Cuántas ocupaciones se le mandan al modelo en esta consulta.

    Normalmente es N_CANDIDATOS. El panel de mantenimiento puede cambiarlo
    para la sesión, y así comparar dos valores en la misma tanda: sin subir
    un archivo, sin esperar el reinicio de la aplicación y sin que los
    compañeros vean nada distinto. Fuera de mantenimiento nadie escribe esa
    casilla, así que siempre vale N_CANDIDATOS.
    """
    valor = st.session_state.get("n_candidatos")
    return int(valor) if valor else N_CANDIDATOS


def una_llamada():
    """¿Se resuelve esta consulta con una sola llamada al modelo?

    De fábrica lo que diga UNA_LLAMADA. El panel de mantenimiento lo cambia
    para la sesión, igual que el número de candidatos, para poder lanzar la
    misma tanda de las dos maneras y comparar. Fuera de mantenimiento nadie
    toca esa casilla.
    """
    # La casilla manda MIENTRAS ESTE PINTADA, y por orden de ejecucion: en la
    # pantalla masiva el aviso de cuota se dibuja arriba y la casilla abajo, asi
    # que en el rerun en que la marcas el valor propio todavia es el de antes y
    # el aviso salia un ciclo por detras -decia "dos o tres llamadas" con el
    # modo ya encendido, justo en la pantalla donde ese numero importa-.
    #
    # Si Streamlit purga la clave del widget -lo hace con los que dejan de
    # pintarse, y este solo existe con la pantalla masiva abierta- se cae al
    # valor propio, que es para lo que se guarda aparte. Y si tampoco esta,
    # a lo que diga la constante.
    if "una_llamada_interruptor" in st.session_state:
        return bool(st.session_state["una_llamada_interruptor"])
    valor = st.session_state.get("una_llamada")
    return UNA_LLAMADA if valor is None else bool(valor)


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

    tope_ia = n_candidatos()
    encontrados = busca(busqueda or texto, tope=tope_ia)
    # Lo que devuelve el buscador para lo que ESCRIBIO la persona, antes de que
    # la IA reinterprete y sustituya `encontrados`. La decision de quien manda
    # tiene que tomarse sobre esto, no sobre la busqueda reescrita por el
    # modelo: ahi las puntuaciones se aplanan y la ventaja real desaparece.
    literales = encontrados[:2]
    _raices_consulta = raices_de(busqueda or texto)
    st.session_state["tiempos"] = []
    # Se vacian junto con los tiempos. Si no, una consulta que falla se lleva
    # al CSV los tokens y los relevos de la anterior, y la tabla miente sin
    # que se note.
    st.session_state["pensamiento"] = None
    st.session_state["relevos"] = []
    # Hay IA si esta pedida y algun proveedor de la cascada tiene clave
    hay_ia = bool(usar_ia and orden_proveedores() and cliente())

    if not encontrados and not hay_ia:
        payload = {"ocupaciones": []}
        with zona.container():
            pinta_resultado(payload)
        return payload

    memoria = st.session_state["cache"]
    # El numero de candidatos y el proveedor forman parte de la clave: sin
    # ellos, dos corridas con ajustes distintos en la misma sesion se
    # devolvian resultados la una a la otra (visto en la prueba 24 vs 32).
    #
    # El modo de llamada va por lo mismo y es peor si falta: la tanda de una
    # llamada y la de dos se lanzan SEGUIDAS y sobre las MISMAS consultas, que
    # es justo la forma de compararlas. Sin el modo en la clave, la segunda
    # tanda sale entera del cache de la primera y las dos columnas del CSV dan
    # identicas: la comparacion diria "no cambia nada" pase lo que pase.
    clave = (f"{normaliza(texto + contexto)}|{tope_ia}|{proveedor_actual()}"
             f"|{'1' if una_llamada() else '2'}")
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

    if not hay_ia:
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
    # PASO 1, y solo si estamos en el modo de dos llamadas.
    #
    # Con una_llamada() la consulta va derecha al modelo que elige, con los
    # candidatos que saco el buscador de lo que se escribio. Si esos candidatos
    # no sirven, el propio modelo devuelve "otros_terminos" y ahi abajo se
    # rebusca y se le vuelve a preguntar: la interpretacion sigue existiendo,
    # pero solo se paga cuando hace falta. Ver UNA_LLAMADA.
    if not una_llamada():
        with zona.container():
            pinta_resultado({}, estado="Interpretando el oficio")
        with cronometra("1. Interpretar el oficio"):
            lecturas = interpreta_consulta(texto)
    else:
        lecturas = []
        # El panel lee esta marca para avisar de que el paso 1 salio de la
        # memoria de la sesion y su 0,0 s no es una llamada rapida. Sin
        # limpiarla, en modo de una llamada se queda la de la consulta ANTERIOR
        # y el panel avisa de un paso que ni siquiera ha corrido.
        st.session_state["interprete_en_memoria"] = False
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
        )[:tope_ia + 4]

        if len(mejores) < 3:
            mejores = busca(
                f"{busqueda or texto} {lecturas[0][0]}",
                tope=tope_ia + 4, grupos=lecturas[0][1],
            )
        if mejores:
            encontrados = mejores
            interpretado = (
                "la consulta",
                " · ".join(dict.fromkeys(t.split()[0] for t, _ in lecturas)),
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
        # El estado se pinta UNA vez, al entrar, y no se vuelve a tocar hasta
        # que hay respuesta. Antes se repintaba cada vez que el avance subia un
        # 4 %, porque la barra tenia que moverse. Ya no muestra porcentaje, asi
        # que repintar solo servia para reiniciar la animacion a media espera y
        # para rehacer el arbol de Streamlit con cada trozo que llegaba.
        with zona.container():
            pinta_resultado({}, estado=etiqueta)
        bruto = ""
        arranque = time.perf_counter()
        # El tramo entero ("2. Afinar el resultado") incluye la espera al
        # modelo Y el procesado local de lo que devuelve. Con un solo numero no
        # se distingue el modelo lento del parseo lento, asi que se anota
        # aparte lo que se va en esperar. Cuando habia streaming esta medida
        # era el primer trozo -lo que tardaba en ARRANCAR-; hoy que la
        # respuesta llega entera de una vez, es la llamada completa.
        espera = None
        entregas = 0
        for pedazo in flujo_modelo(texto + contexto, candidatos):
            if espera is None:
                espera = time.perf_counter() - arranque
                st.session_state.setdefault("tiempos", []).append(
                    (f"{etiqueta} · {SUBTRAMO}", espera)
                )
            bruto += pedazo
            entregas += 1
            # OJO: HOY ESTA COMPROBACION NO CORTA NADA, Y ES CORRECTO QUE NO
            # LO HAGA.
            #
            # El tope total solo puede cortar una respuesta que AUN esta
            # llegando, y eso son dos entregas o mas. Desde que Mistral perdio
            # el streaming (01/09/2026) NINGUN camino lo usa: _flujo_gemini y
            # _flujo_openai entregan la respuesta entera de una vez, asi que
            # `entregas` vale siempre 1 y la condicion es siempre falsa.
            #
            # Se deja puesta a proposito, y no por descuido. Con una entrega
            # unica el corte corria DESPUES de tener la respuesta completa en
            # la mano: lo unico que podia hacer era tirar a la basura una
            # respuesta buena que hubiera tardado 31 s y ensenarle a la persona
            # el aviso de que no se ha podido afinar. Peor que inutil. La
            # guarda `entregas > 1` es justo lo que lo impide, y vuelve a
            # servir sola el dia que algun proveedor recupere el streaming.
            #
            # Quien mire ESPERA_TOTAL buscando por que no corta: no corta aqui.
            # El tope que corta de verdad es PLAZO_INTENTO, en _con_plazo.
            if entregas > 1 and time.perf_counter() - arranque > ESPERA_TOTAL:
                raise TimeoutError(
                    f"El modelo ha tardado más de {ESPERA_TOTAL} segundos."
                )
        return interpreta(bruto)

    # Los tramos van numerados para leerlos en orden en el panel y en el CSV.
    # Sin paso 1 -modo de una llamada- afinar ES el primero: dejarlo como "2."
    # haria buscar en la tanda un paso 1 que no existe, y al comparar los dos
    # CSV las columnas no casarian.
    n = 1 if una_llamada() else 2
    try:
        lista = "\n".join(f"{c}:{d}" for _, c, d in encontrados)
        with cronometra(f"{n}. Afinar el resultado"):
            payload = consulta_al_modelo(lista, "Afinando el resultado")

        if payload.get("mas_terminos"):
            with zona.container():
                pinta_resultado({}, estado="Ampliando la búsqueda")
            ampliados = busca(f"{busqueda or texto} {payload['mas_terminos']}",
                              tope=tope_ia + 6)
            if ampliados:
                encontrados = ampliados
                interpretado = ("la descripción", payload["mas_terminos"])
                with cronometra(f"{n + 1}. Ampliar la búsqueda"):
                    segunda = consulta_al_modelo(
                        "\n".join(f"{c}:{d}" for _, c, d in ampliados),
                        "Afinando el resultado",
                    )
                if segunda["ocupaciones"]:
                    payload = segunda
    except Exception as e:  # noqa: BLE001
        zona.empty()
        # El mensaje se ajusta a la causa: el tope por minuto se pasa solo en
        # un rato y merece la pena decirlo, porque es lo que pasa de verdad
        # cuando se encadenan búsquedas. Lo demás no se sabe.
        if por_minuto(e):
            explicacion = (
                "He tenido que parar: el plan gratuito admite unas cinco "
                "búsquedas por minuto y se ha agotado el cupo. Espera un "
                "momento y vuelve a buscar."
            )
        else:
            explicacion = (
                "No he podido terminar de afinar el resultado. Vuelve a "
                "buscar dentro de un momento."
            )
        corte = {
            "ocupaciones": [],
            "otras": [(c, d) for _, c, d in encontrados[:10]],
            "fallo": f"{type(e).__name__}: {e}",
            "corte": explicacion + " Debajo tienes lo que dice el catálogo "
                     "sin afinar, que se equivoca a menudo con el lenguaje "
                     "de la calle: úsalo como pista, no como respuesta.",
        }
        pinta_resultado(corte)
        return corte

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
st.session_state.setdefault("modelo_ok", {})   # indice de relevo POR proveedor
st.session_state.setdefault("modelo_desde", {})  # cuando se degrado cada uno
st.session_state.setdefault("respuesta", None)
st.session_state.setdefault("masiva_abierta", False)
# Nunca debe quedarse echado entre recargas: si la prueba se corta a medias,
# la herramienta se quedaria muda para el usuario normal.
st.session_state["silencio_pintado"] = False
st.session_state.setdefault("por_guardar", [])
st.session_state.setdefault("refuerzos_por_guardar", [])
st.session_state.setdefault("ultima", "")
st.session_state.setdefault("consulta", "")
st.session_state.setdefault("proveedor", CASCADA)
st.session_state.setdefault("agotados", {})   # proveedor -> hora en que se aparto
st.session_state.setdefault("uso_proveedor", {})

# Clave del popover de ajustes. Con `on_change="rerun"` el popover lleva su
# estado en session_state, y escribir False ahi lo cierra desde codigo.
AJUSTES_ABIERTO = "ajustes_abierto"
st.session_state.setdefault(AJUSTES_ABIERTO, False)


def _cierra_ajustes():
    """Cierra el panel tras elegir algo.

    Solo se llama desde lo que YA da su respuesta al cerrarse (un interruptor,
    un desplegable). Los botones que escriben un resultado dentro del panel
    -probar la conexion, comprobar el gist- no lo cierran: cerrarlos seria
    esconder justo lo que la persona acaba de pedir ver.
    """
    st.session_state[AJUSTES_ABIERTO] = False


AUTOMATICO = "Automático (cadena de relevo)"
OPCIONES_PROVEEDOR = [CASCADA] + ORDEN
st.session_state.setdefault("modelo_elegido", AUTOMATICO)
st.session_state.setdefault("modelo_fijo", None)


def _limpia_memoria_ia():
    """Todo lo que un proveedor o modelo anterior haya dejado escrito.

    modelo_ok son indices sobre listas de modelos que acaban de cambiar. La
    cache y las interpretaciones son respuestas del modelo viejo, y dejarlas
    haria que la comparacion midiese en parte al proveedor anterior.
    """
    st.session_state["modelo_ok"] = {}
    st.session_state["modelo_desde"] = {}
    st.session_state["cfg"] = 0
    st.session_state["cache"] = {}
    st.session_state["interpretaciones"] = {}
    st.session_state["agotados"] = {}
    st.session_state["uso_proveedor"] = {}


def _cambia_proveedor():
    st.session_state["proveedor"] = st.session_state["sel_proveedor"]
    st.session_state["modelo_elegido"] = AUTOMATICO
    st.session_state["sel_modelo"] = AUTOMATICO
    st.session_state["modelo_fijo"] = None
    _limpia_memoria_ia()
    _cierra_ajustes()


def _cambia_modelo():
    elegido = st.session_state.get("sel_modelo", AUTOMATICO)
    st.session_state["modelo_elegido"] = elegido
    st.session_state["modelo_fijo"] = None if elegido == AUTOMATICO else elegido
    _limpia_memoria_ia()
    _cierra_ajustes()


# --- Tareas de un clic -------------------------------------------------------
# Van como `on_click`, NO en linea. Un boton en linea se ejecuta despues de que
# el selectbox ya exista en esta pasada, y Streamlit no deja escribir la clave
# de un widget ya creado. En el callback se escribe antes del rerun y funciona.

def _vacia_memoria():
    """Caché e interpretaciones, sin tocar proveedor ni modelo."""
    st.session_state["cache"] = {}
    st.session_state["interpretaciones"] = {}


def _reintenta_apartados():
    """Devuelve al ruedo lo apartado sin esperar a que caduque solo.

    Toca tambien la degradacion de modelo: un proveedor puede volver del
    apartado y seguir clavado en su tercer modelo, con lo que responderia el
    peor de los suyos y pareceria que el boton no ha hecho nada.
    """
    st.session_state["agotados"] = {}
    st.session_state["modelo_desde"] = {}
    st.session_state["modelo_ok"] = {}


def _medicion_limpia():
    """Deja la sesión lista para una tanda comparable.

    Fija el proveedor que este primero y su primer modelo, y vacia todo lo que
    pudiera responder por memoria. Sustituye los cuatro pasos que habia que
    acordarse de hacer a mano antes de cada prueba masiva, y que se olvidaron
    en la tanda del 26/08.
    """
    prov = proveedor_actual()
    primero = PROVEEDORES[prov]["modelos"][0]
    _limpia_memoria_ia()
    st.session_state["proveedor"] = prov
    st.session_state["sel_proveedor"] = prov
    st.session_state["modelo_elegido"] = primero
    st.session_state["sel_modelo"] = primero
    st.session_state["modelo_fijo"] = primero


def _de_fabrica():
    """Deshace todo lo que se toca en mantenimiento.

    Es el boton de seguridad antes de salir: los ajustes viven en la sesion,
    asi que si te dejas los candidatos en 32 o la IA apagada, sigue asi para
    quien use esa pestaña despues.
    """
    _limpia_memoria_ia()
    st.session_state["proveedor"] = CASCADA
    st.session_state["sel_proveedor"] = CASCADA
    st.session_state["modelo_elegido"] = AUTOMATICO
    st.session_state["sel_modelo"] = AUTOMATICO
    st.session_state["modelo_fijo"] = None
    st.session_state["n_candidatos"] = N_CANDIDATOS
    st.session_state["una_llamada"] = UNA_LLAMADA
    st.session_state["una_llamada_interruptor"] = UNA_LLAMADA
    st.session_state["usar_ia"] = True
    st.session_state["ia_interruptor"] = True


def _fuera_de_fabrica():
    """Qué está tocado ahora mismo. Vacío = todo como de fábrica."""
    dif = []
    if st.session_state.get("proveedor", CASCADA) != CASCADA:
        dif.append(f"proveedor fijado en {st.session_state['proveedor']}")
    if st.session_state.get("modelo_fijo"):
        dif.append(f"modelo fijado en {st.session_state['modelo_fijo']}")
    cand = st.session_state.get("n_candidatos")
    if cand and int(cand) != N_CANDIDATOS:
        dif.append(f"candidatos en {int(cand)} (de fábrica {N_CANDIDATOS})")
    if una_llamada() != UNA_LLAMADA:
        dif.append(
            "una sola llamada" if una_llamada() else "dos llamadas (con paso 1)"
        )
    if not st.session_state.get("usar_ia", True):
        dif.append("IA desactivada")
    return dif

# ---------------------------------------------------------------------------
# LOS OFICIOS DE LA PORTADA
# ---------------------------------------------------------------------------
# Quince bustos, todos con el mismo cuerpo y una sola marca que los distingue:
# el gorro, el casco, las tijeras, el estetoscopio. Nada mas. Cada rasgo de mas
# es un rasgo que hay que reconocer, y esto se ve de refilon mientras alguien
# escribe en el buscador; no es una ilustracion que nadie vaya a mirar dos
# veces. Por eso son siluetas planas en cuatro grises y no dibujos.
#
# El busto se recorta contra el circulo (clip-path): los hombros son mas anchos
# que el marco a esa altura, como en cualquier avatar redondo, y sin recorte se
# saldrian por los lados.

_BUSTO = (
    '<rect x="52" y="60" width="16" height="22" rx="5" class="av-piel"/>'
    '<path d="M18 120a42 42 0 0 1 84 0Z" class="av-cuerpo"/>'
    '<circle cx="60" cy="54" r="21" class="av-piel"/>'
)

# El ORDEN importa: los huecos reparten esta lista de nueve en nueve, asi que
# los nueve primeros son los que se ven al abrir la pagina, y los que se
# emparejan con ellos son los nueve siguientes en el mismo orden.
OFICIOS = [
    ("limpieza",                       # pañuelo a la cabeza
     '<circle cx="60" cy="54" r="21" class="av-marca"/>'
     '<path d="M40 60a20 20 0 0 0 40 0Z" class="av-piel"/>'
     '<circle cx="79" cy="57" r="5" class="av-marca"/>'),
    ("construccion",                   # casco con cresta y ala
     '<path d="M41 42a19 19 0 0 1 38 0Z" class="av-marca"/>'
     '<rect x="57" y="24" width="6" height="18" rx="3" class="av-sombra"/>'
     '<rect x="28" y="40" width="64" height="7" rx="3.5" class="av-marca"/>'),
    ("sanidad",                        # estetoscopio
     '<path d="M46 78v8a14 14 0 0 0 28 0v-8" class="av-linea"/>'
     '<path d="M60 100v6" class="av-linea"/>'
     '<circle cx="60" cy="111" r="6.5" class="av-marca"/>'),
    ("peluqueria",                     # tijeras
     '<path d="M52 98 70 78M68 98 50 78" class="av-linea"/>'
     '<circle cx="52" cy="105" r="6.5" class="av-linea"/>'
     '<circle cx="68" cy="105" r="6.5" class="av-linea"/>'
     '<circle cx="60" cy="88" r="3.5" class="av-marca"/>'),
    ("cocina",                         # gorro
     '<circle cx="47" cy="33" r="11" class="av-marca"/>'
     '<circle cx="60" cy="27" r="13" class="av-marca"/>'
     '<circle cx="73" cy="33" r="11" class="av-marca"/>'
     '<rect x="42" y="36" width="36" height="11" rx="3" class="av-sombra"/>'),
    ("teleoperador",                   # auriculares con micro
     '<path d="M38 54a22 22 0 0 1 44 0" class="av-linea"/>'
     '<rect x="31" y="50" width="12" height="19" rx="6" class="av-marca"/>'
     '<rect x="77" y="50" width="12" height="19" rx="6" class="av-marca"/>'
     '<path d="M77 67c0 9-7 14-13 14" class="av-linea"/>'
     '<circle cx="62" cy="81" r="4.5" class="av-marca"/>'),
    ("conduccion",                     # gorra de visera
     '<path d="M41 44a19 19 0 0 1 38 0Z" class="av-marca"/>'
     '<path d="M58 44h32a4 4 0 0 1 0 8H58Z" class="av-sombra"/>'),
    ("agricultura",                    # sombrero de ala ancha
     '<path d="M44 45a16 16 0 0 1 32 0Z" class="av-marca"/>'
     '<ellipse cx="60" cy="45" rx="35" ry="7" class="av-marca"/>'),
    ("almacen",                        # chaleco reflectante
     '<path d="M44 83c-8 6-12 21-12 37h56c0-16-4-31-12-37l-16 13Z" class="av-marca"/>'
     '<rect x="28" y="100" width="64" height="7" class="av-piel"/>'
     '<rect x="28" y="112" width="64" height="7" class="av-piel"/>'),
    # --- y con estos se van relevando ---
    ("administracion",                 # gafas y corbata
     '<rect x="42" y="49" width="16" height="12" rx="5" class="av-marca"/>'
     '<rect x="62" y="49" width="16" height="12" rx="5" class="av-marca"/>'
     '<rect x="57" y="53" width="6" height="4" class="av-marca"/>'
     '<path d="M60 80l-6 7 6 22 6-22Z" class="av-sombra"/>'),
    ("soldadura",                      # careta de soldador
     '<path d="M39 33h42v29a21 21 0 0 1-42 0Z" class="av-marca"/>'
     '<rect x="47" y="44" width="26" height="9" rx="2" class="av-piel"/>'),
    ("hosteleria",                     # pajarita
     '<path d="M60 87 44 79v17ZM60 87l16-8v17Z" class="av-marca"/>'
     '<rect x="55" y="82" width="10" height="10" rx="3" class="av-sombra"/>'),
    ("comercio",                       # bolsa de la compra
     '<path d="M53 88v-5a7 7 0 0 1 14 0v5" class="av-linea"/>'
     '<path d="M45 88h30v32H45Z" class="av-marca"/>'),
    ("reparto",                        # casco de moto
     '<circle cx="60" cy="52" r="24" class="av-marca"/>'
     '<rect x="44" y="47" width="32" height="15" rx="7.5" class="av-piel"/>'),
    ("docencia",                       # libro abierto
     '<path d="M60 91 39 83v29l21 8Z" class="av-marca"/>'
     '<path d="M60 91 81 83v29l-21 8Z" class="av-piel"/>'
     '<path d="M60 91v29" class="av-linea"/>'),
    ("mantenimiento",                  # martillo, ladeado
     '<g transform="rotate(-27 60 98)">'
     '<rect x="42" y="80" width="31" height="13" rx="3" class="av-marca"/>'
     '<rect x="55" y="90" width="9" height="32" rx="3" class="av-sombra"/>'
     '</g>'),
    ("electricista",                   # bombilla
     '<circle cx="60" cy="97" r="13" class="av-marca"/>'
     '<rect x="54" y="108" width="12" height="10" rx="2" class="av-sombra"/>'),
    ("seguridad",                      # placa
     '<path d="M60 82 78 88v14c0 10-9 15-18 19-9-4-18-9-18-19V88Z" class="av-marca"/>'
     '<circle cx="60" cy="101" r="5" class="av-piel"/>'),
]

HERO_HUECOS = 9        # circulos en la fila
HERO_TURNO = 3.6       # segundos que aguanta cada oficio en su hueco
HERO_DESFASE = 0.8     # ... de retraso de un hueco al siguiente
# La onda: los del centro grandes y a plena tinta, los de los bordes pequeños y
# desvaidos. Es lo que impide que nueve circulos en fila se lean como una regla.
HERO_ESCALAS = (0.6, 0.7, 0.82, 0.92, 1.0, 0.92, 0.82, 0.7, 0.6)
HERO_TINTES = (0.5, 0.62, 0.75, 0.88, 1.0, 0.88, 0.75, 0.62, 0.5)


def _avatar(clave, marca, retraso):
    tapa = f"hc-{clave}"
    return (
        f'<svg class="hero-of" viewBox="0 0 120 120" focusable="false"'
        f' style="animation-delay:{retraso:.2f}s">'
        f'<defs><clipPath id="{tapa}"><circle cx="60" cy="60" r="60"/></clipPath></defs>'
        f'<circle cx="60" cy="60" r="60" class="av-fondo"/>'
        f'<g clip-path="url(#{tapa})">{_BUSTO}{marca}</g>'
        f'</svg>'
    )


def hero_oficios():
    """La fila de oficios que se releva sola en la portada.

    Se reparte la lista de nueve en nueve, no de dos en dos: asi el hueco 0 se
    queda con los oficios 0 y 9, y en pantalla nunca coinciden dos iguales a la
    vez. El retraso es negativo a proposito; el porque esta en el comentario de
    .hero-fila, en la hoja de estilo.
    """
    por_hueco = len(OFICIOS) // HERO_HUECOS
    huecos = []
    for hueco in range(HERO_HUECOS):
        capas = [
            _avatar(*OFICIOS[capa * HERO_HUECOS + hueco],
                    retraso=-(capa * HERO_TURNO + hueco * HERO_DESFASE))
            for capa in range(por_hueco)
        ]
        huecos.append(
            f'<div class="hero-hueco" style="--e:{HERO_ESCALAS[hueco]};'
            f'--o:{HERO_TINTES[hueco]}">' + "".join(capas) + "</div>"
        )
    return (
        f'<div class="hero-fila" style="--vuelta:{HERO_TURNO * por_hueco}s"'
        f' aria-hidden="true">' + "".join(huecos) + "</div>"
    )


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
    with st.popover(":material/tune:", use_container_width=True,
                key=AJUSTES_ABIERTO, on_change="rerun"):
        # OJO: el valor por defecto va explicito con `value=`, y la clave del
        # widget NO es "usar_ia". Con key="usar_ia" a secas, si Streamlit purga
        # la clave -lo hace con widgets que viven dentro de un popover cerrado-
        # st.toggle cae en su valor de fabrica, que es False, y la herramienta
        # arrancaba con la IA apagada sin que nadie la hubiera tocado.
        # "usar_ia" se mantiene como valor propio, no como estado de widget.
        st.session_state["usar_ia"] = st.toggle(
            "Afinar con IA", value=st.session_state["usar_ia"],
            key="ia_interruptor", on_change=_cierra_ajustes,
            help="Desactivado, muestra las coincidencias del catálogo al instante.",
        )
        if st.session_state["registro"]:
            buffer = io.StringIO()
            escritor = csv.writer(buffer, delimiter=";")
            escritor.writerow([
                "consulta", "codigos", "modelo", "respuesta_modelo",
                "total", "pensados", "relevos", "fallo",
            ])
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
            # Las filas de "respuesta del modelo" van DENTRO del tramo que
            # las contiene: sumarlas contaria dos veces los mismos segundos.
            total = sum(t for e, t in tiempos if SUBTRAMO not in e)
            st.caption(f"· Total esperando al modelo: **{total:.1f} s**")
            if st.session_state.get("interprete_en_memoria"):
                st.caption(
                    "· El paso 1 salió de la memoria de la sesión: "
                    "ese tiempo no es una llamada al modelo."
                )

        pens = st.session_state.get("pensamiento")
        if pens:
            st.caption(
                f"· Tokens — entrada: **{pens['entrada']}**, "
                f"pensados: **{pens['pensados']}**, salida: **{pens['salida']}**"
            )

        tocado = _fuera_de_fabrica()
        if tocado:
            st.warning(
                "Ajustes fuera de fábrica: " + "; ".join(tocado)
                + ". Viven en la sesión: quien use esta pestaña después los hereda.",
                icon=":material/warning:",
            )

        st.divider()
        st.markdown("**Tareas**")
        t1, t2 = st.columns(2, gap="small")
        t1.button(
            "Preparar medición", on_click=_medicion_limpia,
            use_container_width=True,
            help="Fija el proveedor y su primer modelo, y vacía caché, "
                 "interpretaciones y contadores. Deja la sesión lista para una "
                 "tanda comparable, en un clic en vez de cuatro pasos.",
        )
        t2.button(
            "Volver a fábrica", on_click=_de_fabrica, use_container_width=True,
            help="Deshace todo: cascada, modelo automático, candidatos de "
                 "fábrica, IA encendida y memoria vacía. Púlsalo antes de salir "
                 "de mantenimiento.",
        )
        t3, t4 = st.columns(2, gap="small")
        t3.button(
            "Vaciar memoria", on_click=_vacia_memoria, use_container_width=True,
            help="Caché e interpretaciones, sin tocar proveedor ni modelo. Para "
                 "repetir una consulta y ver qué responde de verdad.",
        )
        t4.button(
            "Reintentar apartados", on_click=_reintenta_apartados,
            use_container_width=True,
            disabled=not (quemados() or degradados()),
            help="Devuelve al ruedo los proveedores apartados por falta de cupo "
                 "y deshace la degradación de modelo, sin esperar a que caduque "
                 "sola dentro de una hora.",
        )

        st.divider()
        st.markdown("**Proveedor de IA**")
        # Mismo patron que el interruptor de IA: el valor real vive en
        # "proveedor" y el widget usa su propia clave con `index=` explicito.
        # Con key="proveedor" a secas, una purga del estado del popover cerrado
        # devolvia el selector a la primera opcion sin avisar: fijabas Mistral
        # para medir, cerrabas el panel y la tanda salia en cascada.
        try:
            i_prov = OPCIONES_PROVEEDOR.index(st.session_state.get("proveedor", CASCADA))
        except ValueError:
            i_prov = 0
        st.session_state["proveedor"] = st.selectbox(
            "Proveedor", OPCIONES_PROVEEDOR, index=i_prov, key="sel_proveedor",
            on_change=_cambia_proveedor, label_visibility="collapsed",
            format_func=lambda p: (
                "Cascada: " + " → ".join(ORDEN) if p == CASCADA else p
            ),
            help="En cascada se recorren en orden y se salta el que no tenga "
                 "clave. Fijar uno apaga el relevo: es la única forma de medir "
                 "un proveedor sin que otro le responda por él. El cambio vale "
                 "solo para esta sesión.",
        )

        sin_clave = [p for p in ORDEN if not tiene_clave(p)]
        if sin_clave:
            st.caption("Sin clave en los Secrets, se saltan: " + ", ".join(sin_clave))
        apartados = quemados()
        if apartados:
            st.caption(
                "Apartados por cupo agotado (se reintentan solos como mucho "
                "dentro de una hora): " + ", ".join(sorted(apartados))
            )
        bajados = degradados()
        if bajados:
            st.caption(
                "Degradados a un modelo de respaldo: "
                + ", ".join(f"{p} → {m}" for p, m in sorted(bajados.items()))
            )

        # El contador es el instrumento para detectar que un proveedor lleva
        # dias caido sin que se note. Como metrica se lee de un vistazo; como
        # lista de captions grises se perdia entre las demas.
        conteo = st.session_state.get("uso_proveedor", {})
        if conteo:
            st.caption("Llamadas de esta sesión:")
            for col, (p, n) in zip(
                st.columns(len(conteo), gap="small"),
                sorted(conteo.items(), key=lambda x: -x[1]),
            ):
                col.metric(p, n)

        opciones_modelo = [AUTOMATICO] + PROVEEDORES[proveedor_actual()]["modelos"]
        if st.session_state.get("modelo_elegido") not in opciones_modelo:
            st.session_state["modelo_elegido"] = AUTOMATICO
            st.session_state["modelo_fijo"] = None
        st.session_state["modelo_elegido"] = st.selectbox(
            "Modelo", opciones_modelo,
            index=opciones_modelo.index(st.session_state["modelo_elegido"]),
            key="sel_modelo", on_change=_cambia_modelo,
            help="Automático usa la cadena de relevo dentro del proveedor. Para "
                 "comparar hay que fijar uno, o no sabrás cuál te ha respondido.",
        )

        if st.button("Probar la conexión con la IA", use_container_width=True):
            prueba = cliente()
            if prueba is None:
                st.error(f"No hay clave {ajustes_actual()['clave']} en los Secrets.")
            else:
                try:
                    if proveedor_actual() == "gemini":
                        cfg = dict(system_instruction="Responde únicamente con la palabra ok.", max_output_tokens=2048)
                        r = prueba.models.generate_content(
                            model=modelo_actual(), contents="ok",
                            config=types.GenerateContentConfig(**cfg),
                        )
                        salida = (getattr(r, "text", "") or "").strip()
                    else:
                        r = prueba.chat.completions.create(
                            model=modelo_actual(),
                            messages=[
                                {"role": "system", "content": "Responde únicamente con la palabra ok."},
                                {"role": "user", "content": "ok"},
                            ],
                            max_tokens=32, temperature=0,
                        )
                        salida = (r.choices[0].message.content or "").strip()
                    st.success(f"{proveedor_actual()} · {modelo_actual()}: {salida[:60]}")
                except Exception as e:  # noqa: BLE001
                    st.error(f"{type(e).__name__}: {e}")

        st.divider()
        st.markdown("**Herramientas**")
        compartido = lexico_compartido()
        gist_activo, _ = _credenciales()
        if gist_activo:
            st.caption(f"Diccionario compartido: {len(compartido)} términos aprendidos.")
            if st.button("Comprobar que guarda", use_container_width=True):
                correcto, detalle = prueba_gist()
                (st.success if correcto else st.error)(detalle)

        # Fuera del `if gist_activo`: la prueba masiva no depende del diccionario
        # compartido, y tenerla dentro hacia que caducar el token del Gist se
        # llevara por delante el acceso a la prueba.
        if st.button("Prueba masiva", use_container_width=True):
            st.session_state["masiva_abierta"] = True
            st.rerun()
        st.caption(
            "Lanza muchas consultas seguidas por el circuito completo, con IA "
            "incluida, y deja un registro descargable. Gasta cuota: cada "
            "consulta son "
            + ("una o dos" if una_llamada() else "dos o tres")
            + " llamadas al modelo."
        )


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
        if proveedor_actual() == "gemini":
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


@st.cache_data(show_spinner=False)
def _esperados_de_casos():
    """Mapa consulta -> código correcto, leído de casos.csv.

    La tanda escribía la consulta y lo que eligió el modelo, pero no CON QUÉ
    había que comparar: `codigo_esperado` se quedaba en el archivo. Para saber
    el acierto había que cruzar a mano las dos tandas contra casos.csv, ochenta
    filas, que es la clase de trabajo que se hace mal a las once de la noche.

    Con esto el CSV trae ya `esperado` y `acierto`, y comparar dos tandas es
    contar dos columnas.
    """
    try:
        with open("casos.csv", encoding="utf-8-sig") as f:
            return {
                normaliza(x["consulta"]): (x.get("codigo_esperado") or "").strip()
                for x in csv.DictReader(f, delimiter=";")
                if x.get("consulta")
            }
    except Exception:  # noqa: BLE001
        return {}


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
    # El gasto depende del modo, y el aviso tiene que decir la verdad: con el
    # paso 1 son dos llamadas por consulta (tres si el modelo pide ampliar la
    # búsqueda) y sin él, una (dos si la pide).
    if una_llamada():
        st.caption(
            "Cada consulta gasta una o dos llamadas al modelo. Con 100 consultas "
            "se van entre 100 y 200, así que vigila la cuota del día."
        )
    else:
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
            help="Depende del proveedor. Gemini admite 15 peticiones por minuto y "
                 "cada consulta gasta dos o tres: con 12 segundos caben unas cinco "
                 "por minuto, que es su ritmo máximo sostenible. Mistral con "
                 "ministral-3b aguanta 12,5 peticiones por SEGUNDO, así que ahí "
                 "basta 1. Quedarse corto no rompe la prueba desde que hay cascada, "
                 "pero la tanda acaba medida con un proveedor distinto del que "
                 "creías: fija uno arriba antes de comparar.",
        )
        limpiar = c3.checkbox("Ignorar lo ya guardado", value=True,
                              help="Vacía la memoria de la sesión para que ninguna "
                                   "consulta se responda con un resultado antiguo.")

        # Recordatorio de con que se va a lanzar. La tanda del 26/08 se midio
        # sobre tres modelos distintos sin que se viera en ninguna pantalla.
        elegido = st.session_state.get("proveedor", CASCADA)
        if elegido == CASCADA:
            st.warning(
                "Vas a lanzar en **cascada**: si el primer proveedor se atasca, "
                "parte de la tanda la responderá otro y el resultado no será "
                "comparable. Para medir, fija proveedor y modelo en ajustes.",
                icon=":material/warning:",
            )
        else:
            fijo = st.session_state.get("modelo_fijo")
            st.caption(
                f"Se lanzará con **{elegido}** · "
                + (f"**{fijo}**" if fijo else "modelo automático (puede degradar)")
            )

        # Cuántas ocupaciones ve el modelo, solo para esta sesión. Permite
        # lanzar la misma tanda con dos valores distintos sin subir un archivo
        # ni esperar el reinicio, que era la única forma de comparar y obligaba
        # a medir el "antes" antes de tocar nada. Al salir de esta pantalla el
        # valor sigue puesto: si has estado probando, déjalo otra vez en el que
        # trae de fábrica antes de volver al buscador.
        st.number_input(
            f"Candidatos que ve la IA (de fábrica, {N_CANDIDATOS})",
            5, 60, N_CANDIDATOS, 1, key="n_candidatos",
            help="Cuántas ocupaciones del catálogo se le mandan al modelo para "
                 "que elija. Lo que no entra en esta lista, el modelo no lo "
                 "puede proponer por bien que razone. Lanza la tanda con un "
                 "valor, apúntalo, cámbialo y vuelve a lanzarla con la misma "
                 "semilla para comparar.",
        )

        # El interruptor que decide si el paso 1 corre o no. Está aquí y no en
        # los ajustes del buscador a propósito: no es una preferencia de uso,
        # es la variable de un experimento, y solo significa algo lanzando la
        # misma tanda con la misma semilla de las dos maneras.
        # La key del widget NO es "una_llamada", igual que la del interruptor
        # de IA no es "usar_ia" y por el mismo motivo: si la clave del valor y
        # la del widget son la misma y Streamlit purga la del widget -lo hace
        # con los que dejan de pintarse, y este solo se pinta con la pantalla
        # masiva abierta-, el ajuste vuelve solo al de fábrica a mitad de una
        # tanda. El valor propio se guarda aparte, desde lo que devuelve la
        # casilla.
        st.session_state["una_llamada"] = st.checkbox(
            f"Una sola llamada al modelo (de fábrica, "
            f"{'sí' if UNA_LLAMADA else 'no'})",
            value=una_llamada(), key="una_llamada_interruptor",
            help="Marcado, la consulta va derecha al modelo que elige, con los "
                 "candidatos que saca el buscador de lo escrito; si no le "
                 "sirven, él pide más términos y se le vuelve a preguntar. "
                 "Sin marcar, corre antes el paso 1, que traduce la frase a "
                 "vocabulario oficial en una llamada aparte. Compara ACIERTO, "
                 "no solo segundos: quitar el paso 1 ahorra una llamada, y lo "
                 "que hay que ver es si se paga en aciertos.",
        )

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
            _esp = _esperados_de_casos().get(normaliza(consulta), "")
            top_local = locales[0][1] if locales else ""
            interpretado = payload.get("interpretado") or ("", "")
            tiempos = st.session_state.get("tiempos", [])

            filas.append({
                "consulta": consulta,
                # Queda escrito en cada fila para que dos CSV de tandas
                # distintas se puedan comparar meses después sin fiarse de la
                # memoria ni del nombre del archivo.
                "candidatos": n_candidatos(),
                # Igual que "candidatos": el modo queda escrito en cada fila
                # para poder cruzar dos tandas meses después sin fiarse del
                # nombre del archivo.
                "una_llamada": "sí" if una_llamada() else "no",
                "top_local": top_local,
                "denom_local": locales[0][2] if locales else "",
                "ventaja_local": (
                    round(locales[0][0] / locales[1][0], 2)
                    if len(locales) > 1 and locales[1][0] else ""
                ),
                "interpretado_como": interpretado[1] if len(interpretado) > 1 else "",
                "elegido_1": codigos[0] if codigos else "",
                "denom_1": elegidas[0]["denominacion"] if elegidas else "",
                # Vacías cuando la consulta no sale de casos.csv: las de "al
                # azar" y las que propone la IA no tienen código correcto con
                # el que comparar, y una columna a medias miente menos que un
                # "no" que en realidad es un "no se sabe".
                "esperado": _esp,
                "acierto": ("" if not _esp else
                            "sí" if codigos and codigos[0] == _esp else "no"),
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
                "proveedor": st.session_state.get("ultimo_proveedor", ""),
                "modelo": st.session_state.get("ultimo_modelo", ""),
                "error": error,
                "segundos": round(tardanza, 1),
                "detalle_tiempos": " | ".join(f"{n}:{t:.1f}" for n, t in tiempos),
                "pensados": (st.session_state.get("pensamiento") or {}).get("pensados", ""),
                "relevos": " | ".join(st.session_state.get("relevos", [])),
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

        # El acierto va PRIMERO y aparte, porque es lo que decide. El resto son
        # síntomas: se miran cuando el acierto ya ha dicho algo.
        con_esperado = [f for f in filas if f["esperado"]]
        if con_esperado:
            aciertos = sum(1 for f in con_esperado if f["acierto"] == "sí")
            modo_txt = "una llamada" if una_llamada() else "dos llamadas"
            x, y = st.columns(2)
            x.metric(
                f"Acierto · {modo_txt}",
                f"{aciertos} de {len(con_esperado)}",
                f"{100 * aciertos / len(con_esperado):.0f} %",
                delta_color="off",
            )
            y.metric("Media", f"{medio:.1f} s")
            st.caption(
                "Compara ESTO entre las dos tandas, y solo después los segundos. "
                "Apunta las dos cifras antes de lanzar la siguiente: el resumen "
                "se pisa, aunque el CSV descargado no."
            )
        else:
            st.caption(
                "Estas consultas no salen de casos.csv, así que no hay código "
                "correcto con el que comparar y no se puede medir el acierto. "
                "Para decidir el modo de llamada, lanza las de «Escritas a mano»."
            )

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
    st.button("Codificador de ocupaciones", key="marca", on_click=empezar_de_nuevo)
    campo, boton, ajustes = st.columns([6.4, 1.1, 0.5], gap="small")
    with campo:
        # El placeholder NO lleva lupa. La llevaba, y en pantallas estrechas
        # salian dos seguidas: esta y la del boton de al lado, que en movil se
        # queda en icono porque la palabra "Buscar" no cabe en el renglon.
        # Se quita la del campo y no la del boton: la lupa es el simbolo de la
        # ACCION de buscar, y ahi si dice algo; dentro del campo era un adorno
        # que ademas comia ancho al texto de ayuda, que es largo y en movil se
        # corta. En escritorio el boton sigue con su palabra y el campo se
        # explica solo.
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
    medida = _medida()
    st.session_state["registro"].append((
        rotulo or entrada,
        " | ".join(o["codigo"] for o in payload.get("ocupaciones", [])),
        medida["modelo"],
        medida["respuesta_modelo"],
        medida["total"],
        medida["pensados"],
        medida["relevos"],
        str(payload.get("fallo", ""))[:200],
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
    st.markdown(
        '<div class="bienvenida">'
        + hero_oficios()
        + '<div class="bienvenida-texto">'
        'Escribe el <b>puesto de trabajo</b> o describe <b>lo que hacía la persona</b>'
        '</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="seccion">O prueba con un ejemplo</div>', unsafe_allow_html=True)
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
import threading

_pendientes = st.session_state.pop("por_guardar", [])
_refuerzos = st.session_state.pop("refuerzos_por_guardar", [])

if _pendientes or _refuerzos:
    def _ejecuta_segundo_plano(pendientes, refuerzos):
        for clave, valor in pendientes:
            guarda_termino(clave, valor)
        for codigo, palabras in refuerzos:
            guarda_refuerzo(codigo, palabras)
    
    threading.Thread(
        target=_ejecuta_segundo_plano,
        args=(_pendientes, _refuerzos),
        daemon=True
    ).start()

# ---------------------------------------------------------------------------
# FIRMA
# ---------------------------------------------------------------------------
# Ultimo elemento de la pagina, en todas las pantallas. Aqui esta la autoria
# visible: el repositorio puede ser privado, pero quien use el enlace tiene que
# poder saber quien ha hecho esto. `target="_blank"` para no sacar a nadie de
# una busqueda a medias, y `rel="noopener"` porque abrir enlaces externos sin
# el deja a la pagina de destino con acceso a la de origen.
st.markdown(
    '<div class="pie-firma">Creado por '
    '<a href="https://www.linkedin.com/in/alvarosant/" target="_blank" '
    'rel="noopener noreferrer">Álvaro Santamaría</a></div>',
    unsafe_allow_html=True,
)

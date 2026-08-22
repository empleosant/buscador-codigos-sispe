"""
Cargador del motor para las pruebas.

Importa `app.py` hasta justo antes de la interfaz y devuelve el módulo ya
cargado, con `busca`, `raiz`, `normaliza`, `IDX`, `VACIAS` y `SINONIMOS`
listos para usar. No llama a la IA ni gasta cuota.

Lo usan `evaluar.py` (aciertos) y `estres.py` (robustez).

DÓNDE CORTA
    Busca la marca `# === FIN DEL MOTOR ===` en app.py. Si no está, prueba
    con la cabecera `# INTERFAZ`. Si tampoco, ejecuta el archivo entero
    apoyándose en el Streamlit de mentira de más abajo.

    La marca es lo que evita que una reorganización de app.py deje las
    pruebas rotas en silencio, que es justo lo que pasó el 21 de agosto.
"""

import sys
import types

APP = "app.py"

MARCAS = ("# === FIN DEL MOTOR ===", "# INTERFAZ")


class _Vacio:
    """Sustituto de cualquier objeto que devuelva Streamlit.

    Se traga todas las llamadas, sirve como gestor de contexto (`with col:`)
    y es iterable, para que un desempaquetado inesperado no reviente.
    """

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, n):
        return lambda *a, **k: _Vacio()

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def __call__(self, *a, **k):
        return _Vacio()


def _cache(f=None, **k):
    """Imita cache_resource y cache_data: memoriza de verdad."""

    def deco(fn):
        guardado = {}

        def envoltorio(*a, **kk):
            clave = (a, tuple(sorted(kk.items())))
            if clave not in guardado:
                guardado[clave] = fn(*a, **kk)
            return guardado[clave]

        envoltorio.clear = guardado.clear
        return envoltorio

    return deco(f) if callable(f) else deco


def _columnas(reparto, *a, **k):
    """st.columns admite un número o una lista de pesos. Devuelve tantas
    columnas como se pidan, para que el desempaquetado funcione."""
    n = reparto if isinstance(reparto, int) else len(reparto)
    return [_Vacio() for _ in range(max(int(n), 1))]


class _Falso(types.ModuleType):
    """Cualquier función de Streamlit que se llame aquí no hace nada."""

    def __getattr__(self, nombre):
        return lambda *a, **k: _Vacio()


def _instala_streamlit_falso():
    falso = _Falso("streamlit")
    falso.cache_resource = _cache
    falso.cache_data = _cache
    falso.session_state = {}
    falso.secrets = {}

    # Las que devuelven varias cosas y por tanto se desempaquetan.
    falso.columns = _columnas
    falso.tabs = lambda etiquetas, *a, **k: [_Vacio() for _ in etiquetas]

    componentes = _Falso("streamlit.components")
    v1 = _Falso("streamlit.components.v1")
    componentes.v1 = v1
    falso.components = componentes

    sys.modules["streamlit"] = falso
    sys.modules["streamlit.components"] = componentes
    sys.modules["streamlit.components.v1"] = v1

    # google-genai y openai no hacen falta para probar la búsqueda.
    for ausente in ("google", "google.genai", "google.genai.types", "openai"):
        sys.modules.setdefault(ausente, _Falso(ausente))


def _recorta(codigo):
    for marca in MARCAS:
        if marca in codigo:
            return codigo[: codigo.index(marca)], marca
    return codigo, None


def carga_motor(ruta=APP, avisar=True):
    import os

    if not os.path.exists(ruta):
        sys.exit(f"No encuentro {ruta} en esta carpeta.")

    codigo, marca = _recorta(open(ruta, encoding="utf-8").read())
    if marca is None and avisar:
        print(
            f"AVISO: no encuentro ninguna marca de corte en {ruta}. Se ejecuta\n"
            "       el archivo entero, interfaz incluida. Vuelve a poner la\n"
            f"       línea «{MARCAS[0]}» delante de la interfaz.\n",
            file=sys.stderr,
        )

    _instala_streamlit_falso()

    motor = types.ModuleType("motor")
    motor.__dict__["__file__"] = ruta
    exec(compile(codigo, ruta, "exec"), motor.__dict__)  # noqa: S102

    for pieza in ("busca", "raiz", "normaliza", "IDX", "VACIAS", "SINONIMOS"):
        if not hasattr(motor, pieza):
            sys.exit(
                f"El motor cargado no tiene «{pieza}». Es probable que la marca\n"
                f"de corte «{marca}» haya quedado demasiado arriba en {ruta}."
            )

    return motor


def cabecera(motor):
    n = len(motor.IDX["registros"])
    ampliado = motor.IDX.get("ampliado", 0)
    return (
        f"Catálogo: {n} ocupaciones · {ampliado} con vocabulario ampliado\n"
        f"Vocabulario: {len(motor.VACIAS)} vacías, {len(motor.SINONIMOS)} sinónimos"
    )

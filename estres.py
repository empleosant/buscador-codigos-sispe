"""
Batería de estrés del buscador.

`evaluar.py` comprueba aciertos concretos: 40 consultas con su código bueno.
Esto es otra cosa. Aquí no se afirma qué código es el correcto, sino que el
buscador se comporta de forma sensata pase lo que pase: que no reviente con
basura, que dé lo mismo escribir con acentos o sin ellos, que el singular
encuentre lo que está en plural y que dos consultas iguales devuelvan lo
mismo.

Sirve para cazar el tipo de avería que `evaluar.py` no ve: un cambio en el
lematizador puede seguir sacando 40/40 y haber roto doscientas ocupaciones
que no están entre los 40 casos. Pasó el 21/08/2026.

USO
    python estres.py              todas las pruebas
    python estres.py --detalle    enseña cada caso que falla
    python estres.py --rapido     salta las pruebas que recorren el catálogo

No llama a la IA ni gasta cuota. Tarda unos segundos.
"""

import re
import sys
import time
import unicodedata

# Windows: la consola no siempre acepta «·», «…» ni acentos. Sin esto, la
# batería puede morir con UnicodeEncodeError al redirigir la salida a un
# archivo, que es un error confuso y no tiene nada que ver con las pruebas.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001  consolas antiguas
    pass

from motor_pruebas import cabecera, carga_motor

DETALLE = "--detalle" in sys.argv
RAPIDO = "--rapido" in sys.argv

PRUEBAS = []
RESUMEN = []


def prueba(nombre, lento=False):
    def deco(f):
        PRUEBAS.append((nombre, f, lento))
        return f
    return deco


def _informe(nombre, ok, total, fallos, umbral, unidad="casos"):
    porcentaje = 100 * ok / total if total else 100.0
    pasa = porcentaje >= umbral
    RESUMEN.append((nombre, pasa))
    marca = " OK " if pasa else "MAL"
    print(f"[{marca}] {nombre:38} {ok:5}/{total:<5} {porcentaje:5.1f} %  (mínimo {umbral} %)")
    if fallos and (DETALLE or not pasa):
        for f in fallos[:8]:
            print(f"          · {f}")
        if len(fallos) > 8:
            print(f"          · … y {len(fallos) - 8} más")
    return pasa


def top1(motor, consulta):
    r = motor.busca(consulta, tope=3)
    return r[0][1] if r else None


# ---------------------------------------------------------------------------
# 1. Que no reviente
# ---------------------------------------------------------------------------

BASURA = [
    "", "   ", "\t\n", "a", "??", "!!!???", "ñ", "-----", "///", "___",
    "123", "0" * 400, "á" * 60, "y y y y", "o", "e", "de la el",
    "SELECT * FROM ocupaciones; DROP TABLE x", "<script>alert(1)</script>",
    "{{7*7}}", "../../etc/passwd", "🚌 conduzco 🚌", "camarero/a", "peón-albañil",
    "84201043", "8420104", "842010430000",
    "conduzco autobuses " * 80,
    "camarero y", "y camarero", "y y camarero y y",
]


@prueba("No revienta con entradas raras")
def p_basura(motor):
    fallos = []
    for entrada in BASURA:
        try:
            resultados = motor.busca(entrada, tope=5)
            if not isinstance(resultados, list):
                fallos.append(f"{entrada[:30]!r} devuelve {type(resultados).__name__}")
        except Exception as e:  # noqa: BLE001
            fallos.append(f"{entrada[:30]!r} -> {type(e).__name__}: {e}")
    total = len(BASURA)
    return _informe("No revienta con entradas raras", total - len(fallos), total, fallos, 100)


# ---------------------------------------------------------------------------
# 1 bis. Que no reviente con lo que devuelve el MODELO
#
# Las de arriba prueban lo que escribe la persona. Estas prueban lo que manda
# el modelo, que es de donde han salido los tres fallos que han tumbado la app:
#
#   01/09  interpreta()          el array de ocupaciones sin el objeto que lo
#                                envuelve -> 'list' object has no attribute 'get'
#   02/09  interpreta_consulta()  lecturas como cadenas sueltas
#   02/09  verifica()             códigos a pelo en vez de objetos
#
# Los tres son lo mismo: el modelo se salta la forma pedida, y un .get() sobre
# algo que no es un diccionario levanta AttributeError. Como el paso 1 corre en
# TODAS las consultas, lo que reviente ahí no degrada el resultado: tumba la
# herramienta entera y la persona ve una pantalla roja.
#
# Aquí se fija que ninguna forma rara vuelva a hacerlo. Da igual que la
# interpretación salga peor: lo que no puede es levantar una excepción.
# ---------------------------------------------------------------------------

RESPUESTAS_DEL_MODELO = [
    # la forma que se pide en el prompt
    '{"ocupaciones":[{"codigo":"92101027","nivel":"00","motivo":"x"}],"pregunta":"","opciones":[]}',
    # el array sin el objeto que lo envuelve (el fallo del 01/09)
    '[{"codigo":"92101027","nivel":"00","motivo":"x"}]',
    # códigos a pelo (el fallo del 02/09 en verifica)
    '{"ocupaciones":["92101027","84201043"],"pregunta":"","opciones":[]}',
    '{"ocupaciones":[92101027],"pregunta":"","opciones":[]}',
    # mezclas y basura dentro de la lista
    '{"ocupaciones":["92101027",{"codigo":"84201043"},null,[],7]}',
    # opciones y pregunta con formas raras
    '{"ocupaciones":[],"pregunta":"¿En casa o en residencia?","opciones":"en casa"}',
    '{"ocupaciones":[],"pregunta":"¿En casa?","opciones":[{"texto":"en casa"}]}',
    # otros_terminos donde se espera una cadena
    '{"ocupaciones":[],"otros_terminos":["camarero","piso"]}',
    '{"ocupaciones":[],"otros_terminos":{"a":1}}',
    # envuelto en vallas de código, que el modelo pone a menudo
    '```json\n{"ocupaciones":[{"codigo":"92101027"}]}\n```',
    # formas degeneradas
    '{}', '[]', 'null', '3', '"camarero de piso"',
    '{"ocupaciones":null}', '{"ocupaciones":"92101027"}',
    # json roto y texto suelto
    '{"ocupaciones":[{"codigo":', 'no he podido responder', '',
]

LECTURAS_DEL_MODELO = [
    '{"lecturas":[{"terminos":"camarero piso","grupos":"9"}]}',
    # el fallo del 02/09: lecturas como cadenas
    '{"lecturas":["camarero de piso","limpieza"]}',
    '{"lecturas":["camarero de piso",{"terminos":"limpiadora","grupos":"9"}]}',
    '{"lecturas":[{"terminos":["camarero","piso"],"grupos":["9"]}]}',
    '{"lecturas":[1,2,3]}', '{"lecturas":null}', '{"lecturas":[]}',
    '{"terminos":"camarero piso","grupos":"9"}',
    '["camarero de piso"]', '{"lecturas":[{"terminos":', 'camarero de piso', '',
]


@prueba("El modelo no puede tumbar la app")
def p_respuestas_modelo(motor):
    fallos = []

    for bruto in RESPUESTAS_DEL_MODELO:
        try:
            r = motor.interpreta(bruto)
            if not isinstance(r, dict) or not isinstance(r.get("ocupaciones"), list):
                fallos.append(f"interpreta({bruto[:34]!r}) devuelve {type(r).__name__}")
        except Exception as e:  # noqa: BLE001
            fallos.append(f"interpreta({bruto[:34]!r}) -> {type(e).__name__}: {e}")

    # interpreta_consulta habla con el modelo: se le pone uno de mentira que
    # devuelve cada forma, y se comprueba que ninguna sube una excepción.
    original_una, original_orden = motor._interpreta_una, motor.orden_proveedores
    try:
        motor.orden_proveedores = lambda: ["gemini"]
        for bruto in LECTURAS_DEL_MODELO:
            motor.st.session_state["interpretaciones"] = {}
            motor._interpreta_una = lambda prov, texto, b=bruto: b
            try:
                r = motor.interpreta_consulta("una persona que limpia habitaciones")
                if not isinstance(r, list):
                    fallos.append(f"interpreta_consulta({bruto[:30]!r}) devuelve {type(r).__name__}")
            except Exception as e:  # noqa: BLE001
                fallos.append(f"interpreta_consulta({bruto[:30]!r}) -> {type(e).__name__}: {e}")
    finally:
        motor._interpreta_una, motor.orden_proveedores = original_una, original_orden

    total = len(RESPUESTAS_DEL_MODELO) + len(LECTURAS_DEL_MODELO)
    return _informe("El modelo no puede tumbar la app",
                    total - len(fallos), total, fallos, 100)


# ---------------------------------------------------------------------------
# 2. La forma de escribir no cambia el resultado
# ---------------------------------------------------------------------------

def _sin_acentos(t):
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn"
    )


VARIANTES = {
    "mayúsculas":   str.upper,
    "sin acentos":  _sin_acentos,
    "guiones":      lambda t: t.replace(" ", "-"),
    "barras":       lambda t: t.replace(" ", "/"),
    "guión bajo":   lambda t: t.replace(" ", "_"),
    "espacios":     lambda t: "  " + t.replace(" ", "   ") + " ",
    "punto final":  lambda t: t + ".",
    "signos":       lambda t: "¿" + t + "?",
}


@prueba("Da igual cómo se escriba")
def p_invariancia(motor, consultas):
    fallos, comprobaciones, ok = [], 0, 0
    for consulta in consultas:
        base = top1(motor, consulta)
        for nombre, f in VARIANTES.items():
            comprobaciones += 1
            if top1(motor, f(consulta)) == base:
                ok += 1
            else:
                fallos.append(f"{nombre}: «{consulta[:38]}»")
    return _informe("Da igual cómo se escriba", ok, comprobaciones, fallos, 100)


# ---------------------------------------------------------------------------
# 3. El singular encuentra lo que el catálogo guarda en plural
# ---------------------------------------------------------------------------

@prueba("Singular y plural lematizan igual", lento=True)
def p_convergencia(motor):
    """La avería más cara y la más silenciosa.

    El catálogo dice MONTADORES y el ciudadano escribe montador. Si el
    lematizador no los lleva a la misma raíz, la ocupación se vuelve
    inalcanzable sin que ningún caso de evaluar.py se entere.
    """
    pares = set()
    for reg in motor.IDX["registros"]:
        for w in re.findall(r"[a-zñ]+", motor.normaliza(reg["denom"])):
            if len(w) > 4 and w.endswith("es"):
                pares.add((w[:-2], w))
            elif len(w) > 3 and w.endswith("s"):
                pares.add((w[:-1], w))

    fallos = []
    for singular, plural in sorted(pares):
        if motor.raiz(singular) != motor.raiz(plural):
            agente = singular.endswith(("dor", "sor", "tor", "or"))
            fallos.append(
                f"{'AGENTE ' if agente else ''}{singular} -> {motor.raiz(singular)}"
                f"   /   {plural} -> {motor.raiz(plural)}"
            )
    agentes = [f for f in fallos if f.startswith("AGENTE")]
    if agentes:
        print(f"          ATENCIÓN: {len(agentes)} nombres de agente (-or/-dor) rotos.")
    total = len(pares)
    # El 1 % de residuo son irregulares del castellano, no una regresión.
    return _informe("Singular y plural lematizan igual", total - len(fallos), total, fallos, 98)


# ---------------------------------------------------------------------------
# 4. Cada ocupación se encuentra a sí misma
# ---------------------------------------------------------------------------

@prueba("Cada ocupación se encuentra a sí misma", lento=True)
def p_autoidentificacion(motor):
    """Buscar la denominación oficial exacta debe devolver esa ocupación.

    Mide la salud general del índice y de la puntuación. No llega al 100 %
    porque el catálogo tiene entradas casi sinónimas (MÉDICOS, MEDICINA
    GENERAL frente a otras de medicina); por eso se admite el top-3.
    """
    registros = motor.IDX["registros"]
    ok, fallos = 0, []
    for reg in registros:
        codigos = [c for _, c, _ in motor.busca(reg["denom"], tope=3)][:3]
        if reg["codigo"] in codigos:
            ok += 1
        else:
            salio = codigos[0] if codigos else "nada"
            fallos.append(f"{reg['codigo']} {reg['denom'][:44]} -> {salio}")
    return _informe("Cada ocupación se encuentra a sí misma", ok, len(registros), fallos, 99)


# ---------------------------------------------------------------------------
# 5. Todo lo que sale existe en el catálogo
# ---------------------------------------------------------------------------

@prueba("Ningún código inventado")
def p_codigos_reales(motor, consultas):
    """La promesa del README: ninguna denominación procede del modelo."""
    catalogo = {reg["codigo"] for reg in motor.IDX["registros"]}
    fallos, total, ok = [], 0, 0
    for consulta in list(consultas) + BASURA:
        for _, codigo, denom in motor.busca(consulta, tope=10):
            total += 1
            if codigo in catalogo and len(codigo) == 8 and codigo.isdigit() and denom:
                ok += 1
            else:
                fallos.append(f"«{consulta[:28]}» -> {codigo!r} / {denom[:30]!r}")
    return _informe("Ningún código inventado", ok, max(total, 1), fallos, 100)


# ---------------------------------------------------------------------------
# 6. Dos veces lo mismo da lo mismo
# ---------------------------------------------------------------------------

@prueba("Resultados estables")
def p_determinismo(motor, consultas):
    fallos, ok = [], 0
    for consulta in consultas:
        a = [c for _, c, _ in motor.busca(consulta, tope=5)]
        b = [c for _, c, _ in motor.busca(consulta, tope=5)]
        if a == b:
            ok += 1
        else:
            fallos.append(f"«{consulta[:38]}»")
    return _informe("Resultados estables", ok, len(consultas), fallos, 100)


# ---------------------------------------------------------------------------
# 7. Consultas coordinadas
# ---------------------------------------------------------------------------

COORDINADAS = [
    ("cobro en caja", "repongo estantes"),
    ("limpio habitaciones", "hago camas"),
    ("atiendo el telefono", "archivo documentos"),
    ("sirvo mesas", "preparo cafes"),
    ("conduzco furgoneta", "reparto pedidos"),
]


@prueba("La segunda tarea también cuenta")
def p_coordinadas(motor):
    """El troceado por «y» busca que la segunda tarea no se pierda.

    Se exige que la consulta unida devuelva algo relacionado con alguna de
    las dos mitades, no que coincida con una en concreto.
    """
    fallos, ok = [], 0
    for a, b in COORDINADAS:
        sueltos = set()
        for mitad in (a, b):
            sueltos.update(c for _, c, _ in motor.busca(mitad, tope=5))
        juntas = [c for _, c, _ in motor.busca(f"{a} y {b}", tope=5)]
        if juntas and sueltos & set(juntas):
            ok += 1
        else:
            fallos.append(f"«{a} y {b}» no recupera nada de sus mitades")
    return _informe("La segunda tarea también cuenta", ok, len(COORDINADAS), fallos, 100)


# ---------------------------------------------------------------------------
# 8. El respaldo: la segunda petición que sale cuando la primera se cuelga
# ---------------------------------------------------------------------------

@prueba("El respaldo no pisa una respuesta buena")
def p_respaldo(motor):
    """`_con_plazo` es lo único que decide cuánto espera la persona cuando
    una llamada al modelo se queda colgada. No toca el modelo: se le pasan
    funciones de mentira que tardan lo que se les diga, y se comprueba que
    hace lo que promete en cada caso. Ninguna espera pasa de medio segundo.
    """
    import threading

    fallos = []
    casos = 0

    def caso(nombre, condicion):
        nonlocal casos
        casos += 1
        if not condicion:
            fallos.append(nombre)

    def cuenta(valor, tarda=0.0, falla=None):
        """Una llamada de mentira: tarda `tarda` s y devuelve `valor`, o
        levanta `falla`. Cuenta cuántas veces se la ha llamado."""
        n = {"llamadas": 0}
        cerrojo = threading.Lock()

        def hacer():
            with cerrojo:
                n["llamadas"] += 1
            time.sleep(tarda)
            if falla:
                raise falla
            return valor
        hacer.n = n
        return hacer

    respaldos = []
    anota = respaldos.append

    # 1. Contesta rápido: ni respaldo ni espera.
    respaldos.clear()
    h = cuenta("ok", tarda=0.02)
    inicio = time.time()
    r = motor._con_plazo(h, 1.0, respaldo=0.3, al_respaldar=anota)
    caso("rápido devuelve el valor", r == "ok")
    caso("rápido no lanza respaldo", h.n["llamadas"] == 1 and not respaldos)
    caso("rápido no espera al plazo", time.time() - inicio < 0.25)

    # 2. La primera se cuelga y el respaldo vuelve: se entrega el respaldo sin
    #    esperar a la primera ni al plazo, y queda anotado.
    respaldos.clear()
    tardas = iter([0.8, 0.03])          # primera lenta, segunda rápida
    llamadas = []

    def h2():
        t = next(tardas)
        llamadas.append(t)
        time.sleep(t)
        return f"tras {t}"
    inicio = time.time()
    r = motor._con_plazo(h2, 1.5, respaldo=0.15, al_respaldar=anota)
    caso("colgada: gana el respaldo", r == "tras 0.03")
    caso("colgada: se lanzaron dos", len(llamadas) == 2)
    caso("colgada: queda anotado", len(respaldos) == 1 and 0.1 < respaldos[0] < 0.5)
    caso("colgada: no espera al plazo", time.time() - inicio < 0.6)

    # 3. Falla al momento (un 429, un 400): se relanza tal cual, con su tipo,
    #    y NO se lanza respaldo, que sería gastar cupo en lo mismo.
    respaldos.clear()
    h = cuenta(None, tarda=0.01, falla=ValueError("429 quota"))
    try:
        motor._con_plazo(h, 1.0, respaldo=0.3, al_respaldar=anota)
        caso("error rápido: se relanza", False)
    except ValueError:
        caso("error rápido: se relanza", True)
    except Exception as e:  # noqa: BLE001
        caso(f"error rápido: tipo intacto (llegó {type(e).__name__})", False)
    caso("error rápido: sin respaldo", h.n["llamadas"] == 1 and not respaldos)

    # 4. Las dos se cuelgan: TimeoutError al plazo, no antes ni después.
    respaldos.clear()
    h = cuenta("tarde", tarda=2.0)
    inicio = time.time()
    try:
        motor._con_plazo(h, 0.4, respaldo=0.1, al_respaldar=anota)
        caso("las dos colgadas: TimeoutError", False)
    except TimeoutError:
        caso("las dos colgadas: TimeoutError", True)
    except Exception as e:  # noqa: BLE001
        caso(f"las dos colgadas: TimeoutError (llegó {type(e).__name__})", False)
    caso("las dos colgadas: se rinde al plazo", 0.35 < time.time() - inicio < 0.9)
    caso("las dos colgadas: hubo respaldo", h.n["llamadas"] == 2 and len(respaldos) == 1)

    # 5. La primera falla DESPUÉS de salir el respaldo: se espera al respaldo.
    respaldos.clear()
    turnos = iter([("falla", 0.2), ("ok", 0.3)])

    def h5():
        que, t = next(turnos)
        time.sleep(t)
        if que == "falla":
            raise RuntimeError("503")
        return "respaldo"
    r = motor._con_plazo(h5, 1.5, respaldo=0.05, al_respaldar=anota)
    caso("falla tardía: gana el respaldo", r == "respaldo")

    # 6. Sin respaldo pedido, se comporta como siempre: espera y corta.
    h = cuenta("tarde", tarda=2.0)
    inicio = time.time()
    try:
        motor._con_plazo(h, 0.2)
        caso("sin respaldo: TimeoutError", False)
    except TimeoutError:
        caso("sin respaldo: TimeoutError", True)
    caso("sin respaldo: una sola llamada", h.n["llamadas"] == 1)

    return _informe("El respaldo no pisa una respuesta buena",
                    casos - len(fallos), casos, fallos, 100)


# ---------------------------------------------------------------------------
# 9. Velocidad
# ---------------------------------------------------------------------------

@prueba("Suficientemente rápido")
def p_velocidad(motor, consultas):
    inicio = time.time()
    vueltas = 3
    for _ in range(vueltas):
        for consulta in consultas:
            motor.busca(consulta, tope=10)
    total = len(consultas) * vueltas
    ms = 1000 * (time.time() - inicio) / total
    print(f"[{' OK ' if ms < 60 else 'MAL'}] {'Suficientemente rápido':38} {ms:8.1f} ms por consulta  (máximo 60)")
    RESUMEN.append(("Suficientemente rápido", ms < 60))
    return ms < 60


# ---------------------------------------------------------------------------

def consultas_de_prueba(motor):
    """Consultas reales tomadas de casos.csv, más algunas denominaciones."""
    import csv
    import os

    lista = []
    if os.path.exists("casos.csv"):
        with open("casos.csv", encoding="utf-8-sig") as f:
            lista = [fila["consulta"] for fila in csv.DictReader(f, delimiter=";")]
    if not lista:
        lista = [reg["denom"] for reg in motor.IDX["registros"][:40]]
    return lista


def main():
    motor = carga_motor()
    print(cabecera(motor), end="\n\n")
    consultas = consultas_de_prueba(motor)
    print(f"Consultas de trabajo: {len(consultas)}\n")

    inicio = time.time()
    for nombre, funcion, lento in PRUEBAS:
        if lento and RAPIDO:
            print(f"[salt] {nombre:38} (--rapido)")
            continue
        argumentos = funcion.__code__.co_varnames[: funcion.__code__.co_argcount]
        funcion(motor, consultas) if "consultas" in argumentos else funcion(motor)

    malas = [n for n, ok in RESUMEN if not ok]
    print(f"\n{len(RESUMEN) - len(malas)} de {len(RESUMEN)} pruebas pasan"
          f"  ·  {time.time() - inicio:.1f}s")
    if malas:
        print("\nNo pasan:")
        for n in malas:
            print(f"  · {n}")
        print("\nRepite con --detalle para ver los casos concretos.")
    return 1 if malas else 0


if __name__ == "__main__":
    sys.exit(main())

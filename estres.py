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
# 8. Velocidad
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

"""
Batería de pruebas del buscador.

Comprueba que los cambios en el vocabulario, en el lematizador o en la
puntuación no rompen lo que ya funcionaba. Prueba SOLO la búsqueda local: no
llama a la IA, no gasta cuota y tarda un par de segundos.

USO
    python evaluar.py                 pasa todos los casos
    python evaluar.py --detalle       enseña además los tres primeros de cada uno
    python evaluar.py --actualizar    reescribe casos.csv con el resultado actual

ARCHIVOS
    casos.csv   consulta ; codigo_esperado ; denominacion ; tope
                "tope" es la posición máxima admitida: 1 exige que salga
                primero, 3 se conforma con que esté entre los tres primeros.

CÓMO AMPLIARLA
    Cada vez que una consulta real falle, añade una línea a casos.csv con el
    código correcto. Queda como prueba para siempre.
"""

import csv
import os
import sys

CASOS = "casos.csv"
APP = "app.py"


def carga_motor():
    """Importa app.py hasta justo antes de la interfaz.

    Se corta ahí porque a partir de ese punto el archivo dibuja pantalla, y
    aquí solo interesa el buscador.
    """
    import types

    if not os.path.exists(APP):
        sys.exit(f"No encuentro {APP} en esta carpeta.")

    codigo = open(APP, encoding="utf-8").read()
    marca = "# MODO MANTENIMIENTO"
    if marca in codigo:
        codigo = codigo[:codigo.index(marca)]

    # Streamlit mínimo de mentira: el motor solo usa las cachés y el estado.
    class _Vacio:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __getattr__(self, n):
            return lambda *a, **k: _Vacio()

    def _cache(f=None, **k):
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

    class _Falso(types.ModuleType):
        """Cualquier función de Streamlit que se llame aquí no hace nada."""

        def __getattr__(self, nombre):
            return lambda *a, **k: _Vacio()

    falso = _Falso("streamlit")
    falso.cache_resource = _cache
    falso.cache_data = _cache
    falso.session_state = {}
    falso.secrets = {}

    componentes = _Falso("streamlit.components")
    v1 = _Falso("streamlit.components.v1")
    componentes.v1 = v1
    falso.components = componentes

    sys.modules["streamlit"] = falso
    sys.modules["streamlit.components"] = componentes
    sys.modules["streamlit.components.v1"] = v1

    # google-genai y openai no hacen falta para probar la búsqueda
    for ausente in ("google", "google.genai", "google.genai.types", "openai"):
        sys.modules.setdefault(ausente, _Falso(ausente))

    motor = types.ModuleType("motor")
    motor.__dict__["__file__"] = APP
    exec(compile(codigo, APP, "exec"), motor.__dict__)  # noqa: S102
    return motor


def main():
    detalle = "--detalle" in sys.argv
    actualizar = "--actualizar" in sys.argv

    motor = carga_motor()
    print(f"Catálogo: {len(motor.IDX['registros'])} ocupaciones", end="")
    print(f" · {motor.IDX.get('ampliado', 0)} con vocabulario ampliado")
    print(f"Vocabulario: {len(motor.VACIAS)} vacías, {len(motor.SINONIMOS)} sinónimos\n")

    if not os.path.exists(CASOS):
        sys.exit(f"No encuentro {CASOS} en esta carpeta.")

    with open(CASOS, encoding="utf-8-sig") as f:
        casos = list(csv.DictReader(f, delimiter=";"))

    aciertos, fallos, nuevos = 0, [], []
    for caso in casos:
        consulta = caso["consulta"]
        esperado = caso["codigo_esperado"].strip()
        tope = int(caso.get("tope") or 1)

        resultados = motor.busca(consulta, tope=max(tope, 3))
        codigos = [c for _, c, _ in resultados]
        posicion = codigos.index(esperado) + 1 if esperado in codigos else 0

        if posicion and posicion <= tope:
            aciertos += 1
            marca = "  ok "
        else:
            fallos.append((consulta, esperado, codigos[:3]))
            marca = "FALLA"

        if detalle or marca == "FALLA":
            print(f"{marca}  {consulta[:52]:54} esperado {esperado}")
            for i, (_, c, d) in enumerate(resultados[:3], 1):
                print(f"          {i}. {c}  {d[:56]}")

        nuevos.append({
            "consulta": consulta,
            "codigo_esperado": codigos[0] if actualizar and codigos else esperado,
            "denominacion": resultados[0][2][:44] if actualizar and resultados
                            else caso.get("denominacion", ""),
            "tope": tope,
        })

    total = len(casos)
    print(f"\n{aciertos} de {total} ({100 * aciertos // max(total, 1)} %)")

    if fallos:
        print("\nFallan:")
        for consulta, esperado, salieron in fallos:
            print(f"  {consulta[:56]:58} esperado {esperado}, salió {salieron[0]}")

    if actualizar:
        with open(CASOS, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=["consulta", "codigo_esperado", "denominacion", "tope"],
                delimiter=";",
            )
            w.writeheader()
            w.writerows(nuevos)
        print(f"\n{CASOS} reescrito con los resultados actuales.")

    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())

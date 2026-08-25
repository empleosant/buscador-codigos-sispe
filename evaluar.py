"""
Batería de pruebas del buscador.

Comprueba que los cambios en el vocabulario, en el lematizador o en la
puntuación no rompen lo que ya funcionaba. Prueba SOLO la búsqueda local: no
llama a la IA, no gasta cuota y tarda un par de segundos.

Mide dos cosas distintas:

  ACIERTOS    el código correcto sale entre los primeros (columna "tope").
  COBERTURA   el código correcto entra en la lista de N_CANDIDATOS que se le
              manda al modelo. Es más flojo que un acierto, pero es la
              condición mínima: lo que no llega a esa lista, el modelo no lo
              puede elegir por muy bien que razone. Antes no se medía, y por
              eso un recorte de la lista de candidatos podía dejar la batería
              entera en verde mientras la herramienta empeoraba.

USO
    python evaluar.py                 pasa todos los casos
    python evaluar.py --detalle       enseña además los tres primeros de cada uno
    python evaluar.py --informe       vuelca lo que devuelve el motor a un CSV aparte

ARCHIVOS
    casos.csv         consulta ; codigo_esperado ; denominacion ; tope ; resuelto_ia
                "tope" es la posición máxima admitida: 1 exige que salga
                primero, 3 se conforma con que esté entre los tres primeros.
                "resuelto_ia" con un "si" marca los casos que la búsqueda
                local falla pero que Gemini resuelve en la app, comprobados
                a mano. Se siguen probando y se siguen viendo, pero no
                tumban las comprobaciones automáticas: así el rojo sigue
                significando "algo que funcionaba se ha roto".
                Este script NUNCA escribe en casos.csv: es la referencia.
    informe_evaluacion.csv   salida de --informe, regenerable, no versionar
    motor_pruebas.py  carga app.py sin la interfaz (compartido con estres.py)

CÓMO AMPLIARLA
    Cada vez que una consulta real falle, añade a mano una línea a casos.csv
    con el código correcto, comprobado contra el catálogo oficial o contra un
    caso ya grabado en SilcoiWeb. Queda como prueba para siempre.

    El código esperado se decide mirando el catálogo, NUNCA copiando lo que
    contesta el motor: si la referencia se genera desde la salida del propio
    motor, la batería deja de medir nada y solo confirma lo que ya hace.
"""

import csv
import os
import sys

# Windows: evita UnicodeEncodeError al redirigir la salida a un archivo.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from motor_pruebas import cabecera, carga_motor

CASOS = "casos.csv"
INFORME = "informe_evaluacion.csv"

# Porcentaje mínimo de casos en los que el código correcto tiene que entrar en
# la lista de candidatos que se le manda al modelo. El 25/08/2026 estaba en el
# 95 % con N_CANDIDATOS = 24, así que 90 deja margen sin dejar pasar una
# regresión de verdad. Bajarlo para que pase una batería en rojo es engañarse:
# lo que no llega a la lista, el modelo no lo puede elegir.
COBERTURA_MINIMA = 90


def main():
    if "--actualizar" in sys.argv:
        sys.exit(
            "--actualizar ya no existe.\n"
            "Reescribía casos.csv con la salida del propio motor, así que\n"
            "convertía en 'correcto' lo que el buscador contestara ese día y\n"
            "borraba la referencia real.\n"
            "Usa --informe: vuelca los resultados a "
            f"{INFORME} sin tocar {CASOS}."
        )

    detalle = "--detalle" in sys.argv
    informe = "--informe" in sys.argv

    motor = carga_motor()
    print(cabecera(motor), end="\n\n")

    if not os.path.exists(CASOS):
        sys.exit(f"No encuentro {CASOS} en esta carpeta.")

    with open(CASOS, encoding="utf-8-sig") as f:
        casos = list(csv.DictReader(f, delimiter=";"))

    aciertos, fallos, pendientes, filas = 0, [], [], []
    sin_cobertura = []
    for caso in casos:
        consulta = caso["consulta"]
        esperado = caso["codigo_esperado"].strip()
        tope = int(caso.get("tope") or 1)

        # Se pide la lista larga de una vez: el orden es el mismo, así que los
        # tres primeros valen para la comprobación de siempre y la lista
        # entera vale para la cobertura. Una búsqueda cuesta 1,5 ms.
        largo = max(tope, 3, motor.N_CANDIDATOS)
        resultados = motor.busca(consulta, tope=largo)
        codigos = [c for _, c, _ in resultados]
        posicion = codigos.index(esperado) + 1 if esperado in codigos else 0

        # COBERTURA: ¿llega el código correcto a manos del modelo?
        # Es lo que la batería de aciertos no ve. Que el buscador deje la
        # ocupación buena en el puesto 19 no es un acierto, pero tampoco es lo
        # mismo que dejarla fuera: en el primer caso el modelo aún puede
        # rescatarla, en el segundo no la ha visto nunca y no hay nada que
        # hacer. Medido el 25/08/2026: 38 de 40.
        if not posicion or posicion > motor.N_CANDIDATOS:
            sin_cobertura.append((consulta, esperado, posicion))

        resultados = resultados[:max(tope, 3)]
        codigos = codigos[:max(tope, 3)]
        posicion = posicion if posicion and posicion <= max(tope, 3) else 0

        resuelto_ia = (caso.get("resuelto_ia") or "").strip().lower() in ("si", "sí")

        if posicion and posicion <= tope:
            aciertos += 1
            marca = "  ok "
        elif resuelto_ia:
            pendientes.append((consulta, esperado, codigos[:3]))
            marca = "pend"
        else:
            fallos.append((consulta, esperado, codigos[:3]))
            marca = "FALLA"

        if detalle or marca != "  ok ":
            print(f"{marca}  {consulta[:52]:54} esperado {esperado}")
            for i, (_, c, d) in enumerate(resultados[:3], 1):
                print(f"          {i}. {c}  {d[:56]}")

        if informe:
            filas.append({
                "consulta": consulta,
                "tope": tope,
                "codigo_esperado": esperado,
                "denominacion_esperada": caso.get("denominacion", ""),
                "estado": {"  ok ": "ok", "pend": "pendiente"}.get(marca, "FALLA"),
                "posicion": posicion or "",
                "obtenido_1": codigos[0] if len(codigos) > 0 else "",
                "denominacion_1": resultados[0][2][:44] if resultados else "",
                "obtenido_2": codigos[1] if len(codigos) > 1 else "",
                "obtenido_3": codigos[2] if len(codigos) > 2 else "",
            })

    total = len(casos)
    print(f"\n{aciertos} de {total} ({100 * aciertos // max(total, 1)} %)")

    if pendientes:
        print(f"\n{len(pendientes)} pendientes (los resuelve la IA en la app, comprobado):")
        for consulta, esperado, salieron in pendientes:
            salio = salieron[0] if salieron else "nada"
            print(f"  {consulta[:56]:58} esperado {esperado}, salió {salio}")

    if fallos:
        print("\nFallan:")
        for consulta, esperado, salieron in fallos:
            salio = salieron[0] if salieron else "nada"
            print(f"  {consulta[:56]:58} esperado {esperado}, salió {salio}")

    cubiertos = total - len(sin_cobertura)
    cobertura = 100 * cubiertos / max(total, 1)
    marca = " OK " if cobertura >= COBERTURA_MINIMA else "MAL"
    print(f"\n[{marca}] cobertura: el código correcto llega al modelo en "
          f"{cubiertos} de {total} ({cobertura:.0f} %, mínimo {COBERTURA_MINIMA} %)")
    print(f"        (entra en la lista de {motor.N_CANDIDATOS} candidatos "
          f"que se le manda a la IA)")
    for consulta, esperado, posicion in sin_cobertura:
        if not posicion:
            # Se mira mucho más abajo solo para estos, que son pocos: saber si
            # la ocupación está en el puesto 19 o en el 65 cambia por completo
            # qué hay que arreglar. En el 19 basta con alargar la lista; en el
            # 65 el problema es la puntuación del buscador.
            hondo = [c for _, c, _ in motor.busca(consulta, tope=300)]
            posicion = hondo.index(esperado) + 1 if esperado in hondo else 0
        donde = f"puesto {posicion}" if posicion else "no la encuentra"
        print(f"  {consulta[:56]:58} esperado {esperado}, {donde}")

    if informe and filas:
        with open(INFORME, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]), delimiter=";")
            w.writeheader()
            w.writerows(filas)
        print(f"\n{INFORME} escrito. {CASOS} no se ha tocado.")

    return 1 if (fallos or cobertura < COBERTURA_MINIMA) else 0


if __name__ == "__main__":
    sys.exit(main())

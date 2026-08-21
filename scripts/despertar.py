"""
Despertador de la aplicación.

Abre la app con un navegador real. Un ping normal devuelve 200 aunque el
proceso siga dormido, por eso hace falta un navegador.

Lo lanza el flujo .github/workflows/mantener-despierta.yml, que le pasa la
dirección en la variable de entorno URL.
"""

import os
import sys

from playwright.sync_api import sync_playwright

SENALES = (
    "input, textarea, button, "
    "[data-testid='stAppViewContainer'], [data-testid='stMain']"
)


def main():
    url = os.environ.get("URL", "").strip()

    if not url:
        print("ERROR: no se ha recibido la variable URL.")
        return 1
    if "TU-APP" in url or "TU-USUARIO" in url:
        print("ERROR: la URL sigue siendo el marcador de posicion.")
        print("Edita mantener-despierta.yml y pon la direccion real.")
        return 1

    print(f"Abriendo {url}")

    with sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page()

        try:
            # domcontentloaded: no se espera a que "termine" de cargar, porque
            # Streamlit mantiene la conexion abierta y ese momento no llega.
            pagina.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"No se ha podido abrir la direccion: {e}")
            navegador.close()
            return 1

        pagina.wait_for_timeout(5000)
        texto = pagina.content().lower()

        # Streamlit Cloud muestra un boton; Hugging Face arranca solo.
        if "zzz" in texto or "get this app back up" in texto:
            print("Estaba dormida. Pulsando el boton de reactivar...")
            try:
                pagina.get_by_role("button").first.click(timeout=15000)
            except Exception as e:
                print(f"No se ha podido pulsar el boton: {e}")
        else:
            print("No aparece la pantalla de hibernacion.")

        try:
            pagina.wait_for_selector(SENALES, timeout=45000)
            print("La aplicacion responde: la interfaz esta cargada.")
            navegador.close()
            return 0
        except Exception:
            pass

        # No se rinde con error: la app puede estar arrancando todavia y un
        # correo de fallo cada doce horas acabaria en la carpeta de ignorados.
        print("AVISO: no se ha confirmado que la interfaz este cargada.")
        print("Puede que siga arrancando. Se reintentara en el proximo turno.")
        navegador.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())

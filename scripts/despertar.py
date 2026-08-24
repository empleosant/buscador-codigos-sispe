import os
import time
from playwright.sync_api import sync_playwright

URL = os.environ.get("URL", "https://buscador-codigos-sispe.streamlit.app")

def despertar():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        print(f"--- Conectando a {URL} ---")
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        time.sleep(8)

        # Selectores del botón de reposo en Streamlit
        selectores_boton = [
            "button:has-text('Yes, get this app back up')",
            "button:has-text('Get this app back up')",
            "button:has-text('Wake up')",
            "button:has-text('Despertar')",
            "button[data-testid='stAppBackUpButton']"
        ]

        boton_encontrado = None
        for selector in selectores_boton:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                boton_encontrado = loc.first
                break

        if boton_encontrado:
            print(">> Pantalla de reposo detectada. Pulsando el botón de despertar...")
            boton_encontrado.click()
            print(">> Botón pulsado. Esperando arranque del contenedor...")
            try:
                page.wait_for_selector("[data-testid='stAppViewContainer'], .stApp", timeout=90000)
                time.sleep(10)
                print(">> ¡Éxito! La aplicación ha despertado y está activa.")
            except Exception:
                print(">> Se pulsó el botón, pero el contenedor sigue iniciando.")
        else:
            if page.locator("[data-testid='stAppViewContainer'], .stApp").count() > 0:
                print(">> La aplicación ya estaba despierta y cargada correctamente.")
            else:
                print(">> Manteniendo conexión activa para evitar reposo...")
                time.sleep(30)
                print(">> Conexión finalizada.")

        browser.close()

if __name__ == "__main__":
    despertar()

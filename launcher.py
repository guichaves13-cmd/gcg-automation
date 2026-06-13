"""
TitlePilot Pro — Launcher para .exe
Abre o servidor e o navegador automaticamente.
"""
import sys
import os
import threading
import webbrowser
import time
import subprocess

# Quando empacotado pelo PyInstaller, _MEIPASS contém os arquivos extraídos
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    # Muda o diretório de trabalho para onde o .exe está
    APP_DIR = os.path.dirname(sys.executable)
    os.chdir(APP_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BASE_DIR

# Adiciona o diretório base ao path para que server.py encontre seus módulos
sys.path.insert(0, BASE_DIR)

PORT = 5050

def open_browser():
    """Aguarda servidor subir e abre o navegador."""
    import requests
    for _ in range(20):
        time.sleep(1)
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/", timeout=2).status_code == 200:
                webbrowser.open(f"http://127.0.0.1:{PORT}/")
                return
        except Exception:
            pass

if __name__ == "__main__":
    print("=" * 50)
    print("  TitlePilot Pro v2.0")
    print("  Iniciando... aguarde")
    print("=" * 50)
    
    # Abre navegador em thread separada
    t = threading.Thread(target=open_browser, daemon=True)
    t.start()
    
    # Importa e roda o servidor Flask
    import server
    print(f"\n  Acesse: http://127.0.0.1:{PORT}")
    print("  Feche esta janela para parar\n")
    server.app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)

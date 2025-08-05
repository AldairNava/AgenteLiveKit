import os
import sys
import json
import signal
import ctypes
import asyncio
from time import sleep
from ctypes import wintypes
from selenium import webdriver
import socket
import pymysql
import subprocess
from pymysql.cursors import DictCursor
from flask import Flask
from selenium.webdriver.common.by import By
from tools import actualizar_actividad,actualizar_stauts

vicidial_automation = None
app = Flask(__name__)

automation_thread = None
automation_loop = None
ORDER_TXT     = r'C:\LivekitAgent\order.txt'
AGENT_PROCESS_FILE   = r'C:\LivekitAgent\agenteProcess.txt'
shutdown_file = r"C:\LivekitAgent\shutdown.txt"
SALIR_FILE = r'C:\LivekitAgent\salir.txt'
HANGUP_FILE = r'C:\LivekitAgent\hangup.txt'
IP = "192.168.50.121"
XPATH_BANDERA = '//*[@id="Tabs"]/table/tbody/tr/td[5]/img'
XPATH_ORDEN   = '//*[@id="last_name"]'

def fetch_agent_credentials():
    # 1) IP real de esta máquina
    ip_local = socket.gethostbyname(socket.gethostname())

    # 2) Conexión a tu BD
    conn = pymysql.connect(
        host='192.168.50.121',
        user='lhernandez',
        password='lhernandez10',
        database='asterisk',
        cursorclass=DictCursor,
        connect_timeout=5,
        charset='utf8mb4'
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extension, username FROM agentesDepuracion WHERE ip = %s LIMIT 1",
                (ip_local,)
            )
            return cur.fetchone()  # {'extension': '6428', 'username': 'BOTo'}
    finally:
        conn.close()

def _cleanup():
    global vicidial_automation
    if vicidial_automation:
        vicidial_automation._stop = True
        try:
            vicidial_automation.cerrar_sesion_y_salir()
        except Exception:
            pass
        vicidial_automation = None

# Windows: manejo de cierre de consola
if os.name == 'nt':
    kernel32 = ctypes.windll.kernel32
    HandlerRoutine = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    def _console_ctrl_handler(ctrl_type):
        if ctrl_type in (0, 1, 2, 5):
            _cleanup()
            return True
        return False
    kernel32.SetConsoleCtrlHandler(HandlerRoutine(_console_ctrl_handler), True)

# POSIX: manejo de señales como Ctrl+C
def _signal_handler(sig, frame):
    print(f"🛑 Señal {sig} recibida. Cerrando todo...")
    _cleanup()
    sys.exit(0)

signal.signal(signal.SIGINT, _signal_handler)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, _signal_handler)

# Clase principal
class VicidialAutomation:
    def __init__(self, config_file='config.json'):
        self.config = self.load_config(config_file)
        self.driver = None
        self._stop = False

    def load_config(self, config_file):
        # 1) Cargo el JSON
        with open(config_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)

        # 2) Obtengo la IP local real del equipo
        ip_local = socket.gethostbyname(socket.gethostname())
        if ip_local =='192.168.51.43':
            ip_local='192.168.49.139'

        # 3) Conecto a la BD y traigo alias+nombre para esta IP
        try:
            conn = pymysql.connect(
                host='192.168.50.121',
                user='lhernandez',
                password='lhernandez10',
                database='asterisk',
                cursorclass=DictCursor,
                connect_timeout=5,
                charset='utf8mb4'
            )
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT alias, nombre FROM agentesDepuracion WHERE ip = %s LIMIT 1",
                    (ip_local,)
                )
                row = cursor.fetchone()
            conn.close()
        except Exception as e:
            print(f"❌ Error al conectar a BD: {e}")
            row = None

        # 4) Si encontré datos, sobrescribo los del JSON
        if row:
            cfg['extension']     = row['alias']
            cfg['username']      = row['nombre']
            print(f"✅ Cargando credenciales: ext={row['alias']} user={row['nombre']}")

            # 5) Validación extra: si el nombre es "BOTo", ajusto también el user_password
            if row['nombre'] == "BOTo":
                cfg['user_password'] = "Cyberbot2024"
                print("🔒 user_password actualizado para BOTo")
        else:
            print(f"⚠️ No existe registro en agentesDepuracion para IP {ip_local}; usando valores de config.json")

        return cfg


    def _login(self, username, password):
        user_field = self.driver.find_element(By.XPATH,
            '//*[@id="vicidial_form"]/center/table/tbody/tr[3]/td[2]/input')
        pass_field = self.driver.find_element(By.XPATH,
            '//*[@id="vicidial_form"]/center/table/tbody/tr[4]/td[2]/input')
        user_field.send_keys(username)
        pass_field.send_keys(password)
        sleep(2)

    def _select_campaign(self, campaign_value):
        try:
            select_campaign = self.driver.find_element(By.XPATH, '//*[@id="VD_campaign"]')
            select_campaign.click()
        except Exception:
            self.driver.quit(); sys.exit(1)
        sleep(2)
        if campaign_value == "3006 - PruebaBot":
            xpath = ('/html/body/form/center/table/tbody/tr[5]/td[2]/font/span/select/option[3]')
        else:
            xpath = ('/html/body/form/center/table/tbody/tr[5]/td[2]/font/span/select/option[2]')
        self.driver.find_element(By.XPATH, xpath).click()
        sleep(2)

    def _close_popup(self):
        try:
            ok = self.driver.find_element(By.XPATH,
                '//*[@id="DeactivateDOlDSessioNSpan"]/table/tbody/tr/td/font/a')
            ok.click()
        except:
            pass

    def cerrar_sesion_y_salir(self):
        try:
            logout_button = self.driver.find_element(By.XPATH,
                '/html/body/form[1]/span[2]/table/tbody/tr/td[2]/font/a')
            logout_button.click()
            sleep(2)
        except:
            pass
        finally:
            if self.driver:
                self.driver.quit()

            status='session cerrada'
            return status

    async def run(self):
        actualizar_stauts(True)
        actualizar_actividad("Encendido")
        login_success = False
        while not self._stop and not login_success:
            try:
                if self.driver:
                    self.driver.quit()
                self.driver = webdriver.Chrome()
                print("🔗 Navegando a la URL de Vicidial...")
                self.driver.get(self.config['url'])
                sleep(2)

                print("🔐 Realizando primer login con extensión...")
                self._login(self.config['extension'], self.config['password'])
                self.driver.find_element(By.XPATH,
                    '//*[@id="vicidial_form"]/center/table/tbody/tr[5]/td/input').click()
                sleep(2)

                print("🔐 Realizando segundo login con extensión + password...")
                self._login(self.config['username'], self.config['user_password'])
                sleep(2)

                print("📋 Seleccionando campaña...")
                self._select_campaign(self.config['campaign_value'])
                sleep(2)

                print("Boton submit")
                self.driver.find_element(By.XPATH,
                    '/html/body/form/center/table/tbody/tr[6]/td/input').click()
                sleep(5)

                print("❎ Verificando si hay popup que cerrar...")
                self._close_popup()
                sleep(5)

                print("🟢 Colocando estado en DISPONIBLE...")
                self.driver.find_element(By.XPATH,
                    '//*[@id="DiaLControl"]/a/img').click()
                login_success = True
                print("✅ Inicio de sesión completado correctamente.")

            except Exception as e:
                print("❌ Error al iniciar sesión:", e)
                if self._stop:
                    return
                print("🔄 Reintentando reinicio del navegador en 5s...")
                sleep(5)
        agent_process = False
        llamada = False

        if login_success:
            self.bg_audio = subprocess.Popen([
                "ffplay",
                "-nodisp",
                "-loop", "0",        # -loop 0 → bucle infinito
                "-af", "volume=0.05",
                r"C:\LivekitAgent\ruido_fondo.m4a"
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            print("✅ Sesión Vicidial lista, arrancando agente…")
            while not self._stop:
                try:
                    img = self.driver.find_element(By.XPATH, XPATH_BANDERA)
                    src = img.get_attribute("src")
                    ip='192.168.50.121'
                except Exception as e:
                    print("❌ Error al obtener la imagen:")
                    if self.agent_proc:
                        self.agent_proc.kill()    
                        print("✅ Agente detenido.")
                    if self.bg_audio:
                        self.bg_audio.kill()
                        print("Audio de fondo detenido")
                    break
                
                if not agent_process:
                    self.agent_proc = subprocess.Popen(
                        [sys.executable, "main.py", "console"],
                        cwd=r"C:\LivekitAgent"
                    )
                    print(f"▶️ Subproceso iniciado")
                    agent_process=True

                if src == f'http://{ip}/agc/images/agc_live_call_DEAD.gif' and llamada:
                    print('📴 Llamada colgada')
                    llamada = False
                    with open(HANGUP_FILE, 'w', encoding='utf-8'):
                        pass


                if os.path.exists(SALIR_FILE):
                    print("🛑 Archivo salida detectado: cerrando subproceso…")
                    if self.agent_proc and self.agent_proc.poll() is None:
                        self.agent_proc.kill()    
                        print("✅ Subproceso detenido.")
                    agent_process = False
                    llamada = False
                    self.agent_proc = None
                    os.remove(SALIR_FILE)
                    if os.path.exists(ORDER_TXT):
                        os.remove(ORDER_TXT)
                    sleep(1)

                if os.path.exists(shutdown_file):
                    print("🔴 Deteniendo automatización archivo shutdown")
                    if self.agent_proc and self.agent_proc.poll() is None:
                        self.agent_proc.terminate()
                        try:
                            self.agent_proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            print("⚠️ El proceso no respondió, forzando kill()")
                            self.agent_proc.kill()
                        print("✅ Subproceso detenido.")
                    actualizar_stauts(False)
                    actualizar_actividad("Apagado")
                    os.remove(shutdown_file)
                    break
                
                if not llamada:
                    if src == f"http://{IP}/agc/images/agc_live_call_ON.gif":
                        print("📞 Llamada activa detectada")

                        # Extrae la orden
                        orden = self.driver.find_element(By.XPATH, XPATH_ORDEN).get_attribute("value")
                        # orden='1-223091190425'
                        with open(ORDER_TXT, 'w', encoding='utf-8') as f:
                            f.write(orden)
                        print(f"➡️ order.txt creado con: {orden}")
                        llamada = True

        session = self.cerrar_sesion_y_salir()
        print("Cerrando vici")
        return session

# -------------------------------------
# Función asíncrona principal
# -------------------------------------
async def iniciar_automatizacion_async(config_file='config.json'):
    global vicidial_automation
    vicidial_automation = VicidialAutomation(config_file)
    await vicidial_automation.run()

def _run_automation(config_file):
    global automation_loop
    # Cada hilo necesita su propio event loop
    automation_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(automation_loop)
    # Esto bloqueará hasta que termine _stop = True
    automation_loop.run_until_complete(iniciar_automatizacion_async(config_file))
    automation_loop.close()


if __name__ == '__main__':
    if os.path.exists(shutdown_file):
        os.remove(shutdown_file)

    print("🚀 Automatización Vicidial iniciada en hilo principal.")
    _run_automation('config.json')
import asyncio
import json
import socket
from datetime import datetime
from pathlib import Path
import os
from time import sleep
import requests
import pytz
import pymysql
from pymysql.cursors import DictCursor

from instrucciones import update_client_context_from_db,client_context, actualizar_status as actualizar_status_instrucciones

# Carga de configuración inicial
def _load_config():
    config_file = r'C:\LivekitAgent\config.json'
    with open(config_file, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    ip_local = socket.gethostbyname(socket.gethostname())
    if ip_local == '192.168.51.43':
        ip_local = '192.168.49.139'
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
        with conn.cursor() as cur:
            cur.execute(
                "SELECT alias, nombre FROM agentesDepuracion WHERE ip = %s LIMIT 1",
                (ip_local,)
            )
            row = cur.fetchone()
        conn.close()
    except Exception as e:
        print(f"❌ Error al conectar a BD: {e}")
        row = None

    if row:
        cfg['extension'] = row['alias']
        cfg['username']  = row['nombre']
        if row['nombre'] == "BOTo":
            cfg['user_password'] = "Cyberbot2024"
        print(f"✅ Datos desde BD: ext={cfg['extension']}, user={cfg['username']}")
    else:
        print(f"⚠️ No encontré registro para IP {ip_local}; uso config.json")
    return cfg

# Variable global para tipificación pendiente
global pending_tipificacion
pending_tipificacion = None

def call_vicidial_tool(function: str, value: str = "1", extra_args: dict = {}) -> dict:
    config = _load_config()
    API_BASE   = "http://192.168.50.121/agc/api.php"
    SOURCE     = "test"
    USER       = config['username']
    PASSWORD   = config['user_password']
    AGENT_USER = config['username']
    """
    Ejecuta un request GET al API de Vicidial en un hilo separado.
    """
    def _sync_call():
        params = {
            "source": SOURCE,
            "user": USER,
            "pass": PASSWORD,
            "agent_user": AGENT_USER,
            "function": function,
            "value": value,
            **extra_args
        }
        try:
            resp = requests.get(API_BASE, params=params)
            print(f"[Vicidial] {function} -> {resp.status_code}")
            return {"result": resp.ok}
        except Exception as e:
            return {"result": False, "error": str(e)}

    return _sync_call()

# def set_pending_tipificacion(tipificacion: str) -> None:
#     global pending_tipificacion
#     pending_tipificacion = tipificacion

def execute_pending_tipificacion(pending_tipificacion) -> None:
    if pending_tipificacion is None:
        print("⚠️ No hay tipificación pendiente para ejecutar")
        return

    # Ejecuta la herramienta correspondiente según la tipificación pendiente
    if pending_tipificacion == 'SCCAVT':
        external_status_SCCAVT()
    elif pending_tipificacion == 'SCCOVT':
        external_status_SCCOVT()
    elif pending_tipificacion == 'SCTSVT':
        external_status_SCTSVT()
    elif pending_tipificacion == 'SCMADI':
        external_status_SCMADI()
    elif pending_tipificacion == 'SCCCUE' or pending_tipificacion =='SIN CONTACTO':
        external_status_SCCCUE()
    elif pending_tipificacion == 'NCBUZ':
        external_status_NCBUZ()
    elif pending_tipificacion == 'SCNUEQ':
        external_status_SCNUEQ()
    elif pending_tipificacion == 'OSCOM':
        external_status_OSCOM()
    else:
        print("⚠️ Tipificación no reconocida:", pending_tipificacion)

    actualizar_status_instrucciones(client_context["NUMERO_ORDEN"],'Completada')
    print(f"✅ Tipificación '{pending_tipificacion}' ejecutada correctamente")

    pending_tipificacion = None

def insertar_base_not_done_via_api() -> bool:
    config = _load_config()
    tz_cdmx = pytz.timezone('America/Mexico_City')
    ahora = datetime.now(tz_cdmx).strftime("%Y-%m-%d %H:%M:%S")
    url = "https://rpabackizzi.azurewebsites.net/DepuracionNotdone/InsertarDepuracionEXTAGENT"
    payload = {
        "lead_id":      client_context.get("lead_id"),
        "Cuenta":       client_context.get("CUENTA"),
        "Compania":     client_context.get("Compania"),
        "NumOrden":     client_context.get("NUMERO_ORDEN"),
        "Tipo":         client_context.get("Tipo"),
        "MotivoOrden":  client_context.get("MotivoOrden"),
        "Source":       "IA AGENT",
        "time_carga":   ahora,
        "Status":       "Registro pendiente",
        "usuario_creo": config['username'],
        "User_registro": socket.gethostbyname(socket.gethostname()),
        "Procesando":   "0",
    }

    def _sync_post():
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    try:
        data = _sync_post()
        print(f"✅ {data.get('message', data)}: {client_context.get('NUMERO_ORDEN')}")
        return True
    except Exception as e:
        print(f"❌ Error al insertar vía API: {e}")
        return False

# === TOOLS ===

def external_hangup() -> dict:
    sleep(6)
    return call_vicidial_tool("external_hangup")

def external_pause(action: str) -> dict:
    sleep(2)
    return call_vicidial_tool("external_pause", action)

def pausa_estatus() -> dict:
    sleep(2)
    return call_vicidial_tool("pause_code", "BREAK")

def transfer_conference(value: str = "1", ingroup: str = "SOME") -> dict:
    return call_vicidial_tool("transfer_conference", value, {"ingroup_choices": ingroup})

def external_status_SCCAVT() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCCAVT")

def external_status_SCTSVT() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCTSVT")

def external_status_SCCOVT() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCCOVT")

def external_status_SCMADI() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCMADI")

def external_status_SCCCUE() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCCCUE")

def external_status_NCBUZ() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "NCBUZ")

def external_status_SCNUEQ() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "SCNUEQ")

def external_status_OSCOM() -> dict:
    sleep(2)
    return call_vicidial_tool("external_status", "OSCOM")

async def external_pause_and_flag_exit(
    cn_type: str,
    cn_motivo: str,
    tipificacion: str
) -> dict:
    actualizar_actividad("Tipificando")

    # Prepara registro
    registro = client_context.copy()
    for k in ("Colonia", "Status", "status"):
        registro.pop(k, None)
    registro.update({
        "status": "Pendiente",
        "cn_type": cn_type,
        "cn_motivo": cn_motivo,
        "tipoficacion": tipificacion
    })

    # Inserción en BD de forma síncrona en hilo
    def _insert_cn():
        conn = pymysql.connect(
            host='192.168.50.121',
            user='lhernandez',
            password='lhernandez10',
            database='asterisk',
            connect_timeout=5,
            charset='utf8mb4',
            cursorclass=DictCursor
        )
        with conn:
            with conn.cursor() as cursor:
                columnas   = ", ".join(registro.keys())
                marcadores = ", ".join(["%s"] * len(registro))
                sql        = f"INSERT INTO CNAgenteDepuracion ({columnas}) VALUES ({marcadores})"
                cursor.execute(sql, list(registro.values()))
            conn.commit()

    try:
        await asyncio.to_thread(_insert_cn)
        print("✅ Registro insertado correctamente en CNAgenteDepuracion")
    except Exception as e:
        print(f"❌ Error al insertar en CNAgenteDepuracion: {e}")
        return {"result": "error", "error": str(e)}
    
    print("Motivo registrado:", cn_motivo)
    actualizar_status_instrucciones(registro["NUMERO_ORDEN"],'Completada')
    external_hangup()
    execute_pending_tipificacion(tipificacion)

    # Crear archivo de salida
    SALIDA_ARCHIVO = r"C:\LivekitAgent\salir.txt"
    os.makedirs(os.path.dirname(SALIDA_ARCHIVO), exist_ok=True)
    Path(SALIDA_ARCHIVO).write_text("salir", encoding="utf-8")

    return {"result": "success"}

def actualizar_actividad(actividad: str) -> None:
    def _update_act():
        ip = socket.gethostbyname(socket.gethostname())
        if ip == '192.168.51.43':
            ip = '192.168.49.139'
        conn = pymysql.connect(
            host='192.168.50.121',
            user='lhernandez',
            password='lhernandez10',
            database='asterisk',
            connect_timeout=5,
            charset='utf8mb4'
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE agentesDepuracion SET actividad = %s WHERE ip = %s",
                    (actividad, ip)
                )
            conn.commit()
            print(f"✅ Actividad actualizada a '{actividad}'")
        except Exception as e:
            print(f"❌ Error al actualizar actividad: {e}")
        finally:
            conn.close()

    _update_act()

def actualizar_stauts(status: str) -> None:
    def _update_stat():
        ip = socket.gethostbyname(socket.gethostname())
        if ip == '192.168.51.43':
            ip = '192.168.49.139'
        conn = pymysql.connect(
            host='192.168.50.121',
            user='lhernandez',
            password='lhernandez10',
            database='asterisk',
            connect_timeout=5,
            charset='utf8mb4'
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE agentesDepuracion SET status = %s WHERE ip = %s",
                    (status, ip)
                )
            conn.commit()
            print(f"✅ status actualizada a '{status}'")
        except Exception as e:
            print(f"❌ Error al status actividad: {e}")
        finally:
            conn.close()

    _update_stat()


# ordenes={
#     '1-224018876625'
# }
# for orden in ordenes:
#     update_client_context_from_db(orden)
#     insertar_base_not_done_via_api()
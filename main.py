import os
import threading
import asyncio
from dotenv import load_dotenv
from typing import Any, Optional
from instrucciones import get_instructions, update_client_context_from_db,client_context
import requests
from time import sleep
from tools import external_pause_and_flag_exit, transfer_conference
from livekit import agents
from livekit.agents import Agent, AgentSession, RoomInputOptions, RunContext, function_tool,UserStateChangedEvent
from livekit.plugins import openai, noise_cancellation
import logging
from livekit.plugins.openai.realtime.realtime_model import InputAudioNoiseReduction,TurnDetection

logging.getLogger("comtypes").setLevel(logging.WARNING)
logging.getLogger("livekit.agents").setLevel(logging.ERROR)



load_dotenv()

# — Rutas de archivos —
ORDER_FILE = r'C:\LivekitAgent\order.txt'
HANGUP_FILE = r'C:\LivekitAgent\hangup.txt'
SHUTDOWN_FILE = r'C:\LivekitAgent\shutdown.txt'
SALIDA_FILE = r'C:\LivekitAgent\salir.txt'
AGENT_PROCESS_FILE = r'C:\LivekitAgent\agenteProcess.txt'
ERROR_CLIENTE=r'C:\LivekitAgent\errorcliente.txt'

# — Parámetros de Vicidial —
IP = "192.168.50.121"
XPATH_BANDERA = '//*[@id="Tabs"]/table/tbody/tr/td[5]/img'
XPATH_ORDEN = '//*[@id="last_name"]'

# — Sesión global para inyectar mensajes —
session_instance: Optional[AgentSession] = None
modo_silencio = "normal"
_timer_followup: asyncio.Handle | None = None

def _send_followup() -> None:
    """Función que envía el segundo mensaje tras 15s, según el modo."""
    if modo_silencio == "normal":
        session_instance.generate_reply(
            user_input=(
                "El cliente no contesta, despídete rápido y amable y ejecuta "
                "external_pause_and_flag_exit con los parámetros de esta llamada."
            )
        )
    else:  # reset_modem
        session_instance.generate_reply(
            user_input=(
                "El cliente no contesta tras el reset: despídete rápido y amable "
                "y ejecuta external_pause_and_flag_exit con los parámetros de esta llamada."
            )
        )

def on_user_state_changed(ev: UserStateChangedEvent) -> None:
    global _timer_followup

    if ev.new_state == "listening":
        if _timer_followup:
            _timer_followup.cancel()
            _timer_followup = None
        return

    if ev.new_state != "away":
        return
    if modo_silencio == "normal":
        session_instance.generate_reply(
            user_input="El cliente no ha hablado. Pregunta si sigue en la llamada."
        )
    else:  # reset_modem
        session_instance.generate_reply(
            user_input=(
                "Mencione al cliente que siguen con él en la llamada y "
                "está ejecutando el reset del módem."
            )
        )

    if not _timer_followup:
        _timer_followup = session_instance._loop.call_later(
            30.0,
            _send_followup
        )


def load_order_txt() -> str:
    if not os.path.exists(ORDER_FILE):
        raise FileNotFoundError(f"No se encontró el archivo: {ORDER_FILE}")
    with open(ORDER_FILE, 'r', encoding='utf-8') as f:
        return f.read().strip()


def watch_shutdown(loop: asyncio.AbstractEventLoop):
    """
    Hilo que revisa cada segundo:
      - shutdown.txt  → cierre forzado
      - salir.txt     → limpieza y cierre
      - hangup.txt    → inyecta user_input al agente
    """
    llamada=False
    while True:
        if os.path.exists(SHUTDOWN_FILE):
            print("🔴 shutdown.txt detectado: cerrando todo…")
            os.remove(SHUTDOWN_FILE)
            os._exit(0)

        if os.path.exists(SALIDA_FILE) and not llamada:
            print("🔴 salir.txt detectado: limpiando y cerrando…")
            if os.path.exists(ORDER_FILE):
                os.remove(ORDER_FILE)
            llamada= False

            print("✅ Sesión cerrada correctamente")

        if os.path.exists(HANGUP_FILE):
            print("📞 hangup.txt detectado: enviando user_input al agente…")
            os.remove(HANGUP_FILE)
            if session_instance:
                loop.call_soon_threadsafe(
                    lambda: session_instance.generate_reply(
                        user_input="Hangup detectado: ejecuta external_pause_and_flag_exit con los parámetros de esta llamada."
                    )
                )

        if os.path.exists(ORDER_FILE) and not llamada:
            llamada = True

        if os.path.exists(ERROR_CLIENTE):
            print("📞 Error al buscar cliente colgando")
            os.remove(ERROR_CLIENTE)
            if session_instance:
                loop.call_soon_threadsafe(
                    lambda: session_instance.generate_reply(
                        user_input="Sin Informacion del cliente: ejecuta external_pause_and_flag_exit con los parámetros de esta llamada."
                    )
                )


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=get_instructions(),
            llm=openai.realtime.RealtimeModel(
                # input_audio_noise_reduction=InputAudioNoiseReduction(type="near_field"),
                voice="coral",
                model="gpt-4o-realtime-preview-2025-06-03",
                turn_detection=None,
            )
        )

    @function_tool(
        name="external_pause_and_flag_exit",
        description="Pausa la llamada, marca salida y registra la CN en la base de datos",
    )
    async def external_pause_and_flag_exit_tool(
        self,
        context: RunContext,
        cn_type: str,
        cn_motivo: str,
        tipificacion: str,
    ) -> dict[str, Any]:
        return await external_pause_and_flag_exit(cn_type, cn_motivo, tipificacion)

    @function_tool(
        name="transfer_conference",
        description="Transfiere la llamada a un grupo o conferencia en Vicidial",
    )
    async def transfer_conference_tool(
        self,
        context: RunContext,
        value: str = "1",
        ingroup: str = "SOME",
    ) -> dict[str, Any]:
        return await transfer_conference(value, ingroup)
    @function_tool(
        name="send_serial",
        description="Reinicia el módem en el back-end y notifica al cliente del resultado",
    )
    async def send_serial(self, context: RunContext) -> dict[str, Any]:
        global modo_silencio
        modo_silencio = "reset_modem"

        async def _serial_task():
            # 1) Hacer la petición HTTP en hilo de bloqueo
            try:
                resp = await asyncio.to_thread(
                    requests.post,
                    "http://localhost:8000/set_serial",
                    json={
                        "cuenta": client_context["CUENTA"],
                        "serial": client_context["NumeroSerieInternet"],
                    },
                )
                if resp.status_code != 200:
                    raise ValueError(f"HTTP {resp.status_code}")
                éxito = resp.json() is True
            except Exception:
                éxito = False


            if éxito:
                texto = (
                    f"Informale al Señor/a {client_context['NOMBRE_CLIENTE']}, "
                    "que el reset ya fue realizado y si te apoya verificando su servicio."
                )
                modo_silencio = "normal"
            else:

                await asyncio.sleep(20)
                texto = (
                    f"Pide disculpas a [el/la Señor/a] {client_context['NOMBRE_CLIENTE']}, "
                    "y menciona que no se logró establecer la conexión con su módem, "
                    "y procede a confirmar la visita técnica."
                )
                modo_silencio = "normal"
                

            await self.session.generate_reply(user_input=texto)

        self.session._loop.create_task(_serial_task())
        return {"result": "ok (send_serial lanzado en background)"}


async def entrypoint(ctx: agents.JobContext):
    global session_instance

    # 1) Lanzar watcher en background
    loop = asyncio.get_running_loop()
    threading.Thread(target=watch_shutdown, args=(loop,), daemon=True).start()

    while True:
        # 3) Iniciar sesión del agente
        if os.path.exists(ORDER_FILE) :
            orden = load_order_txt()
            print(f"➡️ Orden cargada: {orden}")
            update_client_context_from_db(orden)

            assistant = Assistant()
            session_instance = AgentSession(llm=assistant.llm,
                                            user_away_timeout=40.0,)

            await session_instance.start(
                room=ctx.room,
                agent=assistant,
                room_input_options=RoomInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                ),
            )
            session_instance.on("user_state_changed", on_user_state_changed)
            
            try:
                await session_instance.generate_reply(user_input="hola")
            finally:
                assistant.llm.update_options(
                    # input_audio_noise_reduction=InputAudioNoiseReduction(type="near_field"),
                    turn_detection=TurnDetection(type="server_vad"),
                )
                print("✅ Opciones reconfiguradas en caliente")
            
            break


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint)
    )

#!/usr/bin/env python3
"""
Manager Web Server - Panel de Control para Autologin
Servidor FastAPI que gestiona y monitorea api_server.py y autologin.py
"""

import asyncio
import json
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

# Configuración de templates
templates = Jinja2Templates(directory="templates")

# Diccionario global para almacenar los procesos y su estado
processes: Dict[str, Optional[subprocess.Popen]] = {
    "api_server": None,
    "autologin": None
}

# Lista de clientes WebSocket conectados
websocket_clients = []

# Lock para operaciones thread-safe
process_lock = threading.Lock()


class WebSocketManager:
    """Gestor de conexiones WebSocket para logs en tiempo real"""
    
    def __init__(self):
        self.active_connections = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[MANAGER] Cliente WebSocket conectado. Total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"[MANAGER] Cliente WebSocket desconectado. Total: {len(self.active_connections)}")
    
    async def broadcast(self, message: str):
        """Envía un mensaje a todos los clientes conectados"""
        if not self.active_connections:
            return
        
        # Crear lista de tareas para envío concurrente
        tasks = []
        for connection in self.active_connections.copy():
            tasks.append(self._send_safe(connection, message))
        
        # Ejecutar todas las tareas de envío
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_safe(self, websocket: WebSocket, message: str):
        """Envía mensaje de forma segura, manejando desconexiones"""
        try:
            await websocket.send_text(message)
        except Exception:
            # Cliente desconectado, remover de la lista
            self.disconnect(websocket)


# Instancia global del gestor WebSocket
websocket_manager = WebSocketManager()


def read_process_output(process: subprocess.Popen, process_name: str, loop):
    """
    Lee la salida de un proceso línea por línea y la envía via WebSocket
    Se ejecuta en un hilo separado para no bloquear el servidor
    """
    try:
        while process.poll() is None:  # Mientras el proceso esté ejecutándose
            if process.stdout:
                line = process.stdout.readline()
                if line:
                    log_message = f"[{process_name.upper()}] {line.strip()}"
                    print(log_message)  # Log local
                    
                    # Enviar a WebSocket de forma asíncrona usando el loop del servidor
                    try:
                        asyncio.run_coroutine_threadsafe(
                            websocket_manager.broadcast(log_message),
                            loop
                        ).result(timeout=1.0)  # Timeout para evitar bloqueos
                    except Exception as ws_error:
                        print(f"[MANAGER] Error enviando WebSocket: {ws_error}")
            else:
                time.sleep(0.1)  # Pequeña pausa si no hay stdout
        
        # Proceso terminado
        final_message = f"[{process_name.upper()}] Proceso terminado"
        print(final_message)
        try:
            asyncio.run_coroutine_threadsafe(
                websocket_manager.broadcast(final_message),
                loop
            ).result(timeout=1.0)
        except Exception as ws_error:
            print(f"[MANAGER] Error enviando WebSocket final: {ws_error}")
    
    except Exception as e:
        error_message = f"[{process_name.upper()}] Error leyendo salida: {str(e)}"
        print(error_message)
        try:
            asyncio.run_coroutine_threadsafe(
                websocket_manager.broadcast(error_message),
                loop
            ).result(timeout=1.0)
        except Exception as ws_error:
            print(f"[MANAGER] Error enviando WebSocket error: {ws_error}")


def get_process_status(process_name: str) -> str:
    """Obtiene el estado actual de un proceso"""
    with process_lock:
        process = processes.get(process_name)
        if process is None:
            return "stopped"
        
        # Verificar si el proceso sigue ejecutándose
        poll_result = process.poll()
        if poll_result is None:
            return "running"
        else:
            # Proceso terminado, limpiar referencia
            processes[process_name] = None
            return "stopped"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestor del ciclo de vida de la aplicación"""
    # Startup
    print("[MANAGER] Iniciando servidor de gestión...")
    print("[MANAGER] Panel de control disponible en: http://localhost:8000")
    
    # Verificar que los scripts existen
    required_files = ["api_server.py", "autologin.py"]
    for file in required_files:
        if not Path(file).exists():
            print(f"[MANAGER] ADVERTENCIA: {file} no encontrado")
    
    yield
    
    # Shutdown
    print("[MANAGER] Cerrando servidor...")
    
    # Detener todos los procesos activos
    with process_lock:
        for process_name, process in processes.items():
            if process and process.poll() is None:
                print(f"[MANAGER] Deteniendo {process_name}...")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except:
                    process.kill()


# Configuración global con lifespan
app = FastAPI(
    title="Autologin Manager", 
    description="Panel de Control para Autologin",
    lifespan=lifespan
)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Página principal del panel de control"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/start/{process_name}")
async def start_process(process_name: str, workers: Optional[int] = None):
    """Inicia un proceso específico (api_server o autologin)"""
    if process_name not in processes:
        raise HTTPException(status_code=400, detail=f"Proceso desconocido: {process_name}")
    
    with process_lock:
        # Verificar si ya está ejecutándose
        if processes[process_name] is not None and processes[process_name].poll() is None:
            raise HTTPException(status_code=400, detail=f"El proceso {process_name} ya está ejecutándose")
        
        try:
            # Construir comando según el proceso
            if process_name == "api_server":
                command = ["python", "api_server.py"]
            elif process_name == "autologin":
                workers_count = workers if workers and workers > 0 else 10
                command = ["python", "autologin.py", "--workers", str(workers_count)]
            else:
                raise HTTPException(status_code=400, detail=f"Proceso no válido: {process_name}")
            
            # Iniciar el proceso
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # Combinar stderr con stdout
                text=True,
                bufsize=1,  # Buffer línea por línea
                universal_newlines=True
            )
            
            # Guardar referencia al proceso
            processes[process_name] = process
            
            # Obtener el loop de eventos actual
            current_loop = asyncio.get_event_loop()
            
            # Iniciar hilo para leer la salida
            output_thread = threading.Thread(
                target=read_process_output,
                args=(process, process_name, current_loop),
                daemon=True
            )
            output_thread.start()
            
            # Notificar inicio via WebSocket
            start_message = f"[MANAGER] Iniciando {process_name}..."
            await websocket_manager.broadcast(start_message)
            
            return {
                "status": "success",
                "message": f"Proceso {process_name} iniciado exitosamente",
                "pid": process.pid
            }
        
        except Exception as e:
            error_message = f"Error iniciando {process_name}: {str(e)}"
            await websocket_manager.broadcast(f"[MANAGER] {error_message}")
            raise HTTPException(status_code=500, detail=error_message)


@app.post("/stop/{process_name}")
async def stop_process(process_name: str):
    """Detiene un proceso específico"""
    if process_name not in processes:
        raise HTTPException(status_code=400, detail=f"Proceso desconocido: {process_name}")
    
    with process_lock:
        process = processes[process_name]
        
        if process is None or process.poll() is not None:
            return {
                "status": "info",
                "message": f"El proceso {process_name} no está ejecutándose"
            }
        
        try:
            # Terminar el proceso
            process.terminate()
            
            # Esperar un momento para terminación grácil
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Si no termina gracilmente, forzar terminación
                process.kill()
                process.wait()
            
            # Limpiar referencia
            processes[process_name] = None
            
            # Notificar via WebSocket
            stop_message = f"[MANAGER] Proceso {process_name} detenido"
            await websocket_manager.broadcast(stop_message)
            
            return {
                "status": "success",
                "message": f"Proceso {process_name} detenido exitosamente"
            }
        
        except Exception as e:
            error_message = f"Error deteniendo {process_name}: {str(e)}"
            await websocket_manager.broadcast(f"[MANAGER] {error_message}")
            raise HTTPException(status_code=500, detail=error_message)


@app.get("/status")
async def get_status():
    """Obtiene el estado actual de todos los procesos"""
    status_info = {}
    
    for process_name in processes.keys():
        status_info[process_name] = get_process_status(process_name)
    
    # Información adicional del sistema
    data_json_path = Path("data.json")
    data_json_exists = data_json_path.exists()
    data_json_size = data_json_path.stat().st_size if data_json_exists else 0
    
    return {
        "processes": status_info,
        "data_json": {
            "exists": data_json_exists,
            "size_bytes": data_json_size,
            "last_modified": data_json_path.stat().st_mtime if data_json_exists else None
        },
        "timestamp": time.time()
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para logs en tiempo real"""
    await websocket_manager.connect(websocket)
    
    try:
        # Mantener la conexión activa
        while True:
            # Recibir mensajes del cliente (para mantener la conexión)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo del mensaje recibido (opcional)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Enviar ping para mantener conexión activa
                await websocket.send_text("ping")
    
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)
    except Exception as e:
        print(f"[MANAGER] Error en WebSocket: {e}")
        websocket_manager.disconnect(websocket)


if __name__ == "__main__":
    print("=== Panel de Control Autologin ===")
    print("Iniciando servidor en http://localhost:8000")
    print("Presiona Ctrl+C para detener")
    
    # Configuración de Uvicorn
    uvicorn.run(
        "manager:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Desactivar reload para evitar problemas con subprocesos
        log_level="info"
    )
#!/usr/bin/env python3
"""
Panel de Control Simplificado para wplacer-autologin
Versión simplificada sin funciones complejas que causan problemas
"""

import subprocess
import threading
import time
import json
import pathlib
import sys
from typing import Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

# Configuración
app = FastAPI(title="Panel de Control Simplificado")
templates = Jinja2Templates(directory="templates")

# Estado global
processes: Dict[str, Optional[subprocess.Popen]] = {
    "api_server": None,
    "autologin": None,
    "tor": None
}

class SimpleWebSocketManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[MANAGER] WebSocket client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"[MANAGER] WebSocket client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        if not self.active_connections:
            return
        
        # Enviar a todos los clientes conectados
        for connection in self.active_connections[:]:  # Copia para evitar modificaciones durante iteración
            try:
                await connection.send_text(message)
            except:
                # Remover conexiones que fallan
                self.active_connections.remove(connection)

websocket_manager = SimpleWebSocketManager()

def get_process_status(process_name: str) -> str:
    """Obtiene el estado actual de un proceso"""
    if process_name not in processes:
        return "unknown"
    
    process = processes[process_name]
    if process is None:
        return "stopped"
    
    # Verificar si el proceso sigue ejecutándose
    if process.poll() is None:
        return "running"
    else:
        # Proceso terminado, limpiar referencia
        processes[process_name] = None
        return "stopped"

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Página principal del panel de control"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/start/{process_name}")
async def start_process(process_name: str, workers: Optional[int] = None, sequential: Optional[bool] = None):
    """Inicia un proceso específico"""
    if process_name not in processes:
        raise HTTPException(status_code=400, detail=f"Proceso desconocido: {process_name}")
    
    if get_process_status(process_name) == "running":
        raise HTTPException(status_code=400, detail=f"El proceso {process_name} ya está ejecutándose")
    
    try:
        if process_name == "api_server":
            command = ["python", "api_server.py"]
        elif process_name == "autologin":
            command = ["python", "autologin.py"]
            if sequential:
                command.append("--sequential")
            else:
                workers_count = workers if workers and workers > 0 else 10
                command.extend(["--workers", str(workers_count)])
        elif process_name == "tor":
            command = ["tor.exe"]  # Simplificado
        else:
            raise HTTPException(status_code=400, detail=f"Comando no definido para {process_name}")
        
        # Iniciar proceso
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        processes[process_name] = process
        
        # Iniciar thread para leer logs
        log_thread = threading.Thread(
            target=read_process_output,
            args=(process, process_name),
            daemon=True
        )
        log_thread.start()
        
        return {
            "status": "success",
            "message": f"Proceso {process_name} iniciado exitosamente",
            "pid": process.pid,
            "command": " ".join(command)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error iniciando {process_name}: {str(e)}")

@app.post("/stop/{process_name}")
async def stop_process(process_name: str):
    """Detiene un proceso específico"""
    if process_name not in processes:
        raise HTTPException(status_code=400, detail=f"Proceso desconocido: {process_name}")
    
    process = processes[process_name]
    if process is None:
        raise HTTPException(status_code=400, detail=f"El proceso {process_name} no está ejecutándose")
    
    try:
        process.terminate()
        process.wait(timeout=5)
        processes[process_name] = None
        return {"status": "success", "message": f"Proceso {process_name} detenido exitosamente"}
    except subprocess.TimeoutExpired:
        process.kill()
        processes[process_name] = None
        return {"status": "success", "message": f"Proceso {process_name} forzado a detenerse"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deteniendo {process_name}: {str(e)}")

@app.get("/status")
async def get_status():
    """Obtiene el estado actual de todos los procesos"""
    status_info = {}
    
    for process_name in processes.keys():
        status_info[process_name] = get_process_status(process_name)
    
    # Información adicional del sistema
    data_json_path = pathlib.Path("data.json")
    data_json_exists = data_json_path.exists()
    data_json_size = data_json_path.stat().st_size if data_json_exists else 0
    
    return {
        "processes": status_info,
        "data_json": {
            "exists": data_json_exists,
            "size_bytes": data_json_size,
            "last_modified": data_json_path.stat().st_mtime if data_json_exists else 0
        },
        "timestamp": time.time()
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para logs en tiempo real"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Mantener conexión viva
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.post("/test/tor")
async def test_tor_connection():
    """Prueba la conexión TOR"""
    try:
        import requests
        import socks
        
        # Obtener IP real (sin TOR)
        try:
            normal_response = requests.get('https://httpbin.org/ip', timeout=5)
            normal_ip = normal_response.json().get('origin', 'Unknown')
        except:
            normal_ip = 'Unknown'
        
        # Configurar proxy SOCKS para TOR
        session = requests.Session()
        session.proxies = {
            'http': 'socks5://127.0.0.1:9050',
            'https': 'socks5://127.0.0.1:9050'
        }
        
        # Probar conexión TOR
        response = session.get('https://httpbin.org/ip', timeout=10)
        tor_ip = response.json().get('origin', 'Unknown')
        
        return {
            "status": "success",
            "message": f"TOR funcionando correctamente. IP TOR: {tor_ip}",
            "tor_ip": tor_ip,
            "normal_ip": normal_ip
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error conectando via TOR: {str(e)}",
            "suggestion": "Asegúrate de que TOR esté ejecutándose y configurado correctamente"
        }

@app.get("/files/{filename}")
async def get_file_info(filename: str):
    """Obtiene información de un archivo de configuración"""
    allowed_files = ["emails.txt", "proxies.txt", "data.json"]
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail="Archivo no permitido")
    
    file_path = pathlib.Path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    
    stat = file_path.stat()
    return {
        "filename": filename,
        "exists": True,
        "size_bytes": stat.st_size,
        "last_modified": stat.st_mtime,
        "lines": len(file_path.read_text(encoding="utf-8").splitlines())
    }

@app.get("/files/{filename}/content")
async def get_file_content(filename: str, lines: Optional[int] = None):
    """Obtiene el contenido de un archivo (limitado por seguridad)"""
    allowed_files = ["emails.txt", "proxies.txt", "data.json"]
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail="Archivo no permitido")
    
    file_path = pathlib.Path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    
    try:
        content = file_path.read_text(encoding="utf-8")
        if lines and lines > 0:
            content_lines = content.splitlines()
            content = "\n".join(content_lines[:lines])
        
        return {
            "filename": filename,
            "content": content,
            "total_lines": len(content.splitlines())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo archivo: {str(e)}")

@app.post("/files/data.json/clear")
async def clear_progress():
    """Reinicia el progreso eliminando data.json"""
    data_path = pathlib.Path("data.json")
    
    if not data_path.exists():
        raise HTTPException(status_code=404, detail="data.json no encontrado")
    
    try:
        # Crear backup
        import time
        backup_name = f"data_backup_{int(time.time())}.json"
        backup_path = pathlib.Path(backup_name)
        
        # Copiar archivo actual como backup
        backup_path.write_text(data_path.read_text(encoding="utf-8"), encoding="utf-8")
        
        # Eliminar data.json
        data_path.unlink()
        
        return {
            "status": "success",
            "message": f"Progreso reiniciado. Backup creado como {backup_name}",
            "backup_file": backup_name
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reiniciando progreso: {str(e)}")

@app.post("/tor/status")
async def get_tor_status():
    """Obtiene el estado de TOR"""
    try:
        # Verificar si TOR está ejecutándose
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 9050))
        sock.close()
        
        if result == 0:
            # TOR está ejecutándose, obtener IP
            try:
                import requests
                import socks
                
                session = requests.Session()
                session.proxies = {
                    'http': 'socks5://127.0.0.1:9050',
                    'https': 'socks5://127.0.0.1:9050'
                }
                
                response = session.get('https://httpbin.org/ip', timeout=5)
                tor_ip = response.json().get('origin', 'Unknown')
                
                return {
                    "tor_running": True,
                    "tor_ip": tor_ip,
                    "message": f"TOR está ejecutándose. IP: {tor_ip}"
                }
            except:
                return {
                    "tor_running": True,
                    "tor_ip": "Unknown",
                    "message": "TOR está ejecutándose pero no se pudo obtener IP"
                }
        else:
            return {
                "tor_running": False,
                "tor_ip": None,
                "message": "TOR no está ejecutándose"
            }
            
    except Exception as e:
        return {
            "tor_running": False,
            "tor_ip": None,
            "message": f"Error verificando TOR: {str(e)}"
        }

def read_process_output(process: subprocess.Popen, process_name: str):
    """Lee la salida del proceso y la envía via WebSocket"""
    try:
        while True:
            # Leer línea de forma no bloqueante
            line = process.stdout.readline()
            if line:
                log_message = f"[{process_name.upper()}] {line.strip()}"
                print(log_message)  # Log local
                
                # Enviar a WebSocket de forma simple
                try:
                    import asyncio
                    # Crear un nuevo event loop para cada mensaje
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(websocket_manager.broadcast(log_message))
                    loop.close()
                    # Forzar flush del stdout
                    sys.stdout.flush()
                    print(f"[DEBUG] WebSocket enviado: {log_message[:50]}...")  # Debug
                except Exception as e:
                    print(f"[DEBUG] Error WebSocket: {e}")  # Debug
            else:
                # Si no hay línea, verificar si el proceso terminó
                if process.poll() is not None:
                    break
                time.sleep(0.1)  # Pequeño delay para no saturar CPU
    except Exception as e:
        error_message = f"[{process_name.upper()}] Error leyendo salida: {str(e)}"
        print(error_message)

if __name__ == "__main__":
    print("[MANAGER] Iniciando servidor simplificado...")
    uvicorn.run(app, host="0.0.0.0", port=8000)

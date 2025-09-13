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
    "autologin": None,
    "tor": None
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
                        # Enviar mensaje de forma más simple
                        asyncio.run_coroutine_threadsafe(
                            websocket_manager.broadcast(log_message),
                            loop
                        ).result(timeout=0.1)
                    except:
                        # Ignorar errores de WebSocket para evitar spam
                        pass
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
            ).result(timeout=0.1)
        except:
            # Ignorar errores de WebSocket para evitar spam
            pass


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
async def start_process(
    process_name: str, 
    workers: Optional[int] = None,
    sequential: Optional[bool] = None,
    threads: Optional[int] = None,
    pages: Optional[int] = None,
    port: Optional[int] = None,
    headless: Optional[bool] = None,
    proxy_support: Optional[bool] = None
):
    """Inicia un proceso específico con configuraciones avanzadas"""
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
                # TODO: En futuro se pueden agregar parámetros para API server
                # if port: command.extend(["--port", str(port)])
                # if threads: command.extend(["--threads", str(threads)])
                
            elif process_name == "autologin":
                command = ["python", "autologin.py"]
                
                # Agregar configuraciones de autologin
                if sequential:
                    command.append("--sequential")
                else:
                    workers_count = workers if workers and workers > 0 else 10
                    command.extend(["--workers", str(workers_count)])
                    
        elif process_name == "tor":
            # Verificar si TOR ya está ejecutándose (simplificado)
            try:
                import socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(('127.0.0.1', 9050))
                sock.close()
                if result == 0:
                    raise HTTPException(
                        status_code=400,
                        detail="TOR ya está ejecutándose en el puerto 9050. Usa 'Detener TOR' primero."
                    )
            except:
                pass
                
                # Buscar tor.exe en diferentes ubicaciones posibles
                tor_paths = [
                    "tor-bundle/tor.exe",
                    "tor-bundle/Tor/tor.exe", 
                    "C:/Program Files/Tor Browser/Browser/TorBrowser/Tor/tor.exe",
                    "C:/Program Files (x86)/Tor Browser/Browser/TorBrowser/Tor/tor.exe",
                    "tor.exe"  # Si está en PATH
                ]
                
                tor_exe = None
                for path in tor_paths:
                    if Path(path).exists():
                        tor_exe = path
                        break
                
                if not tor_exe:
                    # Intentar descargar TOR automáticamente
                    try:
                        await download_tor_bundle()
                        tor_exe = "tor-bundle/Tor/tor.exe"
                    except Exception as e:
                        raise HTTPException(
                            status_code=400, 
                            detail=f"TOR no encontrado. Descarga Tor Browser o coloca tor.exe en tor-bundle/. Error: {e}"
                        )
                
                command = [tor_exe]
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
            
            config_info = ""
            if process_name == "autologin":
                if sequential:
                    config_info = " (modo secuencial)"
                else:
                    config_info = f" (workers: {workers or 10})"
            elif process_name == "tor":
                config_info = " (SOCKS: 9050, Control: 9051)"
            
            return {
                "status": "success",
                "message": f"Proceso {process_name} iniciado exitosamente{config_info}",
                "pid": process.pid,
                "command": " ".join(command)
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
    
    # Para TOR, verificar si está ejecutándose independientemente del proceso gestionado
    if status_info.get("tor") == "stopped":
        # Verificar si TOR está ejecutándose en los puertos (simplificado)
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('127.0.0.1', 9050))
            sock.close()
            if result == 0:
                status_info["tor"] = "running"
        except:
            pass
    
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


@app.get("/files/{filename}")
async def get_file_info(filename: str):
    """Obtiene información de un archivo de configuración"""
    allowed_files = ["emails.txt", "proxies.txt", "data.json"]
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail="Archivo no permitido")
    
    file_path = Path(filename)
    
    if not file_path.exists():
        return {
            "exists": False,
            "filename": filename,
            "message": f"Archivo {filename} no encontrado"
        }
    
    try:
        stat = file_path.stat()
        
        # Para archivos de texto, contar líneas
        line_count = 0
        if filename.endswith('.txt'):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    line_count = sum(1 for line in f if line.strip() and not line.strip().startswith('#'))
            except:
                line_count = 0
        
        return {
            "exists": True,
            "filename": filename,
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 2),
            "last_modified": stat.st_mtime,
            "line_count": line_count if filename.endswith('.txt') else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo archivo: {str(e)}")


@app.get("/files/{filename}/content")
async def get_file_content(filename: str, lines: Optional[int] = None):
    """Obtiene el contenido de un archivo (limitado por seguridad)"""
    allowed_files = ["emails.txt", "proxies.txt", "data.json"]
    if filename not in allowed_files:
        raise HTTPException(status_code=400, detail="Archivo no permitido")
    
    file_path = Path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Archivo {filename} no encontrado")
    
    try:
        # Limitar lectura para seguridad
        max_lines = min(lines or 50, 100)
        
        with open(file_path, 'r', encoding='utf-8') as f:
            if filename == "data.json":
                # Para JSON, cargar y formatear
                import json
                try:
                    data = json.load(f)
                    return {
                        "filename": filename,
                        "content_type": "json",
                        "content": json.dumps(data, indent=2)[:5000]  # Limitar tamaño
                    }
                except json.JSONDecodeError:
                    content = f.read()[:2000]
            else:
                # Para archivos de texto, leer líneas
                lines_read = []
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    # Ocultar partes sensibles de emails
                    if filename == "emails.txt" and "|" in line:
                        email, _ = line.split("|", 1)
                        lines_read.append(f"{email}|***")
                    else:
                        lines_read.append(line.rstrip())
                content = "\n".join(lines_read)
        
        return {
            "filename": filename,
            "content_type": "text",
            "content": content,
            "lines_shown": min(max_lines, len(lines_read)) if filename != "data.json" else None
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo contenido: {str(e)}")


@app.post("/tor/status")
async def get_tor_status():
    """Obtiene el estado de TOR (si está ejecutándose)"""
    try:
        tor_running = await is_tor_already_running()
        
        if tor_running:
            # Intentar obtener IP a través de TOR
            try:
                import requests
                import socks
                
                # Configurar proxy SOCKS para TOR
                session = requests.Session()
                session.proxies = {
                    'http': 'socks5://127.0.0.1:9050',
                    'https': 'socks5://127.0.0.1:9050'
                }
                
                response = session.get('https://httpbin.org/ip', timeout=10)
                tor_ip = response.json().get('origin', 'unknown')
                
                return {
                    "status": "success",
                    "tor_running": True,
                    "tor_ip": tor_ip,
                    "message": "TOR está ejecutándose y funcionando correctamente"
                }
            except Exception as e:
                return {
                    "status": "warning",
                    "tor_running": True,
                    "tor_ip": "unknown",
                    "message": f"TOR está ejecutándose pero no se pudo verificar la IP: {str(e)}"
                }
        else:
            return {
                "status": "info",
                "tor_running": False,
                "tor_ip": None,
                "message": "TOR no está ejecutándose"
            }
            
    except Exception as e:
        return {
            "status": "error",
            "tor_running": False,
            "message": f"Error verificando estado de TOR: {str(e)}"
        }


@app.post("/test/tor")
async def test_tor_connection():
    """Prueba la conexión TOR"""
    try:
        # Intentar importar y probar TOR usando check_toy.py si existe
        import requests
        
        # Probar conexión directa
        try:
            normal_response = requests.get("https://httpbin.org/ip", timeout=10)
            normal_ip = normal_response.json().get('origin', 'unknown')
        except:
            normal_ip = "unknown"
        
        # Probar conexión via TOR
        try:
            tor_proxies = {
                'http': 'socks5://127.0.0.1:9050',
                'https': 'socks5://127.0.0.1:9050'
            }
            tor_response = requests.get("https://httpbin.org/ip", proxies=tor_proxies, timeout=15)
            tor_ip = tor_response.json().get('origin', 'unknown')
            
            tor_working = tor_ip != normal_ip and tor_ip != "unknown"
            
            return {
                "status": "success" if tor_working else "warning",
                "tor_working": tor_working,
                "normal_ip": normal_ip,
                "tor_ip": tor_ip,
                "message": "TOR funciona correctamente" if tor_working else "TOR no está funcionando correctamente"
            }
        except Exception as tor_error:
            error_msg = str(tor_error)
            # Detectar error específico de conexión rechazada
            if "10061" in error_msg or "refused" in error_msg.lower():
                helpful_message = "TOR no está ejecutándose. Instala y ejecuta Tor Browser o tor.exe primero."
            else:
                helpful_message = f"Error conectando via TOR: {error_msg}"
            
            return {
                "status": "error",
                "tor_working": False,
                "normal_ip": normal_ip,
                "tor_ip": "error",
                "message": helpful_message,
                "suggestion": "Descarga Tor Browser desde https://www.torproject.org/download/ y ejecutalo"
            }
    
    except Exception as e:
        return {
            "status": "error", 
            "tor_working": False,
            "message": f"Error probando TOR: {str(e)}"
        }


async def is_tor_already_running() -> bool:
    """Verifica si TOR ya está ejecutándose en los puertos 9050/9051"""
    import socket
    
    try:
        # Verificar puerto SOCKS 9050
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', 9050))
        sock.close()
        
        if result == 0:  # Puerto abierto
            return True
            
        # Verificar puerto Control 9051
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', 9051))
        sock.close()
        
        return result == 0
        
    except Exception:
        return False


async def download_tor_bundle():
    """Descarga automáticamente Tor Expert Bundle"""
    import zipfile
    import requests
    
    try:
        # URL del Tor Expert Bundle para Windows
        tor_url = "https://archive.torproject.org/tor-package-archive/torbrowser/13.5.6/tor-expert-bundle-windows-x86_64-13.5.6.tar.gz"
        
        bundle_path = Path("tor-bundle")
        bundle_path.mkdir(exist_ok=True)
        
        tar_file = bundle_path / "tor-bundle.tar.gz"
        
        # Descargar el archivo
        print("[MANAGER] Descargando Tor Expert Bundle...")
        response = requests.get(tor_url, stream=True)
        response.raise_for_status()
        
        with open(tar_file, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        # Extraer el archivo usando tarfile
        import tarfile
        with tarfile.open(tar_file, 'r:gz') as tar:
            tar.extractall(bundle_path)
        
        # Limpiar archivo temporal
        tar_file.unlink()
        
        print("[MANAGER] Tor Expert Bundle descargado y extraído exitosamente")
        return True
        
    except Exception as e:
        print(f"[MANAGER] Error descargando TOR: {e}")
        return False


@app.post("/download/tor")
async def download_tor():
    """Descarga Tor Expert Bundle automáticamente"""
    try:
        success = await download_tor_bundle()
        if success:
            await websocket_manager.broadcast("[MANAGER] ✅ Tor Expert Bundle descargado exitosamente")
            return {
                "status": "success",
                "message": "Tor Expert Bundle descargado y listo para usar"
            }
        else:
            return {
                "status": "error", 
                "message": "Error descargando Tor Expert Bundle"
            }
    except Exception as e:
        await websocket_manager.broadcast(f"[MANAGER] ❌ Error descargando TOR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error descargando TOR: {str(e)}")


@app.post("/files/data.json/clear")
async def clear_progress():
    """Reinicia el progreso eliminando data.json"""
    data_path = Path("data.json")
    
    try:
        if data_path.exists():
            # Hacer backup antes de eliminar
            backup_path = Path(f"data_backup_{int(time.time())}.json")
            data_path.rename(backup_path)
            
            await websocket_manager.broadcast(f"[MANAGER] Progreso reiniciado. Backup creado: {backup_path.name}")
            
            return {
                "status": "success",
                "message": f"Progreso reiniciado. Backup creado como {backup_path.name}"
            }
        else:
            return {
                "status": "info",
                "message": "No hay progreso que reiniciar (data.json no existe)"
            }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reiniciando progreso: {str(e)}")


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
# autologin + api_server — README

## 🚀 Quick Start

1. **Clone the repository**
2. **Install dependencies**: `pip install -r requirements.txt && python -m playwright install`
3. **Setup your files** (IMPORTANT - rename the example files):
   ```bash
   cp emails.example.txt emails.txt
   cp proxies.example.txt proxies.txt
   cp data.example.json data.json
   ```
4. **Add your data** to `emails.txt` and `proxies.txt`
5. **Start the API**: `python api_server.py`
6. **Run autologin**: `python autologin.py`
7. **Or use the web panel**: `python manager.py` → http://localhost:8000

## Files
- `autologin.py` — main script.
- `api_server.py` — small local API that returns a token.
- `emails.txt` — account list. One per line: `email|password`.
- `proxies.txt` — proxies list. One per line: `host:port`.
- `data.json` — progress file. Created automatically.

## Requirements
- Python 3.10+
- Pip packages from `requirements.txt`
- Playwright browsers installed: `python -m playwright install`
- Optional: Tor running locally if you want to route traffic through it

## Install
```bash
pip install -r requirements.txt
python -m playwright install
```

## Start the local API
Open a terminal and run:
```bash
python api_server.py
```
Default address: `http://localhost:8080`

### API endpoints (for your own testing only)
- `GET /turnstile?url=...&sitekey=...` → returns a `task_id`
- `GET /result?id=<task_id>` → returns the token status or value

## Prepare input files
**IMPORTANT**: Copy the example files and rename them (remove `.example`):

```bash
# Copy example files and rename them
cp emails.example.txt emails.txt
cp proxies.example.txt proxies.txt
cp data.example.json data.json
```

**⚠️ The files with `.example` in the name are just templates - you need to rename them to work!**

**emails.txt** (copy from emails.example.txt)
```
test1@example.com|example-pass-1
test2@example.com|example-pass-2
```

**proxies.txt** (copy from proxies.example.txt)
```
127.0.0.1:3128
203.0.113.10:8080
```

**data.json** (copy from data.example.json)
- This file will be created automatically with your account data
- Keep this file private as it contains sensitive information

## Configure (OPTIONAL)
Open `autologin.py` and adjust:
- `POST_URL` — where to send the session value (default: `http://127.0.0.1:80/user`).
- Proxy and (optional) Tor settings if you use them.
- Any site‑specific selectors you added for your own test site.

## Run

### Concurrent Processing (Recommended - Faster)
```bash
# Run with 5 concurrent workers (default)
python autologin.py

# Run with 10 concurrent workers
python autologin.py --workers 10

# Run with custom number of workers
python autologin.py -w 3
```

### Sequential Processing (Original behavior)
```bash
python autologin.py --sequential
```

## What happens
- The script reads `emails.txt` and `proxies.txt`.
- It asks the local API for a token.
- It launches a browser and attempts a login on your **own** test site.
- It looks for a session cookie or value you configured.
- It posts that value to `POST_URL` and writes results to `data.json`.

## Outputs
- `data.json` keeps run status and results.
- Your local receiver at `POST_URL` gets a small JSON payload with the session value you configured.

## Troubleshooting
- **API not reachable**: Check `api_server.py` is running on port 8080.
- **Timeouts**: Verify your test URL and `sitekey` are correct for your own setup.
- **Proxy errors**: Make sure entries in `proxies.txt` are valid and live.

## Concurrent Processing Features

### Performance Improvements
- **5-10x faster processing**: Multiple accounts processed simultaneously
- **Thread-safe operations**: Safe concurrent access to shared resources
- **Automatic proxy distribution**: Each thread uses different proxies to avoid conflicts
- **Progress tracking**: Real-time progress updates during concurrent execution

### How It Works
- Each worker thread processes accounts independently
- Proxy pool is shared safely between threads using locks
- State file (`data.json`) is updated atomically to prevent corruption
- TOR circuits are refreshed per thread to maintain anonymity
- Each thread gets its own browser instance to avoid conflicts

### Recommended Settings
- **5 workers**: Good balance for most setups
- **10 workers**: For faster processing if you have many proxies and good hardware
- **3 workers**: Conservative setting for limited resources

## Panel de Control Web (NUEVO)

### Manager.py - Control desde el Navegador
Ahora puedes controlar todo el sistema desde una interfaz web moderna:

```bash
python manager.py
```

Abre tu navegador en: `http://localhost:8000`

### Funcionalidades del Panel Web
- **Control Visual**: Inicia y detiene procesos con botones
- **Logs en Tiempo Real**: Monitorea la salida de ambos scripts simultáneamente
- **Estado en Vivo**: Indicadores visuales del estado de cada proceso
- **Configuración de Workers**: Ajusta el número de workers para autologin
- **Interfaz Responsiva**: Funciona en desktop y móvil

### Estructura de Archivos Actualizada
```
/
├── manager.py          (NUEVO - Panel de control web)
├── api_server.py       (Servidor de captchas)
├── autologin.py        (Procesador de cuentas)
├── requirements.txt    (Actualizado con FastAPI, Jinja2)
└── templates/
    └── index.html      (NUEVO - Interfaz web)
```

## Security Notes
- **Keep sensitive files private**: `data.json`, `emails.txt`, `proxies.txt` contain sensitive information
- **Use example files**: Copy from `*.example.*` files and add your own data
- **Git ignore**: Sensitive files are automatically ignored by `.gitignore`
- **Never commit**: Real account data, proxies, or processing results to version control

## Performance Notes
- Concurrent processing requires sufficient proxies (at least as many as workers)
- Monitor system resources when using high worker counts
- **NEW**: Use the web panel (`manager.py`) for centralized control and real-time monitoring

from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PWTimeout
import asyncio
import concurrent.futures
from browserforge.fingerprints import Screen
from stem import Signal
from stem.control import Controller
import time, sys, pathlib, requests, os, json, itertools
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

CONSENT_BTN_XPATH = '/html/body/div[2]/div[1]/div[2]/c-wiz/main/div[3]/div/div/div[2]/div/div/button'
STATE_FILE = "data.json"
EMAILS_FILE = "emails.txt"
PROXIES_FILE = "proxies.txt"
POST_URL = "http://127.0.0.1:80/user"
CTRL_HOST, CTRL_PORT = "127.0.0.1", 9051
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 9050

# Thread synchronization
state_lock = threading.Lock()
proxy_lock = threading.Lock()

# ===================== PROXY HANDLING =====================
def load_proxies(path=PROXIES_FILE):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"[ERROR] Proxies file not found: {path}")
        sys.exit(1)
    proxies = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(":")
        if len(parts) == 2:   # host:port only
            proxies.append(f"http://{parts[0]}:{parts[1]}")  
        elif len(parts) == 4:  # host:port:user:pass (DECODO format)
            host, port, user, password = parts
            proxies.append(f"http://{user}:{password}@{host}:{port}")
        else:
            print(f"[WARN] Skipping invalid proxy line: {ln}")
    if not proxies:
        print("[ERROR] no valid proxies found")
        sys.exit(1)
    print(f"[INFO] Loaded {len(proxies)} proxies")
    return itertools.cycle(proxies)  # infinite iterator

proxy_pool = load_proxies()

def get_next_proxy():
    """Thread-safe proxy retrieval"""
    with proxy_lock:
        return next(proxy_pool)

# ===================== GOOGLE LOGIN HELPERS =====================
def exists_sync(fr_or_pg, sel, t=500):
    try:
        fr_or_pg.locator(sel).first.wait_for(state="visible", timeout=t)
        return True
    except PWTimeout:
        return False

def find_login_frame_sync(pg, _type, timeout_s=30):
    t0 = time.time()
    err = False
    while time.time() - t0 < timeout_s and not err:
        for fr in pg.frames:
            try:
                if "https://accounts.google.com/v3/signin/challenge/recaptcha" in str(fr).lower():
                    err = True
                    break
                if fr.locator(_type).count():
                    return fr
            except Exception:
                pass
        time.sleep(0.25)
    if err:
        raise TimeoutError("Captcha shown")
    raise TimeoutError("Google login frame not found")

def click_consent_xpath_sync(gpage, timeout_s=20):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            btn = gpage.locator(f'xpath={CONSENT_BTN_XPATH}').first
            btn.wait_for(state="visible", timeout=800)
            btn.click()
            return True
        except Exception:
            pass
        time.sleep(0.25)
    return False

def poll_cookie_any_context_sync(browser, name="j", timeout_s=180):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            for ctx in browser.contexts:
                for c in ctx.cookies():
                    if c.get("name") == name:
                        return c
        except Exception:
            pass
        time.sleep(0.05)
    return None

# ===================== TURNSTILE SOLVER =====================
def get_solved_token(api_url="http://localhost:8080/turnstile", target_url="https://backend.wplace.live", sitekey="0x4AAAAAABpHqZ-6i7uL0nmG"):
    proxy = get_next_proxy()
    try:
        r = requests.get(api_url, params={"url": target_url, "sitekey": sitekey, "proxy": proxy}, timeout=20)
        if r.status_code != 202:
            raise RuntimeError(f"Bad status {r.status_code}: {r.text}")
        task_id = r.json().get("task_id")
        if not task_id:
            raise RuntimeError("No task_id returned")
        # poll result
        for _ in range(60):
            time.sleep(2)
            res = requests.get(f"http://localhost:8080/result", params={"id": task_id}, timeout=20).json()
            if res.get("status") == "success":
                return res.get("value")
            elif res.get("status") == "error":
                raise RuntimeError(f"Solver error: {res.get('value')}")
        raise RuntimeError("Captcha solving timed out")
    except Exception as e:
        raise RuntimeError(f"Captcha solver failed: {e}")

# ===================== LOGIN =====================
def login_once_sync(email, password):
    # Step 1: Solve captcha and get token (uses proxies from proxies.txt)
    token = get_solved_token()
    backend_url = f"https://backend.wplace.live/auth/google?token={token}"

    # Step 2: Follow redirect via same HTTP proxy (requests)
    proxy_http = get_next_proxy()
    proxies = {"http": proxy_http, "https": proxy_http}  # requests style
    try:
        r = requests.get(backend_url, allow_redirects=True, proxies=proxies, timeout=15)
        google_login_url = r.url
    except Exception as e:
        raise RuntimeError(f"Failed to get Google login URL via proxy {proxy_http}: {e}")

    # Step 3: Open Google login in Camoufox using TOR (SOCKS5)
    tor_proxy = {"server": f"socks5://{SOCKS_HOST}:{SOCKS_PORT}"}
    custom_fonts = ["Arial", "Helvetica", "Times New Roman"]
    with Camoufox(headless=True, humanize=True, block_images=True, disable_coop=True, screen=Screen(max_width=200, max_height=400), proxy=tor_proxy, fonts=custom_fonts, os=["windows", "macos", "linux"], geoip=True, i_know_what_im_doing=True) as browser:
        page = browser.new_page()
        page.set_default_timeout(60000)
        page.goto(google_login_url, wait_until="domcontentloaded")

        # Step 4: Handle Google login frame
        fr = find_login_frame_sync(page, 'input[type="email"]', timeout_s=30)
        fr.fill('input[type="email"]', email)
        fr.locator('#identifierNext').click()
        t0 = time.time()
        while time.time() - t0 < 3:
            fr = find_login_frame_sync(page, 'input[type="password"]', timeout_s=30)
        fr.fill('input[type="password"]', password)
        fr.locator('#passwordNext').click()

        # Step 5: Click consent if needed
        click_consent_xpath_sync(page, timeout_s=20)

        # Step 6: Return "j" cookie
        return poll_cookie_any_context_sync(browser, name="j", timeout_s=180)

async def login_once(email, password):
    """Async wrapper for login_once_sync to run in thread pool"""
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(executor, login_once_sync, email, password)

# ===================== EMAIL & STATE HANDLING =====================
def parse_emails_file(path=EMAILS_FILE):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"[ERROR] File not found: {path}"); sys.exit(1)
    pairs = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "|" not in s:
            continue
        email, password = s.split("|", 1)
        email = email.strip(); password = password.strip()
        if email and password:
            pairs.append((email, password))
    if not pairs:
        print("[ERROR] No valid credentials found"); sys.exit(1)
    return pairs

def load_state():
    if pathlib.Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    pairs = parse_emails_file()
    return {
        "version": 1,
        "config": {"socks_host": SOCKS_HOST, "socks_port": SOCKS_PORT, "ctrl_host": CTRL_HOST, "ctrl_port": CTRL_PORT},
        "cursor": {"next_index": 0},
        "accounts": [{"email": e, "password": p, "status": "pending", "tries": 0, "last_error": "", "result": None} for e, p in pairs],
    }

def save_state(state):
    """Thread-safe state saving"""
    with state_lock:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)

# ===================== TOR HELPERS =====================
def tor_newnym_cookie(host=CTRL_HOST, port=CTRL_PORT):
    with Controller.from_port(address=host, port=port) as c:
        c.authenticate()
        if not c.is_newnym_available():
            time.sleep(c.get_newnym_wait())
        c.signal(Signal.NEWNYM)
# ===================== ACCOUNT PROCESSING =====================
async def process_account(state, idx, thread_id=None):
    """Process a single account - thread-safe version"""
    thread_prefix = f"[T{thread_id}]" if thread_id else ""
    
    # Thread-safe account access and update
    with state_lock:
        acc = state["accounts"][idx]
        acc["tries"] += 1
        state["cursor"]["next_index"] = idx
    
    save_state(state)
    
    try:
        print(f"{thread_prefix} Processing {acc['email']}...")
        time.sleep(1.0)  # Simular tiempo de procesamiento
        print(f"{thread_prefix} Getting proxy and connecting...")
        time.sleep(1.0)  # Simular tiempo de conexión
        c = await login_once(acc["email"], acc["password"])
        if not c:
            raise RuntimeError("cookie_not_found")
        
        payload = {"cookies": {"j": c.get("value", "")}, "expirationDate": 999999999}
        print(f"{thread_prefix} Sending result to server...")
        time.sleep(1.0)  # Simular tiempo de envío
        requests.post(POST_URL, json=payload, timeout=10)
        print(f"{thread_prefix} Result sent successfully!")
        time.sleep(0.5)  # Tiempo final
        
        # Thread-safe result update
        with state_lock:
            acc["status"] = "ok"
            acc["last_error"] = ""
            acc["result"] = {"domain": c.get("domain", ""), "value": c.get("value", "")}
        
        print(f"{thread_prefix} [SUCCESS] {acc['email']} - Token obtenido")
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        
        # Thread-safe error update
        with state_lock:
            acc["status"] = "error" 
            acc["last_error"] = error_msg
        
        print(f"{thread_prefix} [ERROR] {acc['email']} | {error_msg}")
        
    finally:
        save_state(state)
        # Each thread gets its own TOR circuit
        try:
            tor_newnym_cookie()
        except Exception as e:
            print(f"{thread_prefix} [WARN] TOR newnym failed: {e}")
        time.sleep(2)  # Reduced sleep for faster processing

def indices_by_status(state, statuses: set[str]) -> list[int]:
    """Return account indices whose status is in `statuses`.
       Accept both 'error' and 'errored'."""
    out = []
    for i, a in enumerate(state["accounts"]):
        st = (a.get("status") or "pending").lower()
        if st in statuses:
            out.append(i)
    return out

# ===================== MAIN =====================
async def main_concurrent(max_workers=5):
    """Main function with concurrent processing"""
    state = load_state()

    # Queue: all error/errored first, then all pending.
    q = indices_by_status(state, {"error", "errored"}) + indices_by_status(state, {"pending"})

    seen = set()
    ordered = [i for i in q if not (i in seen or seen.add(i))]

    if not ordered:
        print("[DONE] Nothing to process")
        return

    print(f"[INFO] Processing {len(ordered)} accounts with {max_workers} concurrent workers")
    print(f"[INFO] Progress: 0/{len(ordered)} (0%) - Starting...")
    
    # Process accounts concurrently using asyncio
    tasks = []
    for thread_id, idx in enumerate(ordered):
        task = asyncio.create_task(process_account(state, idx, thread_id + 1))
        tasks.append((task, idx))
    
    # Wait for completion and handle results
    completed = 0
    for task, idx in tasks:
        await task
        completed += 1
        progress_percent = (completed / len(ordered)) * 100
        print(f"[INFO] Progress: {completed}/{len(ordered)} ({progress_percent:.1f}%) - Account {idx} completed")
        print(f"[INFO] Progress: {completed}/{len(ordered)} completed")
        # Add small delay to allow WebSocket to send logs progressively
        await asyncio.sleep(0.1)

    # Final state save and cursor update
    with state_lock:
        state["cursor"]["next_index"] = len(state["accounts"])
    save_state(state)
    print("[DONE] All accounts processed")

async def main():
    """Sequential processing (original behavior)"""
    state = load_state()

    # Queue: all error/errored first, then all pending.
    q = indices_by_status(state, {"error", "errored"}) + indices_by_status(state, {"pending"})

    seen = set()
    ordered = [i for i in q if not (i in seen or seen.add(i))]

    if not ordered:
        print("[DONE] Nothing to process")
        return

    for idx in ordered:
        await process_account(state, idx)

    # Final state save and cursor update
    state["cursor"]["next_index"] = len(state["accounts"])
    save_state(state)
    print("[DONE] Sequential processing completed")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Auto-login script with concurrent processing")
    parser.add_argument("--workers", "-w", type=int, default=5, 
                       help="Number of concurrent workers (default: 5)")
    parser.add_argument("--sequential", action="store_true", 
                       help="Use sequential processing instead of concurrent")
    
    args = parser.parse_args()
    
    try:
        if args.sequential:
            print("[INFO] Using sequential processing")
            asyncio.run(main())
        else:
            print(f"[INFO] Using concurrent processing with {args.workers} workers")
            asyncio.run(main_concurrent(max_workers=args.workers))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")

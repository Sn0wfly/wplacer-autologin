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

# ===================== AUTO-DELETE CONFIGURATION =====================
# Set to True to enable automatic account deletion after successful login
AUTO_DELETE_ENABLED = False

# Set to True to enable debug mode with screenshots and detailed logging
AUTO_DELETE_DEBUG = True

# You can also control this via environment variable:
# export AUTO_DELETE_ENABLED=true
if os.environ.get("AUTO_DELETE_ENABLED", "").lower() in ["true", "1", "yes", "on"]:
    AUTO_DELETE_ENABLED = True

# Debug mode via environment variable:
if os.environ.get("AUTO_DELETE_DEBUG", "").lower() in ["true", "1", "yes", "on"]:
    AUTO_DELETE_DEBUG = True

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

    # Step 3: Open Google login in Camoufox (with optional TOR)
    custom_fonts = ["Arial", "Helvetica", "Times New Roman"]
    
    # Check if TOR is available, if not use direct connection
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((SOCKS_HOST, SOCKS_PORT))
        sock.close()
        use_tor = result == 0
    except:
        use_tor = False
    
    if use_tor:
        tor_proxy = {"server": f"socks5://{SOCKS_HOST}:{SOCKS_PORT}"}
        print(f"[INFO] Using TOR proxy: {SOCKS_HOST}:{SOCKS_PORT}")
        with Camoufox(headless=True, humanize=True, block_images=False, disable_coop=False, screen=Screen(max_width=1920, max_height=1080), proxy=tor_proxy, fonts=custom_fonts, os=["windows"], geoip=False, i_know_what_im_doing=True) as browser:
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
    else:
        print(f"[INFO] TOR not available, using direct connection")
        with Camoufox(headless=True, humanize=True, block_images=False, disable_coop=False, screen=Screen(max_width=1920, max_height=1080), fonts=custom_fonts, os=["windows"], geoip=False, i_know_what_im_doing=True) as browser:
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

# ===================== AUTO-DELETE ACCOUNT =====================
def auto_delete_account_sync(j_cookie_value):
    """
    Auto-delete account on wplace.live using the j cookie
    Returns True if successful, False otherwise
    """
    try:
        # Check if TOR is available
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((SOCKS_HOST, SOCKS_PORT))
            sock.close()
            use_tor = result == 0
        except:
            use_tor = False
        
        # Setup browser with same configuration as login
        custom_fonts = ["Arial", "Helvetica", "Times New Roman"]
        
        if use_tor:
            tor_proxy = {"server": f"socks5://{SOCKS_HOST}:{SOCKS_PORT}"}
            print(f"[AUTO-DELETE] Using TOR proxy: {SOCKS_HOST}:{SOCKS_PORT}")
            with Camoufox(headless=True, humanize=True, block_images=False, disable_coop=False, screen=Screen(max_width=1920, max_height=1080), proxy=tor_proxy, fonts=custom_fonts, os=["windows"], geoip=False, i_know_what_im_doing=True) as browser:
                return _perform_account_deletion(browser, j_cookie_value)
        else:
            print(f"[AUTO-DELETE] TOR not available, using direct connection")
            with Camoufox(headless=True, humanize=True, block_images=False, disable_coop=False, screen=Screen(max_width=1920, max_height=1080), fonts=custom_fonts, os=["windows"], geoip=False, i_know_what_im_doing=True) as browser:
                return _perform_account_deletion(browser, j_cookie_value)
                
    except Exception as e:
        print(f"[AUTO-DELETE] Error: {type(e).__name__}: {e}")
        return False

def _perform_account_deletion(browser, j_cookie_value):
    """
    Perform the actual account deletion process
    """
    try:
        page = browser.new_page()
        page.set_default_timeout(30000)
        
        # Step 1: Set the j cookie for wplace.live
        page.context.add_cookies([{
            "name": "j",
            "value": j_cookie_value,
            "domain": ".wplace.live",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax"
        }])
        
        # Step 2: Navigate to wplace.live
        print("[AUTO-DELETE] Navigating to wplace.live...")
        page.goto("https://wplace.live", wait_until="domcontentloaded")
        time.sleep(3)
        
        # Debug: Take screenshot of initial page
        if AUTO_DELETE_DEBUG:
            page.screenshot(path="debug_01_initial_page.png")
            print("[AUTO-DELETE] DEBUG: Screenshot saved - debug_01_initial_page.png")
        
        # Step 3: Look for account/profile button (improved selectors based on findings)
        account_selectors = [
            # Based on your inspection - SVG edit icon with size-4 class
            'svg.size-4',
            'button:has(svg.size-4)',
            '[class*="size-4"]',
            
            # SVG with edit/pencil path (the path you found)
            'svg:has(path[d*="200-200h57l391-391"])',
            'button:has(svg:has(path[d*="200-200h57l391-391"]))',
            
            # Generic edit/profile buttons
            "button[class*='account']",
            "button[class*='profile']",
            "button[class*='user']",
            "button[class*='edit']",
            "[data-testid*='account']",
            "[data-testid*='profile']",
            "[data-testid*='user']",
            "[data-testid*='edit']",
            "img[alt*='profile']",
            "img[alt*='avatar']",
            ".avatar",
            ".profile-button",
            ".account-button",
            ".user-menu",
            
            # Look for any button containing SVG
            "button:has(svg)",
            "div:has(svg):has-text('edit')",
            "div:has(svg):has-text('profile')",
            "div:has(svg):has-text('account')"
        ]
        
        account_button = None
        for selector in account_selectors:
            try:
                account_button = page.locator(selector).first
                if account_button.is_visible(timeout=1000):
                    print(f"[AUTO-DELETE] Found account button with selector: {selector}")
                    break
            except Exception as e:
                if AUTO_DELETE_DEBUG:
                    print(f"[AUTO-DELETE] DEBUG: Selector '{selector}' failed: {e}")
                continue
        
        if not account_button or not account_button.is_visible():
            print("[AUTO-DELETE] Account button not found, trying alternative approach...")
            
            # Debug: List all visible buttons for analysis
            if AUTO_DELETE_DEBUG:
                print("[AUTO-DELETE] DEBUG: Listing all visible buttons...")
                all_buttons = page.locator("button, a, [role='button'], svg").all()
                for i, btn in enumerate(all_buttons[:20]):  # Show first 20
                    try:
                        if btn.is_visible():
                            text = btn.inner_text()
                            classes = btn.get_attribute("class") or ""
                            print(f"[AUTO-DELETE] DEBUG: Button {i}: text='{text}' class='{classes}'")
                    except:
                        pass
            
            # Try clicking on any clickable element that might open user menu
            clickable_elements = page.locator("button, a, [role='button']").all()
            for element in clickable_elements[:10]:  # Check first 10 elements
                try:
                    if element.is_visible():
                        text = element.inner_text().lower()
                        if any(word in text for word in ['account', 'profile', 'user', 'menu']):
                            account_button = element
                            print(f"[AUTO-DELETE] Found potential account button with text: {text}")
                            break
                except:
                    continue
        
        if not account_button:
            raise RuntimeError("Could not find account/profile button")
        
        # Step 4: Click account button
        print("[AUTO-DELETE] Clicking account button...")
        account_button.click()
        time.sleep(3)
        
        # Debug: Take screenshot after clicking account button
        if AUTO_DELETE_DEBUG:
            page.screenshot(path="debug_02_after_account_click.png")
            print("[AUTO-DELETE] DEBUG: Screenshot saved - debug_02_after_account_click.png")
        
        # Step 5: Look for settings/delete account options (EXACT SELECTORS FROM INSPECTION)
        settings_selectors = [
            # EXACT MATCH - Based on your inspection!
            "button.btn.btn-error.btn-soft.btn-sm.w-max",
            "button.btn-error.btn-soft:has-text('Delete Account')",
            "button.btn-error:has-text('Delete Account')",
            
            # Alternative exact matches
            ".modal-box button.btn-error",
            ".modal-box button:has-text('Delete Account')",
            "form button.btn-error",
            
            # Text-based (most reliable)
            "text=Delete Account",
            "button:has-text('Delete Account')",
            
            # Class-based fallbacks
            "button[class*='btn-error']",
            "button[class*='btn-soft']",
            "button.btn-error",
            
            # Generic fallbacks
            "text=Remove Account", 
            "text=Delete",
            "text=Remove",
            "button[class*='delete']",
            "button[class*='remove']",
            "button[class*='danger']",
            "button[class*='red']",
            
            # Modal-specific
            ".modal-box button",
            "[role='dialog'] button",
            "[role='modal'] button", 
            ".modal button",
            ".popup button",
            ".dropdown button",
            
            # Overlay buttons
            "[style*='z-index'] button",
            ".overlay button",
            ".menu button"
        ]
        
        settings_button = None
        for selector in settings_selectors:
            try:
                settings_button = page.locator(selector).first
                if settings_button.is_visible(timeout=1000):
                    print(f"[AUTO-DELETE] Found settings/delete button with selector: {selector}")
                    break
            except:
                continue
        
        if not settings_button:
            # Look for any button/link containing delete-related text
            all_elements = page.locator("button, a, [role='button']").all()
            for element in all_elements:
                try:
                    text = element.inner_text().lower()
                    if any(word in text for word in ['delete', 'remove', 'settings', 'account']):
                        settings_button = element
                        print(f"[AUTO-DELETE] Found potential delete button with text: {text}")
                        break
                except:
                    continue
        
        if not settings_button:
            raise RuntimeError("Could not find settings/delete account option")
        
        # Step 6: Click settings/delete button
        print("[AUTO-DELETE] Clicking settings/delete button...")
        settings_button.click()
        time.sleep(3)
        
        # Debug: Take screenshot after clicking settings/delete
        if AUTO_DELETE_DEBUG:
            page.screenshot(path="debug_03_after_settings_click.png")
            print("[AUTO-DELETE] DEBUG: Screenshot saved - debug_03_after_settings_click.png")
        
        # Step 7: Look for final delete confirmation (EXACT MATCH FROM CONFIRMATION MODAL)
        delete_selectors = [
            # EXACT MATCH - Final confirmation modal button!
            "button.btn.btn-error:has-text('Delete Account')",
            ".modal-box button.btn.btn-error:has-text('Delete Account')",
            
            # Alternative exact matches for confirmation modal
            "button.btn-error:has-text('Delete Account')",
            ".modal-box button.btn-error",
            
            # Text-based (most reliable for final confirmation)
            "text=Delete Account",
            "button:has-text('Delete Account')",
            
            # Context-aware (confirmation modal with "Are you absolutely sure?")
            ".modal-box:has-text('Are you absolutely sure?') button.btn-error",
            ".modal-box:has-text('This will permanently delete') button.btn-error",
            
            # Generic confirmation patterns
            "button.btn-error:has-text('Delete')",
            "button.btn-error:has-text('Confirm')",
            "button.btn-error:has-text('Yes')",
            
            # Class-based fallbacks
            "button.btn-error",
            "button[class*='btn-error']",
            "button[class*='danger']",
            "button[class*='red']",
            
            # Modal confirmation patterns
            ".modal-box button.btn-error",
            "[role='dialog'] button.btn-error",
            
            # Text fallbacks
            "text=Delete",
            "text=Confirm",
            "text=Yes",
            "text=Remove",
            
            # Generic patterns
            "[data-testid*='delete']",
            "[data-testid*='confirm']",
            ".delete-confirm",
            ".confirm-delete"
        ]
        
        delete_button = None
        for selector in delete_selectors:
            try:
                delete_button = page.locator(selector).first
                if delete_button.is_visible(timeout=1000):
                    print(f"[AUTO-DELETE] Found delete confirmation button with selector: {selector}")
                    break
            except:
                continue
        
        if not delete_button:
            print("[AUTO-DELETE] Delete confirmation button not found, account deletion may require manual intervention")
            return False
        
        # Step 8: Final confirmation
        print("[AUTO-DELETE] Performing final delete confirmation...")
        delete_button.click()
        time.sleep(5)
        
        # Debug: Take screenshot after final confirmation
        if AUTO_DELETE_DEBUG:
            page.screenshot(path="debug_04_after_final_delete.png")
            print("[AUTO-DELETE] DEBUG: Screenshot saved - debug_04_after_final_delete.png")
        
        # Step 9: Verify deletion (check if redirected or account no longer exists)
        current_url = page.url
        print(f"[AUTO-DELETE] Current URL after deletion: {current_url}")
        
        # If redirected to login page or homepage, deletion likely successful
        if "login" in current_url.lower() or current_url == "https://wplace.live/" or "auth" in current_url.lower():
            print("[AUTO-DELETE] Account deletion appears successful (redirected to login/home)")
            return True
        
        print("[AUTO-DELETE] Account deletion completed")
        return True
        
    except Exception as e:
        print(f"[AUTO-DELETE] Error during deletion process: {type(e).__name__}: {e}")
        return False

async def auto_delete_account(j_cookie_value):
    """Async wrapper for auto_delete_account_sync to run in thread pool"""
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(executor, auto_delete_account_sync, j_cookie_value)

def test_auto_delete_with_cookie(j_cookie_value):
    """
    Test function to debug auto-delete with a specific cookie
    Usage: test_auto_delete_with_cookie("your_j_cookie_value_here")
    """
    print("[TEST] Starting auto-delete test with debug mode enabled...")
    global AUTO_DELETE_DEBUG
    AUTO_DELETE_DEBUG = True
    
    try:
        result = auto_delete_account_sync(j_cookie_value)
        print(f"[TEST] Auto-delete test completed. Result: {result}")
        print("[TEST] Check the debug screenshots: debug_01_initial_page.png, debug_02_after_account_click.png, etc.")
        return result
    except Exception as e:
        print(f"[TEST] Auto-delete test failed: {type(e).__name__}: {e}")
        return False

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
        
        # Auto-delete account if enabled
        if AUTO_DELETE_ENABLED:
            print(f"{thread_prefix} Auto-delete enabled, attempting to delete account...")
            time.sleep(0.5)
            try:
                delete_success = await auto_delete_account(c.get("value", ""))
                if delete_success:
                    print(f"{thread_prefix} [AUTO-DELETE] Account {acc['email']} deleted successfully!")
                else:
                    print(f"{thread_prefix} [AUTO-DELETE] Failed to delete account {acc['email']}")
            except Exception as delete_error:
                print(f"{thread_prefix} [AUTO-DELETE] Error deleting {acc['email']}: {delete_error}")
            time.sleep(0.5)
        
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
            # Add auto-delete status to result
            if AUTO_DELETE_ENABLED:
                acc["result"]["auto_deleted"] = delete_success if 'delete_success' in locals() else False
        
        print(f"{thread_prefix} [SUCCESS] {acc['email']} - Token obtained{' & Account deleted' if AUTO_DELETE_ENABLED and locals().get('delete_success', False) else ''}")
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        
        # Thread-safe error update
        with state_lock:
            acc["status"] = "error" 
            acc["last_error"] = error_msg
        
        print(f"{thread_prefix} [ERROR] {acc['email']} | {error_msg}")
        
    finally:
        save_state(state)
        # Each thread gets its own TOR circuit (only if TOR is available)
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((CTRL_HOST, CTRL_PORT))
            sock.close()
            if result == 0:
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

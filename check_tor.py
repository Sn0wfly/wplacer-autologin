# Save this as check_tor.py
import socket
import requests

def check_tor():
    print("🔍 Checking Tor...")
    
    # Check SOCKS port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('127.0.0.1', 9050))
        sock.close()
        socks_ok = result == 0
        print(f"SOCKS Port (9050): {'✅ OK' if socks_ok else '❌ CLOSED'}")
    except:
        socks_ok = False
        print("SOCKS Port (9050): ❌ ERROR")
    
    # Check Control port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('127.0.0.1', 9051))
        sock.close()
        control_ok = result == 0
        print(f"Control Port (9051): {'✅ OK' if control_ok else '❌ CLOSED'}")
    except:
        control_ok = False
        print("Control Port (9051): ❌ ERROR")
    
    # Test IP through Tor
    if socks_ok:
        try:
            proxies = {
                'http': 'socks5://127.0.0.1:9050',
                'https': 'socks5://127.0.0.1:9050'
            }
            response = requests.get('https://httpbin.org/ip', proxies=proxies, timeout=10)
            tor_ip = response.json()['origin']
            print(f"IP via Tor: ✅ {tor_ip}")
            
            # Compare with normal IP
            normal_response = requests.get('https://httpbin.org/ip', timeout=10)
            normal_ip = normal_response.json()['origin']
            print(f"Normal IP: {normal_ip}")
            
            if tor_ip != normal_ip:
                print("🎉 Tor is working correctly! (Different IPs)")
            else:
                print("⚠️ IPs are the same, Tor might not be working")
                
        except Exception as e:
            print(f"❌ Error testing Tor: {e}")
    
    return socks_ok and control_ok

if __name__ == "__main__":
    check_tor()
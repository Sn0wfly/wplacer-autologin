# Guarda esto como check_tor.py
import socket
import requests

def check_tor():
    print("🔍 Verificando Tor...")
    
    # Verificar puerto SOCKS
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('127.0.0.1', 9050))
        sock.close()
        socks_ok = result == 0
        print(f"Puerto SOCKS (9050): {'✅ OK' if socks_ok else '❌ CERRADO'}")
    except:
        socks_ok = False
        print("Puerto SOCKS (9050): ❌ ERROR")
    
    # Verificar puerto Control
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex(('127.0.0.1', 9051))
        sock.close()
        control_ok = result == 0
        print(f"Puerto Control (9051): {'✅ OK' if control_ok else '❌ CERRADO'}")
    except:
        control_ok = False
        print("Puerto Control (9051): ❌ ERROR")
    
    # Probar IP a través de Tor
    if socks_ok:
        try:
            proxies = {
                'http': 'socks5://127.0.0.1:9050',
                'https': 'socks5://127.0.0.1:9050'
            }
            response = requests.get('https://httpbin.org/ip', proxies=proxies, timeout=10)
            tor_ip = response.json()['origin']
            print(f"IP via Tor: ✅ {tor_ip}")
            
            # Comparar con IP normal
            normal_response = requests.get('https://httpbin.org/ip', timeout=10)
            normal_ip = normal_response.json()['origin']
            print(f"IP Normal: {normal_ip}")
            
            if tor_ip != normal_ip:
                print("🎉 ¡Tor está funcionando correctamente! (IPs diferentes)")
            else:
                print("⚠️ Las IPs son iguales, puede que Tor no esté funcionando")
                
        except Exception as e:
            print(f"❌ Error probando Tor: {e}")
    
    return socks_ok and control_ok

if __name__ == "__main__":
    check_tor()
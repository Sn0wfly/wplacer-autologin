import requests

# Formato oficial de Decodo
url = 'https://ip.decodo.com/json'
username = 'sp8m5vfxrp'  # ⚠️ CONFIRMA si es 'vfxrp' o 'vfrp'
password = 'g8bB+fiu5rFg2DL8qw'

# Probar varios puertos
ports = ['10001', '10002', '10003']

for port in ports:
    proxy = f"http://{username}:{password}@gate.decodo.com:{port}"
    try:
        result = requests.get(url, proxies={
            'http': proxy,
            'https': proxy
        }, timeout=10)
        print(f"✅ Puerto {port}: {result.text.strip()}")
        break  # Si funciona uno, salimos
    except Exception as e:
        print(f"❌ Puerto {port}: {e}")

# También probar con la URL de prueba general
print("\n🔄 Probando con httpbin.org...")
try:
    proxy = f"http://{username}:{password}@gate.decodo.com:10001"
    result = requests.get('https://httpbin.org/ip', proxies={
        'http': proxy,
        'https': proxy
    }, timeout=10)
    ip = result.json()['origin']
    print(f"✅ httpbin.org: IP = {ip}")
except Exception as e:
    print(f"❌ httpbin.org: {e}")
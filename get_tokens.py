#!/usr/bin/env python3
"""Asistente para obtener los tokens UNA vez, en tu ordenador.

Uso:
    python get_tokens.py

Te pide el Client ID de Simkl y el de AniList, te da los enlaces para autorizar
y al final guarda los tokens como Secrets del repo con la CLI de GitHub (`gh`),
si la tienes instalada. Si no, te los muestra para que los pegues tú en
GitHub → Settings → Secrets and variables → Actions.

Nunca escribe los tokens en ningún archivo. No los subas a GitHub: Simkl los
revoca automáticamente si los detecta en un repositorio.
"""

import getpass
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "nellon86/simkl-anilist-sync"
USER_AGENT = "simkl-anilist-sync/1.0"
SIMKL_API = "https://api.simkl.com"


def post(url, *, data=None, json_body=None, headers=None):
    headers = dict(headers or {})
    headers["User-Agent"] = USER_AGENT
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
    else:
        body = urllib.parse.urlencode(data or {}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


def simkl_device_flow(client_id):
    status, res = post(f"{SIMKL_API}/oauth2/device",
                       data={"client_id": client_id, "scope": "media:read"})
    if status != 200 or "device_code" not in res:
        sys.exit(f"Simkl rechazó la petición ({status}): {res}. "
                 "Comprueba que la app es AUTH V2 y el Client ID es correcto.")

    link = res.get("verification_uri_complete") or res.get("verification_uri")
    print("\n1) Abre este enlace y autoriza la app en Simkl:")
    print(f"   {link}")
    if not res.get("verification_uri_complete"):
        print(f"   Código: {res.get('user_code')}")
    print("   Esperando la autorización… (caduca en 15 minutos)")

    interval = int(res.get("interval") or 5)
    deadline = time.time() + int(res.get("expires_in") or 900)
    while time.time() < deadline:
        time.sleep(interval)
        status, tok = post(f"{SIMKL_API}/oauth2/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": res["device_code"],
        })
        if status == 200 and tok.get("refresh_token"):
            print("   ✓ Simkl autorizado.")
            return tok["refresh_token"]
        error = tok.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        sys.exit(f"Error de Simkl: {error or status}. Vuelve a ejecutar el script.")
    sys.exit("El código de Simkl caducó. Vuelve a ejecutar el script.")


def anilist_token(client_id):
    url = ("https://anilist.co/api/v2/oauth/authorize?"
           + urllib.parse.urlencode({"client_id": client_id, "response_type": "token"}))
    print("\n2) Abre este enlace, autoriza la app en AniList y copia el token que te muestra:")
    print(f"   {url}")
    token = getpass.getpass("   Pega aquí el token (no se verá al escribir): ").strip()
    status, res = post("https://graphql.anilist.co",
                       json_body={"query": "query{ Viewer{ name } }"},
                       headers={"Authorization": f"Bearer {token}"})
    name = ((res.get("data") or {}).get("Viewer") or {}).get("name")
    if status != 200 or not name:
        sys.exit("AniList no acepta ese token. Vuelve a probar.")
    print(f"   ✓ AniList autorizado como {name}.")
    return token


def store_secrets(secrets):
    if shutil.which("gh"):
        answer = input(f"\n¿Guardo los Secrets en {REPO} con la CLI de GitHub? [S/n] ").strip().lower()
        if answer in ("", "s", "si", "sí", "y", "yes"):
            for name, value in secrets.items():
                subprocess.run(["gh", "secret", "set", name, "--repo", REPO],
                               input=value.encode(), check=True)
                print(f"   ✓ {name} guardado.")
            return
    print("\nCrea estos Secrets en GitHub → tu repo → Settings → Secrets and variables → "
          "Actions → New repository secret. Cópialos y cierra esta ventana después:\n")
    for name, value in secrets.items():
        print(f"{name}\n{value}\n")


def main():
    print("Asistente de tokens para simkl-anilist-sync")
    simkl_client_id = input("Client ID de Simkl: ").strip()
    anilist_client_id = input("Client ID de AniList: ").strip()
    secrets = {
        "SIMKL_CLIENT_ID": simkl_client_id,
        "SIMKL_REFRESH_TOKEN": simkl_device_flow(simkl_client_id),
        "ANILIST_TOKEN": anilist_token(anilist_client_id),
    }
    store_secrets(secrets)
    print("\nListo. Lanza el workflow a mano una vez desde la pestaña Actions para comprobarlo.")


if __name__ == "__main__":
    main()

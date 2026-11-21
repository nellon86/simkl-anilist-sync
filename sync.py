#!/usr/bin/env python3
"""Sincroniza el progreso de anime de Simkl hacia AniList (un solo sentido).

Variables de entorno (en GitHub Actions vienen de los Secrets):
  SIMKL_CLIENT_ID      Client ID de la app de Simkl (AUTH V2)
  SIMKL_REFRESH_TOKEN  Refresh token de Simkl (dura 180 días y se renueva con cada uso)
  ANILIST_TOKEN        Token de acceso de AniList
Opcionales:
  FULL_SYNC=1          Ignora el estado guardado y revisa toda la biblioteca de Simkl
  DRY_RUN=1            Muestra lo que haría sin escribir en AniList ni guardar estado

Solo usa la librería estándar de Python. Nunca imprime ni guarda tokens.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP_NAME = "simkl-anilist-sync"
APP_VERSION = "1.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION}"
SIMKL_API = "https://api.simkl.com"
ANILIST_API = "https://graphql.anilist.co"
STATE_FILE = Path(__file__).with_name("state.json")

# Simkl -> AniList
STATUS_MAP = {
    "watching": "CURRENT",
    "plantowatch": "PLANNING",
    "hold": "PAUSED",
    "dropped": "DROPPED",
    "completed": "COMPLETED",
}

# AniList limita a 90 peticiones/minuto (a veces 30 en modo degradado): vamos con margen.
ANILIST_DELAY = 2.1


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "si", "sí")


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"Falta la variable de entorno {name} (configúrala como Secret del repo).")
    return value


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- HTTP

def http(method, url, *, headers=None, data=None, json_body=None, retries=3):
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode() or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 429 and attempt < retries:
                wait = int(e.headers.get("Retry-After") or 60)
                log(f"  Límite de peticiones alcanzado, espero {wait}s…")
                time.sleep(wait + 1)
                continue
            if e.code >= 500 and attempt < retries:
                time.sleep(5 * attempt)
                continue
            # La URL no lleva tokens (van en cabeceras o en el cuerpo), se puede mostrar.
            raise RuntimeError(f"HTTP {e.code} en {method} {url.split('?')[0]}: {detail}") from None
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"Error de red en {url.split('?')[0]}: {e.reason}") from None


# --------------------------------------------------------------------------- Simkl

class Simkl:
    def __init__(self, client_id, refresh_token):
        self.client_id = client_id
        self.access_token = self._refresh(refresh_token)

    def _refresh(self, refresh_token):
        res = http("POST", f"{SIMKL_API}/oauth2/token", data={
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
        })
        token = res.get("access_token")
        if not token:
            sys.exit("Simkl no devolvió access_token. ¿El refresh token ha caducado o es de otra app?")
        return token

    def _params(self, extra=None):
        params = {"client_id": self.client_id, "app-name": APP_NAME, "app-version": APP_VERSION}
        params.update(extra or {})
        return urllib.parse.urlencode(params)

    def get(self, path, extra=None, auth=True):
        headers = {"Authorization": f"Bearer {self.access_token}"} if auth else {}
        return http("GET", f"{SIMKL_API}{path}?{self._params(extra)}", headers=headers)

    def activities(self):
        return self.get("/sync/activities")

    def anime_items(self, date_from=None):
        extra = {"date_from": date_from} if date_from else None
        res = self.get("/sync/all-items/anime", extra)
        return (res or {}).get("anime") or []

    def anime_ids(self, simkl_id):
        """Pide la ficha pública del anime para obtener IDs que falten en la lista."""
        try:
            res = self.get(f"/anime/{simkl_id}", {"extended": "full"}, auth=False)
            return (res or {}).get("ids") or {}
        except RuntimeError as e:
            log(f"  No pude leer la ficha de Simkl {simkl_id}: {e}")
            return {}


# --------------------------------------------------------------------------- AniList

MEDIA_FIELDS = "id episodes title { romaji } mediaListEntry { status progress }"
QUERY_BY_ID = f"query($id:Int){{ Media(id:$id, type:ANIME){{ {MEDIA_FIELDS} }} }}"
QUERY_BY_MAL = f"query($mal:Int){{ Media(idMal:$mal, type:ANIME){{ {MEDIA_FIELDS} }} }}"
MUTATION_SAVE = """
mutation($mediaId:Int, $status:MediaListStatus, $progress:Int){
  SaveMediaListEntry(mediaId:$mediaId, status:$status, progress:$progress){ id status progress }
}"""


class AniList:
    def __init__(self, token, dry_run=False):
        self.token = token
        self.dry_run = dry_run
        self._last = 0.0

    def gql(self, query, variables):
        wait = ANILIST_DELAY - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        res = http("POST", ANILIST_API, headers={"Authorization": f"Bearer {self.token}"},
                   json_body={"query": query, "variables": variables})
        if res.get("errors"):
            msgs = "; ".join(e.get("message", "?") for e in res["errors"])
            if "Not Found" in msgs:
                return None
            raise RuntimeError(f"AniList: {msgs}")
        return res.get("data")

    def viewer(self):
        data = self.gql("query{ Viewer{ name } }", {})
        return (data or {}).get("Viewer", {}).get("name")

    def media(self, anilist_id=None, mal_id=None):
        if anilist_id:
            data = self.gql(QUERY_BY_ID, {"id": anilist_id})
        else:
            data = self.gql(QUERY_BY_MAL, {"mal": mal_id})
        return (data or {}).get("Media")

    def save(self, media_id, status, progress):
        if self.dry_run:
            return
        variables = {"mediaId": media_id, "status": status}
        if progress is not None:
            variables["progress"] = progress
        self.gql(MUTATION_SAVE, variables)


# --------------------------------------------------------------------------- Lógica

def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decide(simkl_status, watched, media):
    """Devuelve (status, progress) a escribir en AniList, o None si no hay que tocar nada.

    Reglas:
    - Nunca se baja el progreso que ya tenga AniList.
    - Una entrada COMPLETED o REPEATING en AniList no se toca.
    - Una entrada que ya está en la lista no vuelve a PLANNING.
    """
    target = STATUS_MAP.get(simkl_status)
    if not target:
        return None
    entry = media.get("mediaListEntry") or {}
    cur_status = entry.get("status")
    cur_progress = entry.get("progress") or 0
    episodes = media.get("episodes")

    if cur_status in ("COMPLETED", "REPEATING"):
        return None
    if target == "PLANNING" and cur_status:
        return None

    progress = max(cur_progress, watched or 0)
    if target == "COMPLETED" and episodes:
        progress = max(progress, episodes)
    if episodes:
        progress = min(progress, episodes)

    if cur_status == target and cur_progress == progress:
        return None
    return target, (progress if progress > 0 else None)


def resolve_media(item, simkl, anilist, id_cache):
    show = item.get("show") or {}
    ids = show.get("ids") or {}
    simkl_id = str(ids.get("simkl") or "")
    title = show.get("title") or f"Simkl {simkl_id}"

    cached = id_cache.get(simkl_id)
    anilist_id = to_int(ids.get("anilist")) or (cached and cached.get("anilist"))
    mal_id = to_int(ids.get("mal")) or (cached and cached.get("mal"))

    if not anilist_id and not mal_id and simkl_id:
        extra = simkl.anime_ids(simkl_id)
        anilist_id = to_int(extra.get("anilist"))
        mal_id = to_int(extra.get("mal"))

    media = None
    if anilist_id:
        media = anilist.media(anilist_id=anilist_id)
    if not media and mal_id:
        media = anilist.media(mal_id=mal_id)

    if media and simkl_id:
        id_cache[simkl_id] = {"anilist": media["id"], "mal": mal_id}
    return title, media


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                          encoding="utf-8")


def main():
    dry_run = env_flag("DRY_RUN")
    full_sync = env_flag("FULL_SYNC")
    simkl = Simkl(require_env("SIMKL_CLIENT_ID"), require_env("SIMKL_REFRESH_TOKEN"))
    anilist = AniList(require_env("ANILIST_TOKEN"), dry_run=dry_run)

    state = load_state()
    id_cache = state.setdefault("id_cache", {})

    act = simkl.activities()
    top_all = act.get("all")
    anime_all = (act.get("anime") or {}).get("all")

    first_run = not state.get("simkl_activities_all")
    if not full_sync and not first_run and anime_all == state.get("simkl_anime_all"):
        log("Sin cambios de anime en Simkl desde la última ejecución.")
        return

    log(f"Usuario de AniList: {anilist.viewer()}")
    date_from = None if (full_sync or first_run) else state.get("simkl_activities_all")
    items = simkl.anime_items(date_from)
    log(f"{'Sincronización completa' if not date_from else 'Cambios desde ' + date_from}: "
        f"{len(items)} anime(s) en Simkl.{' [DRY RUN]' if dry_run else ''}")

    updated = skipped = unresolved = failed = 0
    for item in items:
        if item.get("is_rewatch"):
            continue
        status = item.get("status")
        watched = to_int(item.get("watched_episodes_count")) or 0
        try:
            title, media = resolve_media(item, simkl, anilist, id_cache)
            if not media:
                unresolved += 1
                log(f"  ? {title}: no encuentro el anime en AniList, lo salto.")
                continue
            change = decide(status, watched, media)
            if not change:
                skipped += 1
                continue
            new_status, new_progress = change
            entry = media.get("mediaListEntry") or {}
            anilist.save(media["id"], new_status, new_progress)
            updated += 1
            log(f"  ✓ {media['title']['romaji']}: "
                f"{entry.get('status') or '—'} {entry.get('progress') or 0} → "
                f"{new_status} {new_progress or 0}")
        except RuntimeError as e:
            failed += 1
            log(f"  ✗ Error con {(item.get('show') or {}).get('title')}: {e}")

    log(f"Resumen: {updated} actualizados, {skipped} ya estaban bien, "
        f"{unresolved} sin equivalente en AniList, {failed} con error.")

    if dry_run:
        log("DRY RUN: no se guarda el estado.")
        return
    if failed:
        # No avanzamos la marca de tiempo: la próxima ejecución lo reintentará.
        save_state(state)
        sys.exit(1)

    state["simkl_activities_all"] = top_all
    state["simkl_anime_all"] = anime_all
    state["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)


if __name__ == "__main__":
    main()

# simkl-anilist-sync

Sincroniza el anime de **Simkl** con **AniList** en un solo sentido: el estado de la lista y los episodios vistos, sin puntuaciones. Se ejecuta cada hora con GitHub Actions.

## Cómo funciona

1. Pide a Simkl `/sync/activities`. Si el anime no ha cambiado desde la última vez, termina ahí (una sola petición).
2. Si hay cambios, descarga solo lo modificado con `/sync/all-items/anime?date_from=…`. La primera vez lo descarga todo.
3. Busca cada anime en AniList por su ID de AniList o, si falta, por el de MyAnimeList.
4. Actualiza la entrada con `SaveMediaListEntry`.

| Simkl | AniList |
|---|---|
| watching | CURRENT |
| plantowatch | PLANNING |
| hold | PAUSED |
| dropped | DROPPED |
| completed | COMPLETED |

Reglas de seguridad:
- Nunca baja el progreso que ya tenga AniList.
- No toca entradas que en AniList estén en COMPLETED o REPEATING.
- No devuelve a PLANNING algo que ya estés viendo.
- No borra nada de AniList si lo quitas de Simkl.

`state.json` guarda la última marca de tiempo y una caché de IDs. **Nunca contiene tokens.**

## Configuración (una sola vez)

1. **Simkl**: app AUTH V2 de tipo *TV, devices & command line* en <https://simkl.com/settings/developer/>. Apunta el Client ID.
2. **AniList**: en <https://anilist.co/settings/developer> crea un cliente con Redirect URL `https://anilist.co/api/v2/oauth/pin`. Apunta su ID.
3. En tu ordenador, con Python 3:
   ```
   python get_tokens.py
   ```
   Te guía por las dos autorizaciones y guarda tres Secrets en el repo: `SIMKL_CLIENT_ID`, `SIMKL_REFRESH_TOKEN` y `ANILIST_TOKEN`. Si tienes la CLI `gh`, lo hace solo; si no, te los muestra para que los pegues en *Settings → Secrets and variables → Actions*.
4. En la pestaña **Actions**, lanza *Sync Simkl → AniList* a mano con **dry_run** marcado para ver qué haría. Si te convence, lánzalo sin dry_run.

> ⚠️ No subas nunca los tokens al repo, ni siquiera en privado: Simkl revoca automáticamente los que encuentra en GitHub.

## Caducidad

- **Simkl**: el refresh token dura 180 días y el plazo se reinicia en cada uso, así que con ejecuciones frecuentes no caduca.
- **AniList**: el token dura un año. Cuando caduque, vuelve a ejecutar `get_tokens.py`.

## Opciones del workflow manual

- **full_sync**: revisa toda la biblioteca, no solo los cambios.
- **dry_run**: muestra los cambios sin escribir en AniList.

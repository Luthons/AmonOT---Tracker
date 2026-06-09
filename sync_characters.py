"""
sync_characters.py
Sincroniza os resets dos personagens cadastrados no Supabase
com os dados mais recentes do guild_data.json.
Roda junto com o update_full para manter os perfis atualizados.
"""

import json
import os
import time
import requests
from datetime import date
from characters import fetch_last_seen

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[sync_chars] ⚠ Supabase não configurado")
        return

    # Carrega guild_data.json
    try:
        with open("guild_data.json", encoding="utf-8") as f:
            guild_data = json.load(f)
    except Exception as e:
        print(f"[sync_chars] erro ao ler guild_data.json: {e}")
        return

    # Monta dicionário name → resets a partir dos membros
    members = guild_data.get("members", [])
    reset_map = {}
    for m in members:
        name   = (m.get("name") or "").strip()
        resets = m.get("resets", 0)
        if name:
            reset_map[name.lower()] = {"name": name, "resets": resets}

    # Também cruza com highscores se disponível
    highscores = guild_data.get("highscores", {})
    if isinstance(highscores, dict):
        for entry in highscores.get("resets", {}).get("entries", []):
            name   = (entry.get("name") or "").strip()
            resets = entry.get("value", 0)
            if name and name.lower() not in reset_map:
                reset_map[name.lower()] = {"name": name, "resets": resets}
            elif name and resets > 0:
                reset_map[name.lower()]["resets"] = resets

    if not reset_map:
        print("[sync_chars] nenhum membro encontrado no guild_data.json")
        return

    print(f"[sync_chars] {len(reset_map)} personagens no guild_data")

    # Busca todos os personagens cadastrados no Supabase
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/characters",
            headers=SUPA_HEADERS,
            params={"select": "id,name,resets", "limit": "1000"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[sync_chars] erro ao buscar characters: {r.status_code}")
            return
        chars = r.json()
    except Exception as e:
        print(f"[sync_chars] erro ao buscar characters: {e}")
        return

    print(f"[sync_chars] {len(chars)} personagens no Supabase")

    updated = 0
    skipped = 0

    for char in chars:
        char_name  = (char.get("name") or "").strip()
        char_id    = char.get("id")
        old_resets = char.get("resets", 0) or 0

        patch_body = {}

        # Verifica mudança de resets
        match = reset_map.get(char_name.lower())
        if match and match["resets"] != old_resets:
            patch_body["resets"] = match["resets"]

        # Busca last_seen da página do personagem
        last_seen = fetch_last_seen(char_name)
        if last_seen:
            patch_body["last_seen"] = last_seen
        time.sleep(0.5)

        if not patch_body:
            skipped += 1
            continue

        try:
            r2 = requests.patch(
                f"{SUPABASE_URL}/rest/v1/characters",
                headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
                params={"id": f"eq.{char_id}"},
                json=patch_body,
                timeout=10,
            )
            if r2.status_code in (200, 201, 204):
                if "resets" in patch_body:
                    print(f"[sync_chars] ✅ {char_name}: resets {old_resets} → {patch_body['resets']}" + (f" | last_seen {last_seen}" if last_seen else ""))
                else:
                    print(f"[sync_chars] ✅ {char_name}: last_seen {last_seen}")
                updated += 1
            else:
                print(f"[sync_chars] ❌ {char_name}: {r2.status_code} {r2.text[:80]}")
        except Exception as e:
            print(f"[sync_chars] erro ao atualizar {char_name}: {e}")

    print(f"[sync_chars] ✅ {updated} atualizados | {skipped} sem mudança/não encontrado")

    # Define guild_join_date para profiles que ainda não têm (novos membros)
    try:
        today = date.today().isoformat()
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params={"guild_join_date": "is.null"},
            json={"guild_join_date": today},
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            print(f"[sync_chars] ✅ guild_join_date={today} aplicado a profiles sem data")
        else:
            print(f"[sync_chars] ⚠ guild_join_date: {r.status_code} {r.text[:80]}")
    except Exception as e:
        print(f"[sync_chars] erro guild_join_date: {e}")


if __name__ == "__main__":
    run()

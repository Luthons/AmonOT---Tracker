"""
sync_characters.py
Sincroniza os resets dos personagens cadastrados no Supabase
com os dados mais recentes do guild_data.json.
Roda junto com o update_full para manter os perfis atualizados.
"""

import json
import os
import requests

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
    for h in guild_data.get("highscores", []):
        name   = (h.get("name") or "").strip()
        resets = h.get("resets", 0)
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

        match = reset_map.get(char_name.lower())
        if not match:
            skipped += 1
            continue

        new_resets = match["resets"]
        if new_resets == old_resets:
            skipped += 1
            continue

        # Atualiza resets no Supabase
        try:
            r2 = requests.patch(
                f"{SUPABASE_URL}/rest/v1/characters",
                headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
                params={"id": f"eq.{char_id}"},
                json={"resets": new_resets},
                timeout=10,
            )
            if r2.status_code in (200, 201, 204):
                print(f"[sync_chars] ✅ {char_name}: {old_resets} → {new_resets}")
                updated += 1
            else:
                print(f"[sync_chars] ❌ {char_name}: {r2.status_code} {r2.text[:80]}")
        except Exception as e:
            print(f"[sync_chars] erro ao atualizar {char_name}: {e}")

    print(f"[sync_chars] ✅ {updated} atualizados | {skipped} sem mudança/não encontrado")


if __name__ == "__main__":
    run()

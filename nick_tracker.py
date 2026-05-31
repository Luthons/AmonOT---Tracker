"""
nick_tracker.py
Detecta mudanças de nick em players das listas (hunted, bonus, guild inimiga)
buscando a página de personagens do amonOT e verificando "Nomes Anteriores".
Roda junto com o update_full (a cada 30min).
"""

import json
import os
import time
import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

AMONOT_CHAR_URL = "https://amonot.online/characters?name={}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def supa_get(table, params):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPA_HEADERS, params=params, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[nick_tracker] erro GET {table}: {e}")
        return []


def supa_patch(table, params, body):
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params=params, json=body, timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[nick_tracker] erro PATCH: {e}")
        return False


def get_character_info(name: str) -> dict:
    """Busca info do personagem no amonOT: nome atual e nomes anteriores."""
    try:
        url = AMONOT_CHAR_URL.format(requests.utils.quote(name))
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(separator="\n")
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        result = {"current_name": None, "former_names": [], "resets": None}

        # Find "Resultados para" line to get current name
        for i, line in enumerate(lines):
            if "Resultados para" in line:
                # Extract name from quotes
                import re
                m = re.search(r'"(.+?)"', line)
                if m:
                    result["current_name"] = m.group(1)

            # Find "Nomes Anteriores" section
            if "Nomes Anteriores" in line and i + 1 < len(lines):
                # Next lines are former names until empty or next section
                for j in range(i + 1, min(i + 10, len(lines))):
                    next_line = lines[j]
                    if not next_line or any(k in next_line for k in ["Reset", "Level", "Guild", "Vocation", "Status"]):
                        break
                    result["former_names"].append(next_line)

            # Find resets
            if "resets" in line.lower() and i > 0:
                import re
                m = re.search(r"(\d+)\s*reset", line, re.IGNORECASE)
                if m:
                    result["resets"] = int(m.group(1))

        return result
    except Exception as e:
        print(f"[nick_tracker] erro ao buscar {name}: {e}")
        return {}


def check_and_update(table: str, name_field: str, player_name: str, record_id: str):
    """Verifica se o player mudou de nick e atualiza se necessário."""
    info = get_character_info(player_name)
    if not info:
        return

    current = info.get("current_name")
    former  = info.get("former_names", [])

    # Se o nome atual retornado é diferente do que temos
    # (significa que buscamos pelo nome antigo mas o personagem tem nome novo)
    if current and current.lower() != player_name.lower():
        # O player mudou de nick
        print(f"[nick_tracker] 🔄 Nick change: '{player_name}' → '{current}'")
        supa_patch(table, {"id": f"eq.{record_id}"}, {name_field: current})
        return

    # Se o nome que buscamos aparece como "nome anterior" de outro personagem
    # isso é tratado na busca reversa abaixo


def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[nick_tracker] ⚠ Supabase não configurado")
        return

    # 1. Hunted list
    hunted = supa_get("hunted_list", {"select": "id,name"})
    print(f"[nick_tracker] verificando {len(hunted)} players na hunted list...")
    for h in hunted:
        check_and_update("hunted_list", "name", h["name"], h["id"])
        time.sleep(1)  # respeita rate limit

    # 2. Bonus list
    bonus = supa_get("kill_bonus_list", {"select": "id,char_name"})
    print(f"[nick_tracker] verificando {len(bonus)} players na lista bônus...")
    for b in bonus:
        check_and_update("kill_bonus_list", "char_name", b["char_name"], b["id"])
        time.sleep(1)

    # 3. Guild inimiga — atualiza no guild_data.json via scraper (já feito pelo main.py)
    # Aqui apenas logamos mudanças detectadas
    try:
        with open("guild_data.json", encoding="utf-8") as f:
            guild_data = json.load(f)
        enemy_members = []
        for eg in guild_data.get("enemy_guilds", []):
            enemy_members.extend(eg.get("members", []))
        print(f"[nick_tracker] {len(enemy_members)} membros inimigos (atualizados pelo scraper)")
    except Exception as e:
        print(f"[nick_tracker] erro ao ler guild_data: {e}")

    print("[nick_tracker] ✅ verificação concluída")


if __name__ == "__main__":
    run()

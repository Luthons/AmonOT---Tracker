"""
nick_tracker.py
Detecta mudanças de nick em players das listas (hunted, bonus)
usando o mesmo characters.py do scraper principal.
Roda junto com o update_full (a cada 30min).
"""

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

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


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


def fetch_character_info(name: str) -> dict:
    """Busca info do personagem usando o mesmo padrão do characters.py."""
    url = f"https://amonot.online/characters?name={requests.utils.quote(name)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return {}

        soup = BeautifulSoup(r.text, "html.parser")
        details = {}
        for row in soup.select(".char-detail-row"):
            label = row.select_one(".char-detail-label")
            value = row.select_one(".char-detail-value")
            if label and value:
                details[label.get_text(strip=True)] = value.get_text(strip=True)

        former_names_raw = details.get("Nomes Anteriores", details.get("Former Names", ""))
        former_names = [n.strip() for n in former_names_raw.split(",") if n.strip()] if former_names_raw else []

        try:
            resets = int(details.get("Resets", "0"))
        except:
            resets = None

        # Extract current name from page title/header
        import re
        current_name = None
        # Look for "Resultados para" in page text
        text = soup.get_text(separator="\n")
        m = re.search(r'Resultados para[^\n]*"([^"]+)"', text)
        if m:
            current_name = m.group(1)

        return {
            "current_name":  current_name,
            "former_names":  former_names,
            "resets":        resets,
        }
    except Exception as e:
        print(f"[nick_tracker] erro ao buscar {name}: {e}")
        return {}


def check_player(table: str, name_field: str, player_name: str, record_id: str, update_resets: bool = False):
    """Verifica nick change e atualiza resets se necessário."""
    info = fetch_character_info(player_name)
    if not info:
        return

    update_body = {}

    # Nick change: se o nome que buscamos aparece como nome anterior
    # significa que o personagem mudou para um nome novo
    current = info.get("current_name")
    former  = info.get("former_names", [])

    if current and current.lower() != player_name.lower():
        # O nome atual retornado é diferente do que temos — mudou de nick
        print(f"[nick_tracker] 🔄 {player_name} → {current}")
        update_body[name_field] = current
    elif former and any(f.lower() == player_name.lower() for f in former):
        # O nome que temos está na lista de nomes anteriores
        # Isso não deveria acontecer na busca direta, mas por segurança
        print(f"[nick_tracker] ⚠ {player_name} aparece como nome anterior (inconsistência)")

    # Atualiza resets se solicitado
    if update_resets and info.get("resets") is not None:
        update_body["resets"] = info["resets"]

    if update_body:
        supa_patch(table, {"id": f"eq.{record_id}"}, update_body)


def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[nick_tracker] ⚠ Supabase não configurado")
        return

    # 1. Hunted list
    hunted = supa_get("hunted_list", {"select": "id,name"})
    print(f"[nick_tracker] verificando {len(hunted)} players na hunted list...")
    for h in hunted:
        check_player("hunted_list", "name", h["name"], h["id"], update_resets=True)
        time.sleep(0.5)

    # 2. Bonus list
    bonus = supa_get("kill_bonus_list", {"select": "id,char_name"})
    print(f"[nick_tracker] verificando {len(bonus)} players na lista bônus...")
    for b in bonus:
        check_player("kill_bonus_list", "char_name", b["char_name"], b["id"], update_resets=True)
        time.sleep(0.5)

    print("[nick_tracker] ✅ verificação concluída")


if __name__ == "__main__":
    run()

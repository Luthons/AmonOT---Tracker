"""
market_tracker_realtime.py
Roda no update_realtime (a cada 1 min).
- Busca apenas página 1 de cada raridade
- Detecta itens NOVOS e manda DM de entrada
- NÃO atualiza o snapshot (responsabilidade do market_tracker_full.py)
"""

import os
import time
import requests
from bs4 import BeautifulSoup

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")

MARKET_URLS = [
    {"rarity": 1, "label": "Uncommon", "color": 0x6EC96E, "emoji": "🟢"},
    {"rarity": 2, "label": "Rare",     "color": 0x5AB0E8, "emoji": "🔵"},
    {"rarity": 3, "label": "Epic",     "color": 0xB06FE8, "emoji": "🟣"},
    {"rarity": 4, "label": "Legendary","color": 0xE8A030, "emoji": "🟠"},
    {"rarity": 5, "label": "Mythical", "color": 0xE84040, "emoji": "🔴"},
]

BASE_URL = "https://amonot.online/index.php?page=market&name=&sale=all&slot=&tier=&world=Baiak&rarity={rarity}"

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates",
}


def supa_get(table: str, params: dict) -> list:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=SUPA_HEADERS, params=params, timeout=10)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[market_rt] erro GET {table}: {e}")
        return []


def load_users_cache() -> dict:
    profiles = supa_get("profiles", {"discord_id": "not.is.null", "select": "id,discord_id"})
    if not profiles:
        return {}
    profile_ids = [p["id"] for p in profiles]
    settings_rows = supa_get("notification_settings", {
        "profile_id": f"in.({','.join(profile_ids)})",
        "select":     "profile_id,market_dm_rarities,market_dm_vocations,market_dm_categories,market_dm_attrs,market_dm_blacklist,market_dm_whitelist",
    })
    settings_map = {row["profile_id"]: row for row in settings_rows}
    cache = {}
    for p in profiles:
        discord_id = p["discord_id"]
        if not discord_id or not discord_id.strip():
            continue
        row = settings_map.get(p["id"], {})
        cache[discord_id] = {
            "rarities":   row.get("market_dm_rarities")   or ["all"],
            "vocations":  row.get("market_dm_vocations")  or ["all"],
            "categories": row.get("market_dm_categories") or ["all"],
            "attrs":      row.get("market_dm_attrs")      or [],
            "blacklist":  [x.lower() for x in (row.get("market_dm_blacklist") or [])],
            "whitelist":  [x.lower() for x in (row.get("market_dm_whitelist") or [])],
        }
    print(f"[market_rt] cache: {len(cache)} usuários")
    return cache


def load_items_db_cache() -> dict:
    rows = supa_get("items_db", {"select": "name,category,vocations", "limit": "3000"})
    return {
        row["name"].lower(): {
            "category":  row.get("category", ""),
            # Trata lista vazia como ["all"] — itens sem vocação cadastrada
            # não devem bloquear DMs de usuários com filtro de vocação específica
            "vocations": row.get("vocations") or ["all"],
        }
        for row in rows
    }


def load_snapshot() -> dict:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/market_snapshot", headers=SUPA_HEADERS, timeout=10)
        if r.status_code == 200:
            return {str(row["rarity"]): {item["id"]: item for item in (row["items"] or [])} for row in r.json()}
        return {}
    except Exception as e:
        print(f"[market_rt] erro snapshot: {e}")
        return {}


def save_snapshot_new_items(rarity: int, new_items: list):
    """
    Adiciona os itens novos ao snapshot existente.
    NÃO sobrescreve — apenas acrescenta para evitar re-notificação.
    """
    try:
        import datetime
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers=SUPA_HEADERS,
            params={"rarity": f"eq.{rarity}", "select": "items"},
            timeout=10,
        )
        current_items = []
        if r.status_code == 200 and r.json():
            current_items = r.json()[0].get("items") or []

        current_ids = {item["id"] for item in current_items}
        to_add = [item for item in new_items if item["id"] not in current_ids]
        if not to_add:
            return

        merged = current_items + to_add
        requests.post(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"rarity": rarity, "items": merged, "updated_at": datetime.datetime.utcnow().isoformat()},
            timeout=10,
        )
        print(f"[market_rt] snapshot atualizado: +{len(to_add)} itens (rarity {rarity})")
    except Exception as e:
        print(f"[market_rt] erro ao atualizar snapshot: {e}")


def fetch_page1(rarity: int) -> list:
    url = BASE_URL.format(rarity=rarity) + "&p=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
    except Exception as e:
        print(f"[market_rt] erro fetch: {e}")
        return []

    soup  = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select(".mkt-row"):
        name_el  = row.select_one(".mkt-name")
        attrs_el = row.select_one(".mkt-attrs")
        price_el = row.select_one(".mkt-price")
        if not name_el:
            continue
        name = name_el.get_text(separator=" ", strip=True)
        for lbl in ["Mythical", "Legendary", "Epic", "Rare", "Uncommon", "Common"]:
            if name.endswith(lbl):
                name = name[:-len(lbl)].strip()
                break
        price = "?"
        if price_el:
            total_el = price_el.select_one(".mkt-total")
            price = total_el.get_text(strip=True) if total_el else price_el.get_text(strip=True)
        attrs_text = ""
        if attrs_el:
            attrs_text = attrs_el.get("title", "") or attrs_el.get_text(" ", strip=True)
        item_id = f"{name}|{price}|{attrs_text[:50]}"
        items.append({"id": item_id, "name": name, "price": price, "attrs": attrs_text})
    return items


def should_notify(prefs: dict, rarity: int, item_name: str, item_attrs: str, items_db: dict) -> bool:
    name_lower = item_name.lower()
    if prefs["whitelist"]:
        if name_lower not in prefs["whitelist"]:
            return False
        if "all" not in prefs["rarities"] and str(rarity) not in prefs["rarities"]:
            return False
        return True
    if name_lower in prefs["blacklist"]:
        return False
    if "all" not in prefs["rarities"] and str(rarity) not in prefs["rarities"]:
        return False
    if "all" not in prefs["vocations"]:
        db = items_db.get(name_lower)
        # db["vocations"] já vem normalizado como ["all"] quando vazio (via load_items_db_cache)
        vocs = db["vocations"] if db else ["all"]
        if "all" not in vocs and not set(prefs["vocations"]) & set(vocs):
            return False
    if "all" not in prefs["categories"]:
        db = items_db.get(name_lower)
        cat = db["category"] if db else ""
        if cat not in prefs["categories"]:
            return False
    if prefs["attrs"]:
        al = item_attrs.lower()
        if not any(a.lower() + " lv." in al for a in prefs["attrs"]):
            return False
    return True


def get_dm_channel(user_id: str) -> str | None:
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers={"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"},
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    print(f"[discord] erro DM: {r.status_code}")
    return None


def send_dm(channel_id: str, embed: dict):
    for _ in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"},
            json={"embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            time.sleep(r.json().get("retry_after", 1) + 0.1)
        else:
            return False
    return False


def build_embed_new(item: dict, rarity_info: dict) -> dict:
    import datetime
    attrs_lines = ""
    if item["attrs"]:
        attrs = [a.strip() for a in item["attrs"].replace("·", "\n").split("\n") if a.strip()]
        attrs_lines = "\n".join(f"• {a}" for a in attrs[:8])
    description = f"\n📊 **Atributos:**\n{attrs_lines}" if attrs_lines else ""
    return {
        "title":       f"Novo Item — {rarity_info['label']} — {item['name']}",
        "description": description,
        "color":       rarity_info["color"],
        "fields": [
            {"name": "📦 Item",  "value": item["name"],  "inline": True},
            {"name": "💰 Preço", "value": item["price"], "inline": True},
        ],
        "footer":    {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


def run():
    if not DISCORD_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[market_rt] ⚠ configuração incompleta")
        return

    users_cache = load_users_cache()
    if not users_cache:
        print("[market_rt] ⚠ nenhum usuário cadastrado")
        return

    items_db = load_items_db_cache()
    snapshot = load_snapshot()

    for rarity_info in MARKET_URLS:
        rarity = rarity_info["rarity"]
        label  = rarity_info["label"]
        items  = fetch_page1(rarity)

        if not items:
            print(f"[market_rt] ⚠ {label}: fetch vazio, pulando")
            continue

        prev_ids  = snapshot.get(str(rarity), {})
        new_items = [item for item in items if item["id"] not in prev_ids]

        if not new_items:
            print(f"[market_rt] {label}: sem novidades")
            continue

        print(f"[market_rt] {label}: {len(new_items)} novo(s)")
        for item in new_items:
            print(f"[market_rt] 🆕 {item['name']} ({label})")
            embed = build_embed_new(item, rarity_info)
            for discord_id, prefs in users_cache.items():
                if not should_notify(prefs, rarity, item["name"], item.get("attrs", ""), items_db):
                    continue
                ch = get_dm_channel(discord_id)
                if ch:
                    send_dm(ch, embed)
                time.sleep(0.3)

        # Salva itens novos no snapshot para não re-notificar na próxima run
        save_snapshot_new_items(rarity, new_items)

    print("[market_rt] ✅ concluído")


if __name__ == "__main__":
    run()

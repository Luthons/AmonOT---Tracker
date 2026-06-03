"""
market_tracker_full.py
Roda no update_full (a cada 15 min).
- Busca TODAS as páginas de cada raridade
- Atualiza snapshot com merge (preserva itens do realtime que ainda não foram vistos pelo full)
- Detecta itens que SAÍRAM e manda DM de saída
- NÃO manda DM de entrada (responsabilidade do market_tracker_realtime.py)
"""

import os
import time
import datetime
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
        print(f"[market_full] erro GET {table}: {e}")
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
    print(f"[market_full] cache: {len(cache)} usuários")
    return cache


def load_items_db_cache() -> dict:
    rows = supa_get("items_db", {"select": "name,category,vocations", "limit": "3000"})
    cache = {}
    for row in rows:
        entry = {
            "category":  row.get("category", ""),
            # Trata lista vazia como ["all"] — itens sem vocação cadastrada
            # não devem bloquear DMs de usuários com filtro de vocação específica
            "vocations": row.get("vocations") or ["all"],
        }
        name_lower = row["name"].lower()
        cache[name_lower] = entry

        # Registra também o nome base sem sufixo de tier (T1–T10)
        # Ex: "Soulshanks T1" → também indexado como "soulshanks"
        import re
        base = re.sub(r"\s+t\d{1,2}$", "", name_lower, flags=re.IGNORECASE).strip()
        if base != name_lower:
            cache.setdefault(base, entry)

    return cache


def load_snapshot() -> dict:
    """Retorna snapshot como dict de listas: {rarity_str: [item, ...]}"""
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/market_snapshot", headers=SUPA_HEADERS, timeout=10)
        if r.status_code == 200:
            return {str(row["rarity"]): row["items"] or [] for row in r.json()}
        return {}
    except Exception as e:
        print(f"[market_full] erro snapshot: {e}")
        return {}


def save_snapshot_rarity(rarity: int, items: list):
    """
    Salva o snapshot de uma raridade.
    Recebe a lista já com merge aplicado (itens do full + orphans do realtime).
    """
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"rarity": rarity, "items": items, "updated_at": datetime.datetime.utcnow().isoformat()},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[market_full] erro salvar snapshot {rarity}: {r.status_code}")
    except Exception as e:
        print(f"[market_full] erro snapshot: {e}")


def save_market_history(item: dict, rarity: int, event: str, duration_minutes: int = None):
    try:
        payload = {
            "name":  item["name"],
            "rarity": rarity,
            "price": item.get("price", ""),
            "attrs": item.get("attrs", ""),
            "event": event,
        }
        if duration_minutes is not None:
            payload["duration_minutes"] = duration_minutes
        requests.post(
            f"{SUPABASE_URL}/rest/v1/market_history",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print(f"[market_full] erro histórico: {e}")


def fetch_all_pages(rarity: int) -> list:
    all_items = []
    page = 1
    MAX_PAGES = 20
    while page <= MAX_PAGES:
        url = BASE_URL.format(rarity=rarity) + f"&p={page}"
        print(f"[market_full] rarity {rarity} pág {page}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                break
        except Exception as e:
            print(f"[market_full] erro fetch: {e}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select(".mkt-row")
        if not rows:
            break
        for row in rows:
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
            all_items.append({"id": item_id, "name": name, "price": price, "attrs": attrs_text, "rarity": rarity})
        if len(rows) < 50:
            break
        page += 1
        time.sleep(0.3)
    print(f"[market_full] {len(all_items)} itens (rarity {rarity}, {page} pág.)")
    return all_items


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


def build_embed_removed(item: dict, rarity_info: dict, minutes: int) -> dict:
    return {
        "title":       f"Item Saiu — {rarity_info['label']} — {item['name']}",
        "description": f"**{item['name']}** não está mais disponível.",
        "color":       0x555555,
        "fields": [
            {"name": "📦 Item",             "value": item["name"],          "inline": True},
            {"name": "💰 Preço era",        "value": item.get("price","?"), "inline": True},
            {"name": "⏱️ Ficou disponível", "value": f"~{minutes} minutos", "inline": True},
        ],
        "footer":    {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


def run():
    if not DISCORD_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
        print("[market_full] ⚠ configuração incompleta")
        return

    users_cache = load_users_cache()
    if not users_cache:
        print("[market_full] ⚠ nenhum usuário cadastrado")
        return

    items_db = load_items_db_cache()
    snapshot = load_snapshot()
    now_ts   = int(time.time())

    for rarity_info in MARKET_URLS:
        rarity = rarity_info["rarity"]
        label  = rarity_info["label"]
        items  = fetch_all_pages(rarity)

        if not items:
            print(f"[market_full] ⚠ {label}: fetch vazio, pulando")
            continue

        prev_list = snapshot.get(str(rarity), [])
        prev_ids  = {v["id"]: v for v in prev_list}
        curr_ids  = {item["id"]: item for item in items}

        # Preserva first_seen dos itens que já estavam no snapshot
        for item in items:
            item["first_seen"] = prev_ids[item["id"]].get("first_seen", now_ts) if item["id"] in prev_ids else now_ts

        # ── MERGE: preserva itens que o realtime adicionou mas o full ainda não viu ──
        # O realtime pode ter adicionado itens à página 2+ que o full ainda não buscou
        # neste ciclo, ou itens muito recentes que apareceram entre runs.
        # Esses itens estão no snapshot atual (prev_ids) mas não na busca do full (curr_ids).
        # Antes de sobrescrever, checamos: se um item do snapshot não está no full E
        # tem first_seen recente (< 20 min), é provável que seja orphan do realtime —
        # mantemos para que o realtime não renotifique na próxima run.
        ORPHAN_TTL = 20 * 60  # 20 minutos em segundos
        orphans = [
            item for iid, item in prev_ids.items()
            if iid not in curr_ids
            and (now_ts - item.get("first_seen", 0)) < ORPHAN_TTL
        ]
        if orphans:
            print(f"[market_full] {label}: preservando {len(orphans)} orphan(s) do realtime no snapshot")

        # Lista final: itens do full + orphans do realtime
        merged_items = items + orphans

        # Salva snapshot com merge
        save_snapshot_rarity(rarity, merged_items)

        # Itens removidos = estavam no snapshot, não estão no full E não são orphans
        removed = [
            item for iid, item in prev_ids.items()
            if iid not in curr_ids
            and (now_ts - item.get("first_seen", 0)) >= ORPHAN_TTL
        ]
        for item in removed:
            minutes = max(1, (now_ts - item.get("first_seen", now_ts)) // 60)
            print(f"[market_full] ❌ {item['name']} ({label}) — {minutes} min")
            save_market_history(item, rarity, "left", minutes)
            embed = build_embed_removed(item, rarity_info, minutes)
            for discord_id, prefs in users_cache.items():
                if not should_notify(prefs, rarity, item["name"], item.get("attrs", ""), items_db):
                    continue
                ch = get_dm_channel(discord_id)
                if ch:
                    send_dm(ch, embed)
                time.sleep(0.3)

        # Registra novos no histórico (sem DM — já feito pelo realtime)
        new_items = [item for iid, item in curr_ids.items() if iid not in prev_ids]
        for item in new_items:
            save_market_history(item, rarity, "entered")

        print(f"[market_full] {label}: {len(new_items)} novos (sem DM) | {len(removed)} removidos | {len(orphans)} orphans preservados")

    print("[market_full] ✅ concluído")


if __name__ == "__main__":
    run()

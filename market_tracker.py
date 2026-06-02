"""
market_tracker.py
Monitora o market do AmonOT e envia DM no Discord quando novos itens aparecem.
Usa Supabase para persistir o snapshot — sem dependência do git.

Otimização de performance:
  - Preferências de todos os usuários são carregadas UMA VEZ no início do run
  - items_db é carregado UMA VEZ no início do run
  - Resultado: 3 queries ao Supabase por run, independente de qtd de itens/usuários
"""

import os
import time
import requests
from bs4 import BeautifulSoup

# ── Configuração ──────────────────────────────────────────────────────────────
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


# ── Supabase helpers ──────────────────────────────────────────────────────────

def supa_get(table: str, params: dict) -> list:
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=SUPA_HEADERS,
            params=params,
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        print(f"[market] erro GET {table}: {e}")
        return []


# ── Cache — carregado UMA VEZ no início do run ────────────────────────────────

def load_users_cache() -> dict:
    """
    Carrega todos os usuários com discord_id e suas preferências de uma vez.
    Retorna: { discord_id: { rarities, vocations, categories, attrs, profile_id } }
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}

    # 1. Todos os profiles com discord_id
    profiles = supa_get("profiles", {
        "discord_id": "not.is.null",
        "select":     "id,discord_id",
    })
    if not profiles:
        return {}

    profile_ids = [p["id"] for p in profiles]
    id_to_discord = {p["id"]: p["discord_id"] for p in profiles}

    # 2. Todas as notification_settings de uma vez
    settings_rows = supa_get("notification_settings", {
        "profile_id": f"in.({','.join(profile_ids)})",
        "select":     "profile_id,market_dm_rarities,market_dm_vocations,market_dm_categories,market_dm_attrs",
    })
    settings_map = {row["profile_id"]: row for row in settings_rows}

    default_prefs = {
        "rarities":   ["all"],
        "vocations":  ["all"],
        "categories": ["all"],
        "attrs":      [],
    }

    cache = {}
    for p in profiles:
        discord_id = p["discord_id"]
        if not discord_id or not discord_id.strip():
            continue
        row = settings_map.get(p["id"], {})
        cache[discord_id] = {
            "profile_id": p["id"],
            "rarities":   row.get("market_dm_rarities")   or ["all"],
            "vocations":  row.get("market_dm_vocations")  or ["all"],
            "categories": row.get("market_dm_categories") or ["all"],
            "attrs":      row.get("market_dm_attrs")      or [],
        }

    print(f"[market] cache carregado: {len(cache)} usuários")
    return cache


def load_items_db_cache() -> dict:
    """
    Carrega items_db completo de uma vez.
    Retorna: { item_name_lower: { category, vocations } }
    """
    rows = supa_get("items_db", {"select": "name,category,vocations", "limit": "3000"})
    cache = {}
    for row in rows:
        cache[row["name"].lower()] = {
            "category":  row.get("category", ""),
            "vocations": row.get("vocations") or ["all"],
        }
    print(f"[market] items_db cache: {len(cache)} itens")
    return cache


# ── Lógica de notificação (usa cache, zero queries) ───────────────────────────

def should_notify(prefs: dict, rarity: int, item_name: str, item_attrs: str, items_db: dict) -> bool:
    """
    Verifica se o usuário deve receber DM para este item.
    Usa apenas dicionários em memória — zero queries ao Supabase.
    """
    # Filtro de raridade
    if "all" not in prefs["rarities"]:
        if str(rarity) not in prefs["rarities"]:
            return False

    # Filtro de vocação
    if "all" not in prefs["vocations"]:
        db_item = items_db.get(item_name.lower())
        item_vocs = db_item["vocations"] if db_item else ["all"]
        if "all" not in item_vocs and not set(prefs["vocations"]) & set(item_vocs):
            return False

    # Filtro de categoria
    if "all" not in prefs["categories"]:
        db_item = items_db.get(item_name.lower())
        item_cat = db_item["category"] if db_item else ""
        if item_cat not in prefs["categories"]:
            return False

    # Filtro de atributos (OR) — match exato por nome do atributo
    if prefs["attrs"]:
        attrs_lower = item_attrs.lower()
        if not any(a.lower() + " lv." in attrs_lower for a in prefs["attrs"]):
            return False

    return True


# ── Snapshot ──────────────────────────────────────────────────────────────────

def load_snapshot() -> dict:
    if not SUPABASE_URL or not SUPABASE_KEY:
        return {}
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers=SUPA_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        return {str(row["rarity"]): row["items"] for row in r.json()}
    except Exception as e:
        print(f"[market] erro ao carregar snapshot: {e}")
        return {}


def save_snapshot_rarity(rarity: int, items: list):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        import datetime
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"rarity": rarity, "items": items, "updated_at": datetime.datetime.utcnow().isoformat()},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[market] erro ao salvar snapshot rarity {rarity}: {r.status_code}")
    except Exception as e:
        print(f"[market] erro ao salvar snapshot: {e}")


# ── Scraping ──────────────────────────────────────────────────────────────────

def fetch_market(rarity: int) -> list:
    all_items = []
    page = 1
    MAX_PAGES = 20

    while page <= MAX_PAGES:
        url = BASE_URL.format(rarity=rarity) + f"&p={page}"
        print(f"[market] buscando rarity {rarity} página {page}")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"[market] HTTP {r.status_code}")
                break
        except Exception as e:
            print(f"[market] erro: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select(".mkt-row")

        if not rows:
            break

        for row in rows:
            name_el  = row.select_one(".mkt-name")
            meta_el  = row.select_one(".mkt-meta")
            attrs_el = row.select_one(".mkt-attrs")
            price_el = row.select_one(".mkt-price")

            if not name_el:
                continue

            name = name_el.get_text(separator=" ", strip=True)
            for rarity_label in ["Mythical", "Legendary", "Epic", "Rare", "Uncommon", "Common"]:
                if name.endswith(rarity_label):
                    name = name[:-len(rarity_label)].strip()
                    break

            meta  = meta_el.get_text(strip=True) if meta_el else ""
            price = "?"
            if price_el:
                total_el = price_el.select_one(".mkt-total")
                price = total_el.get_text(strip=True) if total_el else price_el.get_text(strip=True)

            attrs_text = ""
            if attrs_el:
                attrs_text = attrs_el.get("title", "") or attrs_el.get_text(" ", strip=True)

            item_id = f"{name}|{price}|{attrs_text[:50]}"
            all_items.append({"id": item_id, "name": name, "meta": meta, "price": price, "attrs": attrs_text, "rarity": rarity})

        if len(rows) < 50:
            break

        page += 1
        time.sleep(0.3)

    print(f"[market] {len(all_items)} itens (rarity {rarity}, {page} pág.)")
    return all_items


# ── Discord ───────────────────────────────────────────────────────────────────

def get_dm_channel(user_id: str) -> str | None:
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers={"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"},
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    print(f"[discord] erro ao abrir DM: {r.status_code} {r.text}")
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
            retry_after = r.json().get("retry_after", 1)
            print(f"[discord] rate limit — aguardando {retry_after}s")
            time.sleep(retry_after + 0.1)
        else:
            print(f"[discord] erro ao enviar: {r.status_code} {r.text}")
            return False
    return False


def build_embed_new(item: dict, rarity_info: dict) -> dict:
    import datetime
    attrs_lines = ""
    if item["attrs"]:
        attrs = [a.strip() for a in item["attrs"].replace("·", "\n").split("\n") if a.strip()]
        attrs_lines = "\n".join(f"• {a}" for a in attrs[:8])
    description = f"**{item['meta']}**\n" if item["meta"] else ""
    if attrs_lines:
        description += f"\n📊 **Atributos:**\n{attrs_lines}"
    return {
        "title":       f"{rarity_info['emoji']} Novo item no Market! — {rarity_info['label']}",
        "description": description,
        "color":       rarity_info["color"],
        "fields": [
            {"name": "📦 Item",  "value": item["name"],  "inline": True},
            {"name": "💰 Preço", "value": item["price"], "inline": True},
        ],
        "footer":    {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


def build_embed_removed(item: dict, rarity_info: dict, minutes: int) -> dict:
    import datetime
    return {
        "title":       f"❌ Item saiu do Market — {rarity_info['label']}",
        "description": f"**{item['name']}** não está mais disponível.",
        "color":       0x555555,
        "fields": [
            {"name": "📦 Item",             "value": item["name"],          "inline": True},
            {"name": "💰 Preço era",        "value": item["price"],         "inline": True},
            {"name": "⏱️ Ficou disponível", "value": f"~{minutes} minutos", "inline": True},
        ],
        "footer":    {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }


def save_market_history(item: dict, rarity: int, event: str, duration_minutes: int = None):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        payload = {
            "name":   item["name"],
            "rarity": rarity,
            "price":  item.get("price", ""),
            "attrs":  item.get("attrs", ""),
            "event":  event,
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
        print(f"[market] erro ao salvar histórico: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[market] ⚠ DISCORD_BOT_TOKEN não configurado")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[market] ⚠ Supabase não configurado")
        return

    # Carrega TUDO em cache de uma vez — 3 queries totais para o run inteiro
    users_cache = load_users_cache()
    if not users_cache:
        print("[market] ⚠ nenhum usuário com discord_id cadastrado")
        return

    items_db    = load_items_db_cache()
    snapshot    = load_snapshot()
    now_ts      = int(time.time())

    for rarity_info in MARKET_URLS:
        rarity = rarity_info["rarity"]
        label  = rarity_info["label"]
        items  = fetch_market(rarity)

        if not items:
            print(f"[market] ⚠ {label}: fetch vazio, pulando")
            continue

        prev_ids = {v["id"]: v for v in snapshot.get(str(rarity), [])}
        curr_ids = {item["id"]: item for item in items}

        # Preserva first_seen
        for item in items:
            item["first_seen"] = prev_ids[item["id"]].get("first_seen", now_ts) if item["id"] in prev_ids else now_ts

        # Salva snapshot ANTES de notificar
        save_snapshot_rarity(rarity, items)

        # Itens novos
        new_items = [item for iid, item in curr_ids.items() if iid not in prev_ids]
        for item in new_items:
            print(f"[market] 🆕 {item['name']} ({label})")
            save_market_history(item, rarity, "entered")
            embed = build_embed_new(item, rarity_info)
            for discord_id, prefs in users_cache.items():
                if not should_notify(prefs, rarity, item["name"], item.get("attrs", ""), items_db):
                    continue
                ch = get_dm_channel(discord_id)
                if ch:
                    send_dm(ch, embed)
                time.sleep(0.3)

        # Itens removidos
        removed_items = [item for iid, item in prev_ids.items() if iid not in curr_ids]
        for item in removed_items:
            minutes = max(1, (now_ts - item.get("first_seen", now_ts)) // 60)
            print(f"[market] ❌ {item['name']} ({label}) — {minutes} min")
            save_market_history(item, rarity, "left", minutes)
            embed = build_embed_removed(item, rarity_info, minutes)
            for discord_id, prefs in users_cache.items():
                if not should_notify(prefs, rarity, item["name"], item.get("attrs", ""), items_db):
                    continue
                ch = get_dm_channel(discord_id)
                if ch:
                    send_dm(ch, embed)
                time.sleep(0.3)

        print(f"[market] {label}: {len(new_items)} novos | {len(removed_items)} removidos")

    print("[market] ✅ concluído")


if __name__ == "__main__":
    run()

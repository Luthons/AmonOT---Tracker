"""
market_tracker.py
Monitora o market do AmonOT e envia DM no Discord quando novos itens aparecem.
Usa Supabase para persistir o snapshot — sem dependência do git.
"""

import json
import os
import time
import requests
from bs4 import BeautifulSoup

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_USER_IDS = [uid.strip() for uid in os.environ.get("DISCORD_USER_ID", "").split(",") if uid.strip()]
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY    = os.environ.get("SUPABASE_SERVICE_KEY", "")

MARKET_URLS = [
    {"rarity": 3, "label": "Epic",      "color": 0xB06FE8, "emoji": "🟣"},
    {"rarity": 4, "label": "Legendary", "color": 0xE8A030, "emoji": "🟠"},
    {"rarity": 5, "label": "Mythical",  "color": 0xE84040, "emoji": "🔴"},
]

BASE_URL = "https://amonot.online/index.php?page=market&name=&sale=all&slot=&tier=&world=Baiak&rarity={rarity}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "resolution=merge-duplicates",
}


# ── Supabase Snapshot ─────────────────────────────────────────────────────────

def load_snapshot() -> dict:
    """Carrega snapshot do Supabase. Retorna dict {rarity_str: [items]}."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[market] ⚠ Supabase não configurado, usando snapshot vazio")
        return {}
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers=SUPA_HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[market] erro ao carregar snapshot: {r.status_code}")
            return {}
        rows = r.json()
        return {str(row["rarity"]): row["items"] for row in rows}
    except Exception as e:
        print(f"[market] erro ao carregar snapshot: {e}")
        return {}


def save_snapshot_rarity(rarity: int, items: list):
    """Salva snapshot de uma raridade no Supabase (upsert)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/market_snapshot",
            headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"rarity": rarity, "items": items, "updated_at": __import__("datetime").datetime.utcnow().isoformat()},
            timeout=10,
        )
        if r.status_code not in (200, 201, 204):
            print(f"[market] erro ao salvar snapshot rarity {rarity}: {r.status_code} {r.text[:100]}")
    except Exception as e:
        print(f"[market] erro ao salvar snapshot: {e}")


# ── Scraping ──────────────────────────────────────────────────────────────────

def fetch_market(rarity: int) -> list:
    url = BASE_URL.format(rarity=rarity)
    print(f"[market] buscando rarity {rarity}: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"[market] HTTP {r.status_code}")
            return []
    except Exception as e:
        print(f"[market] erro: {e}")
        return []

    soup  = BeautifulSoup(r.text, "html.parser")
    items = []

    for row in soup.select(".mkt-row"):
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
        items.append({"id": item_id, "name": name, "meta": meta, "price": price, "attrs": attrs_text, "rarity": rarity})

    print(f"[market] {len(items)} itens encontrados (rarity {rarity})")
    return items


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
    for attempt in range(3):
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
            print(f"[discord] erro ao enviar mensagem: {r.status_code} {r.text}")
            return False
    return False


def build_embed_new(item: dict, rarity_info: dict) -> dict:
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
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }


def build_embed_removed(item: dict, rarity_info: dict, minutes: int) -> dict:
    return {
        "title":       f"❌ Item saiu do Market — {rarity_info['label']}",
        "description": f"**{item['name']}** não está mais disponível.",
        "color":       0x555555,
        "fields": [
            {"name": "📦 Item",              "value": item["name"],        "inline": True},
            {"name": "💰 Preço era",         "value": item["price"],       "inline": True},
            {"name": "⏱️ Ficou disponível",  "value": f"~{minutes} minutos", "inline": True},
        ],
        "footer":    {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def get_item_vocations(item_name: str) -> list:
    """Busca vocações do item no Supabase items_db."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return ['all']
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/items_db",
            headers=SUPA_HEADERS,
            params={"name": f"ilike.{item_name}", "select": "vocations", "limit": "1"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            return r.json()[0].get("vocations", ["all"])
        return ['all']
    except Exception as e:
        print(f"[market] erro ao buscar vocações: {e}")
        return ['all']


def get_profile_id_for_discord(discord_user_id: str) -> str | None:
    """Busca profile_id pelo discord_id."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/profiles",
            headers=SUPA_HEADERS,
            params={"discord_id": f"eq.{discord_user_id}", "select": "id"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            return r.json()[0].get("id")
        return None
    except Exception as e:
        print(f"[market] erro ao buscar profile: {e}")
        return None


def get_user_market_prefs(profile_id: str) -> dict:
    """Busca preferências de DM de market do usuário."""
    default = {'rarities': ['all'], 'vocations': ['all'], 'categories': ['all'], 'attrs': []}
    if not SUPABASE_URL or not SUPABASE_KEY:
        return default
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/notification_settings",
            headers=SUPA_HEADERS,
            params={"profile_id": f"eq.{profile_id}", "select": "market_dm_rarities,market_dm_vocations,market_dm_categories,market_dm_attrs"},
            timeout=10,
        )
        if r.status_code == 200 and r.json():
            row = r.json()[0]
            return {
                'rarities':   row.get("market_dm_rarities")   or ['all'],
                'vocations':  row.get("market_dm_vocations")  or ['all'],
                'categories': row.get("market_dm_categories") or ['all'],
                'attrs':      row.get("market_dm_attrs")      or [],
            }
        return default
    except Exception as e:
        print(f"[market] erro ao buscar prefs: {e}")
        return default


def should_notify_user(discord_user_id: str, item_name: str, rarity: int, item_attrs: str) -> bool:
    """Verifica se o usuário deve receber DM para este item."""
    profile_id = get_profile_id_for_discord(discord_user_id)
    if not profile_id:
        return True  # sem perfil cadastrado, envia sempre

    prefs = get_user_market_prefs(profile_id)

    # Filtro de raridade
    if 'all' not in prefs['rarities']:
        if str(rarity) not in prefs['rarities']:
            return False

    # Filtro de vocação
    if 'all' not in prefs['vocations']:
        item_vocs = get_item_vocations(item_name)
        if 'all' not in item_vocs and not bool(set(prefs['vocations']) & set(item_vocs)):
            return False

    # Filtro de categoria
    if 'all' not in prefs['categories']:
        item_cat = ''
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                r = requests.get(f"{SUPABASE_URL}/rest/v1/items_db", headers=SUPA_HEADERS,
                    params={"name": f"ilike.{item_name}", "select": "category", "limit": "1"}, timeout=10)
                if r.status_code == 200 and r.json():
                    item_cat = r.json()[0].get("category", "")
            except: pass
        if item_cat not in prefs['categories']:
            return False

    # Filtro de atributos (OR)
    if prefs['attrs']:
        attrs_lower = item_attrs.lower()
        if not any(a.lower() in attrs_lower for a in prefs['attrs']):
            return False

    return True


def save_market_history(item: dict, rarity: int, event: str, duration_minutes: int = None):
    """Salva entrada/saída de item no histórico do Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        payload = {
            "name":    item["name"],
            "rarity":  rarity,
            "price":   item.get("price", ""),
            "attrs":   item.get("attrs", ""),
            "event":   event,
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


def run():
    if not DISCORD_TOKEN or not DISCORD_USER_IDS:
        print("[market] ⚠ DISCORD_BOT_TOKEN ou DISCORD_USER_ID não configurados")
        return

    snapshot   = load_snapshot()
    if not DISCORD_USER_IDS:
        print("[market] ⚠ nenhum DISCORD_USER_ID configurado")
        return

    now_ts = int(time.time())

    for rarity_info in MARKET_URLS:
        rarity   = rarity_info["rarity"]
        label    = rarity_info["label"]
        items    = fetch_market(rarity)
        prev_ids = {v["id"]: v for v in snapshot.get(str(rarity), [])}
        curr_ids = {item["id"]: item for item in items}

        # Itens novos
        new_items = [item for iid, item in curr_ids.items() if iid not in prev_ids]
        for item in new_items:
            item["first_seen"] = now_ts
            print(f"[market] 🆕 novo item: {item['name']} ({label})")
            save_market_history(item, rarity, "entered")
            embed = build_embed_new(item, rarity_info)
            for uid in DISCORD_USER_IDS:
                if not should_notify_user(uid, item["name"], rarity, item.get("attrs","")):
                    print(f"[market] ℹ {uid} não quer notificação para {item['name']} (vocação)")
                    continue
                ch = get_dm_channel(uid)
                if ch: send_dm(ch, embed)
                time.sleep(0.3)

        # Itens removidos
        removed_items = [item for iid, item in prev_ids.items() if iid not in curr_ids]
        for item in removed_items:
            first_seen = item.get("first_seen", now_ts)
            minutes    = max(1, (now_ts - first_seen) // 60)
            print(f"[market] ❌ item removido: {item['name']} ({label}) — ficou {minutes} min")
            save_market_history(item, rarity, "left", minutes)
            embed = build_embed_removed(item, rarity_info, minutes)
            for uid in DISCORD_USER_IDS:
                if not should_notify_user(uid, item["name"], rarity, item.get("attrs","")):
                    continue
                ch = get_dm_channel(uid)
                if ch: send_dm(ch, embed)
                time.sleep(0.3)

        # Preserva first_seen e salva imediatamente no Supabase
        for item in items:
            if item["id"] in prev_ids:
                item["first_seen"] = prev_ids[item["id"]].get("first_seen", now_ts)
            else:
                item["first_seen"] = now_ts

        save_snapshot_rarity(rarity, items)
        print(f"[market] {label}: {len(new_items)} novos | {len(removed_items)} removidos")

    print("[market] ✅ concluído")


if __name__ == "__main__":
    run()

"""
market_tracker.py
Monitora o market do AmonOT e envia DM no Discord quando novos itens aparecem.
Roda junto com o scraper principal via GitHub Actions.
"""

import json
import os
import time
import requests
from bs4 import BeautifulSoup

# ── Configuração ──────────────────────────────────────────────────────────────
DISCORD_TOKEN   = os.environ.get("DISCORD_BOT_TOKEN", "")   # GitHub Secret
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")     # GitHub Secret

MARKET_URLS = [
    {"rarity": 3, "label": "Epic",       "color": 0xB06FE8, "emoji": "🟣"},
    {"rarity": 4, "label": "Legendary",  "color": 0xE8A030, "emoji": "🟠"},
    {"rarity": 5, "label": "Mythical",   "color": 0xE84040, "emoji": "🔴"},
]

BASE_URL     = "https://amonot.online/index.php?page=market&name=&sale=all&slot=&tier=&world=Baiak&rarity={rarity}"
SNAPSHOT_PATH = "market_snapshot.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}


# ── Scraping ──────────────────────────────────────────────────────────────────

def fetch_market(rarity: int) -> list:
    """Busca itens do market para uma raridade específica."""
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

        name  = name_el.get_text(strip=True)
        meta  = meta_el.get_text(" ", strip=True) if meta_el else ""
        price = price_el.get_text(strip=True) if price_el else "?"

        # Extrai atributos do title ou do conteúdo
        attrs_text = ""
        if attrs_el:
            attrs_text = attrs_el.get("title", "") or attrs_el.get_text(" ", strip=True)

        # Monta ID único para o item (nome + preço + attrs)
        item_id = f"{name}|{price}|{attrs_text[:50]}"

        items.append({
            "id":     item_id,
            "name":   name,
            "meta":   meta,
            "price":  price,
            "attrs":  attrs_text,
            "rarity": rarity,
        })

    print(f"[market] {len(items)} itens encontrados (rarity {rarity})")
    return items


# ── Snapshot ──────────────────────────────────────────────────────────────────

def load_snapshot() -> dict:
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("[market] nenhum snapshot anterior, iniciando do zero")
        return {}


def save_snapshot(data: dict):
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Discord ───────────────────────────────────────────────────────────────────

def get_dm_channel(user_id: str) -> str | None:
    """Abre ou obtém o canal de DM com o usuário."""
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers={
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"recipient_id": user_id},
        timeout=10,
    )
    if r.status_code in (200, 201):
        return r.json()["id"]
    print(f"[discord] erro ao abrir DM: {r.status_code} {r.text}")
    return None


def send_dm(channel_id: str, embed: dict):
    """Envia uma mensagem embed no canal de DM."""
    r = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"embeds": [embed]},
        timeout=10,
    )
    if r.status_code not in (200, 201):
        print(f"[discord] erro ao enviar mensagem: {r.status_code} {r.text}")
    return r.status_code in (200, 201)


def build_embed_new(item: dict, rarity_info: dict) -> dict:
    """Monta o embed de novo item."""
    # Formata atributos
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
        "footer": {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }


def build_embed_removed(item: dict, rarity_info: dict, minutes: int) -> dict:
    """Monta o embed de item removido."""
    return {
        "title":       f"❌ Item saiu do Market — {rarity_info['label']}",
        "description": f"**{item['name']}** não está mais disponível.",
        "color":       0x555555,
        "fields": [
            {"name": "📦 Item",          "value": item["name"],  "inline": True},
            {"name": "💰 Preço era",     "value": item["price"], "inline": True},
            {"name": "⏱️ Ficou disponível", "value": f"~{minutes} minutos", "inline": True},
        ],
        "footer": {"text": "AmonOT Market Tracker · Baiak"},
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN or not DISCORD_USER_ID:
        print("[market] ⚠ DISCORD_BOT_TOKEN ou DISCORD_USER_ID não configurados")
        return

    snapshot = load_snapshot()
    dm_channel = get_dm_channel(DISCORD_USER_ID)

    if not dm_channel:
        print("[market] ❌ não foi possível abrir DM")
        return

    new_snapshot = {}
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
            if dm_channel:
                embed = build_embed_new(item, rarity_info)
                send_dm(dm_channel, embed)
                time.sleep(0.5)

        # Itens removidos
        removed_items = [item for iid, item in prev_ids.items() if iid not in curr_ids]
        for item in removed_items:
            first_seen = item.get("first_seen", now_ts)
            minutes    = max(1, (now_ts - first_seen) // 60)
            print(f"[market] ❌ item removido: {item['name']} ({label}) — ficou {minutes} min")
            if dm_channel:
                embed = build_embed_removed(item, rarity_info, minutes)
                send_dm(dm_channel, embed)
                time.sleep(0.5)

        # Mantém first_seen dos itens que continuam
        for item in items:
            if item["id"] in prev_ids:
                item["first_seen"] = prev_ids[item["id"]].get("first_seen", now_ts)
            else:
                item["first_seen"] = now_ts

        new_snapshot[str(rarity)] = items
        print(f"[market] {label}: {len(new_items)} novos | {len(removed_items)} removidos")

    save_snapshot(new_snapshot)
    print("[market] ✅ snapshot salvo")


if __name__ == "__main__":
    run()

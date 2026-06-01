"""
shop_notifier.py
Envia DM no Discord para o dono do anúncio quando alguém demonstra interesse.

Fluxo:
  - Frontend insere registro em guild_shop_interests com discord_sent=false
  - Este script roda a cada ciclo do update_realtime e envia a DM pendente
  - Marca discord_sent=true após envio
"""

import os
import time
import requests

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")

SUPA_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}

DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type":  "application/json",
}

RARITY_COLORS = {
    "Common":    0xaaaaaa,
    "Uncommon":  0x5dbf6e,
    "Rare":      0x4488ff,
    "Epic":      0x8855cc,
    "Legendary": 0xe87830,
    "Mythic":    0xc95050,
}

TYPE_LABELS = {
    "market": "Mercado da Guilda",
    "bazar":  "Bazar da Guilda",
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
        print(f"[shop_notifier] erro GET {table}: {e}")
        return []


def supa_patch(table: str, params: dict, body: dict):
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params=params,
            json=body,
            timeout=10,
        )
    except Exception as e:
        print(f"[shop_notifier] erro PATCH: {e}")


# ── Discord helpers ───────────────────────────────────────────────────────────

def get_dm_channel(user_id: str) -> str | None:
    try:
        r = requests.post(
            "https://discord.com/api/v10/users/@me/channels",
            headers=DISCORD_HEADERS,
            json={"recipient_id": user_id},
            timeout=10,
        )
        return r.json()["id"] if r.status_code in (200, 201) else None
    except Exception:
        return None


def send_dm(user_id: str, embed: dict) -> bool:
    channel_id = get_dm_channel(user_id)
    if not channel_id:
        return False
    for _ in range(3):
        r = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=DISCORD_HEADERS,
            json={"embeds": [embed]},
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 429:
            time.sleep(r.json().get("retry_after", 1) + 0.1)
        else:
            print(f"[shop_notifier] Discord erro {r.status_code}: {r.text[:200]}")
            break
    return False


# ── Notificação principal ─────────────────────────────────────────────────────

def check_pending_interests():
    # Busca interesses ainda não enviados
    interests = supa_get("guild_shop_interests", {
        "discord_sent": "eq.false",
        "select":       "id,shop_id,buyer_name,message,created_at",
    })

    if not interests:
        return

    # Busca os anúncios referenciados de uma vez
    shop_ids = list(set(i["shop_id"] for i in interests))
    shops = supa_get("guild_shop", {
        "id":     f"in.({','.join(shop_ids)})",
        "select": "id,type,item_name,quantity,unit_price,rarity,seller_profile_id,seller_name",
    })
    shop_map = {s["id"]: s for s in shops}

    # Busca discord_id dos vendedores
    seller_ids = list(set(s["seller_profile_id"] for s in shops))
    if not seller_ids:
        return
    profiles = supa_get("profiles", {
        "id":     f"in.({','.join(seller_ids)})",
        "select": "id,discord_id,display_name",
    })
    profile_map = {p["id"]: p for p in profiles}

    for interest in interests:
        shop = shop_map.get(interest["shop_id"])
        if not shop:
            # Anúncio deletado — marca como enviado para não tentar de novo
            supa_patch("guild_shop_interests", {"id": f"eq.{interest['id']}"}, {"discord_sent": True})
            continue

        seller_profile = profile_map.get(shop["seller_profile_id"])
        if not seller_profile or not seller_profile.get("discord_id"):
            print(f"[shop_notifier] ⚠ vendedor '{shop['seller_name']}' sem discord_id cadastrado")
            supa_patch("guild_shop_interests", {"id": f"eq.{interest['id']}"}, {"discord_sent": True})
            continue

        # Monta embed
        section  = TYPE_LABELS.get(shop["type"], "Guild Shop")
        color    = RARITY_COLORS.get(shop.get("rarity"), 0xC9A84C)
        price    = shop["unit_price"]
        qty      = shop["quantity"]
        price_str = f"{price:,} gp" if price > 0 else "Gratuito"
        total_str = f"{price * qty:,} gp" if price > 0 and qty > 1 else ""

        fields = [
            {"name": "📦 Item",      "value": shop["item_name"], "inline": True},
            {"name": "🔢 Qtd",       "value": str(qty),          "inline": True},
            {"name": "💰 Preço",     "value": price_str,         "inline": True},
        ]
        if total_str:
            fields.append({"name": "💰 Total", "value": total_str, "inline": True})
        if shop.get("rarity"):
            fields.append({"name": "💎 Raridade", "value": shop["rarity"], "inline": True})
        if interest.get("message"):
            fields.append({"name": "💬 Mensagem", "value": interest["message"], "inline": False})

        embed = {
            "title":       f"🛒 Novo Interesse no seu Anúncio — {section}",
            "description": f"**{interest['buyer_name']}** demonstrou interesse no seu item.",
            "color":       color,
            "fields":      fields,
            "footer":      {"text": "Lowly People · Guild Shop — Entre em contato com o interessado no jogo."},
        }

        discord_id = seller_profile["discord_id"]
        if send_dm(discord_id, embed):
            print(f"[shop_notifier] ✅ DM enviada para {shop['seller_name']} (interesse de {interest['buyer_name']})")
        else:
            print(f"[shop_notifier] ❌ falha DM para {shop['seller_name']}")

        # Marca como enviado independente do resultado
        # (evita spam se o discord_id for inválido)
        supa_patch("guild_shop_interests", {"id": f"eq.{interest['id']}"}, {"discord_sent": True})
        time.sleep(0.3)


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[shop_notifier] ⚠ DISCORD_BOT_TOKEN não configurado")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[shop_notifier] ⚠ Supabase não configurado")
        return

    check_pending_interests()
    print("[shop_notifier] ✅ concluído")


if __name__ == "__main__":
    run()

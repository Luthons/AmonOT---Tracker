"""
raffle_notifier.py
Notifica staff via DM no Discord sobre:
1. Novas solicitações de rifa pendentes
2. Rifas que atingiram o total de tickets pagos
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

STAFF_ROLES = ["admin", "lider", "vice"]


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        print(f"[raffle_notifier] erro Supabase: {e}")
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
        print(f"[raffle_notifier] erro PATCH: {e}")


def get_staff_discord_ids() -> list:
    """Busca discord_ids de todos os staff (admin, lider, vice)."""
    profiles = supa_get("profiles", {
        "role":   "in.(admin,lider,vice)",
        "select": "discord_id,display_name",
    })
    return [(p["discord_id"], p.get("display_name","")) for p in profiles if p.get("discord_id")]


def get_dm_channel(user_id: str) -> str | None:
    try:
        r = requests.post(
            "https://discord.com/api/v10/users/@me/channels",
            headers=DISCORD_HEADERS,
            json={"recipient_id": user_id},
            timeout=10,
        )
        return r.json()["id"] if r.status_code in (200, 201) else None
    except:
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
            break
    return False


def notify_staff(embed: dict):
    """Envia DM para todos os staff cadastrados."""
    staff = get_staff_discord_ids()
    for discord_id, name in staff:
        if send_dm(discord_id, embed):
            print(f"[raffle_notifier] ✅ DM enviada para {name}")
        else:
            print(f"[raffle_notifier] ❌ falha DM para {name}")
        time.sleep(0.3)


# ── Notificações ──────────────────────────────────────────────────────────────

def check_new_requests():
    """Verifica novas solicitações pendentes ainda não notificadas."""
    # Usa campo notified_at para controle — adicionamos via SQL
    reqs = supa_get("raffle_requests", {
        "status":          "eq.pending",
        "notified_staff":  "eq.false",
        "select":          "*",
    })

    if not reqs:
        return

    # Busca display_name do solicitante
    profile_ids = list(set(r["profile_id"] for r in reqs))
    profiles = supa_get("profiles", {
        "id":     f"in.({','.join(profile_ids)})",
        "select": "id,display_name",
    })
    profile_map = {p["id"]: p["display_name"] for p in profiles}

    for req in reqs:
        player_name = profile_map.get(req["profile_id"], "Desconhecido")
        rarity_colors = {
            "Common":0xaaaaaa,"Uncommon":0x5dbf6e,"Rare":0x4488ff,
            "Epic":0x8855cc,"Legendary":0xe87830,"Mythical":0xc95050,
        }
        color = rarity_colors.get(req["rarity"], 0xC9A84C)

        embed = {
            "title":       "🎟 Nova Solicitação de Rifa",
            "description": f"**{player_name}** ({req['char_name']}) solicitou uma rifa.",
            "color":       color,
            "fields": [
                {"name": "📦 Item",      "value": req["item_name"], "inline": True},
                {"name": "💎 Raridade",  "value": req["rarity"],    "inline": True},
            ],
            "footer": {"text": "Lowly People · Rifas — Acesse o site para aprovar ou recusar."},
        }
        if req.get("attributes"):
            embed["fields"].append({"name": "📊 Atributos", "value": req["attributes"], "inline": False})

        notify_staff(embed)

        # Marca como notificado
        supa_patch("raffle_requests", {"id": f"eq.{req['id']}"}, {"notified_staff": True})


def check_full_raffles():
    """Verifica rifas que atingiram o total de tickets pagos."""
    # Busca rifas abertas não marcadas como full_notified
    raffles = supa_get("raffles", {
        "status":         "eq.open",
        "full_notified":  "eq.false",
        "select":         "id,item_name,rarity,total_tickets",
    })

    if not raffles:
        return

    for raffle in raffles:
        # Conta tickets pagos
        tickets = supa_get("raffle_tickets", {
            "raffle_id": f"eq.{raffle['id']}",
            "status":    "eq.paid",
            "select":    "quantity",
        })
        paid_total = sum(t["quantity"] for t in tickets)

        if paid_total >= raffle["total_tickets"]:
            rarity_colors = {
                "Common":0xaaaaaa,"Uncommon":0x5dbf6e,"Rare":0x4488ff,
                "Epic":0x8855cc,"Legendary":0xe87830,"Mythical":0xc95050,
            }
            color = rarity_colors.get(raffle["rarity"], 0xC9A84C)

            embed = {
                "title":       "🎟 Rifa Completa!",
                "description": f"Todos os tickets da rifa foram vendidos! Hora de rifar.",
                "color":       color,
                "fields": [
                    {"name": "📦 Item",     "value": raffle["item_name"],        "inline": True},
                    {"name": "💎 Raridade", "value": raffle["rarity"],           "inline": True},
                    {"name": "🎟 Tickets",  "value": str(raffle["total_tickets"]), "inline": True},
                ],
                "footer": {"text": "Lowly People · Rifas — Realize o sorteio no Discord com compartilhamento de tela."},
            }

            notify_staff(embed)
            supa_patch("raffles", {"id": f"eq.{raffle['id']}"}, {"full_notified": True})
            print(f"[raffle_notifier] 🎟 Rifa '{raffle['item_name']}' cheia — staff notificada")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if not DISCORD_TOKEN:
        print("[raffle_notifier] ⚠ DISCORD_BOT_TOKEN não configurado")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[raffle_notifier] ⚠ Supabase não configurado")
        return

    check_new_requests()
    check_full_raffles()
    print("[raffle_notifier] ✅ concluído")


if __name__ == "__main__":
    run()

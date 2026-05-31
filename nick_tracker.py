"""
nick_tracker.py
Detecta mudanças de nick em players das listas (hunted, bonus).
Roda junto com o update_full (a cada 30min).

Como funciona:
  1. Busca /characters?name=<nome_atual>
  2. Caso normal (sem mudança): retorna .char-detail-row com a ficha do player
  3. Caso nick mudado: retorna "Você quis dizer?" com div.characters-table
     contendo o nome antigo (riscado) e o novo nome (link dourado)
  4. Atualiza hunted_list e kill_bonus_list no Supabase sem perder histórico
"""

import os
import time
import requests
from urllib.parse import quote, urlparse, parse_qs
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


# ── Supabase helpers ──────────────────────────────────────────────────────────

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


# ── Scraper ───────────────────────────────────────────────────────────────────

def fetch_character_info(name: str) -> dict:
    """
    Retorna:
      { "changed": False, "resets": int|None }              → nick igual, só atualiza resets
      { "changed": True,  "new_name": str, "resets": None } → nick mudou, novo nome detectado
      { "not_found": True }                                  → página não encontrada / erro
    """
    url = f"https://amonot.online/characters?name={quote(name)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"[nick_tracker] HTTP {r.status_code} para '{name}'")
            return {"not_found": True}

        soup = BeautifulSoup(r.text, "html.parser")

        # ── Caso 1: nick mudou — página "Você quis dizer?" ────────────────────
        # Estrutura: div.characters-table > a[href*="?name=Novo+Nome"]
        # Há dois div.page-card-body na página (form de busca + resultado),
        # então buscamos direto na div.characters-table para não depender do pai.
        char_table = soup.find("div", class_="characters-table")
        if char_table:
            if True:
                link = char_table.find("a", href=True)
                if link:
                    href = link["href"]
                    # Extrai o nome do query param: /characters?name=Leozin+Rei+Delas
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    new_name_list = qs.get("name", [])
                    if new_name_list:
                        new_name = new_name_list[0].strip()
                        if new_name.lower() != name.lower():
                            print(f"[nick_tracker] 🔄 '{name}' → '{new_name}'")
                            return {"changed": True, "new_name": new_name, "resets": None}

        # ── Caso 2: ficha normal — extrai resets ─────────────────────────────
        details = {}
        for row in soup.select(".char-detail-row"):
            label = row.select_one(".char-detail-label")
            value = row.select_one(".char-detail-value")
            if label and value:
                details[label.get_text(strip=True)] = value.get_text(strip=True)

        if details:
            try:
                resets = int(details.get("Resets", "0"))
            except Exception:
                resets = None
            return {"changed": False, "resets": resets}

        # Nem ficha nem "quis dizer" → personagem não existe mais
        print(f"[nick_tracker] ⚠ '{name}': página sem dados reconhecíveis")
        return {"not_found": True}

    except Exception as e:
        print(f"[nick_tracker] erro ao buscar '{name}': {e}")
        return {"not_found": True}


# ── Kill rankings: atualiza killer/victim quando nick muda ───────────────────

def update_kill_rankings(old_name: str, new_name: str):
    """
    Atualiza kill_rankings para preservar histórico quando um nick muda.
    Campos afetados: killer e victim.
    """
    updated = 0

    # Atualiza como killer
    r1 = requests.patch(
        f"{SUPABASE_URL}/rest/v1/kill_rankings",
        headers={**SUPA_HEADERS, "Prefer": "return=representation"},
        params={"killer": f"eq.{old_name}"},
        json={"killer": new_name},
        timeout=10,
    )
    if r1.status_code in (200, 204):
        try:
            rows = r1.json()
            if isinstance(rows, list):
                updated += len(rows)
        except Exception:
            pass

    # Atualiza como victim
    r2 = requests.patch(
        f"{SUPABASE_URL}/rest/v1/kill_rankings",
        headers={**SUPA_HEADERS, "Prefer": "return=representation"},
        params={"victim": f"eq.{old_name}"},
        json={"victim": new_name},
        timeout=10,
    )
    if r2.status_code in (200, 204):
        try:
            rows = r2.json()
            if isinstance(rows, list):
                updated += len(rows)
        except Exception:
            pass

    if updated > 0:
        print(f"[nick_tracker] 📊 kill_rankings: {updated} registros atualizados ({old_name} → {new_name})")


# ── Lógica principal por player ───────────────────────────────────────────────

def check_player(table: str, name_field: str, player_name: str, record_id: str):
    info = fetch_character_info(player_name)

    if info.get("not_found"):
        return

    if info.get("changed"):
        new_name = info["new_name"]
        update_body = {name_field: new_name}

        ok = supa_patch(table, {"id": f"eq.{record_id}"}, update_body)
        if ok:
            print(f"[nick_tracker] ✅ {table}: '{player_name}' → '{new_name}'")
            # Preserva histórico de kills/deaths
            update_kill_rankings(player_name, new_name)
        else:
            print(f"[nick_tracker] ❌ falha ao atualizar '{player_name}' em {table}")
    else:
        # Sem mudança de nick — atualiza resets se vieram
        update_body = {}
        if info.get("resets") is not None:
            update_body["resets"] = info["resets"]
        if update_body:
            supa_patch(table, {"id": f"eq.{record_id}"}, update_body)
        print(f"[nick_tracker] ✓ '{player_name}': sem mudança (resets={info.get('resets', '?')})")


# ── Entry point ───────────────────────────────────────────────────────────────

def run():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[nick_tracker] ⚠ Supabase não configurado")
        return

    # Hunted list
    hunted = supa_get("hunted_list", {"select": "id,name"})
    print(f"[nick_tracker] verificando {len(hunted)} players na hunted list...")
    for h in hunted:
        check_player("hunted_list", "name", h["name"], h["id"])
        time.sleep(0.7)

    # Bonus list
    bonus = supa_get("kill_bonus_list", {"select": "id,char_name"})
    print(f"[nick_tracker] verificando {len(bonus)} players na lista bônus...")
    for b in bonus:
        check_player("kill_bonus_list", "char_name", b["char_name"], b["id"])
        time.sleep(0.7)

    print("[nick_tracker] ✅ verificação concluída")


if __name__ == "__main__":
    run()

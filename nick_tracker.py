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


def supa_delete(table, params):
    try:
        r = requests.delete(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params=params, timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"[nick_tracker] erro DELETE: {e}")
        return False


def supa_patch(table, params, body):
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers={**SUPA_HEADERS, "Prefer": "return=minimal"},
            params=params, json=body, timeout=10,
        )
        if r.status_code not in (200, 204):
            # 409 = novo nome já existe na tabela como entrada separada
            # Solução: deletar o registro antigo (o novo já está lá)
            if r.status_code == 409:
                return "duplicate"
            print(f"[nick_tracker] PATCH {table} status={r.status_code} params={params} body={body}")
            try:
                print(f"[nick_tracker] PATCH erro: {r.json()}")
            except Exception:
                print(f"[nick_tracker] PATCH resposta: {r.text[:200]}")
            return False
        return True
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
        # Estrutura: div.page-card-body > label "Nomes Anteriores" + div.characters-table > a
        #
        # CUIDADO: div.characters-table também aparece em páginas de guilda
        # (quando o personagem não existe e o site sugere membros da guilda).
        # O discriminador obrigatório é o texto "Nomes Anteriores" no elemento pai.
        char_table = soup.find("div", class_="characters-table")
        if char_table:
            parent_text = char_table.parent.get_text(separator=" ", strip=True)
            is_nick_change = "Nomes Anteriores" in parent_text or "Former Names" in parent_text
            if is_nick_change:
                link = char_table.find("a", href=True)
                if link:
                    href = link["href"]
                    parsed = urlparse(href)
                    qs = parse_qs(parsed.query)
                    new_name_list = qs.get("name", [])
                    if new_name_list:
                        new_name = new_name_list[0].strip()
                        if new_name.lower() != name.lower():
                            print(f"[nick_tracker] 🔄 '{name}' → '{new_name}'")
                            return {"changed": True, "new_name": new_name, "resets": None}
            else:
                # characters-table presente mas sem "Nomes Anteriores" = busca fuzzy do site
                # O personagem existe com outro nome similar — não é mudança de nick rastreável
                pass

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

def check_player(table: str, name_field: str, player_name: str):
    info = fetch_character_info(player_name)

    if info.get("not_found"):
        return

    if info.get("changed"):
        new_name = info["new_name"]
        result = supa_patch(table, {name_field: f"eq.{player_name}"}, {name_field: new_name})
        if result == "duplicate":
            # Novo nome já existe como entrada separada — deleta o registro antigo
            ok = supa_delete(table, {name_field: f"eq.{player_name}"})
            if ok:
                print(f"[nick_tracker] ✅ {table}: '{player_name}' removido ('{new_name}' já estava na lista)")
            else:
                print(f"[nick_tracker] ❌ falha ao remover duplicata '{player_name}' em {table}")
        elif result:
            print(f"[nick_tracker] ✅ {table}: '{player_name}' → '{new_name}'")
            update_kill_rankings(player_name, new_name)
        else:
            print(f"[nick_tracker] ❌ falha ao atualizar '{player_name}' em {table}")
    else:
        # Sem mudança — atualiza resets se vieram
        if info.get("resets") is not None:
            supa_patch(table, {name_field: f"eq.{player_name}"}, {"resets": info["resets"]})
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
        check_player("hunted_list", "name", h["name"])
        time.sleep(0.7)

    # Bonus list
    bonus = supa_get("kill_bonus_list", {"select": "id,char_name"})
    print(f"[nick_tracker] verificando {len(bonus)} players na lista bônus...")
    for b in bonus:
        check_player("kill_bonus_list", "char_name", b["char_name"])
        time.sleep(0.7)

    print("[nick_tracker] ✅ verificação concluída")


if __name__ == "__main__":
    run()

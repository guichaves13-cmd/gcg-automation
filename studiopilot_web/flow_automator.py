"""
Flow Automator v8 — Sequential send-wait-download per prompt
=============================================================
Key changes:
  - Sequential mode: send #01 → wait → download → send #02 → ...
  - Each prompt verified individually before moving on
  - All downloads to ~/Downloads/downloads_veo3/{session_timestamp}/
  - Resume capability via status file
  - Robust DOM selectors + Playwright download events
  - Multiplier support (x1-x4)
"""
import asyncio, json, os, sys, time, argparse, base64, re
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except: pass

BASE_DIR = Path(__file__).parent.parent
VEO_CONFIG = BASE_DIR / "core" / "veo_accounts.json"
DOWNLOADS_BASE = Path(r"C:\Users\Guilherme\Downloads\downloads_veo3")
STATUS_FILE = Path(__file__).parent / "flow_status.json"
CONTROL_FILE = Path(__file__).parent / "flow_control.json"
SESSION_TAG = datetime.now().strftime("%Y%m%d_%H%M%S")

def load_cfg(acct=1):
    try:
        with open(VEO_CONFIG, encoding="utf-8-sig") as f: return json.load(f).get(f"veo{acct}", {})
    except: return {}

def save_st(s):
    with open(STATUS_FILE, "w") as f: json.dump(s, f, indent=2, ensure_ascii=False)

def check_control():
    try:
        with open(CONTROL_FILE) as f: return json.load(f).get("action", "run")
    except: return "run"

def clear_control():
    try: os.remove(CONTROL_FILE)
    except: pass


async def run(prompts, account=1, model="veo31lite", mult=1):
    from playwright.async_api import async_playwright
    cfg = load_cfg(account)
    if not cfg.get("connected"):
        print("[ERROR] Conta nao conectada. Configure cookies primeiro.")
        return

    pw_cookies = []
    for c in cfg.get("cookies", []):
        ck = {"name": c["name"], "value": c["value"],
              "domain": c.get("domain","labs.google"), "path": c.get("path","/")}
        if c.get("expirationDate"): ck["expires"] = c["expirationDate"]
        if c.get("secure"): ck["secure"] = True
        if c.get("httpOnly"): ck["httpOnly"] = True
        ss = str(c.get("sameSite","lax")).lower()
        ck["sameSite"] = {"lax":"Lax","strict":"Strict","none":"None"}.get(ss,"Lax")
        pw_cookies.append(ck)

    # Session download folder
    session_dir = DOWNLOADS_BASE / f"{SESSION_TAG}_{model}"
    session_dir.mkdir(parents=True, exist_ok=True)
    clear_control()

    total = len(prompts)
    _MODEL_NAMES = {
        "veo31lite": "Veo 3.1 - Lite",
        "veo31": "Veo 3.1",
        "nanobanana": "Nano Banana",
        "geminiomni": "Gemini Omni",
    }
    model_name = _MODEL_NAMES.get(model, "Veo 3.1 - Lite")
    mult = max(1, min(4, int(mult)))

    st = {
        "running": True, "phase": "setup",
        "account": account, "total": total,
        "sent": 0, "completed": 0, "failed": 0,
        "downloads": [], "model": model_name,
        "started_at": time.strftime("%H:%M:%S"),
        "log": [], "paused": False,
        "session_dir": str(session_dir),
        "session_tag": SESSION_TAG,
    }
    save_st(st)

    def log(msg):
        safe = str(msg).encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        try: print(safe)
        except: pass
        st["log"] = (st.get("log",[]) + [{"t":time.strftime("%H:%M:%S"),"m":safe}])[-100:]
        save_st(st)

    def get_btn_js():
        return """() => {
            const btns = Array.from(document.querySelectorAll('button'));
            for (const btn of btns) {
                const r = btn.getBoundingClientRect();
                const h = window.innerHeight;
                if (r.y > h * 0.65 && r.x > 300 && r.width > 60 && r.height > 18 && r.height < 55) {
                    const t = (btn.textContent||'').replace(/[^a-zA-Z0-9 .x\\/]/g,'').trim().substring(0,30);
                    return {cx: r.x + r.width/2, cy: r.y + r.height/2, t};
                }
            }
            return null;
        }"""

    log(f"{'='*60}")
    log(f"  FLOW AUTOMATOR v8 — SEQUENCIAL")
    log(f"  {total} prompts | {model_name} | x{mult}")
    log(f"  Download: {session_dir}")
    log(f"{'='*60}")

    async with async_playwright() as p:
        headless_mode = os.getenv("FLOW_HEADLESS", "0") == "1"
        
        # ═══ STRATEGY: Use dedicated Playwright profile with auto-cookie sync ═══
        # This profile persists between runs, so login state survives restarts.
        # We NEVER use the main Chrome profile (it locks when Chrome is open).
        
        playwright_profile = BASE_DIR / "core" / "playwright_profile"
        playwright_profile.mkdir(parents=True, exist_ok=True)
        
        use_profile = not headless_mode
        
        if use_profile:
            log("[NAV] Usando perfil Playwright persistente (sessao permanente)...")
            try:
                browser = await p.chromium.launch_persistent_context(
                    user_data_dir=str(playwright_profile),
                    channel="chrome",
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                    viewport={"width": 1400, "height": 900},
                    accept_downloads=True,
                )
                ctx = browser  # persistent context IS the context
                page = await ctx.new_page()
                
                # ALWAYS inject cookies from config — this is the definitive fix.
                # Even if the profile has old cookies, we overwrite with fresh ones
                # from veo_accounts.json every time. This prevents stale sessions.
                if pw_cookies:
                    log("[COOKIES] Injetando cookies frescos no perfil...")
                    await ctx.add_cookies(pw_cookies)
                    log(f"[COOKIES] {len(pw_cookies)} cookies injetados com sucesso!")
                else:
                    log("[COOKIES] Nenhum cookie no config — usando sessao existente do perfil.")
                        
            except Exception as e:
                log(f"[NAV] Perfil persistente falhou ({str(e)[:50]}), usando fallback...")
                use_profile = False
        
        if not use_profile:
            browser = await p.chromium.launch(
                headless=headless_mode,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-gpu" if headless_mode else "--start-minimized",
                ]
            )
            ctx = await browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                accept_downloads=True,
            )
            await ctx.add_cookies(pw_cookies)
            page = await ctx.new_page()

        
        # Catch JS errors from Flow page
        js_errors = []
        page.on("console", lambda msg: js_errors.append(f"[JS {msg.type}] {msg.text[:100]}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: js_errors.append(f"[PAGE_ERROR] {str(err)[:100]}"))
        log("[NAV] Conectado ao Google Flow...")

        # Open page — try multiple URL patterns for Google Flow 2026
        flow_urls = [
            "https://labs.google/fx/pt/tools/flow",
            "https://labs.google.com/fx/pt/tools/flow",
            "https://labs.google/fx/tools/flow",
        ]
        page_loaded = False
        for flow_url in flow_urls:
            try:
                await page.goto(flow_url, wait_until="domcontentloaded", timeout=45000)
                page_loaded = True
                break
            except Exception as e:
                log(f"[NAV] URL {flow_url} falhou: {str(e)[:40]}, tentando proxima...")
                continue
        
        if not page_loaded:
            log("[NAV] Todas as URLs falharam. Tentando URL padrao...")
            try:
                await page.goto(flow_urls[0], wait_until="commit", timeout=60000)
            except Exception as e:
                log(f"[NAV] Timeout/erro no load: {str(e)[:60]}")
        
        await asyncio.sleep(7)  # Wait longer for Flow to fully initialize

        # Detect hard rate limits (Error code 253) immediately on load
        try:
            page_text = await page.evaluate("document.body.innerText || ''")
            if "quota limit" in page_text.lower() or "error code 253" in page_text.lower():
                log("[FATAL] Conta bloqueada por limite de cota do Google (Error 253)!")
                log("[DICA] O limite diario do Google foi atingido. Use outra conta ou aguarde 24h.")
                st["running"] = False
                st["phase"] = "error"
                st["error"] = "Limite de cota excedido no Google (Error 253). Troque de conta."
                save_st(st)
                await browser.close()
                return
        except: pass

        # Detect login redirect (expired cookies) — with retry
        current_url = page.url.lower()
        is_login_page = "accounts.google.com" in current_url or "signin" in current_url or "login" in current_url
        
        if is_login_page:
            log("[COOKIES] Detectado redirect para login. Tentando reinjetar cookies...")
            
            # Re-read cookies from file (user might have updated them)
            fresh_cfg = load_cfg(account)
            fresh_cookies = []
            for c in fresh_cfg.get("cookies", []):
                ck = {"name": c["name"], "value": c["value"],
                      "domain": c.get("domain","labs.google"), "path": c.get("path","/")}
                if c.get("expirationDate"): ck["expires"] = c["expirationDate"]
                if c.get("secure"): ck["secure"] = True
                if c.get("httpOnly"): ck["httpOnly"] = True
                ss = str(c.get("sameSite","lax")).lower()
                ck["sameSite"] = {"lax":"Lax","strict":"Strict","none":"None"}.get(ss,"Lax")
                fresh_cookies.append(ck)
            
            if fresh_cookies:
                await ctx.add_cookies(fresh_cookies)
                log(f"[COOKIES] {len(fresh_cookies)} cookies reinjetados. Recarregando...")
                await page.goto(flow_urls[0], wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(7)
                current_url = page.url.lower()
            
            # Check again after retry
            if "accounts.google.com" in current_url or "signin" in current_url:
                # Last resort: clear profile and try clean injection
                log("[COOKIES] Ainda no login. Limpando perfil e tentando de novo...")
                await ctx.clear_cookies()
                if fresh_cookies:
                    await ctx.add_cookies(fresh_cookies)
                elif pw_cookies:
                    await ctx.add_cookies(pw_cookies)
                await page.goto(flow_urls[0], wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(7)
                current_url = page.url.lower()
                
                if "accounts.google.com" in current_url or "signin" in current_url:
                    log("[ERRO] Cookies expirados! Re-conecte a conta com cookies novos.")
                    log("[DICA] Feche o Chrome completamente e rode novamente - usará seu perfil logado.")
                    st["running"] = False
                    st["phase"] = "error"
                    st["error"] = "Cookies expirados! Feche o Chrome e rode novamente, ou cole cookies novos."
                    save_st(st)
                    await browser.close()
                    return

        # Ensure we're on a project page
        if "/project/" not in current_url:
            log("[NAV] Redirecionando para Flow...")
            for sel in ['a[href*="/flow"]', 'button:has-text("Flow")', 'button:has-text("Criar")', 'a[href*="/fx/pt"]']:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        await asyncio.sleep(3)
                        break
                except: continue

            # Detect login redirect again after any click
            if "accounts.google.com" in page.url.lower():
                log("[ERRO] Cookies expirados! Re-conecte a conta com cookies novos.")
                st["running"] = False; st["phase"] = "error"; st["error"] = "Cookies expirados!"
                save_st(st); await browser.close(); return

            # Click "New project" or enter existing
            for sel in ['button[aria-label*="Novo"]', 'button:has-text("+")', 'button:has-text("Novo")', 'button:has-text("New")']:
                try:
                    btn = await page.wait_for_selector(sel, timeout=3000)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await asyncio.sleep(3)
                        log("[NAV] Novo projeto criado!")
                        break
                except: continue

            # Fallback: click first project card
            if "/project/" not in page.url:
                try:
                    cards = await page.query_selector_all('a[href*="/project/"]')
                    if cards:
                        await cards[0].click()
                        await asyncio.sleep(3)
                except: pass

        log(f"[NAV] URL: {page.url[:100]}")

        # ===== CONFIGURE MODEL & SETTINGS =====
        st["phase"] = "config"
        save_st(st)
        log(f"\n[CFG] Configurando: {model_name} x{mult}...")

        ss_dir = Path(__file__).parent
        async def snap(name):
            try:
                p = str(ss_dir / f"debug_{name}_{SESSION_TAG}.png")
                await page.screenshot(path=p, full_page=False)
            except: pass

        async def check_popover_open():
            """Check if popover tabs (Imagem/Vídeo) are visible"""
            return await page.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    const t = (el.textContent || '').trim().toLowerCase();
                    if ((t.includes('imagem') || t.includes('vídeo')) && r.width > 40 && r.width < 200 && r.height > 15 && r.height < 60)
                        return true;
                }
                return false;
            }""")

        is_video = model in ("veo31lite", "veo31", "geminiomni")  # All video models

        # --- Open popover (with retry) ---
        for attempt in range(3):
            pop = await page.evaluate(get_btn_js())
            if pop:
                await page.mouse.click(pop['cx'], pop['cy'])
                await asyncio.sleep(2)
            else:
                h = await page.evaluate("window.innerHeight")
                w = await page.evaluate("window.innerWidth")
                await page.mouse.click(w * 0.7, h * 0.85)
                await asyncio.sleep(2)

            if await check_popover_open():
                log(f"[CFG] Popover aberto (tentativa {attempt+1})")
                break
            log(f"[CFG] Popover NAO abriu, tentativa {attempt+1}")
        else:
            log("[ERRO] Nao foi possivel abrir o popover!")
        await snap("1_apos_popover")

        # --- Click tab (with retry) ---
        tab_patterns = ["vídeo", "video"] if is_video else ["imagem", "image", "imagen"]
        for attempt in range(3):
            tab_clicked = False
            for tab_pattern in tab_patterns:
                tab = await page.evaluate(f"""(txt) => {{
                    const all = document.querySelectorAll('*');
                    for (const el of all) {{
                        const r = el.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        const t = (el.textContent || '').trim().toLowerCase();
                        if (t.includes(txt) && r.width > 30 && r.width < 200 && r.height > 15 && r.height < 60)
                            return {{cx: r.x + r.width/2, cy: r.y + r.height/2, t: t.substring(0,30)}};
                    }}
                    return null;
                }}""", tab_pattern)
                if tab:
                    await page.mouse.click(tab['cx'], tab['cy'])
                    await asyncio.sleep(1.5)
                    tab_clicked = True
                    break

            if not tab_clicked:
                h = await page.evaluate("window.innerHeight")
                w = await page.evaluate("window.innerWidth")
                cx = w * 0.5 + (60 if is_video else -60)
                await page.mouse.click(cx, h * 0.65)
                await asyncio.sleep(1.5)

            if tab_clicked:
                log(f"[CFG] Tab clicada (tentativa {attempt+1})")
                break
        await snap("2_apos_tab")

        # --- Model: select "Veo 3.1 Lite Lower Priority" ---
        if model in ("veo31lite", "veo31", "geminiomni", "nanobanana"):
            model_selected = False
            # Build search keywords based on selected model
            if model == "geminiomni":
                model_keywords = ['omni', 'gemini omni']
            elif model == "nanobanana":
                model_keywords = ['banana', 'nano']
            elif model == "veo31":
                model_keywords = ['veo', '3.1']
            else:  # veo31lite
                model_keywords = ['lite', 'lower', 'menor', 'low priority']

            for attempt in range(3):
                # Click dropdown arrow/model name
                dd_coord = await page.evaluate("""() => {
                    const all = document.querySelectorAll('*');
                    const h = window.innerHeight;
                    for (const el of all) {
                        const r = el.getBoundingClientRect();
                        const t = (el.textContent || '').trim();
                        if ((t === 'arrow_drop_down' || t.includes('arrow_drop_down')) && r.width > 10 && r.width < 70)
                            return {cx: r.x + r.width/2, cy: r.y + r.height/2, found: 'arrow'};
                        if (r.y > h*0.3 && r.y < h*0.9 && r.height > 15 && r.height < 50 && r.width > 50 && r.width < 300
                            && (t.toLowerCase().includes('banana') || t.toLowerCase().includes('nano') || t.toLowerCase().includes('veo') || t.toLowerCase().includes('lite') || t.toLowerCase().includes('omni')))
                            return {cx: r.x + r.width/2, cy: r.y + r.height/2, found: 'modelname'};
                    }
                    return null;
                }""")
                if dd_coord:
                    await page.mouse.click(dd_coord['cx'], dd_coord['cy'])
                    await asyncio.sleep(2)

                await snap("3_apos_dropdown")

                # Find the target model option in dropdown
                search_kws_json = json.dumps(model_keywords)
                lite_opt = await page.evaluate(f"""(keywords) => {{
                    const all = document.querySelectorAll('*');
                    const h = window.innerHeight;
                    let best = null;
                    for (const el of all) {{
                        const r = el.getBoundingClientRect();
                        const t = (el.textContent || '').trim().toLowerCase();
                        if (r.y > h*0.3 && r.y < h && r.height > 15 && r.height < 60 && r.width > 80 && r.width < 300) {{
                            let score = 0;
                            for (const kw of keywords) {{
                                if (t.includes(kw)) score += 2;
                            }}
                            if ((t.includes('low') && t.includes('priority')) || (t.includes('menor') && t.includes('prioridade'))) score += 3;
                            else if (t.includes('prioridade') || t.includes('0 credito') || t.includes('grátis') || t.includes('gratuito') || t.includes('free')) score += 1;
                            if (score && (!best || score > best.score))
                                best = {{cx: r.x + r.width/2, cy: r.y + r.height/2, t: t.substring(0,40), score}};
                        }}
                    }}
                    return best;
                }}""", model_keywords)

                if lite_opt:
                    await page.mouse.click(lite_opt['cx'], lite_opt['cy'])
                    await asyncio.sleep(1)
                    log(f"[CFG] Modelo: '{lite_opt.get('t','')}' (score={lite_opt.get('score')})")
                    model_selected = True
                    break
                else:
                    # Fallback: click last option
                    last_opt = await page.evaluate("""() => {
                        const all = document.querySelectorAll('*');
                        const h = window.innerHeight;
                        let last = null;
                        for (const el of all) {
                            const r = el.getBoundingClientRect();
                            const t = (el.textContent || '').trim().toLowerCase();
                            if (r.y > h*0.3 && r.y < h && r.height > 15 && r.height < 60 && r.width > 80 && r.width < 300 && t.length > 2) {
                                if (!last || r.y > last.y)
                                    last = {cx: r.x + r.width/2, cy: r.y + r.height/2, t: t.substring(0,40), y: r.y};
                            }
                        }
                        return last;
                    }""")
                    if last_opt:
                        await page.mouse.click(last_opt['cx'], last_opt['cy'])
                        await asyncio.sleep(1)
                        log(f"[CFG] Dropdown fallback: '{last_opt.get('t','')}'")
                        model_selected = True
                        break

                log(f"[CFG] Modelo nao selecionado, tentativa {attempt+1}")
            await snap("4_apos_modelo")

        # --- Click multiplier (with 3 retries) ---
        mult_label = f"{mult}x"
        for attempt in range(3):
            mult_found = await page.evaluate(f"""(target) => {{
                const all = document.querySelectorAll('*');
                const h = window.innerHeight;
                for (const el of all) {{
                    const r = el.getBoundingClientRect();
                    const t = (el.textContent || '').trim();
                    if (r.y > h*0.3 && r.y < h*0.9 && r.width > 20 && r.width < 120 && r.height > 15) {{
                        if (t.replace(/[^0-9xX]/g,'').toLowerCase() === target)
                            return {{cx: r.x + r.width/2, cy: r.y + r.height/2}};
                    }}
                }}
                return null;
            }}""", mult_label)
            if mult_found:
                await page.mouse.click(mult_found['cx'], mult_found['cy'])
                log(f"[CFG] Multiplicador: x{mult} em ({mult_found['cx']:.0f},{mult_found['cy']:.0f})")
                break
            # Fallback: click by coordinate
            h = await page.evaluate("window.innerHeight")
            await page.mouse.click(460 + 66 * (mult - 1), int(h * 0.76))
            await asyncio.sleep(0.5)
        await snap("5_apos_mult")

        # --- Close popover ---
        await page.keyboard.press("Escape")
        await asyncio.sleep(1)

        # ===== SEQUENTIAL: SEND → WAIT → DOWNLOAD → NEXT =====
        st["phase"] = "sending"
        save_st(st)
        log(f"\n{'='*60}")
        log(f"  MODO SEQUENCIAL — {total} prompts")
        log(f"{'='*60}")

        # ===== SEND ALL PROMPTS (with retry + rate-limit detection) =====
        consecutive_fails = 0
        MAX_CONSECUTIVE_FAILS = 5
        
        for i, prompt in enumerate(prompts):
            idx = i + 1
            ctrl = check_control()
            while ctrl == "pause":
                st["paused"] = True; save_st(st)
                log("  [PAUSADO]")
                await asyncio.sleep(3)
                ctrl = check_control()
            st["paused"] = False
            if ctrl == "cancel":
                log("[CANCELADO]")
                break

            # Abort if too many consecutive failures (likely blocked)
            if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                log(f"[ERRO] {MAX_CONSECUTIVE_FAILS} falhas consecutivas! Possivel bloqueio.")
                log("[DICA] Aguardando 60s antes de tentar novamente...")
                await asyncio.sleep(60)
                await page.reload()
                await asyncio.sleep(5)
                consecutive_fails = 0  # Reset after cooldown

            st["current"] = idx
            st["current_prompt"] = prompt[:80]
            st["phase"] = f"sending_{idx}"
            save_st(st)

            log(f"\n--- [{idx:03d}/{total:03d}] {prompt[:80]} ---")

            prompt_sent = False
            for retry in range(3):
                try:
                    input_el = None
                    for sel in ['[data-slate-editor="true"]', '[contenteditable="true"]', 'textarea', 'div[role="textbox"]', 'input[type="text"]']:
                        try:
                            el = await page.wait_for_selector(sel, timeout=5000)
                            if el and await el.is_visible():
                                input_el = el
                                break
                        except: continue

                    if not input_el:
                        if retry < 2:
                            log(f"  [#{idx}] Input nao encontrado, retry {retry+1}...")
                            await page.reload()
                            await asyncio.sleep(5)
                            continue
                        else:
                            log(f"  [#{idx}] Input nao encontrado apos 3 tentativas!")
                            st["failed"] = st.get("failed", 0) + 1
                            consecutive_fails += 1
                            save_st(st)
                            break

                    await input_el.click()
                    await asyncio.sleep(0.2)
                    
                    # Use execCommand to clear and type into Slate editor (React state safe)
                    await page.evaluate(f"""(text) => {{
                        document.execCommand('selectAll', false, null);
                        document.execCommand('delete', false, null);
                        document.execCommand('insertText', false, text);
                    }}""", prompt)
                    
                    # Fallback keypress just in case it needs an event trigger
                    await input_el.press("Space")
                    await input_el.press("Backspace")
                    await asyncio.sleep(0.3)

                    # Detect rate-limit messages
                    rate_limited = await page.evaluate("""() => {
                        const body = document.body.innerText.toLowerCase();
                        return body.includes('rate limit') || body.includes('too many') || body.includes('aguarde') || body.includes('limite') || body.includes('quota limit');
                    }""")
                    if rate_limited:
                        page_text = await page.evaluate("document.body.innerText.toLowerCase()")
                        if "error code 253" in page_text or "quota limit" in page_text:
                            log(f"  [#{idx}] [FATAL] Bloqueio definitivo de cota do Google (Error 253). Abortando.")
                            st["running"] = False
                            st["phase"] = "error"
                            st["error"] = "Limite de cota excedido (Error 253)."
                            save_st(st)
                            await browser.close()
                            return
                            
                        wait_time = 30 + (retry * 30)
                        log(f"  [#{idx}] Rate-limit temporario! Aguardando {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    # Click send button at bottom-right
                    sent = False
                    for sel in ['button[aria-label*="Enviar"]', 'button:has-text("Criar")', 'button[aria-label*="Send"]']:
                        try:
                            btn = await page.wait_for_selector(sel, timeout=2000)
                            if btn and await btn.is_visible():
                                await btn.click(); sent = True; break
                        except: continue
                    if not sent:
                        try:
                            btns = await page.query_selector_all('button')
                            for btn in reversed(btns):
                                box = await btn.bounding_box()
                                if box and box["y"] > 750 and box["x"] > 900:
                                    await btn.click(); sent = True; break
                        except: pass
                    if not sent:
                        await page.keyboard.press("Enter")

                    log(f"  [#{idx}] Prompt enviado!")
                    st["sent"] = idx
                    prompt_sent = True
                    consecutive_fails = 0  # Reset on success
                    save_st(st)
                    break

                except Exception as e:
                    err_msg = str(e)[:100]
                    if retry < 2:
                        log(f"  [#{idx}] Erro retry {retry+1}: {err_msg}")
                        await asyncio.sleep(3)
                    else:
                        log(f"  [#{idx}] ERRO final: {err_msg}")
                        st["failed"] = st.get("failed", 0) + 1
                        consecutive_fails += 1
                        save_st(st)

            # Log any JS errors
            if js_errors:
                for je in js_errors[-3:]:
                    log(f"  [#{idx}] JS: {je}")

            # Wait before next prompt (adaptive: longer if we had issues)
            wait = 2 if prompt_sent else 5
            await asyncio.sleep(wait)

        # ===== SAVE FRESH COOKIES (auto-refresh so they never expire) =====
        try:
            fresh = await ctx.cookies()
            if fresh:
                cfg_key = f"veo{account}"
                config_path = VEO_CONFIG
                if config_path.exists():
                    cfg_all = json.loads(config_path.read_text(encoding='utf-8'))
                else:
                    cfg_all = {}
                if cfg_key not in cfg_all:
                    cfg_all[cfg_key] = {}
                cfg_all[cfg_key]["cookies"] = fresh
                cfg_all[cfg_key]["connected"] = True
                cfg_all[cfg_key]["connected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                config_path.write_text(json.dumps(cfg_all, indent=2, ensure_ascii=False), encoding='utf-8')
                log(f"[COOKIES] Cookies salvos automaticamente ({len(fresh)} cookies)")
        except Exception as e:
            log(f"[COOKIES] Erro ao salvar cookies: {str(e)[:60]}")

        # ===== FINAL SUMMARY =====
        st["running"] = False
        st["phase"] = "done"
        st["finished_at"] = time.strftime("%H:%M:%S")
        save_st(st)

        total_dl = len(st["downloads"])
        total_fail = st["failed"]
        log(f"\n{'='*60}")
        log(f"  FINALIZADO!")
        log(f"  Total prompts: {total}")
        log(f"  Downloads OK:  {total_dl}")
        log(f"  Falhas:        {total_fail}")
        log(f"  Pasta:         {session_dir}")
        log(f"{'='*60}")

        await browser.close()


def main():
    ap = argparse.ArgumentParser(description="Flow Automator v8 — Sequential Veo 3.1 generation")
    ap.add_argument("--file", type=str, help="Arquivo com prompts (1 por linha)")
    ap.add_argument("--account", type=int, default=1, help="Numero da conta (1 ou 2)")
    ap.add_argument("--model", type=str, default="veo31lite", choices=["veo31lite", "veo31", "nanobanana", "geminiomni"])
    ap.add_argument("--mult", type=int, default=1, help="Videos por prompt (1-4)")
    ap.add_argument("--status", action="store_true", help="Mostrar status da automacao atual")
    args = ap.parse_args()

    if args.status:
        try:
            with open(STATUS_FILE) as f:
                print(json.dumps(json.load(f), indent=2, ensure_ascii=False))
        except:
            print("Nenhuma automacao rodando no momento.")
        return

    if not args.file:
        print("Uso: python flow_automator.py --file prompts.txt --model veo31lite")
        return

    with open(args.file, encoding="utf-8") as f:
        prompts = [l.strip() for l in f if l.strip()]
    if not prompts:
        print("[ERRO] Nenhum prompt encontrado no arquivo.")
        return

    print(f"{len(prompts)} prompts carregados de {args.file}")
    try:
        asyncio.run(run(prompts, args.account, args.model, args.mult))
    except Exception as e:
        err = str(e)[:200]
        print(f"[FATAL] {err}")
        try:
            save_st({"running": False, "phase": "error", "error": err, "finished_at": time.strftime("%H:%M:%S")})
        except:
            pass


if __name__ == "__main__":
    main()

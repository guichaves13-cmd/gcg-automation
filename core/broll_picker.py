"""
B-Roll Picker — static HTML generator for manual review of B-roll choices.

Given a beat_timeline.json (from core.beat_timeline), generates an interactive
index.html where the user can:
  - See each B-roll segment with its narration text and search terms
  - Preview the chosen video/image inline
  - Mark beats as 'approved' / 'reject' / 'replace' (status saved to a sidecar JSON)
  - Open the file in Explorer to swap manually

This is OPTIONAL — the auto pipeline (Phase 2 validation) handles 90% of cases.
The picker is for the perfectionist tier where the user wants every beat to be just right.
"""

import json
import os
from datetime import datetime
from html import escape


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>B-Roll Picker — {theme}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0; padding: 0; background: #0e1117; color: #e6edf3;
  }}
  header {{
    position: sticky; top: 0; z-index: 10;
    padding: 16px 24px; background: #161b22; border-bottom: 1px solid #30363d;
    display: flex; align-items: center; justify-content: space-between;
  }}
  header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  header .meta {{ font-size: 13px; color: #8b949e; }}
  .stats {{ display: flex; gap: 18px; }}
  .stat {{ display: flex; flex-direction: column; align-items: center; }}
  .stat .num {{ font-size: 20px; font-weight: 700; color: #58a6ff; }}
  .stat .lbl {{ font-size: 11px; color: #8b949e; text-transform: uppercase; }}
  main {{ padding: 24px; max-width: 1400px; margin: 0 auto; }}
  .controls {{ margin-bottom: 16px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .controls button {{
    padding: 6px 14px; border-radius: 6px; border: 1px solid #30363d;
    background: #21262d; color: #e6edf3; cursor: pointer; font-size: 13px;
  }}
  .controls button:hover {{ border-color: #58a6ff; }}
  .beat {{
    margin-bottom: 18px; padding: 14px; background: #161b22;
    border: 1px solid #30363d; border-radius: 8px;
    display: grid; grid-template-columns: 420px 1fr; gap: 18px;
  }}
  .beat.approved {{ border-color: #2ea043; }}
  .beat.rejected {{ border-color: #da3633; opacity: 0.55; }}
  .beat.replace  {{ border-color: #d29922; }}
  .preview {{ background: #000; border-radius: 6px; overflow: hidden; aspect-ratio: 16/9; }}
  .preview video, .preview img {{ width: 100%; height: 100%; object-fit: cover; }}
  .preview .missing {{
    display: flex; align-items: center; justify-content: center; height: 100%;
    color: #8b949e; font-size: 13px;
  }}
  .details {{ display: flex; flex-direction: column; gap: 8px; }}
  .badges {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  .badge {{
    padding: 2px 8px; border-radius: 999px; font-size: 11px;
    background: #1f6feb33; color: #79c0ff; border: 1px solid #1f6feb;
  }}
  .badge.shot {{ background: #6e768133; color: #adbac7; border-color: #6e7681; }}
  .badge.mood {{ background: #a371f733; color: #d2a8ff; border-color: #a371f7; }}
  .badge.src  {{ background: #1f883d33; color: #56d364; border-color: #1f883d; }}
  .badge.score-high {{ background: #1f883d33; color: #56d364; border-color: #1f883d; }}
  .badge.score-low  {{ background: #da363333; color: #ff7b72; border-color: #da3633; }}
  .narration {{
    padding: 10px 12px; background: #0d1117; border-left: 3px solid #58a6ff;
    border-radius: 4px; font-size: 13px; line-height: 1.55; color: #c9d1d9;
  }}
  .terms {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .term {{
    padding: 3px 8px; background: #0d1117; border: 1px solid #30363d;
    border-radius: 4px; font-size: 12px; color: #79c0ff; font-family: monospace;
  }}
  .actions {{ display: flex; gap: 8px; margin-top: 8px; }}
  .actions button {{
    flex: 1; padding: 8px; border-radius: 6px; border: 1px solid #30363d;
    background: #21262d; color: #e6edf3; cursor: pointer; font-size: 13px;
    transition: all 0.15s;
  }}
  .actions button:hover {{ transform: translateY(-1px); }}
  .btn-approve {{ border-color: #2ea043 !important; color: #56d364 !important; }}
  .btn-approve:hover {{ background: #2ea04333 !important; }}
  .btn-reject {{ border-color: #da3633 !important; color: #ff7b72 !important; }}
  .btn-reject:hover {{ background: #da363333 !important; }}
  .btn-replace {{ border-color: #d29922 !important; color: #e3b341 !important; }}
  .btn-replace:hover {{ background: #d2992233 !important; }}
  .filename {{ font-size: 11px; color: #6e7681; font-family: monospace; word-break: break-all; }}
  footer {{ padding: 20px 24px; text-align: center; color: #6e7681; font-size: 12px; }}
  #export {{
    position: fixed; bottom: 20px; right: 20px; padding: 12px 20px;
    background: #1f6feb; color: white; border: none; border-radius: 8px;
    cursor: pointer; font-size: 14px; font-weight: 600;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }}
  #apply-btn {{
    position: fixed; bottom: 20px; right: 220px; padding: 12px 20px;
    background: #2ea043; color: white; border: none; border-radius: 8px;
    cursor: pointer; font-size: 14px; font-weight: 600;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }}
  #apply-btn:hover {{ background: #3fb950; }}
  #apply-panel {{
    display: none; position: fixed; bottom: 70px; right: 20px;
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 18px; width: 390px; z-index: 50;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  }}
  #apply-panel h3 {{ margin: 0 0 12px; font-size: 15px; color: #e6edf3; }}
  #apply-panel label {{ font-size: 12px; color: #8b949e; display: block; margin-bottom: 4px; }}
  #apply-panel input[type=text] {{
    width: 100%; padding: 7px 10px; background: #0d1117; border: 1px solid #30363d;
    border-radius: 6px; color: #e6edf3; font-size: 12px; margin-bottom: 10px;
    box-sizing: border-box;
  }}
  #apply-panel .panel-row {{ display: flex; gap: 8px; margin-top: 10px; }}
  #apply-panel .panel-row button {{ flex: 1; padding: 9px; border-radius: 6px; border: none;
    cursor: pointer; font-size: 13px; font-weight: 600; }}
  #btn-do-apply {{ background: #2ea043; color: white; }}
  #btn-do-apply:hover {{ background: #3fb950; }}
  #btn-analyze {{ background: #1f6feb; color: white; }}
  #btn-analyze:hover {{ background: #388bfd; }}
  #apply-status {{ margin-top: 10px; font-size: 12px; min-height: 18px; }}
  .replace-file-row {{ margin-top: 6px; display: none; }}
  .replace-file-row label {{ font-size: 11px; color: #d29922; margin-bottom: 3px; }}
  .replace-file-row input[type=text] {{
    width: 100%; padding: 5px 8px; background: #0d1117; border: 1px solid #d29922;
    border-radius: 5px; color: #e3b341; font-size: 11px; font-family: monospace;
    box-sizing: border-box;
  }}
</style>
</head>
<body>
<header>
  <div>
    <h1>B-Roll Picker</h1>
    <div class="meta">{theme} · {language} · {duration:.0f}s</div>
  </div>
  <div class="stats">
    <div class="stat"><div class="num">{broll_count}</div><div class="lbl">B-Roll</div></div>
    <div class="stat"><div class="num">{avatar_count}</div><div class="lbl">Avatar</div></div>
    <div class="stat"><div class="num" id="stat-approved">0</div><div class="lbl">Aprovados</div></div>
    <div class="stat"><div class="num" id="stat-rejected">0</div><div class="lbl">Rejeitados</div></div>
  </div>
</header>
<main>
  <div class="controls">
    <button onclick="approveAll()">Aprovar todos</button>
    <button onclick="rejectLowScore()">Rejeitar score &lt; 0.5</button>
    <button onclick="resetAll()">Resetar tudo</button>
    <button onclick="filterShow('all')">Mostrar todos</button>
    <button onclick="filterShow('broll')">Apenas B-roll</button>
    <button onclick="filterShow('low')">Score baixo</button>
  </div>
  <div id="beats">
{beats_html}
  </div>
</main>
<button id="apply-btn" onclick="toggleApplyPanel()">Aplicar no Re-render</button>
<button id="export" onclick="exportSelections()">Exportar Decisoes</button>

<div id="apply-panel">
  <h3>Aplicar Decisoes — Re-render</h3>
  <label>Timeline JSON (gerado pelo pipeline)</label>
  <input type="text" id="inp-timeline" placeholder="C:\\...\\output_beat_timeline.json">
  <label>Avatar / Video base</label>
  <input type="text" id="inp-avatar" placeholder="C:\\...\\avatar.mp4">
  <label>Legendas SRT (opcional)</label>
  <input type="text" id="inp-srt" placeholder="C:\\...\\output.srt">
  <label>Nome do output (opcional)</label>
  <input type="text" id="inp-output" placeholder="output_v2.mp4">
  <div class="panel-row">
    <button id="btn-analyze" onclick="doAnalyze()">Pre-visualizar impacto</button>
    <button id="btn-do-apply" onclick="doApply()">Re-renderizar agora</button>
  </div>
  <div id="apply-status"></div>
</div>

<footer>
  Gerado em {generated_at} · Schema {schema_version}
</footer>
<script>
  const decisions = {{}};
  const replacements = {{}};
  const SERVER = 'http://localhost:5051';

  function setDecision(id, value) {{
    decisions[id] = value;
    const el = document.querySelector('[data-beat-id="' + id + '"]');
    if (el) {{
      el.classList.remove('approved', 'rejected', 'replace');
      if (value) el.classList.add(value);
      // Show/hide replace file input
      const rfRow = el.querySelector('.replace-file-row');
      if (rfRow) rfRow.style.display = (value === 'replace') ? '' : 'none';
    }}
    updateStats();
    persist();
  }}

  function setReplacement(id, path) {{
    if (path && path.trim()) {{
      replacements[id] = path.trim();
    }} else {{
      delete replacements[id];
    }}
    persist();
  }}

  function updateStats() {{
    let approved = 0, rejected = 0;
    Object.values(decisions).forEach(v => {{
      if (v === 'approved') approved++;
      if (v === 'rejected') rejected++;
    }});
    document.getElementById('stat-approved').textContent = approved;
    document.getElementById('stat-rejected').textContent = rejected;
  }}

  function persist() {{
    localStorage.setItem('broll_picker_decisions', JSON.stringify(decisions));
    localStorage.setItem('broll_picker_replacements', JSON.stringify(replacements));
  }}

  function load() {{
    try {{
      const dData = JSON.parse(localStorage.getItem('broll_picker_decisions') || '{{}}');
      const rData = JSON.parse(localStorage.getItem('broll_picker_replacements') || '{{}}');
      Object.entries(dData).forEach(([id, v]) => {{
        decisions[id] = v;
        const el = document.querySelector('[data-beat-id="' + id + '"]');
        if (el && v) {{
          el.classList.add(v);
          if (v === 'replace') {{
            const rfRow = el.querySelector('.replace-file-row');
            if (rfRow) rfRow.style.display = '';
          }}
        }}
      }});
      Object.entries(rData).forEach(([id, path]) => {{
        replacements[id] = path;
        const inp = document.querySelector('[data-repl-id="' + id + '"]');
        if (inp) inp.value = path;
      }});
      updateStats();
    }} catch (e) {{ console.error(e); }}
  }}

  function approveAll() {{
    document.querySelectorAll('.beat[data-type="broll"]').forEach(el => {{
      setDecision(el.dataset.beatId, 'approved');
    }});
  }}

  function rejectLowScore() {{
    document.querySelectorAll('.beat[data-type="broll"]').forEach(el => {{
      const score = parseFloat(el.dataset.score || '1');
      if (score < 0.5) setDecision(el.dataset.beatId, 'rejected');
    }});
  }}

  function resetAll() {{
    if (!confirm('Limpar todas as selecoes?')) return;
    Object.keys(decisions).forEach(k => delete decisions[k]);
    Object.keys(replacements).forEach(k => delete replacements[k]);
    document.querySelectorAll('.beat').forEach(el => {{
      el.classList.remove('approved', 'rejected', 'replace');
      const rfRow = el.querySelector('.replace-file-row');
      if (rfRow) rfRow.style.display = 'none';
    }});
    updateStats();
    persist();
  }}

  function filterShow(mode) {{
    document.querySelectorAll('.beat').forEach(el => {{
      let show = true;
      if (mode === 'broll' && el.dataset.type !== 'broll') show = false;
      if (mode === 'low') {{
        const score = parseFloat(el.dataset.score || '1');
        if (score >= 0.6 || el.dataset.type !== 'broll') show = false;
      }}
      el.style.display = show ? '' : 'none';
    }});
  }}

  function exportSelections() {{
    const out = JSON.stringify({{decisions, replacements}}, null, 2);
    const blob = new Blob([out], {{type: 'application/json'}});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'picker_decisions.json'; a.click();
    URL.revokeObjectURL(url);
  }}

  function toggleApplyPanel() {{
    const panel = document.getElementById('apply-panel');
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
  }}

  function setStatus(msg, color) {{
    const el = document.getElementById('apply-status');
    el.textContent = msg;
    el.style.color = color || '#8b949e';
  }}

  function doAnalyze() {{
    const tl = document.getElementById('inp-timeline').value.trim();
    if (!tl) {{ setStatus('Informe o caminho do timeline JSON.', '#ff7b72'); return; }}
    setStatus('Analisando...', '#8b949e');
    fetch(SERVER + '/api/pipeline/auditor/analyze', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        timeline_path: tl,
        decisions: decisions,
        replacements: replacements,
      }})
    }}).then(r => r.json()).then(d => {{
      if (d.ok) {{
        setStatus(
          'Impacto: ' + d.approved + ' aprovados | ' + d.rejected + ' rejeitados | ' +
          d.replace_manual + ' subst. manual | ' + d.replace_auto + ' re-busca | ' +
          d.unchanged + ' inalterados',
          '#56d364'
        );
      }} else {{
        setStatus('Erro: ' + (d.error || 'desconhecido'), '#ff7b72');
      }}
    }}).catch(e => setStatus('Conexao recusada. Servidor rodando?', '#ff7b72'));
  }}

  function doApply() {{
    const tl = document.getElementById('inp-timeline').value.trim();
    const av = document.getElementById('inp-avatar').value.trim();
    const srt = document.getElementById('inp-srt').value.trim();
    const out = document.getElementById('inp-output').value.trim();

    if (!tl) {{ setStatus('Informe o caminho do timeline JSON.', '#ff7b72'); return; }}
    if (!av) {{ setStatus('Informe o caminho do avatar/video.', '#ff7b72'); return; }}
    if (Object.keys(decisions).length === 0) {{
      setStatus('Nenhuma decisao feita ainda.', '#d29922'); return;
    }}

    setStatus('Enviando para re-render...', '#e3b341');
    fetch(SERVER + '/api/pipeline/rerender', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        timeline_path: tl,
        avatar_path: av,
        subtitles_srt: srt,
        output_name: out || undefined,
        decisions: decisions,
        replacements: replacements,
      }})
    }}).then(r => r.json()).then(d => {{
      if (d.started) {{
        setStatus('Re-render iniciado! Acompanhe em: ' + (d.output_name || d.output), '#56d364');
        pollProgress();
      }} else {{
        setStatus('Erro: ' + (d.error || JSON.stringify(d)), '#ff7b72');
      }}
    }}).catch(e => setStatus('Conexao recusada. Servidor rodando em 5051?', '#ff7b72'));
  }}

  function pollProgress() {{
    const poll = setInterval(() => {{
      fetch(SERVER + '/api/pipeline/status').then(r => r.json()).then(d => {{
        const pct = d.progress || 0;
        const msg = d.message || '';
        setStatus('Re-render: ' + pct + '% — ' + msg, pct === 100 ? '#56d364' : '#e3b341');
        if (!d.running || pct >= 100) clearInterval(poll);
      }}).catch(() => clearInterval(poll));
    }}, 1500);
  }}

  load();
</script>
</body>
</html>
"""


def _beat_card_html(beat: dict) -> str:
    """Render one beat as HTML."""
    bid = beat["id"]
    btype = beat.get("type", "avatar")
    start = beat.get("start", 0)
    end = beat.get("end", 0)
    duration = beat.get("duration", 0)
    narration = beat.get("narration_text", "")
    terms = beat.get("search_terms", [])
    shot_type = beat.get("shot_type", "")
    mood = beat.get("mood", "")
    source = beat.get("source", "")
    file_path = beat.get("file", "")
    is_image = beat.get("is_image", False)
    score = beat.get("validation_score")

    # Preview block
    if btype == "broll" and file_path and os.path.exists(file_path):
        # Use file:// URL — works locally
        file_url = "file:///" + file_path.replace("\\", "/")
        if is_image or file_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            preview = f'<img src="{escape(file_url)}" alt="" loading="lazy">'
        else:
            preview = (
                f'<video src="{escape(file_url)}" controls preload="metadata" muted loop></video>'
            )
    elif btype == "broll":
        preview = '<div class="missing">Arquivo nao encontrado</div>'
    else:
        preview = '<div class="missing">[Avatar segment]</div>'

    # Badges
    badges = [f'<span class="badge">#{bid}</span>',
              f'<span class="badge">{start:.1f}s — {end:.1f}s · {duration:.1f}s</span>']
    if shot_type:
        badges.append(f'<span class="badge shot">{escape(shot_type)}</span>')
    if mood:
        badges.append(f'<span class="badge mood">{escape(mood)}</span>')
    if source:
        badges.append(f'<span class="badge src">{escape(source)}</span>')
    if score is not None:
        cls = "score-high" if score >= 0.6 else "score-low"
        badges.append(f'<span class="badge {cls}">score {score:.2f}</span>')

    badges_html = "\n      ".join(badges)

    # Terms
    terms_html = "".join(f'<span class="term">{escape(t)}</span>' for t in terms) or "<span class='term'>—</span>"

    # Actions only for broll
    actions = ""
    if btype == "broll":
        actions = f"""
      <div class="actions">
        <button class="btn-approve" onclick="setDecision('{bid}', 'approved')">Aprovar</button>
        <button class="btn-replace" onclick="setDecision('{bid}', 'replace')">Substituir</button>
        <button class="btn-reject" onclick="setDecision('{bid}', 'rejected')">Rejeitar</button>
      </div>
      <div class="replace-file-row" style="display:none">
        <label>Caminho do novo arquivo (deixe vazio para re-busca automatica):</label>
        <input type="text" data-repl-id="{bid}" placeholder="C:\\...\\meu_clip.mp4"
               onchange="setReplacement('{bid}', this.value)"
               oninput="setReplacement('{bid}', this.value)">
      </div>"""

    return f"""    <div class="beat" data-beat-id="{bid}" data-type="{btype}" data-score="{score if score is not None else 1}">
      <div class="preview">{preview}</div>
      <div class="details">
        <div class="badges">
      {badges_html}
        </div>
        <div class="narration">{escape(narration) or "[sem narracao]"}</div>
        <div class="terms">{terms_html}</div>
        <div class="filename">{escape(file_path) if file_path else ""}</div>
        {actions}
      </div>
    </div>"""


def generate_picker(beat_timeline: dict, output_html: str) -> str:
    """Generate the picker HTML file from a beat_timeline dict.

    Returns the path to the generated HTML."""
    beats_html_parts = []
    for beat in beat_timeline.get("beats", []):
        beats_html_parts.append(_beat_card_html(beat))
    beats_html = "\n".join(beats_html_parts)

    html = HTML_TEMPLATE.format(
        theme=escape(beat_timeline.get("theme", "?")),
        language=escape(beat_timeline.get("language", "?")),
        duration=beat_timeline.get("duration", 0),
        broll_count=beat_timeline.get("broll_count", 0),
        avatar_count=beat_timeline.get("avatar_count", 0),
        beats_html=beats_html,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        schema_version=beat_timeline.get("schema_version", "1.0"),
    )

    os.makedirs(os.path.dirname(output_html) or ".", exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [broll_picker] HTML saved: {output_html}")
    return output_html


def generate_picker_from_json(json_path: str, output_html: str = None) -> str:
    """Convenience: load beat_timeline.json from disk and generate picker."""
    from core.beat_timeline import load_beat_timeline
    timeline = load_beat_timeline(json_path)
    if not output_html:
        output_html = json_path.replace(".json", "_picker.html")
    return generate_picker(timeline, output_html)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        out = generate_picker_from_json(sys.argv[1])
        print(f"Open: {out}")
    else:
        print("Usage: python broll_picker.py <beat_timeline.json>")

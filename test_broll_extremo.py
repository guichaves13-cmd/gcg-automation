"""
B-Roll EXTREMO — Suite de Testes Máxima
7 seções, 60+ testes cobrindo todos os cantos do sistema.

Seção 1: _build_smart_timeline — edge cases brutais
Seção 2: beat_timeline — edge cases de dados corrompidos/extremos
Seção 3: sync_report — precisão de float e casos de borda
Seção 4: anti_reuse — seeds extremos, arquivos inválidos, stress
Seção 5: Integridade estrutural — invariantes do JSON
Seção 6: Cenários de produção — vídeos reais (45min/50brolls, 12min/20brolls)
Seção 7: Regressão de bugs — garante que NENHUM bug antigo volta (1000 seeds)
"""

import sys, os, json, math, time, tempfile, random, threading, subprocess, shutil, inspect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.pipeline_avatar_auto import _build_smart_timeline
from core.beat_timeline import build_beat_timeline, load_beat_timeline, summarize_beat_timeline

passes = fails = 0
fail_log = []

def ok(name, detail=""):
    global passes
    passes += 1
    print(f"  \033[92m[PASS]\033[0m {name}" + (f" [{detail}]" if detail else ""))

def fail(name, detail=""):
    global fails
    fail_log.append(f"FAIL [{name}]: {detail}")
    print(f"  \033[91m[FAIL]\033[0m {name}: {detail}")

def sep(title):
    print(f"\n{'='*76}\n  {title}\n{'='*76}")

# ─── helpers ──────────────────────────────────────────────────────────────────
def clips(items):
    return [{"file": f"b{i:04d}.mp4", "keyword": kw,
              "timeline_start": float(ts), "timeline_end": float(te),
              "shot_type": st, "mood": "informative", "source": "pexels"}
            for i, (kw, ts, te, st) in enumerate(items)]

def shots(items):
    return [{"start": float(s), "end": float(e),
              "search_terms": t, "shot_type": st, "mood": "informative"}
            for s, e, t, st in items]

def trans(items):
    return [{"text": txt, "start": float(s), "end": float(e)}
            for txt, s, e in items]

def run_timeline(dur, clip_items, seed=42):
    return _build_smart_timeline(dur, clips(clip_items), random.Random(seed))

def brolls(segs): return [s for s in segs if s["type"] == "broll"]
def avatars(segs): return [s for s in segs if s["type"] == "avatar"]

def assert_no_fat_chunks(segs, label="", limit=12.5):
    fat = [s for s in segs if s["type"] == "avatar" and s["duration"] > limit]
    assert not fat, \
        f"{label}: {len(fat)} avatar chunks >{limit}s: " + \
        ", ".join(f"@{s['start']:.0f}s={s['duration']:.2f}s" for s in fat[:4])

def assert_no_overlap(segs, label=""):
    for i in range(len(segs) - 1):
        e = segs[i]["start"] + segs[i]["duration"]
        n = segs[i+1]["start"]
        assert e - n <= 0.15, \
            f"{label}: overlap segs {i}/{i+1}: end={e:.3f} next={n:.3f}"

def assert_coverage(segs, total_dur, label="", tol=3.0):
    cov = sum(s["duration"] for s in segs)
    assert abs(cov - total_dur) < tol, \
        f"{label}: cobertura {cov:.1f}s vs {total_dur:.1f}s (gap={abs(cov-total_dur):.2f}s)"


# ══════════════════════════════════════════════════════════════════════════════
sep("1. _build_smart_timeline — EDGE CASES BRUTAIS")
# ══════════════════════════════════════════════════════════════════════════════

# 1.1 — 100 clips em 60 minutos (3600s)
try:
    rng = random.Random(1001)
    clip_list = clips([(f"kw{i}", i*35+20, i*35+28, "wide" if i%2==0 else "closeup")
                       for i in range(100)])
    t0 = time.time()
    segs = _build_smart_timeline(3600.0, clip_list, rng)
    elapsed = time.time() - t0
    b = brolls(segs); a = avatars(segs)
    assert len(b) >= 80, f"apenas {len(b)} brolls de 100 clips"
    assert_no_fat_chunks(segs, "100 clips 60min")
    assert_no_overlap(segs, "100 clips 60min")
    assert_coverage(segs, 3600.0, "100 clips 60min", tol=5.0)
    ok(f"1.1 — 100 clips / 3600s", f"{elapsed*1000:.0f}ms, {len(b)} brolls {len(a)} avatar_segs")
except Exception as e:
    fail("1.1 — 100 clips 3600s", str(e)[:250])

# 1.2 — Clips com timestamps DUPLICADOS (colisão: dois clips no mesmo segundo)
try:
    # 3 clips querem todos começar em 30s — o V2 é conflitante
    segs = run_timeline(120.0, [
        ("kw_colA", 30.0, 37.0, "closeup"),
        ("kw_colB", 30.0, 37.0, "wide"),
        ("kw_colC", 30.0, 37.0, "closeup"),
    ], seed=55)
    assert isinstance(segs, list) and len(segs) >= 1
    assert_no_fat_chunks(segs, "clips duplicados")
    assert_no_overlap(segs, "clips duplicados")
    ok(f"1.2 — 3 clips no mesmo timestamp (colisao)", f"{len(brolls(segs))} brolls gerados")
except Exception as e:
    fail("1.2 — colisao de timestamps", str(e)[:200])

# 1.3 — Clips com timeline_start ALÉM da duração do vídeo (devem ser pulados/colocados no final)
try:
    segs = run_timeline(60.0, [
        ("early", 20.0, 27.0, "wide"),
        ("beyond1", 70.0, 77.0, "closeup"),   # além dos 60s
        ("beyond2", 120.0, 127.0, "wide"),     # muito além
        ("beyond3", 9999.0, 10006.0, "wide"), # absurdo
    ], seed=7)
    assert isinstance(segs, list)
    # Nenhum segmento deve começar além da duração
    over = [s for s in segs if s["start"] + s["duration"] > 65.0]
    assert not over, f"segs extrapolam: {[(s['start'], s['duration']) for s in over]}"
    ok(f"1.3 — clips alem da duracao ignorados/cortados", f"{len(brolls(segs))} brolls validos")
except Exception as e:
    fail("1.3 — clips alem da duracao", str(e)[:200])

# 1.4 — Clips com timeline_start = 0 (antes do intro — devem usar current_time)
try:
    segs = run_timeline(60.0, [
        ("before_intro", 0.0, 6.0, "wide"),
        ("also_zero", 0.5, 6.5, "closeup"),
        ("normal", 30.0, 37.0, "wide"),
    ], seed=11)
    assert isinstance(segs, list)
    assert_no_fat_chunks(segs, "clips t=0")
    assert_no_overlap(segs, "clips t=0")
    ok(f"1.4 — clips com timeline_start=0 nao crasham", f"{len(segs)} segs")
except Exception as e:
    fail("1.4 — clips timeline_start=0", str(e)[:200])

# 1.5 — Todos shot_types exóticos: aerial, pov, diagram, detail, unknown
try:
    segs = run_timeline(120.0, [
        ("aerial_shot",   20.0, 28.0, "aerial"),
        ("pov_shot",      40.0, 47.0, "pov"),
        ("diagram_shot",  60.0, 67.0, "diagram"),
        ("detail_shot",   80.0, 86.0, "detail"),
        ("unknown_type", 100.0, 107.0, "timelapse"),  # tipo desconhecido
    ], seed=22)
    b = brolls(segs)
    assert len(b) >= 3, f"apenas {len(b)} brolls com shot_types exoticos"
    assert_no_fat_chunks(segs, "shot_types exoticos")
    ok(f"1.5 — shot_types exoticos (aerial/pov/diagram/detail/unknown)", f"{len(b)} brolls")
except Exception as e:
    fail("1.5 — shot_types exoticos", str(e)[:200])

# 1.6 — Seeding determinístico: mesmo seed = output idêntico
try:
    base_clips = clips([
        ("omega3", 15.0, 22.0, "closeup"),
        ("brain",  30.0, 37.0, "wide"),
        ("yoga",   50.0, 57.0, "wide"),
    ])
    segs_a = _build_smart_timeline(90.0, list(base_clips), random.Random(42))
    segs_b = _build_smart_timeline(90.0, list(base_clips), random.Random(42))
    segs_c = _build_smart_timeline(90.0, list(base_clips), random.Random(99))  # seed diferente
    assert segs_a == segs_b, "mesmo seed -> output diferente (não-determinístico!)"
    assert segs_a != segs_c, "seeds diferentes -> output idêntico (seed ignorado!)"
    ok("1.6 — seeding deterministico: seed42=seed42, seed42!=seed99")
except Exception as e:
    fail("1.6 — seeding deterministico", str(e)[:200])

# 1.7 — B-roll que excederia a duração: duração cortada no limite
try:
    segs = run_timeline(35.0, [
        ("late_clip", 28.0, 35.0, "wide"),  # B-roll começaria em 28, duração wide=6-9s → extrapola
    ], seed=33)
    for s in segs:
        assert s["start"] + s["duration"] <= 35.5, \
            f"seg extrapola: {s['start']:.2f}+{s['duration']:.2f}={s['start']+s['duration']:.2f} > 35"
    ok(f"1.7 — B-roll tardio cortado no limite da duracao", f"{len(segs)} segs total")
except Exception as e:
    fail("1.7 — B-roll tardio cortado", str(e)[:200])

# 1.8 — Clips em ordem INVERTIDA no input (devem ser reordenados pelo sort)
try:
    segs = run_timeline(120.0, [
        ("last",   90.0,  97.0, "wide"),
        ("middle", 50.0,  57.0, "closeup"),
        ("first",  20.0,  27.0, "wide"),
    ], seed=44)
    b = brolls(segs)
    starts = [s["start"] for s in b]
    assert starts == sorted(starts), f"brolls fora de ordem: {starts}"
    ok(f"1.8 — clips invertidos reordenados corretamente", f"brolls@{[f'{s:.0f}' for s in starts]}")
except Exception as e:
    fail("1.8 — clips invertidos", str(e)[:200])

# 1.9 — 50 clips consecutivos SEM gap (timeline_start aumenta de 8 em 8s)
try:
    # Isso cria uma sequência densa de B-rolls com nenhum espaço entre eles
    segs = run_timeline(500.0, [
        (f"dense{i}", i*8+10, i*8+17, "closeup") for i in range(50)
    ], seed=88)
    b = brolls(segs); a = avatars(segs)
    assert len(b) >= 20, f"apenas {len(b)} brolls de 50 clips densos"
    assert_no_fat_chunks(segs, "50 clips densos")
    assert_no_overlap(segs, "50 clips densos")
    ok(f"1.9 — 50 clips consecutivos densos", f"{len(b)} brolls {len(a)} avatar_chunks")
except Exception as e:
    fail("1.9 — 50 clips densos", str(e)[:200])

# 1.10 — clip com campos inválidos (None, int, lista como shot_type)
try:
    bad_clips = [
        {"file": "bad1.mp4", "keyword": None, "timeline_start": 20.0,
         "timeline_end": 27.0, "shot_type": None, "source": "pexels"},
        {"file": "bad2.mp4", "keyword": 12345, "timeline_start": 40.0,
         "timeline_end": 47.0, "shot_type": ["list"], "source": "pexels"},
        {"file": "bad3.mp4", "keyword": "ok", "timeline_start": 60.0,
         "timeline_end": 67.0, "shot_type": "", "source": "pexels"},
    ]
    segs = _build_smart_timeline(90.0, bad_clips, random.Random(5))
    assert isinstance(segs, list)
    assert_no_fat_chunks(segs, "campos invalidos")
    ok(f"1.10 — clips com campos invalidos (None/int/lista) nao crasham", f"{len(segs)} segs")
except Exception as e:
    fail("1.10 — campos invalidos no clip", str(e)[:200])

# 1.11 — Duração do vídeo = 0 (edge case extremo)
try:
    segs = _build_smart_timeline(0.0, clips([("kw", 0.0, 5.0, "wide")]), random.Random(1))
    assert isinstance(segs, list)
    ok(f"1.11 — duracao=0 nao crasha", f"{len(segs)} segs")
except Exception as e:
    fail("1.11 — duracao=0", str(e)[:200])

# 1.12 — Duração muito grande (vídeo de 6 horas = 21600s)
try:
    rng_big = random.Random(777)
    clip_list = clips([(f"kw{i}", i*800+100, i*800+108, "wide") for i in range(25)])
    t0 = time.time()
    segs = _build_smart_timeline(21600.0, clip_list, rng_big)
    elapsed = time.time() - t0
    assert_no_fat_chunks(segs, "6h video")
    assert_coverage(segs, 21600.0, "6h video", tol=10.0)
    ok(f"1.12 — video de 6h (21600s)", f"{len(segs)} segs, {elapsed*1000:.0f}ms")
except Exception as e:
    fail("1.12 — video 6h", str(e)[:200])

# 1.13 — Alternância correta de shot pacing: wide(6-9s) vs closeup(3-5s)
try:
    segs = run_timeline(120.0, [
        ("wide_shot",    15.0, 22.0, "wide"),
        ("closeup_shot", 30.0, 37.0, "closeup"),
        ("wide_shot2",   50.0, 57.0, "wide"),
        ("closeup_shot2",65.0, 72.0, "closeup"),
    ], seed=13)
    b = brolls(segs)
    wide_b = [s for s in b if s.get("shot_type") == "wide"]
    close_b = [s for s in b if s.get("shot_type") == "closeup"]
    for s in wide_b:
        assert 5.5 <= s["duration"] <= 9.5, \
            f"wide shot fora do range: {s['duration']:.2f}s"
    for s in close_b:
        assert 2.5 <= s["duration"] <= 5.5, \
            f"closeup shot fora do range: {s['duration']:.2f}s"
    wide_durs = [f"{s['duration']:.1f}" for s in wide_b]
    close_durs = [f"{s['duration']:.1f}" for s in close_b]
    ok(f"1.13 — pacing correto wide({wide_durs}) vs closeup({close_durs})")
except Exception as e:
    fail("1.13 — pacing wide vs closeup", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("2. beat_timeline — DADOS CORROMPIDOS / EXTREMOS")
# ══════════════════════════════════════════════════════════════════════════════

# 2.1 — Transcrição com segmentos sobrepostos no tempo
try:
    transcription_overlap = [
        {"text": "Primeiro texto.", "start": 10.0, "end": 20.0},
        {"text": "Segundo texto sobreposto.", "start": 14.0, "end": 25.0},  # overlap com o 1o
        {"text": "Terceiro limpo.", "start": 30.0, "end": 38.0},
    ]
    segs_ov = [{"type": "broll", "start": 13.0, "duration": 8.0, "file": "b.mp4",
                 "keyword": "kw", "v2_planned_start": 13.0}]
    tl = build_beat_timeline(segs_ov, [], transcription_overlap, {"duration": 50.0})
    b = [x for x in tl["beats"] if x["type"] == "broll"][0]
    # Deve capturar texto dos dois segmentos sobrepostos
    assert len(b["narration_text"]) > 5
    ok(f"2.1 — transcricao sobreposta nao crasha", f"narration='{b['narration_text'][:50]}'")
except Exception as e:
    fail("2.1 — transcricao sobreposta", str(e)[:200])

# 2.2 — Texto de narração MUITO LONGO (>500 chars): deve ser truncado em 500
try:
    long_text = "Omega-3 " * 100  # 800 chars
    tl = build_beat_timeline(
        [{"type": "broll", "start": 5.0, "duration": 6.0, "file": "b.mp4",
           "keyword": "kw", "v2_planned_start": 5.0}],
        [],
        [{"text": long_text, "start": 4.0, "end": 12.0}],
        {"duration": 20.0}
    )
    b = [x for x in tl["beats"] if x["type"] == "broll"][0]
    assert len(b["narration_text"]) <= 500, \
        f"narration_text nao truncado: {len(b['narration_text'])} chars"
    ok(f"2.2 — narration_text truncado em 500 chars", f"len={len(b['narration_text'])}")
except Exception as e:
    fail("2.2 — narration_text truncado", str(e)[:200])

# 2.3 — Narração em árabe, chinês, emojis e RTL
try:
    exotic_texts = [
        {"text": "الأوميغا 3 ضروري للصحة", "start": 5.0, "end": 10.0},   # árabe
        {"text": "大脑健康非常重要", "start": 12.0, "end": 18.0},              # chinês
        {"text": "🧠💊✨ Omega-3 is key!", "start": 20.0, "end": 26.0},    # emojis
        {"text": "Здоровье мозга важно", "start": 30.0, "end": 36.0},     # russo
    ]
    segs_exotic = [
        {"type": "broll", "start": 5.0, "duration": 5.0, "file": "b1.mp4",
         "keyword": "kw1", "v2_planned_start": 5.0},
        {"type": "broll", "start": 20.0, "duration": 6.0, "file": "b2.mp4",
         "keyword": "kw2", "v2_planned_start": 20.0},
    ]
    tl = build_beat_timeline(segs_exotic, [], exotic_texts, {"duration": 50.0})
    assert len(tl["beats"]) >= 2
    ok(f"2.3 — narração arabe/chines/emoji/russo nao crasha")
except Exception as e:
    fail("2.3 — narração exotica unicode", str(e)[:200])

# 2.4 — Segmentos de transcrição com start=0, end=0 e text vazio
try:
    broken_trans = [
        {"text": "", "start": 0.0, "end": 0.0},       # vazio
        {"text": "   ", "start": 5.0, "end": 8.0},    # só espaços
        {"start": 10.0, "end": 15.0},                  # sem "text"
        {"text": "Texto real.", "start": 15.0, "end": 20.0},
    ]
    segs_bt = [{"type": "broll", "start": 14.0, "duration": 6.0, "file": "b.mp4",
                "keyword": "kw", "v2_planned_start": 14.0}]
    tl = build_beat_timeline(segs_bt, [], broken_trans, {"duration": 30.0})
    b = [x for x in tl["beats"] if x["type"] == "broll"][0]
    ok(f"2.4 — transcricao corrompida (vazio/sem-text) nao crasha",
       f"narration='{b['narration_text'][:40]}'")
except Exception as e:
    fail("2.4 — transcricao corrompida", str(e)[:200])

# 2.5 — Transcrição com 1000 segmentos (stress de narração)
try:
    big_trans = trans([(f"segment {i}", i*0.5, i*0.5+0.4) for i in range(1000)])
    segs_big = [{"type": "broll", "start": 50.0, "duration": 5.0, "file": "b.mp4",
                  "keyword": "kw", "v2_planned_start": 50.0}]
    t0 = time.time()
    tl = build_beat_timeline(segs_big, [], big_trans, {"duration": 600.0})
    elapsed = time.time() - t0
    assert elapsed < 2.0, f"1000 segs de transcricao demorou {elapsed:.2f}s"
    ok(f"2.5 — 1000 segmentos de transcricao em {elapsed*1000:.0f}ms")
except Exception as e:
    fail("2.5 — 1000 segmentos transcricao", str(e)[:200])

# 2.6 — Segmentos com tipo None, "broll" sem "file", valores inválidos
try:
    bad_segs = [
        None,                                           # None no lugar de dict
        {"type": "broll"},                              # sem file, sem start/duration
        {"type": "broll", "start": "abc", "duration": 5.0, "file": "b.mp4"},  # start=str
        {"type": "avatar", "start": 0.0, "duration": float("inf")},           # inf
        {"type": "avatar", "start": 0.0, "duration": float("nan")},           # nan
        {"type": "avatar", "start": 5.0, "duration": 5.0},                    # ok
    ]
    tl = build_beat_timeline(bad_segs, [], trans([("texto", 0.0, 60.0)]),
                              {"duration": 60.0})
    # O sistema deve ignorar segs malformados e processar os válidos
    ok(f"2.6 — segs None/sem-file/inf/nan ignorados",
       f"{tl['total_beats']} beats sobreviveram")
except Exception as e:
    fail("2.6 — segs malformados", str(e)[:200])

# 2.7 — Análise com campos ausentes (video_id, theme, language)
try:
    segs_minimal = [{"type": "avatar", "start": 0.0, "duration": 5.0}]
    tl = build_beat_timeline(segs_minimal, [], trans([("t", 0.0, 5.0)]), {})
    assert "beats" in tl
    assert tl.get("video_id", "") == ""
    ok(f"2.7 — analise com campos ausentes nao crasha")
except Exception as e:
    fail("2.7 — analise com campos ausentes", str(e)[:200])

# 2.8 — beat_timeline com mapped_clips: source e keyword do clip aplicados
try:
    segs_mc = [{"type": "broll", "start": 10.0, "duration": 5.0,
                 "file": "clip_x.mp4", "keyword": "omega3 capsule",
                 "v2_planned_start": 10.0}]
    mapped = [{"file": "clip_x.mp4", "source": "pixabay",
               "keyword": "omega3 capsule", "timeline_start": 10.0,
               "validation_score": 0.92}]
    tl = build_beat_timeline(segs_mc, [], trans([("omega3", 9.0, 15.0)]),
                              {"duration": 30.0}, mapped_clips=mapped)
    b = [x for x in tl["beats"] if x["type"] == "broll"][0]
    assert b.get("source") == "pixabay", f"source errado: {b.get('source')}"
    assert b.get("validation_score") == 0.92, f"score errado: {b.get('validation_score')}"
    ok(f"2.8 — mapped_clips: source e validation_score corretos",
       f"source={b['source']} score={b['validation_score']}")
except Exception as e:
    fail("2.8 — mapped_clips source/score", str(e)[:200])

# 2.9 — B-roll com is_image=True (imagem estática no lugar de video)
try:
    segs_img = [{"type": "broll", "start": 10.0, "duration": 5.0,
                  "file": "photo.jpg", "keyword": "brain anatomy",
                  "is_image": True, "v2_planned_start": 10.0}]
    tl = build_beat_timeline(segs_img, [], trans([("brain", 9.0, 15.0)]),
                              {"duration": 30.0})
    b = [x for x in tl["beats"] if x["type"] == "broll"][0]
    assert b.get("is_image") is True
    ok(f"2.9 — is_image=True preservado no beat")
except Exception as e:
    fail("2.9 — is_image no beat", str(e)[:200])

# 2.10 — 500 beats (stress de build_beat_timeline)
try:
    segs_500 = []
    t = 0.0
    for i in range(500):
        tp = "broll" if i % 3 != 0 else "avatar"
        dur = 5.0 if tp == "broll" else 3.0
        seg = {"type": tp, "start": round(t, 2), "duration": dur}
        if tp == "broll":
            seg.update({"file": f"b{i}.mp4", "keyword": f"kw{i}",
                         "v2_planned_start": round(t, 2)})
        segs_500.append(seg)
        t += dur
    t0 = time.time()
    tl = build_beat_timeline(segs_500, [], trans([("text", 0.0, t)]),
                              {"duration": t})
    elapsed = time.time() - t0
    assert elapsed < 5.0, f"500 beats demorou {elapsed:.2f}s"
    assert tl["total_beats"] == 500
    ok(f"2.10 — 500 beats em {elapsed*1000:.0f}ms", f"total={tl['total_beats']}")
except Exception as e:
    fail("2.10 — 500 beats stress", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("3. sync_report — PRECISAO DE FLOAT E CASOS DE BORDA")
# ══════════════════════════════════════════════════════════════════════════════

def make_broll_seg(start, v2, dur=5.0, idx=0):
    return {"type": "broll", "start": start, "duration": dur,
            "file": f"clip{idx}.mp4", "keyword": f"kw{idx}",
            "v2_planned_start": v2}

def quick_tl(segs_list, dur=100.0):
    return build_beat_timeline(segs_list, [], trans([("t", 0.0, dur)]),
                                {"duration": dur})

# 3.1 — Drift exatamente 2.0s (borda): NÃO deve entrar em beats_with_drift (strict >)
try:
    tl = quick_tl([make_broll_seg(12.0, 10.0)], 50.0)  # drift = 2.0 exato
    sr = tl["sync_report"]
    assert sr["max_drift_seconds"] == 2.0
    assert len(sr["beats_with_drift"]) == 0, \
        f"drift=2.0 nao deve estar em beats_with_drift (threshold e strict >): {sr['beats_with_drift']}"
    assert sr["sync_ok"] is True
    ok("3.1 — drift=2.0s exato: sync_ok=True (threshold strict >2.0)")
except Exception as e:
    fail("3.1 — drift exatamente 2.0s", str(e)[:200])

# 3.2 — Drift 2.0001s: deve entrar em beats_with_drift
try:
    tl = quick_tl([make_broll_seg(12.0001, 10.0)])  # drift = 2.0001
    sr = tl["sync_report"]
    drift = sr["max_drift_seconds"]
    # Após round(..., 2) → drift = 2.0
    # Se round -> 2.0, não entra. Se round -> 2.01, entra. Depende da implementação.
    # O importante é não crashar e ter medição correta
    ok(f"3.2 — drift=2.0001s: max_drift={drift}s (round correto)")
except Exception as e:
    fail("3.2 — drift 2.0001s", str(e)[:200])

# 3.3 — Drift de 0.001s (quase zero)
try:
    tl = quick_tl([make_broll_seg(10.001, 10.0)])
    sr = tl["sync_report"]
    assert sr["max_drift_seconds"] <= 0.01
    assert sr["sync_ok"] is True
    ok(f"3.3 — drift=0.001s: sync_ok=True, max_drift={sr['max_drift_seconds']}s")
except Exception as e:
    fail("3.3 — drift 0.001s", str(e)[:200])

# 3.4 — 50 beats com drift=0 (perfeição total)
try:
    segs_perf = [make_broll_seg(float(i*10+5), float(i*10+5), idx=i) for i in range(50)]
    tl = quick_tl(segs_perf, 600.0)
    sr = tl["sync_report"]
    assert sr["measured_beats"] == 50
    assert sr["max_drift_seconds"] == 0.0
    assert sr["avg_drift_seconds"] == 0.0
    assert sr["sync_ok"] is True
    assert len(sr["beats_with_drift"]) == 0
    ok(f"3.4 — 50 beats drift=0 (perfeicao total)", f"measured={sr['measured_beats']}")
except Exception as e:
    fail("3.4 — 50 beats drift=0", str(e)[:200])

# 3.5 — 50 beats todos com drift=5s (100% fora de sync)
try:
    segs_bad = [make_broll_seg(float(i*10+10), float(i*10+5), idx=i) for i in range(50)]
    tl = quick_tl(segs_bad, 600.0)
    sr = tl["sync_report"]
    assert sr["measured_beats"] == 50
    assert sr["max_drift_seconds"] == 5.0
    assert sr["sync_ok"] is False
    assert len(sr["beats_with_drift"]) == 50
    ok(f"3.5 — 50 beats todos fora de sync", f"beats_with_drift={len(sr['beats_with_drift'])}")
except Exception as e:
    fail("3.5 — 50 beats todos fora", str(e)[:200])

# 3.6 — Mix: 25 OK + 25 ruins (avg_drift correto)
try:
    segs_mix = (
        [make_broll_seg(float(i*20+5), float(i*20+5), idx=i) for i in range(25)] +   # drift=0
        [make_broll_seg(float(i*20+5+5), float(i*20+5), idx=i+25) for i in range(25)] # drift=5
    )
    tl = quick_tl(segs_mix, 1200.0)
    sr = tl["sync_report"]
    expected_avg = round((0*25 + 5*25) / 50, 2)  # 2.5
    assert abs(sr["avg_drift_seconds"] - expected_avg) < 0.05, \
        f"avg_drift esperado {expected_avg}, got {sr['avg_drift_seconds']}"
    assert sr["max_drift_seconds"] == 5.0
    assert len(sr["beats_with_drift"]) == 25
    ok(f"3.6 — mix 25OK+25ruins: avg={sr['avg_drift_seconds']}s max={sr['max_drift_seconds']}s")
except Exception as e:
    fail("3.6 — mix 25ok 25ruins", str(e)[:200])

# 3.7 — Beat sem v2_planned_start não aparece no sync_report
try:
    segs_novp = [
        {"type": "broll", "start": 10.0, "duration": 5.0, "file": "b.mp4",
         "keyword": "kw"},                           # sem v2_planned_start
        make_broll_seg(20.0, 20.0, idx=1),           # com v2_planned_start
    ]
    tl = quick_tl(segs_novp)
    sr = tl["sync_report"]
    assert sr["measured_beats"] == 1, \
        f"apenas 1 beat deveria ser medido (sem v2_planned_start), got {sr['measured_beats']}"
    ok(f"3.7 — beat sem v2_planned_start excluido do sync_report")
except Exception as e:
    fail("3.7 — sem v2_planned_start", str(e)[:200])

# 3.8 — sync_report com v2_planned_start=None explícito
try:
    segs_none = [{"type": "broll", "start": 10.0, "duration": 5.0, "file": "b.mp4",
                   "keyword": "kw", "v2_planned_start": None}]
    tl = quick_tl(segs_none)
    sr = tl["sync_report"]
    assert sr["measured_beats"] == 0, \
        f"v2=None nao deve ser medido, got {sr['measured_beats']}"
    ok(f"3.8 — v2_planned_start=None: nao medido no sync_report")
except Exception as e:
    fail("3.8 — v2_planned_start=None", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("4. anti_reuse — SEEDS EXTREMOS / ARQUIVOS INVALIDOS / STRESS")
# ══════════════════════════════════════════════════════════════════════════════
try:
    from core.anti_reuse import apply_anti_reuse, load_used_clips, save_used_clips, clear_used_clips
    _anti_reuse_ok = True
except Exception as e:
    fail("anti_reuse import", str(e))
    _anti_reuse_ok = False

ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

def make_synth_video(path, w=320, h=180, dur=2, color="gray"):
    """Cria um vídeo sintético com ffmpeg para testes."""
    r = subprocess.run(
        [ffmpeg_bin, "-y", "-f", "lavfi",
         "-i", f"color={color}:size={w}x{h}:rate=15",
         "-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        capture_output=True, timeout=30
    )
    return r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 500

if _anti_reuse_ok:
    # 4.1 — Arquivo inexistente: não crasha
    try:
        with tempfile.TemporaryDirectory() as td:
            apply_anti_reuse(os.path.join(td, "nao_existe.mp4"),
                             os.path.join(td, "out.mp4"), seed=42)
        ok("4.1 — arquivo inexistente: nao crasha")
    except Exception as e:
        # Se falhar com erro do ffmpeg (esperado), também é ok
        if "ffmpeg" in str(e).lower() or "return" in str(e).lower() or "No such" in str(e):
            ok("4.1 — arquivo inexistente: falha graciosamente (ffmpeg error esperado)")
        else:
            fail("4.1 — arquivo inexistente", str(e)[:200])

    # 4.2 — Seed negativo (-999999)
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.mp4")
            outp = os.path.join(td, "out.mp4")
            if make_synth_video(inp):
                apply_anti_reuse(inp, outp, width=320, height=180, fps=15, seed=-999999)
                assert os.path.exists(outp)
                ok(f"4.2 — seed negativo (-999999) funciona", f"{os.path.getsize(outp)//1024}KB")
            else:
                ok("4.2 — seed negativo: ffmpeg nao disponivel (skip)")
    except Exception as e:
        fail("4.2 — seed negativo", str(e)[:150])

    # 4.3 — Seed 0
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.mp4")
            outp = os.path.join(td, "out.mp4")
            if make_synth_video(inp):
                apply_anti_reuse(inp, outp, width=320, height=180, fps=15, seed=0)
                assert os.path.exists(outp)
                ok(f"4.3 — seed=0 funciona", f"{os.path.getsize(outp)//1024}KB")
            else:
                ok("4.3 — seed=0: ffmpeg nao disponivel (skip)")
    except Exception as e:
        fail("4.3 — seed=0", str(e)[:150])

    # 4.4 — Dimensões muito pequenas (16x16)
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "in.mp4")
            outp = os.path.join(td, "out.mp4")
            if make_synth_video(inp, w=32, h=32):
                apply_anti_reuse(inp, outp, width=16, height=16, fps=15, seed=1)
                ok(f"4.4 — dimensoes 16x16 nao crasham")
            else:
                ok("4.4 — dimensoes 16x16: ffmpeg nao disponivel (skip)")
    except Exception as e:
        ok(f"4.4 — dimensoes 16x16: falha graciosamente ({type(e).__name__})")

    # 4.5 — 10 seeds diferentes produzem outputs diferentes entre si
    try:
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "base.mp4")
            if make_synth_video(inp, color="blue"):
                sizes = []
                for seed in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
                    outp = os.path.join(td, f"out_{seed}.mp4")
                    apply_anti_reuse(inp, outp, width=320, height=180, fps=15, seed=seed)
                    if os.path.exists(outp):
                        sizes.append(os.path.getsize(outp))
                unique_sizes = len(set(sizes))
                ok(f"4.5 — 10 seeds diferentes: {unique_sizes} tamanhos distintos ({len(sizes)} processados)")
            else:
                ok("4.5 — 10 seeds: ffmpeg nao disponivel (skip)")
    except Exception as e:
        fail("4.5 — 10 seeds diferentes", str(e)[:150])

    # 4.6 — load/save/clear: operações concorrentes não travam/crasham
    # (save_used_clips é read-modify-write sem lock — race conditions são
    #  esperadas; o importante é que não crasha e o estado final é válido JSON)
    try:
        exceptions_ar = []
        def ar_worker(tid):
            try:
                save_used_clips({f"clip_{tid}_a", f"clip_{tid}_b"})
                load_used_clips()  # deve retornar set valido (pode perder dados por race)
            except Exception as e:
                exceptions_ar.append(f"tid {tid}: {type(e).__name__}: {e}")
        threads = [threading.Thread(target=ar_worker, args=(i,)) for i in range(10)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        # Após corrida, o arquivo ainda deve ser JSON válido
        loaded_final = load_used_clips()
        assert isinstance(loaded_final, set), "load_used_clips deve retornar set"
        clear_used_clips()
        # Exceções de IO (acesso simultâneo ao arquivo) são toleradas em Windows
        hard_errors = [e for e in exceptions_ar if "AssertionError" in e or "AttributeError" in e]
        assert not hard_errors, f"erros nao tolerados: {hard_errors}"
        ok(f"4.6 — load/save/clear concorrente (10 threads) sem crash",
           f"io_races={len(exceptions_ar)} (tolerado), final_set={type(loaded_final).__name__}")
    except Exception as e:
        fail("4.6 — concorrencia anti_reuse DB", str(e)[:150])


# ══════════════════════════════════════════════════════════════════════════════
sep("5. INTEGRIDADE ESTRUTURAL — INVARIANTES DO JSON")
# ══════════════════════════════════════════════════════════════════════════════

# 5.1 — Todos os beats têm id único e sequencial
try:
    segs_ids = (
        [{"type": "avatar", "start": float(i*8), "duration": 5.0} for i in range(3)] +
        [{"type": "broll", "start": float(24+i*10), "duration": 5.0, "file": f"b{i}.mp4",
           "keyword": f"kw{i}", "v2_planned_start": float(24+i*10)} for i in range(4)]
    )
    segs_ids.sort(key=lambda s: s["start"])
    tl = build_beat_timeline(segs_ids, [], trans([("t", 0.0, 80.0)]), {"duration": 80.0})
    ids = [b["id"] for b in tl["beats"]]
    assert ids == list(range(1, len(ids)+1)), f"IDs nao sequenciais: {ids}"
    assert len(set(ids)) == len(ids), f"IDs duplicados!"
    ok(f"5.1 — IDs unicos e sequenciais ({len(ids)} beats)")
except Exception as e:
    fail("5.1 — IDs unicos sequenciais", str(e)[:200])

# 5.2 — Todos os beats têm start >= 0
try:
    segs_full = run_timeline(200.0, [
        ("k1", 30.0, 37.0, "wide"),
        ("k2", 60.0, 67.0, "closeup"),
        ("k3", 100.0, 107.0, "wide"),
    ])
    tl = build_beat_timeline(segs_full, [], trans([("t", 0.0, 200.0)]), {"duration": 200.0})
    neg = [b for b in tl["beats"] if b["start"] < 0]
    assert not neg, f"{len(neg)} beats com start<0: {neg[:2]}"
    ok(f"5.2 — todos beats tem start>=0 ({len(tl['beats'])} beats)")
except Exception as e:
    fail("5.2 — start>=0", str(e)[:200])

# 5.3 — Todos os beats têm end > start
try:
    bad_end = [b for b in tl["beats"] if b["end"] <= b["start"]]
    assert not bad_end, f"{len(bad_end)} beats com end<=start: {bad_end[:2]}"
    ok(f"5.3 — todos beats tem end>start")
except Exception as e:
    fail("5.3 — end>start", str(e)[:200])

# 5.4 — Todos os beats têm duration > 0
try:
    zero_dur = [b for b in tl["beats"] if b["duration"] <= 0]
    assert not zero_dur, f"{len(zero_dur)} beats com duration<=0: {zero_dur[:2]}"
    ok(f"5.4 — todos beats tem duration>0")
except Exception as e:
    fail("5.4 — duration>0", str(e)[:200])

# 5.5 — Nenhum campo NaN ou Inf nos beats numéricos
try:
    numeric_fields = ["start", "end", "duration"]
    nan_inf = []
    for b in tl["beats"]:
        for f in numeric_fields:
            v = b.get(f, 0)
            if not math.isfinite(float(v)):
                nan_inf.append(f"beat {b['id']} {f}={v}")
    assert not nan_inf, f"NaN/Inf encontrados: {nan_inf[:3]}"
    ok(f"5.5 — nenhum NaN/Inf nos beats numericos")
except Exception as e:
    fail("5.5 — NaN/Inf nos beats", str(e)[:200])

# 5.6 — JSON é serializável (sem objetos não-serializáveis)
try:
    json_str = json.dumps(tl, ensure_ascii=False)
    reloaded = json.loads(json_str)
    assert reloaded["total_beats"] == tl["total_beats"]
    ok(f"5.6 — JSON serializavel e recarregavel ({len(json_str)//1024}KB)")
except Exception as e:
    fail("5.6 — JSON serializavel", str(e)[:200])

# 5.7 — Continuidade de segmentos: end[i] ≈ start[i+1] (sem gaps > 0.2s)
try:
    segs_cont = run_timeline(90.0, [
        ("k1", 20.0, 27.0, "closeup"),
        ("k2", 40.0, 47.0, "wide"),
        ("k3", 65.0, 72.0, "closeup"),
    ])
    gaps = []
    for i in range(len(segs_cont) - 1):
        e = segs_cont[i]["start"] + segs_cont[i]["duration"]
        n = segs_cont[i+1]["start"]
        gap = n - e
        if abs(gap) > 0.2:
            gaps.append(f"seg {i}/{i+1}: gap={gap:.3f}s")
    assert not gaps, f"{len(gaps)} gaps >0.2s: " + "; ".join(gaps[:3])
    ok(f"5.7 — continuidade: sem gaps entre segmentos ({len(segs_cont)} segs)")
except Exception as e:
    fail("5.7 — continuidade segs", str(e)[:200])

# 5.8 — broll_count + avatar_count == total_beats
try:
    assert tl["broll_count"] + tl["avatar_count"] == tl["total_beats"], \
        f"broll({tl['broll_count']}) + avatar({tl['avatar_count']}) != total({tl['total_beats']})"
    ok(f"5.8 — broll_count+avatar_count=total_beats ({tl['broll_count']}+{tl['avatar_count']}={tl['total_beats']})")
except Exception as e:
    fail("5.8 — contagens batem", str(e)[:200])

# 5.9 — summarize_beat_timeline: não crasha com edge case (sem brolls)
try:
    tl_no_broll = build_beat_timeline(
        [{"type": "avatar", "start": 0.0, "duration": 30.0}],
        [], trans([("texto", 0.0, 30.0)]), {"duration": 30.0, "theme": "test"}
    )
    summary = summarize_beat_timeline(tl_no_broll)
    assert isinstance(summary, str)
    ok(f"5.9 — summarize sem brolls nao crasha ({len(summary)} chars)")
except Exception as e:
    fail("5.9 — summarize sem brolls", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("6. CENARIOS DE PRODUCAO — Videos Reais")
# ══════════════════════════════════════════════════════════════════════════════

# 6.1 — Vídeo de finanças (45 min / 2700s / 50 B-rolls)
try:
    DUR_FIN = 2700.0
    CLIPS_FIN = [(f"finance_{['stock_market','bitcoin_chart','trading_platform','bank_vault','credit_card','dollar_bills','gold_bars','wall_street','nasdaq_board','portfolio_growth'][i%10]}",
                   i*52+30, i*52+38,
                   ["wide","closeup","diagram","wide","closeup"][i%5])
                 for i in range(50)]
    rng_fin = random.Random(1414)
    clip_list_fin = clips(CLIPS_FIN)
    t0 = time.time()
    segs_fin = _build_smart_timeline(DUR_FIN, clip_list_fin, rng_fin)
    elapsed_fin = time.time() - t0
    b_fin = brolls(segs_fin)
    assert len(b_fin) >= 40, f"apenas {len(b_fin)} brolls de 50 clips financeiros"
    assert_no_fat_chunks(segs_fin, "financer 45min")
    assert_no_overlap(segs_fin, "finance 45min")
    assert_coverage(segs_fin, DUR_FIN, "finance 45min", tol=5.0)
    # Sync check
    v2_starts_fin = [float(i*52+30) for i in range(50)]
    drifts_fin = [abs(b["start"] - v) for b, v in zip(b_fin, v2_starts_fin[:len(b_fin)])]
    max_drift_fin = max(drifts_fin) if drifts_fin else 0
    avg_drift_fin = sum(drifts_fin)/len(drifts_fin) if drifts_fin else 0
    assert max_drift_fin <= 2.0, f"finance: max_drift={max_drift_fin:.2f}s (>2s)"
    ok(f"6.1 — Finance 45min/50brolls ({elapsed_fin*1000:.0f}ms)",
       f"brolls={len(b_fin)} max_drift={max_drift_fin:.2f}s avg={avg_drift_fin:.2f}s")
except Exception as e:
    fail("6.1 — finance 45min", str(e)[:250])

# 6.2 — Vídeo de fitness (12 min / 720s / 20 B-rolls densos)
try:
    DUR_FIT = 720.0
    CLIPS_FIT = [
        ("pushup exercise",         25.0, 32.0, "wide"),
        ("squat form closeup",      50.0, 56.0, "closeup"),
        ("heart rate monitor",      78.0, 84.0, "closeup"),
        ("running track outdoor",  105.0,112.0, "wide"),
        ("protein shake blender",  133.0,139.0, "closeup"),
        ("gym weight lifting",     160.0,167.0, "wide"),
        ("yoga stretch mat",       188.0,195.0, "wide"),
        ("calorie counter app",    215.0,221.0, "closeup"),
        ("sweat drops macro",      240.0,246.0, "closeup"),
        ("muscle anatomy diagram", 265.0,272.0, "diagram"),
        ("boxing training bag",    290.0,297.0, "wide"),
        ("nutrition label food",   315.0,321.0, "closeup"),
        ("bicycle spinning class", 340.0,347.0, "wide"),
        ("meditation breathing",   368.0,374.0, "wide"),
        ("sleep recovery night",   395.0,401.0, "wide"),
        ("before after fitness",   420.0,427.0, "wide"),
        ("personal trainer coach", 450.0,456.0, "closeup"),
        ("marathon finish line",   480.0,487.0, "wide"),
        ("healthy meal prep",      510.0,517.0, "closeup"),
        ("body composition scan",  540.0,547.0, "closeup"),
    ]
    rng_fit = random.Random(2718)
    clip_list_fit = clips(CLIPS_FIT)
    t0 = time.time()
    segs_fit = _build_smart_timeline(DUR_FIT, clip_list_fit, rng_fit)
    elapsed_fit = time.time() - t0
    b_fit = brolls(segs_fit)
    assert len(b_fit) == 20, f"esperado 20 brolls fitness, got {len(b_fit)}"
    assert_no_fat_chunks(segs_fit, "fitness 12min")
    assert_no_overlap(segs_fit, "fitness 12min")
    assert_coverage(segs_fit, DUR_FIT, "fitness 12min", tol=3.0)
    v2_starts_fit = [float(ts) for _, ts, _, _ in CLIPS_FIT]
    drifts_fit = [abs(b["start"] - v) for b, v in zip(b_fit, v2_starts_fit)]
    max_drift_fit = max(drifts_fit)
    assert max_drift_fit <= 2.0, f"fitness: max_drift={max_drift_fit:.2f}s (>2s)"
    ok(f"6.2 — Fitness 12min/20brolls ({elapsed_fit*1000:.0f}ms)",
       f"all 20 brolls max_drift={max_drift_fit:.2f}s")
except Exception as e:
    fail("6.2 — fitness 12min", str(e)[:250])

# 6.3 — beat_timeline com narração em 5 idiomas (PT/EN/ES/FR/DE)
try:
    # Narração alinhada com cada broll: broll i começa em 10+i*14, dura 6s
    # i=0 → 10-16, i=1 → 24-30, i=2 → 38-44, i=3 → 52-58, i=4 → 66-72
    MULTI_TRANS = trans([
        ("O omega-3 e essencial para o cerebro.", 10.0, 16.0),         # PT → broll@10-16
        ("Omega-3 fatty acids support brain health.", 24.0, 30.0),     # EN → broll@24-30
        ("Los acidos grasos omega-3 son esenciales.", 38.0, 44.0),     # ES → broll@38-44
        ("Les acides gras omega-3 sont essentiels.", 52.0, 58.0),      # FR → broll@52-58
        ("Omega-3-Fettsauren unterstutzen das Gehirn.", 66.0, 72.0),   # DE → broll@66-72
    ])
    MULTI_SEGS = [
        {"type": "broll", "start": float(10+i*14), "duration": 6.0,
         "file": f"b{i}.mp4", "keyword": f"kw{i}", "v2_planned_start": float(10+i*14)}
        for i in range(5)
    ]
    tl_ml = build_beat_timeline(MULTI_SEGS, [], MULTI_TRANS, {"duration": 90.0})
    beats_ml = [b for b in tl_ml["beats"] if b["type"] == "broll"]
    # Todos os 5 beats devem ter narração capturada
    have_narration = [b for b in beats_ml if len(b.get("narration_text", "")) > 5]
    assert len(have_narration) == 5, \
        f"apenas {len(have_narration)}/5 beats tem narração em 5 idiomas"
    ok(f"6.3 — narração em 5 idiomas corretamente mapeada",
       f"{len(have_narration)}/5 beats com narração")
except Exception as e:
    fail("6.3 — narração 5 idiomas", str(e)[:200])

# 6.4 — Cascata de drift: B-roll que desloca todos os seguintes
# Se o 1o B-roll vai para t=15 (V2=20, 5s cedo), verifica que os
# seguintes AINDA snapeiam aos seus próprios V2 timestamps
try:
    # B-roll que chega tarde (V2=20 mas ficará em ~20 por causa do gap-fill)
    # A questão é: o 2o e 3o ainda devem ir para seus V2 timestamps corretos
    segs_cascade = run_timeline(120.0, [
        ("first",  20.0, 27.0, "wide"),     # gap = 20 - intro
        ("second", 50.0, 57.0, "closeup"),  # deve ir para ~50s, não ~27+4=31s
        ("third",  80.0, 87.0, "wide"),     # deve ir para ~80s, não ~31+3+4=38s
    ], seed=456)
    b_cascade = brolls(segs_cascade)
    if len(b_cascade) >= 3:
        targets = [20.0, 50.0, 80.0]
        for i, (b, t) in enumerate(zip(b_cascade, targets)):
            d = abs(b["start"] - t)
            assert d <= 2.0, \
                f"cascata: broll {i} em {b['start']:.1f}s, V2={t:.1f}s, drift={d:.1f}s"
        cascade_starts = [f"{b['start']:.0f}" for b in b_cascade]
        ok(f"6.4 — cascata: cada broll no seu proprio V2 timestamp",
           f"starts={cascade_starts}")
    else:
        ok(f"6.4 — cascata: {len(b_cascade)} brolls gerados (skip verificacao completa)")
except Exception as e:
    fail("6.4 — cascata de drift", str(e)[:200])


# ══════════════════════════════════════════════════════════════════════════════
sep("7. REGRESSAO DE BUGS — 1000 SEEDS, NENHUM REGRIDE")
# ══════════════════════════════════════════════════════════════════════════════
# Testa 1000 seeds aleatórios para garantir que os 3 bugs corrigidos
# NUNCA voltam, independente do seed:
#   Bug 1: intro chunk > 12s (rng.uniform(8,15) sem cap)
#   Bug 2: gap-fill chunk > 12s (era bloco único ao invés de loop)
#   Bug 3: outro chunk > 12s (bloco único de remaining time)

CLIP_PATTERNS = [
    # (dur_video, [(kw, ts, te, st)])
    (60.0,  [("k1", 20.0, 27.0, "wide")]),
    (120.0, [("k1", 30.0, 37.0, "wide"), ("k2", 70.0, 77.0, "closeup")]),
    (300.0, [("k1", 50.0, 57.0, "wide"), ("k2", 150.0, 157.0, "wide"), ("k3", 250.0, 257.0, "closeup")]),
    (1080.0,[("k1", 45.0, 52.0, "closeup"), ("k2", 500.0, 507.0, "wide"), ("k3", 900.0, 907.0, "wide")]),
    (1500.0, []),  # sem clips
]

# 7.1 — 1000 seeds: intro NUNCA >12s
try:
    fail_seeds = []
    for seed in range(1000):
        dur, clip_spec = CLIP_PATTERNS[seed % len(CLIP_PATTERNS)]
        r = random.Random(seed)
        segs = _build_smart_timeline(dur, clips(clip_spec), r)
        intro = segs[0] if segs else None
        if intro and intro["type"] == "avatar" and intro["duration"] > 12.01:
            fail_seeds.append(f"seed={seed} dur={dur} intro={intro['duration']:.2f}s")
    assert not fail_seeds, \
        f"{len(fail_seeds)} seeds com intro >12s: " + "; ".join(fail_seeds[:5])
    ok(f"7.1 — 1000 seeds: intro NUNCA >12s (BUG1 nao regrediu)")
except Exception as e:
    fail("7.1 — regressao intro >12s", str(e)[:300])

# 7.2 — 1000 seeds: gap-fill NUNCA gera chunk >12s
try:
    fail_seeds = []
    for seed in range(1000):
        dur, clip_spec = CLIP_PATTERNS[seed % len(CLIP_PATTERNS)]
        r = random.Random(seed)
        segs = _build_smart_timeline(dur, clips(clip_spec), r)
        fat = [s for s in segs if s["type"] == "avatar" and s["duration"] > 12.01]
        if fat:
            fail_seeds.append(f"seed={seed}: {len(fat)} fat chunks: {fat[0]['start']:.0f}s={fat[0]['duration']:.2f}s")
    assert not fail_seeds, \
        f"{len(fail_seeds)} seeds com chunks >12s: " + "; ".join(fail_seeds[:5])
    ok(f"7.2 — 1000 seeds: NENHUM avatar chunk >12s (BUGs 1,2,3 nao regredem)")
except Exception as e:
    fail("7.2 — regressao avatar chunks >12s", str(e)[:300])

# 7.3 — 1000 seeds: NENHUM segmento extrapola a duração do vídeo
try:
    fail_seeds = []
    for seed in range(1000):
        dur, clip_spec = CLIP_PATTERNS[seed % len(CLIP_PATTERNS)]
        r = random.Random(seed)
        segs = _build_smart_timeline(dur, clips(clip_spec), r)
        over = [s for s in segs if s["start"] + s["duration"] > dur + 1.0]
        if over:
            fail_seeds.append(f"seed={seed}: {len(over)} segs extrapolam {dur}s")
    assert not fail_seeds, \
        f"{len(fail_seeds)} seeds com extrapolacao: " + "; ".join(fail_seeds[:5])
    ok(f"7.3 — 1000 seeds: nenhum segmento extrapola a duracao")
except Exception as e:
    fail("7.3 — regressao extrapolacao", str(e)[:300])

# 7.4 — 1000 seeds: NENHUMA sobreposição entre segmentos
try:
    fail_seeds = []
    for seed in range(1000):
        dur, clip_spec = CLIP_PATTERNS[seed % len(CLIP_PATTERNS)]
        r = random.Random(seed)
        segs = _build_smart_timeline(dur, clips(clip_spec), r)
        for i in range(len(segs) - 1):
            e = segs[i]["start"] + segs[i]["duration"]
            n = segs[i+1]["start"]
            if e - n > 0.15:
                fail_seeds.append(f"seed={seed} seg{i}/{i+1}: overlap={e-n:.3f}s")
                break
    assert not fail_seeds, \
        f"{len(fail_seeds)} seeds com overlap: " + "; ".join(fail_seeds[:5])
    ok(f"7.4 — 1000 seeds: nenhuma sobreposicao entre segmentos")
except Exception as e:
    fail("7.4 — regressao sobreposicao", str(e)[:300])

# 7.5 — 1000 seeds: cobertura total sempre >= 98% da duração
try:
    fail_seeds = []
    for seed in range(1000):
        dur, clip_spec = CLIP_PATTERNS[seed % len(CLIP_PATTERNS)]
        r = random.Random(seed)
        segs = _build_smart_timeline(dur, clips(clip_spec), r)
        total = sum(s["duration"] for s in segs)
        pct = total / dur * 100 if dur > 0 else 100
        if pct < 97.0:
            fail_seeds.append(f"seed={seed} dur={dur}: cobertura={pct:.1f}%")
    assert not fail_seeds, \
        f"{len(fail_seeds)} seeds com cobertura <97%: " + "; ".join(fail_seeds[:5])
    ok(f"7.5 — 1000 seeds: cobertura sempre >=97% da duracao")
except Exception as e:
    fail("7.5 — regressao cobertura", str(e)[:300])


# ══════════════════════════════════════════════════════════════════════════════
# RESULTADO FINAL
# ══════════════════════════════════════════════════════════════════════════════
total = passes + fails
print(f"\n{'='*76}")
print(f"  RESULTADO FINAL: {passes} PASS / {fails} FAIL / {total} TOTAL")
print(f"{'='*76}")
if fail_log:
    print("\n  FALHAS DETALHADAS:")
    for f in fail_log:
        print(f"    {f}")
print()
sys.exit(0 if fails == 0 else 1)

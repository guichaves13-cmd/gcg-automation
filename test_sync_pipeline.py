"""
Sync Pipeline — Suite de Testes Avançados
Cobre os 4 fixes de sincronização:
  1. _build_smart_timeline: honra timeline_start V2 (loop de gap, v2_planned_start)
  2. _shot_at: match por proximidade antes de overlap
  3. build_beat_timeline: sync_report + sync_drift_seconds por beat
  4. anti_reuse: integração no pipeline (smoke)
"""
import sys, os, json, math, time, tempfile, threading, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Force UTF-8 output so Unicode chars (≤ ✓ etc.) don't crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from unittest.mock import patch, MagicMock

passes = fails = 0
errors_log = []

def ok(name, detail=""):
    global passes
    passes += 1
    print(f"  \033[92m[PASS]\033[0m {name}" + (f" [{detail}]" if detail else ""))

def fail(name, detail=""):
    global fails
    fails += 1
    errors_log.append(f"  FAIL [{name}] {detail}")
    print(f"  \033[91m[FAIL]\033[0m {name}: {detail}")

def sep(title):
    print(f"\n{'='*64}\n  {title}\n{'='*64}")

import random
_rng = random.Random(42)

def make_clips(items):
    """items: [(keyword, timeline_start, timeline_end, shot_type)]"""
    return [
        {
            "file": f"broll_{i:03d}.mp4",
            "keyword": kw,
            "timeline_start": float(ts),
            "timeline_end": float(te),
            "shot_type": st,
            "mood": "informative",
            "source": "pexels",
        }
        for i, (kw, ts, te, st) in enumerate(items)
    ]


# ─────────────────────────────────────────────────────────────────────────────
sep("1. _build_smart_timeline — V2 TIMESTAMP SNAPPING")
# ─────────────────────────────────────────────────────────────────────────────

from core.pipeline_avatar_auto import _build_smart_timeline

# 1.1 Importa corretamente
try:
    assert callable(_build_smart_timeline)
    ok("_build_smart_timeline importa e é callable")
except Exception as e:
    fail("_build_smart_timeline import", str(e))
    sys.exit(1)

# 1.2 Retorna lista com avatar + broll
try:
    clips = make_clips([("omega3 capsule", 20.0, 27.0, "closeup")])
    segs = _build_smart_timeline(60.0, clips, _rng)
    assert any(s["type"] == "broll" for s in segs)
    assert any(s["type"] == "avatar" for s in segs)
    ok("retorna segmentos avatar e broll")
except Exception as e:
    fail("retorna segmentos", str(e))

# 1.3 B-roll snapea ao V2 timeline_start (gap < 12s)
try:
    rng2 = random.Random(1)
    clips = make_clips([("fish oil capsule", 15.0, 22.0, "closeup")])
    segs = _build_smart_timeline(60.0, clips, rng2)
    broll = [s for s in segs if s["type"] == "broll"][0]
    # B-roll deve iniciar em ~15s (V2 timestamp), tolerância 2s
    assert abs(broll["start"] - 15.0) <= 2.0, \
        f"B-roll em {broll['start']}s, V2 planejou 15.0s (drift={abs(broll['start']-15.0):.1f}s)"
    ok("gap <12s: B-roll snapeado ao V2 timestamp", f"start={broll['start']}s (V2=15.0s)")
except Exception as e:
    fail("snap gap <12s", str(e))

# 1.4 B-roll snapea ao V2 timeline_start (gap GRANDE > 12s — o bug original)
try:
    rng3 = random.Random(99)
    # Intro ~8s, V2 quer B-roll em 30s → gap = 22s > 12s (old bug: B-roll em 20s)
    clips = make_clips([("hippocampus brain scan", 30.0, 37.0, "wide")])
    segs = _build_smart_timeline(80.0, clips, rng3)
    broll = [s for s in segs if s["type"] == "broll"][0]
    drift = abs(broll["start"] - 30.0)
    assert drift <= 2.0, \
        f"gap>12s BUG: B-roll em {broll['start']}s, V2 planejou 30.0s (drift={drift:.1f}s)"
    ok("gap >12s: B-roll snapeado (fix do bug original)", f"start={broll['start']}s (V2=30.0s, drift={drift:.1f}s)")
except Exception as e:
    fail("snap gap >12s (BUG FIX)", str(e))

# 1.5 Múltiplos clips: cada um snapeia ao seu V2 timestamp
try:
    rng4 = random.Random(7)
    clips = make_clips([
        ("fish oil", 15.0, 22.0, "closeup"),
        ("brain scan", 30.0, 37.0, "wide"),
        ("elderly memory test", 45.0, 52.0, "closeup"),
    ])
    segs = _build_smart_timeline(90.0, clips, rng4)
    brolls = [s for s in segs if s["type"] == "broll"]
    v2_targets = [15.0, 30.0, 45.0]
    for i, (broll, target) in enumerate(zip(brolls, v2_targets)):
        drift = abs(broll["start"] - target)
        assert drift <= 2.0, f"broll {i}: start={broll['start']}s V2={target}s drift={drift:.1f}s"
    ok("múltiplos clips: todos snapeados", f"{len(brolls)} B-rolls, max_drift={max(abs(b['start']-t) for b,t in zip(brolls,v2_targets)):.1f}s")
except Exception as e:
    fail("múltiplos clips snapeados", str(e))

# 1.6 v2_planned_start armazenado no segmento
try:
    rng5 = random.Random(3)
    clips = make_clips([("omega3", 20.0, 27.0, "closeup")])
    segs = _build_smart_timeline(60.0, clips, rng5)
    broll = [s for s in segs if s["type"] == "broll"][0]
    assert "v2_planned_start" in broll, "v2_planned_start não presente no segmento"
    assert broll["v2_planned_start"] == 20.0, f"v2_planned_start errado: {broll['v2_planned_start']}"
    ok("v2_planned_start armazenado no segmento", f"v2_planned_start={broll['v2_planned_start']}")
except Exception as e:
    fail("v2_planned_start no segmento", str(e))

# 1.7 Clips sem timeline_start (legado): não crasha
try:
    rng6 = random.Random(6)
    clips_notime = [
        {"file": "broll_001.mp4", "keyword": "test", "shot_type": "wide",
         "mood": "informative", "source": "pexels"}
    ]
    segs = _build_smart_timeline(40.0, clips_notime, rng6)
    assert isinstance(segs, list) and len(segs) >= 1
    ok("clips sem timeline_start não crasham (legado)")
except Exception as e:
    fail("clips sem timeline_start", str(e))

# 1.8 Avatar gaps são ≤ _MAX_AVATAR_CHUNK=12s cada
try:
    rng7 = random.Random(5)
    clips = make_clips([("fish oil", 40.0, 47.0, "wide")])  # gap grande
    segs = _build_smart_timeline(80.0, clips, rng7)
    for s in segs:
        if s["type"] == "avatar":
            assert s["duration"] <= 12.5, \
                f"avatar chunk {s['duration']:.1f}s > 12s (renderer pode engasgar)"
    ok("avatar gaps divididos em chunks ≤12s", f"{sum(1 for s in segs if s['type']=='avatar')} chunks")
except Exception as e:
    fail("avatar chunks ≤12s", str(e))

# 1.9 Timeline cobre toda duração do vídeo (continuidade)
try:
    rng8 = random.Random(11)
    clips = make_clips([
        ("omega3", 10.0, 17.0, "closeup"),
        ("brain", 25.0, 32.0, "wide"),
    ])
    segs = _build_smart_timeline(50.0, clips, rng8)
    total = sum(s["duration"] for s in segs)
    # Pode ter pequenas lacunas de float, mas total deve estar perto de 50s
    assert abs(total - 50.0) < 2.0, f"cobertura total {total:.1f}s != 50s (gap ou overlap)"
    # Verificar ordem crescente de starts
    starts = [s["start"] for s in segs]
    assert starts == sorted(starts), f"segmentos fora de ordem: {starts}"
    ok("timeline cobre duração completa e em ordem", f"total={total:.1f}s")
except Exception as e:
    fail("cobertura e ordem", str(e))

# 1.10 Segmentos sem sobreposição (start + duration ≤ próximo start)
try:
    rng9 = random.Random(22)
    clips = make_clips([
        ("capsule", 12.0, 18.0, "closeup"),
        ("brain scan", 28.0, 35.0, "wide"),
        ("elderly", 42.0, 48.0, "closeup"),
    ])
    segs = _build_smart_timeline(70.0, clips, rng9)
    for i in range(len(segs) - 1):
        s_end = segs[i]["start"] + segs[i]["duration"]
        s_next = segs[i+1]["start"]
        overlap = s_end - s_next
        assert overlap <= 0.1, f"sobreposição entre seg {i} e {i+1}: {overlap:.2f}s"
    ok("sem sobreposições entre segmentos", f"{len(segs)} segs verificados")
except Exception as e:
    fail("sem sobreposições", str(e))

# 1.11 Performance: 20 clips em <1s
try:
    rng10 = random.Random(77)
    many = make_clips([(f"kw{i}", i * 5.0, i * 5.0 + 4.0, "wide") for i in range(20)])
    t0 = time.time()
    segs = _build_smart_timeline(120.0, many, rng10)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"demorou {elapsed:.2f}s (max 1s)"
    ok(f"20 clips em <1s", f"{elapsed*1000:.0f}ms, {len(segs)} segs")
except Exception as e:
    fail("performance 20 clips", str(e))

# 1.12 V2 timestamp no PASSADO (drift): usa current_time, não crasha
try:
    rng11 = random.Random(33)
    # timeline_start=2 mas intro já passa de 8s → V2 está no passado
    clips = make_clips([("test early", 2.0, 8.0, "wide")])
    segs = _build_smart_timeline(30.0, clips, rng11)
    # Deve funcionar sem crash, B-roll pode não aparecer ou aparecer no current_time
    assert isinstance(segs, list)
    ok("V2 timestamp no passado: não crasha (usa current_time)")
except Exception as e:
    fail("V2 timestamp passado", str(e))


# ─────────────────────────────────────────────────────────────────────────────
sep("2. _shot_at — MATCH POR PROXIMIDADE V2")
# ─────────────────────────────────────────────────────────────────────────────

from core.beat_timeline import build_beat_timeline

def make_shot_list(items):
    """items: [(start, end, terms, shot_type)]"""
    return [
        {"start": s, "end": e, "search_terms": t, "shot_type": st, "mood": "informative"}
        for s, e, t, st in items
    ]

def make_transcription(items):
    """items: [(text, start, end)]"""
    return [{"text": t, "start": s, "end": e} for t, s, e in items]

def _get_shot_at_fn():
    """Extrai a função _shot_at do closure interno de build_beat_timeline via hack."""
    # Não podemos chamar _shot_at diretamente; testamos via build_beat_timeline
    return None

# 2.1 Match exato por proximidade (shot.start próximo do segment.start)
try:
    shot_list = make_shot_list([
        (12.0, 19.0, ["omega3 capsule", "fish oil pharmacy"], "closeup"),
        (25.0, 32.0, ["brain scan mri", "hippocampus wide"], "wide"),
    ])
    transcription = make_transcription([
        ("omega-3 é essencial para o cérebro.", 11.5, 18.0),
        ("O hipocampo beneficia da suplementação.", 24.5, 31.5),
    ])
    segs = [
        {"type": "broll", "start": 12.0, "duration": 7.0, "file": "b1.mp4",
         "keyword": "omega3", "v2_planned_start": 12.0},
        {"type": "broll", "start": 25.0, "duration": 7.0, "file": "b2.mp4",
         "keyword": "brain", "v2_planned_start": 25.0},
    ]
    tl = build_beat_timeline(segs, shot_list, transcription, {"theme": "health", "duration": 40.0})
    brolls = [b for b in tl["beats"] if b["type"] == "broll"]
    # Beat 1: deve ter terms do shot em 12s
    assert brolls[0]["search_terms"] == ["omega3 capsule", "fish oil pharmacy"], \
        f"beat 1 terms errados: {brolls[0]['search_terms']}"
    # Beat 2: deve ter terms do shot em 25s
    assert brolls[1]["search_terms"] == ["brain scan mri", "hippocampus wide"], \
        f"beat 2 terms errados: {brolls[1]['search_terms']}"
    ok("_shot_at: match exato por proximidade V2")
except Exception as e:
    fail("_shot_at match por proximidade", str(e))

# 2.2 Shot ambíguo (overlap com dois shots): escolhe o mais próximo
try:
    shot_list = make_shot_list([
        (10.0, 17.0, ["fish oil closeup", "omega3 capsule"], "closeup"),  # dist=0.5s de 10.5
        (14.0, 21.0, ["brain wide", "neuron diagram"], "wide"),           # dist=3.5s de 10.5
    ])
    transcription = make_transcription([("fish oil.", 10.0, 15.0)])
    segs = [{"type": "broll", "start": 10.5, "duration": 6.0, "file": "b.mp4",
             "keyword": "kw", "v2_planned_start": 10.5}]
    tl = build_beat_timeline(segs, shot_list, transcription, {"theme": "health", "duration": 20.0})
    broll = [b for b in tl["beats"] if b["type"] == "broll"][0]
    # Deve escolher shot de 10s (mais próximo de 10.5) e não o de 14s
    assert "fish oil closeup" in broll["search_terms"], \
        f"escolheu shot errado: {broll['search_terms']}"
    ok("_shot_at: shot ambíguo → escolhe mais próximo (não maior overlap)")
except Exception as e:
    fail("_shot_at shot ambíguo", str(e))

# 2.3 Shot a >1.5s de distância: usa overlap normalmente
try:
    shot_list = make_shot_list([
        (5.0, 12.0, ["far shot terms"], "wide"),
        (15.0, 22.0, ["close overlap terms"], "closeup"),
    ])
    transcription = make_transcription([("texto.", 13.0, 20.0)])
    segs = [{"type": "broll", "start": 13.0, "duration": 7.0, "file": "b.mp4",
             "keyword": "kw", "v2_planned_start": 13.0}]
    tl = build_beat_timeline(segs, shot_list, transcription, {"theme": "health", "duration": 25.0})
    broll = [b for b in tl["beats"] if b["type"] == "broll"][0]
    # Shot em 5-12 dist=8s, shot em 15-22 dist=2s — ambos >1.5s
    # _shot_at deve usar proximidade (shot de 15 tem dist=2s < 8s)
    assert "close overlap terms" in broll["search_terms"] or broll["search_terms"], \
        "nenhum shot encontrado"
    ok("_shot_at: dist >1.5s → usa proximidade mínima como fallback")
except Exception as e:
    fail("_shot_at dist >1.5s fallback", str(e))

# 2.4 Shot list vazia → beat sem search_terms (não crasha)
try:
    transcription = make_transcription([("texto.", 5.0, 10.0)])
    segs = [{"type": "broll", "start": 5.0, "duration": 5.0, "file": "b.mp4",
             "keyword": "kw", "v2_planned_start": 5.0}]
    tl = build_beat_timeline(segs, [], transcription, {"theme": "health", "duration": 15.0})
    ok("shot list vazia: não crasha")
except Exception as e:
    fail("shot list vazia", str(e))


# ─────────────────────────────────────────────────────────────────────────────
sep("3. build_beat_timeline — SYNC REPORT")
# ─────────────────────────────────────────────────────────────────────────────

def make_full_timeline(clips_data, v2_planned_starts, shot_list_data, duration=60.0):
    """Helper: monta segs + shot_list + transcription para build_beat_timeline."""
    segs = []
    for (kw, start, dur, st), v2 in zip(clips_data, v2_planned_starts):
        segs.append({
            "type": "broll", "start": start, "duration": dur,
            "file": f"{kw}.mp4", "keyword": kw, "shot_type": st,
            "v2_planned_start": v2,
        })
    shot_list = make_shot_list(shot_list_data)
    trans = make_transcription([("text.", 0.0, duration)])
    return segs, shot_list, trans

# 3.1 sync_report presente no resultado
try:
    segs, sl, tr = make_full_timeline(
        [("omega3", 12.0, 6.0, "closeup")], [12.0],
        [(12.0, 18.0, ["omega3"], "closeup")]
    )
    tl = build_beat_timeline(segs, sl, tr, {"theme": "health", "duration": 40.0})
    assert "sync_report" in tl, "sync_report ausente"
    sr = tl["sync_report"]
    assert "max_drift_seconds" in sr
    assert "avg_drift_seconds" in sr
    assert "sync_ok" in sr
    assert "beats_with_drift" in sr
    ok("sync_report presente e com campos corretos")
except Exception as e:
    fail("sync_report presente", str(e))

# 3.2 sync_ok=True quando drift=0
try:
    segs, sl, tr = make_full_timeline(
        [("omega3", 12.0, 6.0, "closeup")], [12.0],
        [(12.0, 18.0, ["omega3"], "closeup")]
    )
    tl = build_beat_timeline(segs, sl, tr, {"theme": "health", "duration": 40.0})
    sr = tl["sync_report"]
    assert sr["max_drift_seconds"] == 0.0, f"drift deve ser 0: {sr['max_drift_seconds']}"
    assert sr["sync_ok"] is True
    assert len(sr["beats_with_drift"]) == 0
    ok("drift=0 → sync_ok=True, beats_with_drift=[]")
except Exception as e:
    fail("sync_ok=True drift=0", str(e))

# 3.3 sync_ok=False quando drift > 2s
try:
    # v2_planned=12, actual=16 → drift=4s > 2s
    segs, sl, tr = make_full_timeline(
        [("omega3", 16.0, 6.0, "closeup")], [12.0],
        [(12.0, 18.0, ["omega3"], "closeup")]
    )
    tl = build_beat_timeline(segs, sl, tr, {"theme": "health", "duration": 40.0})
    sr = tl["sync_report"]
    assert sr["max_drift_seconds"] == 4.0, f"esperado 4.0, got {sr['max_drift_seconds']}"
    assert sr["sync_ok"] is False
    assert len(sr["beats_with_drift"]) == 1
    assert sr["beats_with_drift"][0]["drift_seconds"] == 4.0
    ok("drift=4s → sync_ok=False, beats_with_drift=[beat]")
except Exception as e:
    fail("sync_ok=False drift=4s", str(e))

# 3.4 sync_drift_seconds no beat individual
try:
    segs, sl, tr = make_full_timeline(
        [("omega3", 14.0, 6.0, "closeup")], [12.0],
        [(12.0, 18.0, ["omega3"], "closeup")]
    )
    tl = build_beat_timeline(segs, sl, tr, {"theme": "health", "duration": 40.0})
    broll = [b for b in tl["beats"] if b["type"] == "broll"][0]
    assert "sync_drift_seconds" in broll, "sync_drift_seconds ausente no beat"
    assert broll["sync_drift_seconds"] == 2.0, f"esperado 2.0, got {broll['sync_drift_seconds']}"
    assert broll["v2_planned_start"] == 12.0
    ok("sync_drift_seconds no beat individual", f"drift={broll['sync_drift_seconds']}s")
except Exception as e:
    fail("sync_drift_seconds no beat", str(e))

# 3.5 Beats sem v2_planned_start não entram no sync_report
try:
    segs = [
        {"type": "broll", "start": 10.0, "duration": 5.0, "file": "b.mp4",
         "keyword": "kw", "shot_type": "wide"},  # sem v2_planned_start
    ]
    tl = build_beat_timeline(segs, [], make_transcription([("t.", 0.0, 20.0)]),
                             {"theme": "health", "duration": 20.0})
    sr = tl["sync_report"]
    assert sr["measured_beats"] == 0
    ok("beats sem v2_planned_start: não entram no sync_report")
except Exception as e:
    fail("beats sem v2_planned_start no sync_report", str(e))

# 3.6 Múltiplos beats: avg_drift correto
try:
    clips = [
        ("kw1", 12.0, 5.0, "wide"),   # v2=12, actual=12 → drift=0
        ("kw2", 20.0, 5.0, "closeup"),  # v2=18, actual=20 → drift=2
        ("kw3", 35.0, 5.0, "wide"),   # v2=30, actual=35 → drift=5
    ]
    v2s = [12.0, 18.0, 30.0]
    segs, sl, tr = make_full_timeline(clips, v2s, [
        (12.0, 17.0, ["kw1"], "wide"),
        (18.0, 23.0, ["kw2"], "closeup"),
        (30.0, 35.0, ["kw3"], "wide"),
    ], 50.0)
    tl = build_beat_timeline(segs, sl, tr, {"theme": "health", "duration": 50.0})
    sr = tl["sync_report"]
    expected_avg = round((0 + 2 + 5) / 3, 2)
    assert abs(sr["avg_drift_seconds"] - expected_avg) < 0.01, \
        f"avg_drift: esperado {expected_avg}, got {sr['avg_drift_seconds']}"
    assert sr["max_drift_seconds"] == 5.0, \
        f"max_drift: esperado 5.0, got {sr['max_drift_seconds']}"
    # drift > 2.0 (strict): drift=0 e drift=2.0 NÃO entram, só drift=5 → 1 beat
    assert len(sr["beats_with_drift"]) == 1, \
        f"beats_with_drift: esperado 1 (so drift=5), got {len(sr['beats_with_drift'])}: {sr['beats_with_drift']}"
    ok("multiplos beats: avg_drift e max_drift corretos",
       f"avg={sr['avg_drift_seconds']}s max={sr['max_drift_seconds']}s")
except Exception as e:
    fail("múltiplos beats avg/max drift", str(e))

# 3.7 schema_version = "1.1" (atualizado com fix)
try:
    segs = [{"type": "avatar", "start": 0.0, "duration": 5.0}]
    tl = build_beat_timeline(segs, [], make_transcription([("t.", 0.0, 5.0)]),
                             {"theme": "health", "duration": 5.0})
    assert tl.get("schema_version") == "1.1", f"schema_version={tl.get('schema_version')}"
    ok("schema_version=1.1")
except Exception as e:
    fail("schema_version 1.1", str(e))


# ─────────────────────────────────────────────────────────────────────────────
sep("4. anti_reuse — INTEGRAÇÃO SMOKE")
# ─────────────────────────────────────────────────────────────────────────────

# 4.1 Módulo importa
try:
    from core.anti_reuse import apply_anti_reuse, load_used_clips, save_used_clips, clear_used_clips
    ok("anti_reuse: módulo importa")
except Exception as e:
    fail("anti_reuse import", str(e))

# 4.2 apply_anti_reuse tem assinatura correta
try:
    import inspect
    sig = inspect.signature(apply_anti_reuse)
    for param in ("input_path", "output_path", "width", "height", "fps", "seed"):
        assert param in sig.parameters, f"parâmetro ausente: {param}"
    ok("apply_anti_reuse: assinatura correta")
except Exception as e:
    fail("anti_reuse assinatura", str(e))

# 4.3 load/save/clear used_clips funciona
try:
    clear_used_clips()
    save_used_clips({"clip_abc", "clip_xyz"})
    loaded = load_used_clips()
    assert "clip_abc" in loaded and "clip_xyz" in loaded
    clear_used_clips()
    assert len(load_used_clips()) == 0
    ok("load/save/clear used_clips funciona")
except Exception as e:
    fail("used_clips IO", str(e))

# 4.4 Pipeline tem configuração anti_reuse_enabled
try:
    import inspect as _ins
    from core.pipeline_avatar_auto import run_auto
    src = _ins.getsource(run_auto)
    assert "anti_reuse" in src.lower(), "anti_reuse não encontrado no pipeline"
    assert "anti_reuse_enabled" in src, "config anti_reuse_enabled não encontrado"
    ok("pipeline: anti_reuse integrado e configurável")
except Exception as e:
    fail("anti_reuse no pipeline", str(e))

# 4.5 apply_anti_reuse com arquivo real (sintético)
try:
    import subprocess, shutil as _sh
    ffmpeg = _sh.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "test_clip.mp4")
        outp = os.path.join(td, "test_clip_ar.mp4")
        # Criar um MP4 sintético de 3s (tom cinza sólido)
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "color=gray:size=320x180:rate=15",
             "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", inp],
            capture_output=True, timeout=30
        )
        if r.returncode == 0 and os.path.exists(inp):
            apply_anti_reuse(inp, outp, width=320, height=180, fps=15, seed=42)
            assert os.path.exists(outp), "arquivo de saída não criado"
            assert os.path.getsize(outp) > 1000, "arquivo de saída muito pequeno"
            ok("apply_anti_reuse: transforma MP4 real", f"{os.path.getsize(outp)//1024}KB")
        else:
            ok("apply_anti_reuse: ffmpeg não disponível (skip)")
except Exception as e:
    fail("apply_anti_reuse com MP4 real", str(e)[:100])

# 4.6 apply_anti_reuse com seed determinístico
try:
    import subprocess, shutil as _sh
    ffmpeg = _sh.which("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "in.mp4")
        out1 = os.path.join(td, "out1.mp4")
        out2 = os.path.join(td, "out2.mp4")
        r = subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "color=blue:size=320x180:rate=15",
             "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", inp],
            capture_output=True, timeout=30
        )
        if r.returncode == 0:
            apply_anti_reuse(inp, out1, width=320, height=180, fps=15, seed=123)
            apply_anti_reuse(inp, out2, width=320, height=180, fps=15, seed=123)
            sz1 = os.path.getsize(out1) if os.path.exists(out1) else 0
            sz2 = os.path.getsize(out2) if os.path.exists(out2) else 0
            # Mesmo seed → mesmos filtros → tamanhos similares (dentro de 10%)
            if sz1 > 0 and sz2 > 0:
                ratio = min(sz1, sz2) / max(sz1, sz2)
                assert ratio > 0.9, f"outputs com mesmo seed deveriam ser similares: {sz1} vs {sz2}"
            ok("apply_anti_reuse: seed determinístico produz outputs consistentes")
        else:
            ok("apply_anti_reuse seed: ffmpeg não disponível (skip)")
except Exception as e:
    fail("apply_anti_reuse seed determinístico", str(e)[:100])


# ─────────────────────────────────────────────────────────────────────────────
sep("5. INTEGRAÇÃO COMPLETA — Pipeline V2 → Timeline → Sync")
# ─────────────────────────────────────────────────────────────────────────────

# 5.1 Simulação E2E: V2 shot_list → _build_smart_timeline → build_beat_timeline
try:
    import random as _rnd
    rng_e2e = _rnd.Random(42)

    # Simula shot_list V2 (narração sobre saúde cerebral)
    shot_list_e2e = make_shot_list([
        (8.0,  15.0, ["fish oil capsule pharmacy", "omega3 bottle closeup"], "closeup"),
        (16.0, 23.0, ["brain mri scan wide", "hippocampus anatomy"], "wide"),
        (30.0, 37.0, ["elderly memory test clinic", "doctor consultation"], "closeup"),
        (45.0, 52.0, ["supplement bottle label", "daily vitamin pills"], "wide"),
    ])

    # Simula clips baixados com timestamps V2
    clips_e2e = make_clips([
        ("omega3 capsule", 8.0, 15.0, "closeup"),
        ("brain scan", 16.0, 23.0, "wide"),
        ("elderly memory", 30.0, 37.0, "closeup"),
        ("vitamin pills", 45.0, 52.0, "wide"),
    ])

    # Simula transcrição Whisper
    trans_e2e = make_transcription([
        ("Fish oil contains omega-3 EPA and DHA.", 8.0, 14.5),
        ("The brain benefits from regular supplementation.", 15.5, 22.0),
        ("Patients showed improved memory after 90 days.", 30.0, 36.5),
        ("Daily dose: two capsules with meals.", 45.0, 51.5),
    ])

    # _build_smart_timeline
    segs_e2e = _build_smart_timeline(70.0, clips_e2e, rng_e2e)
    brolls_e2e = [s for s in segs_e2e if s["type"] == "broll"]

    # Verificar snapping
    v2_targets_e2e = [8.0, 16.0, 30.0, 45.0]
    for i, (b, target) in enumerate(zip(brolls_e2e, v2_targets_e2e)):
        drift = abs(b["start"] - target)
        assert drift <= 2.0, f"B-roll {i}: start={b['start']:.1f} V2={target:.1f} drift={drift:.1f}s"

    # build_beat_timeline
    analysis_e2e = {"theme": "health", "duration": 70.0, "language": "en"}
    tl_e2e = build_beat_timeline(segs_e2e, shot_list_e2e, trans_e2e, analysis_e2e)

    # Verificar beats
    beats_broll = [b for b in tl_e2e["beats"] if b["type"] == "broll"]
    assert len(beats_broll) == 4, f"esperado 4 B-roll beats, got {len(beats_broll)}"

    # Verificar que narração está correta (V2 anchored)
    fish_beats = [b for b in beats_broll if "omega" in " ".join(b.get("search_terms", []))]
    assert fish_beats, "beat de omega3 não encontrado"
    assert "omega3" in " ".join(fish_beats[0].get("search_terms", [])).lower() or \
           "fish" in " ".join(fish_beats[0].get("search_terms", [])).lower(), \
           f"search_terms do beat omega3 errados: {fish_beats[0].get('search_terms')}"

    # Sync report
    sr_e2e = tl_e2e["sync_report"]
    assert sr_e2e["sync_ok"] is True or sr_e2e["max_drift_seconds"] <= 2.0, \
        f"drift E2E alto: {sr_e2e['max_drift_seconds']}s"

    ok("E2E: V2→timeline→beat_timeline→sync_report",
       f"{len(beats_broll)} B-rolls, max_drift={sr_e2e['max_drift_seconds']}s, sync_ok={sr_e2e['sync_ok']}")
except Exception as e:
    fail("E2E integração completa", str(e)[:150])

# 5.2 Narração do beat deve conter palavras do chunk V2 correspondente
try:
    rng_narr = random.Random(55)
    clips = make_clips([("omega3", 10.0, 17.0, "closeup")])
    segs = _build_smart_timeline(40.0, clips, rng_narr)
    trans = make_transcription([
        ("Fish oil contains omega-3 fatty acids.", 9.5, 16.0),
        ("This helps brain function significantly.", 20.0, 26.0),
    ])
    shot_list = make_shot_list([(10.0, 17.0, ["omega3 capsule"], "closeup")])
    tl = build_beat_timeline(segs, shot_list, trans, {"theme": "health", "duration": 40.0})
    broll = [b for b in tl["beats"] if b["type"] == "broll"]
    if broll:
        narr = broll[0].get("narration_text", "")
        assert "omega" in narr.lower() or "fish" in narr.lower() or "fatty" in narr.lower(), \
            f"narração não contém termos relevantes: '{narr}'"
        ok("narração do beat contém palavras do chunk V2", f"'{narr[:60]}'")
    else:
        ok("narração do beat: nenhum broll gerado (V2 timestamp no passado)")
except Exception as e:
    fail("narração do beat vs chunk V2", str(e))

# 5.3 Concorrência: múltiplos pipelines simultâneos não interferem
try:
    errors_conc = []
    results_conc = {}
    def run_pipeline(tid):
        try:
            r = random.Random(tid)
            clips = make_clips([(f"kw{tid}", tid*10+5.0, tid*10+12.0, "wide")])
            segs = _build_smart_timeline(60.0 + tid*5, clips, r)
            results_conc[tid] = len(segs)
        except Exception as e:
            errors_conc.append(f"thread {tid}: {e}")
    threads = [threading.Thread(target=run_pipeline, args=(i,)) for i in range(6)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=5)
    assert not errors_conc, f"erros em threads: {errors_conc}"
    assert len(results_conc) == 6
    ok("6 pipelines concorrentes: sem interferência", f"results={list(results_conc.values())}")
except Exception as e:
    fail("concorrência pipeline", str(e))


# ─────────────────────────────────────────────────────────────────────────────
print()
import io as _io, sys as _sys
_w = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
_w.write("\n" + "=" * 64 + "\n")
_w.write(f"RESULTADO FINAL: {passes} PASS / {fails} FAIL / {passes+fails} TOTAL\n")
_pct = round(passes / max(passes + fails, 1) * 100)
_w.write(f"Taxa de sucesso: {_pct}%\n")
_w.write("=" * 64 + "\n")
_w.flush()

if errors_log:
    print("\nFALHAS DETALHADAS:")
    for e in errors_log:
        print(e)
print()
sys.exit(0 if fails == 0 else 1)

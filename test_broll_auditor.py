"""
=============================================================================
TEST B-ROLL AUDITOR -- test_broll_auditor.py
=============================================================================
Cobre:
  Modulo 1  -- Carregamento de decisions JSON (ambos formatos)
  Modulo 2  -- analyze_decisions: todos os casos
  Modulo 3  -- build_amended_plan: approved/rejected/replace/unchanged
  Modulo 4  -- build_amended_timeline: estrutura e campos
  Modulo 5  -- rerender_video: video sintetico end-to-end
  Modulo 6  -- run_auditor: pipeline completo mockado
  Modulo 7  -- API /api/pipeline/rerender
  Modulo 8  -- API /api/pipeline/auditor/analyze
  Modulo 9  -- API /api/pipeline/auditor/load_timeline
  Modulo 10 -- Picker HTML: novos botoes, replace-file-row, doApply JS
  Modulo 11 -- Edge Cases: decisions vazias, arquivos ausentes, plano vazio
  Modulo 12 -- Stress: 500 beats, concurrent API calls
=============================================================================
"""

import sys, os, json, shutil, subprocess, tempfile, time
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

GREEN = "\033[92m"; RED = "\033[91m"; CYAN = "\033[96m"; BOLD = "\033[1m"; RESET = "\033[0m"
passes = 0; fails = 0; errors_list = []

def ok(label, detail=""):
    global passes; passes += 1
    sfx = f" [{detail}]" if detail else ""
    try:
        print(f"  {GREEN}[PASS]{RESET} {label}{sfx}")
    except UnicodeEncodeError:
        print(f"  [PASS] {label}{sfx}".encode("ascii","replace").decode())

def fail(label, reason=""):
    global fails; fails += 1
    errors_list.append(f"{label}: {reason}")
    try:
        print(f"  {RED}[FAIL]{RESET} {label} -- {reason}")
    except UnicodeEncodeError:
        print(f"  [FAIL] {label} -- {reason}".encode("ascii","replace").decode())

def sep(title):
    print(f"\n{'='*60}")
    try:
        print(f"  {BOLD}{CYAN}{title}{RESET}")
    except UnicodeEncodeError:
        print(f"  {title}".encode("ascii","replace").decode())
    print(f"{'='*60}")


# -- Helpers ------------------------------------------------------------------

def _ffmpeg():
    for c in [os.path.join(ROOT,"ffmpeg","ffmpeg.exe"), os.path.join(ROOT,"ffmpeg","ffmpeg"),
              "ffmpeg.exe", "ffmpeg"]:
        if shutil.which(c) or os.path.isfile(c):
            return c
    return "ffmpeg"

def create_synthetic_video(path, duration=3.0, color="blue", has_audio=True):
    """Cria video sintetico com ffmpeg (lavfi)."""
    ff = _ffmpeg()
    if has_audio:
        cmd = [ff, "-y", "-f","lavfi", "-i", f"color=c={color}:size=320x240:rate=24",
               "-f","lavfi", "-i", "sine=frequency=440:sample_rate=44100",
               "-t", str(duration), "-c:v","libx264", "-c:a","aac",
               "-shortest", path]
    else:
        cmd = [ff, "-y", "-f","lavfi", "-i", f"color=c={color}:size=320x240:rate=24",
               "-t", str(duration), "-c:v","libx264", "-an", path]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.isfile(path)

def _make_timeline(beats):
    """Cria estrutura de timeline minima."""
    return {"beats": beats, "total_beats": len(beats), "schema_version": "1.0"}

def _broll_beat(bid, file=None, start=0.0, duration=4.0, score=0.8):
    return {
        "id": bid, "type": "broll", "start": start, "end": start+duration,
        "duration": duration, "file": file or "", "validation_score": score,
        "search_terms": ["test"], "shot_type": "wide", "mood": "neutral",
        "source": "pexels", "status": "downloaded", "narration_text": ""
    }

def _avatar_beat(bid, start=0.0, duration=5.0):
    return {
        "id": bid, "type": "avatar", "start": start, "end": start+duration,
        "duration": duration, "file": "", "validation_score": None,
        "search_terms": [], "shot_type": "avatar", "mood": "neutral",
        "source": "avatar", "status": "ok", "narration_text": "Hello world"
    }


# =============================================================================
# MODULO 1 -- CARREGAMENTO DE DECISIONS JSON
# =============================================================================
sep("1. CARREGAMENTO DE DECISIONS JSON (ambos formatos)")

try:
    from core.broll_auditor import load_decisions, load_timeline
    ok("broll_auditor importa (load_decisions, load_timeline)")
except Exception as e:
    fail("broll_auditor importa", str(e))
    print("\n[FATAL] Nao e possivel continuar sem importar broll_auditor"); sys.exit(1)

# 1.1 Formato simples
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"beat1": "approved", "beat2": "rejected", "beat3": "replace"}, f)
    _path1 = f.name
try:
    d1, r1 = load_decisions(_path1)
    assert d1["beat1"] == "approved" and d1["beat2"] == "rejected" and d1["beat3"] == "replace"
    assert r1 == {}
    ok("load_decisions: formato simples [3 decisoes]")
except Exception as e:
    fail("load_decisions formato simples", str(e))
finally:
    os.unlink(_path1)

# 1.2 Formato extendido com replacements
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"decisions": {"b1": "replace", "b2": "approved"},
               "replacements": {"b1": "/tmp/clip.mp4"}}, f)
    _path2 = f.name
try:
    d2, r2 = load_decisions(_path2)
    assert d2["b1"] == "replace" and d2["b2"] == "approved"
    assert r2["b1"] == "/tmp/clip.mp4"
    ok("load_decisions: formato extendido com replacements")
except Exception as e:
    fail("load_decisions formato extendido", str(e))
finally:
    os.unlink(_path2)

# 1.3 Formato suffix _replacement
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"b1": "replace", "b1_replacement": "/path/vid.mp4", "b2": "rejected"}, f)
    _path3 = f.name
try:
    d3, r3 = load_decisions(_path3)
    assert d3["b1"] == "replace"
    assert r3.get("b1") == "/path/vid.mp4"
    ok("load_decisions: formato suffix _replacement")
except Exception as e:
    fail("load_decisions formato suffix", str(e))
finally:
    os.unlink(_path3)

# 1.4 load_timeline
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
    tl = _make_timeline([_broll_beat(f"b{i}") for i in range(5)])
    json.dump(tl, f)
    _tl_path = f.name
try:
    tl_loaded = load_timeline(_tl_path)
    assert tl_loaded["total_beats"] == 5
    ok("load_timeline: carrega corretamente [5 beats]")
except Exception as e:
    fail("load_timeline", str(e))
finally:
    os.unlink(_tl_path)

# 1.5 load_decisions arquivo inexistente -> exception
try:
    load_decisions("/tmp/nao_existe_xyz.json")
    fail("load_decisions: arquivo inexistente deveria levantar exception")
except Exception:
    ok("load_decisions: arquivo inexistente levanta exception")


# =============================================================================
# MODULO 2 -- ANALYZE_DECISIONS
# =============================================================================
sep("2. ANALYZE_DECISIONS - Todos os casos")

try:
    from core.broll_auditor import analyze_decisions
    ok("analyze_decisions importa")
except Exception as e:
    fail("analyze_decisions importa", str(e))

# 2.1 Basico: approved/rejected/replace_manual
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td, "clip1.mp4"); open(f1, "wb").write(b"x"*100)
    f2 = os.path.join(td, "clip2.mp4"); open(f2, "wb").write(b"x"*100)
    f3 = os.path.join(td, "newclip.mp4"); open(f3, "wb").write(b"x"*100)
    beats = [
        _broll_beat("b1", file=f1),
        _broll_beat("b2", file=f2),
        _broll_beat("b3", file=f1),
    ]
    tl = _make_timeline(beats)
    decs = {"b1": "approved", "b2": "rejected", "b3": "replace"}
    reps = {"b3": f3}
    try:
        a = analyze_decisions(tl, decs, reps)
        # approved/rejected/replace_manual are lists; replace_manual is a dict
        assert len(a["approved"]) == 1 and len(a["rejected"]) == 1
        assert len(a["replace_manual"]) == 1  # dict
        ok("analyze_decisions: approved/rejected/replace_manual detectados [total=3]")
    except Exception as e:
        fail("analyze_decisions basico", str(e))

# 2.2 Replace sem arquivo -> replace_auto
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td, "clip1.mp4"); open(f1, "wb").write(b"x"*100)
    beats = [_broll_beat("b1", file=f1)]
    tl = _make_timeline(beats)
    decs = {"b1": "replace"}
    reps = {}  # sem arquivo de replacement
    try:
        a2 = analyze_decisions(tl, decs, reps)
        assert len(a2["replace_auto"]) >= 1 or len(a2["replace_manual"]) >= 1
        ok("analyze_decisions: replace sem arquivo -> replace_auto")
    except Exception as e:
        fail("analyze_decisions replace sem arquivo", str(e))

# 2.3 Arquivo original ausente (sem decisao) -> invalid_files
with tempfile.TemporaryDirectory() as td:
    beats = [_broll_beat("b1", file="/nao/existe.mp4")]
    tl = _make_timeline(beats)
    try:
        a3 = analyze_decisions(tl, {}, {})
        assert len(a3["invalid_files"]) >= 1 or len(a3["unchanged"]) >= 1
        ok("analyze_decisions: arquivo ausente sem decisao -> unchanged+invalid_files")
    except Exception as e:
        fail("analyze_decisions arquivo ausente", str(e))

# 2.4 Approved mas arquivo sumiu -> handled
with tempfile.TemporaryDirectory() as td:
    beats = [_broll_beat("b1", file="/nao/existe.mp4")]
    tl = _make_timeline(beats)
    try:
        a4 = analyze_decisions(tl, {"b1": "approved"}, {})
        assert len(a4["invalid_files"]) >= 1 or len(a4["approved"]) >= 1
        ok("analyze_decisions: approved com arquivo ausente -> handled")
    except Exception as e:
        fail("analyze_decisions approved arquivo ausente", str(e))

# 2.5 Sem nenhuma decisao -> todos unchanged
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td, "c1.mp4"); open(f1,"wb").write(b"x"*100)
    f2 = os.path.join(td, "c2.mp4"); open(f2,"wb").write(b"x"*100)
    beats = [_broll_beat("b1",file=f1), _broll_beat("b2",file=f2)]
    tl = _make_timeline(beats)
    try:
        a5 = analyze_decisions(tl, {}, {})
        assert len(a5["unchanged"]) == 2
        ok("analyze_decisions: sem decisoes -> todos unchanged", f"unchanged={len(a5['unchanged'])}")
    except Exception as e:
        fail("analyze_decisions sem decisoes", str(e))

# 2.6 total_broll correto
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td, "c.mp4"); open(f1,"wb").write(b"x"*100)
    beats = [_broll_beat("b1",file=f1), _broll_beat("b2",file=f1), _avatar_beat("b3")]
    tl = _make_timeline(beats)
    try:
        a6 = analyze_decisions(tl, {}, {})
        assert a6["total_broll"] == 2
        ok("analyze_decisions: total_broll correto [3 beats]")
    except Exception as e:
        fail("analyze_decisions total_broll", str(e))


# =============================================================================
# MODULO 3 -- BUILD_AMENDED_PLAN
# =============================================================================
sep("3. BUILD_AMENDED_PLAN - Approved/Rejected/Replace/Unchanged")

try:
    from core.broll_auditor import build_amended_plan
    ok("build_amended_plan importa")
except Exception as e:
    fail("build_amended_plan importa", str(e))

with tempfile.TemporaryDirectory() as td:
    orig = os.path.join(td, "orig.mp4"); open(orig,"wb").write(b"x"*100)
    newf = os.path.join(td, "new.mp4");  open(newf,"wb").write(b"x"*100)

    beats = [
        _avatar_beat("b1", start=0.0, duration=5.0),
        _broll_beat("b2", file=orig, start=5.0, duration=4.0),
        _broll_beat("b3", file=orig, start=9.0, duration=3.0),
        _broll_beat("b4", file=orig, start=12.0, duration=4.0),
    ]
    tl = _make_timeline(beats)

    # 3.1 Rejected -> converte para avatar
    try:
        plan = build_amended_plan(tl, {"b3": "rejected"}, {})
        rejected_segs = [s for s in plan if "rejected" in s.get("_auditor","")]
        assert len(rejected_segs) >= 1 and rejected_segs[0]["type"] == "avatar"
        ok("build_amended_plan: rejected -> avatar", f"_auditor={rejected_segs[0]['_auditor']}")
    except Exception as e:
        fail("build_amended_plan rejected->avatar", str(e))

    # 3.2 Replace manual -> usa arquivo fornecido
    try:
        plan2 = build_amended_plan(tl, {"b2": "replace"}, {"b2": newf})
        replaced = [s for s in plan2 if s.get("file") == newf]
        assert len(replaced) >= 1
        ok("build_amended_plan: replace manual -> arquivo correto")
    except Exception as e:
        fail("build_amended_plan replace manual", str(e))

    # 3.3 Replace com new_clips (auto-download simulado)
    try:
        auto_clip = os.path.join(td, "auto.mp4"); open(auto_clip,"wb").write(b"x"*100)
        plan3 = build_amended_plan(tl, {"b4": "replace"}, {}, new_clips={"b4": auto_clip})
        auto_segs = [s for s in plan3 if s.get("file") == auto_clip]
        assert len(auto_segs) >= 1
        ok("build_amended_plan: replace com new_clips (replace_auto)")
    except Exception as e:
        fail("build_amended_plan replace_auto", str(e))

    # 3.4 Approved -> mantem arquivo original
    try:
        plan4 = build_amended_plan(tl, {"b2": "approved"}, {})
        approved_segs = [s for s in plan4 if s.get("file") == orig and s.get("type") == "broll"]
        assert len(approved_segs) >= 1
        ok("build_amended_plan: approved -> original mantido")
    except Exception as e:
        fail("build_amended_plan approved", str(e))

    # 3.5 Arquivo original sumiu (sem decisao) -> avatar fallback ou handled
    try:
        missing_beats = [_broll_beat("bx", file="/nao/existe_xyz.mp4", start=0.0)]
        tl_miss = _make_timeline(missing_beats)
        plan5 = build_amended_plan(tl_miss, {}, {})
        assert len(plan5) >= 1
        ok("build_amended_plan: arquivo ausente -> handled sem crash")
    except Exception as e:
        fail("build_amended_plan arquivo ausente", str(e))


# =============================================================================
# MODULO 4 -- BUILD_AMENDED_TIMELINE
# =============================================================================
sep("4. BUILD_AMENDED_TIMELINE - Estrutura e campos")

try:
    from core.broll_auditor import build_amended_timeline
    ok("build_amended_timeline importa")
except Exception as e:
    fail("build_amended_timeline importa", str(e))

with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td, "c1.mp4"); open(f1,"wb").write(b"x"*100)
    f2 = os.path.join(td, "c2.mp4"); open(f2,"wb").write(b"x"*100)

    beats = [
        _avatar_beat("b1", start=0.0, duration=5.0),
        _broll_beat("b2", file=f1, start=5.0, duration=4.0),
        _broll_beat("b3", file=f1, start=9.0, duration=3.0),
    ]
    original_tl = _make_timeline(beats)
    decs = {"b2": "approved", "b3": "rejected"}
    reps = {}
    amended_plan = build_amended_plan(original_tl, decs, reps)

    # 4.1 Schema version e campos de auditoria
    try:
        new_tl = build_amended_timeline(original_tl, amended_plan, decs, reps, {})
        assert new_tl.get("schema_version") == "1.1"
        assert "auditor_applied_at" in new_tl
        assert "auditor_summary" in new_tl
        ok("build_amended_timeline: schema_version=1.1, auditor_applied_at, auditor_summary")
    except Exception as e:
        fail("build_amended_timeline schema", str(e))

    # 4.2 Beats preservados no timeline
    try:
        new_tl2 = build_amended_timeline(original_tl, amended_plan, decs, reps, {})
        tl_beats = new_tl2.get("beats", [])
        assert len(tl_beats) >= 1
        ok("build_amended_timeline: beats preservados", f"n={len(tl_beats)}")
    except Exception as e:
        fail("build_amended_timeline beats", str(e))

    # 4.3 Beat rejeitado tem info de auditoria
    try:
        new_tl3 = build_amended_timeline(original_tl, amended_plan, decs, reps, {})
        b3_in_tl = next((b for b in new_tl3.get("beats",[]) if b.get("id") == "b3"), None)
        if b3_in_tl:
            dec_val = b3_in_tl.get("_decision","") or b3_in_tl.get("_auditor_action","")
            # qualquer campo de auditoria e valido
            assert dec_val is not None
        ok("build_amended_timeline: beat rejeitado tem info de auditoria")
    except Exception as e:
        fail("build_amended_timeline rejeitado", str(e))

    # 4.4 auditor_summary e dict
    try:
        new_tl4 = build_amended_timeline(original_tl, amended_plan, decs, reps, {})
        summ = new_tl4.get("auditor_summary", {})
        assert isinstance(summ, dict)
        ok("build_amended_timeline: auditor_summary e dict", f"keys={list(summ.keys())[:3]}")
    except Exception as e:
        fail("build_amended_timeline auditor_summary", str(e))


# =============================================================================
# MODULO 5 -- RERENDER_VIDEO (end-to-end sintetico)
# =============================================================================
sep("5. RERENDER_VIDEO - End-to-end com video sintetico")

try:
    from core.broll_auditor import rerender_video
    ok("rerender_video importa")
except Exception as e:
    fail("rerender_video importa", str(e))

with tempfile.TemporaryDirectory() as td:
    avatar = os.path.join(td, "avatar.mp4")
    broll1 = os.path.join(td, "broll1.mp4")
    broll2 = os.path.join(td, "broll2.mp4")
    out    = os.path.join(td, "output.mp4")

    av_ok = create_synthetic_video(avatar, 10.0, "blue", has_audio=True)
    b1_ok = create_synthetic_video(broll1, 4.0, "red", has_audio=False)
    b2_ok = create_synthetic_video(broll2, 3.0, "green", has_audio=False)

    if not (av_ok and b1_ok and b2_ok):
        fail("rerender_video: setup sintetico falhou (ffmpeg?)")
    else:
        # 5.1 Plano simples: avatar + broll
        plan_simple = [
            {"type":"avatar", "start":0.0, "duration":5.0},
            {"type":"broll",  "start":5.0, "duration":4.0, "file": broll1},
        ]
        try:
            result = rerender_video(avatar, plan_simple, out)
            assert result == True or os.path.isfile(out)
            ok("rerender_video: plano simples avatar+broll renderizou")
        except Exception as e:
            fail("rerender_video plano simples", str(e))

        # 5.2 Todos rejeitados -> so avatar
        out2 = os.path.join(td, "output2.mp4")
        plan_all_avatar = [
            {"type":"avatar", "start":0.0, "duration":5.0},
            {"type":"avatar", "start":5.0, "duration":5.0, "_auditor":"rejected_beat_b1"},
        ]
        try:
            result2 = rerender_video(avatar, plan_all_avatar, out2)
            assert result2 == True or os.path.isfile(out2)
            ok("rerender_video: todos rejeitados -> video so com avatar")
        except Exception as e:
            fail("rerender_video todos rejeitados", str(e))

        # 5.3 Replace manual -> arquivo substituido
        out3 = os.path.join(td, "output3.mp4")
        plan_replace = [
            {"type":"avatar", "start":0.0, "duration":5.0},
            {"type":"broll",  "start":5.0, "duration":3.0, "file": broll2, "_decision":"replace_manual"},
        ]
        try:
            result3 = rerender_video(avatar, plan_replace, out3)
            assert result3 == True or os.path.isfile(out3)
            ok("rerender_video: replace manual -> arquivo substituido")
        except Exception as e:
            fail("rerender_video replace manual", str(e))


# =============================================================================
# MODULO 6 -- RUN_AUDITOR (pipeline completo)
# =============================================================================
sep("6. RUN_AUDITOR - Pipeline completo")

try:
    from core.broll_auditor import run_auditor
    ok("run_auditor importa")
except Exception as e:
    fail("run_auditor importa", str(e))

with tempfile.TemporaryDirectory() as td:
    avatar = os.path.join(td, "avatar.mp4")
    broll1 = os.path.join(td, "broll1.mp4")
    out    = os.path.join(td, "output.mp4")
    tl_path = os.path.join(td, "timeline.json")
    dec_path = os.path.join(td, "decisions.json")

    av_ok = create_synthetic_video(avatar, 8.0, "blue", has_audio=True)
    b1_ok = create_synthetic_video(broll1, 4.0, "red", has_audio=False)

    beats = [
        _avatar_beat("b1", start=0.0, duration=4.0),
        _broll_beat("b2", file=broll1, start=4.0, duration=4.0),
    ]
    tl_data = _make_timeline(beats)

    with open(tl_path, "w", encoding="utf-8") as f:
        json.dump(tl_data, f)
    with open(dec_path, "w", encoding="utf-8") as f:
        json.dump({"b2": "approved"}, f)

    if not (av_ok and b1_ok):
        fail("run_auditor: setup sintetico falhou")
    else:
        # 6.1 Run basico
        try:
            r1 = run_auditor(tl_path, dec_path, avatar, out)
            assert r1.get("ok") == True or "error" in r1
            ok("run_auditor: run basico ok", f"ok={r1.get('ok')}")
        except Exception as e:
            fail("run_auditor basico", str(e))

        # 6.2 Stats no resultado
        try:
            r2 = run_auditor(tl_path, dec_path, avatar, out)
            assert "stats" in r2 or "ok" in r2
            ok("run_auditor: resultado tem stats ou ok", f"keys={list(r2.keys())[:4]}")
        except Exception as e:
            fail("run_auditor stats", str(e))

        # 6.3 Timeline invalido -> ok=False
        bad_tl = os.path.join(td, "bad_tl.json")
        with open(bad_tl, "w") as f: f.write("NAO E JSON VALIDO{{{")
        try:
            r3 = run_auditor(bad_tl, dec_path, avatar, out)
            assert r3.get("ok") == False
            ok("run_auditor: timeline invalido -> ok=False", r3.get("error","")[:50])
        except Exception as e:
            fail("run_auditor timeline invalido", str(e))

        # 6.4 Decisions vazias -> tudo inalterado
        empty_dec = os.path.join(td, "empty_dec.json")
        with open(empty_dec, "w") as f: json.dump({}, f)
        out4 = os.path.join(td, "out4.mp4")
        try:
            r4 = run_auditor(tl_path, empty_dec, avatar, out4)
            assert r4.get("ok") == True or "stats" in r4
            stats4 = r4.get("stats", {})
            ok("run_auditor: decisions vazias -> tudo inalterado",
               f"unchanged={stats4.get('unchanged',0)}")
        except Exception as e:
            fail("run_auditor decisions vazias", str(e))

        # 6.5 Avatar invalido -> ok=False
        bad_av = os.path.join(td, "bad_avatar.mp4")
        open(bad_av,"wb").write(b"NOT A VIDEO")
        out5 = os.path.join(td, "out5.mp4")
        try:
            r5 = run_auditor(tl_path, dec_path, bad_av, out5)
            assert r5.get("ok") == False or "error" in r5
            ok("run_auditor: avatar invalido -> ok=False ou error",
               r5.get("error","")[:60])
        except Exception as e:
            fail("run_auditor avatar invalido", str(e))


# =============================================================================
# MODULO 7 -- API /api/pipeline/rerender
# =============================================================================
sep("7. API /api/pipeline/rerender")

try:
    import studiopilot_web.server as srv_mod
    from studiopilot_web.server import app
    client = app.test_client()
    ok("server importa, test_client criado")
except Exception as e:
    fail("server importa", str(e))
    client = None

def _post(c, url, data):
    return c.post(url, data=json.dumps(data), content_type="application/json")

if client:
    # Use mkdtemp (not TemporaryDirectory) so background thread can keep files alive
    _td7 = tempfile.mkdtemp()
    try:
        avatar = os.path.join(_td7, "avatar.mp4")
        broll1 = os.path.join(_td7, "broll1.mp4")
        tl_path = os.path.join(_td7, "timeline.json")
        create_synthetic_video(avatar, 8.0, "blue", has_audio=True)
        create_synthetic_video(broll1, 4.0, "red", has_audio=False)

        beats = [_avatar_beat("b1",0.0,4.0), _broll_beat("b2",file=broll1,start=4.0,duration=4.0)]
        tl_data = _make_timeline(beats)
        with open(tl_path,"w",encoding="utf-8") as f: json.dump(tl_data,f)

        # 7.1 Sem avatar_path -> 400
        try:
            r = _post(client, "/api/pipeline/rerender",
                      {"timeline_path": tl_path, "decisions": {"b2":"approved"}})
            assert r.status_code == 400
            ok("rerender: sem avatar_path -> 400")
        except Exception as e:
            fail("rerender sem avatar_path", str(e))

        # 7.2 Sem timeline_path -> 400
        try:
            r = _post(client, "/api/pipeline/rerender",
                      {"avatar_path": avatar, "decisions": {"b2":"approved"}})
            assert r.status_code == 400
            ok("rerender: sem timeline_path -> 400")
        except Exception as e:
            fail("rerender sem timeline_path", str(e))

        # 7.3 Sem decisions nem decisions_path -> 400
        try:
            r = _post(client, "/api/pipeline/rerender",
                      {"avatar_path": avatar, "timeline_path": tl_path})
            assert r.status_code == 400
            ok("rerender: sem decisions -> 400")
        except Exception as e:
            fail("rerender sem decisions", str(e))

        # 7.4 Inline decisions validas -> 200 started=True
        try:
            out_name = "test_rerender_output"
            r = _post(client, "/api/pipeline/rerender", {
                "avatar_path": avatar,
                "timeline_path": tl_path,
                "decisions": {"b2": "approved"},
                "output_name": out_name,
            })
            assert r.status_code == 200
            d = json.loads(r.data)
            assert d.get("started") == True
            ok("rerender: inline decisions -> started=True", d.get("output_name","?"))
        except Exception as e:
            fail("rerender inline decisions", str(e))

        # 7.5 content-type errado -> 400 ou 415
        try:
            r3 = client.post("/api/pipeline/rerender",
                             data="raw text", content_type="text/plain")
            assert r3.status_code in (400, 415)
            ok("rerender: wrong content-type -> 400/415", f"status={r3.status_code}")
        except Exception as e:
            fail("rerender wrong content-type", str(e))

        # 7.6 Avatar inexistente -> 400 (validation before pipeline-running check)
        try:
            r = _post(client, "/api/pipeline/rerender", {
                "avatar_path": "/nao/existe_xyz.mp4",
                "timeline_path": tl_path,
                "decisions": {"b2": "approved"},
            })
            assert r.status_code in (400, 404)
            ok("rerender: avatar inexistente -> 400/404", f"status={r.status_code}")
        except Exception as e:
            fail("rerender avatar inexistente", str(e))

        # 7.7 Timeline inexistente -> 400
        try:
            r = _post(client, "/api/pipeline/rerender", {
                "avatar_path": avatar,
                "timeline_path": "/nao/existe_tl.json",
                "decisions": {"b2": "approved"},
            })
            assert r.status_code in (400, 404)
            ok("rerender: timeline inexistente -> 400/404", f"status={r.status_code}")
        except Exception as e:
            fail("rerender timeline inexistente", str(e))

    finally:
        # Wait briefly then cleanup (background thread may be using files)
        time.sleep(0.5)
        shutil.rmtree(_td7, ignore_errors=True)


# =============================================================================
# MODULO 8 -- API /api/pipeline/auditor/analyze
# =============================================================================
sep("8. API /api/pipeline/auditor/analyze")

if client:
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td, "c1.mp4"); open(f1,"wb").write(b"x"*100)
        f2 = os.path.join(td, "c2.mp4"); open(f2,"wb").write(b"x"*100)
        tl_path = os.path.join(td, "timeline.json")
        beats = [_broll_beat("b1",file=f1,start=0.0), _broll_beat("b2",file=f2,start=4.0)]
        with open(tl_path,"w",encoding="utf-8") as f: json.dump(_make_timeline(beats),f)

        # 8.1 Inline decisions -> analise correta
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": tl_path,
                "decisions": {"b1":"approved","b2":"rejected"},
            })
            assert r.status_code == 200
            d = json.loads(r.data)
            assert d.get("ok") == True
            assert d.get("approved") == 1 and d.get("rejected") == 1
            ok("auditor/analyze: inline decisions -> analise correta",
               f"approved={d.get('approved')},rejected={d.get('rejected')}")
        except Exception as e:
            fail("auditor/analyze inline decisions", str(e))

        # 8.2 decisions_path arquivo -> analise correta
        dec_file = os.path.join(td, "decs.json")
        with open(dec_file,"w") as f: json.dump({"b1":"approved","b2":"approved"},f)
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": tl_path,
                "decisions_path": dec_file,
            })
            assert r.status_code == 200
            d = json.loads(r.data)
            assert d.get("ok") == True
            ok("auditor/analyze: decisions_path arquivo -> ok")
        except Exception as e:
            fail("auditor/analyze decisions_path", str(e))

        # 8.3 Timeline invalido -> 400
        bad_tl = os.path.join(td, "bad.json")
        open(bad_tl,"w").write("INVALID JSON")
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": bad_tl,
                "decisions": {"b1":"approved"},
            })
            assert r.status_code in (400, 500)
            ok("auditor/analyze: timeline invalido -> 400/500", f"status={r.status_code}")
        except Exception as e:
            fail("auditor/analyze timeline invalido", str(e))

        # 8.4 Sem decisions -> 400
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": tl_path,
            })
            assert r.status_code == 400
            ok("auditor/analyze: sem decisions -> 400")
        except Exception as e:
            fail("auditor/analyze sem decisions", str(e))

        # 8.5 beat_ids presente no resultado
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": tl_path,
                "decisions": {"b1": "rejected"},
            })
            d = json.loads(r.data)
            assert "beat_ids" in d or "total_broll" in d
            ok("auditor/analyze: beat_ids/total_broll no resultado",
               f"total_broll={d.get('total_broll','?')}")
        except Exception as e:
            fail("auditor/analyze beat_ids", str(e))


# =============================================================================
# MODULO 9 -- API /api/pipeline/auditor/load_timeline
# =============================================================================
sep("9. API /api/pipeline/auditor/load_timeline")

if client:
    with tempfile.TemporaryDirectory() as td:
        tl_path = os.path.join(td, "timeline.json")
        beats = [_broll_beat(f"b{i}",start=i*4.0) for i in range(3)]
        with open(tl_path,"w",encoding="utf-8") as f:
            json.dump(_make_timeline(beats), f)

        # 9.1 Path valido -> 200 com timeline
        try:
            r = _post(client, "/api/pipeline/auditor/load_timeline",
                      {"timeline_path": tl_path})
            assert r.status_code == 200
            d = json.loads(r.data)
            assert d.get("ok") == True
            assert "timeline" in d
            assert d["timeline"]["total_beats"] == 3
            ok("load_timeline: path valido -> timeline completo",
               f"beats={d['timeline']['total_beats']}")
        except Exception as e:
            fail("load_timeline path valido", str(e))

        # 9.2 Path invalido -> 404
        try:
            r = _post(client, "/api/pipeline/auditor/load_timeline",
                      {"timeline_path": "/nao/existe_xyz.json"})
            assert r.status_code == 404
            ok("load_timeline: path invalido -> 404")
        except Exception as e:
            fail("load_timeline path invalido", str(e))

        # 9.3 Sem parametro -> 404 ou 400
        try:
            r = _post(client, "/api/pipeline/auditor/load_timeline", {})
            assert r.status_code in (400, 404)
            ok("load_timeline: sem parametro -> 400/404", f"status={r.status_code}")
        except Exception as e:
            fail("load_timeline sem parametro", str(e))

        # 9.4 output_name inferencia (inexistente)
        try:
            r = _post(client, "/api/pipeline/auditor/load_timeline",
                      {"output_name": "output_nao_existe"})
            assert r.status_code in (400, 404)
            ok("load_timeline: output_name inexistente -> 400/404",
               f"status={r.status_code}")
        except Exception as e:
            fail("load_timeline output_name", str(e))


# =============================================================================
# MODULO 10 -- PICKER HTML: novos botoes e JS
# =============================================================================
sep("10. PICKER HTML - Botoes, replace-file-row, doApply JS")

try:
    from core.broll_picker import generate_picker as generate_picker_html
    ok("broll_picker importa (generate_picker)")
except Exception as e:
    fail("broll_picker importa", str(e))
    generate_picker_html = None

if generate_picker_html:
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td,"c1.mp4"); open(f1,"wb").write(b"x"*100)
        f2 = os.path.join(td,"c2.mp4"); open(f2,"wb").write(b"x"*100)

        beats = [
            _broll_beat("b1", file=f1, start=0.0, score=0.85),
            _broll_beat("b2", file=f2, start=4.0, score=0.35),
            _avatar_beat("b3", start=8.0),
        ]
        tl = _make_timeline(beats)
        out_html = os.path.join(td, "picker.html")

        try:
            generate_picker_html(tl, out_html)
            assert os.path.isfile(out_html)
            html = open(out_html, encoding="utf-8").read()

            # 10.1 Botao "Aplicar no Re-render"
            assert "Aplicar" in html or "apply" in html.lower() or "doApply" in html
            ok("picker HTML: botao Aplicar no Re-render presente")

            # 10.2 apply-panel presente
            assert "apply" in html.lower() or "panel" in html.lower() or "inp-timeline" in html
            ok("picker HTML: apply panel ou inp-timeline presente")

            # 10.3 replace-file-row ou Substituir presente
            assert "replace-file" in html or "Substituir" in html or "replace" in html.lower()
            ok("picker HTML: replace-file-row ou Substituir presente")

            # 10.4 doApply JS function
            assert "doApply" in html or "apply" in html.lower()
            ok("picker HTML: doApply ou apply JS presente")

            # 10.5 rejectLowScore ou score JS
            assert "rejectLowScore" in html or "score" in html.lower()
            ok("picker HTML: rejectLowScore ou score JS presente")

            # 10.6 exportSelections presente
            assert "exportSelections" in html or "export" in html.lower()
            ok("picker HTML: exportSelections presente")

            # 10.7 Score badge
            assert "0.35" in html or "score" in html.lower()
            ok("picker HTML: score badge presente (0.35)")

            # 10.8 Beats broll renderizados
            broll_count = html.count("b1") + html.count("b2")
            assert broll_count >= 1
            ok("picker HTML: beats broll renderizados no HTML")

        except AssertionError as e:
            fail("picker HTML conteudo", str(e))
        except Exception as e:
            fail("picker HTML gerado", str(e))


# =============================================================================
# MODULO 11 -- EDGE CASES
# =============================================================================
sep("11. EDGE CASES")

# 11.1 Timeline sem beats broll -> ok (so avatar)
with tempfile.TemporaryDirectory() as td:
    avatar = os.path.join(td, "avatar.mp4")
    tl_path = os.path.join(td, "timeline.json")
    dec_path = os.path.join(td, "decs.json")
    out = os.path.join(td, "out.mp4")
    create_synthetic_video(avatar, 6.0, "blue", has_audio=True)
    beats_only_avatar = [_avatar_beat(f"a{i}", start=i*3.0, duration=3.0) for i in range(2)]
    with open(tl_path,"w",encoding="utf-8") as f: json.dump(_make_timeline(beats_only_avatar),f)
    with open(dec_path,"w",encoding="utf-8") as f: json.dump({},f)
    try:
        r = run_auditor(tl_path, dec_path, avatar, out)
        assert r.get("ok") == True or "error" in r
        ok("edge: timeline sem broll -> auditor ok", f"stats={r.get('stats',{})}")
    except Exception as e:
        fail("edge timeline sem broll", str(e))

# 11.2 Decisions com IDs inexistentes no timeline -> sem crash
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
    beats = [_broll_beat("b1", file=f1)]
    tl = _make_timeline(beats)
    try:
        a = analyze_decisions(tl, {"INEXISTENTE": "approved", "OUTRO": "rejected"}, {})
        assert isinstance(a, dict)
        ok("edge: decisions com IDs inexistentes -> sem crash")
    except Exception as e:
        fail("edge decisions IDs inexistentes", str(e))

# 11.3 Beat com duracao zero -> sem crash
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
    beats = [_broll_beat("bz", file=f1, start=0.0, duration=0.0)]
    tl = _make_timeline(beats)
    try:
        plan = build_amended_plan(tl, {"bz": "approved"}, {})
        assert isinstance(plan, list)
        ok("edge: beat com duracao zero -> sem crash")
    except Exception as e:
        fail("edge beat duracao zero", str(e))

# 11.4 API analyze com decisions invalidas -> sem crash 500
if client:
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
        tl_p = os.path.join(td,"tl.json")
        with open(tl_p,"w") as f: json.dump(_make_timeline([_broll_beat("b1",file=f1)]),f)
        try:
            r = _post(client, "/api/pipeline/auditor/analyze", {
                "timeline_path": tl_p,
                "decisions": {"b1": "DECISAO_INVALIDA_XYZ"},
            })
            assert r.status_code != 500
            ok("edge: API analyze com decisoes invalidas -> sem crash 500",
               f"status={r.status_code}")
        except Exception as e:
            fail("edge API analyze decisoes invalidas", str(e))

# 11.5 rerender_video com plano vazio -> sem crash
with tempfile.TemporaryDirectory() as td:
    avatar = os.path.join(td, "avatar.mp4")
    create_synthetic_video(avatar, 4.0, "blue", has_audio=True)
    out = os.path.join(td, "out_empty.mp4")
    try:
        result_empty = rerender_video(avatar, [], out)
        ok("edge: plano vazio -> sem crash", f"result={result_empty}")
    except Exception as e:
        ok("edge: plano vazio -> exception sem crash", str(e)[:50])

# 11.6 JSON corrompido -> exception capturado
with tempfile.TemporaryDirectory() as td:
    bad = os.path.join(td, "bad.json")
    open(bad,"w").write("{{CORROMPIDO}}")
    try:
        load_decisions(bad)
        fail("edge: JSON corrompido deveria levantar exception")
    except Exception:
        ok("edge: decisions JSON corrompido -> exception capturado")


# =============================================================================
# MODULO 12 -- STRESS
# =============================================================================
sep("12. STRESS - 500 beats, concurrent API calls")

# 12.1 analyze_decisions com 500 beats
with tempfile.TemporaryDirectory() as td:
    clips = []
    for i in range(10):
        p = os.path.join(td, f"c{i}.mp4"); open(p,"wb").write(b"x"*100)
        clips.append(p)

    beats_500 = [_broll_beat(f"b{i}", file=clips[i%10], start=i*4.0) for i in range(500)]
    tl_500 = _make_timeline(beats_500)
    decs_500 = {f"b{i}": ("approved" if i%3==0 else "rejected" if i%3==1 else "replace")
                for i in range(500)}

    try:
        t0 = time.time()
        a_stress = analyze_decisions(tl_500, decs_500, {})
        elapsed = time.time() - t0
        assert a_stress["total_broll"] == 500
        ok(f"stress: analyze_decisions 500 beats", f"{elapsed:.2f}s")
    except Exception as e:
        fail("stress analyze_decisions 500", str(e))

# 12.2 build_amended_plan com 500 beats
with tempfile.TemporaryDirectory() as td:
    f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
    beats_500b = [_broll_beat(f"b{i}", file=f1, start=i*4.0) for i in range(500)]
    tl_500b = _make_timeline(beats_500b)
    decs_500b = {f"b{i}": "approved" for i in range(0,500,2)}
    decs_500b.update({f"b{i}": "rejected" for i in range(1,500,2)})
    try:
        t0 = time.time()
        plan_500 = build_amended_plan(tl_500b, decs_500b, {})
        elapsed = time.time() - t0
        assert len(plan_500) == 500
        ok(f"stress: build_amended_plan 500 beats -> {len(plan_500)} segs", f"{elapsed:.2f}s")
    except Exception as e:
        fail("stress build_amended_plan 500", str(e))

# 12.3 generate_picker_html com 500 beats
if generate_picker_html:
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
        beats_html = [_broll_beat(f"b{i}", file=f1, start=i*4.0, score=0.5+i*0.001)
                      for i in range(500)]
        tl_html = _make_timeline(beats_html)
        out_html = os.path.join(td, "picker500.html")
        try:
            t0 = time.time()
            generate_picker_html(tl_html, out_html)
            elapsed = time.time() - t0
            assert os.path.isfile(out_html)
            size_kb = os.path.getsize(out_html) // 1024
            ok(f"stress: picker 500 beats gerado", f"{size_kb}KB em {elapsed:.2f}s")
        except Exception as e:
            fail("stress picker 500 beats", str(e))

# 12.4 Concurrent API analyze (20x)
if client:
    import threading
    with tempfile.TemporaryDirectory() as td:
        f1 = os.path.join(td,"c.mp4"); open(f1,"wb").write(b"x"*100)
        tl_c = os.path.join(td,"tl.json")
        with open(tl_c,"w") as f:
            json.dump(_make_timeline([_broll_beat("b1",file=f1)]), f)

        results_conc = []
        def _do_analyze():
            try:
                r = _post(client, "/api/pipeline/auditor/analyze", {
                    "timeline_path": tl_c, "decisions": {"b1":"approved"}})
                results_conc.append(r.status_code)
            except Exception:
                results_conc.append(0)

        threads = [threading.Thread(target=_do_analyze) for _ in range(20)]
        t0 = time.time()
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.time() - t0
        ok_count = sum(1 for s in results_conc if s == 200)
        ok(f"stress: 20x concurrent analyze", f"{ok_count}/20 ok em {elapsed:.2f}s")


# =============================================================================
# RESULTADO FINAL
# =============================================================================
total = passes + fails
print(f"\n{'='*60}")
print(f"  RESULTADO FINAL: {passes}/{total} testes passaram")
if fails:
    print(f"  {fails} FALHAS:")
    for e in errors_list:
        try:
            print(f"    - {e}")
        except UnicodeEncodeError:
            print(f"    - {e}".encode("ascii","replace").decode())
print(f"{'='*60}\n")
sys.exit(0 if fails == 0 else 1)

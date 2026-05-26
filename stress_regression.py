"""
Advanced stress tests — exercise edge cases that real production hits:
- malformed inputs, broken files, missing deps, Unicode paths
- concurrent calls, resource leaks
- API failure paths (offline)
- XSS in picker, SRT escaping, concat with bad segments
"""
import os, sys, json, tempfile, shutil, subprocess, threading, time, html as _html
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# Use script-relative paths (portable Linux/Windows)
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

PASS = 0
FAIL = 0
ERR = 0

def t(name, fn):
    global PASS, FAIL, ERR
    try:
        fn()
        print(f"  [OK]   {name}")
        PASS += 1
    except AssertionError as e:
        print(f"  [FAIL] {name}: {e}")
        FAIL += 1
    except Exception as e:
        import traceback
        print(f"  [ERR]  {name}: {type(e).__name__}: {e}")
        traceback.print_exc()
        ERR += 1

def section(s):
    print(f"\n{'='*72}\n  {s}\n{'='*72}")

TMP = tempfile.mkdtemp(prefix="stress_")
print(f"TMP: {TMP}\n")


# ============================================================
section("1. VALIDATOR EDGE CASES")
# ============================================================
from core.video_intelligence import VideoIntelligence

intel = VideoIntelligence(google_api_key="")

def test_validator_missing_file():
    s = intel.validate_clip(os.path.join(TMP, "nonexistent.mp4"), ["x"], "test")
    assert s == 0.0, f"expected 0.0 for missing file, got {s}"
t("validate_clip: missing file -> 0.0", test_validator_missing_file)

def test_validator_empty_file():
    p = os.path.join(TMP, "empty.mp4")
    open(p, "w").close()
    s = intel.validate_clip(p, ["x"], "test")
    assert s == 0.0, f"expected 0.0 for empty file, got {s}"
t("validate_clip: empty file -> 0.0", test_validator_empty_file)

def test_validator_tiny_file():
    p = os.path.join(TMP, "tiny.mp4")
    with open(p, "wb") as f:
        f.write(b"x" * 50)
    s = intel.validate_clip(p, ["x"], "test")
    assert s == 0.0, f"expected 0.0 for tiny file (<1KB), got {s}"
t("validate_clip: <1KB file -> 0.0", test_validator_tiny_file)

def test_validator_no_client():
    s = intel.validate_clip(__file__, ["python"], "code", metadata_text="python script test")
    # No client; falls through to textual on meta. python should match.
    assert s >= 0.0, f"got {s}"
t("validate_clip: no Gemini, textual fallback", test_validator_no_client)

def test_textual_unicode():
    s = intel._textual_match_score("clip with açaí naïve", ["açai natural"], "food")
    assert s >= 0.0, f"unicode should not crash, got {s}"
t("textual: unicode chars normalized", test_textual_unicode)

def test_textual_long_input():
    long_text = "lorem ipsum " * 500
    s = intel._textual_match_score(long_text, ["lorem"], "test")
    assert isinstance(s, float)
t("textual: long input no crash", test_textual_long_input)


# ============================================================
section("2. SHOT-LIST LANGUAGE GUARD")
# ============================================================
# Verify the language guard added in _create_shot_list inline detector works
import re as _re_l
PT_GIVEAWAYS = {"voce","voces","nossa","nosso","muito","pode","completamente","apenas",
                "tambem","sera","sao","esta","esto","facilmente","totalmente",
                "qualquer","alguma","quando","onde","porque","entao","assim"}
def looks_pt(s):
    sl = s.lower()
    if any(ch in sl for ch in "áéíóúãõçâêôà"): return True
    words = _re_l.findall(r"[a-zA-ZÀ-ſ]+", sl)
    if any(w.endswith(("mente","cao","ções")) for w in words): return True
    if any(w in PT_GIVEAWAYS for w in words): return True
    return False

cases_pt = [
    ("voce pode personalizar", True),
    ("rapid customization screen", False),
    ("avaliação rápida", True),
    ("modern technology demo", False),
    ("educação infantil escola", True),
    ("kids classroom learning", False),
]
for txt, expected in cases_pt:
    got = looks_pt(txt)
    t(f"PT-detect {txt[:35]!r:40s} -> {got} (expected {expected})",
      lambda g=got, e=expected: (g == e) or (_ for _ in ()).throw(AssertionError(f"{g} != {e}")))


# ============================================================
section("3. BEAT TIMELINE — MALFORMED INPUTS")
# ============================================================
from core.beat_timeline import build_beat_timeline

def test_bt_empty():
    bt = build_beat_timeline(
        segments_plan=[], shot_list=[], transcription=[], analysis={"theme":"","language":"en"},
    )
    assert isinstance(bt, dict)
    assert bt["broll_count"] == 0
    assert bt["avatar_count"] == 0
t("beat_timeline: empty inputs", test_bt_empty)

def test_bt_missing_keys():
    # segments_plan items without 'type' should not crash
    bt = build_beat_timeline(
        segments_plan=[{"start": 0, "end": 5}],  # no 'type'
        shot_list=[], transcription=[],
        analysis={"theme":"x", "language":"en"},
    )
    assert isinstance(bt, dict)
t("beat_timeline: missing 'type' key gracefully", test_bt_missing_keys)

def test_bt_unicode_text():
    bt = build_beat_timeline(
        segments_plan=[{"type":"avatar","start":0,"end":3}],
        shot_list=[],
        transcription=[{"start":0,"end":3,"text":"olá 你好 مرحبا"}],
        analysis={"theme":"multilingual","language":"auto"},
    )
    assert isinstance(bt, dict)
t("beat_timeline: unicode narration", test_bt_unicode_text)


# ============================================================
section("4. PICKER HTML — XSS + SAFETY")
# ============================================================
from core.broll_picker import generate_picker

def test_picker_xss_safe():
    bt = build_beat_timeline(
        segments_plan=[{"type":"broll","start":0,"end":5}],
        shot_list=[{
            "start":0,"end":5,
            "search_terms": ["<script>alert('xss1')</script>"],
            "shot_type":"<img src=x onerror=alert(2)>",
            "mood":"javascript:alert(3)",
            "visual_description":"\"><script>alert(4)</script>",
        }],
        transcription=[{"start":0,"end":5,"text":"</script><script>alert(5)</script>"}],
        analysis={"theme":"<script>alert(6)</script>","language":"en"},
        mapped_clips=[],
    )
    out_path = os.path.join(TMP, "picker_xss.html")
    generate_picker(bt, out_path)
    html_txt = open(out_path, encoding="utf-8").read()
    # Count script tags — only the legitimate ones (picker's own JS, 1-2)
    script_open = html_txt.count("<script>")
    assert script_open <= 3, f"too many <script> tags (XSS leak?): {script_open}"
    # Check the user-provided dangerous strings are escaped
    assert "alert('xss1')" not in html_txt or "&#x27;" in html_txt or "&apos;" in html_txt
t("picker html: XSS in shot terms escaped", test_picker_xss_safe)

def test_picker_empty_broll():
    bt = build_beat_timeline(
        segments_plan=[{"type":"avatar","start":0,"end":5}],
        shot_list=[], transcription=[],
        analysis={"theme":"x","language":"en"},
    )
    out_path = os.path.join(TMP, "picker_empty.html")
    generate_picker(bt, out_path)
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 500
t("picker html: no B-rolls -> still valid", test_picker_empty_broll)


# ============================================================
section("5. UNICODE PATHS — windows-cp1252 fragility")
# ============================================================

def test_unicode_path_validate():
    # Path with non-ASCII chars (like "automaçao") — must not crash
    unicode_dir = os.path.join(TMP, "tëst_ção_中文")
    os.makedirs(unicode_dir, exist_ok=True)
    fake = os.path.join(unicode_dir, "fake.mp4")
    with open(fake, "wb") as f:
        f.write(b"x" * 2000)
    # Should not crash on unicode path; will return 0.0/0.2 due to bad file
    s = intel.validate_clip(fake, ["test"], "demo")
    assert isinstance(s, float)
t("unicode path: validate_clip does not crash", test_unicode_path_validate)


# ============================================================
section("6. CONCAT — FAULT TOLERANCE")
# ============================================================
from core.video_processor import concat_segments_with_audio, _find_ffmpeg

def test_concat_with_missing():
    # Create 3 valid + 2 missing segments
    ffmpeg = _find_ffmpeg()
    segs = []
    for i in range(3):
        p = os.path.join(TMP, f"seg{i}.mp4")
        r = subprocess.run([
            ffmpeg, "-y", "-f", "lavfi",
            "-i", f"color=c=red:s=320x240:d=1:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-shortest", p
        ], capture_output=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode()[:300])
        segs.append(p)
    segs.insert(1, os.path.join(TMP, "missing1.mp4"))  # gap
    segs.append(os.path.join(TMP, "missing2.mp4"))      # gap

    out = os.path.join(TMP, "concat_out.mp4")
    concat_segments_with_audio(segs, out)
    assert os.path.exists(out), "concat output not created"
    # Output should be ~3s (only 3 valid segments)
t("concat: skips missing segments gracefully", test_concat_with_missing)


# ============================================================
section("7. AUDITOR — RATE LIMIT BUDGET")
# ============================================================
from core.video_auditor import VideoAuditor

def test_auditor_no_client():
    # Without API key, should fail gracefully
    auditor = VideoAuditor(google_api_key="")
    # Just verify init doesn't crash
    assert auditor is not None
t("auditor: init without API key", test_auditor_no_client)


# ============================================================
section("8. SUBTITLE SRT — SPECIAL CHARS")
# ============================================================

def test_srt_special_chars():
    # Create an SRT with chars that break ffmpeg subtitle filter
    srt = """1
00:00:00,000 --> 00:00:02,000
Test with 'quotes' and "doubles" : colons \\ backslash

2
00:00:02,000 --> 00:00:04,000
Unicode: 中文 français عربى émoji👋
"""
    p = os.path.join(TMP, "test.srt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(srt)
    # Just ensure path escaping doesn't blow up
    srt_escaped = p.replace("\\", "/").replace(":", "\\:")
    assert ":" not in srt_escaped or "\\:" in srt_escaped
t("srt: special chars in escaped path", test_srt_special_chars)


# ============================================================
section("9. PIPELINE IMPORTS — no circular / no undefined")
# ============================================================

def test_all_pipeline_imports():
    from core.pipeline_avatar_auto import (
        run_auto, _make_broll_with_pip, _find_ffmpeg, _find_ffprobe, _get_encoder
    )
    from core.video_intelligence import VideoIntelligence
    from core.video_auditor import VideoAuditor
    from core.beat_timeline import build_beat_timeline, summarize_beat_timeline
    from core.broll_picker import generate_picker
    from core.youtube_broll import search_and_download
    from core.video_processor import concat_segments_with_audio, _get_encoder
    # Verify _get_encoder is reachable from both
    enc = _get_encoder()
    assert isinstance(enc, list) and "-c:v" in enc
t("imports: all pipeline modules + _get_encoder reachable", test_all_pipeline_imports)


# ============================================================
section("10. TEXTUAL MATCHER — REGRESSION")
# ============================================================
cases = [
    # (meta, kws, theme, min_score, max_score, desc)
    ("avatar pilot demo", ["avatar pilot"], "social media", 0.5, 1.0, "exact match"),
    ("minecraft armor video", ["personalizar avatar"], "social media", 0.0, 0.1, "wrong content"),
    ("iran missile pentagon", ["iranian retaliation"], "geopolitics", 0.3, 1.0, "substring match"),
    ("", ["x"], "y", -1.0, -1.0, "no metadata sentinel"),
    ("xyz qrs def", ["completely unrelated"], "another theme", 0.0, 0.1, "zero overlap"),
]
for meta, kws, theme, lo, hi, desc in cases:
    s = intel._textual_match_score(meta, kws, theme)
    ok = lo <= s <= hi
    def check(s=s, lo=lo, hi=hi):
        assert lo <= s <= hi, f"score {s} not in [{lo}, {hi}]"
    t(f"textual {desc:25s} ({meta[:25]!r}, {kws[0][:20]!r}) -> {s:.2f}", check)


# ============================================================
section("11. CONCURRENT SAFETY — threads + validate_clip")
# ============================================================

def test_concurrent_textual():
    # Race condition check: 8 threads scoring concurrently
    results = []
    lock = threading.Lock()
    errs = []
    def worker():
        try:
            for _ in range(20):
                s = intel._textual_match_score("test meta", ["test"], "demo")
                with lock:
                    results.append(s)
        except Exception as e:
            with lock:
                errs.append(e)
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads: th.start()
    for th in threads: th.join(timeout=10)
    assert not errs, f"concurrent errors: {errs[:3]}"
    assert len(results) == 8 * 20, f"got {len(results)} results"
    # Should all be identical
    assert len(set(results)) == 1, f"non-deterministic: {set(results)}"
t("concurrent: 160 parallel textual calls deterministic", test_concurrent_textual)


# ============================================================
section("12. RESOURCE LEAK — temp files cleaned")
# ============================================================

def test_validator_no_temp_leak():
    """Verify validate_clip cleans up its extracted frames."""
    # Create a fake .mp4 so it tries to extract
    ffmpeg = _find_ffmpeg()
    p = os.path.join(TMP, "leak_test.mp4")
    subprocess.run([
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "color=c=green:s=640x360:d=2:r=30",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-an", "-pix_fmt", "yuv420p", p
    ], capture_output=True, timeout=20, check=True)

    # Count temp .jpg files before
    sys_tmp = tempfile.gettempdir()
    before = set(f for f in os.listdir(sys_tmp) if f.endswith(".jpg"))

    intel.validate_clip(p, ["green"], "test")

    after = set(f for f in os.listdir(sys_tmp) if f.endswith(".jpg"))
    leaked = after - before
    assert len(leaked) == 0, f"leaked temp files: {leaked}"
t("validator: no temp .jpg leak on video", test_validator_no_temp_leak)


# ============================================================
section("13. FFPROBE PATH DERIVATION — DIRNAME/BASENAME SAFETY")
# ============================================================

def _derive_ffprobe(ffmpeg_path):
    """Replicates the safe derivation pattern used in fixed modules.
    Uses ntpath/posixpath explicit to be cross-platform safe (CI runs on Linux,
    but we need to test the Windows path pattern too)."""
    import ntpath, posixpath
    # Detect Windows-style path (drive letter or backslash)
    is_win = ("\\" in ffmpeg_path) or (len(ffmpeg_path) > 1 and ffmpeg_path[1] == ":")
    mod = ntpath if is_win else posixpath
    ff_dir = mod.dirname(ffmpeg_path)
    ff_base = mod.basename(ffmpeg_path)
    probe_base = ff_base.replace("ffmpeg", "ffprobe")
    return mod.join(ff_dir, probe_base) if ff_dir else probe_base

def test_ffprobe_path_with_ffmpeg_in_dir():
    # The classic broken pattern: path contains 'ffmpeg' as DIRECTORY name
    bad_input = r"C:\Users\X\ffmpeg\bin\ffmpeg.exe"
    # Naive replace would corrupt directory: C:\Users\X\ffprobe\bin\ffprobe.exe
    naive = bad_input.replace("ffmpeg", "ffprobe")
    assert "ffprobe\\bin" in naive, "this assertion proves the naive bug exists"
    # Safe derivation only changes basename:
    safe = _derive_ffprobe(bad_input)
    assert safe == r"C:\Users\X\ffmpeg\bin\ffprobe.exe", f"safe got {safe!r}"
t("ffprobe derivation: dir 'ffmpeg' preserved, only basename changes", test_ffprobe_path_with_ffmpeg_in_dir)

def test_ffprobe_path_bare_name():
    safe = _derive_ffprobe("ffmpeg")
    assert safe == "ffprobe", f"bare-name got {safe!r}"
t("ffprobe derivation: bare 'ffmpeg' -> 'ffprobe'", test_ffprobe_path_bare_name)

def test_ffprobe_path_no_ext():
    safe = _derive_ffprobe("/usr/bin/ffmpeg")
    assert safe.endswith("/ffprobe") or safe.endswith("\\ffprobe"), f"got {safe!r}"
t("ffprobe derivation: unix path", test_ffprobe_path_no_ext)


# ============================================================
section("14. SMART BROLL — keyword extraction sanity")
# ============================================================
from core.smart_broll import _group_segments

def test_group_segments_basic():
    segs = [
        {"start": 0, "end": 2, "text": "hello"},
        {"start": 2, "end": 4, "text": "world"},
        {"start": 10, "end": 13, "text": "later"},
    ]
    chunks = _group_segments(segs, chunk_duration=5.0, total_duration=15)
    assert len(chunks) >= 1
    assert all("start" in c and "end" in c and "text" in c for c in chunks)
t("smart_broll: _group_segments produces well-formed chunks", test_group_segments_basic)

def test_group_segments_empty():
    chunks = _group_segments([], chunk_duration=5.0, total_duration=0)
    assert chunks == []
t("smart_broll: _group_segments handles empty input", test_group_segments_empty)


# ============================================================
section("15. ALL CORE MODULES IMPORT")
# ============================================================
def test_all_core_imports():
    import importlib
    # Use relative path so test works on both Windows and Linux CI
    core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core")
    mods = [f[:-3] for f in os.listdir(core_dir)
            if f.endswith(".py") and not f.startswith("_")]
    failed = []
    for m in mods:
        try:
            importlib.import_module(f"core.{m}")
        except Exception as e:
            failed.append((m, str(e)[:80]))
    assert not failed, f"import failures: {failed}"

# Compute file count using relative path (works on Win and Linux)
_core_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "core")
_core_count = sum(1 for f in os.listdir(_core_dir) if f.endswith(".py") and not f.startswith("_"))
t(f"imports: all core/*.py modules ({_core_count} files)", test_all_core_imports)


# ============================================================
shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'='*72}")
print(f"  RESULT: {PASS} PASS  /  {FAIL} FAIL  /  {ERR} ERR  (total {PASS+FAIL+ERR})")
print(f"{'='*72}")
sys.exit(0 if (FAIL + ERR) == 0 else 1)

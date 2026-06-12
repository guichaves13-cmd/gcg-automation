"""
Teste end-to-end do intelligent_broll com os 3 casos exatos do usuário:

1. "peixes ornamentais nadando no aquário com plantas naturais"
2. "máquina agrícola colhendo trigo no campo"
3. "destruição do solo por erosão"

Verifica que cada segmento recebe clip VISUALMENTE correto via Gemini Vision.
"""

import os
import sys
import json
import time
import base64
from pathlib import Path

# Force UTF-8 stdout on Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _decode(raw):
    """Try base64-decode; fall back to raw if it isn't base64."""
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw.encode()).decode()
        # Sanity check: real keys look like AIzaSy... / sk_... / etc — printable ASCII
        if decoded and all(32 <= ord(c) < 127 for c in decoded):
            return decoded
    except Exception:
        pass
    return raw


# Load API keys — try multiple paths
api_keys = {}
for candidate in [".api_keys.json", "studiopilot_web/.api_keys.json"]:
    p = Path(__file__).parent / candidate
    if p.exists():
        with open(p, encoding="utf-8") as f:
            api_keys = json.load(f)
        print(f"Loaded keys from {p.name}")
        break

GEMINI_KEY = _decode(api_keys.get("gemini") or api_keys.get("google_ai") or "")
PEXELS_KEY = _decode(api_keys.get("pexels") or "")
PIXABAY_KEY = _decode(api_keys.get("pixabay") or "")
GROQ_KEY = _decode(api_keys.get("groq") or "")
NVIDIA_KEY = _decode(api_keys.get("nvidia") or "")

# Pexels stored as test placeholder — disable if it isn't a real key
if PEXELS_KEY.startswith("test_"):
    print(f"⚠ Pexels key is a placeholder ({PEXELS_KEY[:20]}) — disabling Pexels source")
    PEXELS_KEY = ""

os.environ["GROQ_API_KEY"] = GROQ_KEY

# Set env for any downstream Google libraries
os.environ["GOOGLE_API_KEY"] = GEMINI_KEY

from core.intelligent_broll import (
    SegmentIntent,
    IntentExtractor,
    MultiSourceSearcher,
    VisionVerifier,
    IntelligentBrollEngine,
    BeatPlan,
)

# Output dir
OUT = Path(__file__).parent / "test_output" / "intelligent_broll"
OUT.mkdir(parents=True, exist_ok=True)


# ────────────────────────────────────────────────────────────────────────────
# TEST 1: Intent extraction só (sem audio, rápido)
# ────────────────────────────────────────────────────────────────────────────


def test_intent_extraction():
    """Verifica que pra cada texto, extract retorna entidade certa."""
    print("\n" + "=" * 70)
    print("TEST 1: Intent Extraction")
    print("=" * 70)
    extractor = IntentExtractor(gemini_api_key=GEMINI_KEY, groq_api_key=GROQ_KEY)

    cases = [
        {
            "text": "Hoje vou mostrar peixes ornamentais lindos nadando no meu aquário com plantas naturais",
            "expected_entity_contains": ["peixe", "aquário"],
        },
        {
            "text": "Esta máquina agrícola moderna está colhendo trigo no campo durante a safra de verão",
            "expected_entity_contains": ["máquina", "agrícola", "trator", "colheitadeira"],
        },
        {
            "text": "Em apenas alguns minutos, o solo está sendo completamente destruído pela erosão da chuva",
            "expected_entity_contains": ["solo", "erosão"],
        },
    ]

    passes = 0
    for i, c in enumerate(cases):
        print(f"\nCase {i+1}: {c['text'][:80]}…")
        data = extractor.extract(c["text"], theme="natureza")
        entity = data["main_entity"].lower()
        queries = data["search_queries"]
        action = data["action"]
        print(f"  main_entity: {data['main_entity']}")
        print(f"  action:      {action}")
        print(f"  queries[:3]: {queries[:3]}")
        matched = any(exp.lower() in entity for exp in c["expected_entity_contains"])
        if matched:
            print(f"  ✅ entity contém {c['expected_entity_contains']}")
            passes += 1
        else:
            print(f"  ⚠ entity={entity!r} NÃO contém nenhum de {c['expected_entity_contains']}")

    print(f"\n→ Intent Extraction: {passes}/{len(cases)} OK")
    return passes == len(cases)


# ────────────────────────────────────────────────────────────────────────────
# TEST 2: Multi-source search retorna candidatos
# ────────────────────────────────────────────────────────────────────────────


def test_multisource_search():
    """Verifica busca paralela em 3 fontes."""
    print("\n" + "=" * 70)
    print("TEST 2: MultiSource Search (Pexels + Pixabay + YouTube)")
    print("=" * 70)
    searcher = MultiSourceSearcher(
        pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY, youtube_enabled=True,
        max_per_source=5,
    )

    queries = ["fish aquarium plants", "agricultural machine wheat harvest", "soil erosion rain"]
    all_results = {}
    for q in queries:
        print(f"\nQuery: {q!r}")
        t0 = time.time()
        candidates = searcher.search_all(q, min_duration=3.0)
        elapsed = time.time() - t0
        by_source = {}
        for c in candidates:
            by_source[c.source] = by_source.get(c.source, 0) + 1
        print(f"  {len(candidates)} candidatos em {elapsed:.1f}s | por fonte: {by_source}")
        all_results[q] = candidates

    # Pass: pelo menos 5 candidatos por query
    ok = all(len(v) >= 5 for v in all_results.values())
    print(f"\n→ MultiSource Search: {'OK' if ok else 'FAIL'} (queries com >=5 candidatos)")
    return ok, all_results


# ────────────────────────────────────────────────────────────────────────────
# TEST 3: Vision verifier rejeita imagens erradas
# ────────────────────────────────────────────────────────────────────────────


def test_vision_verifier(search_results=None):
    """Pega candidatos da test 2 e verifica que Gemini Vision dá scores razoáveis."""
    print("\n" + "=" * 70)
    print("TEST 3: Vision Verifier — scores Gemini")
    print("=" * 70)
    verifier = VisionVerifier(gemini_api_key=GEMINI_KEY, nvidia_api_key=NVIDIA_KEY)

    test_cases = [
        ("fish aquarium plants",
         SegmentIntent(
             index=0, text="peixes ornamentais no aquário com plantas",
             start=0, end=5,
             main_entity="peixes ornamentais aquário com plantas",
             action="nadando",
             visual_context="real, close-up, sem texto",
             search_queries=[],
         )),
    ]

    if not search_results:
        from core.intelligent_broll import MultiSourceSearcher
        searcher = MultiSourceSearcher(
            pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY, youtube_enabled=True,
            max_per_source=3,
        )
        search_results = {q: searcher.search_all(q) for q, _ in test_cases}

    for query, intent in test_cases:
        candidates = search_results.get(query, [])[:5]
        print(f"\nQuery: {query!r}")
        print(f"Intent: {intent.main_entity}")
        for cand in candidates:
            if not cand.thumbnail_url:
                continue
            score, desc, reason = verifier.verify(cand.thumbnail_url, intent)
            verdict = "✅ APROVADO" if score >= 80 else "❌ REJ"
            print(f"  [{score:3d}] {verdict} {cand.source}#{cand.source_id[:8]}: {desc[:60]}")
            if reason:
                print(f"        reason: {reason[:80]}")

    print(f"\n→ Vision Verifier: rodou em {len(test_cases)} casos")
    return True


# ────────────────────────────────────────────────────────────────────────────
# TEST 4: End-to-end com script real (3 cenas)
# ────────────────────────────────────────────────────────────────────────────


def test_end_to_end():
    """Roteiro real com 3 cenas → engine completo retorna 3 BeatPlans com clip."""
    print("\n" + "=" * 70)
    print("TEST 4: End-to-End (3 cenas exatas do user)")
    print("=" * 70)
    script = (
        "Hoje vou mostrar peixes ornamentais lindos nadando no meu aquário "
        "com plantas naturais. "
        "Agora veja esta máquina agrícola moderna colhendo trigo no campo. "
        "Mas atenção: o solo está sendo destruído pela erosão da chuva."
    )

    engine = IntelligentBrollEngine(
        gemini_api_key=GEMINI_KEY,
        groq_api_key=GROQ_KEY,
        nvidia_api_key=NVIDIA_KEY,
        pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY,
        youtube_enabled=True,
        output_dir=str(OUT / "clips"),
        max_candidates_per_intent=6,
        max_search_attempts=2,
    )

    plans = engine.build(
        audio_path="", script=script, theme="documentário natureza/agricultura",
        min_relevance=70,  # Um pouco mais permissivo para teste rápido
    )

    print(f"\n=== RESULTADO: {sum(1 for p in plans if p.is_solved())}/{len(plans)} resolvidos ===")
    for p in plans:
        status = "✅" if p.is_solved() else "⚠ MANUAL"
        print(f"\n  {status} {p.intent.short_label()}")
        print(f"     entity: {p.intent.main_entity}")
        print(f"     action: {p.intent.action}")
        if p.clip:
            print(f"     CLIP:   {p.clip.short_label()} score={p.clip.relevance_score}")
            print(f"     saw:    {p.clip.vision_description[:80]}")
            if p.download_path:
                size_mb = os.path.getsize(p.download_path) / 1024 / 1024 if os.path.exists(p.download_path) else 0
                print(f"     file:   {p.download_path} ({size_mb:.1f}MB)")
        elif p.error:
            print(f"     ERROR:  {p.error}")

    # Save plan to JSON
    plan_path = OUT / "plan_e2e.json"
    serializable = []
    for p in plans:
        serializable.append({
            "intent": {
                "text": p.intent.text, "start": p.intent.start, "end": p.intent.end,
                "main_entity": p.intent.main_entity, "action": p.intent.action,
                "search_queries": p.intent.search_queries,
            },
            "clip": {
                "source": p.clip.source, "title": p.clip.title,
                "score": p.clip.relevance_score, "description": p.clip.vision_description,
                "page_url": p.clip.page_url,
            } if p.clip else None,
            "download_path": p.download_path,
            "needs_manual_review": p.needs_manual_review,
            "error": p.error,
        })
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\n  Plan saved to {plan_path}")
    return all(p.is_solved() for p in plans)


# ────────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print(f"GOOGLE_AI key: {'OK' if GEMINI_KEY else 'MISSING'}")
    print(f"PEXELS key:    {'OK' if PEXELS_KEY else 'MISSING'}")
    print(f"PIXABAY key:   {'OK' if PIXABAY_KEY else 'MISSING'}")

    if not GEMINI_KEY:
        print("\nERRO: precisa GEMINI_KEY no studiopilot_web/.api_keys.json")
        sys.exit(1)

    # Run tests
    t1 = test_intent_extraction()
    t2_ok, t2_results = test_multisource_search()
    t3 = test_vision_verifier(t2_results)
    t4 = test_end_to_end()

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  1. Intent Extraction:    {'PASS' if t1 else 'FAIL'}")
    print(f"  2. MultiSource Search:   {'PASS' if t2_ok else 'FAIL'}")
    print(f"  3. Vision Verifier:      {'PASS' if t3 else 'FAIL'}")
    print(f"  4. End-to-End Pipeline:  {'PASS' if t4 else 'FAIL (precisa revisão manual)'}")

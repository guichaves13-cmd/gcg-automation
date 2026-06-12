"""
TESTE MULTI-NICHO AVANÇADO — valida que intelligent_broll funciona em qualquer nicho.

8 nichos × 3 cenas cada = 24 segmentos.

Critério de aprovação:
- Para cada segmento, Vision Verifier precisa dar score ≥ 70 confirmando que o clip
  selecionado MOSTRA exatamente o que está sendo dito.
- Meta: ≥ 80% de aprovação (19/24 segmentos).
"""

import os, sys, json, base64, time
from pathlib import Path
from collections import defaultdict

if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _decode(raw):
    if not raw: return ""
    try:
        d = base64.b64decode(raw.encode()).decode()
        if d and all(32 <= ord(c) < 127 for c in d):
            return d
    except Exception:
        pass
    return raw


api_keys = json.load(open(".api_keys.json", encoding="utf-8"))
GEMINI_KEY = _decode(api_keys.get("gemini") or api_keys.get("google_ai") or "")
GROQ_KEY = _decode(api_keys.get("groq") or "")
NVIDIA_KEY = _decode(api_keys.get("nvidia") or "")
PEXELS_KEY = _decode(api_keys.get("pexels") or "")
PIXABAY_KEY = _decode(api_keys.get("pixabay") or "")
if PEXELS_KEY.startswith("test_"): PEXELS_KEY = ""

os.environ.update({
    "GOOGLE_API_KEY": GEMINI_KEY, "GROQ_API_KEY": GROQ_KEY,
    "NVIDIA_API_KEY": NVIDIA_KEY,
})

from core.intelligent_broll import IntelligentBrollEngine

OUT = Path("test_output/multiniche")
OUT.mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════════════════════
# 8 NICHOS — roteiros REAIS com 3 cenas concretas cada (24 segmentos total)
# ════════════════════════════════════════════════════════════════════════════

NICHES = [
    {
        "name": "Aquarismo",
        "theme": "aquário e peixes ornamentais",
        "script": (
            "Estes peixes betta lutadores nadam em águas calmas com plantas naturais. "
            "Agora veja como funciona um filtro biológico canister limpando a água do aquário. "
            "E olha o crescimento das plantas anubias fixadas em pedras de lava."
        ),
        "expect_keywords": [
            ["betta", "peixe", "lutador", "aquário"],
            ["filtro", "canister", "água", "aquário"],
            ["planta", "anubias", "pedra", "aquário"],
        ],
    },
    {
        "name": "Agronegócio",
        "theme": "agricultura industrial",
        "script": (
            "Esta colheitadeira John Deere está colhendo soja em uma fazenda no Mato Grosso. "
            "Veja agora a aplicação de defensivos agrícolas com drone autônomo no campo. "
            "E o gado nelore pastando em terras de pecuária extensiva."
        ),
        "expect_keywords": [
            ["colheitadeira", "soja", "máquina", "agrícola"],
            ["drone", "defensivo", "agrícola", "aplicação"],
            ["gado", "nelore", "pasto", "boi", "vaca"],
        ],
    },
    {
        "name": "Medicina",
        "theme": "saúde e procedimentos médicos",
        "script": (
            "Cirurgiões realizam uma operação cardíaca usando equipamento de ponta no hospital. "
            "Em seguida, vemos enfermeiros cuidando de pacientes na UTI. "
            "E uma ressonância magnética sendo realizada para diagnóstico."
        ),
        "expect_keywords": [
            ["cirurgia", "médico", "operação", "cirurgião"],
            ["enfermeir", "paciente", "uti", "hospital"],
            ["ressonância", "exame", "máquina", "diagnóstico"],
        ],
    },
    {
        "name": "Tecnologia",
        "theme": "data center e infraestrutura digital",
        "script": (
            "Os servidores do data center processam milhões de requisições por segundo. "
            "Veja agora robôs industriais montando smartphones em uma fábrica moderna. "
            "E engenheiros programando inteligência artificial em telas com código."
        ),
        "expect_keywords": [
            ["servidor", "data center", "rack", "computador"],
            ["robô", "smartphone", "fábrica", "linha de montagem"],
            ["programador", "código", "tela", "computador"],
        ],
    },
    {
        "name": "Culinária",
        "theme": "gastronomia profissional",
        "script": (
            "O chef finaliza um prato de massa italiana caprese com manjericão fresco. "
            "Agora ele corta tomates orgânicos com técnica de faca profissional. "
            "E coloca a massa para cozinhar em água fervente com sal grosso."
        ),
        "expect_keywords": [
            ["massa", "italiana", "prato", "manjericão", "comida"],
            ["tomate", "faca", "corte", "cozinha"],
            ["panela", "água fervente", "cozinhar", "massa"],
        ],
    },
    {
        "name": "Esportes",
        "theme": "futebol profissional",
        "script": (
            "O atacante chuta a bola de futebol e marca um gol espetacular no canto. "
            "Veja a torcida lotada no estádio comemorando com bandeiras. "
            "E os jogadores correndo no campo durante o treino tático."
        ),
        "expect_keywords": [
            ["futebol", "gol", "bola", "chute", "atacante"],
            ["torcida", "estádio", "bandeira", "comemoração"],
            ["jogador", "treino", "campo", "futebol"],
        ],
    },
    {
        "name": "Natureza/Animais",
        "theme": "fauna selvagem africana",
        "script": (
            "Um leão da savana africana caminha pela grama alta ao pôr do sol. "
            "Veja agora uma manada de elefantes atravessando o rio com filhotes. "
            "E uma girafa comendo folhas no topo de uma árvore acácia."
        ),
        "expect_keywords": [
            ["leão", "savana", "áfrica"],
            ["elefante", "manada", "rio"],
            ["girafa", "folha", "árvore"],
        ],
    },
    {
        "name": "Construção",
        "theme": "obras civis e construção",
        "script": (
            "Um operário de capacete coloca tijolos em uma parede de construção. "
            "Veja agora a betoneira despejando concreto fresco na fundação. "
            "E um guindaste içando vigas de aço no alto do edifício."
        ),
        "expect_keywords": [
            ["pedreiro", "operário", "tijolo", "parede", "construção"],
            ["betoneira", "concreto", "obra", "construção"],
            ["guindaste", "viga", "aço", "edifício", "construção"],
        ],
    },
]


# ════════════════════════════════════════════════════════════════════════════
# RUN
# ════════════════════════════════════════════════════════════════════════════


def main():
    print("=" * 72)
    print(f"TESTE MULTI-NICHO AVANÇADO — {len(NICHES)} nichos × 3 cenas = {len(NICHES)*3} segmentos")
    print("=" * 72)
    print(f"Pexels:  {'OK' if PEXELS_KEY else 'OFF'}")
    print(f"Pixabay: {'OK' if PIXABAY_KEY else 'OFF'}")
    print(f"Groq:    {'OK' if GROQ_KEY else 'OFF'}")
    print(f"NVIDIA:  {'OK' if NVIDIA_KEY else 'OFF'}")
    print(f"Gemini:  {'OK (mas free tier may be exhausted)' if GEMINI_KEY else 'OFF'}")

    engine = IntelligentBrollEngine(
        gemini_api_key=GEMINI_KEY, groq_api_key=GROQ_KEY, nvidia_api_key=NVIDIA_KEY,
        pexels_key=PEXELS_KEY, pixabay_key=PIXABAY_KEY,
        youtube_enabled=True,
        output_dir=str(OUT / "clips"),
        max_candidates_per_intent=8,
        max_search_attempts=2,
    )

    results_by_niche = {}
    total_segments = 0
    total_solved = 0
    total_score_sum = 0
    total_with_score = 0
    score_buckets = defaultdict(int)  # 0-39, 40-69, 70-79, 80-89, 90-100
    sources_used = defaultdict(int)
    t_start = time.time()

    for niche in NICHES:
        print("\n" + "─" * 72)
        print(f"NICHO: {niche['name']}  ({niche['theme']})")
        print(f"Roteiro: {niche['script'][:100]}...")
        print("─" * 72)

        t0 = time.time()
        plans = engine.build(
            audio_path="", script=niche["script"], theme=niche["theme"],
            min_relevance=70,
        )
        elapsed = time.time() - t0

        niche_results = []
        for i, p in enumerate(plans):
            total_segments += 1
            expected = niche["expect_keywords"][i] if i < len(niche["expect_keywords"]) else []

            ok = p.is_solved()
            score = p.clip.relevance_score if p.clip else 0
            saw = p.clip.vision_description if p.clip else ""
            source = p.clip.source if p.clip else "none"

            # Bucket score
            if score >= 90: score_buckets["90-100"] += 1
            elif score >= 80: score_buckets["80-89"] += 1
            elif score >= 70: score_buckets["70-79"] += 1
            elif score >= 40: score_buckets["40-69"] += 1
            else: score_buckets["0-39"] += 1

            if score > 0:
                total_score_sum += score
                total_with_score += 1
            if ok:
                total_solved += 1
            sources_used[source] += 1

            # Check if vision description mentions expected keywords
            saw_lower = saw.lower()
            entity_match = any(kw.lower() in saw_lower for kw in expected) if expected else False

            status = "✅" if (ok and (entity_match or score >= 80)) else ("⚠" if score >= 50 else "❌")
            print(f"\n  {status} Cena {i+1} [{p.intent.start:.1f}–{p.intent.end:.1f}s] "
                  f"score={score} src={source}")
            print(f"     Esperado:   {expected}")
            print(f"     Entidade:   {p.intent.main_entity}")
            print(f"     Vision viu: {saw[:90]}")
            if p.clip:
                print(f"     URL:        {p.clip.page_url}")
            if p.error:
                print(f"     ERROR:      {p.error}")

            niche_results.append({
                "scene": i + 1,
                "expected": expected,
                "entity": p.intent.main_entity,
                "score": score,
                "source": source,
                "vision_saw": saw,
                "page_url": p.clip.page_url if p.clip else "",
                "matched": ok and (entity_match or score >= 80),
                "entity_match": entity_match,
            })

        print(f"\n  ⏱  {elapsed:.1f}s para 3 cenas neste nicho")
        results_by_niche[niche["name"]] = niche_results

    elapsed_total = time.time() - t_start

    # ─── RELATÓRIO FINAL ────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("RELATÓRIO FINAL — multi-nicho")
    print("=" * 72)

    matched_total = sum(1 for niche in results_by_niche.values()
                        for r in niche if r["matched"])
    avg_score = total_score_sum / max(1, total_with_score)

    print(f"\nSegmentos totais:        {total_segments}")
    print(f"Resolvidos (clip ok):    {total_solved}/{total_segments} "
          f"= {total_solved*100/total_segments:.0f}%")
    print(f"Matched (clip correto):  {matched_total}/{total_segments} "
          f"= {matched_total*100/total_segments:.0f}%")
    print(f"Score médio:             {avg_score:.1f}")
    print(f"Tempo total:             {elapsed_total:.0f}s "
          f"({elapsed_total/max(1,total_segments):.1f}s/segmento)")

    print(f"\nDistribuição de scores:")
    for bucket in ["90-100", "80-89", "70-79", "40-69", "0-39"]:
        n = score_buckets[bucket]
        bar = "█" * n
        print(f"  {bucket:6}: {n:3d} {bar}")

    print(f"\nFontes selecionadas:")
    for src, n in sorted(sources_used.items(), key=lambda x: -x[1]):
        print(f"  {src:10}: {n}")

    print(f"\nPor nicho:")
    for niche_name, results in results_by_niche.items():
        matched = sum(1 for r in results if r["matched"])
        avg = sum(r["score"] for r in results) / len(results)
        status = "✅" if matched == 3 else ("⚠" if matched >= 2 else "❌")
        print(f"  {status} {niche_name:18}: {matched}/3 matched, score médio {avg:.0f}")

    # Save full report
    report_path = OUT / "report_multiniche.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_segments": total_segments,
            "matched": matched_total,
            "match_rate": matched_total / total_segments,
            "avg_score": avg_score,
            "elapsed_seconds": elapsed_total,
            "score_buckets": dict(score_buckets),
            "sources_used": dict(sources_used),
            "by_niche": results_by_niche,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n  Relatório completo: {report_path}")

    # Verdict
    pass_rate = matched_total / total_segments
    print("\n" + "═" * 72)
    if pass_rate >= 0.80:
        print(f"  ✅ APROVADO — {pass_rate*100:.0f}% match (meta ≥ 80%)")
    elif pass_rate >= 0.60:
        print(f"  ⚠ PARCIAL — {pass_rate*100:.0f}% match (meta ≥ 80%, precisa refinar)")
    else:
        print(f"  ❌ REPROVADO — apenas {pass_rate*100:.0f}% match")
    print("═" * 72)
    return pass_rate >= 0.80


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

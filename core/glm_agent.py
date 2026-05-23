"""
GLM-5.1 Agent — NVIDIA NVIDIA reasoning model via OpenAI-compatible API
Model: z-ai/glm-5.1 (thinking/reasoning support)
"""
import os, sys, json, time
from pathlib import Path

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
_REASONING_COLOR = "\033[90m" if _USE_COLOR else ""
_RESET_COLOR = "\033[0m" if _USE_COLOR else ""


def get_key():
    from core.api_keys import load_api_key
    return load_api_key("nvidia")


def ask(prompt, temperature=1, top_p=1, max_tokens=16384,
        show_reasoning=False, stream=False,
        enable_thinking=True, timeout=60.0, max_retries=2):
    """Call GLM-5.1 via NVIDIA API.

    Args:
        enable_thinking: True=reasoning mode (slow, more accurate). False=fast mode.
        timeout: HTTP timeout in seconds. Increase for complex prompts (JSON gen).
        max_retries: openai client retries on transient errors.
    """
    key = get_key()
    if not key:
        return None, "No NVIDIA API key configured"
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=key,
            timeout=timeout,
            max_retries=max_retries,
        )
        completion = client.chat.completions.create(
            model="z-ai/glm-5.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": enable_thinking,
                    "clear_thinking": False,
                }
            },
            stream=stream,
        )
        if stream:
            reasoning_text = ""
            content_text = ""
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                if not chunk.choices or getattr(chunk.choices[0], "delta", None) is None:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_text += reasoning
                    if show_reasoning:
                        print(f"{_REASONING_COLOR}{reasoning}{_RESET_COLOR}", end="", flush=True)
                content = getattr(delta, "content", None)
                if content:
                    content_text += content
                    if show_reasoning:
                        print(content, end="", flush=True)
            if show_reasoning:
                print()
            return {"content": content_text, "reasoning": reasoning_text}, None
        else:
            msg = completion.choices[0].message
            text = getattr(msg, "content", "") or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if show_reasoning and reasoning:
                print(f"{_REASONING_COLOR}{reasoning}{_RESET_COLOR}")
                print(text)
            return {"content": text, "reasoning": reasoning}, None
    except ImportError:
        return None, "openai library not installed (pip install openai)"
    except Exception as e:
        return None, str(e)[:200]


def analyze_script(topic, language="Portuguese", duration="5 min"):
    word_counts = {"5 min": 800, "8 min": 1200, "10 min": 1600}
    min_words = word_counts.get(duration, 800)
    prompt = f"""You are an expert documentary scriptwriter. Write a {duration} narration script about:
"{topic}"
LANGUAGE: {language}
MINIMUM WORDS: {min_words}

Rules:
- Raw narration text only, no stage directions
- Use rhetorical questions, dramatic pauses (...)
- Specific numbers, dates, names — no vague generalizations
- Vary sentence length: short punchy mixed with flowing
- End with powerful call-to-action
- Write at least the minimum words.

Structure:
- HOOK (30s) — grabs attention
- CHAPTER 1 — set the stage
- CHAPTER 2 — deep dive
- CHAPTER 3 — revelation
- CONCLUSION — emotional payoff + CTA

Write the complete script now."""
    return ask(prompt, temperature=0.9, show_reasoning=True)


def analyze_topic(topic_text):
    prompt = f"""Analyze this topic deeply and return a JSON object:
{topic_text}

Return ONLY valid JSON:
{{
    "theme": "main theme 2-3 words",
    "subtopics": ["list", "of", "specific", "subtopics"],
    "emotions": ["emotional", "tones"],
    "target_audience": "who would watch this",
    "visual_style": "recommended visual style",
    "key_moments": [{{"text": "phrase", "visual": "what to show"}}]
}}"""
    result, err = ask(prompt, temperature=0.3)
    if err:
        return None, err
    try:
        text = result["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0] if "```" in text else text
        return json.loads(text.strip()), None
    except Exception as e:
        return None, str(e)


def generate_shot_list(full_text, theme, duration):
    prompt = f"""You are a professional video editor creating a GLOBAL SHOT LIST.

FULL TRANSCRIPTION:
\"\"\"{full_text[:4000]}\"\"\"
VIDEO THEME: {theme}
TOTAL DURATION: {duration:.0f}s

Create a detailed shot list for the ENTIRE video. Group into ~8-second segments.
For each segment, provide 2 UNIQUE stock footage search terms.

CRITICAL RULES:
1. EVERY search term UNIQUE across the WHOLE video
2. Match MEANING of narration, not literal words
3. Context is about "{theme}"
4. If narration says "fish oil supplements" → search "omega capsules bottle" NOT "fish swimming"
5. Each term: 2-5 words, specific, visually accurate, English
6. VARY visuals: alternate close-ups, wide shots, diagrams, people, objects

Return ONLY valid JSON array:
[{{"start": 0, "end": 8, "terms": ["close up supplement capsules", "person taking vitamin"]}}]"""
    result, err = ask(prompt, temperature=0.3)
    if err:
        return None, err
    try:
        text = result["content"].strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0] if "```" in text else text
        return json.loads(text.strip()), None
    except Exception as e:
        return None, str(e)

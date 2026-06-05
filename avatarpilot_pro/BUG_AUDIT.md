# 🐛 Bug Prediction Audit — AvatarPilot Pro v1.1.0

**Data:** 2026-06-05
**Método:** Static analysis + edge case testing + code review

---

## ✅ Findings positivos (sem bugs encontrados)

### Resource leaks
- ✅ **tempfile.mkdtemp**: 2/2 com `shutil.rmtree` em `finally`
- ✅ **subprocess.run**: 99/99 com `timeout=` parameter (verificado em multi-line)
- ✅ **WhisperModel cache**: módulo-level singleton, sem reload
- ✅ **Real-ESRGAN cache**: lazy-load por scale, sem leak entre jobs

### Code hygiene
- ✅ **0 TODO/FIXME/HACK** em production code
- ✅ **0 hardcoded Windows paths** (tudo via BASE_DIR/MODELS_DIR)
- ✅ **7 bare excepts** mas todos em cleanup paths (acceptable)
- ✅ Atomic settings write + lock + AV retry

### Error handling
- ✅ `_upstream_error_response()` mapeia AI errors pra HTTP correto (429/401/402/503)
- ✅ `_check_cancel()` em 7 checkpoints do pipeline
- ✅ `_safe_pipeline_runner()` catches SystemExit + MemoryError + all Exception
- ✅ Watchdog 4h + 60min stall detector

### Concorrência
- ✅ `jobs_lock` em todas mutações do dict `jobs`
- ✅ `workers_lock` em `active_workers` counter
- ✅ `_settings_lock` em load/save settings
- ✅ Single-worker model evita VRAM oversubscription

---

## ⚠️ Issues encontrados + status

### 1. Voice inválida aceita sem warning (EDGE 4)
**Symptom:** `voice=xx-XX-FakeNeural` retorna HTTP 200 no submit.
**Impacto:** User só descobre falha durante pipeline (Edge-TTS retorna erro).
**Mitigação atual:** Edge-TTS retry 3x + voice fallback por idioma → degradação graciosa.
**Status:** Acceptable — fallback funcional, mas poderia validar upfront.

### 2. Mediapipe wheel bug Windows path non-ASCII
**Symptom:** `solutions.face_mesh.FaceMesh` falha com `FileNotFoundError`
**Root cause:** mediapipe C++ resource loader ANSI encoding, ç vira `?`
**Mitigação:** Fallback gracioso pra Haar cascade (já +165% sharpness)
**Status:** Conhecido, documentado. Resolve movendo projeto pra path ASCII.

### 3. MuseTalk subprocess uninterruptible
**Symptom:** Cancel pode demorar até 90s pra subprocess terminar.
**Mitigação:** Checkpoints `_check_cancel()` antes/depois + 90s grace.
**Status:** Acceptable — cancel race test 10/10 PASS com janela 90s.

---

## 🛡️ Edge cases validados nesta auditoria

| # | Cenário | Resultado |
|---|---|---|
| 1 | Generate sem nada | ✅ 400 + erro claro |
| 2 | Unicode complexo (chinês/árabe/hebraico/emoji) | ✅ 200 aceito |
| 3 | Script vazio + apenas image | ✅ 400 + erro |
| 4 | Voice inválida (xx-XX-Fake) | ⚠️ 200 (fallback no pipeline) |
| 5 | Image GIF inválida | ✅ 400 "Não foi possível ler imagem" |
| 6 | License key inválida | ✅ 400 + mensagem clara |

---

## 🔮 Possíveis issues futuras (não bugs, áreas de atenção)

### A. Long-running soak (24h+)
**Risco:** Memory creep de cache acumulado (Whisper, Real-ESRGAN, CodeFormer).
**Mitigação atual:** Watchdog 4h restart automatico.
**Recomendação:** Adicionar prometheus metrics + restart preventivo no schedule.

### B. Concurrent dialogue + regular job
**Risco:** Dialogue submete N jobs em batch — pode encher fila.
**Mitigação:** Max 10 turnos por dialogue. Rate limit 10/IP/60s.
**Status:** Suficiente pra single-user. Multi-user em vendor server precisa de queue manager dedicado.

### C. 4K + features pesadas em hardware fraco
**Risco:** GPU <8GB OOM em 4K.
**Mitigação:** Real-ESRGAN tem retry com batch reduzido. ETA warning em preflight.
**Recomendação:** Bloquear 4K se VRAM < 6GB livre (adicionar em preflight).

### D. License renewal flow
**Risco:** Quando user expira/renova, flow não testado.
**Mitigação:** License system suporta re-activation com mesma key.
**Recomendação:** Testar manualmente após Stripe live setup.

---

## ✅ Veredicto

**Production-ready para v1.1.0.**

- 0 critical bugs encontrados
- 3 limitações conhecidas (mediapipe path, MuseTalk uninterruptible, voice fallback acceptable)
- Edge cases cobertos com erros amigáveis (não 500 silenciosos)
- Code hygiene OK
- Resource cleanup OK
- Concorrência segura

**Próximo passo de bug-hunt:** Bug bash em VM clean pós-installer compilado.
Aí surge bugs específicos de deploy/setup que não dá pra prever no dev environment.

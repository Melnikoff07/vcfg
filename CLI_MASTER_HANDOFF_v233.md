# VCFG PS4 Frame Generation — Master CLI Handoff & Context Specification (`v2.33`)

> [!CAUTION]
> **АБСОЛЮТНОЕ ПРАВИЛО ПРОЕКТА (INVARIANT #0):**
> **НИКОГДА И НИГДЕ** не упоминай названия игр (ни на английском, ни на русском, ни в сокращениях) — ни в ответах в чате, ни в комментариях кода, ни в коммитах, ни в тестах, ни в `release notes`.
> Используй **ИСКЛЮЧИТЕЛЬНО `CUSAxxxxx` Title ID** (например, `CUSA07439`, `CUSA13801`, `CUSA00557`).

---

## 1. Конфигурация Antigravity CLI (`agy.exe`)

Все задачи запускаются через официальный консольный агент **Antigravity CLI (`v1.4.6`)**:
- **Бинарник:** `C:\Users\user\AppData\Local\agy\bin\agy.exe`
- **Единственная разрешенная модель и режим мышления:**
  ```text
  --model gemini-4-argon-high --effort high
  ```
  *(Никакие другие модели не используются — строго `gemini-4-argon-high` на уровне `high`).*
- **Базовый шаблон вызова CLI в headless-режиме:**
  ```powershell
  & "C:\Users\user\AppData\Local\agy\bin\agy.exe" `
    -p "<PROMPT_OR_INSTRUCTION>" `
    --mode agent `
    --workspace "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt" `
    --model gemini-4-argon-high `
    --effort high `
    --output-format json `
    --max-turns 40
  ```

---

## 2. Рабочее окружение, пути и инфраструктура

| Компонент | Путь / Адрес | Описание |
| :--- | :--- | :--- |
| **Workspace (Корень репозитория)** | `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt` | Основной репозиторий кода VCFG (`source/`, `classic/`, `overlay/`, `launcher/`, `tools/`) |
| **OpenOrbis SDK** | `C:\PS4-OpenOrbis` (`$env:OO_PS4_TOOLCHAIN`) | PS4 SDK, заголовочные файлы, стабы, `create-fself.exe`, `PkgTool.Core.exe` |
| **Компилятор / Шейдеры** | `C:\Program Files\LLVM\bin\clang.exe`, `llc.exe`, `ld.lld.exe` | Компиляция C (`x86_64-scei-ps4`) и компиляция шейдеров LLVM IR (`-march=amdgcn -mcpu=bonaire`) |
| **PS4 Pro / Slim (FTP)** | `192.168.1.21:2121` (без пароля, `anonymous`) | Тестовая консоль с GoldHEN. Деплой выполняется **только атомарно** через `.stage` -> `DELETE` -> `RNFR`/`RNTO` |
| **GitHub Репозиторий релизов** | `k13509792-eng/ps4-vcfg-release` | Репозиторий публичных сборок. На GitHub сейчас опубликована `v2.32`. Версия `v2.33` собрана и залита локально на консоль `192.168.1.21` (не публиковать на GitHub без явной команды!) |

---

## 3. Текущее состояние проекта (`v2.33` Hardened)

### 3.1. Версии и собранные артефакты (`v2.33`)
Вся кодовая база находится на версии **`2.33`** (`overlay/vov.h` -> `VOV_VERSION "2.33"`, `launcher/build_pkg.ps1` -> `2.33`).
Текущие скомпилированные бинарники (уже развернуты на PS4 `192.168.1.21:2121`):

| Файл в репозитории | Путь на PS4 (`192.168.1.21:2121`) | Размер (байт) | SHA256 (первые 16 символов) | Назначение |
| :--- | :--- | :--- | :--- | :--- |
| `build/vcfg/vcfg.prx` | `/data/GoldHEN/plugins/vcfg16.prx` | `494,864` | `cff1fe3868c90d7d` | Движок `ENGINE_NEW` (`CUSA07439` family) |
| `build/classic/vcfg_classic.prx` | `/data/GoldHEN/plugins/vcfg_classic51.prx` | `586,416` | `e11212bbfc6ee521` | Основной универсальный движок `ENGINE_CLASSIC` / `ENGINE_CLASSIC_BB` |
| `build/classic/vcfg_classic.prx` | `/data/GoldHEN/plugins/vcfg_alt_16.prx` | `586,416` | `e11212bbfc6ee521` | Профиль `ENGINE_CLASSIC_ALT` (единый бинарник с `vcfg_classic.prx`) |
| `build/launcher/VCFG_v2.33.pkg` | `/data/pkg/VCFG_v2.33.pkg` | `6,619,136` | `739e6d5d7b46193d` | Установщик лаунчера `VCFG00001` (включает все 3 `.prx` внутри `/app0/plugins/`) |

### 3.2. База поддерживаемых тайтлов
- **59 канонических семейств `CUSAxxxxx`** в `k_sup[]` (`launcher/src/main.c:43-104`) и `k_ver[]` (`overlay/title_families.h:266-279`).
- **140+ региональных алиасов** в `k_vcfg_title_aliases[]` (`overlay/title_families.h:10-248`).
- **20 семейств в `auto_2buf()`** (`launcher/src/main.c:106-119`), включая два новых проверенных 2-буферных тайтла из `R20`: **`CUSA02962`** и **`CUSA01004`**.
- На консоли в `/data/GoldHEN/plugins.ini` прописаны **170 секций `[CUSAxxxxx]`**.

### 3.3. Что уже внедрено и протестировано в `v2.33`
В `v2.33` поверх стабильной базы `v2.32` (4-ступенчатый клапан памяти `R07`, авто-калибровка G-Velocity `R08`, биквадратный 12-tap `warp8`/`warp8g` + перцептивный `rcas8` `R09`, защита границ и субтитров `R10`, субпиксельный 8x8 поток `R11`, адаптивный ФАПЧ-пейсер `R12`) интегрированы 8 исследований (`R13`..`R20`):

1. **`R13` — Async Compute & Ping-Pong Pyramid Reuse (`classic/source/main.c`, `classic/source/fg8_table.h`)**:
   - Пирамида яркости предыдущего кадра (`L0prev`..`L3prev`) не пересчитывается с нуля каждый кадр, а переиспользуется через переключение четности слотов `slot_parity ^ 1` (`pyr_warm`), экономя 4 шейдерных диспатча (`luma`, `pyr8` x3) и снимая `s_waitcnt` барьер перед `me3`.
2. **`R14` — Квадратичная 3-точечная траектория движения (`classic/shaders/me3.ll`, `me1.ll`, `fgs8.ll`, `warp8.ll`, `warp8g.ll`)**:
   - Использование истории двух прошлых полей потока (`V_prev`, `V_prev2`) для предсказания ускорения (`2*V_prev - V_prev2`) при вращении камеры и проверки темпоральной согласованности (`cw`).
3. **`R15` — Анти-шиммер бликов, тонких проводов и листвы (`classic/shaders/warp8.ll`, `warp8g.ll`, `rcas8.ll`)**:
   - `diff3_k` с подавлением одиночных ярких бликов (`glint_d`), детектор тонких 1-px линий `ridge_boost` (100% непрерывность проводов без разрывов) и анизотропный `rcas8` с подавлением мерцания листвы (`canopy_atten`).
4. **`R16` + `R19` — Защита 2-буферного режима от перезаписи истории + `YCoCg-R 4:2:0` (`classic/shaders/pcopy8.ll`, `classic/source/fg8_table.h`)**:
   - Детектор `fg8_check_2buf_overwrite_hazard()` ловит перезапись буфера `P` движком игры при нехватке `g_pbuf` (16 МБ) и сжимает историю в 4.15 МБ буфер `YCoCg-R 4:2:0` (`16 бит/пиксель`).
   - Исправлены смещения дескрипторов `pcopy8.ll` (`V#ycocg` на `+96`, параметры `{mode, pitch, w, h}` на `+112`) и добавлена проверка границ `x < w && y < h` для `1600x900`.
5. **`R17` — Runtime Auto-Healer & Persistent `adapt-<TITLE>.ini` (`classic/source/main.c`, `classic/source/debug_hooks.h`)**:
   - При срабатывании клапана памяти, переключении `2-buf` или перехвате краша (`SIGSEGV`/`SIGBUS`/`SIGABRT`) плагин сохраняет найденный рабочий профиль (`shave_mode`, `reg_mode`, `two_buffer`, `vout_path`, `gv_conv`) в `/data/bb_probe/adapt-<TITLE>.ini`, чтобы при следующем запуске сразу стартовать с безопасными параметрами.
6. **Loading-Screen Submission Hibernation Gate (`classic/source/main.c`)**:
   - Функция `on_sub_hibernation_gate()` в хуках `sceGnmSubmitCommandBuffers` и `sceGnmSubmitAndFlipCommandBuffers` мгновенно ставит генерацию кадров на паузу во время тяжелых загрузок уровней (когда идут аллокации DirectMemory без новых флипов экрана), предотвращая дедлоки на экранах загрузки (`CUSA00557`, `CUSA13795` и др.).
7. **`R18` — Защита конечных автоматов и памяти (`classic/source/main.c`, `classic/source/debug_hooks.h`, `classic/source/fg8_table.h`)**:
   - Сброс `g_mbuf_state = -1` в `release_early_mem()` и проверка `g_pre_mem` в `try_recover_hibernated_work_at()` (устранен Use-After-Free через 180 кадров после Step 6).
   - Переиспользование освобожденных слотов в `record_flex_vmap_ex()` (устранено переполнение таблицы `g_flex_vmap[512]` после 512 циклов загрузки).
   - Полная обратимость перехода `2-buf -> 3-buf` (`g_pbuf_valid = 0`) и проверка `P != C` для `pyr_warm`.
8. **`R20` — Триаж-анализатор `v2.0` (`tools/triage_logs.py`)**:
   - Поддержка 5 корзин (`BUCKET_1_OLD_VERSION` .. `BUCKET_5_NEW_SUPPORTED_CANDIDATE`) и 3 скрытых симптомов без краша (`SYMPTOM_LOW_FPS_LOCK`, `SYMPTOM_AUDIO_DM_STARVATION`, `SYMPTOM_LOADING_FREEZE`).

### 3.4. Аппаратный бюджет шейдеров GCN (`gfx700` / Bonaire)
- Все 27 шейдеров в `classic/shaders/*.ll` компилируются со строго нулевым использованием скретч-памяти (`scratch = 0`, `VGPR <= 64`, `SGPR <= 104`, `LDS <= 32768`).
- Суммарный размер 17 рантайм-шейдеров Mode 8 в кодовом регионе GPU:
  **`0x8000 + 0x13700 = 0x1B700`** (лимит `DCB8_OFF = 0x1C000`, свободно `0x900` = `2304` байт).

---

## 4. Команды сборки, тестирования и деплоя

### 4.1. Полная сборка плагинов и PKG
Запускать из корня `C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt`:
```powershell
Set-Location "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt"

# 1. Сборка нового движка (build/vcfg/vcfg.prx) + встроенные тесты клапана памяти и 69 аппаратных трейсов
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 2. Перегенерация шейдеров (classic/source/shaders.h) и сборка classic-движка (build/classic/vcfg_classic.prx)
powershell -ExecutionPolicy Bypass -File .\classic\build.ps1

# 3. Сборка лаунчера и упаковка инсталлятора (build/launcher/VCFG_v2.33.pkg)
powershell -ExecutionPolicy Bypass -File .\launcher\build_pkg.ps1
```

### 4.2. Запуск тестовых наборов (Unit Tests & Fuzzers)
```powershell
Set-Location "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt"

# Быстрый прогон ключевых наборов безопасности v2.33 (выполняется ~1 сек):
python -m unittest `
  tools/tests/test_lifecycle_fuzzer.py `
  tools/tests/test_shader_safety_audit.py `
  tools/tests/test_triage_logs.py `
  tools/tests/test_2buf_history_protection.py `
  tools/tests/test_loading_hibernation.py `
  tools/tests/test_runtime_autohealer.py -v

# Прогон тяжелых попиксельных симуляторов шейдеров (R09..R15, выполняется ~40-60 сек):
python -m unittest `
  tools/tests/test_pyramid_pingpong.py `
  tools/tests/test_quadratic_flow_consistency.py `
  tools/tests/test_specular_foliage_antishimmer.py `
  tools/tests/test_disocclusion_and_hud.py `
  tools/tests/test_edge_and_moving_warp.py `
  tools/tests/test_fine_grid_8x8_flow.py -v
```

### 4.3. Триаж новых архивов логов от пользователей
Когда приходит `.zip` архив или папка с логами (`probe.txt`, `crash.txt`, `vcfg_error_report_*.txt`):
```powershell
Set-Location "C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt"
python tools/triage_logs.py "<PATH_TO_ZIP_OR_DIR>" --export-traces --report-md "<PATH_TO_REPORT.md>"
```
Скрипт автоматически раскладывает все сессии по 5 корзинам:
- **`BUCKET_1_OLD_VERSION`** — старая версия плагина (`< 2.32`). Игнорировать.
- **`BUCKET_2_L2_NO_CRASH`** — чистая сессия без крашей и без скрытых симптомов.
- **`BUCKET_3_NEW_TITLE_CRASH`** — ошибка/краш/симптом на новом неподдерживаемом `CUSAxxxxx`.
- **`BUCKET_4_VERIFIED_REGRESSION`** — регрессия, краш, `INERT_NO_MBUF` или скрытый симптом (`SYMPTOM_LOW_FPS_LOCK`, `SYMPTOM_AUDIO_DM_STARVATION`, `SYMPTOM_LOADING_FREEZE`) на поддерживаемом `CUSAxxxxx` (наивысший приоритет!).
- **`BUCKET_5_NEW_SUPPORTED_CANDIDATE`** — новый `CUSAxxxxx`, отработавший `>= 300` флипов и `>= 100` промежуточных кадров без ошибок. Кандидат на добавление в `k_sup` (`launcher/src/main.c`) и `overlay/title_families.h` (если `recommend_auto_2buf == true`, также добавить в `auto_2buf()`).

### 4.4. Атомарный деплой на PS4 (`192.168.1.21:2121`)
При заливке `.prx` или `.pkg` на консоль всегда использовать `.stage` файл с последующим переименованием:
```python
import ftplib, hashlib, io, pathlib

repo = pathlib.Path(r"C:\Users\user\Documents\Codex\2026-10-18\new-chat\work\vcfg-alt")
ftp = ftplib.FTP()
ftp.connect("192.168.1.21", 2121, timeout=15)
ftp.login()

def atomic_upload(local_path: pathlib.Path, remote_dir: str, remote_name: str):
    data = local_path.read_bytes()
    stage = f"{remote_dir}/{remote_name}.stage"
    target = f"{remote_dir}/{remote_name}"
    ftp.storbinary(f"STOR {stage}", io.BytesIO(data))
    try:
        ftp.delete(target)
    except Exception:
        pass
    ftp.rename(stage, target)

atomic_upload(repo / "build/vcfg/vcfg.prx", "/data/GoldHEN/plugins", "vcfg16.prx")
atomic_upload(repo / "build/classic/vcfg_classic.prx", "/data/GoldHEN/plugins", "vcfg_classic51.prx")
atomic_upload(repo / "build/classic/vcfg_classic.prx", "/data/GoldHEN/plugins", "vcfg_alt_16.prx")
atomic_upload(repo / "build/launcher/VCFG_v2.33.pkg", "/data/pkg", "VCFG_v2.33.pkg")
ftp.quit()
```

---

## 5. Контракт ответа для автоматического CLI-оркестратора

При работе в автоматическом пайплайне (или по запросу пользователя) в конце выполнения задачи выдавай краткое резюме на русском языке (строго без названий игр, только `CUSAxxxxx`) и при необходимости блок `JSON`:

```json
{
  "status": "fixed_and_built | no_action_needed | needs_hardware_test",
  "version": "2.33",
  "triaged_buckets": {
    "bucket_1_old_version": [],
    "bucket_2_l2_no_crash": [],
    "bucket_3_new_title_crash": [],
    "bucket_4_verified_regression": [],
    "bucket_5_new_supported_candidate": []
  },
  "root_causes_fixed": [
    {
      "cusa_id": "CUSAxxxxx",
      "symptom": "краткое описание причины по логу",
      "files_modified": ["classic/source/main.c"]
    }
  ],
  "tests_passed": true,
  "artifacts": {
    "vcfg_prx": "build/vcfg/vcfg.prx",
    "vcfg_classic_prx": "build/classic/vcfg_classic.prx",
    "pkg": "build/launcher/VCFG_v2.33.pkg"
  },
  "deployed_to_ps4": true,
  "published_to_github": false
}
```

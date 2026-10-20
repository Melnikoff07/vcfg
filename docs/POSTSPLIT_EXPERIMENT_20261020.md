# Immutable split AA with raw color history, 2026-10-20

Status: CUSA00557 and CUSA08519 hardware activation observed. The owner judged
CUSA00557 perhaps a couple percent better and CUSA08519 "вроде норм" after a
short test. This is an experiment, not a release or proof that all quality
metrics improved.

## Why another test

The raw-real / out-of-place mid-post diagnostic removed the previous-frame
sharpening feedback and in-place post aliasing, but the owner still reports
flicker. The next test adds Enhance AA1 to displayed real and generated frames.
The existing 16x16 motion reconstruction, mid sharpness 100 and cut-replacement
disable remain the same as in that diagnostic; the two-grid mix is still absent.

Applying real AA in place would again contaminate the next warp's P input.
This build keeps one separate raw-history surface and one post scratch:

1. Select saved raw P only for a contiguous real-frame sequence; first frame,
   unsupported profile or sequence gap invalidates that history selection.
2. Build raw C luma, compute motion and render raw mid M using raw P and C.
3. Post M with AA1 + sharpness 100 into scratch; barrier, point-copy back to M.
4. Point-copy raw C into history, after the previous P's last warp use.
5. Post real C with AA1 and zero sharpening into scratch; barrier, point-copy
   back to C before its original flip. Every neighborhood post reads a source
   that remains immutable throughout that dispatch.

The real game surface is ultimately AA-filtered for display, but is not the
next contiguous warp's color history. Retained motion pyramids also stay raw.
Native mode performs no post; its frames remain raw. Overlays target mids only.
Runtime logs include `POSTSPLIT active=1` and raw-prior transitions so raw-history
selection can be confirmed instead of inferred from a fixed status string.

## Offline assessment and limits

`experiment_split_immutable.py` screens AA1/sharp100, AA1/sharp50 and AA2/sharp50
on six even archived triples. AA1/sharp100 was fixed for the six odd triples.
The existing 12 scenes were explored previously: this is not an unseen-scene
validation set. `bench/split_immutable/combined_results.json` holds all 12 rows
for the chosen profile and input/source SHA-256 entries. Sparse triples cannot
measure continuous in-game shimmer or history behavior on native transitions.

With a common raw GT and raw endpoints:

| Method | PSNR | SSIM | LPIPS lower | TCE lower | static PSNR |
|---|---:|---:|---:|---:|---:|
| raw-real mid-only diagnostic | 31.10736 | 0.852013 | 0.0888622 | 2.676592 | 36.91372 |
| split AA1, raw history, sharp100 mid | 31.52051 | 0.861692 | 0.1113604 | 2.617562 | 37.40875 |

AA1 worsens raw-reference LPIPS by about 25.3%, despite improving several other
scores. The HF/GT proxy falls from 1.02553 to 0.72260, indicating substantial
loss/change of fine detail relative to raw game imagery. This is a meaningful
softness tradeoff, not an all-metrics win. The owner has been asked to judge
both remaining flicker and whether the softness is acceptable.

Against the different, AA1-filtered displayed target/endpoints, the chosen
method scores PSNR 32.39847, SSIM 0.881271, LPIPS 0.0778225 and TCE 2.388914;
HF/displayed-GT is 0.93030. These cannot be ranked against raw-target scores as
the same task. HUD PSNR remains unavailable; HUD_flicker is only the static
fallback. A softer target is not evidence that artifacts were fixed in play.

## Safety checks and artifact

`build_postfix_plugin.py --split` stages sources under `build/postfix_split/`,
using `build_postfix_split.py`. Production runtime files and generated headers
are not replaced. Only CUSA00557 and its split marker arm this plugin. The
active path requires Quality / sharp100 / Enhance1 / overlay0 / one mid /
optical-only motion. Failure/unsupported settings disable split post/history.

The 18 MiB flexible allocation supplies two 2 MiB aligned 8 MiB surfaces,
retained until process exit. Command tables are distinct and immutable:
real post at slot+0, mid copy +0x680, raw-history copy +0x700, real final copy
+0x780, all below the VS region +0x800; primary table remains slot+0xB00.

- Actual staged C command builders pass 144 host cases: four slots, enabled
  and rejected profiles/allocation, present copy on/off, and first/contiguous/
  discontinuous raw history. Checks decode the dispatch sequence and ensure
  raw C is saved before real AA, post destinations are separate, the real post
  has zero sharpening, and the original flip tail is retained. Maximum DCB:
  2592 / 8192 bytes. All 128 possible flexible page offsets fit both surfaces.
- The actual post IR was host-executed for mid 100/AA1 and real 0/AA1 on two
  full-size inputs: 24,883,200 RGB components, 39 one-code differences from
  NumPy, none larger; source hashes, alpha and destination guards pass. This
  checks math/ownership, not actual GPU scheduling or texture tiling.
- Seven existing benchmark tests and `git diff --check` pass.

Artifact: `build/postfix_split/vcfg_postfix_split.prx`, 409520 bytes.
SHA-256: `146f5abfdbd6e8e69da3e526d3c9ec96320d0145e991f65e5e202b9e4eb4304d`.
Remote: `/data/GoldHEN/plugins/vcfg_classic_postsplit_20261020a.prx`.
Marker: `/data/bb_probe/postfix_split_CUSA00557`.
Previous mapping: `vcfg_classic_postfix_20261020a.prx`.
INI backup: `/data/GoldHEN/plugins.ini.before_CUSA00557_20261020_202518`.
Profile backup: `/data/bb_probe/config-CUSA00557.ini.before_postsplit_20261020_202508`.

Only CUSA00557 changed: profile Enhance 0 -> 1, with exact staged/read-back
backup, and a uniquely named PRX through console.py set. Framegen, preset,
sharpness, overlay and other title profiles/mappings remain unchanged. Local
profile copies and preparation metadata: `logs/postsplit_before_20261020/`.
Klog capture continues in `logs/postfix_before_20261020/hardware_klog.txt` and
contains both test sessions, separated by plugin load and POSTSPLIT markers.

Initial hardware log confirms allocation at 0x881db4000, aligned scratch at
0x881e00000, raw history at 0x882600000, active=1 and raw-prior=1 at seq122 after
initialization at seq121. That confirms activation and initial history use;
loading/menu GPU/cadence samples are not steady-gameplay acceptance evidence.
The owner has been asked to fully restart and compare the same scene for
flicker versus softness. `rollback_postsplit_test.py` restores postfix a and
the exact previous profile, moving the marker into quarantine. No artifacts
are deleted and no package, push or release was made.
# Дополнительный тест CUSA08519, 20:32

Пользователь оценил изменение на CUSA00557 как возможные «пару процентов»
и запросил перенос того же варианта на другую игру. Это не принятие качества.

Установленная CUSA08519 подтверждена в live FTP inventory и ранее сохранённом
launcher log. Для неё собран отдельный title/marker-gated вариант без новых
алгоритмических изменений. Шейдеры postout8, warp8, me1, mf8 побайтово совпали
с CUSA00557 postsplit. Host command check: 144 случая, максимум 2592/8192 байт;
128 смещений аллокации. Старый title gate CUSA00557 в новой сборке заменён
только в экспериментальной проверке; штатные known-game списки сохранены.

- Артефакт: `build/postfix_split_CUSA08519/vcfg_postfix_split.prx`, 409536 байт.
- SHA256: `7457653b121a17bcbcc93f0d707a8720d75831984274a3e73ba251f47b813e84`.
- Remote: `/data/GoldHEN/plugins/vcfg_classic_postsplit_08519_20261020a.prx`.
- INI backup: `/data/GoldHEN/plugins.ini.before_CUSA08519_20261020_203242`.
- Profile backup: `/data/bb_probe/config-CUSA08519.ini.before_postsplit_20261020_203236`.
- Marker: `/data/bb_probe/postfix_split_CUSA08519`.
- Только для CUSA08519 sharpness 40 -> 100 для совпадения с испытанным профилем;
  Enhance1, Quality, framegen1, overlay0 сохранены.
- Snapshot: `logs/postsplit_08519_deployed_20261020`, 43 файла, 0 ошибок.
- Проверено: изменилась только секция CUSA08519; профиль совпадает со staged
  побайтово. CUSA00557 осталась на прежней postsplit-сборке.
- Restore: `tools/bench/prepare_postsplit_08519.py --rollback` проверяет текущие
  значения перед восстановлением classic36 и точного исходного профиля.

После полного перезапуска пользователь сообщил по CUSA08519: «да вроде норм».
В `logs/postfix_before_20261020/hardware_klog.txt` видны загрузка нужного PRX,
`POSTSPLIT active=1` и переход `raw-prior=0` на seq121 к `raw-prior=1` на seq122.
Поздние окна показывают примерно 7.4–8.7 мс GPU на наши проходы, включая
сцены с `game span` около 25–35 мс; это аппаратное время проходов, не
input-to-photon latency и не строгая оценка качества. В проверенных строках
нет признака нового GPU fault. Краткий отзыв не доказывает отсутствие
мерцания в иных сценах. Действующий ограниченный klog collector —
`logs/postfix_before_20261020/hardware_klog.txt`.

Новый внешний отчёт разобран отдельно в `QUALITY_REPORT_AUDIT_20261020.md`;
его изменения в эту сборку не входят.


# Architecture — v2.36

VCFG ships two GoldHEN plugin engines and a launcher. The new engine is in `plugin/`; the classic engine is in `classic/`. The launcher installs plugins, manages per-title settings and collects reports. The overlay provides the in-game menu and FPS counter.

## Frame generation

The plugin observes game submissions and VideoOut buffers, checks the title profile and runtime safety gates, then estimates motion between consecutive game frames on the GPU. Pyramid, motion, filtering and warp passes synthesize an intermediate image. Presentation inserts generated images between native frames when the selected mode and runtime state allow it.

Source dimensions, pixel formats, allocation sizes and generated-buffer counts depend on the engine, title and memory tier. Do not apply early 1080p-only or single-buffer assumptions to all titles. v2.36 includes 3200×1800 and non-16:9 warp handling and targeted 10-bit fixes.

## Modes

- **Quality:** existing quality-oriented generation path.
- **Fast:** shallower presentation queue and timer pacing, including below-30-FPS output. The timer path is excluded for the CUSA01439 family. An empty `/data/bb_probe/no_pace_timer_<TITLE>` opts a title out.
- **Sharp:** stronger sharpening of the real frame before generation; the effective profile adds 35 to the slider value, or uses 50 when the slider is zero.

The Fast latency figures in the release notes are trace-replay estimates, not a hardware input-lag guarantee. Effective behavior also depends on runtime gates. Menus expose ACTIVE / READY / INERT / LIMITED states; logs record `options effective:`.

## Safety boundaries

- Admit work only when buffer layout, submission form and required GPU state are understood; rejected frames use native presentation.
- Preserve ordering between game reads/writes, generated work and scanout. A buffer cannot be reused while it is still being read or displayed.
- Memory pressure can reduce or disable generation. Never free memory or unregister surfaces before GPU, command and scanout retirement are proven.
- Shader headers contain generated machine code: edit shader IR and regenerate arrays, never patch headers with text replacement.
- Host tests and trace replays do not prove PS4 behavior. Hardware validation is a separate step.

See [contribution checks](CONTRIBUTING.md) and the [release notes](https://github.com/Melnikoff07/vcfg/releases/tag/v2.36).

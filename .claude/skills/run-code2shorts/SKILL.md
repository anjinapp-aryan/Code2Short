---
name: run-code2shorts
description: Build, run, and drive Code2Shorts - compile and trace real Java, render the Manim video, generate speech and subtitles, produce and validate a 1080x1920 MP4, and extract screenshot frames. Use when asked to run, start, build, test, render, screenshot, or debug Code2Shorts or its pipeline.
---

# Running Code2Shorts

Code2Shorts turns a Java algorithm into a 1080×1920 educational short by
**really executing the Java** and rendering only what the execution trace
proves happened.

Two things make it unlike a normal app:

- **There is no GUI and the packaged CLI is a stub.** `code2shorts` only
  prints a version. Don't conclude the app is broken — the app *is* the
  pipeline.
- **"Screenshot" means extracting frames from the MP4.** A green exit code
  says nothing about what is on screen. You must look at PNGs.

Everything is driven through one script. **Paths below are relative to the
repo root.**

```
.claude/skills/run-code2shorts/driver.py
```

## Prerequisites

Verified on Windows 11 + Git-Bash/PowerShell, Python 3.12.10, JDK 25
(compiles to release 17), Maven 3.9.15, Manim 0.21.0, FFmpeg 7.1.

```bash
python -m venv .venv
.venv/Scripts/activate                  # Windows;  source .venv/bin/activate elsewhere
pip install -e ".[dev,render]"
```

FFmpeg must be reachable as `ffmpeg`. `imageio-ffmpeg` ships the binary
under a different name, so copy it once:

```bash
cp .venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe \
   .venv/Scripts/ffmpeg.exe
```

**The instrumenter jar must be built or `trace()` fails:**

```bash
cd tools/java-instrumenter && mvn -q clean package && cd ../..
```

Check everything at once:

```bash
.venv/Scripts/python.exe .claude/skills/run-code2shorts/driver.py doctor
```

It prints a fix hint per missing tool and exits non-zero until all are
present.

## Run (agent path)

```bash
# In Git-Bash, Maven needs this first (see Gotchas). PowerShell doesn't.
export MAVEN_HOME='I:\Software\apache-maven-3.9.15-bin\apache-maven-3.9.15'

P=.venv/Scripts/python.exe
D=.claude/skills/run-code2shorts/driver.py

$P $D --help                        # command list
$P $D doctor                        # tool + jar + SAPI check
$P $D trace   two_sum               # real compile+execute+trace, dump events
$P $D frames  move_zeroes           # array/pointer/scalar state per event
$P $D sync    palindrome            # highlighted line == real source line?
$P $D scene   palindrome            # Manim source, NO render  (~2s)
$P $D render  palindrome            # REAL render -> MP4       (~25-35s)
$P $D pipeline remove_duplicates    # render+speech+subtitles+mux+validate
$P $D shots   output/driver/remove_duplicates/final.mp4 4
$P $D validate output/driver/remove_duplicates/final.mp4 20.10
```

Algorithms: `reverse_string palindrome two_sum move_zeroes remove_duplicates`.
Output lands in `output/driver/<algo>/` (gitignored).

**Pick the cheapest command that covers your change:**

| You changed... | Run |
|---|---|
| `visualization/primitives.py`, `manim_renderer.py` | `scene` (2s) — it compiles the generated source; only `render` when you need pixels |
| `visualization/state.py` | `frames` — no render needed |
| `code_state.py`, instrumenter, trace runtime | `sync` — checks every event against real source |
| `langadapter/java/**` | `trace` |
| `narration/**`, `media/**` | `pipeline` |
| anything visual | `render` → `shots` → **read the PNGs** |

### Looking at the output

```bash
$P $D shots output/driver/palindrome/render/media/videos/scene/1920p30/output.mp4 3
```

Then **read the PNG files**. Frames land in a `frames/` dir beside the MP4.

The code panel is small in a 1080×1920 frame — reading line numbers off a
full-frame PNG is unreliable. Crop and upscale before making any claim
about which line is highlighted:

```bash
$P -c "from PIL import Image; im=Image.open(r'output/driver/palindrome/render/media/videos/scene/1920p30/frames/frame_1.png'); b=(330,1055,750,1265); im.crop(b).resize(((b[2]-b[0])*3,(b[3]-b[1])*3), Image.LANCZOS).save('zoom.png')"
```

Better: don't eyeball it at all — `driver.py sync` checks every event
against the real file and prints `checked=N mismatches=M`.

## Run (human path)

There isn't a meaningful one. `code2shorts` prints a version and exits.
The closest human-facing entry points are the scripts, which do the same
work as the driver with fixed settings:

```bash
$P scripts/run_golden_path.py            # video only
$P scripts/run_narrated_golden_path.py   # + speech + subtitles
$P scripts/measure_performance.py        # regenerates docs/PHASE_4_5_PERFORMANCE.md
```

## Test

```bash
$P -m pytest -q -m "not integration"   # 269 tests, ~0.6s, no external tools
```

**The integration and real-render tests silently SKIP unless the tools are
on `pytest`'s own PATH.** The driver self-heals its PATH; `pytest` does not.
Without this you get a reassuring `6 skipped` and learn nothing:

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"     # PowerShell
$env:MAVEN_HOME = 'I:\Software\apache-maven-3.9.15-bin\apache-maven-3.9.15'

.venv\Scripts\python.exe -m pytest -q tests/test_e2e_golden_path.py   # 6 passed, ~2.2 min
.venv\Scripts\python.exe -m pytest -q                                 # 342 tests, ~5 min
```

Always check the summary line says `passed`, not `skipped`.

## Gotchas

- **Git-Bash has no Maven on PATH here; PowerShell does.** In Bash you get
  `MavenNotFoundError: mvn executable not found on PATH`.
  **`export PATH="$PATH:/i/Software/.../bin"` does NOT fix it** - bash's own
  `which mvn` then finds it, but a native Windows Python still cannot: the
  POSIX `/i/...` entry is not translated on the way into the child process
  and arrives as an *empty* PATH element. Verified:
  ```bash
  export PATH="$PATH:/i/Software/apache-maven-3.9.15-bin/apache-maven-3.9.15/bin"
  which mvn                                    # -> found
  .venv/Scripts/python.exe -c "import shutil; print(shutil.which('mvn'))"   # -> None
  ```
  Use a **Windows-style** `MAVEN_HOME` instead, which the driver honours:
  ```bash
  export MAVEN_HOME='I:\Software\apache-maven-3.9.15-bin\apache-maven-3.9.15'
  ```
  The driver self-heals `manim`/`ffmpeg` from `.venv/Scripts` automatically.
- **`pytest -x tests/...` piped through PowerShell `Select-Object -First N`
  reports exit 255.** That's the pipe closing early, not a failure. Check
  `$LASTEXITCODE` from an unpiped run.
- **Manim silently drops leading/trailing blank lines in `Code(...)`.**
  Verified: leading and trailing *truly empty* lines are stripped (all of
  them), interior blanks and whitespace-only lines survive.
  ```bash
  $P -c "from manim import Code; c=Code(code_string='\nAAA\nBBB\nCCC',language='java',add_line_numbers=True,line_numbers_from=2); print(len(c.code_lines), c.line_numbers.lines_text.text)"
  # -> 3 234   (input had 4 lines)
  ```
  This **used to corrupt every rendered line number**: a window starting on
  a blank line lost that line while the label column kept counting, so every
  number read one low and the highlight box landed on the *next* statement.
  Fixed in `code_state.py::_trim_blank_edges`, which trims the same set so
  the invariant "`lines[i]` is file line `start_line + i`" survives
  rendering. **If you touch windowing or `code_panel`, re-check this** -
  `driver.py sync` catches the logic half, but only a cropped frame catches
  the render half.

- **Frames sampled from a video often land mid-fade** and look dim, which
  reads as a rendering bug but is a sampling artifact. `shots` already
  samples mid-step to avoid this; if you extract frames yourself, don't
  sample at exact step boundaries.
- **Render time is 25–35s per algorithm and dominates everything else**
  (~83% of pipeline time). `scene` exists so you don't pay it while
  iterating on layout code.
- **A first-ever `mvn` run downloads dependencies** and takes far longer
  than the ~1.3s steady-state compile.
- **`trace()` needs the instrumenter jar.** If you `mvn clean` in
  `tools/java-instrumenter`, every trace fails until you re-`package`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `MavenNotFoundError: mvn executable not found on PATH` | Run from PowerShell, or in Bash `export MAVEN_HOME='I:\Software\...'` (a Windows-style path). Appending to `$PATH` in Bash does **not** work - see Gotchas. |
| `instrumenter jar not found at .../java-instrumenter-1.0.0.jar` | `cd tools/java-instrumenter && mvn -q clean package` |
| `FileNotFoundError: ...\render\...\render\scene.py` | A caller passed a relative output dir. The renderer resolves paths itself now; if you see this, something bypassed `ManimVideoRenderer.render`. |
| `manim`/`ffmpeg` "not found" although installed | Use the driver (it prepends `.venv/Scripts`), or prepend it yourself. |
| `TTSFailure: Windows SAPI is unavailable` | Non-Windows host — the pipeline falls back to `SyntheticTTSProvider` (real but silent audio). `doctor` reports which is active. |
| Blank/black extracted frame | You sampled during a fade; re-extract at a different timestamp. |
| `policy: FAIL ... picture was truncated/stretched` | Genuine composition drift. Do **not** "fix" it by trimming — the visual timeline is authoritative; fix the composition arguments. |

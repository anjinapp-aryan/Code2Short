# Phase 4.5 — Focused Reuse Audit (Production Hardening)

Scope: only capabilities relevant to hardening. Decisions from Phase 4.2A /
4.3 / 4.4 stand and are not re-litigated. Verified 2026-08-24 by inspecting
the installed environment, not by reading READMEs.

## Decision matrix

| Candidate | Capability | License | Maintenance | Compatibility | Security | Decision |
|---|---|---|---|---|---|---|
| **PyAV** (`av`) | probe real video/audio streams | BSD-3 ✅ | active, 2026-08-22 | **already installed** (Manim dep), already used by `media/probe.py` | library call, no new surface | 🟢 **DIRECT REUSE** |
| **FFmpeg** | mux, encode, frame extraction | LGPL/GPL build | active | already used since Phase 4.1 | fixed-arg subprocess, timeout | 🟢 **DIRECT REUSE** |
| **`srt`** | SRT compose/parse/validate | MIT ✅ | format frozen | **already installed** | pure-python | 🟢 **DIRECT REUSE** |
| **pytest** (+ markers) | integration gating, skipif | MIT ✅ | active | already the test runner; `integration` / `real_render` markers already registered | n/a | 🟢 **DIRECT REUSE** |
| **stdlib `hashlib`** | artifact checksums | PSF | stdlib | already used by `Artifact.create` | n/a | 🟢 **DIRECT REUSE** |
| **stdlib `tempfile` / `shutil`** | workspace isolation + cleanup | PSF | stdlib | already `execution/sandbox.py::Workspace` | n/a | 🟢 **DIRECT REUSE** |
| **stdlib `subprocess`** | fixed-arg exec, timeout, kill-tree | PSF | stdlib | already wrapped by `run_subprocess` (incl. Windows `taskkill /T`) | the trusted seam | 🟢 **DIRECT REUSE** |
| `ffmpeg-python` | ffmpeg command building | Apache-2.0 | 2022-ish, largely dormant | would *replace* our fixed-arg construction | **builds commands dynamically — weakens the fixed-argument guarantee** | 🔴 **REJECT** |
| `tenacity` | retry/backoff | Apache-2.0 ✅ | active | duplicates `workflow/retry.py` (`RetryPolicy`, `BackoffStrategy`, failure-kind-aware) | n/a | 🔴 **REJECT** (already solved) |
| `structlog` / `loguru` | structured logging | MIT/Apache ✅ | active | duplicates typed `WorkflowEvent` + stdlib `logging` | n/a | 🔴 **REJECT** (already solved) |
| `psutil` | process/resource inspection | BSD-3 ✅ | active | `run_subprocess` already does timeout + whole-tree kill | n/a | 🔴 **REJECT** (not needed) |
| **Media validation layer** | assert MP4/H.264/1080x1920/AAC, timeline invariants | ours | ours | exact fit | controlled | 🟢 **BUILD** |
| **Failure-injection + lineage-break tests** | prove the pipeline fails loudly | ours | ours | exact fit | controlled | 🟢 **BUILD** |

## Findings

**Nothing needs to be added.** Every hardening capability is either already
installed (PyAV, `srt`, pytest, FFmpeg) or already implemented in-house:

- retry/backoff → `workflow/retry.py` (`RetryPolicy`, `BackoffStrategy`,
  and crucially *failure-kind aware*: deterministic and permanent failures
  are never retried, which generic libraries do not model).
- structured observability → typed `WorkflowEvent` / `WorkflowEventType`
  (18 event types) plus stdlib `logging`. A logging framework would add a
  dependency to restate what typed events already give us.
- process timeout / cleanup → `run_subprocess` with `stdin=DEVNULL`,
  explicit timeout, and whole-process-tree kill (`taskkill /F /T` on
  Windows, `os.killpg` on POSIX) — hardened in Phase 1 against a real
  orphaned-`java.exe` bug.

**`ffmpeg-python` is rejected on security grounds, not merely redundancy.**
Its value proposition is *dynamic* command construction. Our entire FFmpeg
trust model rests on the opposite property: fixed, literal argument lists
that no AI- or trace-derived string can restructure. Adopting it would
trade away the guarantee that Phase 4.1/4.4 security tests exist to protect.

**The one genuine gap is media *validation*.** We probe media
(`media/probe.py`) but nothing asserts a policy over the result — that the
output really is H.264 1080×1920 with a sane frame count, that audio does
not exceed the visual timeline, that subtitle cues stay inside the video.
That is Code2Shorts-specific policy; no library encodes it. **BUILD.**

## Dependency outcome

```
New dependencies: 0
Removed dependencies: 0
```

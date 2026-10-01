"""Wiring: which concrete renderer, TTS engine and LLM provider the web
app hands to the EXISTING pipeline nodes.

This is the one place the web layer touches pipeline components, and it
touches only their constructors. It chooses implementations; it does not
implement anything. Every class named here already existed and is used
identically by the golden-path scripts.

The choices themselves come from `Settings` and from the request's
teaching configuration, never from a literal here - so switching voice or
provider is configuration, exactly as it was before a UI existed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from code2shorts.ai.providers import build_llm_provider
from code2shorts.config import Settings
from code2shorts.core.video_profile import VideoProfile
from code2shorts.generation.manager import GenerationRequest, PipelineFactory
from code2shorts.media import MediaComposer, MediaPolicy
from code2shorts.narration import (
    KokoroTTSProvider,
    SapiTTSProvider,
    SyntheticTTSProvider,
    TTSProvider,
)
from code2shorts.visualization import ManimVideoRenderer
from code2shorts.visualization.layout import layout_for
from code2shorts.workflow import (
    ComposeMediaNode,
    CompileNode,
    EducationalPlanNode,
    ExplainNode,
    FinalValidationNode,
    NarrationNode,
    NarrationTimingNode,
    RenderVideoNode,
    TraceNode,
    VisualizationPlanNode,
)

logger = logging.getLogger(__name__)


def build_tts(config, settings: Settings) -> TTSProvider:
    """Kokoro when it is really available, SAPI otherwise, synthetic last.

    The same order the Phase 6.2 golden path uses. Availability is asked,
    never assumed: a missing model must degrade to a working voice rather
    than fail the run, and the provider that actually spoke is recorded in
    the version's fingerprint.
    """
    requested = (config.tts_provider or "kokoro").strip().lower()
    if requested == "kokoro":
        kokoro = KokoroTTSProvider(voice=config.voice or settings.kokoro_voice)
        if kokoro.is_available():
            return kokoro
        logger.warning("kokoro unavailable, falling back to SAPI")
    if SapiTTSProvider.is_available():
        return SapiTTSProvider()
    return SyntheticTTSProvider()


def build_renderer(profile: VideoProfile) -> ManimVideoRenderer:
    """The renderer for one output format.

    The profile reaches Manim as its pixel size and as the layout its
    orientation selects (`visualization.layout.layout_for`). The scene
    derives its frame from the layout, and FFmpeg copies the rendered video
    stream, so this is the one place a format enters the render path.
    """
    return ManimVideoRenderer(
        fps=30,
        resolution=profile.resolution,
        timeout_seconds=900.0,
        layout=layout_for(profile),
    )


class DefaultPipelineFactory(PipelineFactory):
    """The real pipeline, with real Java, a real LLM provider, real TTS,
    real Manim and real FFmpeg.

    Phase 8.1: the stages after narration are the golden path's, not the
    Phase 4 shortcut. Speech is synthesised per segment and the visual
    timeline fitted to it BEFORE rendering (`NarrationTimingNode`), and the
    composed file is validated against the profile's pixel size and the
    alignment (`ComposeMediaNode`). The same TTS engine serves both, so the
    voice that was measured is the voice that is heard."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def build(self, request: GenerationRequest, output_dir: Path) -> list:
        settings = self._settings
        provider = build_llm_provider(settings)
        provider_name = (settings.llm_provider or "mock").strip().lower()
        model = getattr(provider, "_model", None) or settings.llm_model

        profile = request.config.video_profile
        tts = build_tts(request.config, settings)

        return [
            CompileNode(),
            TraceNode(),
            ExplainNode(provider, provider_name=provider_name, model=model),
            EducationalPlanNode(provider, provider_name=provider_name, model=model),
            VisualizationPlanNode(provider, provider_name=provider_name, model=model),
            NarrationNode(provider, provider_name=provider_name, model=model),
            NarrationTimingNode(tts_provider=tts, output_dir=output_dir),
            RenderVideoNode(build_renderer(profile), output_dir=output_dir),
            ComposeMediaNode(
                tts_provider=tts,
                composer=MediaComposer(),
                output_dir=output_dir,
                media_policy=MediaPolicy(
                    width=profile.pixel_width, height=profile.pixel_height
                ),
            ),
            FinalValidationNode(),
        ]

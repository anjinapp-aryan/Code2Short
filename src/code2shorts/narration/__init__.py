from code2shorts.narration.tts import (
    MockTTSProvider,
    SapiTTSProvider,
    SyntheticTTSProvider,
    TTSFailure,
    TTSProvider,
    TTSResult,
)
from code2shorts.narration.alignment import (
    AlignedSegment,
    AlignmentError,
    AlignmentResult,
    align_narration,
    validate_alignment,
    validate_each_moment_is_narrated_once,
    validate_narration_describes_its_moment,
    validate_teaching_synchronization,
)
from code2shorts.narration.fitting import (
    FitAdjustment,
    FitResult,
    fit_plan_to_narration,
)
from code2shorts.narration.subtitles import (
    build_srt,
    sanitize_subtitle_text,
    validate_srt,
    write_srt,
)
from code2shorts.narration.validation import validate_narration

__all__ = [
    "FitAdjustment",
    "FitResult",
    "fit_plan_to_narration",
    "AlignedSegment",
    "AlignmentError",
    "AlignmentResult",
    "MockTTSProvider",
    "SapiTTSProvider",
    "SyntheticTTSProvider",
    "TTSFailure",
    "TTSProvider",
    "TTSResult",
    "align_narration",
    "build_srt",
    "sanitize_subtitle_text",
    "validate_alignment",
    "validate_each_moment_is_narrated_once",
    "validate_narration_describes_its_moment",
    "validate_teaching_synchronization",
    "validate_narration",
    "validate_srt",
    "write_srt",
]

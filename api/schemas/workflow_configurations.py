from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_MAX_CALL_DURATION_SECONDS = 300
# Hard ceiling on configurable call duration. Must stay <= the concurrency
# rate limiter's stale_call_timeout (20 min): a call running past that has
# its slot purged as stale and the org concurrency limit under-counts.
MAX_CALL_DURATION_SECONDS = 1200
DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_SMART_TURN_STOP_SECS = 1.5
DEFAULT_TURN_START_STRATEGY = "default"
DEFAULT_TURN_START_MIN_WORDS = 3
# Minimum words required before triggering a user turn on the default
# (non-explicit) strategy for telephony pipelines. A gate of 3 words:
#   - blocks 1–2-word STT hallucinations from phone line noise and filler
#     sounds ("ആ ഉം", "আচ্ছা হ্যাঁ") while the bot is speaking
#   - still fires on real answers ("Jubilee Hills location", "apartment please")
#   - automatically reduces to 1-word when the bot is NOT speaking
#     (MinWordsUserTurnStartStrategy built-in behaviour)
DEFAULT_TURN_START_MIN_WORDS_TELEPHONY = 3
DEFAULT_PROVISIONAL_VAD_PAUSE_SECS = 1.2
DEFAULT_TURN_STOP_STRATEGY = "transcription"
DEFAULT_CONTEXT_COMPACTION_ENABLED = False
# Silence duration (seconds) the pipeline waits after the last speech
# frame before closing a user turn. VAD stop_secs already covers
# inter-syllable pauses at the signal level; this is only the
# application-level floor on top. 0.2 s keeps turn-end snappy without
# the 0.5–0.8 s dead air that made replies feel 1–2 s late.
DEFAULT_SPEECH_TIMEOUT_SECS = 0.2

# Silero VAD — matches CONVERSATIONAL_VAD_PARAMS. Changing these defaults
# changes first-call behavior for every agent that has not overridden them.
DEFAULT_VAD_CONFIDENCE = 0.7
DEFAULT_VAD_MIN_VOLUME = 0.6
DEFAULT_VAD_START_SECS = 0.15
DEFAULT_VAD_STOP_SECS = 0.25
DEFAULT_REALTIME_PREFIX_PADDING_MS = 100
DEFAULT_REALTIME_SILENCE_DURATION_MS = 300

DEFAULT_SEMANTIC_EOT_COMPLETE_TIMEOUT_SECS = 0.3
DEFAULT_SEMANTIC_EOT_INCOMPLETE_TIMEOUT_SECS = 1.2

DEFAULT_STT_ENDPOINTING_MS = 300
DEFAULT_FLUX_EOT_TIMEOUT_MS = 800
DEFAULT_FLUX_EOT_THRESHOLD = 0.7
DEFAULT_FLUX_EAGER_EOT_THRESHOLD = 0.5

DEFAULT_TOOL_FILLER_DELAY_MS = 600
DEFAULT_TOOL_FILLER_PHRASES: tuple[str, ...] = (
    "Let me check that.",
    "One second.",
    "Got it, looking that up.",
)

DEFAULT_BARGE_IN_IGNORE_PHRASES: tuple[str, ...] = (
    "yeah",
    "yep",
    "yup",
    "uh-huh",
    "uh huh",
    "okay",
    "ok",
    "mhm",
    "mm-hmm",
    "right",
    "sure",
    "aha",
)


class ExternalPBXFieldMapping(BaseModel):
    """Map one gathered-context value to a provider-native field."""

    context_path: str = Field(min_length=1, max_length=255)
    destination_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

    @field_validator("context_path", mode="before")
    @classmethod
    def strip_context_path(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("destination_field", mode="before")
    @classmethod
    def strip_destination_field(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AmbientNoiseConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    volume: float = 0.3


class VadConfigurationDefaults(BaseModel):
    """Per-agent Silero VAD and Gemini Live server-VAD knobs."""

    model_config = ConfigDict(extra="allow")

    confidence: float = Field(default=DEFAULT_VAD_CONFIDENCE, ge=0.1, le=1.0)
    min_volume: float = Field(default=DEFAULT_VAD_MIN_VOLUME, ge=0.0, le=1.0)
    start_secs: float = Field(default=DEFAULT_VAD_START_SECS, ge=0.05, le=2.0)
    stop_secs: float = Field(default=DEFAULT_VAD_STOP_SECS, ge=0.05, le=2.0)
    realtime_prefix_padding_ms: int = Field(
        default=DEFAULT_REALTIME_PREFIX_PADDING_MS, ge=0, le=1000
    )
    realtime_silence_duration_ms: int = Field(
        default=DEFAULT_REALTIME_SILENCE_DURATION_MS, ge=50, le=2000
    )


class SemanticEotConfigurationDefaults(BaseModel):
    """Timeouts used when turn_stop_strategy is semantic_eot."""

    model_config = ConfigDict(extra="allow")

    complete_timeout_secs: float = Field(
        default=DEFAULT_SEMANTIC_EOT_COMPLETE_TIMEOUT_SECS, ge=0.1, le=2.0
    )
    incomplete_timeout_secs: float = Field(
        default=DEFAULT_SEMANTIC_EOT_INCOMPLETE_TIMEOUT_SECS, ge=0.3, le=3.0
    )


class SttTurnConfigurationDefaults(BaseModel):
    """STT-level end-of-turn knobs. Independent of Silero VAD."""

    model_config = ConfigDict(extra="allow")

    endpointing_ms: int = Field(default=DEFAULT_STT_ENDPOINTING_MS, ge=50, le=2000)
    flux_eot_timeout_ms: int = Field(
        default=DEFAULT_FLUX_EOT_TIMEOUT_MS, ge=100, le=3000
    )
    flux_eot_threshold: float = Field(
        default=DEFAULT_FLUX_EOT_THRESHOLD, ge=0.1, le=1.0
    )
    flux_eager_eot_threshold: float = Field(
        default=DEFAULT_FLUX_EAGER_EOT_THRESHOLD, ge=0.1, le=1.0
    )


class BargeInFilterConfigurationDefaults(BaseModel):
    """Drop short backchannels so they do not interrupt the agent."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    ignore_phrases: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BARGE_IN_IGNORE_PHRASES),
        max_length=50,
    )

    @field_validator("ignore_phrases")
    @classmethod
    def _normalize_ignore_phrases(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for phrase in value:
            normalized = " ".join(str(phrase).strip().lower().split())
            if not normalized or normalized in seen:
                continue
            if len(normalized) > 40:
                raise ValueError("ignore phrases must be 40 characters or fewer")
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned


class ToolFillerConfigurationDefaults(BaseModel):
    """Speak a short filler if a tool call is still running after delay_ms."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    delay_ms: int = Field(default=DEFAULT_TOOL_FILLER_DELAY_MS, ge=100, le=5000)
    phrases: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TOOL_FILLER_PHRASES),
        max_length=20,
    )

    @field_validator("phrases")
    @classmethod
    def _normalize_phrases(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for phrase in value:
            text = " ".join(str(phrase).split()).strip()
            if not text:
                continue
            if len(text) > 200:
                raise ValueError("filler phrases must be 200 characters or fewer")
            cleaned.append(text)
        return cleaned or list(DEFAULT_TOOL_FILLER_PHRASES)


class WorkflowConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _treat_null_as_unset(cls, data):
        # Stored configs (and older clients) carry explicit JSON nulls for
        # keys the user never configured; dropping them lets the field
        # defaults apply instead of failing validation.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    ambient_noise_configuration: AmbientNoiseConfigurationDefaults = Field(
        default_factory=AmbientNoiseConfigurationDefaults
    )
    max_call_duration: int = Field(
        default=DEFAULT_MAX_CALL_DURATION_SECONDS,
        gt=0,
        le=MAX_CALL_DURATION_SECONDS,
    )
    max_user_idle_timeout: float = DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS
    smart_turn_stop_secs: float = DEFAULT_SMART_TURN_STOP_SECS
    turn_start_strategy: Literal["default", "min_words", "provisional_vad"] = (
        DEFAULT_TURN_START_STRATEGY
    )
    turn_start_min_words: int = DEFAULT_TURN_START_MIN_WORDS
    provisional_vad_pause_secs: float = DEFAULT_PROVISIONAL_VAD_PAUSE_SECS
    turn_stop_strategy: Literal["transcription", "turn_analyzer", "semantic_eot"] = (
        DEFAULT_TURN_STOP_STRATEGY
    )
    speech_timeout_secs: float = Field(
        default=DEFAULT_SPEECH_TIMEOUT_SECS, ge=0.05, le=2.0
    )
    user_turn_stop_timeout: float | None = Field(default=None, ge=0.5, le=30.0)
    vad_configuration: VadConfigurationDefaults = Field(
        default_factory=VadConfigurationDefaults
    )
    semantic_eot_configuration: SemanticEotConfigurationDefaults = Field(
        default_factory=SemanticEotConfigurationDefaults
    )
    stt_turn_configuration: SttTurnConfigurationDefaults = Field(
        default_factory=SttTurnConfigurationDefaults
    )
    barge_in_filter: BargeInFilterConfigurationDefaults = Field(
        default_factory=BargeInFilterConfigurationDefaults
    )
    tool_filler_configuration: ToolFillerConfigurationDefaults = Field(
        default_factory=ToolFillerConfigurationDefaults
    )
    dictionary: str = ""
    context_compaction_enabled: bool = DEFAULT_CONTEXT_COMPACTION_ENABLED
    external_pbx_field_mappings: list[ExternalPBXFieldMapping] = Field(
        default_factory=list,
        max_length=100,
    )


def get_default_workflow_configurations() -> WorkflowConfigurationDefaults:
    return WorkflowConfigurationDefaults()

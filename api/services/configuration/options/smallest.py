SMALLEST_TTS_MODELS = ("lightning_v3.1", "lightning_v3.1_pro")

# Voices are fetched live from the Smallest AI API at
# GET https://api.smallest.ai/waves/v1/{model}/get_voices
# No hardcoding needed — these empty tuples are kept for schema compatibility.
SMALLEST_TTS_VOICES: tuple = ()
SMALLEST_TTS_PRO_VOICES: tuple = ()

# Full list of ISO 639-1 codes supported by Smallest AI Waves TTS.
# "te" (Telugu) is confirmed supported: voices sridhar, srikanth, rajan, shreyas, etc.
SMALLEST_TTS_LANGUAGES = (
    "en",   # English
    "hi",   # Hindi
    "te",   # Telugu   ← confirmed supported via live API
    "ta",   # Tamil
    "kn",   # Kannada
    "ml",   # Malayalam
    "mr",   # Marathi
    "gu",   # Gujarati
    "bn",   # Bengali
    "pa",   # Punjabi
    "or",   # Odia
    "fr",   # French
    "de",   # German
    "es",   # Spanish
    "it",   # Italian
    "nl",   # Dutch
    "sv",   # Swedish
    "pt",   # Portuguese
    "pl",   # Polish
    "ru",   # Russian
    "ar",   # Arabic
    "he",   # Hebrew
)

# Map from Smallest AI full language names (as returned by the get_voices API)
# to ISO 639-1 codes used in our settings form.
SMALLEST_LANGUAGE_NAME_TO_ISO: dict[str, str] = {
    "english": "en",
    "hindi": "hi",
    "telugu": "te",
    "tamil": "ta",
    "kannada": "kn",
    "malayalam": "ml",
    "marathi": "mr",
    "gujarati": "gu",
    "bengali": "bn",
    "punjabi": "pa",
    "odia": "or",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "italian": "it",
    "dutch": "nl",
    "swedish": "sv",
    "portuguese": "pt",
    "polish": "pl",
    "russian": "ru",
    "arabic": "ar",
    "hebrew": "he",
}


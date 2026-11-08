"""Import-only stand-in for nemo_text_processing.

SILMA imports NeMo's text normalizer at module level, but NeMo needs pynini, which has no Windows build.
The worker turns normalization off (enable_normalizer=False, normalize_numbers=False), so this is never called.
"""

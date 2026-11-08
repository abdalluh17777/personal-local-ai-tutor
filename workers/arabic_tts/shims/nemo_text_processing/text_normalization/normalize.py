class Normalizer:
    """Never used: the worker disables SILMA's text normalization. Fails loudly if that ever changes."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("NeMo text normalization is not available on Windows; it must stay disabled")

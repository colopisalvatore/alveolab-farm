"""Tables of the ALVEOLAB modules; ``load_all_models`` calls ``load`` so create_all sees them."""


def load() -> None:
    from backend.app.alveolab.production import models  # noqa: F401

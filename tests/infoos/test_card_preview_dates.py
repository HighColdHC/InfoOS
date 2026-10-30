import pytest

from backend.api.routes.card_preview_dates import publication_bounds


@pytest.mark.parametrize("since,until", [
    ("not-a-date", None), (None, "2026-02-30"),
    (None, "2026-09-16T12:00:00"), (None, ""),
    ("2026-09-17", "2026-09-16"),
])
def test_publication_bounds(since, until):
    with pytest.raises(ValueError):
        publication_bounds(since, until)

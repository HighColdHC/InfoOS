"""Empty cursors mean the first page; malformed nonempty cursors stay invalid."""
import pytest

from backend.read_models.new_cards import list_new_cards
from backend.read_models.opportunities import list_opportunities, list_opportunity_candidates
from backend.read_models.research_runs import decode_research_cursor
from backend.read_models.verifications import list_verifications


@pytest.mark.parametrize("read,arguments", [
    (list_new_cards, {"since": "2026-10-17T00:00:00Z"}),
    (list_opportunity_candidates, {"since": "2026-10-17T00:00:00Z"}),
    (list_opportunities, {}), (list_verifications, {}),
])
def test_empty_cursor_equals_omitted(tmp_path, read, arguments):
    assert read(tmp_path, **arguments, cursor="") == read(tmp_path, **arguments)
    with pytest.raises(ValueError, match="invalid_cursor"):
        read(tmp_path, **arguments, cursor="bad")


def test_research_empty_cursor_equals_omitted():
    assert decode_research_cursor("") == decode_research_cursor(None) == (None, None)
    with pytest.raises(ValueError, match="invalid_cursor"):
        decode_research_cursor("bad")

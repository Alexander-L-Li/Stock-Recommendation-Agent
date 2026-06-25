def test_watchlist_add_list_remove(store):
    assert store.list_watchlist() == []

    store.add_to_watchlist("aapl")
    store.add_to_watchlist("MSFT", note="core holding")
    store.add_to_watchlist("nvda")

    assert store.list_watchlist() == ["AAPL", "MSFT", "NVDA"]

    store.remove_from_watchlist("msft")
    assert store.list_watchlist() == ["AAPL", "NVDA"]


def test_watchlist_add_is_idempotent(store):
    store.add_to_watchlist("AAPL")
    store.add_to_watchlist("AAPL")
    assert store.list_watchlist() == ["AAPL"]


def test_watchlist_rejects_empty(store):
    import pytest
    with pytest.raises(ValueError):
        store.add_to_watchlist("   ")

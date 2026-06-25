"""Tests for the Reddit collector using lightweight fakes (no network)."""
import time

from stock_agent.config import Config
from stock_agent.collectors.reddit_collector import RedditCollector


class FakeComment:
    def __init__(self, id, body, score, created_utc):
        self.id = id
        self.body = body
        self.score = score
        self.created_utc = created_utc


class FakeComments(list):
    def replace_more(self, limit=0):
        return []


class FakeSubmission:
    def __init__(self, id, title, selftext, score, created_utc, comments=None):
        self.id = id
        self.title = title
        self.selftext = selftext
        self.score = score
        self.created_utc = created_utc
        self.url = f"https://reddit.com/{id}"
        self.comments = FakeComments(comments or [])


class FakeSubreddit:
    def __init__(self, submissions):
        self._submissions = submissions

    def hot(self, limit=50):
        return self._submissions[:limit]


class FakeReddit:
    def __init__(self, mapping):
        self._mapping = mapping

    def subreddit(self, name):
        return FakeSubreddit(self._mapping.get(name, []))


def _config():
    return Config(subreddits=["stocks"], lookback_hours=24,
                  reddit_post_limit=50, reddit_comment_limit=5)


def test_collect_parses_submissions_and_comments():
    now = 1_000_000.0
    recent = now - 3600  # 1h ago
    sub = FakeSubmission(
        "p1", "AAPL looks strong", "Great fundamentals", 120, recent,
        comments=[FakeComment("c1", "I agree, buying NVDA too", 30, recent)],
    )
    reddit = FakeReddit({"stocks": [sub]})
    collector = RedditCollector(_config(), reddit_client=reddit)

    posts = collector.collect(now=now)

    assert len(posts) == 2
    submission = next(p for p in posts if p.kind == "submission")
    comment = next(p for p in posts if p.kind == "comment")
    assert submission.title == "AAPL looks strong"
    assert "Great fundamentals" in submission.text
    assert comment.body == "I agree, buying NVDA too"
    assert comment.subreddit == "stocks"


def test_collect_filters_old_posts():
    now = 1_000_000.0
    old = now - 48 * 3600  # 2 days ago, outside 24h window
    recent = now - 3600
    reddit = FakeReddit({"stocks": [
        FakeSubmission("old", "old post", "", 5, old),
        FakeSubmission("new", "new post", "", 5, recent),
    ]})
    collector = RedditCollector(_config(), reddit_client=reddit)

    posts = collector.collect(now=now)

    ids = {p.id for p in posts}
    assert "new" in ids
    assert "old" not in ids


def test_collect_filters_old_comments():
    now = 1_000_000.0
    recent = now - 3600
    old = now - 48 * 3600
    sub = FakeSubmission("p1", "title", "", 10, recent, comments=[
        FakeComment("c_new", "fresh", 5, recent),
        FakeComment("c_old", "stale", 5, old),
    ])
    collector = RedditCollector(_config(), reddit_client=FakeReddit({"stocks": [sub]}))

    posts = collector.collect(now=now)
    comment_ids = {p.id for p in posts if p.kind == "comment"}
    assert comment_ids == {"c_new"}


def test_one_bad_subreddit_does_not_abort():
    now = 1_000_000.0
    recent = now - 3600

    class PartlyBrokenReddit:
        def subreddit(self, name):
            if name == "broken":
                raise RuntimeError("403 forbidden")
            return FakeSubreddit([FakeSubmission("ok", "AAPL", "", 1, recent)])

    cfg = Config(subreddits=["broken", "stocks"], lookback_hours=24)
    collector = RedditCollector(cfg, reddit_client=PartlyBrokenReddit())
    posts = collector.collect(now=now)
    assert any(p.title == "AAPL" for p in posts)

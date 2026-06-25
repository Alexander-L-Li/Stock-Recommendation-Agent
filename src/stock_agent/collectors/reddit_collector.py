"""Reddit collector.

Pulls recent hot/top submissions and their top comments from the configured
subreddits using praw (the official Reddit API client). Records older than the
configured lookback window are filtered out.

The praw client is injected (``reddit_client``) so tests can pass a fake without
network access or credentials.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..config import Config
from ..models import RedditPost

logger = logging.getLogger(__name__)


def build_reddit_client(config: Config) -> Any:
    """Construct a read-only praw Reddit client from config. Imported lazily so
    the module loads without praw installed (e.g. for unit tests)."""
    import praw

    if not config.reddit_client_id or not config.reddit_client_secret:
        raise ValueError(
            "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET must be set to use Reddit"
        )
    return praw.Reddit(
        client_id=config.reddit_client_id,
        client_secret=config.reddit_client_secret,
        user_agent=config.reddit_user_agent,
        check_for_async=False,
    )


class RedditCollector:
    def __init__(self, config: Config, reddit_client: Optional[Any] = None) -> None:
        self.config = config
        self._client = reddit_client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_reddit_client(self.config)
        return self._client

    def collect(self, now: Optional[float] = None) -> list[RedditPost]:
        """Return submissions + top comments newer than the lookback window."""
        now = now if now is not None else time.time()
        cutoff = now - self.config.lookback_hours * 3600
        posts: list[RedditPost] = []

        for sub_name in self.config.subreddits:
            try:
                posts.extend(self._collect_subreddit(sub_name, cutoff))
            except Exception as exc:  # one bad subreddit shouldn't kill the run
                logger.warning("Failed to collect r/%s: %s", sub_name, exc)
        return posts

    def _collect_subreddit(self, sub_name: str, cutoff: float) -> list[RedditPost]:
        out: list[RedditPost] = []
        subreddit = self.client.subreddit(sub_name)
        for submission in subreddit.hot(limit=self.config.reddit_post_limit):
            created = float(getattr(submission, "created_utc", 0) or 0)
            if created < cutoff:
                continue
            out.append(
                RedditPost(
                    id=str(getattr(submission, "id", "")),
                    subreddit=sub_name,
                    title=getattr(submission, "title", "") or "",
                    body=getattr(submission, "selftext", "") or "",
                    score=int(getattr(submission, "score", 0) or 0),
                    created_utc=created,
                    url=getattr(submission, "url", "") or "",
                    kind="submission",
                )
            )
            out.extend(self._collect_comments(submission, sub_name, cutoff))
        return out

    def _collect_comments(self, submission: Any, sub_name: str,
                          cutoff: float) -> list[RedditPost]:
        out: list[RedditPost] = []
        comments = getattr(submission, "comments", None)
        if comments is None:
            return out
        # Avoid expensive "load more comments" expansion on large threads.
        replace_more = getattr(comments, "replace_more", None)
        if callable(replace_more):
            try:
                replace_more(limit=0)
            except Exception:
                pass
        try:
            comment_list = list(comments)[: self.config.reddit_comment_limit]
        except TypeError:
            comment_list = []
        for comment in comment_list:
            created = float(getattr(comment, "created_utc", 0) or 0)
            if created < cutoff:
                continue
            out.append(
                RedditPost(
                    id=str(getattr(comment, "id", "")),
                    subreddit=sub_name,
                    title="",
                    body=getattr(comment, "body", "") or "",
                    score=int(getattr(comment, "score", 0) or 0),
                    created_utc=created,
                    kind="comment",
                )
            )
        return out

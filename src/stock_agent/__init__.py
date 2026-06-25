"""Daily long-term stock recommendation agent.

Discovers candidate stocks from Reddit and business news, scores them with a
fundamentals-dominant (70/30) model gated against hype, and emails a ranked,
explainable report. Runs once daily on AWS Lambda + EventBridge. Free-tier only.

This produces signals and reasoning to aid your own research, not automated
financial advice.
"""

__version__ = "0.1.0"

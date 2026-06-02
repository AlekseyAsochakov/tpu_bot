from prometheus_client import Counter

# Prometheus Metrics
USER_INTERACTION_COUNT = Counter(
    "bot_user_interactions_total",
    "Total number of user interactions",
    ["command"]
)

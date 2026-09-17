"""``nova-scheduler`` — the singleton that decides which task graphs are due.

It computes cron/interval next-fire times, persists a ``CONFIG_TASK_GRAPH_RUNS``
row to ``NOVA_SYSTEM``, then pushes the job to a Redis Stream for the workers.
It never executes SQL against StarRocks.
"""

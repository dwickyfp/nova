-- events table with the event_data JSON document the JSON array examples describe (tj is created by the page).
CREATE TABLE events (
    id INT NOT NULL,
    event_data JSON NULL
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO events VALUES (1, parse_json('{"user": "Alice", "actions": [{"type": "click", "timestamp": "2024-03-17T10:00:00Z", "quantity": 1}, {"type": "view", "timestamp": "2024-03-17T10:05:00Z", "quantity": 2}, {"type": "purchase", "timestamp": "2024-03-17T10:10:00Z", "quantity": 3}]}'));

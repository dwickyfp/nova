#!/bin/sh
# Nova StarRocks Init Script
# Mounted as /docker-entrypoint-initdb.d/init-nova.sql

set -eu

echo "Waiting for StarRocks FE to be ready..."
until mysql -h starrocks-fe -P 9030 -u root -e "SELECT 1" 2>/dev/null; do
  sleep 2
done

echo "Waiting for BE to be alive..."
until mysql -h starrocks-fe -P 9030 -u root -e "SHOW BACKENDS" 2>/dev/null | grep -q "true"; do
  sleep 2
done

echo "StarRocks ready! Running init script..."
mysql -h starrocks-fe -P 9030 -u root < /docker-entrypoint-initdb.d/init-nova.sql

echo ""
echo "=========================================="
echo "  Nova Engine Init Complete!"
echo "=========================================="
echo "  FE:       http://localhost:8030"
echo "  BE:       http://localhost:8040"
echo "  MySQL:    mysql -h localhost -P 9030 -u nova_admin -p"
echo "=========================================="

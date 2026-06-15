#!/bin/bash
# Run before every migration. Takes 2 seconds. Saves hours.
TIMESTAMP=$(date +%Y%m%d_%H%M)
cp artemis.db artemis_backup_${TIMESTAMP}.db
echo "Backup created: artemis_backup_${TIMESTAMP}.db"

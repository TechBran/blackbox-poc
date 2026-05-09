#!/bin/bash
# Cleanup script that runs when the service crashes
# This helps maintain system health for unattended operation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_TAG="blackbox-cleanup"

# Log the cleanup start
echo "[$(date)] Starting post-crash cleanup" | systemd-cat -t $LOG_TAG -p info

# 1. Check and clean up disk space if needed
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
if [ $DISK_USAGE -gt 85 ]; then
    echo "[$(date)] High disk usage detected: ${DISK_USAGE}%" | systemd-cat -t $LOG_TAG -p warning

    # Emergency archive cleanup - more aggressive
    if [ -d "$SCRIPT_DIR/Archive" ]; then
        OLD_SIZE=$(du -sh "$SCRIPT_DIR/Archive" 2>/dev/null | cut -f1)

        # Delete archives older than 7 days in emergency
        find "$SCRIPT_DIR/Archive" -name "Volume_*.txt" -type f -mtime +7 -delete 2>/dev/null

        # If still over 85%, delete oldest files until we're under 80%
        while [ $DISK_USAGE -gt 80 ]; do
            OLDEST=$(find "$SCRIPT_DIR/Archive" -name "Volume_*.txt" -type f -printf '%T+ %p\n' 2>/dev/null | sort | head -1 | cut -d' ' -f2-)
            if [ -n "$OLDEST" ]; then
                rm -f "$OLDEST"
                echo "[$(date)] Deleted old archive: $OLDEST" | systemd-cat -t $LOG_TAG -p info
            else
                break
            fi
            DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
        done

        NEW_SIZE=$(du -sh "$SCRIPT_DIR/Archive" 2>/dev/null | cut -f1)
        echo "[$(date)] Archive cleanup: $OLD_SIZE -> $NEW_SIZE" | systemd-cat -t $LOG_TAG -p info
    fi
fi

# 2. Clean up old upload files (older than 30 days)
if [ -d "$SCRIPT_DIR/Portal/uploads" ]; then
    find "$SCRIPT_DIR/Portal/uploads" -type f -mtime +30 -delete 2>/dev/null
    echo "[$(date)] Cleaned old upload files" | systemd-cat -t $LOG_TAG -p info
fi

# 3. Check and repair SQLite database if needed
DB_PATH="$SCRIPT_DIR/Portal/tasks.db"
if [ -f "$DB_PATH" ]; then
    # Check database integrity
    INTEGRITY_CHECK=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('$DB_PATH')
    cursor = conn.cursor()
    cursor.execute('PRAGMA integrity_check')
    result = cursor.fetchone()[0]
    conn.close()
    print(result)
except Exception as e:
    print(f'ERROR: {e}')
" 2>/dev/null)

    if [ "$INTEGRITY_CHECK" != "ok" ]; then
        echo "[$(date)] Database integrity issue detected: $INTEGRITY_CHECK" | systemd-cat -t $LOG_TAG -p error

        # Backup corrupted database
        cp "$DB_PATH" "${DB_PATH}.corrupted.$(date +%Y%m%d_%H%M%S)"

        # Try to recover
        python3 -c "
import sqlite3
try:
    # Try to recover what we can
    conn = sqlite3.connect('$DB_PATH')
    conn.execute('VACUUM')
    conn.close()
    print('Database vacuumed')
except:
    # If vacuum fails, recreate database
    import os
    os.remove('$DB_PATH')
    print('Database recreated')
" 2>/dev/null | systemd-cat -t $LOG_TAG -p info
    fi
fi

# 4. Clear Python cache
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR" -type f -name "*.pyc" -delete 2>/dev/null

# 5. Check memory usage and clear caches if high
MEM_USAGE=$(free | grep Mem | awk '{print int($3/$2 * 100)}')
if [ $MEM_USAGE -gt 90 ]; then
    echo "[$(date)] High memory usage: ${MEM_USAGE}%, clearing caches" | systemd-cat -t $LOG_TAG -p warning
    sync
    echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
fi

# 6. Log system status for debugging
echo "[$(date)] System status after cleanup:" | systemd-cat -t $LOG_TAG -p info
df -h / | systemd-cat -t $LOG_TAG -p info
free -h | systemd-cat -t $LOG_TAG -p info

echo "[$(date)] Post-crash cleanup completed" | systemd-cat -t $LOG_TAG -p info
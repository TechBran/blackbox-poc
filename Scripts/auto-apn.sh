#!/bin/bash
# Auto-detect APN from SIM operator for the 5G-Internet connection.
#
# This script works in TWO modes:
# 1. NM dispatcher (pre-up): Runs before connection activation
# 2. Standalone: Can be called directly or from udev/systemd to fix APN after SIM swap
#
# Install dispatcher:
#   sudo cp auto-apn.sh /etc/NetworkManager/dispatcher.d/pre-up.d/10-auto-apn
#   sudo chmod 755 /etc/NetworkManager/dispatcher.d/pre-up.d/10-auto-apn
#
# Install systemd watcher (runs on modem plug/SIM swap):
#   sudo cp auto-apn-modem.service /etc/systemd/system/
#   sudo cp auto-apn-modem.path /etc/systemd/system/
#   sudo systemctl enable --now auto-apn-modem.path

CON_NAME="5G-Internet"
PROVIDER_DB="/usr/share/mobile-broadband-provider-info/serviceproviders.xml"

# When called as NM dispatcher, only run for our connection
if [ -n "$CONNECTION_ID" ] && [ "$CONNECTION_ID" != "$CON_NAME" ]; then
    exit 0
fi

logger -t auto-apn "Triggered (CONNECTION_ID=${CONNECTION_ID:-standalone} args=$*)"

# Wait for ModemManager to detect modem and read SIM
OPERATOR_ID=""
for i in $(seq 1 30); do
    MODEM_PATH=$(mmcli -L 2>/dev/null | grep -oP '/org/freedesktop/ModemManager1/Modem/\K\d+' | head -1)
    if [ -n "$MODEM_PATH" ]; then
        # Try modem's registered operator first (available sooner than SIM read)
        OPERATOR_ID=$(mmcli -m "$MODEM_PATH" 2>/dev/null | grep "operator id" | awk '{print $NF}')
        if [ -n "$OPERATOR_ID" ] && [ "$OPERATOR_ID" != "--" ]; then
            break
        fi
        # Fallback: read from SIM card directly
        SIM_PATH=$(mmcli -m "$MODEM_PATH" 2>/dev/null | grep "primary sim path" | grep -oP 'SIM/\K\d+')
        if [ -n "$SIM_PATH" ]; then
            OPERATOR_ID=$(mmcli -i "$SIM_PATH" 2>/dev/null | grep "operator id" | awk '{print $NF}')
            if [ -n "$OPERATOR_ID" ] && [ "$OPERATOR_ID" != "--" ]; then
                break
            fi
        fi
    fi
    sleep 2
done

if [ -z "$OPERATOR_ID" ] || [ "$OPERATOR_ID" = "--" ]; then
    logger -t auto-apn "ERROR: Could not read operator ID after 60s"
    exit 0
fi

MCC="${OPERATOR_ID:0:3}"
MNC="${OPERATOR_ID:3}"

logger -t auto-apn "SIM operator: MCC=$MCC MNC=$MNC"

# Look up the best data APN from the provider database
# AT&T uses usage types like "mms-internet-hipri" not just "internet",
# so we check if "internet" appears anywhere in the usage type string.
# We prefer the first matching provider (main carrier, not MVNOs).
APN=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('$PROVIDER_DB')
root = tree.getroot()
found = False
for country in root.findall('.//country'):
    if found:
        break
    for provider in country.findall('provider'):
        if found:
            break
        for net in provider.findall('.//network-id'):
            if net.get('mcc') == '$MCC' and net.get('mnc') == '$MNC':
                # Pass 1: data APN without ims/wap/mms in the name
                for apn in provider.findall('.//apn'):
                    usage = apn.find('usage')
                    utype = usage.get('type', '') if usage is not None else ''
                    if 'internet' in utype:
                        val = apn.get('value', '')
                        if 'ims' not in val.lower() and 'wap' not in val.lower() and 'mms' not in val.lower():
                            print(val)
                            found = True
                            break
                if found:
                    break
                # Pass 2: any APN with internet in usage type
                for apn in provider.findall('.//apn'):
                    usage = apn.find('usage')
                    utype = usage.get('type', '') if usage is not None else ''
                    if 'internet' in utype:
                        print(apn.get('value', ''))
                        found = True
                        break
                break
" 2>/dev/null)

if [ -z "$APN" ]; then
    logger -t auto-apn "WARNING: No APN found for MCC=$MCC MNC=$MNC, keeping current"
    exit 0
fi

# Check current APN (case-insensitive — APNs are case-insensitive on the network)
CURRENT_APN=$(nmcli -g gsm.apn connection show "$CON_NAME" 2>/dev/null)

if [ "${CURRENT_APN,,}" = "${APN,,}" ]; then
    logger -t auto-apn "APN already correct: $APN"
    # If running standalone (not dispatcher), try to bring connection up
    if [ -z "$CONNECTION_ID" ]; then
        STATE=$(nmcli -g GENERAL.STATE connection show "$CON_NAME" 2>/dev/null)
        if [ "$STATE" != "activated" ]; then
            logger -t auto-apn "Connection not active, activating..."
            nmcli connection up "$CON_NAME" 2>/dev/null && logger -t auto-apn "Connection activated" || logger -t auto-apn "Activation failed"
        fi
    fi
    exit 0
fi

logger -t auto-apn "Updating APN: $CURRENT_APN -> $APN (operator $MCC$MNC)"
nmcli connection modify "$CON_NAME" gsm.apn "$APN"

# If running standalone (not as dispatcher), activate the connection
if [ -z "$CONNECTION_ID" ]; then
    sleep 2
    logger -t auto-apn "Activating connection with new APN..."
    nmcli connection up "$CON_NAME" 2>/dev/null && logger -t auto-apn "Connection activated with APN=$APN" || logger -t auto-apn "Activation failed (may need retry)"
fi

exit 0

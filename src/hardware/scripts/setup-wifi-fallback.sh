#!/usr/bin/env bash
# Configure WiFi auto-connect priority so RPi 开机优先连指定的 SSID,
# 失败再 fallback 到 hotspot 模式(让用户能从局域网直接 SSH 进去配网络)。
#
# 用法:
#   ./setup-wifi-fallback.sh                           # 只改 priority(假设 connections 已存在)
#   ./setup-wifi-fallback.sh <SSID> <PSK>              # 先 add WiFi connection,再设 priority
#
# 假设你的 hotspot connection 叫 "Pi_Hot"(raspi-config 默认名)。

set -e

PREFERRED_SSID="${1:-JMR_STATION}"
PREFERRED_PSK="${2:-}"
HOTSPOT_NAME="Pi_Hot"

# 1) 如果传了 SSID + PSK 且 connection 不存在,先 add
if [ -n "$PREFERRED_PSK" ]; then
    if ! nmcli connection show "$PREFERRED_SSID" >/dev/null 2>&1; then
        echo ">> adding WiFi connection: $PREFERRED_SSID"
        sudo nmcli connection add type wifi ifname wlan0 con-name "$PREFERRED_SSID" \
            ssid "$PREFERRED_SSID" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PREFERRED_PSK"
    fi
fi

# 2) 提高首选 SSID 优先级
echo ">> setting $PREFERRED_SSID autoconnect-priority=100"
sudo nmcli connection modify "$PREFERRED_SSID" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100

# 3) 降低 hotspot 优先级让它只在没别的网时 fallback
if nmcli connection show "$HOTSPOT_NAME" >/dev/null 2>&1; then
    echo ">> setting $HOTSPOT_NAME autoconnect-priority=0 (fallback only)"
    sudo nmcli connection modify "$HOTSPOT_NAME" \
        connection.autoconnect yes \
        connection.autoconnect-priority 0
else
    echo "WARN: $HOTSPOT_NAME connection not found — fallback will not work."
    echo "      To enable hotspot fallback see raspi-config → System → Hotspot, or:"
    echo "      sudo nmcli device wifi hotspot ifname wlan0 con-name Pi_Hot ssid Pi_Hot password 'YOUR_AP_PSK'"
fi

# 4) 清理重复 connections(nmcli 在多次 wifi connect 时会生成 'SSID 1', 'SSID 2' 等)
echo ">> cleaning duplicate connections"
nmcli -t -f NAME connection show | awk -F: '
    /^.* [0-9]+$/ { print }
' | while read -r dup; do
    [ -n "$dup" ] && sudo nmcli connection delete "$dup" 2>/dev/null && echo "  deleted: $dup"
done

echo
echo ">> final state:"
nmcli -t -f NAME,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show | grep -vE '^lo:|^docker0:|^Wired'

echo
echo "done. on next boot:"
echo "  1. NetworkManager scans available SSIDs"
echo "  2. if $PREFERRED_SSID in range → connect (priority 100)"
echo "  3. else → fallback to $HOTSPOT_NAME hotspot (priority 0)"

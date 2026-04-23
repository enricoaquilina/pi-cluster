#!/bin/bash
set -euo pipefail
# Lower Pi 5 fan trip points for always-on kiosk workload
# Default: 50/60/67.5/75°C → Custom: 45/50/57/65°C
ZONE=/sys/class/thermal/thermal_zone0
echo 45000 > "$ZONE/trip_point_1_temp"
echo 50000 > "$ZONE/trip_point_2_temp"
echo 57000 > "$ZONE/trip_point_3_temp"
echo 65000 > "$ZONE/trip_point_4_temp"

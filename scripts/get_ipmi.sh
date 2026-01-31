#!/bin/bash
docker exec fan-manager /bin/sh -c "ipmitool -I lanplus -H 10.20.20.3 -U root -P 'T2!y3wagler' sdr list > /tmp/verbose_sensors.txt && head -n 50 /tmp/verbose_sensors.txt"

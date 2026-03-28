#!/bin/sh

MODPROBE_ARGS=""
if [ $# -eq 1 ]; then
  if [ $1 = "-r" ]; then
    MODPROBE_ARGS="-r"
  fi
fi

modprobe $MODPROBE_ARGS g_cobb.ko


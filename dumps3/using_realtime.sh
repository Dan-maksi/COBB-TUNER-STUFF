#!/bin/sh
# Call this script to fetch current realtime support for your platform


if [ $AP_MANUF == "SUB" ]; then
  . ./subaru_funcs.sh
else
  . ./sh_funcs.sh
fi

if [ -e $AP_CFG_PATH/$AP_VEHICLE.cfg ];then
  get_cfg_val $AP_VEHICLE using_realtime
fi

# default case is realtime support for Subarus only!
if [ ! -n "$using_realtime" -a "$AP_MANUF" == "SUB" ]; then
  using_realtime=yes
else
  # everyone else default to no
  using_realtime=no
fi

# DIT SUB-004s are a special case and we have to verify support is installed
#   on the ECU!
if [ "$AP_MANUF" == "SUB" ]; then
   test_for_realtime
fi

# these are returning true and false, thus backwards from bash norms
if [ $using_realtime == "no" ]; then
  exit 0
else
  exit 1
fi

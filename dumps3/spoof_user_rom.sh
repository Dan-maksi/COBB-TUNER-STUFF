#!/bin/sh
. ./sh_funcs.sh
. ./user_funcs.sh
. ./subaru_funcs.sh

# If we don't have a user rom and we're installed, we can just spoof it
if [[ ! -e "$AP_USER_ROM" ]] && [[ 1 -eq "$AP_STATE" ]]; then
  # check the disable_dump field in the config file. We only want to do
  # this if the vehicle config explicitly skipped the dump
  get_cfg_val $AP_VEHICLE disable_dump

  if [[ "true" ==  "$disable_dump" ]]; then
    get_base_rom $AP_VEHICLE BASE_ROM
    set_user_rom $BASE_ROM $BASE_ROM $AP_USER_ROM
    echo "set user rom to $BASE_ROM"
  fi
else
  echo "spoof_user_rom.sh: doing nothing"
fi

exit 0
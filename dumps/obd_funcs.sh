#!/bin/sh

scripts="uiInterface user_funcs serialnumber modular_funcs"
for script in $scripts; do
  if [ -e "${script}.sh" ]; then
    . "./${script}.sh"
  fi
done

# ERROR CODES for sh_funcs.sh
# Dem ERROR CODES
ERR_MKDIR_USER_DUMP=1002 #@ Failed to create user data directory
ERR_MV_USER_RAM=1002 #@ Failed to copy user's ROM data

ERR_CFG_FILE_NOT_FOUND=1014   #@ Failed to find AP configuration file
ERR_OUT_FILE_NOT_FOUND=1015   #@ Failed to find AP output file
ERR_AP_MOUNT_MNT1=1016            #@ Failed to mount filesystem partition
ERR_RM_PTM=1017               #@ Failed to clean up existing PTM map files
ERR_RM_USER_PREV=1018         #@ Failed to remove previous installation files
ERR_AP_MOUNT_MNT2=1019            #@ Failed to mount filesystem partition
ERR_ENV_UNDEFINED=1020        #@ environment variable not defined
ERR_NO_PATCH=1021             #@ Missing patch.ptm file
ERR_BAD_PATCH=1022            #@ Patch.ptm crc does not match the BASE_VEHICLE
ERR_BAD_PTM=1023              #@ Map file crc does not match the BASE_VEHILCE.
ERR_PATCH_FAILED=1024         #@ Could not apply the Patch.ptm 
ERR_PTM_FAILED=1025           #@ Could not apply the map.ptm (Forced)
ERR_MKDIR_USER_DATA=1026      #@ Could not create user data directory
ERR_MKDIR_USER_MAPS=1027    ash utility
  if [ ! -e "$APDX_FW_PATH" ]; then
    showInfo "UI_INFO_SYSTEM_ERROR" "OBD update data not found"
    _getUserSelection
    return 1
  fi

  echo_stderr "Firmware Path = \"$APDX_FW_PATH\""

  # reset PIC into boot loader mode
  echo 2 > $APDX_PIC_STATE

  # make sure the PIC is in boot loader mode
  if [ `cat $APDX_PIC_STATE` != "2" ]; then
    showInfo "UI_INFO_SYSTEM_ERROR" "Unable to update OBD hardware! ($?)"
    _getUserSelection
    return 1
  fi

  cat "$APDX_FW_PATH" > /dev/picspi0
  ret=$?

  # reset back into normal mode
  echo 1 > $APDX_PIC_STATE

  # make sure the the update worked and the PIC is running
  if [ `cat $APDX_PIC_STATE` != "1" ]; then
    showInfo "UI_INFO_SYSTEM_ERROR" "OBD update failed! ($?)"
    _getUserSelection
    return 1
  fi

  echo_stderr "RunUpdateAPDX complete"
  return 0
}


DX_PIC_Powerup()
{
  # used for shutting down PIC on loss of 12v power
  echo 1 > $APDX_PIC_STATE
}

DX_PIC_Shutdown()
{
  # used for shutting down PIC on loss of 12v power
  echo 0 > $APDX_PIC_STATE
}

CheckUpdateDX()
{
  if [ `cat $APDX_VBAT` -lt 8000 ]; then
    echo_stderr "PIC Firmware update ignored without VBAT-12V"
    showInfo "UI_INFO_ERR_CABLE"
    _getUserSelection
    return 1
  else
    echo_stderr "Current PIC Firmware is $VERSION_DX"

    # determine what version the firmware binary is
    CUR_APDX_PIC_FW=`ls -l "$BASE_PATH" | grep "dx_pic_fw_latest.ap" | sed 's/.*dx_pic_fw_latest.ap -> dx_pic_fw_\([0-9]*\).ap/\1/g'`
    echo_stderr "Latest PIC firmware is $CUR_APDX_PIC_FW"
    if [ "$VERSION_DX" != "$CUR_APDX_PIC_FW" ]; then
      echo_stderr "PIC Firmware is out of date, upgrading now"
      RunUpdateAPDX
      if [ $? -eq 0 ]; then
        echo_stderr "PIC Firmware update complete"
        return 0
      else
        echo_stderr "PIC Firmware update failed!"
        return 1
      fi
    else
      echo_stderr "PIC Firmware is up to date"
    fi

    # check to make sure the PIC RAM is good
    local memory_failures=`cat /sys/dx_hw_pic/memtest16`
    echo_stderr "PIC Memory test result: $memory_failures"
    if [ $memory_failures -ne 0 ]; then
      showInfo "UI_INFO_SYSTEM_ERROR" "OBD hardware fault! ($memory_failures)"
       _getUserSelection
      return 2
    fi 
  fi
  return 0
}


GetVersionAPDX
local ret=$?
if [ -n "$VERSION_DX" ]; then
  CheckUpdateDX
  ret=$?
fi
return $ret

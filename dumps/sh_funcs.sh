#!/bin/sh

. ./sh_funcs.sh

SD_SUFFIX="_SD"
CF_SUFFIX="_CF"
TRUE_SUFFIX="_true"
USER_REFLASH_MAP="$AP_USER_DATA/reflash"
USER_REFLASH_PROPS="$AP_USER_DATA/reflash_props"
AP_USER_MAPNAMES=${AP_USER_DATA}mapnames
TMP_MAP_PROPS=/tmp/AP_MAP_PROPS
ERR_MAP_PROPSe user data directory
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
ERR_MKDIR_USER_MAPS=1027      #@ Could not create user maps directory
ERR_AP_MOUNT_MNT3=1028            #@ Failed to mount filesystem partition
ERR_AP_MOUNT_MNT4=1029            #@ Failed to mount filesystem partition

AP_DATA=/ap-data

AP_USER_DATA_ECUKEYS=$AP_USER_DATA/ecukeys
AP_USER_SETTINGS="/user/ap-user/settings"
AP_DEFAULT_SETTINGS="/ap-app/settings"
TMP_USER_SETTINGS="/tmp/tmp_settings"
AP_RECOVERY_SETTINGS=${AP_USER_DATA}recovery_settings

AP_CFG_PATH=${AP_DATA}/ecu_info
AP_TCM_CFG_PATH=${AP_DATA}/tcm_info
AP_DATA_PRESETS_PATH=${AP_DATA}/log_presets

AP_CFG_DEFAULTS_PATH=$AP_CFG_PATH/DEFAULTS.cfg
AP_TCM_CFG_DEFAULTS_PATH=$AP_TCM_CFG_PATH/DEFAULTS.cfg

# temp files for various outputs from utilities
TMP_MAPSEL=/tmp/AP_MAPSEL
TMP_IDENTIFY=/tmp/AP_IDENTIFY
TMP_VENDOR=/tmp/AP_VENDOR
TMP_ROM=/tmp/AP_ROM
TMP_ROM_MD5=/tmp/AP_ROM.romsum
DUMP_ROM=/tmp/DUMP_ROM
SCRIPTLOG=/tmp/DUMPLOG

# These should mirror DeviceState.h::e_DeviceState 
DS_INVALID=-1
DS_UNINSTALLED=0
DS_INSTALLED=1
DS_RECOVERY=2
DS_UNKNOWN_ROM=3
DS_FW_CONFLICT=4
DS_DEACTIVATED=5

# predicate values are reversed vs. C++ land.
SHELL_SUCCESS=0
SHELL_FAILURE=1

# Feature enabled/disabled mirrored from shell.h
FEATURE_ENABLED=1
FEATURE_DISABLED=0

# Exit return values used by exit_normal() and exit_reload_gui()
EXIT_NORMAL=0
EXIT_RELOAD_GUI=1

MODULE_ID_ECU="ECU"
MODULE_ID_TCM="TCM"

# calculate floating point values using awk
# example params: "5/2"
# NOTE: DO NOT USE SPACES
calc()
{ 
  awk "BEGIN { print "$*" }"; 
}

# test if the global transmission tuning feature is enabled.
is_trans_feature_enabled()
{
  if [ -n "$AP_FEATURE_TRANS_TUNING" ] ; then
    if [ $AP_FEATURE_TRANS_TUNING -eq $FEATURE_ENABLED ] ; then
     return $SHELL_SUCCESS
    fi
  fi
  return $SHELL_FAILURE
}

# no params, loads the appropriate manuf_funcs.sh file
load_manuf_funcs()
{
  AP_MANUFSTR=`get_manufstr_from_manuf $AP_MANUF`
  if [ -e ${AP_MANUFSTR}_funcs.sh ]; then
    . ./${AP_MANUFSTR}_funcs.sh
  else 
    die "${AP_MANUFSTR}_funcs.sh not found!"
  fi
}

# syntax: die <"error_message"> <opt: reload_gui (0|1)>
die() 
{
  echo -e "$1" 1>&2 # redirect to stderr
  if [ ! -z $2 ]; then
    if [ $2 -eq 0 ]; then
      exit 0
    fi
  fi
  exit 1
}

err_die()
{
  echo -e "$1" 1>&2 # redirect some stuff over to some other stuff (to stderr)
  showSystemErr $2
  getUserSelection
  exit 0    # We're going to exit if the user hits OK or cancel or whatever
}

# if the specified filename does not exist, put up a system error
err_if_not_exist()
{
  if [ -z "$1" -o ! -e "$1" ]; then
    err_die "File does not exist! $1" $ERR_OUT_FILE_NOT_FOUND
  fi
}

# check return converts 0-255 to a signed return code and checks for success
# syntax: chkret <return_value> <"error_message"> <opt: reload_gui (0|1)>
chkret() 
{
  if [ $1 -gt 127 ]; then
    die "$2" $3 
  fi
}

# kinda like check ret but performs the function you give it and checks
# the return value
# syntax: execute <"command"> <"error_message"> <opt: reload_gui (0|1)>
execute()
{
  eval "$1"
  ret=$?
  chkret $ret "$2" $3
  return $ret
}

# So this is for system executables that don't have their own error handling
# syntax: sys_exec <"command"> <"error_message"> <error_code>
sys_exec()
{
  eval "$1"
  #if [ $? -gt 127 ]; then
  if [ ! $? -eq 0 ]; then
    err_die "$2" $3
  fi
}

# This is the same as above, however does not die if we fail.
sys_exec_allow_fail()
{
  eval "$1"
  local retCode=$?
  #if [ $? -gt 127 ]; then
  if [ ! $retCode -eq 0 ]; then
    echo "$2 - continuing anyway."
  fi
  return $retCode
}

# Evaluates an argument while piping the stderr to a log file.  
# Returns the return value for post processing if necessary. 
# In no condition does it ever exit.
# Auto-Initializes a Log.
# syntax: run_and_log <"command">
log_and_run()
{
  #Determine if the Scriptlog exists already
  if [ ! -e $SCRIPTLOG ]; then
    #Creating/Init Log if none is detected
    echo -e "Start of Script... \n" > $SCRIPTLOG
  fi

  eval "$1" 2>> $SCRIPTLOG
  ret=$?
  return $ret
}

# Logging Execute is for binaries that already have error handling built in.
# This prevents redundant error calls being made.
# Made as a direct replacement for execute()
# Creates a log in temp if none exists.
# Writes an Error file if an error occurs during execution and exits.
# Auto-Initializes a Log.
# -syntax : log_execute <"command"> <"Message to write to log on Fail">
# -Param 1 = Command to Evaluate, piped to stderr
# -Param 2 = Error Message to Log, Added as a new line
# -Param 3 =  <opt: reload_gui (0|1)>
log_execute()
{
  E_Argument=$1
  Error_Arg=$2
  
  #Determine if the Scriptlog exists already
  if [ ! -e $SCRIPTLOG ]; then
    #Creating/Init Log if none is detected
    echo -e "Start of Script... \n" > $SCRIPTLOG
  fi 
 
  #Evaluate the Argument, log to StdError, and check the return
  eval "$E_Argument" 2>> $SCRIPTLOG
  ret=$?
  
  if [ $ret -gt 127 ]; then
      echo -e "Error: $Error_Arg Failed \n" >> $SCRIPTLOG
      echo -e "Command: $E_Argument Failed \n" >> $SCRIPTLOG
      log_exit "$3" "E"
  fi
  echo -e "Command: $E_Argument Success \n" >> $SCRIPTLOG
  return $ret
}

# Logging System execute for binaries/scripts/tasks with no error handling.
# Made as a direct replacement for sys_exec ()
# Creates a log in temp if none exists.
# Writes an Error file if an error occurs during execution and exits.
# Auto-Initializes a Log.
# -syntax: log_sys_exec <"command"> <"Message to write to log on Fail"> <"Error code to display on sys_error">
# -Param 1 = Command to Evaluate
# -Param 2 = Error Message to Log
# -Param 3 = Error Code to Display to GUI

log_sys_exec()
{
  E_Argument=$1
  Error_Arg=$2
  Error_Code=$3

  #Determine if the Scriptlog exists already
  if [ ! -e $SCRIPTLOG ]; then
    #Creating/Init Log if none is detected
    echo -e "Start of Script... \n" > $SCRIPTLOG
  fi 
 
  #Evaluate the Argument, log to StdError, and check the return
  eval "$E_Argument" 2>> $SCRIPTLOG
  ret=$?
  
  if [ $ret -gt 127 ]; then
    echo -e "Error: $Error_Arg Failed \n" >> $SCRIPTLOG
    echo -e "Command: $E_Argument Failed \n" >> $SCRIPTLOG
    log_write_out
    showSystemErr $Error_Code    
    getUserSelection
    exit 0
  fi
  echo -e "Command: $E_Argument Success \n" >> $SCRIPTLOG
  return $ret
}

# Writes an echo'd statement into the Log, and inits it if it does not exist.  
# Automatically appends a "newline" tag to separate text.  
# syntax: log_echo <"Message to log in Quotes">
# Auto-Initializes a log.  
# Param 1 = Statement or string to append into log

log_echo()
{
  #Determine if the Scriptlog exists already
  if [ ! -e $SCRIPTLOG ]; then
    #Creating/Init Log if none is detected
    echo -e "Start of Script... \n" > $SCRIPTLOG
  fi 

  echo -e "$1 \n">> $SCRIPTLOG

}

# A version of exit that acts the same as "exit 0" or "exit 1",
# but adds the ability to close logs.  
# Forces the closing of the SCRIPTLOG.
# -Param 1: 0 or 1 for exit 0 or exit 1.
# -Opt Param 1: Forces write of log 
#     ( "W" for standard write) (Defaulted with anything else as error - such as "E")
# Example: log_exit 1      -- exits with a value of one.
# Example: log_exit 1 W    -- exits while write a file of "LOG_#.log" in the user partition.
# Example: log_exit 1 E    -- exits with a file of "ERROR_#.log" in the user partition.
log_exit()
{
  if [ ! -z "$2" ]; then
    if [ $2 = "W" ]; then
      log_write_out 1
    else
      log_write_out
    fi
  else
    rm -f $SCRIPTLOG
  fi

  die "log_exit exiting" $1
}


# Write Log Helper for the logging system (Cannot be called on 
# its own without log_execute or log_sys_exec.)
# Assumes that the Scriptlog has already been initialized
# Writes out the file, and removes the temp file scriptlog (DUMPLOG)
# - Optional Argument: <0 = Error Condition Name (default), 1 = Log_ prefix>
log_write_out()
{
  sys_exec "./ap_mount --remount=rw" "ap_mount cancelled" $ERR_AP_MOUNT_MNT1
        
  #check and see if /user/ap-user/logs/ is there
  USER_LOGS=/user/ap-user/logs
  if [ ! -e $USER_LOGS ]; then
    mkdir $USER_LOGS
  fi
  
  local LOG_NAME="ERROR_.Log"
  if [ ! -z "$1" ]; then
    if [ $1 -eq 1 ]; then
      LOG_NAME="LOG_.Log"
    fi
  fi

  #Create the Script Directory, auto generate a File Number.  Max 20 files.
  export SCRIPTLOGFinal=$(./create_log.sh name=$LOG_NAME max_files=20 dir=$USER_LOGS)

  #copy Contents (i.e. Write out) -ENCRYPTED
  ./rom_encrypter ENC $SCRIPTLOG
  local ext=_enc.rom
  local logToMove=$SCRIPTLOG$ext
  cp $logToMove $SCRIPTLOGFinal
  
  #make the filesystem read only
  sys_exec "./ap_mount --remount=ro" "ap_mount cancelled" $ERR_AP_MOUNT_MNT2

  #Remove the temp file log path
  rm $SCRIPTLOG
 
}

# param #1 = cfg filename -- uses DEFAULTS.cfg if cfg filename doesn't exist
# param #2 = which value to retrieve AS WELL AS the return variable name
# param #3 = see get_out_val
# EXAMPLE: 
# get_cfg_val SUBA_US_WRX_02 cpu
# echo "CPU type is $cpu"
# RESULT:
# CPU type is HC16
get_cfg_val() {
  if [ ! -e $AP_CFG_PATH/$1.cfg -a "$3" != "1" ]; then
    showInfo "UI_INFO_ERR_VEHICLE_NOT_SUPPORTED" "$1"
    getUserSelection
    echo "error: $AP_CFG_PATH/$1.cfg does NOT exist!"
    exit 0
  fi

  if [ ! -e $AP_CFG_DEFAULTS_PATH ]; then
    get_out_val $AP_CFG_PATH/$1.cfg $2 $3
  else
    # if the key value exists in the vehicle cfg, use it
    if grep -q ^$2= $AP_CFG_PATH/$1.cfg; then
      get_out_val $AP_CFG_PATH/$1.cfg $2 $3
    else
      # nothing was found, look for a default value
      get_out_val $AP_CFG_DEFAULTS_PATH $2 $3
    fi
  fi
}


# param #1 = tcm cfg filename
# param #2 = which value to retrieve AS WELL AS the return variable name
# PARAM #3 - if 1, will not halt if no cfg file found
# EXAMPLE:
# get_cfg_val NISS_GTR_LC1_JF09E  base
# echo "base type is $base"
# RESULT:
# base type is NISS_GTR_LC1_JF09E
get_tcm_cfg_val()
{
  if [ ! -e $AP_TCM_CFG_PATH/$1.cfg -a "$3" != "1" ]; then
    showInfo "UI_INFO_ERR_VEHICLE_NOT_SUPPORTED" "$1"
    getUserSelection
    echo "error: $AP_TCM_CFG_PATH/$1.cfg does NOT exist!"
    exit 0
  fi

  if [ ! -e $AP_TCM_CFG_DEFAULTS_PATH ]; then
    get_out_val $AP_TCM_CFG_PATH/$1.cfg $2 $3
  else
    # if the key value exists in the vehicle cfg, use it
    if grep -q ^$2= $AP_TCM_CFG_PATH/$1.cfg; then
      get_out_val $AP_TCM_CFG_PATH/$1.cfg $2 $3
    else
      # nothing was found, look for a default value
      get_out_val $AP_TCM_CFG_DEFAULTS_PATH $2 $3
    fi
  fi
}

# With the introduction of vehicle specific settings files we can't use a
#  universal path for the settings file anymore.
# This function will echo the correct name of the settings file.
# EXAMPLE:
# local settings=`default_settings`
# echo "$settings"
# RESULT:
# "/ap-app/settings.SUBA_US_WRXM_15"
default_settings()
{
  local default_settings=""
  if [ ! -z "$AP_VEHICLE" ]; then
    default_settings="$default_settings.$AP_VEHICLE"
    if [ ! -e "$default_settings" ]; then
      default_settings="$AP_DEFAULT_SETTINGS"
    fi
  else
    default_settings="$AP_DEFAULT_SETTINGS"
  fi

  echo "$default_settings"

  return 0
}

# param #1 = output filename
# param #2 = which value to retrieve ALL WELL AS the return variable name
# param #3 = optional parameter, 1=don't err_die, otherwise err_die
# EXAMPLE:
# get_out_val /tmp/ident_result ident
# echo "vehicle ident is $ident"
# RESULT:
# "vehicle ident is SUBA_US_WRX_02"
get_out_val()
{
  get_named_out_val $1 $2 $2 $3
  return $?
}

# Allows providing the output variable name instead of using the input key name
# param #1 = output filename
# param #2 = output variable name
# param #3 = which value to retrieve 
# param #4 = optional parameter, 1=don't err_die, otherwise err_die
# EXAMPLE:
# filename $AP_CFG_PATH/ids.txt contains CM5A_APD=FORD_US_FOCUS_ST_13C
# get_named_out_val $AP_CFG_PATH/ids.txt ecu_id CM5A_APD
# echo "vehicle ident is $ecu_id"
# RESULT:
# "vehicle ident is FORD_US_FOCUS_ST_13C"
get_named_out_val()
{
  out_file=$1
  if [ ! -e $out_file ]; then
    if [ $4 -eq 1 ]; then
      echo "get_named_out_val - FAILED! $out_file not found!"
      return 1  # file not found failure!
    else
      err_die "$out_file does not exist" $ERR_OUT_FILE_NOT_FOUND
    fi
  fi

  # BusyBox sed doesn't support much for control characters! So to remove \r...
  if [ -z "$CR" ]; then
    # use printf! it's slow to call printf, so only do it once!
    CR=$(printf '\r')
  fi
  # and then do all this in one shot with sed to save calls into BusyBox
  val=`sed -n -e s/$CR\$// -e s/^$3=//p < $out_file`
  eval "$2=\"$val\""  # this assigns our value to $2 (still neato!)

  return 0
}

# param #1 = which value to retrieve AS WELL AS the return variable name
# EXAMPLE:
# get_settings_val ident
# echo "vehicle ident is $ident"
# RESULT:
# "vehicle ident is SUBA_US_WRX_02"
get_settings_val()
{
  local retval=""

  get_out_val "$AP_USER_SETTINGS" "$1" 1 # 1 = don't err_die
  if [ $? -eq 1 ]; then
    local settings=`default_settings`
    get_out_val "$settings" "$1" 1 # 1 = don't err_die
    if [ $? -eq 1 ]; then
      return 1
    fi
  fi

  return 0
}

# param #1 = output filename
# param #2 = name of the param to set
# param #3 = value of the param to set
# Will set a value in the settings file provided in param #1
# If this file doesn't exist it will try to create the file and then fail.
# EXAMPLE:
# set_out_val /tmp/ident_result.cfg ident CAR
# get_out_val /tmp/ident_result.cfg ident
# echo $ident
# RESULT:
# "CAR"
set_out_val()
{
  #remove any instances of the variable before inserting.
  sed -i "/^$2=/d" "$1"
  echo "$2=$3" >> "$1"
}

# param #1 = name of the param to set
# param #2 = value of the param to set 
# Sets a value in the USER settings file
# If this file doesn't exist it will copy the default one and then add this setting
set_settings_val()
{
  user_remount_rw

  #make sure the user settings file exists
  if [ ! -e "$AP_USER_SETTINGS" ]; then
    local settings=`default_settings`
    # then we'll copy the default settings file if the local copy doesn't exist
    cp "$settings" "$AP_USER_SETTINGS"
  fi

  set_out_val $AP_USER_SETTINGS $1 $2
  local ret=$?

  user_remount_ro

  if [ $ret -ne 0 ]; then
    err_die "Default settings file not found." $ERR_CFG_FILE_NOT_FOUND 
  fi
}

# param #1 = list of comma separated values
# param #2 = value to find in list
# returns 0 if found, 1 if not found
list_contains()
{
  if echo ",$1," | grep -q ",$2,"; then
    return 0 # success
  fi

  return 1 # failure
}

# param #1 = list of comma separated values
# echos the list with commas converted to spaces
convert_csv_to_spaces()
{
  echo "$1" | sed 's/,/ /g'
}

# param #1 = VIN 
# echos the 2nd and 3rd characters of the provided parameter, which are the
# manufacturer code in a VIN
get_manufcode_from_vin()
{
  local param_vin=$1
  echo "${param_vin:1:2}"
}

sanity_check_env()
{
  if [ ! "$AP_MANUF" ]; then
    err_die "AP_MANUF undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_SERIAL" ]; then
    err_die "AP_SERIAL undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_VEHICLE" ]; then
    err_die "AP_VEHICLE undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_APP" ]; then
    err_die "AP_APP undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_APP_SETTINGS" ]; then
    err_die "AP_APP_SETTINGS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_DATA" ]; then
    err_die "AP_DATA undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_DATA_MAPS" ]; then
    err_die "AP_DATA_MAPS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_DATA_ROMS" ]; then
    err_die "AP_DATA_ROMS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_DATA_STUBS" ]; then
    err_die "AP_DATA_STUBS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_DATA" ]; then
    err_die "AP_USER_DATA undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_USER" ]; then
    err_die "AP_USER_USER undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_MAPS" ]; then
    err_die "AP_USER_MAPS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_LOGS" ]; then
    err_die "AP_USER_LOGS undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_PREV" ]; then
    err_die "AP_USER_PREV undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_ROM" ]; then
    err_die "AP_USER_ROM undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_USER_TCM_ROM" ]; then
    err_die "AP_USER_TCM_ROM undefined" $ERR_ENV_UNDEFINED
  elif [ ! "$AP_DEVDIR" ]; then
    err_die "AP_DEVDIR undefined" $ERR_ENV_UNDEFINED
  fi
}

dongle_ident()
{
  if [ -e "/sys/cobb_pic_spi/sysfs/fw_ver" ]; then # APDX integrated PIC
    local pic_ver=`cat /sys/cobb_pic_spi/sysfs/fw_ver`
    dongle_ident="On-board DXv$pic_ver"
  elif [ -e "/sys/cobb_obd" ]; then # APv2-B integrated INF
    dongle_ident="On-board v2B"
  else # APv2 USB dongle
    dongle_ident=`cat /proc/bus/usb/devices | grep "P:" | awk '{ if ( $2 == "Vendor=1a84" ) { if ( $3 == "ProdID=0211" ) { print "COBB2 v"$5 } else if ( $3 == "ProdID=0210" ) { print "COBB v"$5 } else if( $3 == "ProdID=0213" ) { print "COBB3 v"$5 }  else { print "Unknown "$3 } } }'`
  fi

  eval "$1=\"$dongle_ident\""
}

###############################################################################
# make_vehicle_rom

make_vehicle_rom()
{
BASE_ROM=$1
PATCH_MAP=$2
VEHICLE_ROM=$3
PLACE_HOLDER=0
VENDOR=/tmp/cobbVendor



  #make sure there is a patch.ptm 
  if [ -z $PATCH_MAP ]; then
    err_die "$PATCH_MAP is missing " $ERR_NO_PATCH
  fi

  val=`cat  $PATCH_MAP | grep Holder`
  HOLDER=`echo $val | awk '{print $2}' `

  #test to see if the map is just a place holder map
  if [ "$HOLDER" == "Holder" ]; then
   # echo "this is the place holder patch.ptm"
    PLACE_HOLDER=1
  fi

  if [ $PLACE_HOLDER -eq 0 ]; then
    # apply the patch.ptm to the BASE_ROM
    execute "./map2rom --map=\"$PATCH_MAP\" --stock=$BASE_ROM --rom=$VEHICLE_ROM --vendor=$VENDOR" "map2rom - ROM image failed" $ERR_PATCH_FAILED

   rm $VENDOR

  else
    #the bae rom is the same as the vehicle_rom 
    cp $BASE_ROM $VEHICLE_ROM

  fi
return 0

}
###############################################################################
# get_vehicle_from_mode
#
# $1 = mode type (e.g. SD, PSI)
# $2 = install mode
#
# pre-condition: $vehicle must be set in the environment
# post-condition: $vehicle will be re-set in the environment
# return values:
#   -1= invalid function arguments
#   0= no change in vehicle ID
#   1= switching from non-SD to SD vehicle ID
#   2= switching SD to non-SD vehicle ID
get_vehicle_from_mode()
{
  if [ -z "$1" ]; then
    echo "get_vehicle_from_mode Usage"
    echo "get_vehicle_from_mode <S_MODE> <INSTALL_MODE>"
    echo "<S_MODE> (SD)"
    echo "<INSTALL_MODE> (1|0) default=0"
    return -1
  fi

  local S_MODE_POSTFIX="_$1"
  local N_MODE_TEXT
  local N_MODE_DISABLED_TEXT
  local S_MODE_TEXT
  local S_MODE_DISABLED_TEXT
  local S_MODE_SELECT_MENU
  local INSTALL_MODE=0 # Default installation to off

  if [ $1 == "SD" ]; then
    N_MODE_TEXT="UI_ITEM_STANDARD_ROM"
    N_MODE_DISABLED_TEXT="UI_ITEM_STANDARD_DISABLED_ROM"
    S_MODE_TEXT="UI_ITEM_SPEED_D_ROM"
    S_MODE_DISABLED_TEXT="UI_ITEM_SPEED_D_DISABLED_ROM"
    S_MODE_SELECT_MENU="UI_LIST_SELECT_ROM_TYPE"
  else
    echo "invalid mode"
    return -1
  fi

  local INSTALL_MODE=0 # Default installation to off
  if [ ! -z "$2" ]; then
    if [ $2 -eq 1 ]; then
      INSTALL_MODE=1
    fi
  fi

  local MODE_RETURN=0 # Sets the return value

  # Detect if the vehicle installed is normal or special, need this for later
  local S_MODE=0
  if echo $vehicle | grep -q "$S_MODE_POSTFIX"; then
    S_MODE=1
  fi

  #We want to pre-check for SD maps, if none exist we act like normal..
  local S_VEHICLE=$vehicle
  local N_VEHICLE=$vehicle
  if [ $S_MODE -eq 0 ]; then
    S_VEHICLE=`echo $vehicle | sed -n "s/\(^.*\)\(_[0-9]*\)/\1$S_MODE_POSTFIX\2/p"`
  else
    N_VEHICLE=`echo $vehicle | sed -n "s/\(.*\)$S_MODE_POSTFIX\(.*\)/\1\2/p"`
  fi

  local N_MAP_DIRS="--dir=$AP_USER_MAPS"
  if [ $INSTALL_MODE -eq 1 ]; then
    N_MAP_DIRS="$N_MAP_DIRS --dir=${AP_DATA_MAPS}$N_VEHICLE"
  fi
  local MENU_MODE
  execute "common_map_select $N_MAP_DIRS --filter=$N_VEHICLE --map_mode --smode_postfix=$S_MODE_POSTFIX" "map_select cancelled"
  MENU_MODE=$?   
  
  if [ $S_MODE -eq 0 ]; then
    local N_FLAG="S" # Select "Normal" by default
    local S_FLAG=""
  else
    local N_FLAG=""
    local S_FLAG="S" # Select "SD" by default
  fi

  local N_MENU_TEXT="$N_MODE_TEXT"
  local S_MENU_TEXT="$S_MODE_TEXT"
  case $MENU_MODE in
    0) #No normal or special maps detected
      return $MODE_RETURN ;;
    1) #Normal maps detected, no special
      if [ $S_MODE -eq 0 ]; then
        return $MODE_RETURN
      else 
        S_FLAG=E
        S_MENU_TEXT="$S_MODE_DISABLED_TEXT"
      fi ;;
    2) #Special maps detected, no normal
      if [ $S_MODE -eq 0 ]; then
        N_FLAG=E
        N_MENU_TEXT="$N_MODE_DISABLED_TEXT"
      else 
        return $MODE_RETURN
      fi ;;
  esac

  # If we made it this far, we need some type of menu
  addLocalItem "$N_MENU_TEXT" "$N_FLAG"
  addLocalItem "$S_MENU_TEXT" "$S_FLAG"
  showList "$S_MODE_SELECT_MENU"
  getUserSelections MODE_SELECTION
  if [ $? -le 1 ]; then
    echo "Vehicle selection cancelled, returning to main menu."
    exit 0
  else
    case $MODE_SELECTION in
      0)
        echo "normal rom will be used"
        #if SD is installed, we need to remove the _SD from the vehicle_id
  if [ $S_MODE -eq 1 ]; then
    vehicle="$N_VEHICLE"
    MODE_RETURN=2
  fi
  echo "vehicle=$vehicle" ;;
      1)
  echo "special rom will be used"
  # if the user selects a SD rom then we need to set the vehicle to SD
  # but only if its not already an SD car...        
  if [ $S_MODE -eq 0 ]; then
    vehicle="$S_VEHICLE"
    MODE_RETURN=1
  fi
  echo "vehicle=$vehicle" ;;
      *)
  showInfo "UI_INFO_ERR"
  getUserSelection
  echo "error recievied bad option"
  exit 0 ;;
    esac
  fi

  if [ $MODE_RETURN -gt 0 ]; then
    if [ $MODE_RETURN -eq 1 ]; then
      MODE_CHANGE_INFO="UI_INFO_MAF_MODE_CHANGE"
    elif [ $MODE_RETURN -eq 2 ]; then
      MODE_CHANGE_INFO="UI_INFO_SD_MODE_CHANGE"
    fi
    showInfo $MODE_CHANGE_INFO
    getUserSelection
    if [ $? -ne 1 ]; then # Cancel
      exit 0
    fi
  fi

  return $MODE_RETURN
}

# Patches a provided base ROM with the provided patch file
# $1 = the patch that is to be applied to the base ROM to create the "stock" ROM
# $2 = the base ROM to begin with
# $3 = the output ROM file, which will end up being either the base ROM itself, or
#      the base+patch 
# Handles "empty" (Place Holder File) patch files for cases where the provided "patch"
# is identical to the provided "base" ROM
patch_base_rom()
{
  local patch_file="$1"
  local base_rom="$2"
  local out_rom="$3"

  # check to see if we need to create a base+patched file -- if the patch file contains
  # the text "Place Holder File," it is not necessary to patch, because the patch
  # file contained no differences from the base file at build time
  if ! grep -q "Place Holder File" $patch_file; then    
    # apply the patch to the base file
    execute "map2rom --map=\"$patch_file\" --stock=$base_rom --rom=$out_rom" "map2rom - ROM image failed" $ERR_PATCH_FAILED
    base_rom=$out_rom
  else
    # nothing to do, set the output to contain the "base" ROM 
    rm "$out_rom"
    cp "$base_rom" "$out_rom"
  fi
}

###############################################################################
# apply_patch_and_map
# $1 = the patch that is to be applyed to the base rom to create the stock rom
# $2 = the map to be applyed after the patch has been applied
# $3 = BASE_ROM
# $4 = OUT_ROM the rom with the patch and the map applied to it
# $5 = VENDOR file were the vendor info will be sent
# this routine will allow for old maps made for the VEHILCE to be flased to the 
# car.  There is a check to see if the CRC of the map matches the BASE_VEHICLE 
# or the VEHICLE.

apply_patch_and_map()
{

  if [ ! "$#" -eq 5 ]; then
    echo "apply_patch_and_map: Usage"
    echo "<PATCH_MAP> (rom_patch.ptm)"
    echo "<INSTALL_MaP> (stage1.ptm)"
    echo "<BASE_ROM> (base.rom)"
    echo "<OUT_ROM> (base.rom + rom_patch.ptm + stage1.ptm)"
    echo "<VENDOR> (/tmp/vendor.txt)"
    return -1
 fi

  PATCH_MAP="$1"
  INSTALL_MAP="$2"
  BASE_ROM=$3
  OUT_ROM=$4
  VENDOR=$5
  PLACE_HOLDER=0

  if [ "$MODULE_ID" == "$MODULE_ID_TCM" ]; then
    showBusy "UI_WAIT_BUILDING_TCM_FLASH"
  else
    showBusy "UI_WAIT_BUILDING_FLASH"
  fi

  #make sure there is a patch.ptm 
  if [ -z $PATCH_MAP ]; then
    err_die "$PATCH_MAP is missing " $ERR_NO_PATCH
  fi

  val=`cat  $PATCH_MAP | grep Holder`
  HOLDER=`echo $val | awk '{print $2}' `

  #test to see if the map is just a place holder map
  if [ "$HOLDER" == "Holder" ]; then
   # echo "this is the place holder patch.ptm"
    PLACE_HOLDER=1
  fi

  # for base vehicles there is place holder ptm that has "Place Holder" writen in it
  # that means that the BASE_VEHILCE and the VEHILCE are the same and there is no
  #patch.ptm

  if [ $PLACE_HOLDER -eq 0 ]; then
    #test the patch.ptm to make sure the crc is correct
     execute " ./map2rom --map=\"$PATCH_MAP\" --stock=$BASE_ROM --rom=$OUT_ROM --vendor=$VENDOR --cksums" "map2rom ptm does not match base_vehilce " $ERR_BAD_PATCH
  fi

  #if this is a place holder map then there is no reason to put a base map on it
  if [ $PLACE_HOLDER -eq 0 ]; then
    
    # apply the patch.ptm to the BASE_ROM
    execute "./map2rom --map=\"$PATCH_MAP\" --stock=$BASE_ROM --rom=$OUT_ROM --vendor=$VENDOR" "map2rom - ROM image failed" $ERR_PATCH_FAILED
   
  fi
   
  #make sure the map matched the crc for the base_vehicle or the vehicle rom

  echo "going to apply the map "
  echo "--map=\"$INSTALL_MAP\" "
  echo "--stock=$BASE_ROM "
  echo "--vendor=$VENDOR"
  echo "PLACE_HOLDER=$PLACE_HOLDER"

  ./map2rom --quiet --map="$INSTALL_MAP" --stock=$BASE_ROM --rom=$OUT_ROM --vendor=$VENDOR --cksums

  RETURN_VAL=$?
  echo "RETURN_VAL=$RETURN_VAL "


  if [ ! $RETURN_VAL -eq 0 ]; then
    echo "checking to see if this is an old map"
    #this check will only happen if the map crc didn't match the base_vehicle crc
    #this will happend for all old tunes done with the old software.
    execute "./map2rom --map=\"$INSTALL_MAP\" --stock=$OUT_ROM --rom=$OUT_ROM --vendor=$VENDOR --cksums" "map2rom map does not match base or vehicle rom" $ERR_BAD_MAP
  fi

  # --patch will force the map file to the OUT_ROM (VEHILCE_ROM)  
  # we've already varified that the crc matched either the base_vehicle or the VEHICLE rom. 
  # build the rom from map selection
  if [ $PLACE_HOLDER -eq 0 ]; then  #--stock=OUT_ROM=BASE_ROM + patch.ptm
    execute "./map2rom --patch --map=\"$INSTALL_MAP\" --stock=$OUT_ROM --rom=$OUT_ROM --vendor=$VENDOR" "map2rom - ROM image failed" $ERR_PTM_FAILED
  else                             #--stock=BASE_ROM
    execute "./map2rom --patch --map=\"$INSTALL_MAP\" --stock=$BASE_ROM --rom=$OUT_ROM --vendor=$VENDOR" "map2rom - ROM image failed" $ERR_PTM_FAILED
  fi

  #vendor is sent to the file
  # BASE_VEHILE + patch.ptm = VEHILCE.rom

  return 0

}

# Get the raw vbat reading from the LRADC and display it as a user friendly
#  voltage number (e.g. 11700 -> 11.7V)
userVoltage(){
  local vbat=`cat /sys/dx_lradc/vbat`
  local converted_vbat
  converted_vbat=`echo $vbat | sed -e 's/\(.*\)../\1/' -e 's/\(.\{1\}\)$/.\1V/'`

  echo "$converted_vbat"
}

showInstallDangerMessage(){
  showInfo "UI_INFO_OVERWRITE_WARN"
  getUserSelection
  if [ $? -lt 1 ]; then
    exit 0 # stop executing
  fi
}

# ask the user to turn off the ac, headlights, HVAC, whatever else
# $1= if provided, operate silently for pass-through
reqBattCharger()
{
  local vbat=$(userVoltage)
  if [ $# -lt 1 ]; then
    showInfo "UI_INFO_BATT_CHARGER" "Current voltage:$vbat"
    getUserSelection
    if [ $? -lt 1 ]; then
      exit 0 # stop executing
    fi
  fi
}

# Verify the Accessport can be used with the ECU
#
# Compares the Accessport vehicle to the ECU vehicle
#
# $1= ECU vehicle
# $2= Optional exit code. If exit code is 0, exit 0 is called on error; if exit code is !0, exit 1 is called on error. If no exit code, function will return 1.
verify_vehicle() {
  # echo "verify_vehicle()"

  # verify against all variants provided for a match!
  for variant in $1; do
    if [ $variant == $AP_VEHICLE ]; then
      return 0
    fi
  done

  showInfo "UI_INFO_ERR_NOT_INSTALLED" "$1"
  getUserSelection

  if [ ! -z "$2" ]; then
    if [ $2 -eq 0 ]; then
      exit 0
    else
      exit 1
    fi
  fi

  return 1
}

# Passes the most common map select parameters to ease the map select call
# $*= additional arguments such as --dir, --filter, and --base_filter
common_map_select() {
  echo "AP_LOCK=[$AP_LOCK]"
  echo "AP_VENDOR=[$AP_VENDOR]"
  echo "AP_SERIAL=[$AP_SERIAL]"
  echo "AP_UPGR_SERIAL=[$AP_UPGR_SERIAL]"
  echo "@=[$@]"

  ./map_select --ap_lock=$AP_LOCK --ap_vendor=$AP_VENDOR --ap_serial=$AP_SERIAL --upgr_serial=$AP_UPGR_SERIAL "$@"

  return $?
}

# generates a list of ROM files for the user to choose to flash.
# short descriptions for each ROM file will be loaded from filename.rom.desc in
# the same directory, if present
# $1= directory of roms to display for selection
# $2= output variable name for chosen ROM (empty if none chosen)
rom_select()
{
  if [ $# -lt 2 ]; then
    echo "rom_select parameters incorrect"
    exit 1 #should only happen if the developer sets this up incorrectly
  fi

  ITEMS=0
  for rom in $1/*.rom ; do
    # get just the filename without the path
    get_rom_name_and_description "$rom"
    addNonLocalItem "$rom_name" "" "$description"

    # ash scripting (which is what busybox supports on the AP) does not 
    # support true arrays, so we use eval to make up a dynamic variable
    # name, then reference it below.
    eval "rom_list_$ITEMS=\"$rom\""

    let ITEMS=$ITEMS+1
  done

  showList "UI_MENU_TUNE"
  getUserSelections SELECTION

  if [ $SELECTION -ge 0 ]; then
    # reference to the rom_list we generated above
    eval "$2=\`echo \$rom_list_$SELECTION\`"
  else
    $2=""
  fi
}

#this was shared code between GTR and Subaru for the mem_snapshot.sh file
#after a successful dump we need to encrypt it and move it to a user extractable
#area.
# $1 = dump filename
# $2 = if present, the encrypted filename (otherwise will use MEM_SNAPSHOT.ram)
process_ram_dump() {
  # tmp files
  
  if [ $# -lt 1 ]; then
    echo "RAM_DUMP parameter missing"
    exit 0 #should only happen if the developer sets this up incorrectly
  fi
  
  local TMP_RAM=$1

  local MEM_SNAPSHOT=MEM_SNAPSHOT.ram

  if [ ! -z "$2" ]; then
    MEM_SNAPSHOT=$2
  fi

  # encrypt
  ./rom_encrypter ENC $TMP_RAM /tmp/$MEM_SNAPSHOT # return codes from this are useless
  rm "$TMP_RAM"

  user_remount_rw

  prepare_user_folders

  # move the encrypted dump
  sys_exec "mv /tmp/$MEM_SNAPSHOT $AP_USER_DUMP/$MEM_SNAPSHOT" "mv failed" $ERR_MV_USER_RAM

  user_remount_ro
}

# Ask the LRADC if we're getting voltage on the 12V rail. If we are then an
#  OBD2 cable is likely attached (and by implication an ECU). This is a fast
#  way to quickly check for the presence of the OBD2 cable before bringing up
#  any comms.
# This function returns 0 for positive 12V connection or 1 for no 12V.
obd_cable_present () {
  local vbat
  vbat=`cat /sys/dx_lradc/vbat`

  # even unplugged the LRADC will pick up some noise, over 1V plugged then in!
  if [ $vbat -gt 1000 ]; then
    return 0
  else
    return 1
  fi
}

# Ask the LRADC what our current vbat is and check against the range supplied.
# Display the provided error message if we fall outside of that range!
# Inputs:
#
# $1 = Low voltage in mV (e.g. 11000 for 11.0V)
# $2 = High voltage threshold in mV
# $3 = custom GUI error range string (e.g. "11.0V-14.5V")
voltage_check () {
  local low_vbat=$1
  local hi_vbat=$2
  local user_selection=0
  local vbat=`cat /sys/dx_lradc/vbat`

  # spin on vbat until it's either valid or the user aborts!
  while [ $vbat -lt $low_vbat -o $vbat -gt $hi_vbat ]; do
    #bad vbat! warn!
    local user_vbat=$(userVoltage)
    if [ $vbat -lt $low_vbat ]; then
      showInfo "UI_INFO_CHECK_VOLTAGE_LOW" "Range:$3 Current:${user_vbat}"
    elif [ $vbat -gt $hi_vbat ]; then
      showInfo "UI_INFO_CHECK_VOLTAGE_HIGH" "Range:$3 Current:${user_vbat}"
    fi
    getUserSelection
    if [ $? -ne 1 ]; then # Cancel
      if [ -n "$SINGLEMAP" ]; then
        # exit with an error if called in passthrough
        exit 255 # bash for -1
      else
        # exit with a gui_reload success if not in passthough
        exit 1
      fi
    fi
    # fetch vbat and try again!
    vbat=`cat /sys/dx_lradc/vbat`
  done
}

# Verify the ECU we are about to flash is the same one we identified and/or
# verified marriage to at the beginning of our flash attempt. This closes a
# flash loophole that was a potential exploit.
# $1 = identify binary name
# $2 = identify binary args 
#      NOTE: (you should include --quiet or your equivalent to suppress output)
# $3 = identify temp output filename
# $4 = name of the license variable to check in the output file of your identify
# $5 = current vehicle identifier or variant list (for the installed case)
verifyECUMatch() {
  local ident_binary="$1"
  local args="$2"
  local tmp_ident="$3"
  local license_val="$4"
  local last_vehicle="$5 " #this space is intentional

  # uninstalled (and unknown ROM) behavior is to verify a vehicle ID match
  if [ $AP_STATE -eq $DS_UNINSTALLED -o $AP_STATE -eq $DS_UNKNOWN_ROM ]; then
    # verify we are still attached to the same ECU
    execute "./$ident_binary $args" "$ident_binary $args cancelled"
    get_named_out_val "$tmp_ident" "new_vehicle" "$license_val"
    rm $tmp_ident

    if [ -z "$last_vehicle" -o "${last_vehicle/"$new_vehicle "}" == "$last_vehicle" ]; then
      # vehicles no longer match! do not continue!
      showInfo "UI_INFO_ERR_ECU_MISMATCH"
      getUserSelection
      exit 0
    fi
  elif [ $AP_STATE -eq $DS_INSTALLED ]; then
    # Do a final serial number check to make sure they didn't switch vehicles.
    execute "./$ident_binary $args" "$ident_binary $args cancelled"
    get_named_out_val "$tmp_ident" "serial" "$license_val"
    rm $tmp_ident # no sys_exec

    # is this the right vehicle?
    verify_serial $serial 0
  fi
  # NOTE: We won't run this check if we are in recovery
}

# these functions are intended to be used before flash operations. Set the
# planned future state, so that recovery mode can properly move to the
# state that was requested. 
# For example, when uninstalled (0), the future state will be installed (1)
# Or, when installed (1), the future state will be uninstalled (0)
# The current AP_STATE will also be saved on each call, so that consecutive
# recovery calls can be detected, and different action can be taken
#
# $1 = desired future state (0-5)
# $2 = the map, if any, that is going to be flashed
recovery_set_future_state() {
  if [ $# -lt 1 ]; then
    echo "recovery_set_future_state parameter missing"
    exit 0 #should only happen if the developer sets this up incorrectly
  fi

  user_remount_rw
  set_out_val $AP_RECOVERY_SETTINGS recovery_prev_state $AP_STATE
  set_out_val $AP_RECOVERY_SETTINGS recovery_future_state $1
  set_out_val $AP_RECOVERY_SETTINGS recovery_map "$2" # may be empty
  user_remount_ro
}

# no parameters, sets the below variables and returns
recovery_get_future_state() {
  # don't err_die if these settings don't exist
  get_out_val $AP_RECOVERY_SETTINGS recovery_prev_state 1
  get_out_val $AP_RECOVERY_SETTINGS recovery_future_state 1
  get_out_val $AP_RECOVERY_SETTINGS recovery_map 1
}

# no parameters, clears the future state. Use after a successful flash
recovery_clear_future_state() {
  user_remount_rw
  rm $AP_RECOVERY_SETTINGS
  user_remount_ro
}

# Gets the vehicle from the $1 map file and sets it into $2 using eval.
#
# $1 - map file
# $2 - variable to set with the map name.
#
# Returns 0 on success, 1 on failure to read map.
get_vehicle_from_map()
{
  # pull vehicle ID from the map.
  local temp_vehicle
  temp_vehicle=`./map_props --map="$1" --vehicleid`

  # check if we got it.
  if $? -ne 0 ; then
    return 1
  fi

  # parse out the vehicle ID from the output string.
  temp_vehicle="`echo "$temp_vehicle" | sed 's/^.*=//'`"

  # set the ref value.
  eval "$2=\"$temp_vehicle\""
  # success
  return 0
}

# Check in the supported id file for the given ID.  If it exists, prompt the user
# and return 0.  If it does not exist, return 1.
#
# $1 the gui message to display.
# $2 the ecu id to look for.
# $3 (optional) 0 means prompt, 1 means don't prompt.  Default is to show the prompt.

check_if_supported_id()
{
  local SHOW_PROMPT=0
  local HIDE_PROMPT=1

  local message_id=$1
  local prompt=$SHOW_PROMPT

  if [ -n "$3" ] ; then
    prompt=$3
  fi

  if [ -z "$2" ] ; then
    # ahh, something bad is happening!  Abort!
    return 1
  fi

  local vehicle_id_to_test=$2
  local supported_ids_file="${AP_DATA}supported_ids.cfg"
  local other_part_nums=""
  
  # try to look up the id and store the value into other_part_nums, but don't die.
  get_named_out_val "$supported_ids_file" other_part_nums "$vehicle_id_to_test" 1

  if [ -n "$other_part_nums" ] ; then
    # we successfully got a non-empty value!  Yay!
    if [ $prompt -eq $SHOW_PROMPT ] ; then
      showInfo "$message_id" "$other_part_nums"
      getUserSelection
    fi

    return 0
  fi

  return 1
}

# Check in the supported id file for the given ID.  If it exists, prompt the user
# and return 0.  If it does not exist, return 1.
# 
# $1 the gui message to display.
# $2 the ecu id to look for.
check_and_notify_if_supported_id()
{
  check_if_supported_id $1 $2 0 # 0 means prompt
  return $?
}


# exit_normal - Exit the function call without reloading the GUI
exit_normal(){
exit $EXIT_NORMAL
}

# exit_reload_gui - Exit the function and reload the gui 
exit_reload_gui(){
exit $EXIT_RELOAD_GUI
}

# Takes a module ("TCM" or "ECU") and returns the install state of that module. Defaults to ECU
get_module_state() {
  if [ "$1" == "$MODULE_ID_TCM" ]; then
    return $AP_CM1_STATE
  else
    return $AP_STATE
  fi
}

# Get the vehicle variants for the vehicle
#
# A vehicle variant is any tuning strategy or ROM variation that is valid for a
#   vehicle ID (e.g. MAF, SD, CF, or _B).
# $1= vehicle ID
# output: A space separated list of vehicle variants stored in $variants
get_vehicle_variants()
{
  # get the vehicle variants
  get_cfg_val $1 variants
  variants=`echo "$variants" | sed 's/,/ /g'` # convert csv to spaces
  variants="$vehicle $variants" # add current vehicle to variants
}

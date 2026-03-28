#!/bin/sh
#
# make sure this is the same car we are married to
# flash the "stock" rom

# import useful stuff
. ./subaru_funcs.sh
. ./serialnumber.sh

# ERROR CODES for uninstall.sh
ERR_CP_VEHICLE_ROM=6004     #@ Failed to copy generic AccessPORT vehicle ROM for use
ERR_SET_AP_STATE=6006       #@ Failed to set uninstalled state in AccessPORT

# tmp files for data passing
TMP_IDENTIFY=/tmp/AP_IDENTIFY
TMP_ROM=/tmp/AP_ROM

sanity_check_env

# are you really sure you want to do this, really?
showInfo "UI_INFO_nge_message

  case $cpu in
    "HC16")
      # 11.75V-13.0V
      low_limit=11750
      high_limit=13000
      range_message="11.75V-13.0V"
      ;;
    *)
      # "SH7055" | "SH7055SF" | "SH7058" | "SH7059" | "SH7254" | "SH72544"
      # 11V-14V
      low_limit=11000
      high_limit=14000
      range_message="11.0V-14.0V"
      ;;
  esac

  voltage_check $low_limit $high_limit "$range_message"
}

# show the info screens prompting the user to connect initialization and test
# mode connectors
# pre-conditions: cpu and protocol must be defined
connectTestMode()
{
  # HC16 needs init mode
  if [ $cpu == "HC16" ]; then 
    showInfo "UI_INFO_CONN_INIT_MODE"
    getUserSelection
    if [ ! $? -eq 1 ]; then
      exit 0
    fi
  fi

  # CAN doesn't need test mode
  if [ "$protocol" == "SSMII" -o "$protocol" == "SSMIII" ]; then
    if [ $AP_STATE -eq 2 ]; then # recovery mode can't talk
      showInfo "UI_INFO_CONN_TEST_MODE"
      getUserSelection
      if [ ! $? -eq 1 ]; then
        exit 0
      fi
    else
      execute "./subaru_test --test_mode" "subaru_test --test_mode cancelled"
    fi
  fi
}

# show the info screens prompting the user to disconnect initialization
# and test mode connectors
# pre-conditions: cpu and protocol must be defined
disconnectTestMode()
{
  if [ $cpu == "HC16" ]; then
    showInfo "UI_INFO_DISC_INIT_MODE"
    getUserSelection
  fi

  if [ "$protocol" == "SSMII" -o "$protocol" == "SSMIII" ]; then
    showInfo "UI_INFO_DISC_TEST_MODE"
    getUserSelection
  fi
}

# Call subaru_test to verify basic info (ignition, engine, test mode)
# NOTE: can_mode is determined here and need not be supplied
# $1 - test args to subaru_test, e.g. "--ign_on --test_mode"
testConditions() {
  local ign_args="$1 $(get_comm_mode_args)"

  # call subaru_test and verify given conditions
  execute "./subaru_test $ign_args" "subaru_test $ign_args cancelled"
}

# Verify the car is on, waiting until it is
verifyIgnition() {
  testConditions "--ign_on"
}

# Gets the vehicle name and display it for the user (Troubleshooting Menu)
get_ecu_identity()
{
  local ident_args="--output=/tmp/AP_IDENTIFY --vehicle --more_info $(get_comm_mode_args)"
  ./subaru_identify $ident_args

  return $?
}

# Gets the vehicle id and returns it silently
get_ecu_identity_silent()
{
  local TMP_IDENTIFY=/tmp/AP_IDENTIFY
  local ident_args="--output=$TMP_IDENTIFY --quiet --vehicle --more_info $(get_comm_mode_args)"
  execute "./subaru_identify $ident_args"

  get_out_val $TMP_IDENTIFY vehicle 1
}

# Display preflash recommendations and warnings and verify some basic
#  conditions are correct preflash, or wait until they are.
# $1 - Additional test args for subaru_test, e.g. "--engine_off"
verifyFlashConditions() {
  local test_args="$1"

  # these tests will fail and cause the AP to hang if the ECU is in recovery!
  if [ $AP_STATE -ne 2 ]; then
    test_args="--ign_on --engine_off $test_args"
  fi

  # connect battery charger
  reqBattCharger $SINGLEMAP

  # HC16 needs init mode
  if [ $cpu == "HC16" ]; then 
    showInfo "UI_INFO_CONN_INIT_MODE"
    getUserSelection
    if [ ! $? -eq 1 ]; then
      exit 0
    fi
  fi

  # CAN doesn't need test mode
  if [ "$protocol" == "SSMII" -o "$protocol" == "SSMIII" ]; then
    if [ $AP_STATE -eq 2 ]; then # recovery mode can't talk
      showInfo "UI_INFO_CONN_TEST_MODE"
      getUserSelection
      if [ ! $? -eq 1 ]; then
        exit 0
      fi
    else
      test_args="$test_args --test_mode"
    fi
  fi

  # verify voltage is within our accepted range
  preflash_voltage_check

  # verify ignition on, engine off, and test mode connectors (if applicable)
  if [ -n "$test_args" ]; then
    testConditions "$test_args"
  fi
}

# Given the CPU and protocol, is a boot stub needed?
# pre-conditions:
#   $cpu must be defined
#   $protocol must be defined
# post-conditions:
#   $needs_stub= 1 if a stub is needed, 0 if not
#   $stub= "stub/path" if a stub is needed, "" if not
check_for_stub() {
  stub=""
  case $cpu in
    "HC16")
      stub="subaru_hc16_stub.bin"
      ;;
    "SH7055")
      stub="subaru_sh_stub_7055.bin"
      ;;
    "SH7055SF")
      stub="subaru_sh_stub_7055sf.bin"
      ;;
    "SH7058")
      if [ $protocol == "SSMIII" ]; then
        stub="subaru_sh_stub_7058.bin"
      elif [ $protocol == "SSMIV" ]; then
        stub="subaru_sh_stub_7058can.bin"
      elif [ $protocol == "SSMIV_ISO" ]; then
        stub="subaru_sh_stub_7058isocan.bin"
      fi
      ;;
  esac

  needs_stub=1
  case $protocol in
    "SSMV")
      needs_stub=0
      ;;
    "SSMVI")
      needs_stub=0
      ;;
    "LEVORG")
      needs_stub=0
      ;;
  esac

  if [ $needs_stub -eq 1 ]; then
    echo "stub= $stub"

    if [ -z "$stub" ]; then
      showInfo "UI_INFO_ERR_VEHICLE_NOT_SUPPORTED"
      getUserSelection
      exit 0
    fi

    # set the stub directory
    stub=${AP_DATA_STUBS}$stub

    if [ ! -e "$stub" ]; then
      showInfo "UI_INFO_ERR_VEHICLE_NOT_SUPPORTED"
      getUserSelection
      exit 0
    fi
  fi
}

# Perform any post-flash operations then show a success message.
#
# SSMII and SSMIII require a key-cycle to exit the bootstub.
# SSMIV/SSMIV_ISO can exit the bootstub by command, but requiring a key-cycle
#  won't hurt.
# SSMV requires a key-cycle to exit the bootloader.
#
# $1= A standard success message.
# pre-conditions: vehicle must be defined
flash_success() {
  local info_success="$1"

  # The GUI hasn't refreshed the EEPROM info yet so update the state manually to use
  #   the correct security key on the first try
  if [ $info_success == "UI_INFO_UNINSTALL_SUCCESS" ]; then
    ecu_state=$DS_UNINSTALLED
  elif [ $info_success == "UI_INFO_INSTALL_SUCCESS" ]; then
    ecu_state=$DS_INSTALLED
  fi

  # ask for a key-off
  showInfo "UI_INFO_IGN_OFF_RESET"
  getUserSelection

  disconnectTestMode

  # wait for a pre-set amount of time...
  showProgress "UI_WAIT_IGN_OFF_RESET"

  local OFF_DURATION=1

  if [ "$protocol" == "SSMIV" -o "$protocol" == "SSMIV_ISO" ]; then
    OFF_DURATION=5 # 5 seconds gives a short progress
  elif [ "$protocol" == "SSMV" -o "$protocol" == "SSMVI" -o "$protocol" == "LEVORG" ]; then
    OFF_DURATION=15 # 15 seconds allows 
  fi

  showTimedProgress $OFF_DURATION

  showInfo "UI_INFO_IGN_ON_RESET"
  getUserSelection

  verifyIgnition

  reset_realtime $vehicle

  reset_ecu $vehicle

  # if LEVORG, call identify again. This writes bootloader info to the flash log without prompting the user.
  TMP_IDENTIFY=/tmp/AP_IDENTIFY
  levorgIdentArgs="--output=$TMP_IDENTIFY --bootloader -x -q"
  if [ $AP_MODEL -eq 6 -o $AP_MODEL -eq 7 ]; then
    execute "./subaru_identify $levorgIdentArgs"
  fi
  showInfo "$info_success"
  getUserSelection
}

# Get any alternate fast flash and checksum correction locations
# output: to be inserted as a command line arg to subaru_flash
get_offset_locations()
{
  local offset_args
  # check for alternate fast flash and checksum correction location
  get_cfg_val $vehicle checksum_offset
  if [ -n "$checksum_offset" ]; then
    offset_args="--checksum_offset=$checksum_offset"
  fi
  get_cfg_val $vehicle blocksum_offset
  if [ -n "$blocksum_offset" ]; then
    offset_args="$offset_args --blocksum_offset=$blocksum_offset"
  fi

  echo "$offset_args"
}

# Get the name of the base ROM used by consolidation ROMs.
# $1= vehicle ID
# $2= output variable
# output: If a ROM is consolidated, the base ROM name is assigned to $2. If a
#   ROM is not consolidated, the vehicle ROM name is assigned to $2.
get_base_rom()
{
  local base_vehicle
  # use the base vehicle as a reference, if available, otherwise use the vehicle
  get_cfg_val $1 base
  if [ -n "$base" ]; then
    base_vehicle=$base
  else
    base_vehicle=$1
  fi
  eval "$2=${AP_DATA_ROMS}${base_vehicle}.rom"
}

# Patch a base ROM with a consolidation patch to create a consolidated ROM.
# $1= vehicle ID
# $2= output variable
# output: If a ROM is consolidated, it is stored to $TMP_ROM and the path is
#   assigned to $2. If a ROM is not consolidated, $2 is unchanged.
patch_cons_rom()
{
  get_cfg_val $1 cons_patch
  echo "cons_patch= $cons_patch"
  if [ ! -z "$cons_patch" ]; then
    get_cfg_val $1 base
    echo "base= $base"
  
    local BASE_ROM="${AP_DATA_ROMS}${base}.rom"
    local PATCH_PTM=${AP_DATA_ROMS}/$base/${cons_patch}_patch.ptm
    echo "BASE_ROM= $BASE_ROM"
    echo "PATCH_PTM= $PATCH_PTM"

    execute "make_vehicle_rom $BASE_ROM $PATCH_PTM $TMP_ROM " "make_vehicle_rom failed"

    eval "$2=\"$TMP_ROM\""    # This assigns our value to $1
  fi
}

# Get the correct stock ROM for the vehicle ID.
# For some late-model ROMs, and all SD ROMs, the vehicle id is NOT the same as
#   the stock vehicle ROM.
# $1= vehicle ID
# output: A true stock ROM is either created or copied to $TMP_ROM.
get_true_stock_rom() {
  local STOCK_ROM
  #2013 STI is a special case where we use a modified '12 rom. In mass failure we need the actual stock rom.
  # Be sure to add in the ID and any variants when modifying this
  case "$1" in
    SUBA_US_WRXM_06 | SUBA_US_WRXM_SD_06 | SUBA_US_WRXM_CF_06 | \
    SUBA_US_WRXA_06 | SUBA_US_WRXA_SD_06 | SUBA_US_WRXA_CF_06 | \
    SUBA_US_FXTM_06 | SUBA_US_FXTM_SD_06 | SUBA_US_FXTM_CF_06 | \
    SUBA_US_FXTA_06 | SUBA_US_FXTA_SD_06 | SUBA_US_FXTA_CF_06 | \
    SUBA_US_STI_13 | SUBA_US_STI_SD_13 | SUBA_US_STI_CF_13 | \
    SUBA_US_WRXM_13 | SUBA_US_WRXM_SD_13 | SUBA_US_WRXM_CF_13 | \
    SUBA_US_FXTA_13 | SUBA_US_FXTA_SD_13 | \
    SUBA_US_WRXM_14 | SUBA_US_WRXM_SD_14 | SUBA_US_WRXM_CF_14 | \
    SUBA_US_STI_14 | SUBA_US_STI_SD_14 | SUBA_US_STI_CF_14 | \
    SUBA_US_STI_15 | SUBA_US_STI_SD_15 | SUBA_US_STI_CF_15)
      STOCK_ROM="${1}${TRUE_SUFFIX}" ;;
    *)
      STOCK_ROM=${1} ;;
  esac

  # variant vehicles need the variantless ROM (stock) on uninstall
  if echo $STOCK_ROM | grep -q "$SD_SUFFIX"; then
    STOCK_ROM=`echo $STOCK_ROM | sed -n "s/\(.*\)$SD_SUFFIX\(.*\)/\1\2/p"`
  fi
  if echo $STOCK_ROM | grep -q "$CF_SUFFIX"; then
    STOCK_ROM=`echo $STOCK_ROM | sed -n "s/\(.*\)$CF_SUFFIX\(.*\)/\1\2/p"`
  fi

  echo "STOCK_ROM= $STOCK_ROM"

  # Not all true ROMs are consolidated. If a config exists, get the
  #   consolidation details and deconsolidate.
  local CONS_ROM
  local STOCK_CFG=${AP_CFG_PATH}/${STOCK_ROM}.cfg
  echo "STOCK_CFG= $STOCK_CFG"
  if [ -e $STOCK_CFG ]; then
    patch_cons_rom $STOCK_ROM CONS_ROM
  fi

  echo "CONS_ROM= $CONS_ROM"

  # if no ROM consolidation is present, get a non-consolidated copy
  if [ -z "$CONS_ROM" ]; then
    STOCK_ROM=${AP_DATA_ROMS}/${STOCK_ROM}.rom
    echo "STOCK_ROM= $STOCK_ROM"
    cp $STOCK_ROM $TMP_ROM
  fi
}

# Determine if the Subaru supports realtime. If not defined, the default is a
#   resounding YES!
# $1= vehicle identifier
# variables exported, if not already defined: using_realtime
get_using_realtime() {
  if [ -z "$using_realtime" ]; then
    get_cfg_val $1 using_realtime
    if [ -z "$using_realtime" ]; then
      using_realtime="yes"
    fi
  fi
}

# Generate a string of feature arguments to eventually pass into
#  other binaries that will use them.
# $1 name of the binary to build an argument list for
# Prereqs: Any requested versions are preloaded (e.g. ram_version, ff_a_b)
# Note: map_select expects an ordered list of features. Features will be
#       evaluated (and failed) in the order given!
# Note: Subaru feature toggles use: Off = -1 (0xFF) and On = 0 (0x00)
get_feature_args() {
  feature_args=""

  # Note: Map_select expects an ordered list of features to compare against a
  #  map. Features are evaluated in the order provided. The first non-equality
  #  encountered and map_select will invalidate the map for realtime!
  if [ "$1" == "map_select" ]; then
    if [ -n "$ram_version" ]; then
      # ram_version of NULL is a backwards compatible "show everything"
      # ram_version of 0 is no realtime maps
      # ram_version of > 0 is a realtime map that must match exactly
      if [ $ram_version -ne -1 ]; then
        feature_args="$feature_args --feature=ram_version=$ram_version"
        if [ -n "$ff_version" ]; then
          feature_args="$feature_args --feature=ff_version=$ff_version"
          if [ -n "$ff_activate" ]; then
            feature_args="$feature_args --feature=ff_activate=$ff_activate"
          fi
        fi
      fi
    fi
  # Note: subaru_identify is supplied all known feature addresses and queries
  #  them from the ECU for later use.
  elif [ "$1" == "subaru_identify" ]; then
    # feature versions (include a check value)
    if [ -n "$ram_version_addr" ]; then
      feature_args="$feature_args --feature_version=$ram_version_addr"
    fi
    if [ -n "$ff_version_addr" ]; then
      feature_args="$feature_args --feature_version=$ff_version_addr"
    fi
    if [ -n "$lc_version_addr" ]; then
      feature_args="$feature_args --feature_version=$lc_version_addr"
    fi
    if [ -n "$can_version_addr" ]; then
      feature_args="$feature_args --feature_version=$can_version_addr"
    fi
    if [ -n "$cangw_version_addr" ]; then
      feature_args="$feature_args --feature_version=$cangw_version_addr"
    fi
    # feature toggles (no check value)
    if [ -n "$ff_activate" ]; then
      feature_args="$feature_args --feature_toggle=$ff_activate"
    fi
    if [ -n "$ff_a_b" ]; then
      feature_args="$feature_args --feature_toggle=$ff_a_b"
    fi
  elif [ "$1" == "lca_identify" ]; then
    # feature versions (include a check value)

    # feature toggles (no check value)
    if [ -n "$lca_ign_toggle_lc_addr" ]; then
      feature_args="$feature_args --feature_toggle=$lca_ign_toggle_lc_addr"
    fi
    if [ -n "$lca_ign_toggle_ffs_addr" ]; then
      feature_args="$feature_args --feature_toggle=$lca_ign_toggle_ffs_addr"
    fi
    if [ -n "$lca_ign_toggle_rlc_addr" ]; then
      feature_args="$feature_args --feature_toggle=$lca_ign_toggle_rlc_addr"
    fi
    # NOTE: While the LCA boost offset min/max aren't technically toggles they work the same way
    if [ -n "$lca_bst_adj_min_addr" ]; then
      feature_args="$feature_args --feature_toggle=$lca_bst_adj_min_addr"
    fi
    if [ -n "$lca_bst_adj_max_addr" ]; then
      feature_args="$feature_args --feature_toggle=$lca_bst_adj_max_addr"
    fi
  else
    echo "error: get_feature_args doesn't know how to handle [$1]"
  fi
}

# Query the ECU for a feature's version.
#  -1 = no cable attached
#   0 = no platform support or not installed
#   > 0 support and version number!
# $1= vehicle identifier
# $2= feature to grab (same as command line option for identify!)
# $3= default value (optional, 1 if unset)
# feature_version will be exported and assigned the return value.
get_feature_version() {
  local TMP_IDENT=/tmp/AP_IDENT # ensure this exists!
  local vehicle_id="$1"
  local feature_arg="--feature_version"
  feature_version=-1 # default to unset/invalid
  get_cfg_val $vehicle_id $2 # get the feature version address
  eval "local feature_addr=\$$2" # double dereference $2

  if [ ! -z "$feature_addr" ]; then
    # An address was found! Ask the ECU for versioning info...
    local ident_args="--state=$AP_STATE $(get_comm_mode_args $vehicle_id)"

    if obd_cable_present; then
      if [ "$3" == "toggle" ]; then
        feature_arg="--feature_toggle"
      fi
      execute "./subaru_identify $ident_args --output=$TMP_IDENT --quiet $feature_arg=$feature_addr" "subaru_identify --$2 cancelled"
      # reformat the identify output into usable ky/value pairs
      feature_version=`sed -n "s/^$feature_addr=//p" < "$TMP_IDENT"`
      rm $TMP_IDENT # no sys_exec
    fi
  else
    if [ ! -z "$3" ]; then
      # feature wasn't found, use provided default value!
      feature_version="$3"
    else
      # feature not found, BUT old, non-CCF ECUs support everything
      feature_version=1
    fi
  fi
}

get_feature_toggle() {
  get_feature_version "$1" "$2" "toggle"

  if [ $feature_version -eq 0 ]; then
    feature_toggle=1
  elif [ $feature_version -eq -1 ]; then
    feature_toggle=0
  else
    feature_toggle="invalid"
  fi
}

# SUB-003/4/~5 and newer default to CAN mode, SUB-005 defaults to isocan_mode
# SUB-006 is --levorg_mode
# $1 = vehicle ID to get comms mode for
get_comm_mode_args(){
  local vehicle_id="$1"
  local comm_mode_args=""
  if [ $AP_MODEL -eq 6 -o $AP_MODEL -eq 7 ]; then
    # 22+ WRX and 22+ OBXT use different channels for comms
    comm_mode_args="--levorg_mode"
  fi
  if [ $AP_MODEL -eq 5 ]; then
    if [ -z "$vehicle_id" ]; then
      # use the incoming vehicle if one isn't provided
      if [ -z "$vehicle" ]; then
        vehicle_id="$AP_VEHICLE"
      else
        vehicle_id="$vehicle"
      fi
    fi

    if [ "$vehicle_id" != "?" ]; then
      get_cfg_val $vehicle_id protocol
    fi

    if [ "$protocol" == "SSMV" ]; then
      # Ascent special case handling
      comm_mode_args="--can_mode"
    else
      comm_mode_args="--isocan_mode"
    fi
  elif [ $AP_MODEL -eq 3 -o $AP_MODEL -eq 4 ]; then
    # default SUB-003/4 to can_mode if no vehicle provided
    if [ -z "$vehicle_id" ]; then
      comm_mode_args="--can_mode"
    else
      if [ "$vehicle_id" != "?" ]; then
        get_cfg_val $vehicle_id protocol
      fi

      if [ "$protocol" == "SSMV" ]; then
        comm_mode_args="--can_mode"
      fi
    fi
  fi

  echo "$comm_mode_args"
}

# Reset realtime and launch control, if applicable
# $1= vehicle identifier
# variables exported, if not already defined: protocol, cpu, rt_chk_addr,
#   lc_hi_addr, and ecu_state
reset_realtime() {
  get_using_realtime $1

  if [ "$using_realtime" == "yes" ]; then
    if [ -z "$protocol" ]; then
      get_cfg_val $1 protocol
    fi

    local comms_args=""

    # older protocols use KLINE for reset still!
    if [ "$protocol" == "SSMV" -o "$protocol" == "SSMVI" ]; then
      comms_args="$(get_comm_mode_args)"
    fi

    if [ -z "$cpu" ]; then
      get_cfg_val $1 cpu
    fi

    if [ -z "$rt_chk_addr" ]; then
      get_cfg_val $1 rt_chk_addr
    fi

    if [ -z "$ecu_state" ]; then
      ecu_state=$AP_STATE
    fi

    # clear learning and realtime
    execute "./subaru_reset --cpu=$cpu --state=$ecu_state --offset=$rt_chk_addr $comms_args" "subaru_reset cancelled"

    # clear lc/ffs
    if [ -z "$lc_hi_addr" ]; then
      get_cfg_val $1 lc_hi_addr
    fi

    if [ -n "$lc_hi_addr" ]; then
      execute "./lc_adj --cpu=$cpu --state=$ecu_state --upper_addr=$lc_hi_addr --reset --quiet $comms_args"
    fi
  fi

}

# Reset ECU learning and CAN modules, if applicable
# $1= vehicle identifier
# variables exported, if not already defined: protocol
reset_ecu() {
  local vehicle_id="$1"

  if [ -z "$protocol" ]; then
    get_cfg_val $vehicle_id protocol
  fi

  #Step 1 DTC Reset
  #Step 2 ECU Reset
  #Levorg use exec_util
  #everything else see other comments
  if [ "$protocol" == "LEVORG" ]; then
    # --no_confirm shows no success prompt after reset
    execute "./exec_util ClearCodes --no_confirm" "exec_util ClearCodes cancelled"
    sleep 2
    execute "./exec_util BroadcastModeFourteen_ClearDiagnosticInfo --no_confirm" "exec_util BroadcastModeFourteen_ClearDiagnosticInfo cancelled"
    sleep 2
  else
    local dtc_reset_args="$(get_comm_mode_args)"
    local ecu_reset_args="$(get_comm_mode_args $vehicle_id)"

    # DTC RESET:
    # SSMVI: --isocan_mode - MODE 14 CAN-bus DTC reset
    # SSMIV, SSMIV_ISO, SSMV: --can_mode - MODE 14 CAN-bus DTC reset
    # SSMII, SSMIII (kline): - Do nothing here!
    if [ "$protocol" == "SSMIV" -o "$protocol" == "SSMIV_ISO" -o "$protocol" == "SSMV" -o "$protocol" == "SSMVI" ]; then
      execute "./subaru_reset $dtc_reset_args --dtc" "subaru_reset cancelled"
    fi

    # Step 2 - ECU RESET:
    # SSMVI: --isocan_mode = MODE 04 CAN-bus reset
    # SSMV: --can_mode = MODE 04 CAN-bus reset + SSM 0x60 over CAN
    # Others: (kline) = SSM 0x60 reset over KLINE
    execute "./subaru_reset $ecu_reset_args --ecu" "subaru_reset cancelled"
  fi
}

# Nag the user if they are flashing an outdated map!
# $1= vehicle identifier
# note: a cancel will abort the flash
deprecated_warning_check() {
  get_cfg_val $1 recalled
  if [ "$recalled" == "yes" ]; then 
    showInfo "UI_INFO_WARN_MAP_RECALLED"
    getUserSelection
    if [ ! $? -eq 1 ]; then
      exit 0
    fi
  fi

  #NOTE: add additional deprecated checks and warnings here!
}

# examine a ROM for checksum correctness
# $1 = path to ROM to check
# sets BAD_USER_ROM=1 on detection of a bad ROM
check_rom_integrity() {
  local tmp_chk_rom=/tmp/CHK_ROM
  local rom="$1"

  if [ -e "$rom" ]; then
    execute "./subaru_flash --cpu=$cpu --protocol=$protocol --checksum --file=$rom --output=$tmp_chk_rom" "subaru_flash cancelled"
    if [ -e "$tmp_chk_rom" ]; then
      orig_sum=`md5sum < "$rom"`
      new_sum=`md5sum < "$tmp_chk_rom"`
      rm "$tmp_chk_rom" # don't need this anymore

      if [ "$orig_sum" == "$new_sum" ]; then
        echo "$rom checksum is good!"
      else
        # the user can bail out at this point to preserve the current tune
        showInfo "UI_INFO_USER_ROM_BAD_CHECKSUMS"
        getUserSelection
        if [ ! $? -eq 1 ]; then
          rm "$rom" #cleanup!
          exit 0
        else
          # treat the install as an over-install going forward
          PREV_INSTALL=1
          BAD_USER_ROM=1
        fi
      fi
    fi
  else 
    echo "no user rom? nothing dumped, nothing to do"
  fi
}

# Check SUB-004 ECUs ONLY for the presence of a valid ram version and thus
#   realtime support
# set using_realtime=no if ECU lacks support
# NOTE: initiates security access with the ECU and possible undesirable side effects
test_for_realtime() {
  if [ -e "$USER_REFLASH_PROPS" ]; then
    get_out_val $USER_REFLASH_PROPS ff_activate
  else
    cache_reflash_props
    get_out_val $USER_REFLASH_PROPS ff_activate
  fi

  # realtime is disabled on maps with FF active
  if [ $ff_activate -eq 0 ]; then
    using_realtime=no
  fi

  if [ "$AP_MODEL" -eq 4 ]; then
    get_cfg_val $AP_VEHICLE protocol
    if [ "$protocol" == "SSMV" ]; then
      # Verify realtime for the log header for these ECUs!
      if [ $using_realtime == "yes" ]; then
        get_feature_version $AP_VEHICLE ram_version_addr
        if [ $feature_version -le 0 ]; then
          using_realtime=no
        fi
      fi
    fi # [$protocol == "SSMV"]; then
  fi #[ "$AP_MODEL" -eq 4 ]; then
}

ShowDashlightWarningMessage(){
  if [ $AP_MODEL -eq 5 ]; then
    showInfo "UI_INFO_WARN_DASHLIGHTS"
    getUserSelection

    if [ $? -le 0 ]; then
      exit 0
    fi
  fi
}

GetRecalledVehicleIDParameters(){
  local recalled_vehicles_file="$AP_DATA/recalled_vehicles.cfg"

  if [ -e "$recalled_vehicles_file" ]; then
    local vehicle_ids=`cat "$recalled_vehicles_file"`
    for vehicle_id in $vehicle_ids; do
      local recalledVehicleParameters="$recalledVehicleParameters --recalled_vehicleID=$vehicle_id"
    done
  fi

  echo "$recalledVehicleParameters"
}
cache_reflash_props(){
  user_remount_rw
  sys_exec "./map_props --feature --vendor --vendorid -m$USER_REFLASH_MAP > $USER_REFLASH_PROPS" "map_props failed" $ERR_MAP_PROPS
  user_remount_ro
}

VerifyRealtimeIfVendorMistmatch(){
  local realtime_vendor="$1"
  local ecu_vendor_id="$2"

  if [ -e "$USER_REFLASH_PROPS" ]; then
    get_named_out_val $USER_REFLASH_PROPS reflash_vendor vendor
    get_named_out_val $USER_REFLASH_PROPS reflash_vendor_id vendorid
    #These fields might be empty if the modifid call to map_props with additional arguments hasn't been called yet on this AP
    if [ -z "$reflash_vendor" -o -z "$reflash_vendor_id" ]; then
      cache_reflash_props
      get_named_out_val $USER_REFLASH_PROPS reflash_vendor vendor
      get_named_out_val $USER_REFLASH_PROPS reflash_vendor_id vendorid
    fi
  else
    cache_reflash_props
    get_named_out_val $USER_REFLASH_PROPS reflash_vendor vendor
    get_named_out_val $USER_REFLASH_PROPS reflash_vendor_id vendorid
  fi

  if [ "$reflash_vendor" != "$realtime_vendor" ]; then

    #If the vendor ID the ECU reports is different from the vendor ID the reflash map reports this is unintended behavior
    if [ "$ecu_vendor_id" != "$reflash_vendor_id" ]; then
      reflash_vendor_id="Unknown"
    fi

    showInfo "UI_INFO_WARN_REFLASH_MISMATCH" "Reflash: $reflash_vendor" "Realtime: $realtime_vendor"
    getUserSelection
    if [ $? -eq 0 ]; then
        exit 0
    fi
  fi
}

disable_eod()
{
  set_settings_val eod_setting 0
  # immediately disable the feature as well
  echo 0 > /sys/dx_pm/eod
}

# Disable any AP features this particular ECU doesn't support
# $1 = optional vehicle ID (expects $vehicle instead)
verify_vehicle_features()
{
  local vehicle_id="$vehicle"
  if [ ! -z "$1" ]; then
    vehicle_id="$1"
  fi

  # 1. Does this ECU support EOD?
  get_cfg_val "$vehicle_id" disable_eod
  if [ "$disable_eod" == "true" ]; then
    # set everything to off!
    disable_eod
  fi
}

# Perform a ROM patch based off a patch .cfg file to adjust tables based on
#  GUI input (based off the Mazda implementation of this feature!)
#
# Input:  $1 = $TMP_ROM - temp ROM location
#         $2 = $base - vehicle base ID
#         $3 = 1/0; tells the script to look up the previous value and re-use
#         $4 = $CHANGE_MAP - the old map name
#         $5 = Patch file name (e.g. PATCH_MAZ-002_FFS.cfg)
#         $6 = 1/0; Show a busy screen for large patches
#         $7 = 1/0; No GUI (for use when calling multiple patches with the same
#               user input
subaru_basic_patch(){
  local temp_rom="$1"
  local patch_vehicle_id="$2"
  local use_old_value="$3"
  local old_mapname="$4"
  local patch_file="$5"
  local show_busy="$6"
  local no_gui="$7"
  local default_value
  local min_value
  local max_value
  local increment
  local patch_error
  local patch_units
  local patch_short_name
  local patch_long_name

  # need to verify patch file is present and legit
  echo "verifying patch file..."
  ./patch_rom --patch="$patch_file" --input="$temp_rom" --output="$temp_rom" --vehicle="$patch_vehicle_id" --patch_options=0 --verify --error_message="$patch_error"
  local ret="$?"
  get_out_val "$patch_file" default_value 1
  get_out_val "$patch_file" patch_ui_control 1
  get_out_val "$patch_file" min_value 1
  get_out_val "$patch_file" max_value 1
  get_out_val "$patch_file" increment 1
  get_out_val "$patch_file" patch_error 1
  get_out_val "$patch_file" patch_units 1
  get_out_val "$patch_file" patch_short_name 1
  get_out_val "$patch_file" patch_long_name 1

  # must verify the patch file has everything we need to continue
  if [ "$ret" -eq 0 -a -n "$default_value" -a -n "$patch_ui_control" -a -n "$min_value" -a -n "$max_value" -a -n "$increment" -a -n "$patch_error" -a -n "$patch_units" -a -n "$patch_short_name" -a -n "$patch_long_name" ]; then
    if [ "$use_old_value" -eq 1 ]; then
      get_out_val "$AP_USER_MAPNAMES" reflash 1 # don't err_die!
      get_out_val "$AP_USER_MAPNAMES" patched_short_desc 1 # don't err_die!
      # if get_out_val fails, then the below should fail too and we're ok
      if [ "$old_mapname" == "$reflash" ]; then
        if [ "${patched_short_desc/"$patch_short_name"}" != "$patched_short_desc" ]; then
          echo "$patch_short_name was used last time, [$patched_short_desc]"
          default_value=`echo "$patched_short_desc" | sed s/[^0-9]*//g`
        fi
      fi
    fi

    # don't show the gui if requested
    if [ "$no_gui" -ne 1 ]; then
      # IMPLEMENT WARNING SCEEN HERE
      #showInfo "UI_INFO_WARN_FIXED_ETHANOL"
      showSpinner "$patch_ui_control" $default_value $min_value $max_value $increment "$patch_units"
      local USER_INPUT=""
      getUserSelection
      patch_ret="$?"
      #preserve this input going forward...
      PATCH_USER_INPUT="$USER_INPUT"
    fi

    if [ "$patch_ret" -eq 1 -a "$PATCH_USER_INPUT" -ne 0 ]; then
      echo "$patch_short_name adjustment: got '$PATCH_USER_INPUT'"
      if [ "$show_busy" -eq 1 ]; then
        showBusy "UI_WAIT_BUILDING_FLASH"
      fi
      # thunder patching is go!
      ./patch_rom --patch="$patch_file" --input="$1" --output="$1" --vehicle="$2" --patch_options="$PATCH_USER_INPUT"
      if [ $? -ne 0 ]; then # ENTER
        echo "oh god something went wrong - ./patch_rom FAILED"
        # ROM may be tainted by patch_rom at this point! bail out!
        rm $1 # delete the TMP_ROM
        exit 0
      fi
      # only append on the first operation of a multi-patch
      if [ "$no_gui" -ne 1 ]; then
        # append to the new descriptions!
        local new_long_desc

        if [ "$PATCH_USER_INPUT" -gt 0 ]; then
          PATCHED_SHORT_DESC="${PATCHED_SHORT_DESC}, $patch_short_name${pos_sign}${PATCH_USER_INPUT}$patch_units"
          new_long_desc="$patch_long_name ${pos_sign}${PATCH_USER_INPUT}$patch_units"
        else
          PATCHED_SHORT_DESC="${PATCHED_SHORT_DESC}, $patch_short_name${PATCH_USER_INPUT}$patch_units"
          new_long_desc="$patch_long_name ${PATCH_USER_INPUT}$patch_units"
        fi

        if [ -z "$PATCHED_LONG_DESC" ]; then
          PATCHED_LONG_DESC=" ACTIVE ADJUSTMENTS: $new_long_desc"
        else
          PATCHED_LONG_DESC="${PATCHED_LONG_DESC}, $new_long_desc"
        fi
      fi
    else
      # user cancel, user zero, or carryover cancel/zero
      echo "$patch_short_name adjustment: User entered 0 or cancelled, don't adjust! Don't flash!"
      # ABORT DO NOT FLASH, remove ALL temp files
      rm $tmp_patch_file
      rm $tmp_read_results
      rm $temp_rom
      rm $TMP_MAP_PROPS
      exit 0
    fi
  else # patch_rom failed with an error!
    # patch_rom handles the showInfo for errors. We haven't tainted the ROM yet,
    #  so let's go ahead and continue the script. Users can bail out at the battery
    #  charger screen if desired.
    echo "patch_rom failed! critical data missing in file [$ret]"
  fi
}

# Note: Subaru feature toggles use: Off = -1 (0xFF) and On = 0 (0x00)
subaru_fixed_ethanol_adjustments()
{
  local patch_file=`printf "PATCH_SUB-%03d_FE.cfg" $AP_MODEL`
  local tmp_patch_file="/tmp/$patch_file"
  local tmp_read_results="/tmp/ROM_READ"
  local patch_base="$1"

  # we don't allow these features with pass-through flashing!
  if [ -z "$SINGLEMAP" ]; then
    # check the map for support!
    get_out_val $TMP_MAP_PROPS ff_activate
    get_out_val $TMP_MAP_PROPS ff_version
    get_out_val $TMP_MAP_PROPS ff_fixed_activate
    get_out_val $TMP_MAP_PROPS ff_fixed_default
    get_out_val $TMP_MAP_PROPS ff_fixed_min
    get_out_val $TMP_MAP_PROPS ff_fixed_max
    #read supported version from the patch file
    get_out_val "$AP_CFG_PATH/$patch_file" supported_version 1

    # 1: check for support!
    if [ $ff_activate -eq 0 -a $ff_fixed_activate -eq 0 -a $ff_version -eq $supported_version ]; then
      # 2: try to read the min, max, start values from the ROM!
      cp "$AP_CFG_PATH/$patch_file" "/tmp/$patch_file"

      # sanity test input!
      if [ -z "$ff_fixed_max" -o -z "$ff_fixed_min" -o -z "$ff_fixed_default" ]; then
        echo "missing fixed ethanol addresses, abort FE adjust!"
        showInfo "UI_INFO_ERR_PATCH_FAILED"
        getUserSelection
        exit 1
      fi
      if [ ! -f "$tmp_patch_file" ]; then
        echo "patch file missing! abort FE adjust!"
        showInfo "UI_INFO_ERR_PATCH_FAILED"
        getUserSelection
        exit 1
      fi

      #need to fetch min/max/default from the ROM!
      #MAX
      execute "./patch_rom --patch=$tmp_patch_file --input=$TMP_ROM --output=$tmp_read_results --vehicle=$patch_base --read --address=$ff_fixed_max"
      local spin_max=`sed 's/^[^,]*,//' $tmp_read_results`
      set_out_val $tmp_patch_file ${patch_base}_maxValue $spin_max
      set_out_val $tmp_patch_file max_value $spin_max
      #MIN
      execute "./patch_rom --patch=$tmp_patch_file --input=$TMP_ROM --output=$tmp_read_results --vehicle=$patch_base --read --address=$ff_fixed_min"
      local spin_min=`sed 's/^[^,]*,//' $tmp_read_results`
      set_out_val $tmp_patch_file ${patch_base}_minValue $spin_min
      set_out_val $tmp_patch_file min_value $spin_min
      #START
      execute "./patch_rom --patch=$tmp_patch_file --input=$TMP_ROM --output=$tmp_read_results --vehicle=$patch_base --read --address=$ff_fixed_default"
      local spin_start=`sed 's/^[^,]*,//' $tmp_read_results`
      set_out_val $tmp_patch_file default_value $spin_start

      set_out_val $tmp_patch_file ${patch_base}_tableOffset $ff_fixed_default

      # fixed ethanol adjustment
      subaru_basic_patch "$TMP_ROM" "$patch_base" 1 "$CHANGE_MAP" "$tmp_patch_file" 1 0
    else
      echo "Map does NOT support fixed ethanol adjustments"
    fi
  fi

  #remove patching temp files
  rm $tmp_patch_file
  rm $tmp_read_results

  echo "$PATCHED_SHORT_DESC"
  echo "$PATCHED_LONG_DESC"
}

# Uses the get_true_stock_rom function and the
# uninstall_vehicle cfg entry for a vehicle ID ($1) to
# set the stock uninstall ROM into $TMP_ROM
#
# $1 - vehicle ID. If no argument is passed, $vehicle is used
#
# sets $TMP_ROM
fetch_uninstall_rom() {
  local uninstall_vehicle
  local arg_vehicle

  if [ -z "$1" ]; then
    # no arg passed, use $vehicle
    arg_vehicle="$vehicle"
  else
    # use passed in vehicle for lookup
    arg_vehicle="$1"
  fi

  # Check if we have a different uninstall vehicle than we detected
  get_cfg_val $arg_vehicle uninstall_vehicle

  if [ ! -z "$uninstall_vehicle" ]; then
    # use alternate $uninstall_vehicle
    arg_vehicle="$uninstall_vehicle"
  fi

  sys_exec "get_true_stock_rom \"$arg_vehicle\"" "rom file copy failed" $ERR_CP_VEHICLE_ROM
}

# dump_and_save_rom argument options:
# $5 = ap_state
#   - Default value: None (required argument)
      AP_UNINSTALLED="uninstalled"
      AP_UNKNOWN_ROM="unknown_rom"
# $6 = $full_dump_allowed
#   - Default value: FULL_DUMP_ALLOWED
      FULL_DUMP_ALLOWED=1
      PARTIAL_DUMP_ONLY=0
dump_and_save_rom(){
  local cpu="$1"
  local stub_arg="$2"
  local protocol="$3"
  local calid="$4"
  local ap_state="$5"
  local full_dump_allowed="$6"

  if [ "$AP_UNINSTALLED" != "$ap_state" -a "$AP_UNKNOWN_ROM" != "$ap_state" ]; then
    echo "Invalid ap state provided!"
    exit 1
  fi

  ERR_MV_USER_ROM=2003 #@ Failed to move user rom
  TMP_ROM=/tmp/AP_ROM
  USER_DUMP=/user/ap-user/dump/
  TMP_STUBRATE=/tmp/ECU_STUBRATE
  ENCKEY=$RANDOM # generate the stub encryption key
  PARTIAL='_partial'

  if [ -z "$full_dump_allowed" ]; then
    full_dump_allowed="$FULL_DUMP_ALLOWED"
  fi

  # Dump the ROM
  execute "./subaru_flash --$ap_state --cpu=$cpu --dump $stub_arg --output=$TMP_ROM --partial=$PARTIAL --protocol=$protocol --enckey=$ENCKEY --save_rate_to=$TMP_STUBRATE" "subaru_flash cancelled"
  
  if [ "$protocol" == "LEVORG" ]; then
  # The argument must be set to something (non-empty) for the call to reset_ecu to run as intended
    reset_ecu "placeholder_value"
  fi
  rm $TMP_STUBRATE
  partial_dump_occured=false
  tmp_partial_rom="$TMP_ROM$PARTIAL"
  if [ -f "$tmp_partial_rom" ]; then
    partial_dump_occured=true
    TMP_ROM=$tmp_partial_rom
  elif [ "$PARTIAL_DUMP_ONLY" == "$full_dump_allowed" ]; then
    # This is a safeguard aganinst a full ROM dump happening on install
    echo "Error! Only a partial ROM dump is allowed on install"
    showInfo "UI_INFO_ERR_DUMP_FAILED"
    getUserSelection
    exit 1
  fi

  if [ $protocol == "SSMII" -o $protocol == "SSMIII" ]; then
    calid=`./subaru_rom_validator --cpu=$cpu --fwid=ffffffff --snoffset=0x1 --input=$TMP_ROM`
    chkret $? "subaru_rom_validator cancelled"
  fi

  echo "calid= $calid"
  # Save the user ROM
  showBusy "UI_WAIT_SAVING_ROM"
  ./rom_encrypter ENC $TMP_ROM
  rm "$TMP_ROM"

  user_remount_rw
  prepare_user_folders
  TMP_ROM=${TMP_ROM}_enc.rom

  rom_name=""
  if $partial_dump_occured; then
    rom_name=$USER_DUMP/${calid}${PARTIAL}_enc.rom
  else
    rom_name=$USER_DUMP/${calid}_enc.rom
  fi

  sys_exec "mv $TMP_ROM $rom_name" "mv failed" $ERR_MV_USER_ROM
  user_remount_ro
}

# shows the AP test menu
# no arguments
execute_ap_test_menu() {
  initializeNamedIndexList TEST_TOOLS
  addNamedIndexItem "UI_ITEM_GENERATE_MONITOR_CFG"
  showList "UI_LIST_DEBUG_TESTS"
  getUserSelections TEST_TOOLS

  if [ $? -le 1 ]; then #cancel?
    exit 0
  fi

  case $TEST_TOOLS in
    $UI_ITEM_GENERATE_MONITOR_CFG)
      echo "Generating cfgs"
      showBusy "UI_WAIT_GENERATE_MONITOR_CFG_IN_PROGRESS"
      ./subaru_live_data --generate_cfgs
      showInfo "UI_HELP_INFO_GENERATE_MONITOR_CFG_COMPLETE"
      getUserSelection
      ;;
    $UI_ITEM_GENERATE_MONITOR_CFG)
      echo "Generating cfgs"
      showBusy "UI_WAIT_GENERATE_MONITOR_CFG_IN_PROGRESS"
      ./subaru_live_data --generate_cfgs
      showInfo "UI_HELP_INFO_GENERATE_MONITOR_CFG_COMPLETE"
      getUserSelection
      ;;
    *)
      "Unknown selection!"
      exit 0
      ;;
  esac
}

# Opens a new flash log and writes bootloader info. 
# Does nothing if not on levorg
start_flash_log() {
  # if LEVORG, call identify again. This writes bootloader info to the flash log without prompting the user.
  levorgIdentArgs="--output=$TMP_IDENTIFY --bootloader -x -q"
  if [ $AP_MODEL -eq 6 -o $AP_MODEL -eq 7 ]; then
    execute "./exec_util StartNewFlashLog"
    execute "./subaru_identify $levorgIdentArgs"
  fi
}

# Same as above, but only writes the booloader version to a tmp file
get_bl_version_from_ecu() {
  # We only care about (/support the arg for) bootloader version on levorg comms cars ATM
  if [ $AP_MODEL -eq 6 -o $AP_MODEL -eq 7 ]; then
    TMP_IDENTIFY=/tmp/AP_IDENTIFY
    execute "./subaru_identify --output=$TMP_IDENTIFY -z -x"
  fi
}

#=========================================================================================
# This function determines if a bootloader update is required before flashing the live_rom
# $1 = flash_vehicle - eg. SUBA_US_OBXTW_CF_25
determine_if_doing_double_flash() {
  local install_vehicle=$1
  if [[ $AP_MODEL -eq 7 ]] || [[ $AP_MODEL -eq 6 ]]; then
    get_bl_version_from_ecu
    get_out_val $TMP_IDENTIFY bl_version

    get_out_val "$AP_CFG_PATH/$install_vehicle.cfg" pre_rom
    if [ -n "$pre_rom" ]; then
      # The config had a pre_rom, and the pre_rom should _always_ have cfg_bl_version in it or something has gone wrong
      # Strip off the "_pre" from the rom, as _that_ will have the `cfg_bl_version` in it. Since some vehicles (i.e SUB07 currently)
      #   just have the pre rom and no "pre_rom" field in the config we need to leave this as is.
      #   https://stackoverflow.com/a/61294531
      target_config=${pre_rom%_pre}

      get_out_val "$AP_CFG_PATH/$target_config.cfg" cfg_bl_version
    else
      get_out_val "$AP_CFG_PATH/$install_vehicle.cfg" cfg_bl_version
    fi

    if [ -n "$cfg_bl_version" ]; then # if cfg_bl_version is populated
      cfg_bl_version=`echo $cfg_bl_version | sed 's/0x//g'` # trim 0x from the byte
      if [ $cfg_bl_version -gt $bl_version ]; then # if config bl version is higher than ecu bl version
        return $SHELL_SUCCESS # yes, we need to double flash
      else
        return $SHELL_FAILURE # no, we would prefer not to double flash, but thanks anyways
      fi
    else
      # We did not have a cfg_bl version so throw an error
      showInfo "UI_INFO_ERR_ROMFILE_IO"
      getUserSelection
      exit 0
    fi
  fi

  # If not supported
  return $SHELL_FAILURE
}

#=============================================================================
# This function flashes the "pre" rom if needed for vehicles with levorg comms
# $1 = flash_vehicle - eg. SUBA_US_OBXTW_CF_25
# $2 = cpu_type - eg. RH850
# $3 = enc_key - eg. 8654
# $4 = flash_protocol - eg. LEVORG
# $5 = stubrate_file = path to stubrate file, optional
# $6 = additional_flash_args - eg. --full_flash --Installed
do_double_flash() {
  local flash_vehicle=$1
  local cpu_type=$2
  local enc_key=$3
  local flash_protocol=$4
  local stubrate_file=$5
  local additional_flash_args=$6
  local TMP_BL_ROM="/tmp/AP_BL_ROM"
  local log_message="Flashing pre-rom"

  showInfo "UI_INFO_WARN_DOUBLE_FLASH"
  getUserSelection
    if [ ! $? -eq 1 ]; then
      exit 0
    fi

  # If we have a pre_rom in the cfg use it, otherwise use {flash_vehicle}_pre
  get_out_val "$AP_CFG_PATH/$flash_vehicle.cfg" pre_rom
  local first_flash_rom=""
  local first_flash_patch=""
  if [ -n "$pre_rom" ]; then
    # Strip off the "_pre" from the rom, see determine_if_doing_double_flash comment for stackoverflow link
    first_flash_patch="$AP_DATA_ROMS/${pre_rom%_pre}/${pre_rom}_patch.ptm"
    first_flash_rom="$AP_DATA_ROMS/${pre_rom%_pre}.rom"
  else
    # Use the flash vehicle as the pre/base
    first_flash_patch="$AP_DATA_ROMS/${flash_vehicle}/${flash_vehicle}_pre_patch.ptm"
    first_flash_rom="$AP_DATA_ROMS/${flash_vehicle}.rom"
  fi

  echo "Attempting double flash with:"
  echo "patch $first_flash_patch"
  echo "rom $first_flash_rom"

  # Get the patch ptm for "part 1"
  if [ ! -e $first_flash_patch ]; then
    echo "Could not find $first_flash_patch"
    showInfo "UI_INFO_ERR_FILE_IO"
    getUserSelection
    exit 0
  fi

  # Get the rom for "part 1"
  if [ ! -e $first_flash_rom ]; then
    echo "Could not find $first_flash_rom"
    showInfo "UI_INFO_ERR_FILE_IO"
    getUserSelection
    exit 0
  fi

  execute "./map2rom --map=\"$first_flash_patch\" --stock=$first_flash_rom --rom=$TMP_BL_ROM" "map2rom - ROM image failed" $ERR_PATCH_FAILED

  execute "./subaru_flash --cpu=$cpu --enckey=$ENCKEY --file=$TMP_BL_ROM --flash --protocol=$protocol $additional_flash_args --future_state=Uninstalled --save_rate_to=$stubrate_file --message=\"$log_message\""


  flash_ret=$?
  if [ $flash_ret -eq $SHELL_SUCCESS ]; then
    showInfo "UI_INFO_IGN_OFF_INITIAL_RESET"
    getUserSelection
    showBusy "UI_WAIT_IGN_OFF_INITIAL_RESET"
    sleep 10
    showInfo "UI_INFO_IGN_ON_SECOND_RESET"
    getUserSelection
    showBusy "UI_WAIT_IGN_ON_SECOND_RESET"
    sleep 10
    showInfo "UI_INFO_IGN_OFF_SECOND_RESET"
    getUserSelection
    showBusy "UI_WAIT_IGN_OFF_SECOND_RESET"
    sleep 10
    showInfo "UI_INFO_IGN_ON_FINAL_RESET"
    getUserSelection
  fi

  if [ -e $TMP_BL_ROM ]; then
    rm $TMP_BL_ROM
  fi
}

# Determines if a levorg supports memory snapshot
determine_if_snapshot_supported() {
  echo "determine_if_snapshot_supported()"
  MIN_IPPP_VERSION="2"

  get_ippp_version_from_ecu
  ippp_ret=$?
  if [ $ipp_ret -ne $SHELL_SUCCESS ]; then
    return $SHELL_FAILURE
  fi

  get_out_val $TMP_IDENTIFY ippp_version
  if [ "$ippp_version" -ge $MIN_IPPP_VERSION ]; then
    return $SHELL_SUCCESS
  else
    return $SHELL_FAILURE
  fi
}

get_ippp_version_from_ecu() {
  # We only care about (/support the arg for) ip protection pid version on levorg comms cars
  if [ $AP_MODEL -eq 6 -o $AP_MODEL -eq 7 ]; then
    TMP_IDENTIFY=/tmp/AP_IDENTIFY
    execute "./subaru_identify --output=$TMP_IDENTIFY -Z -x"
  fi
}
#==============================================================================================
# Post firmware update, if it's detected that the vehicle ID the AP is installed to has a
# successor vehicle ID, the user's OTS maps for the predecessor vehicle ID (the vehicle ID 
# the AP is installed to) are updated to the OTS maps for the successor's vehicle ID.
#
# Note: While this script is gated to just Subaru at the moment, it has no Subaru specific
# code and can be used by any manufacturer.
#==============================================================================================

#!/bin/sh
echo "Updating maps that have a successor" >&2

. ./sh_funcs.sh

# If the AP isn't in an installed state, don't do anything
if [ "$AP_STATE" -ne "$DS_INSTALLED" ]; then
  exit 0
fi

get_settings_val last_recorded_firmware_version

# Detecting if a firmware update has occured
if [ "$last_recorded_firmware_version" != "$AP_FIRMWARE" ]; then
  # Updating the recorded firmware version
  set_settings_val last_recorded_firmware_version "$AP_FIRMWARE"

  cp_vehicle_datalog_presets $AP_VEHICLE $MODULE_ID_ECU
  echo "Finished Updating/Restoring OTS Datalog Presets" >&2

  # Only do maps for Subaru APs
  if [ "$AP_MANUF" != "SUB" ]; then
    exit 0
  fi

  # If there are no maps in the user-partition, there's nothing to update
  if ! $(ls $AP_USER_MAPS/*.ptm > /dev/null); then
    exit 0
  fi

  get_cfg_val $AP_VEHICLE successor_vehicle_id

  if [ -z "$successor_vehicle_id" ]; then
    exit 0
  fi

  local MAP_PROPS_OUTPUT_FILE=/tmp/user_map.out

  # For all of the maps in the user's partition
  for user_map in $AP_USER_MAPS/*.ptm
  do
    "/ap-app/bin/map_props" -o$MAP_PROPS_OUTPUT_FILE -e -i -m"$user_map"

    if [ $? -ne 0 ]; then
      echo "Cannot get properties for $AP_USER_MAPS/${user_map_name}" >&2
      continue
    fi

    get_out_val $MAP_PROPS_OUTPUT_FILE vendorid
    get_out_val $MAP_PROPS_OUTPUT_FILE vehicleid

    local upper_case_vendorid=`echo "$vendorid"| awk '{ print toupper($0) }'`
    local installed_to_vehicle_id=$AP_VEHICLE

    local no_path_or_version='s/[^\/]*\///g;s/v[0-9]\{1,\}//;s/.ptm//g'

    if [ "$upper_case_vendorid" == "COBB" -a "$vehicleid" == "$installed_to_vehicle_id" ]; then
      local user_mapname_without_version=`echo "$user_map" | sed $no_path_or_version`

       # For all the maps  in the ap-data partition
      for ap_data_map in $AP_DATA_MAPS/$successor_vehicle_id/*.ptm
      do
        local ap_data_mapname_without_version=`echo "$ap_data_map" | sed $no_path_or_version`
        # This will autmatically replace the user's maps with the successor's maps
        if [ "$ap_data_mapname_without_version" == "$user_mapname_without_version" ]; then
          user_remount_rw
          rm "$user_map"
          cp -a "$ap_data_map" "$AP_USER_MAPS"
          user_remount_ro
        fi
      done
    fi
  done
fi
echo "Finished updating maps that have a successor" >&2
exit 0

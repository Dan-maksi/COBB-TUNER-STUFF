#!/bin/sh

. sh_funcs.sh # get user_funcs.sh and other necessary functions

showBusy "UI_WAIT_INIT_USER_FS"

flash_eraseall /dev/mtd9
ubidetach /dev/ubi_ctrl -m9
ubiformat /dev/mtd9 -y
ubiattach /dev/ubi_ctrl -m9
ubimkvol /dev/ubi3 -N user -m
mount -rw /user

# now setup the user folder structure again
prepare_user_folders

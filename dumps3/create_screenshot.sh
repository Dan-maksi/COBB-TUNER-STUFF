#!/bin/sh

. ./sh_funcs.sh

# Screenshots are dumped as /tmp/screenshot_####
prefix="screenshot"
screenshot=${prefix}_0000
max_files=10

# Output filenames should resemble screenshot#.png where # is some number (or blank for the first entry.)
ext=".png"
naming=${prefix}${ext}

echo_stderr()
{
  echo "$1" >&2
}

# If the screenshot does not exist, error out and return accordingly.
if [ ! -e /tmp/$screenshot.ppm ]; then
  echo_stderr "!!error: invalid screenshot"
  exit 1
fi

# If the user directory for screenshots does not exist, then create it.
screen_dir=/user/ap-user/screenshots
if [ ! -d $screen_dir ]; then
  user_remount_rw
  mkdir $screen_dir
  user_remount_ro
fi

# Using create_log.sh, generate a new filename for the screenshot.
new_fname=`./create_log.sh name="$naming" max_files=$max_files dir=$screen_dir`

# Convert the ppm file to png.
user_remount_rw
pnmtopng < /tmp/$screenshot.ppm > $new_fname
user_remount_ro

# Clear all of the screenshot files that reside in the /tmp directory.
rm /tmp/$prefix*

exit 0

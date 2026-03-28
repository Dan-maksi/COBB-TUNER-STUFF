#!/bin/sh

. ./sh_funcs.sh

usage()
{
  echo_stderr "Usage: create_log.sh [dir=<dir>] name=name"
  echo_stderr "dir: optional target directory on the user partition"
  echo_stderr "max_files: optional maximum file count"
  echo_stderr "name: initial name of the log file, including extension(s)"
  echo_stderr "output: \`\$name[0-9]+.\$ext' where the number is the lowest possible number above highest found file number"
  echo_stderr "output: may delete the lowest found number if file count exceeds max_files"
}

echo_stderr()
{
  echo "$1" >&2
}

# Validate the command-line arguments
if [ "$#" -eq 0 ]; then
  usage
  exit 1
fi

#echo_stderr "$0: command-line= \`$*'..."
while [ ! -z "$1" ]; do
  #echo_stderr "$0: parsing \`$1'"
  case "$1" in
    dir=*)
      dir=`echo "$1" | sed s/.*=//`
      #echo "dir= $dir"
      ;;
    name=*)
      name_with_extension=`echo "$1" | sed s/.*=//`
      #echo "name= $name_with_extension"
      ;;
    max_files=*)
      max_files=`echo "$1" | sed s/.*=//`
      #echo "max_files= $max_files"
      ;;
    *)
      echo_stderr "$0: unknown option \`$1'";;
  esac
  shift # iterate through all the command-line parameters
done

# Verify the options are legit
if [ -z "$name_with_extension" ]; then
  echo_stderr "!!error: invalid name"
  usage
  exit 1
fi

# NOTE: This directory should be on the user partition to guarantee success.
if [ -e "$dir" ]; then
  if [ ! -d "$dir" ]; then
    echo_stderr "!!error: file already exists with dir name"
    exit 1
  fi
else
  echo_stderr "creating \`$dir'"
  user_remount_rw
  mkdir -p "$dir"
  user_remount_ro
fi

# This allows for multiple extensions per file:
#    The filename + extension(s) are split into seperate 
#    tokens, where '.' is the token delimiter. The first
#    token is use as the filename and the remaining tokens
#    are used as the file extension(s)
name=`echo "$name_with_extension" | awk -F . '{printf $1}'`
ext=${name_with_extension#$name.} # strip filename and first '.'

prev_pwd=`pwd` # save previous working directory
cd "$dir" # go into working directory for undecorated file names
# Grab a filtered file list - just the files we're trying to enumerate
file_list=`find . -regex ".*$name[0-9]*.$ext"`
cd "$prev_pwd"

file_count=`echo "$file_list" | wc -l`

# sanitize the file list so addition is always successful!
trimmed_file_list=`echo "$file_list" | sed s/[^0-9]*//g`
# sort our files and calculate the next available file
highest_num=`echo "$trimmed_file_list" | sort -n -r | head -n 1`
next_file_num=$((highest_num + 1))
# and we finally have our new file name
new_file="$name$next_file_num.$ext"

# If over the file limit delete the lowest number of tracked/versioned files
#  until we are back at our limit 
if [ $file_count -ge $max_files ]; then
  #echo_stderr "file count exceeds max file count"
  deletion_count=$((file_count - max_files + 1))
  deletion_list=`echo "$trimmed_file_list" | sort -n | head -n $deletion_count`
fi

# go read/write once for the next block of operations!
user_remount_rw
# delete all files in the deletion list
if [ -n "$deletion_list" ]; then
  IFS=$'\n'
  for file_num in $deletion_list; do
    echo_stderr "removing $dir/$name$file_num.$ext"
    rm "$dir/$name$file_num.$ext"
  done
fi
# create the new file!
touch "$dir/$new_file"
user_remount_ro

echo "$dir/$new_file" #this is the output for the pipe! must be echoed!

exit 0

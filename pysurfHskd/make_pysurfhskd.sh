#!/bin/bash
# this script builds the desired portion of pysurfHskd
# in a target.

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

MAIN_FILE="testStartup.py"

AUX_FILES="pueoTimer.py \
	   pyHskHandler.py \
	   HskProcessor.py \
           surfStartupHandler.py"

if [ "$#" -ne 1 ] ; then
    echo "usage: make_pysurfhskd.sh <destination directory>"
    echo "usage: (e.g. make_pysurfhskd.sh path/to/tmpsquashfs/pylib/ )"
    exit 1
fi

DEST=$1
mkdir -p $DEST/pysurfHskd

cp ${SCRIPT_DIR}/${MAIN_FILE} $DEST/pysurfHskd/
for f in ${AUX_FILES} ; do
    cp ${SCRIPT_DIR}/$f $DEST/pysurfHskd/
done

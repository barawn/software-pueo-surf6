#!/bin/bash

# reload systemd to pick up pyfwupd
systemctl daemon-reload

# list of services to check to stop
CHECK_SERVICES="pyfwupd"

PYSURFHSKDIR="/usr/local/pysurfHskd"
PYSURFHSKD_NAME="testStartup.py"
PYSURFHSKD=${PYSURFHSKDIR}/${PYSURFHSKD_NAME}

# we do need to tack on a subdir
export PYTHONPATH=$PYTHONPATH:$PYSURFHSKDIR

# dead duplicate of what's in pueo-squashfs
catch_term() {
    echo "termination signal caught"
    kill -TERM "$waitjob" 2>/dev/null
}

# automatically program the FPGA, weee!
autoprog.py pysoceeprom.PySOCEEPROM

trap catch_term SIGTERM

# here's where pysurfHskd would run
$PYSURFHSKD &
waitjob=$!

wait $waitjob
RETVAL=$?

# we need to make sure all services stop
# to allow the unmount to proceed
for service in ${CHECK_SERVICES}
do
    systemctl stop $service
done

exit $RETVAL

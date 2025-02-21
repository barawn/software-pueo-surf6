#!/bin/bash

# fake it for now
PYSURFHSK="sleep infinity"

# dead duplicate of what's in pueo-squashfs
catch_term() {
    echo "termination signal caught"
    kill -TERM "$waitjob" 2>/dev/null
}

# automatically program the FPGA, weee!
autoprog.py pysoceeprom.PySOCEEPROM

# here's where pysurfHskd would run
$PYSURFHSK &
waitjob=$!

wait $waitjob
RETVAL=$?

exit $RETVAL

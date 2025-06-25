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


# Even though systemd-networkd is enabled in this image, it's not  apparently used to configure interfaces. ???
# take everything down, killing the DHCP configured in /etc/network/interfaces
# who calls that? I have no idea. networking.service is masked so... who knows
ifdown -a

#turn the link back on
ip link set eth0 up

# get a uniquish IP. I checked that both the spare or the one on the crate with ethernet don't conflict :)
MACLAST=`cat /sys/class/net/eth0/address | grep -o "[0-9a-f]*$"` #grabs the last part of the mac address
MACLASTASDEC=`printf "%d" 0x${MACLAST}` # convert to decimal from hex (though the two we care about don't use a-f..)(

# set the clever ip, hope we don't put a SURF on the network that conflicts with something else (maybe the SURFs should have their own subnet...)
ip addr add 10.123.45.${MACLASTASDEC}/24 dev eth0

# routing
ip route add default via 10.123.45.1 dev eth0





exit $RETVAL

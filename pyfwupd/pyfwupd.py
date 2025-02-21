#!/usr/bin/env python3

# pyfwupd is independent of the housekeeping path.
# pysurfHskd spawns it when it receives an eDownloadMode
# message with a value of 1 and then terminates
# it when it receives an eDownloadMode message with
# a value of 0. You don't need eDownloadMode for every
# file, just once will work.

# The first 32-bit word in the first frame needs to be b'PYFW'
# followed by a 32-bit length, then followed by a null-terminated
# string indicating the filename, then lots o data.

# If you screw up, just restart this guy (eDownloadMode=0
# then eDownloadMode=1). It completes whatever it's doing when
# it catches a signal.

from pyzynqmp import Bitstream, PyZynqMP
from signalhandler import SignalHandler
from gpio import GPIO 
import os
import shutil
import logging
import argparse
import selectors

import struct
import signal
from subprocess import Popen, PIPE
from pathlib import Path

LOG_NAME = 'pyfwupd'
LOG_LEVEL_OVERRIDE = "/tmp/pyfwupd.loglevel"
if os.path.isfile(LOG_LEVEL_OVERRIDE):
    LOG_LEVEL = int(Path(LOG_LEVEL_OVERRIDE).read_text())
else:
    LOG_LEVEL = logging.WARNING

PYFW=b'PYFW'
CURRENT=PyZynqMP.CURRENT
READBACK_TYPE_PATH=PyZynqMP.READBACK_TYPE_PATH
READBACK_LEN_PATH=PyZynqMP.READBACK_LEN_PATH
IMAGE_PATH=PyZynqMP.IMAGE_PATH
EVENTPATH="/dev/input/event0"

TMPPATH="/tmp/pyfwupd.tmp"

BANKOFFSET=0x40000

FRAMELEN=95704
BANKLEN=49152

# we START UP in WARNING which is 30, and will print virtually nothing
# -v displays just file transfers begins/ends (INFO)
# -vv (DETAIL), -vvv (DEBUG), and -vvvv (TRACE) display progressively more

# https://stackoverflow.com/questions/2183233/how-to-add-a-custom-loglevel-to-pythons-logging-facility/35804945
def addLoggingLevel(levelName, levelNum, methodName=None):
    if not methodName:
        methodName = levelName.lower()

    if hasattr(logging, levelName):
       raise AttributeError('{} already defined in logging module'.format(levelName))
    if hasattr(logging, methodName):
       raise AttributeError('{} already defined in logging module'.format(methodName))
    if hasattr(logging.getLoggerClass(), methodName):
       raise AttributeError('{} already defined in logger class'.format(methodName))

    # This method was inspired by the answers to Stack Overflow post
    # http://stackoverflow.com/q/2183233/2988730, especially
    # http://stackoverflow.com/a/13638084/2988730
    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            self._log(levelNum, message, args, **kwargs)
    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    logging.addLevelName(levelNum, levelName)
    setattr(logging, levelName, levelNum)
    setattr(logging.getLoggerClass(), methodName, logForLevel)
    setattr(logging, methodName, logToRoot)

# dunno how fast this will be, we'll see
# some part of me thinks I should write this in the damn driver
# or do a ctypes ffi call
class Converter:
    XILFRAME = "/usr/local/bin/xilframe"
    XILFRAME_ARGS = "-"
    def __init__(self):
        if not os.access(self.XILFRAME, os.X_OK):
            raise FileNotFoundError("cannot execute %s" % self.XILFRAME)
        self.xf = Popen([self.XILFRAME, self.XILFRAME_ARGS],
                        stdin=PIPE,
                        stdout=PIPE)

    def convert(self, fr):
        self.xf.stdin.write(fr)
        return self.xf.stdout.read(49152)

# this is supertrimmed for PUEO
class Event:
    # ll = struct timespec, H=type, H=code, I=value
    FORMAT='llHHI'
    LENGTH=struct.calcsize(FORMAT)
    def __init__(self, data):
        vals = struct.unpack(self.FORMAT, data)
        if vals[2] != 0 or vals[3] != 0 or vals[4] != 0:
            self.code = vals[3]
            self.value = vals[4]
        else:
            self.code = None

if __name__ == "__main__":
    z = PyZynqMP()
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--verbose', action='count', default=0)
    args = parser.parse_args()
    # just make the first -v count double
    if args.verbose:
        args.verbose += 1
    logLevel = LOG_LEVEL - 5*args.verbose
    addLoggingLevel('TRACE', logging.DEBUG - 5)
    addLoggingLevel('DETAIL', logging.INFO - 5)
    
    logger = logging.getLogger(LOG_NAME)
    logging.basicConfig(level=logLevel)
        
    err = None
    try:
        if z.state() != 'operating':
            raise RuntimeError("FPGA is not operating")
        if not os.path.islink(CURRENT):
            raise RuntimeError("Can't determine current firmware")
        userid = Bitstream(os.readlink(CURRENT)).userid
        if userid == 0xFFFFFFFF:
            raise RuntimeError("UserID does not have frame start")
    except Exception as e:
        logger.error("cannot start up - " + repr(e))
        exit(1)

    bankA = PyZynqMP.encodeReadbackType(userid, capture=True)
    bankB = PyZynqMP.encodeReadbackType(userid+BANKOFFSET, capture=True)
    gpioA = GPIO(GPIO.get_gpio_pin(12), 'out')
    gpioB = GPIO(GPIO.get_gpio_pin(13), 'out')
    typePath = Path(READBACK_TYPE_PATH)
    lenPath = Path(READBACK_LEN_PATH)
    image = Path(IMAGE_PATH)
    
    # okey dokey, starting up

    # create the selector
    sel = selectors.DefaultSelector()

    # and create the handler - by default it does SIGINT/SIGTERM
    handler = SignalHandler(sel)
    
    # we always write our length, it's constant
    lenPath.write_text(str(FRAMELEN))
    stateA = [30, gpioA, str(bankA), None]
    stateB = [31, gpioB, str(bankB), None]
    logger.debug("stateA is %d (%s)" % (stateA[0], stateA[2]))
    logger.debug("stateB is %d (%s)" % (stateB[0], stateB[2]))
    stateA[3] = stateB
    stateB[3] = stateA

    # start up with bank A mode
    # whenever you enter eDownloadMode you need to start with MARK_A
    state = stateA
    logger.detail("currently in state %d" % state[0])
    
    typePath.write_text(state[2])
    
    # start with no file
    curFile = None    
    # start with no horrible errors
    horribleProblem = None
    # open the temporary file...
    tempFile = open(TMPPATH, "w+b")    
    # spawn the converter
    conv = Converter()

    # dear god this is insane
    with open(EVENTPATH, "rb") as evf:
        
        # maybe farm this off into a class
        # create the event handler function
        def handleEvent(f, m):
            # we modify state/curFile, need to mark it as a global
            global state
            global curFile
            global tempFile
            
            eb = f.read(Event.LENGTH)
            # info
            logger.debug("out of read wait, got %d bytes" % len(eb))
            logger.trace(list(eb))
            if not eb or len(eb) != Event.LENGTH:
                logger.error("skipping malformed read")
                return
            e = Event(eb)
            if e.code is None:
                logger.debug("skipping separator")
                return
            else:
                logger.detail("processing an event: currently in state %d" % state[0])
                if e.code == state[0] and e.value == 0:
                    # this shouldn't happen, it's a clear event for the one we're on
                    logger.warning("code %d value %d ????" % (e.code, e.value))
                    return
                if e.code == state[0] and e.value == 1:
                    # sooo much of this needs exception handling!!
                    # this is like, absurdly awful!!
                    r = image.read_bytes()
                    state[1].write(1)
                    state[1].write(0)
                    state = state[3]
                    typePath.write_text(state[2])
                    
                    data = conv.convert(r)
                    dlen = BANKLEN
                    if curFile is None:
                        logger.detail("no curFile - parsing first block")
                        logger.trace("marker:" + str(list(data[0:4])))
                        logger.trace("length:" + str(list(data[4:8])))
                        logger.trace("beginning of fn:" + str(list(data[8:12])))
                        try:
                            if data[0:4] != PYFW:
                                raise ValueError("communication error: no file, but got " + str(list(data[0:4])))
                            logger.debug("PYFW okay, unpacking header")

                            thisLen = struct.unpack(">I", data[4:8])[0]
                            # index of the null terminator
                            endFn = data[8:].index(b'\x00') + 8
                            # now sum through the checksum, which is after the null terminator
                            # (so in python you add 2 b/c the end slice index is 1 after your final)
                            cks = sum(data[:endFn+2]) % 256                                
                            if cks != 0:
                                logger.error(list(data[:endFn+2]))                                
                                raise ValueError("checksum failed: %2.2x" % cks)
                            thisFn = data[8:endFn].decode()
                            data = data[endFn+2:]
                            dlen = len(data)
                        except Exception as e:
                            horribleProblem = 2
                            logger.error("First frame failed: " + repr(e))
                            handler.set_terminate()
                            return
                        logger.info("beginning " + thisFn + " len " + str(thisLen))
                        curFile = [thisFn, thisLen]
                    if dlen > curFile[1]:
                        try:
                            # grr curFile[1] is right: it's # of bytes remaining
                            # and b[0:5] grabs the first 5 bytes
                            tempFile.write(data[:curFile[1]])
                            logger.info("completed file %s" % curFile[0])
                            # close the temporary file
                            tempFile.close()
                            # move it to its final destination
                            shutil.move(TMPPATH, curFile[0])
                            # and get a new one
                            tempFile = open(TMPPATH, "w+b")
                        except Exception as e:
                            horribleProblem = 3
                            logger.error("Finishing file failed: " + repr(e))
                            handler.set_terminate()
                            return
                        curFile = None
                    else:
                        try:
                            tempFile.write(data)
                        except Exception as e:
                            horribleProblem = 4
                            logger.error("Writing to file failed: " + repr(e))
                            handler.set_terminate()
                            return
                        curFile[1] = curFile[1] - dlen
                        logger.detail("%s: %d bytes, %d remaining" % (curFile[0], dlen, curFile[1]))
                else:
                    if e.code == state[3][0] and e.value == 0:
                        logger.detail("release event seen")
                    else:
                        logger.warning("code %d value %d ???" % (e.code, e.value))
                    return
            return        
        
        sel.register(evf, selectors.EVENT_READ, handleEvent)
        # we can now mark things as ready. evf is already open,
        # so when we select below, even if something comes in while
        # we're setting it back to zero, we'll still see it.
        stateA[1].write(1)
        stateB[1].write(1)
        stateA[1].write(0)
        stateB[1].write(0)
        # do selecty thing here
        while not handler.terminate:
            events = sel.select()
            for key, mask in events:
                callback = key.data
                callback(key.fileobj, mask)

    if curFile:
        logger.warning("file " + curFile[0] + " is incomplete, deleting temporary!!")
        tempFile.close()
        os.unlink(TMPPATH)
                
    if horribleProblem:
        logger.error("terminating with horrible error %d" % horribleProblem)
        exit(horribleProblem)
    else:
        logger.info("terminating normally")
        exit(0)

    # we do NOT need to clear psdones, because they autoclear
    # when download mode is turned off.
    

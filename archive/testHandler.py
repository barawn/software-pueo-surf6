from pyHskHandler import HskPacketHandler
from pueoTimer import HskTimer
from serial.threaded import ReaderThread
from serial import Serial
from enum import Enum
import queue
import logging
import re
import ast

# FAKEYFAKEY TESTING
MY_ID = b'\x02'

cmds = {}
with open('HskCommands.h') as cmdFile:
    for name, value in re.findall(r'#define\s+(\w+)\s+(.*)',cmdFile.read()):
        try:
            cmds[name] = ast.literal_eval(value)
        except Exception as e:
            print("exception parsing header %s: %s" % (name, str(e)))

class EventType(str, Enum):
    HSK = 'HSK'
    TIMER = 'TIMER'
    def __str__(self) -> str:
        return self.value

# this is the subtractive checksum
def cksum(data):
    return ((-1*(sum(data) & 0xFF)) & 0xFF)
    
logger = logging.getLogger('pysurfHskd')
logging.basicConfig(level=logging.DEBUG)
    
# ok, so the way this works is that we create an
# event queue, and our little handler buddies
# push events onto it, and our main loop
# just pops events off of it and figures out
# what to do. which is like, what every event-driven
# processing setup does. wheeeeeee
eventFifo = queue.Queue()
timer = HskTimer(eventFifo, EventType.TIMER)
# create closure to generate a packet handler
def makePacketHandler():
    return HskPacketHandler(eventFifo,
                            eventType=EventType.HSK)
ser = Serial('/dev/ttyPS1', 500000)
# create a reader thread that uses our packet handler
reader = ReaderThread(ser, makePacketHandler)


maxTick = 500
curTick = None
# start up the thready-threads
timer.start()
reader.start()
# get reference to the handler
transport, handler = reader.connect()
# loopy loop
while curTick != maxTick:
    ev = eventFifo.get()
    if ev[0] == EventType.TIMER:
        curTick = ev[1]
        logger.debug("tick %d" % curTick)
    elif ev[0] == EventType.HSK:
        pkt = ev[1]
        # oh this is going to get big
        # right now stay stupid, respond to everything
        # n.b. in python a bytes slice returns a bytes, so do a 1-entry slice
        # instead of index
        if pkt[2] == cmds['ePingPong']:
            myPkt = MY_ID + pkt[0:1] + pkt[2:]
            handler.send_packet(myPkt)
        elif pkt[2] == cmds['eStatistics']:
            stats = handler.statistics()
            cks = cksum(stats)
            myPkt = MY_ID + pkt[0:1] + pkt[2:3] + bytes([len(stats)]+stats+[cks])
            handler.send_packet(myPkt)

# stop the thready-threads
timer.cancel()
reader.stop()


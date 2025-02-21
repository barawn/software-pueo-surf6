from serial.threaded import Packetizer, ReaderThread
from serial import Serial
from cobs import cobs
import os
import logging
import traceback
import threading
import queue
import selectors

# REWORKED AGAIN. We now wrap the serial reader
# thread inside ANOTHER class because of
# handler difficulties.
class HskHandler:
    SOCID_BASE = 128
    SLOT_BASE = 64
    
    def __init__(self,
                 sel,
                 eeprom=None,
                 logName='testing',
                 port='/dev/ttyPS1',
                 baud=500000):
        self.selector = sel
        self.logger = logging.getLogger(logName)
        self.fifo = queue.Queue()
        self.port = Serial(port, baud)
        self.handler = None
        self.transport = None
        thisFilter = None
        self.myID = None
        if eeprom is not None:
            # simple filter function for the super-early version
            # no broadcasts, no slot-based ID
            self.myID = eeprom.socid + self.SOCID_BASE
            myID = self.myID
            def packetFilter(pkt):
                pktLen = len(pkt)
                rv = 0
                if pktLen < 5 or pkt[3] != pktLen-5 or (sum(pkt[4:]) % 256):
                    self.logger.info("Invalid packet: %s", pkt.hex(sep=' '))
                    rv = -1
                # packet is ok, now filter on my ID
                elif pkt[1] != myID:
                    rv = 1
                else:
                    rv = 0
                return rv
            thisFilter = packetFilter

        def makePacketHandler():
            return HskPacketHandler(self.fifo, logName, thisFilter)
        self.reader = ReaderThread(self.port, makePacketHandler)
        self.sendPacket = self.notRunningError
        self.statistics = self.notRunningError

    def start(self, callback=None):
        if not callback:
            callback = self.dumpPacket
        self.reader.start()
        transport, handler = self.reader.connect()
        self.handler = handler
        self.transport = transport
        self.sendPacket = self.handler.send_packet
        self.statistics = self.handler.statistics
        
        self.selector.register(handler.rfd,
                               selectors.EVENT_READ,
                               callback)

    def stop(self):
        self.sendPacket = self.notRunningError
        self.statistics = self.notRunningError
        self.handler = None
        self.transport = None
        self.reader.stop()
                
    @staticmethod
    def notRunningError(*args):
        raise RuntimeError("the housekeeping handler is not running")

    def dumpPacket(self, fd, mask):
        """ print out the received packet from the fifo """
        if self.fifo.empty():
            self.logger.error("dump_packet called but FIFO is empty?")
            return
        pktno = os.read(fd, 1)
        pkt = self.fifo.get()
        self.logger.info("Pkt %d: %s", pktno[0], pkt.hex(sep=' '))
            
        
# sigh, reworked. we use a pipe to signal that our fifo should
# be read. we push the received packet number % 255.
# we also take the selector.

# This ONLY HANDLES COBS DECODING
# filterFn handles checking if it's for us or if it has a checksum error
# filterFn returns 0 if no issues, 1 if it's filtered, and -1 if it's
# an error (really anything other than 0 or 1)
class HskPacketHandler(Packetizer):
    _nullFilter = lambda pkt : 0             
    def __init__(self,
                 fifo,
                 logName='pysurfHskd',
                 filterFn=_nullFilter
                 ):
        super(HskPacketHandler, self).__init__()
        self.rfd, self.wfd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)        
        self.fifo = fifo
        self.filterFn = filterFn
        
        self.logger = logging.getLogger(logName)
        self._statisticsLock = threading.Lock()

        self._receivedPackets = 0
        self._sentPackets = 0
        self._errorPackets = 0
        self._droppedPackets = 0
        self._filteredPackets = 0
        self._mod = lambda x : x & 0xFF
        
    def connection_made(self, transport):
        super(HskPacketHandler, self).connection_made(transport)
        self.logger.info("opened port")

    def connection_lost(self, exc):
        if isinstance(exc, Exception):
            self.logger.info("port closed due to exception")
            raise exc
        self.logger.info("closed port")


    def handle_packet(self, packet):
        """ implement the handle_packet function """
        if len(packet) == 0:
            return
        try:
            pkt = cobs.decode(packet)
        except cobs.DecodeError:
            with self._statisticsLock:
                self._errorPackets = self._errorPackets + 1
                errorPackets = self._errorPackets
            self.logger.error("COBS decode error #%d : %s",
                              errorPackets,
                              packet.hex(sep=' '))
            return
        # COBS decode OK
        filterResult = self.filterFn(pkt)
        self.logger.debug("got packet: filter result %d", filterResult)
        if filterResult == 0:
            if not self.fifo.full():
                with self._statisticsLock:
                    curPkt = self._receivedPackets
                    self._receivedPackets = self._receivedPackets + 1
                self.fifo.put(pkt)
                toWrite = (curPkt & 0xFF).to_bytes(1, 'little')
                nb = os.write(self.wfd, toWrite)
                if nb != 1:
                    self.logger.error("could not write packet number %d to pipe!!!" % curPkt)
            else:
                with self._statisticsLock:
                    self._droppedPackets = self._droppedPackets + 1
                    droppedPackets = self._droppedPackets
                self.logger.error("packet FIFO is full: dropped packet count %d" % droppedPackets)
        elif filterResult == 1:
            # not for us
            with self._statisticsLock:
                self._filteredPackets = self._filteredPackets + 1
        else:
            # filter found an error
            with self._statisticsLock:
                self._errorPackets = self._errorPackets + 1
                errorPackets = self._errorPackets
            self.logger.error("Filter error #%d : %d", errorPackets, filterResult)

    def send_packet(self, packet):
        """ send binary packet via COBS encoding """
        d = cobs.encode(packet) + b'\x00'
        if self.transport:
            self.transport.write(d)
        with self._statisticsLock:
            self._sentPackets = self._sentPackets + 1

    def statistics(self):
        r = []
        with self._statisticsLock:
            r = [self._receivedPackets,
                 self._sentPackets,
                 self._errorPackets,
                 self._droppedPackets,
                 self._filteredPackets]
        return list(map(self._mod, r))
    

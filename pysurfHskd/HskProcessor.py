import logging
import os
from subprocess import Popen, PIPE, TimeoutExpired
from pathlib import Path
import pickle
import struct

class HskProcessor:
    kReboot = 0xFF
    kTerminate = 0xFE
    bmKeepCurrentSoft = 0x1
    bmRevertChanges = 0x2
    bmCleanup = 0x4
    bmForceReprogram = 0x8
    bmMagicValue = 0x80
    def ePingPong(self, pkt):
        rpkt = bytearray(pkt)
        rpkt[1] = rpkt[0]
        rpkt[0] = self.hsk.myID
        self.hsk.sendPacket(rpkt)

    def eStatistics(self, pkt):
        rpkt = bytearray(9)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 15
        rpkt[3] = 4
        rpkt[4:8] = self.hsk.statistics()
        rpkt[8] = (256-sum(rpkt[4:8])) & 0xFF
        self.hsk.sendPacket(rpkt)
    
    def eVolts(self, pkt):
        rpkt = bytearray(17)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 17
        rpkt[3] = 12
        rpkt[4:16] = struct.pack(">HHHHHH", *self.zynq.raw_volts())
        rpkt[16] = (256-sum(rpkt[4:16])) & 0xFF
        self.hsk.sendPacket(rpkt)

    def eTemps(self, pkt):
        rpkt = bytearray(9)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 16
        rpkt[3] = 4
        rpkt[4:8] = struct.pack(">HH", *self.zynq.raw_temps())
        rpkt[8] = (256-sum(rpkt[4:8])) & 0xFF
        self.hsk.sendPacket(rpkt)

    # identify sends
    # PL DNA
    # MAC
    # plxVersion
    # sqfs version if any
    # slot identifier if any
    # tends to be around 75 bytes or so
    def eIdentify(self, pkt):
        rpkt = bytearray(4)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 18
        # fixed length
        rpkt += self.zynq.dna.encode() + b'\x00'
        rpkt += self.zynq.mac.encode() + b'\x00'
        # this part is not
        rpkt += self.plxVersion
        # remainder is optional
        v = self.version
        if v is not None:
            rpkt += b'\x00' + v
        l = self.eeprom.location
        if l is not None:
            rpkt += b'\x00' + l['crate'] + l['slot']
        rpkt[3] = len(rpkt[4:])
        cks = (256 - sum(rpkt[4:])) & 0xFF
        rpkt.append(cks)
        self.hsk.sendPacket(rpkt)

    def eStartState(self, pkt):
        if len(pkt) > 5:
            self.startup.endState = rpkt[4]
        rpkt = bytearray(6)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 32
        rpkt[3] = 1
        rpkt[4] = self.startup.state
        rpkt[5] = (256 - rpkt[4]) & 0xFF
        self.hsk.sendPacket(rpkt)
        
    @staticmethod
    def _getSoftTimestamp(fn: bytes):
        cmd = ["unsquashfs", "-fstime", fn.decode()]
        p = Popen(cmd, stdin=PIPE, stdout=PIPE)
        r = p.communicate()
        if p.returncode == 0:
            return r[0].strip(b'\n')
        return b''
        
    def eSoftNext(self, pkt):
        rpkt = bytearray(4)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        linkname = b''
        timestamp = b''
        if len(pkt) > 5:
            fn = pkt[4:-1]
            fp = Path(fn.decode())
            if fn[0] == 0:
                if self.nextSoft.exists():
                    self.nextSoft.unlink()
            else:
                # want to set it, so do sanity check
                if fp.is_file():
                    timestamp = self._getSoftTimestamp(fn)
                if timestamp == b'':
                    # failed sanity check
                    rpkt[2] = 0xFF
                    rpkt[3] = 0
                    rpkt.append(0)
                    self.hsk.sendPacket(rpkt)
                    return
                # replace the link
                if self.nextSoft.exists():
                    self.nextSoft.unlink()
                self.nextSoft.symlink_to(fp)
                linkname = fn
        else:
            # just reading it
            if self.nextSoft.exists():
                if not self.nextSoft.is_symlink():
                    logger.error("%s is not a link! Deleting it!!",
                                 self.nextSoft.name)
                    self.nextSoft.unlink()
                else:
                    linkname = bytes(self.nextSoft.readlink())
                    timestamp = self._getSoftTimestamp(linkname)
        rpkt[2] = 135
        rpkt += linkname + b'\x00' + timestamp
        rpkt[3] = len(rpkt[4:])
        cks = (256 - sum(rpkt[4:])) & 0xFF
        rpkt.append(cks)
        self.hsk.sendPacket(rpkt)                    

    # so much more error checking
    def eFwNext(self, pkt):
        rpkt = bytearray(4)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        if len(pkt) > 5:
            fn = pkt[4:-1]
            fp = Path(fn.decode())
            if fn[0] == 0:
                if self.nextFw.exists():
                    self.nextFw.unlink()
            elif not fp.is_file():
                rpkt[2] = 255
                rpkt[3] = 0
                rpkt.append(0)
                self.hsk.sendPacket(rpkt)
                return
            else:
                if self.nextFw.exists():
                    self.nextFw.unlink()
                self.nextFw.symlink_to(fp)
        rpkt[2] = 129        
        if not self.nextFw.exists() or not self.nextFw.is_symlink():
            if self.nextFw.exists():
                self.logger.error("%s is not a link! Deleting it!!",
                                  self.zynq.NEXT)
                self.nextFw.unlink()
            rpkt[3] = 1
            rpkt += b'\x00\x00'
            self.hsk.sendPacket(rpkt)
        else:
            fn = bytes(self.nextFw.readlink())
            rpkt += fn
            cks = (256 - sum(rpkt[4:])) & 0xFF
            rpkt.append(cks)
            self.hsk.sendPacket(rpkt)

    def eDownloadMode(self, pkt):
        rpkt = bytearray(6)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 190
        rpkt[3] = 1
        d = pkt[4:-1]
        if len(d):
            st = d[0]
            self._downloadMode(st)
        rpkt[4] = self._downloadState()
        rpkt[5] = (256 - rpkt[4]) & 0xFF
        self.hsk.sendPacket(rpkt)
        
    def eJournal(self, pkt):
        rpkt = bytearray(4)
        rpkt[1] = pkt[0]
        rpkt[0] = self.hsk.myID
        rpkt[2] = 189
        d = pkt[4:-1]
        if len(d):
            args = d.decode().split(' ')
            cmd = [ "journalctl" ] + args
            try:
                p = Popen(cmd, stdin=PIPE, stdout=PIPE)
                self.journal = p.communicate(timeout=5)[0]
            except TimeoutExpired:
                p.kill()
                self.journal = p.communicate()[0]
                
        # all of this works even if journal is b''
        rd = self.journal[:255]
        self.journal = self.journal[255:]
        rpkt += rd
        rpkt[3] = len(rpkt[4:])
        cks = (256 - sum(rpkt[4:])) & 0xFF
        rpkt.append(cks)
        self.hsk.sendPacket(rpkt)

    # no reply, and only check length/magic no
    def eRestart(self, pkt):
        d = pkt[4:-1]
        # fake an error if you didn't tell me what to do
        code = 0x80 if not len(d) else d[0]
        if code & self.bmMagicValue:
            if code != self.kReboot and code != self.kTerminate:
                rpkt = bytearray(5)
                rpkt[1] = pkt[0]
                rpkt[0] = hsk.myID
                rpkt[2] = 0xFF
                rpkt[3] = 0
                rpkt[4] = 0
                self.hsk.sendPacket(rpkt)
                return
        self.restartCode = code
        self.terminate()        
        
    # this guy is like practically the whole damn program
    def __init__(self,
                 hsk,
                 zynq,
                 eeprom,
                 startup,
                 logName,
                 terminateFn,
                 softNextFile="/tmp/pueo/next",
                 plxVersionFile=None,
                 versionFile=None):
        # these need to be actively defined to make them
        # closures - they're methods, not constant functions
        self.hskMap = {
            0 : self.ePingPong,
            15 : self.eStatistics,
            16 : self.eTemps,
            17 : self.eVolts,
            18 : self.eIdentify,
            32 : self.eStartState,
            129 : self.eFwNext,
            135 : self.eSoftNext,
            189 : self.eJournal,
            190 : self.eDownloadMode,
            191 : self.eRestart
        }        
        self.hsk = hsk
        self.zynq = zynq
        self.eeprom = eeprom
        self.startup = startup
        self.logger = logging.getLogger(logName)
        self.terminate = terminateFn
        self.restartCode = None
        self.nextSoft = Path(softNextFile)
        self.nextFw = Path(self.zynq.NEXT)
        self.plxVersion = b''
        if plxVersionFile:
            p = Path(plxVersionFile)
            if p.is_file():
                self.plxVersion = p.read_text().strip("\n").encode()

        v = None
        if versionFile:
            try:
                with open(versionFile, 'rb') as f:
                    pv = pickle.load(f)
                v = pv['version'].encode() + b'\x00'
                v += pv['hash'].encode() + b'\x00'
                v += pv['date'].encode()
            except Exception as e:
                self.logger.error("Exception loading version: %s", repr(e))
        self.version = v            
        self.journal = b''

    def _downloadMode(self, st):
        if st == 0:
            os.system("systemctl stop pyfwupd")
        else:
            os.unlink("/tmp/pyfwupd.loglevel")
            if st & 0x80:
                loglevel = st & 0x7F
                Path("/tmp/pyfwupd.loglevel").write_text(str(loglevel))
            os.system("systemctl start pyfwupd")
        
    def _downloadState(self):
        return 0 if os.system("systemctl is-active --quiet pyfwupd") else 1

    def stop(self):
        self._downloadMode(0)
        
    def basicHandler(self, fd, mask):
        if self.hsk.fifo.empty():
            self.logger.error("handler called but FIFO is empty?")
            return
        pktno = os.read(fd, 1)
        pkt = self.hsk.fifo.get()
        cmd = pkt[2]
        if cmd in self.hskMap:
            try:
                cb = self.hskMap.get(cmd)
                self.logger.debug("calling %s", cb.__name__)
                cb(pkt)
            except Exception as e:
                import traceback
                self.logger.error("exception %s thrown inside housekeeping handler?", repr(e))
                self.logger.error(traceback.format_exc())
                self.terminate()
        else:
            self.logger.info("ignoring unknown hsk command: %2.2x", cmd)
            

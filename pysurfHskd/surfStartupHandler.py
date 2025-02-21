from enum import Enum
import logging
import os
from pueo.common.bf import bf

# the startup handler actually runs in the main
# thread. it either writes a byte to a pipe to
# indicate that it should be called again,
# or it pushes its run function into the tick FIFO
# if it wants to be called when the tick FIFO
# expires.
# the tick FIFO takes closures now
# god this thing is a headache
class StartupHandler:
    LMK_FILE = "/usr/local/share/SURF6_LMK.txt"
        
    class StartupState(int, Enum):
        STARTUP_BEGIN = 0
        WAIT_CLOCK = 1
        RESET_CLOCK = 2
        RESET_CLOCK_DELAY = 3
        PROGRAM_ACLK = 4
        WAIT_ACLK_LOCK = 5
        ENABLE_ACLK = 6
        WAIT_PLL_LOCK = 7
        ALIGN_RXCLK = 8
        LOCATE_EYE = 9
        TURFIO_LOCK = 10
        WAIT_TURFIO_LOCKED = 11
        ENABLE_TRAIN = 12
        WAIT_TURFIO = 13
        DISABLE_TRAIN = 14
        STARTUP_FAILURE = 255

        def __index__(self) -> int:
            return self.value

    def __init__(self,
                 logName,
                 surfDev,
                 surfClock,
                 surfClockReset,
                 autoHaltState,
                 tickFifo):
        self.state = self.StartupState.STARTUP_BEGIN
        self.logger = logging.getLogger(logName)
        self.surf = surfDev
        self.clock = surfClock
        self.clockReset = surfClockReset
        self.endState = autoHaltState        
        self.tick = tickFifo
        self.rfd, self.wfd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.endState is None:
            self.endState = self.StartupState.STARTUP_BEGIN

    def _runNextTick(self):
        if not self.tick.full():
            self.tick.put(self.run)
        else:
            raise RuntimeError("tick FIFO became full in handler!!")

    def _runImmediate(self):
        toWrite = (self.state).to_bytes(1, 'big')
        nb = os.write(self.wfd, toWrite)
        if nb != len(toWrite):
            raise RuntimeError("could not write to pipe!")
        
    def run(self):
        # whatever dumb debugging
        self.logger.trace("startup state: %s", self.state)
        # endState is used to allow us to single-step
        # so if you set startup to 0 in the EEPROM, you can
        # set the end state via HSK and single-step through
        # startup.
        if self.state == self.endState or self.state == self.StartupState.STARTUP_FAILURE:
            self._runNextTick()
            return
        elif self.state == self.StartupState.STARTUP_BEGIN:
            id = self.surf.read(0).to_bytes(4,'big')
            if id != b'SURF':
                self.logger.error("failed identifying SURF: %s", id.hex())
                self.state == self.StartupState.STARTUP_FAILURE
                self._runNextTick()
                return
            else:
                dv = self.surf.DateVersion(self.surf.read(0x4))
                self.logger.info("this is SURF %s", str(dv))
                # cool you're a surf turn on an LED or some'n
                r = bf(self.surf.read(0xC))
                r[1] = 1
                self.surf.write(0xC, int(r))
                self.state = self.StartupState.WAIT_CLOCK
                self._runImmediate()
                return
        elif self.state == self.StartupState.WAIT_CLOCK:
            r = bf(self.surf.read(0xC))
            if not r[31]:
                self._runNextTick()
                return
            else:
                self.logger.info("RACKCLK is ready.")                
                self.state = self.StartupState.RESET_CLOCK
                self._runImmediate()
                return
        elif self.state == self.StartupState.RESET_CLOCK:
            if not os.path.exists(self.LMK_FILE):
                self.logger.error("failed locating %s", self.LMK_FILE)
                self.state = self.StartupState.STARTUP_FAILURE
                self._runNextTick()
            self.clockReset.write(1)
            self.clockReset.write(0)
            self.state = self.StartupState.RESET_CLOCK_DELAY
            self._runNextTick()
            return
        elif self.state == self.StartupState.RESET_CLOCK_DELAY:
            self.clock.surfClockInit()            
            self.state = self.StartupState.PROGRAM_ACLK
            self._runNextTick()
            return
        elif self.state == self.StartupState.PROGRAM_ACLK:
            # debugging
            st = self.clock.surfClock.status()
            self.logger.detail("Clock status before programming: %2.2x", st)
            self.clock.surfClock.configure(self.LMK_FILE)
            self.state = self.StartupState.WAIT_ACLK_LOCK
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_ACLK_LOCK:
            st = self.clock.surfClock.status()
            self.logger.detail("Clock status now: %2.2x", st)
            if st & 0x2 == 0:
                self._runNextTick()
                return
            else:
                self.logger.info("ACLK is ready.")
                self.state = self.StartupState.ENABLE_ACLK
                self._runImmediate()
                return
        elif self.state == self.StartupState.ENABLE_ACLK:
            # write 1 to enable CE on ACLK BUFGCE
            rv = bf(self.surf.read(0xC))
            rv[0] = 1
            self.surf.write(0xC, int(rv))
            # write 0 to pull PLLs out of reset
            rv = bf(self.surf.read(0x800))
            rv[13] = 0
            self.surf.write(0x800, int(rv))
            self.state = self.StartupState.WAIT_PLL_LOCK
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_PLL_LOCK:
            rv = bf(self.surf.read(0x800))
            if not rv[14]:
                self._runNextTick()
                return
            self.state = self.StartupState.ALIGN_RXCLK
            self._runImmediate()
            return
        elif self.state == self.StartupState.ALIGN_RXCLK:
            # use firmware parameters for this eventually!!!
            # this needs to freaking do something if it fails!!
            av = self.surf.align_rxclk()
            self.logger.info("RXCLK aligned at offset %f", av)
            self.state = self.StartupState.LOCATE_EYE
            self._runImmediate()
            return
        elif self.state == self.StartupState.LOCATE_EYE:
            # use firmware parameters for this eventually!!!
            eye = self.surf.locate_eyecenter()
            self.surf.setDelay(eye[0])
            self.surf.turfioSetOffset(eye[1])
            self.state = self.StartupState.TURFIO_LOCK
            self._runImmediate()
            return
        elif self.state == self.StartupState.TURFIO_LOCK:
            self.surf.turfioLock(True)
            self.state = self.StartupState.WAIT_TURFIO_LOCKED
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_TURFIO_LOCKED:
            if not self.surf.turfioLocked():
                self._runNextTick()
                return
            self.state = self.StartupState.ENABLE_TRAIN
            self._runImmediate()
            return
        elif self.state == self.StartupState.ENABLE_TRAIN:
            # dangit lookup what to do here
            self.state = self.StartupState.WAIT_TURFIO
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_TURFIO:
            # figure out what to do here too!!!
            self._runNextTick()
            return

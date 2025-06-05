from enum import Enum
import logging
import os
from pueo.common.bf import bf
from surfExceptions import StartupException
from dataclasses import dataclass

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

    @dataclass
    class MultiTileSync:
        t1_codes : None
        pll_codes : None
        latency : None
        target_latency : int = -1
        sysref_enable : int = 0

    @dataclass
    class Align:
        rx_delay : float = None
        cin_delay : float = None
        cin_bit : int = None
        
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
        WAIT_CIN_ACTIVE = 9
        LOCATE_EYE = 10
        TURFIO_LOCK = 11
        WAIT_TURFIO_LOCKED = 12
        ENABLE_TRAIN = 13
        WAIT_LIVE = 14
        WAIT_SYNC = 15
        MTS_STARTUP = 16
        RUN_MTS = 17
        MTS_SHUTDOWN = 18
        STARTUP_FINISH = 254
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

        self.mts = self.MultiTileSync( t1_codes=None,
                                       pll_codes=None,
                                       target_latency = -1,
                                       sysref_enable = 0,
                                       latency=None )
        self.align = self.Align()
                            
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
                raise StartupException("firmware identify error")
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
                # shut down unused clocks
                self.clock.surfClock.driveClock(self.clock.lmk_map['MGT'],
                                               self.clock.surfClock.DriveMode.POWERDOWN)
                self.clock.surfClock.driveClock(self.clock.lmk_map['EXT'],
                                               self.clock.surfClock.DriveMode.POWERDOWN)
                self.clock.surfClock.clockDividerEnable(self.clock.lmk_map['MGT'], False)
                self.clock.surfClock.clockDividerEnable(self.clock.lmk_map['EXT'], False)
                # feedback's output can be turned off
                self.clock.surfClock.driveClock(5, self.clock.surfClock.DriveMode.POWERDOWN)
                # you do NOT need to issue SYNC. I honestly don't know why, but you don't:
                # it's probably because they're all part of the SYNC group. This is also
                # good because if we DID issue sync we'd have to wait for it to lock
                # AGAIN because syncing blows up the lock.
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
            if self.align.rx_delay:
                self.logger.info(f'Applying RXCLK alignment {self.align.rx_delay}')
            self.align.rx_delay = self.surf.align_rxclk(userSkew=self.align.rx_delay)
            self.logger.info(f'RXCLK aligned at offset {self.align.rx_delay}')
            # reset the active indicator
            self.surf.turfio_cin_active = 0
            self.state = self.StartupState.WAIT_CIN_ACTIVE
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_CIN_ACTIVE:
            if not self.surf.turfio_cin_active:
                self._runNextTick()
                return
            self.state = self.StartupState.LOCATE_EYE
            # I think we want to give a bit of a pause here??
            self._runNextTick()
            return
        elif self.state == self.StartupState.LOCATE_EYE:
            if self.align.cin_delay is None:
                try:
                    delay, bit = self.surf.locate_eyecenter()
                except Exception as e:
                    self.logger.error(f'Locating eye center failed! {repr(e)}')
                    self.state = self.StartupState.STARTUP_FAILURE
                    self._runNextTick()
                    return
                self.align.cin_delay = delay
                self.align.cin_bit = bit
                self.logger.info("Located CIN eye at %f bit %d", delay, bit)
            else:
                self.logger.info(f'Using CIN eye: {self.align.cin_delay} bit {self.align.cin_bit}')
            self.surf.setDelay(self.align.cin_delay)
            self.surf.turfioSetOffset(self.align.cin_bit)
            self.state = self.StartupState.TURFIO_LOCK
            self._runImmediate()
            return
        elif self.state == self.StartupState.TURFIO_LOCK:
            self.surf.turfio_lock_req = 1
            self.state = self.StartupState.WAIT_TURFIO_LOCKED
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_TURFIO_LOCKED:
            if not self.surf.turfio_locked_or_running:
                self._runNextTick()
                return
            self.logger.info("CIN is locked, waiting for remote to train.")
            # lower lock req, so that bit is now cin_running
            self.surf.turfio_lock_req = 0
            self.state = self.StartupState.ENABLE_TRAIN
            self._runImmediate()
            return
        elif self.state == self.StartupState.ENABLE_TRAIN:
            self.surf.turfio_train_enable = 1
            self.state = self.StartupState.WAIT_LIVE
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_LIVE:
            # We now just need to check for noop_live.
            # We don't actually check for running anymore,
            # because the TURFIO needs to sync us before
            # it even tries to train. So we just check
            # live seen.
            if not self.surf.live_seen:
                self._runNextTick()
                return
            self.surf.turfio_train_enable = 0
            self.logger.info("Remote finished training: CIN/COUT/DOUT OK.")
            self.state = self.StartupState.WAIT_SYNC
            self._runImmediate()
            return
        elif self.state == self.StartupState.WAIT_SYNC:
            if not self.surf.sync_seen:
                self._runImmediate()
                return
            self.logger.info("SYNC has been issued.")
            self.state = self.StartupState.MTS_STARTUP
            self._runNextTick()
            return
        elif self.state == self.StartupState.MTS_STARTUP:
            self.clock.surfClock.driveClock(self.clock.lmk_map['SYSREF'],
                                            self.clock.surfClock.DriveMode.HSDS_8)
            self.clock.surfClock.driveClock(self.clock.lmk_map['PLSYSREF'],
                                            self.clock.surfClock.DriveMode.HSDS_8)
            self.state = self.StartupState.RUN_MTS
            # give it a sec
            self._runNextTick()
            return
        elif self.state == self.StartupState.RUN_MTS:
            # must be reftile = 1 due to clock distribution
            self.surf.rfdc.MultiConverter_Init(self.surf.rfdc.ConverterType.ADC,
                                               refTile=1)
            self.surf.rfdc.mtsAdcConfig.target_latency = self.mts.target_latency
            self.surf.rfdc.mtsAdcConfig.sysref_enable = self.mts.sysref_enable
            r = self.surf.rfdc.MultiConverter_Sync(self.surf.rfdc.ConverterType.ADC)
            if r == 0:
                self.logger.info("MTS succeeded:")
                self.mts.latency = [ 0, 0, 0, 0 ]
                self.mts.latency[0] = self.surf.rfdc.mtsAdcConfig.Latency[0]
                self.mts.latency[1] = self.surf.rfdc.mtsAdcConfig.Latency[1]
                self.mts.latency[2] = self.surf.rfdc.mtsAdcConfig.Latency[2]
                self.mts.latency[3] = self.surf.rfdc.mtsAdcConfig.Latency[3]
                self.state = self.StartupState.MTS_SHUTDOWN
            else:
                self.logger.info("MTS failed?!?")
                self.state = self.StartupState.STARTUP_FAILURE
            self._runImmediate()
            return
        elif self.state == self.StartupState.MTS_SHUTDOWN:
            self.clock.surfClock.driveClock(self.clock.lmk_map['SYSREF'],
                                            self.clock.surfClock.DriveMode.POWERDOWN)
            self.clock.surfClock.driveClock(self.clock.lmk_map['PLSYSREF'],
                                            self.clock.surfClock.DriveMode.POWERDOWN)
            # 7/8 have a common clkdiv
            self.clock.surfClock.clockDividerEnable(self.clock.lmk_map['SYSREF'], False)
            # shut it all down, folks
            self.clock.surfClock.en_buf_clk_top = False
            self.clock.surfClock.en_buf_sync_top = False
            self.clock.surfClock.en_buf_sync_bottom = False
            # and shut the DAC down.
            # I could do this through their tools!
            # But I'm not going to!            
            self.surf.rfdc.dev.write(0x4008, 0x3)
            self.surf.rfdc.dev.write(0x4004, 0x1)
            self.state = self.StartupState.STARTUP_FINISH
            self._runNextTick()
            return
        elif self.state == self.StartupState.STARTUP_FINISH:
            self._runNextTick()
            return

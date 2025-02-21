import spi

from enum import Enum
import re
import struct
import time


class LMK0461x(spi.SPI):
    class DriveMode(Enum):
        POWERDOWN = 0
        HSDS_4 = 16
        HSDS_6 = 20
        HSDS_8 = 24
        HCSL_8 = 59
        HCSL_16 = 63

    # all the drive modes are 6 bits, but where they start is goofy
    clockDriveMap = { 1 : [ 0x34, 2 ],
                      2 : [ 0x35, 0 ],
                      3 : [ 0x37, 0 ],
                      4:  [ 0x38, 2 ],
                      5:  [ 0x39, 0 ],
                      6:  [ 0x3C, 2 ],
                      7:  [ 0x3D, 0 ],
                      8:  [ 0x3E, 2 ],
                      9:  [ 0x40, 2 ],
                      10: [ 0x41, 0 ] }
        
    def __init__(self, path='/dev/spidev1.0'):
        super().__init__(path)
        self.mode = self.MODE_0
        self.bits_per_word = 8
        self.speed = 500000

    # we *always* do single-byte transactions
    def readRegister(self, regNum):
        txd = [ 0x80 | ((regNum >> 8) & 0xFF), regNum & 0xFF, 0x00 ]
        rv = self.transfer(txd)
        return rv[2]

    def writeRegister(self, regNum, val):
        txd = [ ((regNum >> 8) & 0xFF), regNum & 0xFF, val & 0xFF]
        self.transfer(txd)
        
    def identify(self, verbose=False):
        # Clock ID is in registers 3/4/5/6. 4/5 can be read in 1 go
        # This might be a spidev issue or something? Dunno
        type = self.readRegister(3)
        id = (self.readRegister(4) << 8) | self.readRegister(5)
        ver = self.readRegister(6)
        if verbose:
            print("Type %2.2x ID %4.4x ver %2.2x" %
                  ( type, id, ver ))
        fullId = [ type, id, ver ]
        return fullId
    
    def driveClock(self, clockNum, drive=DriveMode.HSDS_4, verbose=True):
        reg = self.clockDriveMap[clockNum][0]
        startBit = self.clockDriveMap[clockNum][1]
        stopBit = startBit + 6
        if startBit > 0:
            maskBot = (1<<(startBit))-1
        else:
            maskBot = 0
        if stopBit < 8:
            maskTop = ((1<<(8-stopBit))-1)<<stopBit
        else:
            maskTop = 0
        mask = maskBot | maskTop
        if verbose:
            print("writing drive %2.2x to reg %2.2x %2.2x mask %2.2x" % 
                  ( drive.value, regTop, regBot, mask))
        oldVal = self.readRegister(reg)
        newVal = (oldVal & mask) | (drive.value << startBit)
        if verbose:
            print("current val %2.2x => %2.2x" % (oldVal, newVal))

        self.writeRegister(reg, newVal)
    
    def status(self, verbose=False):
        clkin = self.readRegister(0x124)
        if verbose:
            if (clkin & 0xF) == 0x4:
                print("CLKIN0 selected")
            elif (clkin & 0xF) == 0x8:
                print("CLKIN1 selected")
            else:
                print("Unknown Clock Input Source!!")
            
        st = self.readRegister(0xBE)
        if verbose:
            print("Status: %2.2x" % st)
            if st & 0x20:
                print(" - LOS: Loss of Source")
            if st & 0x10:
                print(" - HOLDOVER_DLD: Holdover - Digital Lock Detect")
            if st & 0x8:
                print(" - HOLDOVER_LOL: Holdover - Loss of Lock")
            if st & 0x4:
                print(" - HOLDOVER_LOS: Holdover - Loss of Source")
            if st & 0x2:
                print(" - PLL2_LCK_DET: PLL2 Lock Detect")
            if st & 0x1:
                print(" - PLL1_LCK_DET: PLL1 Lock Detect")
        return st
    
    def configure(self, ticsFilename):
        registers = []
        with open(ticsFilename, 'r') as f:
            lines = [l.rstrip("\n") for l in f]
            registers = []
            for i in lines:
                m = re.search('[\t]*(0x[0-9A-F]*)', i)
                registers.append(int(m.group(1),16),)
        # the overall programming sequence is:
        # set startup = 0
        self.transfer([0x00, 0x11, 0x00])
        # program all registers
        for v in registers:
            # this is a 3-byte val...
            data = struct.pack('>I', v)
            # so strip off the first zero byte after we pack by 4
            self.transfer(data[1:])
        # set PLL2 LD WINDW SIZE
        self.transfer([0x00, 0x85, 0x00])
        # set PLL2 DLD EN
        self.transfer([0x00, 0xF6, 0x02])
        # startup
        self.transfer([0x00, 0x11, 0x01])
        # clear lock detect
        self.transfer([0x00, 0xAD, 0x30])
        time.sleep(0.02)
        self.transfer([0x00, 0xAD, 0x00])
        

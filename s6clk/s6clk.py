# There are 2 possible clocks on the SURF6.
# we wrote the LMK module
from .LMK0461x import LMK0461x

from electronics.gateways import LinuxDevice
from electronics.devices import Si5395
from enum import Enum

import spi

import os
import time
import glob
import re
import struct
from pathlib import Path
from collections import defaultdict

class SURF6Clock:
    class Revision(Enum):
        REVA = 'Rev A'
        REVB = 'Rev B/C'
        
    def __init__(self, trenzClockBus=1):
        self.gw = LinuxDevice(trenzClockBus)
        self.trenzClock = Si5395(self.gw, 0x69)
        surfClockPath = self._find_lmk()
        if surfClockPath is None:
            print("no LMK04610 found, assuming rev A")
            self.rev = self.Revision.REVA
            self.surfClock = None
        else:
            self.rev = self.Revision.REVB
            self.surfClock = LMK0461x(surfClockPath)
            # we need to configure the LMK properly
            # first to talk to it.
            # We occasionally switch SYNC behavior so
            # make sure to force it properly here
            self.surfClockInit()

    # you HAVE TO DO THIS to read from an LMK on the SURF
    def surfClockInit(self):
        self.surfClock.writeRegister(0x141, 0x4)
        self.surfClock.writeRegister(0x142, 0x30)

    def identify(self):
        if self.rev == self.Revision.REVB:
            id = self.surfClock.identify()
            print("SURF Clock: type %2.2x id %4.4x rev %2.2x" %
                  ( id[0], id[1], id[2] ))
        id = self.trenzClock.identify()
        print("Trenz Clock: Si%2.2x%2.2x%c-%c-%c%c" %
              (id[1], id[0],chr(ord('A')+id[2]),chr(ord('A')+id[3]),
               'G' if id[4] == 0 else "?",
               'M' if id[5] == 0 else "?"))
            
    def _find_lmk(self):
        for dev in Path('/sys/bus/spi/devices').glob('*'):
            print("checking", dev)
            # Xilinx's original method for this was stupid
            fullCompatible = (dev / 'of_node' / 'compatible').read_text().rstrip('\x00')
            print(fullCompatible)
            if fullCompatible == "ti,lmk0461x":
                print("yup")
                if ( dev / 'driver').exists():
                    ( dev / 'driver' / 'unbind').write_text(dev.name)
                ( dev / 'driver_override').write_text('spidev')
                Path('/sys/bus/spi/drivers/spidev/bind').write_text(dev.name)
                devname = "/dev/spidev"+dev.name[3:]
                return devname
            else:
                print("nope")
        return None
    

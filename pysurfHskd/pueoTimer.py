from threading import Timer
import queue
import os
import selectors

class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)

class HskTimer(RepeatTimer):
    """ Periodic timer which writes to a pipe launching a callback every interval. """
    def __init__(self,
                 sel,
                 callback=None,
                 interval=1):
        """
        sel : selector used for multiple I/O handling
        callback : function to be called when timer goes off (see printTick)
        interval : time interval to run at (default 1)
        """
        self.rfd, self.wfd = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC )
        self.tickCount = 0
        if not callback:
            callback = self.printTick
            
        sel.register(self.rfd, selectors.EVENT_READ, callback)
        def tickFn():
            toWrite = (self.tickCount & 0xFF).to_bytes(1, 'big')
            os.write(self.wfd, toWrite)
            self.tickCount = self.tickCount + 1

        super(HskTimer, self).__init__(interval, tickFn)
    
    def printTick(self, fd, mask):
        """ dummy callback which just prints the current tick. """
        tick = os.read(fd, 1)
        print("tick", ord(tick))

    

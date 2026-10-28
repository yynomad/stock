"""One application/CLI writer per physical runtime directory (macOS/Linux)."""
import fcntl
from pathlib import Path

class DirectoryLock:
    def __init__(self,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        self.handle=(directory/'.stockman.lock').open('a')
        try:fcntl.flock(self.handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close();raise RuntimeError('runtime directory already in use; stop the other Bot/CLI first') from None
    def close(self):
        if not self.handle.closed:
            fcntl.flock(self.handle,fcntl.LOCK_UN);self.handle.close()

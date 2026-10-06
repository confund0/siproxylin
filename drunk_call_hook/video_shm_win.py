"""
Video frames from the call service over shared memory: the Windows part.

Same layout and Reader as drunk_call_hook/video_shm.py (Linux memfd). On
Windows the app makes an unnamed file mapping backed by the paging file and
passes the inheritable handle to the call service: the handle value goes in
the env variable SIPROXYLIN_VIDEO_SHM_HANDLE, and Popen gets the handle in
startupinfo handle_list, so the child inherits only this handle. No name, so
no other process can open the mapping.
C++ side: drunk_call_service/src/video_shm.cpp (_WIN32 parts).
"""

import ctypes
import subprocess

from .video_shm import Reader, TOTAL_SIZE, init_header

ENV_HANDLE = 'SIPROXYLIN_VIDEO_SHM_HANDLE'

# Windows API constants
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0x000F001F
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class SecurityAttributes(ctypes.Structure):
    """SECURITY_ATTRIBUTES of the Windows API."""
    _fields_ = [
        ('nLength', ctypes.c_uint32),
        ('lpSecurityDescriptor', ctypes.c_void_p),
        ('bInheritHandle', ctypes.c_int),
    ]


def _kernel32():
    """kernel32 with the argument types of the calls used here."""
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateFileMappingW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SecurityAttributes),
                                     ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                                     ctypes.c_wchar_p]
    k.CreateFileMappingW.restype = ctypes.c_void_p
    k.MapViewOfFile.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                ctypes.c_uint32, ctypes.c_size_t]
    k.MapViewOfFile.restype = ctypes.c_void_p
    k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    k.UnmapViewOfFile.restype = ctypes.c_int
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.CloseHandle.restype = ctypes.c_int
    return k


def _last_error() -> int:
    return ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0


class WindowsVideoShm:
    """
    The file mapping and its view. Keep it open while the service runs.
    Same interface as video_shm.VideoShm for the bridge: reader, close_fd().
    """

    def __init__(self, kernel32, handle: int, address: int):
        self._kernel32 = kernel32
        self.handle = handle
        self.address = address
        view = (ctypes.c_ubyte * TOTAL_SIZE).from_address(address)
        self.mm = memoryview(view).cast('B')
        self.reader = Reader(self.mm)

    def spawn_args(self, env: dict) -> dict:
        """Put the handle value into env. Returns the Popen arguments."""
        env[ENV_HANDLE] = str(self.handle)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.lpAttributeList = {'handle_list': [self.handle]}
        return {'startupinfo': startupinfo}

    def close_fd(self) -> None:
        """Nothing to do: the handle stays open while the service runs."""

    def close(self) -> None:
        """
        The service stopped: unmap the view and close the handle. Call it
        only when no reader is used any more (the memory is gone after it).
        """
        if self.address:
            # A later read through the reader then gives a ValueError, not a crash
            try:
                self.mm.release()
            except (AttributeError, BufferError, ValueError):
                pass
            self._kernel32.UnmapViewOfFile(self.address)
            self.address = None
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def create(kernel32=None) -> WindowsVideoShm:
    """Make the unnamed, inheritable file mapping with the header."""
    if kernel32 is None:
        kernel32 = _kernel32()
    attrs = SecurityAttributes()
    attrs.nLength = ctypes.sizeof(SecurityAttributes)
    attrs.lpSecurityDescriptor = None
    attrs.bInheritHandle = 1
    handle = kernel32.CreateFileMappingW(INVALID_HANDLE_VALUE, ctypes.byref(attrs),
                                         PAGE_READWRITE, TOTAL_SIZE >> 32,
                                         TOTAL_SIZE & 0xFFFFFFFF, None)
    if not handle:
        raise OSError(f"CreateFileMappingW failed: error {_last_error()}")
    address = kernel32.MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, TOTAL_SIZE)
    if not address:
        error = _last_error()
        kernel32.CloseHandle(handle)
        raise OSError(f"MapViewOfFile failed: error {error}")
    v = WindowsVideoShm(kernel32, handle, address)
    init_header(v.mm)
    return v

"""One bounded native scan. Binary stdin, metadata-only stdout, no payload files."""
import ctypes
import hashlib
import json
import os
import sys

MAX_BYTES=5*1024*1024


def scan(data):
    if os.name!='nt':return -2147467263,None
    from ctypes import wintypes
    api=ctypes.WinDLL('amsi.dll',winmode=0x00000800)  # System32 only.
    pointer=ctypes.c_void_p
    api.AmsiInitialize.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(pointer)]
    api.AmsiInitialize.restype=ctypes.c_long
    api.AmsiOpenSession.argtypes=[pointer,ctypes.POINTER(pointer)]
    api.AmsiOpenSession.restype=ctypes.c_long
    api.AmsiScanBuffer.argtypes=[pointer,pointer,wintypes.ULONG,wintypes.LPCWSTR,pointer,ctypes.POINTER(wintypes.ULONG)]
    api.AmsiScanBuffer.restype=ctypes.c_long
    api.AmsiCloseSession.argtypes=[pointer,pointer];api.AmsiCloseSession.restype=None
    api.AmsiUninitialize.argtypes=[pointer];api.AmsiUninitialize.restype=None
    context=pointer();session=pointer();opened=False
    status=api.AmsiInitialize('AirportAssistantLocalAttachment',ctypes.byref(context))
    if status!=0 or not context.value:return status or -2147467259,None
    try:
        status=api.AmsiOpenSession(context,ctypes.byref(session))
        if status!=0 or not session.value:return status or -2147467259,None
        opened=True
        buffer=ctypes.create_string_buffer(data)
        result=wintypes.ULONG()
        status=api.AmsiScanBuffer(context,ctypes.cast(buffer,pointer),len(data),'opaque-attachment',session,ctypes.byref(result))
        if buffer.raw[:len(data)]!=data:return -2147467259,None
        return status,result.value if status==0 else None
    finally:
        if opened:api.AmsiCloseSession(context,session)
        api.AmsiUninitialize(context)


def main():
    data=sys.stdin.buffer.read(MAX_BYTES+1)
    status,result=-2147024809,None
    if 0<len(data)<=MAX_BYTES:
        try:status,result=scan(data)
        except (OSError,ValueError,AttributeError):status,result=-2147467259,None
    print(json.dumps(dict(hresult=status,result=result,byte_size=len(data),sha256=hashlib.sha256(data).hexdigest()),separators=(',',':')))


if __name__=='__main__':main()

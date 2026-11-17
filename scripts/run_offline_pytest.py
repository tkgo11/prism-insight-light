"""Run pytest with inherited Linux seccomp denial of non-Unix sockets."""
import ctypes
import ctypes.util
import errno
import os
import socket
import sys

lib = ctypes.CDLL(ctypes.util.find_library('seccomp'), use_errno=True)
class ArgCmp(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_uint), ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]
lib.seccomp_init.argtypes = [ctypes.c_uint32]
lib.seccomp_init.restype = ctypes.c_void_p
lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(ArgCmp)]
lib.seccomp_load.argtypes = [ctypes.c_void_p]
lib.seccomp_release.argtypes = [ctypes.c_void_p]
ctx = lib.seccomp_init(0x7fff0000)
if not ctx:
    raise RuntimeError('seccomp_init failed')
try:
    for syscall in (b'socket', b'socketpair'):
        nr = lib.seccomp_syscall_resolve_name(syscall)
        # SCMP_CMP_NE: permit AF_UNIX only. Deny IPv4/6 and raw packet sockets.
        compare = ArgCmp(0, 1, socket.AF_UNIX, 0)
        rc = lib.seccomp_rule_add_array(ctx, 0x00050000 | errno.EPERM, nr, 1, ctypes.byref(compare))
        if rc != 0:
            raise RuntimeError(f'seccomp rule failed {syscall!r}: {rc}')
    if lib.seccomp_load(ctx) != 0:
        raise RuntimeError('seccomp_load failed')
finally:
    lib.seccomp_release(ctx)
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        socket.socket(family)
    except PermissionError:
        pass
    else:
        raise RuntimeError('offline guard did not deny IP socket')
print('OFFLINE_SECCOMP_ACTIVE: IPv4/IPv6/packet sockets denied, inherited by subprocesses', flush=True)
os.environ['SIGNAL_FRESH_QUOTE_ENABLED'] = 'false'
sys.path.insert(0, os.getcwd())
import pytest
raise SystemExit(pytest.main(sys.argv[1:]))

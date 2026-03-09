"""eBPF-based per-process network traffic collector (TCP + UDP).

Attaches kprobes to kernel functions that handle TCP/UDP send and receive.
Byte counts are aggregated per-PID in a BPF hash map in kernel space.
User space reads and clears the map each collection cycle (~0.1ms).

Requires root privileges (CAP_BPF + CAP_SYS_ADMIN) and python3-bpfcc.
"""

import os
import sys
from typing import Dict

# BPF C program — compiled and loaded into the kernel by BCC.
# We only need <uapi/linux/ptrace.h> for PT_REGS_RC.
# We do NOT include <net/sock.h> — it triggers compile errors on newer
# kernels (6.17+) due to struct bpf_wq forward declaration issues.
# Instead we read kprobe arguments positionally via PT_REGS_PARM*.
_BPF_TEXT = r"""
#include <uapi/linux/ptrace.h>

struct traffic_key_t {
    u32 pid;
    u32 protocol;   // 1 = TCP, 2 = UDP
    u32 direction;  // 0 = TX (send), 1 = RX (receive)
};

BPF_HASH(traffic_stats, struct traffic_key_t, u64);

static inline void update_stats(u32 pid, u32 protocol, u32 direction, u64 bytes) {
    if (bytes == 0) return;

    struct traffic_key_t key = {.pid = pid, .protocol = protocol, .direction = direction};
    u64 *val, zero = 0;

    val = traffic_stats.lookup_or_try_init(&key, &zero);
    if (val) {
        __sync_fetch_and_add(val, bytes);
    }
}

// --- TCP ---
// tcp_sendmsg(struct sock *sk, struct msghdr *msg, size_t size)
// size is the 3rd parameter
int kprobe__tcp_sendmsg(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 size = (u64)PT_REGS_PARM3(ctx);
    update_stats(pid, 1, 0, size);
    return 0;
}

// tcp_cleanup_rbuf(struct sock *sk, int copied)
// copied is the 2nd parameter
int kprobe__tcp_cleanup_rbuf(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    int copied = (int)PT_REGS_PARM2(ctx);
    if (copied > 0) {
        update_stats(pid, 1, 1, (u64)copied);
    }
    return 0;
}

// --- UDP ---
// udp_sendmsg(struct sock *sk, struct msghdr *msg, size_t len)
// len is the 3rd parameter
int kprobe__udp_sendmsg(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    u64 len = (u64)PT_REGS_PARM3(ctx);
    update_stats(pid, 2, 0, len);
    return 0;
}

// udp_recvmsg return value = bytes received
int kretprobe__udp_recvmsg(struct pt_regs *ctx) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    int copied = PT_REGS_RC(ctx);
    if (copied > 0) {
        update_stats(pid, 2, 1, (u64)copied);
    }
    return 0;
}
"""


class EbpfNetCollector:
    """Collects per-PID TCP+UDP byte counts using eBPF kprobes.

    On construction, compiles the BPF program and attaches probes.
    If anything fails (missing BCC, missing privileges, kernel issues),
    ``available`` remains False and the caller should use a fallback.
    """

    def __init__(self):
        self._bpf = None
        self._available = False
        self._try_init()

    def _try_init(self):
        """Attempt to compile and attach the eBPF program.

        Suppresses BCC's stderr output (compiler warnings/errors) since
        we fall back to ss gracefully on failure.
        """
        try:
            from bcc import BPF
            # Suppress BCC compiler output (warnings, errors) to stderr.
            # On failure we fall back to ss, so the user doesn't need to
            # see pages of kernel header warnings.
            stderr_fd = sys.stderr.fileno()
            saved_stderr = os.dup(stderr_fd)
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, stderr_fd)
            os.close(devnull)
            try:
                self._bpf = BPF(text=_BPF_TEXT)
                self._available = True
            finally:
                os.dup2(saved_stderr, stderr_fd)
                os.close(saved_stderr)
        except Exception:
            self._bpf = None
            self._available = False

    @property
    def available(self) -> bool:
        """Whether eBPF probes were successfully attached."""
        return self._available

    def collect_stats(self) -> Dict[int, Dict[str, int]]:
        """Read and clear the BPF traffic map.

        Returns:
            Dict mapping PID to byte counts accumulated since last call::

                {
                    pid: {
                        'tcp_tx': int,
                        'tcp_rx': int,
                        'udp_tx': int,
                        'udp_rx': int,
                    },
                    ...
                }
        """
        if not self._available:
            return {}

        stats_map = self._bpf["traffic_stats"]
        result: Dict[int, Dict[str, int]] = {}

        for key, val in stats_map.items():
            pid = key.pid
            if pid not in result:
                result[pid] = {'tcp_tx': 0, 'tcp_rx': 0, 'udp_tx': 0, 'udp_rx': 0}

            field = self._key_to_field(key.protocol, key.direction)
            result[pid][field] += val.value

        # Clear the map for the next interval
        stats_map.clear()

        return result

    @staticmethod
    def _key_to_field(protocol: int, direction: int) -> str:
        """Map (protocol, direction) to field name."""
        if protocol == 1:
            return 'tcp_tx' if direction == 0 else 'tcp_rx'
        else:
            return 'udp_tx' if direction == 0 else 'udp_rx'

    def shutdown(self):
        """Clean up BPF resources."""
        if self._bpf is not None:
            self._bpf.cleanup()
            self._bpf = None
            self._available = False

"""Tests for the system collectors against the machine running the tests."""

from ros2_vitals.collectors import DiskCollector, NetworkCollector, SystemCollector
from ros2_vitals.collectors.ebpf_net_collector import EbpfNetCollector
from ros2_vitals.collectors.net_stats_collector import NetStatsCollector


SS_OUTPUT = """\
State Recv-Q Send-Q Local Address:Port Peer Address:Port Process
ESTAB 0      0      127.0.0.1:41000    127.0.0.1:7400    users:(("talker",pid=1234,fd=12))
\t cubic wscale:7,7 rto:204 bytes_sent:500 bytes_acked:400 bytes_received:900 segs_out:10
ESTAB 0      0      127.0.0.1:41002    127.0.0.1:7410    users:(("talker",pid=1234,fd=13))
\t cubic wscale:7,7 rto:204 bytes_acked:100 bytes_received:50 segs_out:10
ESTAB 0      0      127.0.0.1:41004    127.0.0.1:7420    users:(("listener",pid=99,fd=4))
\t cubic wscale:7,7 rto:204 bytes_acked:7 bytes_received:3 segs_out:1
ESTAB 0      0      127.0.0.1:41006    127.0.0.1:7430
\t cubic wscale:7,7 rto:204 bytes_acked:1000 bytes_received:1000 segs_out:1
"""


def test_parse_ss_output():
    stats = NetStatsCollector._parse_ss_output(SS_OUTPUT)
    assert stats == {
        1234: {"rx_total": 950, "tx_total": 500},
        99: {"rx_total": 3, "tx_total": 7},
    }


def test_parse_ss_output_empty():
    assert NetStatsCollector._parse_ss_output("") == {}


def test_ebpf_key_to_field():
    assert EbpfNetCollector._key_to_field(1, 0) == "tcp_tx"
    assert EbpfNetCollector._key_to_field(1, 1) == "tcp_rx"
    assert EbpfNetCollector._key_to_field(2, 0) == "udp_tx"
    assert EbpfNetCollector._key_to_field(2, 1) == "udp_rx"


def test_system_collect_all():
    system = SystemCollector().collect_all()
    assert system["hostname"]
    assert system["cpu_count"] >= 1
    assert len(system["cpu_per_core"]) == system["cpu_count"]
    assert 0.0 <= system["cpu_percent"] <= 100.0
    assert system["ram_total_bytes"] > 0
    assert 0 <= system["ram_used_bytes"] <= system["ram_total_bytes"]
    assert system["uptime_seconds"] > 0


def test_network_interfaces():
    interfaces = NetworkCollector().get_interfaces()
    assert isinstance(interfaces, list)
    for iface in interfaces:
        assert iface["name"]


def test_disk_partitions():
    partitions = DiskCollector().get_partitions()
    assert isinstance(partitions, list)
    for part in partitions:
        assert part["mount_point"]
        assert part["total_bytes"] >= part["used_bytes"] >= 0

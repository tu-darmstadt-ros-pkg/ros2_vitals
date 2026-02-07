import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/**
 * Card displaying system status for a single host.
 */
Rectangle {
    id: root

    //! Host status data
    property var status: null

    //! Whether this host is selected
    property bool selected: false

    //! Alert thresholds
    property real cpuWarning: 70
    property real cpuError: 90
    property real ramWarning: 75
    property real ramError: 90
    property real tempWarning: 75
    property real tempError: 85

    signal clicked()

    implicitHeight: contentColumn.implicitHeight + 16
    color: selected ? Qt.darker(palette.base, 1.1) : palette.base
    border.color: selected ? palette.highlight : palette.mid
    border.width: selected ? 2 : 1
    radius: 6

    MouseArea {
        anchors.fill: parent
        onClicked: root.clicked()
    }

    ColumnLayout {
        id: contentColumn
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6

        // Header: hostname and IPs
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: status ? status.hostname : "Unknown"
                font.bold: true
                font.pixelSize: 14
            }

            Item { Layout.fillWidth: true }

            Label {
                visible: status && status.ip_addresses && status.ip_addresses.length > 0
                text: status && status.ip_addresses && status.ip_addresses.length > 0
                    ? status.ip_addresses.at(0) : ""
                font.pixelSize: 11
                opacity: 0.7
            }
        }

        // CPU bar
        UsageBar {
            Layout.fillWidth: true
            label: "CPU"
            value: status ? status.cpu_percent : 0
            detail: status ? status.cpu_count + " cores" : ""
            warningThreshold: root.cpuWarning
            errorThreshold: root.cpuError
        }

        // RAM bar
        UsageBar {
            Layout.fillWidth: true
            label: "RAM"
            value: status && status.ram_total_bytes > 0
                ? (status.ram_used_bytes / status.ram_total_bytes) * 100
                : 0
            detail: status ? formatBytes(status.ram_used_bytes) + " / " + formatBytes(status.ram_total_bytes) : ""
            warningThreshold: root.ramWarning
            errorThreshold: root.ramError
        }

        // GPU bars (if available)
        Repeater {
            id: gpuRepeater
            model: status && status.gpus ? status.gpus.length : 0

            ColumnLayout {
                Layout.fillWidth: true
                required property int index
                spacing: 2

                property var gpu: status.gpus.at(index)
                property real memPercent: gpu && gpu.memory_total_bytes > 0
                    ? (gpu.memory_used_bytes / gpu.memory_total_bytes) * 100
                    : 0

                // GPU utilization bar
                UsageBar {
                    Layout.fillWidth: true
                    label: "GPU" + parent.index
                    value: gpu.utilization_percent
                    detail: (gpu.temperature_celsius > 0 ? gpu.temperature_celsius.toFixed(0) + "°C" : "")
                           + (gpu.power_watts > 0 ? " " + gpu.power_watts.toFixed(0) + "W" : "")
                }

                // VRAM bar
                UsageBar {
                    Layout.fillWidth: true
                    label: "  VRAM"
                    value: parent.memPercent
                    detail: formatBytes(gpu.memory_used_bytes) + " / " + formatBytes(gpu.memory_total_bytes)
                }
            }
        }

        // Network summary
        RowLayout {
            Layout.fillWidth: true
            spacing: 16

            Label {
                text: "Net:"
                font.pixelSize: 11
                opacity: 0.7
            }

            Label {
                property real totalSend: {
                    if (!status || !status.network_interfaces || status.network_interfaces.length === 0) return 0;
                    let total = 0;
                    for (let i = 0; i < status.network_interfaces.length; ++i) {
                        const iface = status.network_interfaces.at(i);
                        if (iface) total += iface.bytes_sent_per_sec;
                    }
                    return total;
                }
                text: "↑ " + formatBytesRate(totalSend)
                font.pixelSize: 11
            }

            Label {
                property real totalRecv: {
                    if (!status || !status.network_interfaces || status.network_interfaces.length === 0) return 0;
                    let total = 0;
                    for (let i = 0; i < status.network_interfaces.length; ++i) {
                        const iface = status.network_interfaces.at(i);
                        if (iface) total += iface.bytes_recv_per_sec;
                    }
                    return total;
                }
                text: "↓ " + formatBytesRate(totalRecv)
                font.pixelSize: 11
            }

            Item { Layout.fillWidth: true }

            // Load average
            Label {
                visible: status && status.load_avg_1min > 0
                text: "Load: " + (status ? status.load_avg_1min.toFixed(2) : "")
                font.pixelSize: 11
                opacity: 0.7
            }
        }

        // Process count
        RowLayout {
            Layout.fillWidth: true

            Label {
                text: status && status.processes
                    ? status.processes.length + " ROS processes"
                    : "No processes"
                font.pixelSize: 11
                opacity: 0.7
            }

            Item { Layout.fillWidth: true }

            // Uptime
            Label {
                visible: status && status.uptime_seconds > 0
                text: "Up: " + formatUptime(status ? status.uptime_seconds : 0)
                font.pixelSize: 11
                opacity: 0.7
            }
        }
    }

    // Helper functions
    function formatBytes(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
        return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
    }

    function formatBytesRate(bytesPerSec) {
        if (bytesPerSec < 1024) return bytesPerSec.toFixed(0) + " B/s";
        if (bytesPerSec < 1024 * 1024) return (bytesPerSec / 1024).toFixed(1) + " KB/s";
        return (bytesPerSec / (1024 * 1024)).toFixed(1) + " MB/s";
    }

    function formatUptime(seconds) {
        if (seconds < 3600) return Math.floor(seconds / 60) + "m";
        if (seconds < 86400) return (seconds / 3600).toFixed(1) + "h";
        return (seconds / 86400).toFixed(1) + "d";
    }
}

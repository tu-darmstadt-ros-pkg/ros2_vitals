import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import Ros2
import RQml.Elements
import RQml.Fonts
import "interfaces"
import "elements"

/**
 * System Monitor plugin for visualizing ROS 2 Vitals data.
 * Shows system metrics for multiple hosts and their ROS processes.
 */
Rectangle {
    id: root
    anchors.fill: parent
    property var kddockwidgets_min_size: Qt.size(500, 400)
    color: palette.base

    Component.onCompleted: {
        if (context.enabled === undefined)
            context.enabled = true;
        if (!context.topic)
            context.topic = "/vitals/status";
        if (!context.selectedHost)
            context.selectedHost = "";
        // Always reset to sorting by name ascending on startup
        context.sortColumn = "name";
        context.sortAscending = true;
        if (!context.expandedLaunches)
            context.expandedLaunches = {};
    }

    // ========================================================================
    // Alert Thresholds
    // ========================================================================

    QtObject {
        id: thresholds
        property real cpuWarning: 70
        property real cpuError: 90
        property real ramWarning: 75
        property real ramError: 90
        property real tempWarning: 75
        property real tempError: 85
    }

    // ========================================================================
    // Private Data
    // ========================================================================

    // Process list model - using ListModel to preserve scroll position during updates
    ListModel {
        id: processListModel
    }

    QtObject {
        id: d

        property var vitalsInterface: VitalsInterface {
            enabled: context.enabled ?? true
            topic: context.topic || "/vitals/status"
            onDataUpdated: d.updateProcessModel()
        }

        // Track last update to avoid unnecessary model rebuilds
        property var lastProcessData: null

        /**
         * Update the process ListModel with current data.
         * Uses in-place updates to preserve scroll position.
         */
        function updateProcessModel() {
            const newData = getProcesses();

            // Update model size
            while (processListModel.count > newData.length) {
                processListModel.remove(processListModel.count - 1);
            }
            while (processListModel.count < newData.length) {
                processListModel.append({});
            }

            // Update each item in place
            for (let i = 0; i < newData.length; i++) {
                processListModel.set(i, {
                    proc: newData[i].proc,
                    isLaunch: newData[i].isLaunch || false,
                    isChild: newData[i].isChild || false,
                    launchKey: newData[i].launchKey || "",
                    expanded: newData[i].expanded || false,
                    isLast: newData[i].isLast || false
                });
            }
        }

        /**
         * Get processes for selected host, sorted and flattened with hierarchy info.
         * Returns array of objects with: proc, isLaunch, isChild, expanded
         */
        function getProcesses() {
            if (!context.selectedHost || !vitalsInterface.hosts[context.selectedHost])
                return [];

            const status = vitalsInterface.hosts[context.selectedHost].status;
            if (!status || !status.processes || status.processes.length === 0)
                return [];

            // Copy ROS array to JS array using .at() method
            let procs = [];
            for (let i = 0; i < status.processes.length; ++i) {
                procs.push(status.processes.at(i));
            }

            // Default to name ascending
            const col = context.sortColumn || "name";
            const asc = context.sortAscending !== undefined ? context.sortAscending : true;

            // Sort function for processes
            function sortProcs(a, b) {
                let valA, valB;
                switch (col) {
                    case "cpu":
                        valA = a.cpu_percent;
                        valB = b.cpu_percent;
                        break;
                    case "ram":
                        valA = a.ram_bytes;
                        valB = b.ram_bytes;
                        break;
                    case "gpu":
                        valA = a.gpu_memory_bytes;
                        valB = b.gpu_memory_bytes;
                        break;
                    case "disk":
                        valA = a.disk_read_bytes_per_sec + a.disk_write_bytes_per_sec;
                        valB = b.disk_read_bytes_per_sec + b.disk_write_bytes_per_sec;
                        break;
                    case "net_in":
                        valA = a.net_rx_bytes_per_sec || 0;
                        valB = b.net_rx_bytes_per_sec || 0;
                        break;
                    case "net_out":
                        valA = a.net_tx_bytes_per_sec || 0;
                        valB = b.net_tx_bytes_per_sec || 0;
                        break;
                    case "name":
                    default:
                        valA = a.node_name || a.cmdline;
                        valB = b.node_name || b.cmdline;
                        return asc
                            ? valA.localeCompare(valB)
                            : valB.localeCompare(valA);
                }
                return asc ? valA - valB : valB - valA;
            }

            procs.sort(sortProcs);

            // Build flat list with hierarchy info
            let result = [];
            for (let i = 0; i < procs.length; ++i) {
                const proc = procs[i];
                const launchKey = proc.pid.toString();
                const isExpanded = context.expandedLaunches && context.expandedLaunches[launchKey];

                if (proc.is_launch_process) {
                    // Add launch group header
                    result.push({
                        proc: proc,
                        isLaunch: true,
                        isChild: false,
                        launchKey: launchKey,
                        expanded: isExpanded || false,
                    });

                    // Add child nodes if expanded
                    if (isExpanded && proc.child_nodes && proc.child_nodes.length > 0) {
                        // Copy and sort child nodes
                        let children = [];
                        for (let j = 0; j < proc.child_nodes.length; ++j) {
                            children.push(proc.child_nodes.at(j));
                        }
                        children.sort(sortProcs);

                        for (let j = 0; j < children.length; ++j) {
                            result.push({
                                proc: children[j],
                                isLaunch: false,
                                isChild: true,
                                launchKey: launchKey,
                                isLast: j === children.length - 1,
                            });
                        }
                    }
                } else {
                    // Standalone process
                    result.push({
                        proc: proc,
                        isLaunch: false,
                        isChild: false,
                    });
                }
            }

            return result;
        }

        function toggleLaunchExpanded(launchKey) {
            if (!context.expandedLaunches)
                context.expandedLaunches = {};
            const current = context.expandedLaunches[launchKey] || false;
            // Create a new object to trigger property change
            let newExpanded = {};
            for (let key in context.expandedLaunches) {
                newExpanded[key] = context.expandedLaunches[key];
            }
            newExpanded[launchKey] = !current;
            context.expandedLaunches = newExpanded;
            // Immediately update model when expanding/collapsing
            updateProcessModel();
        }

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
    }

    // Re-sort when sort settings change
    Connections {
        target: context
        function onSortColumnChanged() { d.updateProcessModel(); }
        function onSortAscendingChanged() { d.updateProcessModel(); }
        function onSelectedHostChanged() { d.updateProcessModel(); }
    }

    // ========================================================================
    // UI Layout
    // ========================================================================

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 8

        // --------------------------------------------------------------------
        // Header Row
        // --------------------------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: "Topic:"
            }

            TextField {
                Layout.fillWidth: true
                text: context.topic || "/vitals/status"
                onEditingFinished: context.topic = text
            }

            IconToggleButton {
                iconOn: IconFont.iconPause
                iconOff: IconFont.iconPlay
                tooltipTextOn: "Click to pause"
                tooltipTextOff: "Click to resume"
                checked: context.enabled ?? true
                onToggled: context.enabled = checked
            }

            IconButton {
                text: IconFont.iconTrash
                tooltipText: "Clear all data"
                onClicked: d.vitalsInterface.clear()
            }
        }

        // --------------------------------------------------------------------
        // Host Cards
        // --------------------------------------------------------------------

        Label {
            text: "Hosts (" + d.vitalsInterface.hostCount + ")"
            font.bold: true
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: hostCardsRow.height + 10
            Layout.minimumHeight: 170
            Layout.maximumHeight: 300
            clip: true

            Row {
                id: hostCardsRow
                spacing: 8

                Repeater {
                    model: d.vitalsInterface.hostnames

                    HostCard {
                        required property string modelData
                        required property int index

                        width: 300
                        // Dynamic height based on GPU count
                        property int gpuCount: status && status.gpus ? status.gpus.length : 0
                        height: 160 + (gpuCount * 76)  // 76px per GPU (2 bars + spacing)
                        status: d.vitalsInterface.hosts[modelData]
                            ? d.vitalsInterface.hosts[modelData].status
                            : null
                        selected: context.selectedHost === modelData
                        cpuWarning: thresholds.cpuWarning
                        cpuError: thresholds.cpuError
                        ramWarning: thresholds.ramWarning
                        ramError: thresholds.ramError

                        onClicked: context.selectedHost = modelData

                        Component.onCompleted: {
                            // Auto-select first host
                            if (!context.selectedHost && index === 0)
                                context.selectedHost = modelData;
                        }
                    }
                }

                // Empty state
                Rectangle {
                    visible: d.vitalsInterface.hostCount === 0
                    width: 300
                    height: 160
                    color: palette.alternateBase
                    radius: 6
                    border.color: palette.mid

                    Label {
                        anchors.centerIn: parent
                        text: context.enabled
                            ? "Waiting for vitals data...\nSubscribed to: " + (context.topic || "/vitals/status")
                            : "Paused"
                        horizontalAlignment: Text.AlignHCenter
                        color: palette.mid
                    }
                }
            }
        }

        // --------------------------------------------------------------------
        // Process Table Header
        // --------------------------------------------------------------------

        Label {
            Layout.fillWidth: true
            text: context.selectedHost
                ? "Processes on " + context.selectedHost
                : "Select a host"
            font.bold: true
        }

        // --------------------------------------------------------------------
        // Process Table
        // --------------------------------------------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: palette.alternateBase
            radius: 4

            ListView {
                id: processListView
                anchors.fill: parent
                anchors.margins: 4
                clip: true
                model: processListModel
                cacheBuffer: 200  // Cache items for smoother scrolling
                reuseItems: true
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {
                    policy: processListView.contentHeight > processListView.height
                        ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                }

                header: Rectangle {
                    id: tableHeader
                    width: processListView.width
                    height: 28
                    color: palette.mid
                    z: 2

                    // Helper: sort arrow for active column
                    function sortArrow(columnId) {
                        if ((context.sortColumn || "name") !== columnId) return "";
                        return context.sortAscending ? " \u2191" : " \u2193";
                    }

                    // Helper: click handler for header columns
                    function headerClicked(columnId) {
                        if ((context.sortColumn || "name") === columnId) {
                            context.sortAscending = !context.sortAscending;
                        } else {
                            context.sortColumn = columnId;
                            context.sortAscending = columnId === "name";  // Name defaults asc, others desc
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 20
                        spacing: 8

                        Label {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 200
                            text: "Node" + tableHeader.sortArrow("name")
                            font.bold: (context.sortColumn || "name") === "name"
                            opacity: (context.sortColumn || "name") === "name" ? 1.0 : 0.7
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("name")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 60
                            text: "CPU" + tableHeader.sortArrow("cpu")
                            font.bold: context.sortColumn === "cpu"
                            opacity: context.sortColumn === "cpu" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("cpu")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 80
                            text: "RAM" + tableHeader.sortArrow("ram")
                            font.bold: context.sortColumn === "ram"
                            opacity: context.sortColumn === "ram" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("ram")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 80
                            text: "GPU" + tableHeader.sortArrow("gpu")
                            font.bold: context.sortColumn === "gpu"
                            opacity: context.sortColumn === "gpu" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("gpu")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 70
                            text: "Disk" + tableHeader.sortArrow("disk")
                            font.bold: context.sortColumn === "disk"
                            opacity: context.sortColumn === "disk" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("disk")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 70
                            text: "Net In" + tableHeader.sortArrow("net_in")
                            font.bold: context.sortColumn === "net_in"
                            opacity: context.sortColumn === "net_in" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("net_in")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 70
                            text: "Net Out" + tableHeader.sortArrow("net_out")
                            font.bold: context.sortColumn === "net_out"
                            opacity: context.sortColumn === "net_out" ? 1.0 : 0.7
                            horizontalAlignment: Text.AlignRight
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: tableHeader.headerClicked("net_out")
                            }
                        }

                        Label {
                            Layout.preferredWidth: 60
                            text: "Status"
                            opacity: 0.7
                            horizontalAlignment: Text.AlignCenter
                        }

                        // Kill column header (empty, just spacing)
                        Item {
                            Layout.preferredWidth: 28
                        }
                    }
                }
                headerPositioning: ListView.OverlayHeader

                delegate: Rectangle {
                    id: delegateRoot
                    required property int index
                    // ListModel properties
                    required property var proc
                    required property bool isLaunch
                    required property bool isChild
                    required property string launchKey
                    required property bool expanded
                    required property bool isLast

                    width: processListView.width
                    height: 32
                    color: isLaunch ? Qt.rgba(palette.highlight.r, palette.highlight.g, palette.highlight.b, 0.3)
                         : index % 2 === 0 ? palette.base : palette.alternateBase

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 20
                        spacing: 8

                        // Expand/collapse button for launch processes
                        Label {
                            Layout.preferredWidth: 20
                            visible: delegateRoot.isLaunch
                            text: delegateRoot.expanded ? "\u25BC" : "\u25B6"  // ▼ or ▶
                            font.pixelSize: 10
                            horizontalAlignment: Text.AlignCenter

                            MouseArea {
                                anchors.fill: parent
                                onClicked: d.toggleLaunchExpanded(delegateRoot.launchKey)
                            }
                        }

                        // Tree branch for child nodes
                        Label {
                            Layout.preferredWidth: 20
                            visible: delegateRoot.isChild
                            text: delegateRoot.isLast ? "\u2514\u2500" : "\u251C\u2500"  // └─ or ├─
                            font.pixelSize: 11
                            color: palette.mid
                        }

                        // Spacer when no icon needed
                        Item {
                            Layout.preferredWidth: 20
                            visible: !delegateRoot.isLaunch && !delegateRoot.isChild
                        }

                        // Node name
                        Label {
                            Layout.fillWidth: true
                            Layout.preferredWidth: 180
                            text: {
                                if (delegateRoot.isLaunch) {
                                    const childCount = proc.child_nodes ? proc.child_nodes.length : 0;
                                    return (proc.launch_name || proc.node_name) + " (" + childCount + ")";
                                }
                                return proc.node_name || proc.cmdline.split(' ')[0];
                            }
                            elide: Text.ElideRight
                            font.bold: delegateRoot.isLaunch

                            ToolTip.visible: nameMouseArea.containsMouse
                            ToolTip.text: proc.cmdline

                            MouseArea {
                                id: nameMouseArea
                                anchors.fill: parent
                                hoverEnabled: true
                                onClicked: {
                                    if (delegateRoot.isLaunch)
                                        d.toggleLaunchExpanded(delegateRoot.launchKey);
                                }
                            }
                        }

                        // CPU
                        Label {
                            Layout.preferredWidth: 60
                            text: proc.cpu_percent.toFixed(1) + "%"
                            color: proc.cpu_percent > thresholds.cpuError ? Material.color(Material.Red)
                                 : proc.cpu_percent > thresholds.cpuWarning ? Material.color(Material.Orange)
                                 : palette.text
                            horizontalAlignment: Text.AlignRight
                        }

                        // RAM
                        Label {
                            Layout.preferredWidth: 80
                            text: d.formatBytes(proc.ram_bytes)
                            horizontalAlignment: Text.AlignRight
                        }

                        // GPU
                        Label {
                            Layout.preferredWidth: 80
                            text: proc.gpu_index >= 0
                                ? d.formatBytes(proc.gpu_memory_bytes)
                                : "-"
                            color: proc.gpu_index >= 0 ? palette.text : palette.mid
                            horizontalAlignment: Text.AlignRight
                        }

                        // Disk I/O
                        Label {
                            Layout.preferredWidth: 70
                            property real diskRate: proc.disk_read_bytes_per_sec
                                + proc.disk_write_bytes_per_sec
                            text: d.formatBytesRate(diskRate)
                            horizontalAlignment: Text.AlignRight
                        }

                        // Network In
                        Label {
                            Layout.preferredWidth: 70
                            text: d.formatBytesRate(proc.net_rx_bytes_per_sec || 0)
                            color: (proc.net_rx_bytes_per_sec || 0) > 0 ? palette.text : palette.mid
                            horizontalAlignment: Text.AlignRight
                        }

                        // Network Out
                        Label {
                            Layout.preferredWidth: 70
                            text: d.formatBytesRate(proc.net_tx_bytes_per_sec || 0)
                            color: (proc.net_tx_bytes_per_sec || 0) > 0 ? palette.text : palette.mid
                            horizontalAlignment: Text.AlignRight
                        }

                        // Status
                        Rectangle {
                            Layout.preferredWidth: 60
                            Layout.preferredHeight: 20
                            radius: 3
                            // Translate Linux process status to user-friendly display
                            property string displayStatus: {
                                switch (proc.status) {
                                    case "sleeping": return "idle";  // More intuitive than "sleeping"
                                    case "disk-sleep": return "I/O";
                                    default: return proc.status;
                                }
                            }
                            color: {
                                switch (proc.status) {
                                    case "running": return Qt.rgba(Material.color(Material.Green).r, Material.color(Material.Green).g, Material.color(Material.Green).b, 0.25);
                                    case "sleeping": return Qt.rgba(Material.color(Material.Blue).r, Material.color(Material.Blue).g, Material.color(Material.Blue).b, 0.25);
                                    case "disk-sleep": return Qt.rgba(Material.color(Material.Purple).r, Material.color(Material.Purple).g, Material.color(Material.Purple).b, 0.25);
                                    case "zombie": return Qt.rgba(Material.color(Material.Red).r, Material.color(Material.Red).g, Material.color(Material.Red).b, 0.25);
                                    case "stopped": return Qt.rgba(Material.color(Material.Orange).r, Material.color(Material.Orange).g, Material.color(Material.Orange).b, 0.25);
                                    default: return palette.mid;
                                }
                            }

                            Label {
                                anchors.centerIn: parent
                                text: parent.displayStatus
                                font.pixelSize: 10
                            }
                        }

                        // Kill button
                        Rectangle {
                            Layout.preferredWidth: 28
                            Layout.preferredHeight: 24
                            radius: 3
                            color: killMouseArea.containsMouse
                                ? Qt.rgba(Material.color(Material.Red).r, Material.color(Material.Red).g, Material.color(Material.Red).b, 0.3)
                                : "transparent"

                            Label {
                                anchors.centerIn: parent
                                text: "\u00D7"  // ×
                                font.pixelSize: 16
                                font.bold: true
                                color: killMouseArea.containsMouse
                                    ? Material.color(Material.Red)
                                    : palette.mid
                            }

                            MouseArea {
                                id: killMouseArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    processContextMenu.processData = proc;
                                    processContextMenu.popup();
                                }
                            }

                            ToolTip.visible: killMouseArea.containsMouse
                            ToolTip.text: "Kill process (PID " + proc.pid + ")"
                        }
                    }

                    // Context menu
                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.RightButton
                        onClicked: mouse => {
                            if (mouse.button === Qt.RightButton) {
                                processContextMenu.processData = proc;
                                processContextMenu.popup();
                            }
                        }
                    }
                }

                // Empty state
                Label {
                    anchors.centerIn: parent
                    visible: processListView.count === 0 && context.selectedHost
                    text: "No processes found"
                    color: palette.mid
                }
            }
        }

        // --------------------------------------------------------------------
        // Status Bar
        // --------------------------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Label {
                text: "Hosts: " + d.vitalsInterface.hostCount
                color: palette.mid
            }

            Item { Layout.fillWidth: true }

            Label {
                visible: context.selectedHost !== ""
                property var hostData: d.vitalsInterface.hosts[context.selectedHost]
                property int procCount: hostData && hostData.status && hostData.status.processes
                    ? hostData.status.processes.length : 0
                text: "Processes: " + procCount
                color: palette.mid
            }
        }
    }

    // ========================================================================
    // Kill Service
    // ========================================================================

    // Kill service client - recreated when host changes
    // Hostname must be sanitized: ROS 2 names only allow alphanumerics and '_'
    property var killServiceClient: {
        if (!context.selectedHost)
            return null;
        const sanitized = context.selectedHost.replace(/-/g, '_');
        return Ros2.createServiceClient(
            "/" + sanitized + "/vitals/kill_process",
            "ros2_vitals_msgs/srv/KillProcess"
        );
    }

    /**
     * Call the kill service for the selected host.
     * Service is at /<hostname>/vitals/kill_process
     */
    function callKillService(pid, force) {
        if (!killServiceClient) {
            console.warn("No host selected or kill service not available");
            return;
        }

        const request = {
            pid: pid,
            force: force
        };

        killServiceClient.sendRequestAsync(request, function(response) {
            if (!response) {
                console.warn("Kill service call failed - no response");
                return;
            }
            if (response.success) {
                console.log("Kill successful:", response.message);
            } else {
                console.warn("Kill failed:", response.message);
            }
        });
    }

    // ========================================================================
    // Context Menu
    // ========================================================================

    Menu {
        id: processContextMenu

        property var processData: null

        Action {
            text: "Copy Node Name"
            enabled: processContextMenu.processData !== null
            onTriggered: {
                if (processContextMenu.processData)
                    RQml.copyTextToClipboard(processContextMenu.processData.node_name
                        || processContextMenu.processData.cmdline);
            }
        }

        Action {
            text: "Copy PID"
            enabled: processContextMenu.processData !== null
            onTriggered: {
                if (processContextMenu.processData)
                    RQml.copyTextToClipboard(String(processContextMenu.processData.pid));
            }
        }

        MenuSeparator {}

        Action {
            text: "Kill Process (SIGTERM)"
            enabled: processContextMenu.processData !== null
            onTriggered: {
                if (processContextMenu.processData) {
                    root.callKillService(processContextMenu.processData.pid, false);
                }
            }
        }

        Action {
            text: "Force Kill (SIGKILL)"
            enabled: processContextMenu.processData !== null
            onTriggered: {
                if (processContextMenu.processData) {
                    root.callKillService(processContextMenu.processData.pid, true);
                }
            }
        }
    }
}

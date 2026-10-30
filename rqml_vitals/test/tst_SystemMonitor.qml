import QtQuick
import QtTest
import Ros2

Item {
    id: root

    property var context: contextObj
    property var plugin: pluginLoader.item

    function find(name) {
        return helpers.findChild(root, name);
    }

    height: 768
    width: 1024

    QtObject {
        id: contextObj

        property var enabled
        property var expandedLaunches
        property var selectedHost
        property var sortAscending
        property var sortColumn
        property var topic
    }
    Utils {
        id: helpers

    }
    Loader {
        id: pluginLoader

        function reload() {
            source = "";
            source = "../qml/SystemMonitor.qml";
        }

        anchors.fill: parent
    }
    TestCase {
        id: testCase

        function createChildNode(name, pid) {
            var child = Ros2.createEmptyMessage("ros2_vitals_msgs/msg/ChildProcessStatus");
            child.node_name = name;
            child.pid = pid;
            child.cmdline = name;
            child.gpu_index = -1;
            child.status = "sleeping";
            return child;
        }
        function createGpu(utilization, memoryTotal, memoryUsed) {
            var gpu = Ros2.createEmptyMessage("ros2_vitals_msgs/msg/GpuStatus");
            gpu.utilization_percent = utilization;
            gpu.memory_total_bytes = memoryTotal;
            gpu.memory_used_bytes = memoryUsed;
            return gpu;
        }
        function createNetworkInterface(name, sendRate, recvRate) {
            var iface = Ros2.createEmptyMessage("ros2_vitals_msgs/msg/NetworkInterface");
            iface.name = name;
            iface.bytes_sent_per_sec = sendRate;
            iface.bytes_recv_per_sec = recvRate;
            return iface;
        }
        function createProcess(name, pid, fields) {
            var proc = Ros2.createEmptyMessage("ros2_vitals_msgs/msg/ProcessStatus");
            proc.node_name = name;
            proc.pid = pid;
            proc.cmdline = name;
            proc.gpu_index = -1;
            proc.status = "sleeping";
            for (var key in fields)
                proc[key] = fields[key];
            return proc;
        }
        function createStatus(hostname, fields) {
            var msg = Ros2.createEmptyMessage("ros2_vitals_msgs/msg/SystemStatus");
            msg.hostname = hostname;
            msg.ip_addresses = ["10.0.0.1"];
            msg.cpu_percent = 10;
            msg.cpu_count = 4;
            msg.ram_total_bytes = 1024;
            msg.ram_used_bytes = 256;
            for (var key in fields)
                msg[key] = fields[key];
            return msg;
        }
        function findInRow(name, index) {
            var listView = find("systemMonitorProcessList");
            listView.positionViewAtIndex(index, ListView.Contain);
            var item = null;
            tryVerify(function () {
                var delegate = listView.itemAtIndex(index);
                item = delegate ? helpers.findChild(delegate, name + "_" + index) : null;
                return item !== null;
            }, 2000, name + " should exist in row " + index);
            return item;
        }
        function init() {
            failOnWarning(/Binding loop|TypeError|ReferenceError|Could not convert|Unable to assign/);
            // Every kill request to a service without handler must be expected with ignoreWarning.
            failOnWarning(/No handler registered/);
            Ros2.reset();
            contextObj.enabled = undefined;
            contextObj.expandedLaunches = undefined;
            contextObj.selectedHost = undefined;
            contextObj.sortAscending = undefined;
            contextObj.sortColumn = undefined;
            contextObj.topic = undefined;
            pluginLoader.reload();
            tryVerify(function () {
                return pluginLoader.status === Loader.Ready;
            }, 2000, "Loader should be ready");
        }
        function processNames() {
            var listView = find("systemMonitorProcessList");
            var names = [];
            for (var i = 0; i < listView.count; ++i)
                names.push(findInRow("systemMonitorNameLabel", i).text);
            return names;
        }
        function publishSampleStatus(hostname) {
            publishStatus(createStatus(hostname, {
                "processes": [createProcess("b_node", 10, {
                        "cpu_percent": 5,
                        "ram_bytes": 300
                    }), createProcess("a_node", 11, {
                        "cpu_percent": 50,
                        "ram_bytes": 100,
                        "status": "running"
                    }), createProcess("[launch] bringup/robot", 12, {
                        "is_launch_process": true,
                        "launch_name": "bringup/robot",
                        "cpu_percent": 20,
                        "ram_bytes": 200,
                        "status": "disk-sleep",
                        "child_nodes": [createChildNode("child_z", 13), createChildNode("child_y", 14)]
                    })]
            }));
        }
        function publishStatus(msg) {
            var sub = null;
            tryVerify(function () {
                sub = Ros2.findSubscription(contextObj.topic);
                return sub !== null;
            }, 2000, "Subscription should exist");
            sub.injectMessage(msg);
        }
        function test_clear_button() {
            publishSampleStatus("robot");
            tryCompare(find("systemMonitorHostCountLabel"), "text", "Hosts (1)", 2000);
            mouseClick(find("systemMonitorClearButton"));
            tryCompare(find("systemMonitorHostCountLabel"), "text", "Hosts (0)", 2000, "Clear should remove all hosts");
            verify(find("systemMonitorWaitingLabel").visible, "Waiting state should be shown again");
        }
        function test_copy_actions() {
            publishSampleStatus("robot");
            tryCompare(find("systemMonitorProcessList"), "count", 3, 2000);
            mouseClick(findInRow("systemMonitorKillButton", 1));
            RQml.resetClipboard();
            find("systemMonitorCopyNodeNameAction").triggered();
            tryCompare(RQml, "clipboard", "a_node", 1000, "Copy Node Name should copy the row's node name");
            RQml.resetClipboard();
            find("systemMonitorCopyPidAction").triggered();
            tryCompare(RQml, "clipboard", "11", 1000, "Copy PID should copy the row's PID");
        }
        function test_expand_launch_shows_sorted_children() {
            publishSampleStatus("robot");
            var listView = find("systemMonitorProcessList");
            tryCompare(listView, "count", 3, 2000);
            compare(findInRow("systemMonitorNameLabel", 0).text, "bringup/robot (2)");
            mouseClick(findInRow("systemMonitorExpandButton", 0));
            tryCompare(listView, "count", 5, 2000, "Expanding should show the launch's child nodes");
            compare(processNames(), ["bringup/robot (2)", "child_y", "child_z", "a_node", "b_node"]);
            verify(contextObj.expandedLaunches["12"], "Expanded state should persist in the context");
            mouseClick(findInRow("systemMonitorExpandButton", 0));
            tryCompare(listView, "count", 3, 2000, "Collapsing should hide the child nodes");
        }
        function test_host_card_shows_status() {
            publishStatus(createStatus("robot", {
                "cpu_percent": 42.5,
                "ram_total_bytes": 2048,
                "ram_used_bytes": 1024,
                "uptime_seconds": 7200,
                "gpus": [createGpu(30, 100, 40)],
                "network_interfaces": [createNetworkInterface("eth0", 512, 2048), createNetworkInterface("wlan0", 512, 0)],
                "processes": [createProcess("talker", 1)]
            }));
            var card = null;
            tryVerify(function () {
                card = find("systemMonitorHostCard_0");
                return card !== null;
            }, 2000, "Host card should be created");
            compare(find("systemMonitorHostCountLabel").text, "Hosts (1)");
            compare(helpers.findChild(card, "hostCardHostnameLabel").text, "robot");
            compare(helpers.findChild(card, "hostCardIpLabel").text, "10.0.0.1");
            compare(helpers.findChild(card, "hostCardCpuBar").value, 42.5);
            compare(helpers.findChild(card, "hostCardCpuBar").detail, "4 cores");
            compare(helpers.findChild(card, "hostCardRamBar").value, 50);
            compare(helpers.findChild(card, "hostCardRamBar").detail, "1.0 KB / 2.0 KB");
            compare(helpers.findChild(card, "hostCardGpuBar_0").value, 30);
            compare(helpers.findChild(card, "hostCardVramBar_0").value, 40);
            compare(helpers.findChild(card, "hostCardNetSendLabel").text, "↑ 1.0 KB/s", "Send rate should sum all interfaces");
            compare(helpers.findChild(card, "hostCardNetRecvLabel").text, "↓ 2.0 KB/s");
            compare(helpers.findChild(card, "hostCardProcessCountLabel").text, "1 ROS processes");
            compare(helpers.findChild(card, "hostCardUptimeLabel").text, "Up: 2.0h");
        }
        function test_hosts_sorted_and_selectable() {
            publishSampleStatus("zeta");
            tryCompare(contextObj, "selectedHost", "zeta", 2000, "First host should be selected automatically");
            publishSampleStatus("alpha");
            tryCompare(find("systemMonitorHostCountLabel"), "text", "Hosts (2)", 2000);
            var alphaCard = find("systemMonitorHostCard_0");
            compare(helpers.findChild(alphaCard, "hostCardHostnameLabel").text, "alpha", "Hosts should be sorted by name");
            compare(contextObj.selectedHost, "zeta", "New hosts should not change the selection");
            // The Row positions recreated cards on the next polish, before that they overlap
            waitForRendering(alphaCard);
            mouseClick(alphaCard);
            tryCompare(contextObj, "selectedHost", "alpha", 1000, "Clicking a host card should select it");
            compare(find("systemMonitorProcessesLabel").text, "Processes on alpha");
        }
        function test_kill_process() {
            var requests = [];
            Ros2.registerService("/robot_pc/vitals/kill_process", "ros2_vitals_msgs/srv/KillProcess", function (req) {
                requests.push({
                    "pid": req.pid,
                    "force": req.force
                });
                var resp = Ros2.createEmptyServiceResponse("ros2_vitals_msgs/srv/KillProcess");
                resp.success = true;
                resp.message = "Sent signal to PID " + req.pid;
                return resp;
            });
            publishSampleStatus("robot-pc");
            tryCompare(find("systemMonitorProcessList"), "count", 3, 2000);
            mouseClick(findInRow("systemMonitorKillButton", 2));
            tryCompare(find("systemMonitorContextMenu"), "opened", true, 1000);
            mouseClick(find("systemMonitorKillAction"));
            tryCompare(requests, "length", 1, 2000, "Kill should call the host's kill service");
            compare(requests[0], {
                "pid": 10,
                "force": false
            });
            tryCompare(find("systemMonitorContextMenu"), "visible", false, 1000, "Menu should close after triggering");
            // Move like a user would, so the hovered button's tooltip from the first click hides
            var killButton = findInRow("systemMonitorKillButton", 1);
            mouseMove(killButton);
            mouseClick(killButton);
            tryCompare(find("systemMonitorContextMenu"), "opened", true, 1000);
            mouseClick(find("systemMonitorForceKillAction"));
            tryCompare(requests, "length", 2, 2000, "Force kill should call the host's kill service");
            compare(requests[1], {
                "pid": 11,
                "force": true
            });
        }
        function test_pause_and_resume() {
            var toggle = find("systemMonitorEnableToggle");
            verify(toggle.checked, "Should be enabled initially");
            mouseClick(toggle);
            tryCompare(contextObj, "enabled", false, 1000, "Pausing should persist in the context");
            compare(Ros2.findSubscription("/vitals/status"), null, "Paused plugin should unsubscribe");
            compare(find("systemMonitorWaitingLabel").text, "Paused");
            mouseClick(toggle);
            tryCompare(contextObj, "enabled", true, 1000);
            verify(Ros2.findSubscription("/vitals/status") !== null, "Resumed plugin should subscribe again");
        }
        function test_plugin_loads() {
            verify(plugin !== null, "SystemMonitor plugin should load");
            compare(contextObj.enabled, true);
            compare(contextObj.topic, "/vitals/status");
            compare(contextObj.sortColumn, "name");
            compare(contextObj.sortAscending, true);
            compare(find("systemMonitorHostCountLabel").text, "Hosts (0)");
            compare(find("systemMonitorWaitingLabel").text, "Waiting for vitals data...\nSubscribed to: /vitals/status");
            verify(Ros2.findSubscription("/vitals/status") !== null, "Plugin should subscribe to the default topic");
        }
        function test_sort_by_header_click() {
            publishSampleStatus("robot");
            tryCompare(find("systemMonitorProcessList"), "count", 3, 2000);
            compare(processNames(), ["bringup/robot (2)", "a_node", "b_node"], "Processes should be sorted by name initially");
            mouseClick(find("systemMonitorSortHeader_cpu"));
            compare(contextObj.sortColumn, "cpu");
            compare(contextObj.sortAscending, false, "Numeric columns should sort descending first");
            tryVerify(function () {
                return processNames()[0] === "a_node";
            }, 2000);
            compare(processNames(), ["a_node", "bringup/robot (2)", "b_node"]);
            mouseClick(find("systemMonitorSortHeader_cpu"));
            compare(contextObj.sortAscending, true, "Clicking the active column should toggle the order");
            tryVerify(function () {
                return processNames()[0] === "b_node";
            }, 2000);
            mouseClick(find("systemMonitorSortHeader_ram"));
            compare(contextObj.sortColumn, "ram");
            tryVerify(function () {
                return processNames()[0] === "b_node";
            }, 2000);
            compare(processNames(), ["b_node", "bringup/robot (2)", "a_node"]);
        }
        function test_status_labels() {
            publishSampleStatus("robot");
            tryCompare(find("systemMonitorProcessList"), "count", 3, 2000);
            compare(findInRow("systemMonitorStatusLabel", 0).text, "I/O", "disk-sleep should be shown as I/O");
            compare(findInRow("systemMonitorStatusLabel", 1).text, "running");
            compare(findInRow("systemMonitorStatusLabel", 2).text, "idle", "sleeping should be shown as idle");
        }
        function test_topic_field_changes_subscription() {
            var topicField = find("systemMonitorTopicField");
            mouseClick(topicField);
            topicField.selectAll();
            keyClick(Qt.Key_Delete);
            for (var c of "/robot/vitals")
                keyClick(c);
            keyClick(Qt.Key_Return);
            tryCompare(contextObj, "topic", "/robot/vitals", 1000, "Topic should persist in the context");
            verify(Ros2.findSubscription("/robot/vitals") !== null, "Plugin should subscribe to the new topic");
            compare(Ros2.findSubscription("/vitals/status"), null, "Old topic should be unsubscribed");
        }

        name: "SystemMonitorTest"
        when: windowShown
    }
}

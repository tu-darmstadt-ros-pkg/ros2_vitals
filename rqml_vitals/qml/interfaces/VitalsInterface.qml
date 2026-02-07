import QtQuick
import Ros2

/**
 * Backend interface for Vitals system monitor.
 * Subscribes to /vitals/status and aggregates data from multiple hosts.
 */
QtObject {
    id: root

    //! Whether data collection is enabled
    property bool enabled: true

    //! Topic to subscribe to
    property string topic: "/vitals/status"

    //! Map of hostname -> status data
    property var hosts: ({})

    //! List of hostnames (sorted)
    property var hostnames: []

    //! Total number of hosts
    property int hostCount: 0

    //! Whether any data has been received
    readonly property bool hasData: hostCount > 0

    //! History length in samples (for graphs)
    property int historyLength: 60

    //! Signal emitted when host data changes (use dataUpdated to avoid conflict with hosts property signal)
    signal dataUpdated()

    // ========================================================================
    // Public Functions
    // ========================================================================

    /**
     * Get data for a specific host.
     */
    function getHost(hostname) {
        return _hostData[hostname] || null;
    }

    /**
     * Get CPU history for a host.
     */
    function getCpuHistory(hostname) {
        return _cpuHistory[hostname] || [];
    }

    /**
     * Get RAM history for a host.
     */
    function getRamHistory(hostname) {
        return _ramHistory[hostname] || [];
    }

    /**
     * Clear all collected data.
     */
    function clear() {
        _hostData = {};
        _cpuHistory = {};
        _ramHistory = {};
        _gpuHistory = {};
        _lastUpdate = {};
        root.hosts = {};
        root.hostnames = [];
        root.hostCount = 0;
        root.dataUpdated();
    }

    // ========================================================================
    // Private Implementation
    // ========================================================================

    //! Private data storage
    property var _hostData: ({})
    property var _cpuHistory: ({})
    property var _ramHistory: ({})
    property var _gpuHistory: ({})
    property var _lastUpdate: ({})

    function _processMessage(msg) {
        const hostname = msg.hostname;
        const now = Date.now();

        // Store the message data
        _hostData[hostname] = msg;
        _lastUpdate[hostname] = now;

        // Update history
        _updateHistory(hostname, 'cpu', msg.cpu_percent, _cpuHistory);

        const ramPercent = msg.ram_total_bytes > 0
            ? (msg.ram_used_bytes / msg.ram_total_bytes) * 100
            : 0;
        _updateHistory(hostname, 'ram', ramPercent, _ramHistory);

        // GPU history (first GPU only for simplicity)
        if (msg.gpus && msg.gpus.length > 0) {
            const gpu = msg.gpus.at(0);
            const gpuPercent = gpu.memory_total_bytes > 0
                ? (gpu.memory_used_bytes / gpu.memory_total_bytes) * 100
                : 0;
            _updateHistory(hostname, 'gpu', gpuPercent, _gpuHistory);
        }

        // Update public properties
        _rebuildHostList();
    }

    function _updateHistory(hostname, metric, value, historyMap) {
        if (!historyMap[hostname]) {
            historyMap[hostname] = [];
        }

        historyMap[hostname].push(value);

        // Trim to history length
        while (historyMap[hostname].length > root.historyLength) {
            historyMap[hostname].shift();
        }
    }

    function _rebuildHostList() {
        const names = Object.keys(_hostData).sort();
        root.hostnames = names;
        root.hostCount = names.length;

        // Build hosts object for binding
        const hostsObj = {};
        for (const name of names) {
            hostsObj[name] = {
                status: _hostData[name],
                lastUpdate: _lastUpdate[name],
                cpuHistory: _cpuHistory[name] || [],
                ramHistory: _ramHistory[name] || [],
                gpuHistory: _gpuHistory[name] || [],
            };
        }
        root.hosts = hostsObj;
        root.dataUpdated();
    }

    // ========================================================================
    // Subscription (using property to hold the component)
    // ========================================================================

    property Subscription _statusSubscription: Subscription {
        topic: root.enabled ? root.topic : ""
        throttleRate: 0  // Receive all messages
        onNewMessage: function(msg) {
            if (!root.enabled)
                return;
            root._processMessage(msg);
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

/**
 * Animated usage bar with optional label and color coding.
 * Uses Material theme colors for proper dark/light mode support.
 */
Item {
    id: root

    //! Current value (0-100)
    property real value: 0

    //! Label text
    property string label: ""

    //! Detail text (shown on right)
    property string detail: ""

    //! Warning threshold
    property real warningThreshold: 70

    //! Error threshold
    property real errorThreshold: 90

    //! Bar height
    property int barHeight: 16

    //! Colors - use Material colors for theme support
    property color normalColor: Material.color(Material.Green)
    property color warningColor: Material.color(Material.Orange)
    property color errorColor: Material.color(Material.Red)
    property color backgroundColor: Material.background

    implicitHeight: barHeight + (label ? 20 : 0)
    implicitWidth: 200

    ColumnLayout {
        anchors.fill: parent
        spacing: 2

        // Label row
        RowLayout {
            Layout.fillWidth: true
            visible: label !== ""
            spacing: 4

            Label {
                text: root.label
                font.pixelSize: 11
            }

            Item { Layout.fillWidth: true }

            Label {
                text: root.detail
                font.pixelSize: 11
                color: palette.text
                opacity: 0.7
            }
        }

        // Bar
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: root.barHeight
            color: Qt.darker(root.backgroundColor, 1.2)
            radius: 3
            border.width: 1
            border.color: Qt.darker(root.backgroundColor, 1.4)

            Rectangle {
                id: fillBar
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.margins: 1
                width: Math.max(0, Math.min(1, root.value / 100)) * (parent.width - 2)
                radius: 2

                color: root.value >= root.errorThreshold ? root.errorColor
                     : root.value >= root.warningThreshold ? root.warningColor
                     : root.normalColor

                Behavior on width {
                    NumberAnimation { duration: 150; easing.type: Easing.OutQuad }
                }

                Behavior on color {
                    ColorAnimation { duration: 150 }
                }
            }

            // Percentage text overlay
            Label {
                anchors.centerIn: parent
                text: root.value.toFixed(1) + "%"
                font.pixelSize: 10
                font.bold: true
                color: palette.text
            }
        }
    }
}

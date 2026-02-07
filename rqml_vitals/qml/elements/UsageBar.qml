import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

/**
 * Animated usage bar with optional label and color coding.
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

    //! Colors
    property color normalColor: "#2ecc71"
    property color warningColor: "#f39c12"
    property color errorColor: "#e74c3c"
    property color backgroundColor: palette.mid

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
            color: root.backgroundColor
            radius: 3

            Rectangle {
                id: fillBar
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: Math.max(0, Math.min(1, root.value / 100)) * parent.width
                radius: 3

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
                color: "white"
                style: Text.Outline
                styleColor: "#00000080"
            }
        }
    }
}

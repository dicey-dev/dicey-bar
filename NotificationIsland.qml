import Quickshell
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui
import "NotificationIslandModel.js" as IslandModel

// The center island's expanded state: a pill that grows out of the clock
// island, shows the newest notification, and shrinks back. It is a separate,
// click-through-outside-the-pill layer surface so the bar's own surface (and
// its exclusive zone) never changes size. It is mapped only while it shows.
PanelWindow {
  id: island

  // Bar.qml root, which owns the shared notification feed.
  required property var bar
  // The in-bar center island the pill grows from and returns to.
  required property Rectangle anchorItem

  // The bar window's output. Reading this window's own `screen` here loops:
  // mapping the window re-announces it.
  required property string screenName
  readonly property bool onThisScreen: bar.islandItem !== null && screenName !== ""
    && bar.islandScreen === screenName
  readonly property bool wanted: onThisScreen && bar.islandEnabled && !bar.barHidden && !bar.vertical
    && anchorItem.visible
  // `open` drives the morph; `mapped` keeps the surface up until the pill has
  // shrunk back onto the clock island.
  property bool open: false
  property bool mapped: false
  property var item: null
  readonly property bool bottom: bar.position === "bottom"

  readonly property real collapsedWidth: Math.max(1, anchorItem.width)
  readonly property real collapsedHeight: Math.max(1, anchorItem.height)
  readonly property real expandedWidth: Math.max(collapsedWidth,
    Math.min(Style.space(460), anchorItem.parent.width - Style.space(40)))
  readonly property real expandedHeight: Math.max(collapsedHeight, Style.space(60))
  // Where the clock island sits inside the bar surface.
  readonly property real anchorOffset: Math.max(0, (bar.barSize - collapsedHeight) / 2)

  // The theme's notification border, as on Omarchy's own toasts; critical
  // notifications use the theme's urgent color instead.
  readonly property real borderWidth: Math.max(1, Style.space(2))
  readonly property var borderSpec: item && item.urgency === 2
    ? Border.flat(Color.urgent, borderWidth)
    : Border.surfaceSpec("notifications", "border", Color.notifications.border, borderWidth)
  readonly property var stackBorderSpec: Border.surfaceSpec("notifications", "border",
    Color.notifications.border, borderWidth)

  // Earlier notifications still in the open island peek out behind the pill.
  readonly property int maxBehind: IslandModel.MAX_STACK - 1
  readonly property int behind: Math.max(0, Math.min(maxBehind, (bar.islandStack || []).length - 1))
  readonly property real peek: Style.space(6)
  readonly property int moreCount: Math.max(0, bar.islandArrivals - 1)

  onWantedChanged: wanted ? show() : hide()
  Connections {
    target: island.bar
    function onIslandSerialChanged() {
      if (!island.wanted) return
      var stacking = island.open && island.item !== null
      // New content in an open pill: a click aimed at the old one must not land.
      island.clickable = false
      clickArm.restart()
      island.show()
      // The newcomer drops onto the stack; the previous one moves behind.
      if (stacking) arrivalAnimation.restart()
    }
  }

  function show() {
    item = bar.islandItem
    mapped = true
    // Let the surface map at the clock island's size before growing.
    Qt.callLater(function() { if (island.wanted) island.open = true })
  }

  function hide() {
    open = false
    collapseDone.restart()
  }

  Timer {
    id: collapseDone
    interval: Style.duration(160) + 40
    onTriggered: if (!island.open) {
      // An unmapped surface may never send a pointer leave.
      island.bar.setIslandHovered(island, false)
      island.mapped = false
      island.item = null
    }
  }

  // A pill that appears under the pointer must not take a click meant for
  // whatever was there a moment ago.
  property bool clickable: false
  onOpenChanged: {
    clickable = false
    if (open) clickArm.restart()
  }
  Timer {
    id: clickArm
    interval: 400
    onTriggered: island.clickable = island.open
  }

  visible: mapped
  color: "transparent"
  surfaceFormat.opaque: false
  exclusionMode: ExclusionMode.Ignore
  WlrLayershell.namespace: "dicey-bar-island"
  WlrLayershell.layer: WlrLayer.Top
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

  // Anchored to one edge only, so the compositor centers it horizontally,
  // exactly like the full-width bar centers its clock island.
  anchors.top: !bottom
  anchors.bottom: bottom
  margins.top: bottom ? 0 : bar.topIslandMargin
  margins.bottom: 0
  implicitWidth: Math.ceil(expandedWidth)
  implicitHeight: Math.ceil(anchorOffset + expandedHeight + maxBehind * peek)

  mask: Region { item: pill }

  // Cards behind the pill, nearest first; drawn before it, so it covers them.
  Repeater {
    model: island.maxBehind

    BorderSurface {
      id: card
      required property int index
      property real reveal: island.open && index < island.behind ? 1 : 0
      Behavior on reveal {
        NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic }
      }

      visible: reveal > 0
      opacity: reveal * (index === 0 ? 0.9 : 0.7)
      width: Math.max(1, pill.width - Style.space(14) * (index + 1) * 2)
      height: pill.height
      x: Math.round((parent.width - width) / 2)
      y: pill.y + (island.bottom ? -1 : 1) * Math.round(reveal * island.peek * (index + 1))
      radius: pill.radius
      color: pill.color
      borderSpec: island.stackBorderSpec
    }
  }

  Rectangle {
    id: pill
    width: island.open ? island.expandedWidth : island.collapsedWidth
    height: island.open ? island.expandedHeight : island.collapsedHeight
    x: Math.round((parent.width - width) / 2)
    y: island.bottom ? parent.height - island.anchorOffset - height : island.anchorOffset
    radius: island.open ? Style.space(14) : island.anchorItem.radius
    color: Qt.rgba(island.bar.background.r, island.bar.background.g, island.bar.background.b, 1)
    clip: true

    Behavior on width {
      NumberAnimation {
        duration: island.open ? Style.duration(240) : Style.duration(160)
        easing.type: Easing.Bezier; easing.bezierCurve: Style.easeOut
      }
    }
    Behavior on height {
      NumberAnimation {
        duration: island.open ? Style.duration(240) : Style.duration(160)
        easing.type: Easing.Bezier; easing.bezierCurve: Style.easeOut
      }
    }
    Behavior on radius {
      NumberAnimation { duration: Style.duration(160); easing.type: Easing.OutCubic }
    }

    MouseArea {
      id: pointer
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: island.bar.setIslandHovered(island, containsMouse)
      Component.onDestruction: island.bar.setIslandHovered(island, false)
      onClicked: function(mouse) {
        if (!island.item || !island.clickable) return
        if (mouse.button === Qt.LeftButton) island.bar.activateIsland(island.item.key)
        else island.bar.closeIsland()
      }
    }

    Item {
      id: content
      anchors.fill: parent
      anchors.leftMargin: Style.space(14)
      anchors.rightMargin: Style.space(16)
      opacity: island.open && pill.width >= island.expandedWidth * 0.9 ? 1 : 0
      visible: opacity > 0
      Behavior on opacity {
        NumberAnimation { duration: island.open ? Style.duration(140) : Style.duration(80); easing.type: Easing.OutCubic }
      }

      Item {
        id: arrival
        anchors.fill: parent
        transform: Translate { id: arrivalShift }

        ParallelAnimation {
          id: arrivalAnimation
          NumberAnimation {
            target: arrivalShift; property: "y"
            from: (island.bottom ? 1 : -1) * Style.space(10); to: 0
            duration: Style.duration(220); easing.type: Easing.OutCubic
          }
          NumberAnimation {
            target: arrival; property: "opacity"; from: 0; to: 1
            duration: Style.duration(220); easing.type: Easing.OutCubic
          }
        }

        Item {
          id: iconSlot
          width: Style.space(32)
          height: width
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter

          readonly property string source: {
            var names = IslandModel.iconNames(island.item)
            for (var i = 0; i < names.length; i++) {
              var path = Quickshell.iconPath(names[i], true)
              if (path) return path
            }
            return ""
          }

          Image {
            id: appIcon
            anchors.fill: parent
            source: iconSlot.source
            sourceSize.width: width * 2
            sourceSize.height: height * 2
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            smooth: true
            visible: status === Image.Ready
          }

          Rectangle {
            anchors.fill: parent
            radius: width / 2
            visible: !appIcon.visible
            color: Qt.rgba(island.bar.foreground.r, island.bar.foreground.g, island.bar.foreground.b, 0.12)

            Text {
              anchors.centerIn: parent
              textFormat: Text.PlainText
              // Nerd Font bell; the bar font is the Omarchy monospace Nerd Font.
              text: "\u{f009a}"
              color: island.item && island.item.urgency === 2 ? Color.urgent : island.bar.foreground
              font.family: island.bar.fontFamily
              font.pixelSize: Style.font.title
            }
          }
        }

        Column {
          anchors.left: iconSlot.right
          anchors.leftMargin: Style.space(12)
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Item {
            width: parent.width
            height: appName.implicitHeight

            Text {
              id: appName
              anchors.left: parent.left
              anchors.right: moreLabel.visible ? moreLabel.left : parent.right
              anchors.rightMargin: moreLabel.visible ? Style.space(8) : 0
              textFormat: Text.PlainText
              text: island.item ? (island.item.app || "Notification") : ""
              color: Qt.rgba(island.bar.foreground.r, island.bar.foreground.g, island.bar.foreground.b, 0.65)
              font.family: island.bar.fontFamily
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
              maximumLineCount: 1
            }

            // How many more arrived while the island was open.
            Text {
              id: moreLabel
              anchors.right: parent.right
              visible: island.moreCount > 0
              textFormat: Text.PlainText
              text: "+" + island.moreCount + " more"
              color: Color.accent
              font.family: island.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: IslandModel.headline(island.item)
            color: island.bar.foreground
            font.family: island.bar.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            elide: Text.ElideRight
            maximumLineCount: 1
          }

          Text {
            width: parent.width
            visible: text !== ""
            textFormat: Text.PlainText
            text: IslandModel.detail(island.item)
            color: Qt.rgba(island.bar.foreground.r, island.bar.foreground.g, island.bar.foreground.b, 0.85)
            font.family: island.bar.fontFamily
            font.pixelSize: Style.font.bodySmall
            elide: Text.ElideRight
            maximumLineCount: 1
          }
        }
      }
    }

    // Drawn over the content so the border sits on the pill's edge; it fades
    // in with the morph and out before the pill becomes the clock island.
    BorderSurface {
      anchors.fill: parent
      color: "transparent"
      radius: pill.radius
      borderSpec: island.borderSpec
      opacity: island.open ? 1 : 0
      Behavior on opacity {
        NumberAnimation { duration: Style.duration(160); easing.type: Easing.OutCubic }
      }
    }
  }
}

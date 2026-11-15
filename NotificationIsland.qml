import Quickshell
import Quickshell.Wayland
import QtQuick
import qs.Commons
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

  onWantedChanged: wanted ? show() : hide()
  Connections {
    target: island.bar
    function onIslandSerialChanged() {
      if (!island.wanted) return
      // New content in an open pill: a click aimed at the old one must not land.
      island.clickable = false
      clickArm.restart()
      island.show()
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
  implicitHeight: Math.ceil(anchorOffset + expandedHeight)

  mask: Region { item: pill }

  Rectangle {
    id: pill
    width: island.open ? island.expandedWidth : island.collapsedWidth
    height: island.open ? island.expandedHeight : island.collapsedHeight
    x: Math.round((parent.width - width) / 2)
    y: island.bottom ? parent.height - island.anchorOffset - height : island.anchorOffset
    radius: island.open ? Style.space(14) : island.anchorItem.radius
    color: Qt.rgba(island.bar.background.r, island.bar.background.g, island.bar.background.b, 1)
    border.width: island.item && island.item.urgency === 2 && island.open ? 1 : 0
    border.color: island.bar.urgent
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
            color: island.item && island.item.urgency === 2 ? island.bar.urgent : island.bar.foreground
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

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: island.item ? (island.item.app || "Notification") : ""
          color: Qt.rgba(island.bar.foreground.r, island.bar.foreground.g, island.bar.foreground.b, 0.65)
          font.family: island.bar.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
          maximumLineCount: 1
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
}

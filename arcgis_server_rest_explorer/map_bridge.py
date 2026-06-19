from PySide6.QtCore import QObject, Signal, Slot


class MapBridge(QObject):
    featureClicked = Signal(int)
    areaDrawn = Signal(float, float, float, float)
    polygonDrawn = Signal(str)

    @Slot(int)
    def onFeatureClicked(self, feature_index: int):
        self.featureClicked.emit(feature_index)

    @Slot(float, float, float, float)
    def onAreaDrawn(self, west: float, south: float, east: float, north: float):
        self.areaDrawn.emit(west, south, east, north)

    @Slot(str)
    def onPolygonDrawn(self, coordinates_json: str):
        self.polygonDrawn.emit(coordinates_json)

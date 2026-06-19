from dataclasses import dataclass


@dataclass
class ArcGISNodeData:
    kind: str
    url: str
    name: str = ""

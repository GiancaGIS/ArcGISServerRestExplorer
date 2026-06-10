let bridge = null;
new QWebChannel(qt.webChannelTransport, function(channel) {
    bridge = channel.objects.bridge;
});

const mapConfig = window.arcgisRestExplorerMapConfig || {};
const data = mapConfig.data || { type: 'FeatureCollection', features: [] };
const rendererStyle = mapConfig.rendererStyle || {};
const map = L.map('map').setView([42.5, 12.5], 5);

L.tileLayer(mapConfig.basemapUrl, {
    maxZoom: 19,
    attribution: mapConfig.attribution || ''
}).addTo(map);

let selectedLayer = null;
const layersByIndex = {};

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, function(ch) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
    });
}

function popupHtml(feature) {
    const properties = feature.properties || {};
    const aliases = properties.__aliases || {};
    const objectIdField = properties.__objectIdField || 'OBJECTID';
    const objectId = properties[objectIdField] ?? '';
    const keys = Object.keys(properties).filter(k => !k.startsWith('__')).slice(0, 25);
    let html = `<div class='popup-title'>${escapeHtml(objectIdField)}: ${escapeHtml(objectId)}</div>`;
    html += "<table class='popup-table'>";
    for (const k of keys) {
        const label = aliases[k] || k;
        html += `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(properties[k])}</td></tr>`;
    }
    html += '</table>';
    return html;
}

function defaultStyle(feature) {
    if (feature.properties && feature.properties.__spatialFilter) {
        return { color: '#f97316', weight: 3, opacity: 1, fillColor: '#fb923c', fillOpacity: 0.22 };
    }
    return {
        color: rendererStyle.color || '#60a5fa',
        weight: rendererStyle.weight || 2,
        opacity: rendererStyle.opacity || 0.95,
        fillColor: rendererStyle.fillColor || '#60a5fa',
        fillOpacity: rendererStyle.fillOpacity ?? 0.25
    };
}

function markerOptions(feature) {
    return {
        radius: rendererStyle.radius || 7,
        color: rendererStyle.color || '#bfdbfe',
        weight: rendererStyle.weight || 2,
        fillColor: rendererStyle.fillColor || '#60a5fa',
        fillOpacity: rendererStyle.fillOpacity ?? 0.8
    };
}

function highlightLayer(layer) {
    if (selectedLayer && selectedLayer.setStyle) {
        featureLayer.resetStyle(selectedLayer);
    }
    selectedLayer = layer;
    if (layer.setStyle) {
        layer.setStyle({ color: '#facc15', fillColor: '#facc15', weight: 5, fillOpacity: 0.85 });
    }
    if (layer.bringToFront) {
        layer.bringToFront();
    }
}

const featureLayer = L.geoJSON(data, {
    style: defaultStyle,
    pointToLayer: function(feature, latlng) {
        return L.circleMarker(latlng, markerOptions(feature));
    },
    onEachFeature: function(feature, layer) {
        const properties = feature.properties || {};
        const idx = properties.__featureIndex;
        layersByIndex[idx] = layer;
        layer.bindPopup(popupHtml(feature));
        layer.on('click', function() {
            highlightLayer(layer);
            if (bridge) {
                bridge.onFeatureClicked(idx);
            }
        });
    }
}).addTo(map);

let drawingArea = false;
let drawingPolygon = false;
let drawStart = null;
let drawRectangle = null;
let polygonPoints = [];
let polygonPreview = null;
let polygonMarkers = [];
let finishingPolygon = false;
const drawHint = document.getElementById('drawHint');
const drawControls = document.getElementById('drawControls');
const finishPolygonBtn = document.getElementById('finishPolygonBtn');
const cancelPolygonBtn = document.getElementById('cancelPolygonBtn');

function resetDrawingState() {
    drawingArea = false;
    drawingPolygon = false;
    drawStart = null;
    map.dragging.enable();
    map.doubleClickZoom.enable();
    map.getContainer().style.cursor = '';
    drawHint.style.display = 'none';
    drawControls.style.display = 'none';
}

window.enableAreaDrawing = function() {
    resetPolygonPreview();
    drawingArea = true;
    drawStart = null;
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
    drawHint.textContent = 'Drag on the map to draw a rectangular spatial filter';
    drawHint.style.display = 'block';
};

window.enablePolygonDrawing = function() {
    if (drawRectangle) {
        map.removeLayer(drawRectangle);
        drawRectangle = null;
    }
    resetPolygonPreview();
    drawingPolygon = true;
    finishingPolygon = false;
    map.dragging.enable();
    map.doubleClickZoom.disable();
    map.getContainer().style.cursor = 'crosshair';
    drawHint.textContent = 'Click polygon vertices, then Finish Polygon. Right-click also finishes.';
    drawHint.style.display = 'block';
    drawControls.style.display = 'flex';
};

function resetPolygonPreview() {
    if (polygonPreview) {
        map.removeLayer(polygonPreview);
        polygonPreview = null;
    }
    for (const marker of polygonMarkers) {
        map.removeLayer(marker);
    }
    polygonMarkers = [];
    polygonPoints = [];
}

map.on('mousedown', function(e) {
    if (!drawingArea) return;
    drawStart = e.latlng;
    if (drawRectangle) {
        map.removeLayer(drawRectangle);
    }
    drawRectangle = L.rectangle([drawStart, drawStart], {
        color: '#f97316',
        weight: 3,
        fillColor: '#fb923c',
        fillOpacity: 0.22
    }).addTo(map);
});

map.on('mousemove', function(e) {
    if (!drawingArea || !drawStart || !drawRectangle) return;
    drawRectangle.setBounds(L.latLngBounds(drawStart, e.latlng));
});

map.on('mouseup', function(e) {
    if (!drawingArea || !drawStart || !drawRectangle) return;
    const bounds = drawRectangle.getBounds();
    resetDrawingState();
    if (bridge) {
        bridge.onAreaDrawn(
            bounds.getWest(),
            bounds.getSouth(),
            bounds.getEast(),
            bounds.getNorth()
        );
    }
});

map.on('click', function(e) {
    if (!drawingPolygon) return;
    L.DomEvent.preventDefault(e);
    polygonPoints.push(e.latlng);
    polygonMarkers.push(L.circleMarker(e.latlng, {
        radius: 5,
        color: '#f97316',
        weight: 2,
        fillColor: '#fb923c',
        fillOpacity: 0.9
    }).addTo(map));
    updatePolygonPreview();
});

map.on('mousemove', function(e) {
    if (!drawingPolygon || polygonPoints.length === 0) return;
    updatePolygonPreview(e.latlng);
});

map.on('dblclick', function(e) {
    if (!drawingPolygon) return;
    L.DomEvent.preventDefault(e);
    finishPolygonDrawing();
});

map.on('contextmenu', function(e) {
    if (!drawingPolygon) return;
    L.DomEvent.preventDefault(e);
    finishPolygonDrawing();
});

finishPolygonBtn.addEventListener('click', function(e) {
    e.preventDefault();
    finishPolygonDrawing();
});

cancelPolygonBtn.addEventListener('click', function(e) {
    e.preventDefault();
    resetDrawingState();
    resetPolygonPreview();
});

function finishPolygonDrawing() {
    if (!drawingPolygon) return;
    if (finishingPolygon) return;
    if (polygonPoints.length < 3) {
        drawHint.textContent = 'Add at least 3 vertices before finishing the polygon';
        return;
    }
    finishingPolygon = true;
    const coords = polygonPoints.map(p => [p.lng, p.lat]);
    resetDrawingState();
    resetPolygonPreview();
    if (bridge) {
        bridge.onPolygonDrawn(JSON.stringify(coords));
    }
}

function updatePolygonPreview(cursorLatLng = null) {
    if (polygonPreview) {
        map.removeLayer(polygonPreview);
    }
    let points = polygonPoints.slice();
    if (cursorLatLng) {
        points.push(cursorLatLng);
    }
    if (points.length === 1) return;
    if (points.length >= 3 && !cursorLatLng) {
        polygonPreview = L.polygon(points, {
            color: '#f97316',
            weight: 3,
            fillColor: '#fb923c',
            fillOpacity: 0.22
        }).addTo(map);
    } else {
        polygonPreview = L.polyline(points, {
            color: '#f97316',
            weight: 3,
            dashArray: '6,4'
        }).addTo(map);
    }
}

window.selectFeatureFromPython = function(index) {
    const layer = layersByIndex[index];
    if (!layer) return;
    highlightLayer(layer);
    try {
        if (layer.getBounds) {
            map.fitBounds(layer.getBounds(), { padding: [40, 40], maxZoom: 17 });
        } else if (layer.getLatLng) {
            map.setView(layer.getLatLng(), Math.max(map.getZoom(), 12));
        }
        layer.openPopup();
    } catch (e) {
        console.log(e);
    }
};

if (data.features.length > 0) {
    try {
        map.fitBounds(featureLayer.getBounds(), { padding: [30, 30] });
    } catch (e) {
        console.log(e);
    }
}

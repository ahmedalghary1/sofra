Leaflet 1.9.4 (stable), BSD-2-Clause. See LICENSE.
Vendored from the upstream npm distribution:
https://unpkg.com/leaflet@1.9.4/dist/leaflet.js
https://unpkg.com/leaflet@1.9.4/dist/leaflet.css
License: https://github.com/Leaflet/Leaflet/blob/v1.9.4/LICENSE
Documentation: https://leafletjs.com/reference-1.9.4.html

No external library scripts run on the guest menu. The staff location editor
loads these local assets, then requests OpenStreetMap tiles only when the user
opens the map. It uses a CSS div marker; default marker images and layer controls
are not used. Missing source maps do not affect runtime.

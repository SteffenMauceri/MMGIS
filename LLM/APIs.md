# MMGIS API Documentation

This document provides a comprehensive overview of the MMGIS APIs, which are essential for programmatic control and interaction with the MMGIS application. It is divided into two main sections: the Backend REST API and the Frontend JavaScript API.

## 1. Backend REST API

The Backend REST API allows for programmatic management of missions, configurations, datasets, and other backend resources.

### Authentication

Most REST API endpoints, especially those under `/api/configure`, require an API Token for authentication.

To get a token:
1. Login to the configuration page (`/configure`).
2. Navigate to the "API Tokens" page.
3. Generate a new token.
4. Use the token in the `Authorization` header of your requests: `Authorization: Bearer <token>`.

---

### Endpoints

#### Configure REST API (`/api/configure`)

This API enables programmatic control over mission configurations.

##### GET /missions

Gets a list of all configured missions. _Auth token not needed._

| Parameter |   Type    | Required | Default |                                 Description                                 |
| :-------: | :-------: | :------: | :-----: | :-------------------------------------------------------------------------: |
| **full**  | _boolean_ |  false   |   N/A   | If true, returns versions and configuration objects alongside mission names |

**Example:**
`curl -X GET http://localhost:8889/api/configure/missions`

##### GET /versions

Gets a list of available versions of a mission's configuration object.

**Example:**
`curl -X GET -H "Authorization:Bearer <token>" http://localhost:8889/api/configure/versions?mission=Test`

##### GET /get

Gets a mission's configuration object. _Auth token not needed._

|  Parameter  |   Type    | Required | Default |              Description              |
| :---------: | :-------: | :------: | :-----: | :-----------------------------------: |
| **mission** | _string_  |   true   |   N/A   |             Mission name              |
| **version** | _number_  |  false   | latest  |       Version of configuration        |
|  **full**   | _boolean_ |  false   |  false  | Return additional metadata and status |

**Example:**
`curl -X GET http://localhost:8889/api/configure/get?mission=Test`

##### POST /validate

Validates a configuration object and performs no other action.

| Parameter  |   Type   | Required | Default |        Description        |
| :--------: | :------: | :------: | :-----: | :-----------------------: |
| **config** | _object_ |  false   |   N/A   | Full configuration object |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"config":{}}' http://localhost:8889/api/configure/validate`

##### POST /upsert

Sets a mission's configuration object. Only complete configuration objects are acceptable.

|  Parameter  |   Type   | Required | Default |                        Description                        |
| :---------: | :------: | :------: | :-----: | :-------------------------------------------------------: |
| **mission** | _string_ |   true   |   N/A   |                       Mission name                        |
| **config**  | _object_ |  false   |   N/A   |                 Full configuration object                 |
| **version** | _number_ |  false   |   N/A   | Set a configuration version number to rollback to instead |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"mission":"Test", "config":{}}' http://localhost:8889/api/configure/upsert`

##### POST /addLayer

Adds a single layer to a mission's configuration object.

|       Parameter       |        Type         | Required | Default |                                                                  Description                                                                  |
| :-------------------: | :-----------------: | :------: | :-----: | :-------------------------------------------------------------------------------------------------------------------------------------------: |
|      **mission**      |      _string_       |   true   |   N/A   |                                                                 Mission name                                                                  |
|       **layer**       | _object_ or _array_ |   true   |   N/A   | Full new layer configuration object or array of full new layer configuration objects. |
|  **placement.path**   |      _string_       |  false   |   ''    |              A path to a header in 'layers' to place the new layer. Defaults to no group               |
|  **placement.index**  |      _number_       |  false   |   end   |                       Index in 'layers' to place the new layer. Out of range placement indices are best fit.                        |
| **forceClientUpdate** |      _boolean_      |  false   |  false  |                                                        Push the change out to clients.                                                        |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"mission":"Test", "layer":{"name":"", "type":""}}' http://localhost:8889/api/configure/addLayer`

##### POST /updateLayer

Updates a single layer. Specified layer values are deep merged and overwrite existing values.

|       Parameter       |   Type    | Required | Default |                                                    Description                                                     |
| :-------------------: | :-------: | :------: | :-----: | :----------------------------------------------------------------------------------------------------------------: |
|      **mission**      | _string_  |   true   |   N/A   |                                                    Mission name                                                    |
|     **layerUUID**     | _string_  |   true   |   N/A   |                                                  Layer to update                                                   |
|       **layer**       | _object_  |   true   |   N/A   |           A partial layer configuration object.            |
|  **placement.path**   | _string_  |  false   |   ''    | A path to a header in 'layers' to place the new layer. Defaults to no group |
|  **placement.index**  | _number_  |  false   |   end   |          Index in 'layers' (or path) to place the new layer.          |
| **forceClientUpdate** | _boolean_ |  false   |  false  |                                          Push the change out to clients.                                           |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"mission":"Test", "layerUUID":"uuid", "layer":{}}' http://localhost:8889/api/configure/updateLayer`

##### POST /removeLayer

Removes a single layer from the configuration object.

|       Parameter       |        Type         | Required | Default |                           Description                            |
| :-------------------: | :-----------------: | :------: | :-----: | :--------------------------------------------------------------: |
|      **mission**      |      _string_       |   true   |   N/A   |                           Mission name                           |
|     **layerUUID**     | _string_ or _array_ |   true   |   N/A   | Layer to remove as string or array of layers as string to remove |
| **forceClientUpdate** |      _boolean_      |  false   |  false  |                 Push the change out to clients.                  |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"mission":"Test", "layerUUID":"name"}' http://localhost:8889/api/configure/removeLayer`

##### POST /updateInitialView

Updates the initial latitude, longitude, zoom of the map when users first arrive to the site.

|   Parameter   |   Type   | Required | Default  |           Description           |
| :-----------: | :------: | :------: | :------: | :-----------------------------: |
|  **mission**  | _string_ |   true   |   N/A    |          Mission name           |
| **latitude**  | _number_ |  false   | existing | Map latitude center coordinate  |
| **longitude** | _number_ |  false   | existing | Map Longitude center coordinate |
|   **zoom**    | _number_ |  false   | existing |         Map zoom level          |

**Example:**
`curl -X POST -H "Authorization:Bearer <token>" -H "Content-Type: application/json" -d '{"mission":"Test", "zoom":12}' http://localhost:8889/api/configure/updateInitialView`

---

#### GeoDatasets API (`/api/geodatasets`)

Enables programmatic control over GeoDataset layers. GeoDatasets are GeoJSON files uploaded and managed by MMGIS and stored in MMGIS' Postgres/PostGIS database.

##### GET /get

Queries a geodataset and returns geojson or vectortiles.

|   Parameter   |   Type    | Required | Default |                        Description                         |
| :-----------: | :-------: | :------: | :-----: | :--------------------------------------------------------: |
|   **layer**   | _string_  |   true   |   N/A   |                   Geodataset layer name                    |
|   **type**    | _string_  |   true   |   N/A   | Format to return. 'geojson' or 'mvt' (Mapbox Vector Tiles) |
|   **minx**    | _number_  |  false   |   N/A   |   Minimum X (lng) value for a bounding-box extent query    |
|   **miny**    | _number_  |  false   |   N/A   |   Minimum Y (lat) value for a bounding-box extent query    |
|   **maxx**    | _number_  |  false   |   N/A   |   Maximum X (lng) value for a bounding-box extent query    |
|   **maxy**    | _number_  |  false   |   N/A   |   Maximum Y (lat) value for a bounding-box extent query    |
| **startProp** | _string_  |  false   |   N/A   |        Name of key of feature's start time property        |
| **starttime** |  _time_   |  false   |   N/A   |             Start time of time window to query             |
|  **endProp**  | _string_  |  false   |   N/A   |         Name of key of feature's end time property         |
|  **endtime**  |  _time_   |  false   |   N/A   |              End time of time window to query              |
|     **x**     | _integer_ |  false   |   N/A   |               If type=mvt, x of tile to get                |
|     **y**     | _integer_ |  false   |   N/A   |               If type=mvt, y of tile to get                |
|     **z**     | _integer_ |  false   |   N/A   |               If type=mvt, z of tile to get                |

##### POST /entries

Lists out available geodatasets and their last updated dates.

##### POST /search

Returns all features that match a geojson `properties` property key's value.

##### POST /append/:name

Append geojson features to an existing geodataset.

##### POST /recreate

Creates or replaces an existing geodataset with a new geojson.

##### DELETE /remove/:name

Removes a geodataset.

---

#### Other Backend Endpoints

An incomplete list of other supported backend endpoints.

##### Users Endpoints
- `POST /api/users/login`
- `POST /api/users/signup`
- `GET /api/users/logged_in`
- `POST /api/users/logout`

##### Utility Endpoints
- `POST /api/utils/getbands`
- `POST /api/utils/getprofile`
- `GET /api/utils/queryTilesetTimes`

##### Draw Endpoints
- `POST /api/draw/add`
- `POST /api/draw/edit`
- `POST /api/draw/remove`

##### Files Endpoints
- `POST /api/files/getfiles`
- `GET /api/files/compile`

##### URL Shortener Endpoints
- `POST /api/shortener/shorten`
- `POST /api/shortener/expand`

---

## 2. Frontend JavaScript API (`window.mmgisAPI`)

The Frontend JavaScript API is the **primary method for controlling the user interface** and interacting with the map in real-time. It is exposed through the global `window.mmgisAPI` object.

### Main API (`window.mmgisAPI`)

High-level functions for controlling layers, time, events, and more.

#### Layer Control

- `addLayer(layerObj, placement)`: Adds a layer to the map client-side.
- `removeLayer(layerUUID)`: Removes a layer from the map.
- `clearVectorLayer(layerUUID)`: Clears an existing vector layer.
- `updateVectorLayer(layerUUID, inputData)`: Updates an existing vector layer with GeoJSON data.
- `toggleLayer(layerUUID, on)`: Sets the visibility state for a named layer.
- ...and more for trimming, reloading, etc.

#### Time Control

- `toggleTimeUI(visibility)`: Toggles the visibility of ancillary Time Control User Interface.
- `setTime(startTime, endTime, ...)`: Sets the global time properties for all of MMGIS.
- `setLayerTime(layer, startTime, endTime)`: Sets the start and end time for a single layer.
- `getTime()`: Returns the current time on the map.
- ...and more for getting start/end times, reloading time layers, etc.

#### Event Listeners

- `addEventListener(eventName, functionReference)`: Adds a map event (`onPan`, `onZoom`, `onClick`) or MMGIS action listener (`toolChange`, `layerVisibilityChange`).
- `removeEventListener(eventName, functionReference)`: Removes a listener.

#### Map Feature Information

- `map`: Exposes the Leaflet map object.
- `featuresContained()`: Returns an array of all features in the current map view.
- `getActiveFeature()`: Returns the currently active (clicked) feature.
- `selectFeature(layerUUID, options)`: Programmatically selects a vector layer feature.
- `getVisibleLayers()`: Returns an object with the visibility state of all layers.
- `getLayers()`: Returns all the configuration set Leaflet Map layers.
- `getLayerConfigs(match)`: Returns all the layer configuration objects, with optional filtering.

#### Miscellaneous Features

- `writeCoordinateURL()`: Returns the long-form "Copy Link" URL.
- `onLoaded(onLoadCallback)`: Calls the callback function once MMGIS has finished loading.
- `getActiveTool()`: Returns the currently active tool.
- `project(lnglat)`: Converts Lng/Lat to X/Y coordinates.
- `unproject(xy)`: Converts X/Y to Lng/Lat coordinates.

### Utility Functions (`window.mmgisAPI.utils`)

A collection of helper functions for data manipulation, geometry calculations, and more, available under `window.mmgisAPI.utils`.

#### Object Manipulation
- `clone(obj)`: Deep clones an object.
- `getIn(obj, keyArray, notSetValue)`: Traverses a nested object with a key path.

#### String Manipulation
- `bracketReplace(str, obj)`: Populates `{bracketed}` parameters in a string.
- `fileNameFromPath(path)`: Extracts a filename from a path.

#### Geo-Spatial
- `bearingBetweenTwoLatLngs(lat1, lng1, lat2, lng2)`: Finds the bearing from one point to another.
- `lngLatDistBetween(lon1, lat1, lon2, lat2)`: Calculates the distance in meters between two points.

...and many more. Refer to the source or full docs for a complete list.


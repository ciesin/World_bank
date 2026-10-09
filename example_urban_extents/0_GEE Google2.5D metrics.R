##############################################################
### World Bank Contract 2026                               ###
### Settlements: Density Bands                            ###
### By: Dana R. Thomson d.r.thomson@ciesin.columbia.edu    ###
### Purpose: Documenting my GEE script for extracting Google2.5D metrics  ###
### Last updated: 27 Jun 2026                              ###
##############################################################



#################################################################################
# PART 1: Generate impervious surface masks and bounding boxes around cities
#################################################################################

# GEE (part 2) assumes you've already created extent windows aroudn the AOI

extent_polys_wgs84


#################################################################################
# PART 2: MOVED TO GEE AND COMPUTED GOOGLE2.5D METRICS 
#################################################################################

# 
# // -------------------------------------------------------------------
#   // Google Open Buildings 2.5D Temporal metrics
# // 250 m moving-window summaries for pilot city WSF Tracker extents
# // -------------------------------------------------------------------
#   
#   
#   // -------------------------------------------------------------------
#   // 1. User settings
# // -------------------------------------------------------------------
#   
#   // Your uploaded WSF Tracker extent polygons
# var cities = ee.FeatureCollection(
#   'projects/density-bands-3-cities/assets/WSF_Tracker_raster_extents_pilot_cities'
# );
# 
# // City-name field in the asset
# var cityField = 'city';
# 
# // Pilot cities
# var cityNames = ['Juba', 'Kigali', 'Johannesburg'];
# 
# // Google Open Buildings 2.5D year
# // Latest available annual layer is 2023
# var year = 2023;
# 
# // Output projection and scale
# // EPSG:3857 is convenient for meter-based moving windows.
# // For final area/statistics, you can later reproject to an equal-area CRS.
# var crs = 'EPSG:3857';
# var outScale = 100;       // output pixel size in meters
# var windowRadius = 250;   // moving-window radius in meters
# 
# // Threshold for "high building presence" when summarizing height
# var presenceThreshold = 0.5;
# 
# // Google Drive export folder
# var exportFolder = 'GEE_exports';
# 
# 
# // -------------------------------------------------------------------
#   // 2. Load Google Open Buildings 2.5D Temporal
# // -------------------------------------------------------------------
#   
#   var ob = ee.ImageCollection('GOOGLE/Research/open-buildings-temporal/v1')
# .filterDate(year + '-01-01', (year + 1) + '-01-01');
# 
# 
# // -------------------------------------------------------------------
#   // 3. Processing function for one city
# // -------------------------------------------------------------------
#   
#   function processCity(cityName) {
#     
#     // ---------------------------------------------------------------
#       // AOI
#     // ---------------------------------------------------------------
#       
#       var cityFeature = cities.filter(ee.Filter.eq(cityField, cityName)).first();
#       var aoi = ee.Feature(cityFeature).geometry();
#       
#       // Buffer AOI so the 250 m moving window has context near edges,
#       // then clip final outputs back to original AOI.
#       var aoiBuffered = aoi.buffer(windowRadius);
#       
#       // ---------------------------------------------------------------
#         // Load/mosaic Open Buildings 2.5D for this city/year
#       // ---------------------------------------------------------------
#         
#         var obCityRaw = ob
#       .filterBounds(aoiBuffered)
#       .mosaic()
#       .clip(aoiBuffered);
#       
#       // Force a valid default projection before reduceResolution().
#       var obCity = obCityRaw
#       .setDefaultProjection({
#         crs: crs,
#         scale: 4
#       });
#       
#       var bldgCount = obCity.select('building_fractional_count');
#       var presence = obCity.select('building_presence');
#       var height = obCity.select('building_height');
#       
#       // ---------------------------------------------------------------
#         // Create 100 m base layers from the native Google 2.5D data
#       // ---------------------------------------------------------------
#         // These preserve source information by aggregating the native/fine
#       // pixels into a coarser working grid before calculating 250 m windows.
#       
#       // Estimated building count per 100 m cell:
#         // sum of fractional building count.
#       var count100m = bldgCount
#       .reduceResolution({
#         reducer: ee.Reducer.sum(),
#         maxPixels: 65535
#       })
#       .reproject({
#         crs: crs,
#         scale: outScale
#       })
#       .rename('bldg_count_100m');
#       
#       // Mean building presence per 100 m cell.
#       var presence100m = presence
#       .reduceResolution({
#         reducer: ee.Reducer.mean(),
#         maxPixels: 65535
#       })
#       .reproject({
#         crs: crs,
#         scale: outScale
#       })
#       .rename('presence_mean_100m');
#       
#       // Building height only where presence is high enough.
#       var heightHighPresence = height.updateMask(presence.gte(presenceThreshold));
#       
#       // Mean height among high-presence building pixels per 100 m cell.
#       var height100m = heightHighPresence
#       .reduceResolution({
#         reducer: ee.Reducer.mean(),
#         maxPixels: 65535
#       })
#       .reproject({
#         crs: crs,
#         scale: outScale
#       })
#       .rename('height_mean_100m');
#       
#       // Rough volume / vertical-intensity proxy at native scale:
#         // fractional building count * height.
#       var volumeProxyNative = bldgCount
#       .multiply(heightHighPresence)
#       .rename('bldg_count_x_height');
#       
#       // Sum of building_fractional_count * building_height per 100 m cell.
#       var volumeProxy100m = volumeProxyNative
#       .reduceResolution({
#         reducer: ee.Reducer.sum(),
#         maxPixels: 65535
#       })
#       .reproject({
#         crs: crs,
#         scale: outScale
#       })
#       .rename('volume_proxy_100m');
#       
#       // ---------------------------------------------------------------
#         // 250 m moving-window summaries
#       // ---------------------------------------------------------------
#         
#         var kernel250m = ee.Kernel.circle({
#           radius: windowRadius,
#           units: 'meters'
#         });
#       
#       // Metric 1:
#         // Sum of building_fractional_count in 250 m moving window.
#       var count250m = count100m
#       .reduceNeighborhood({
#         reducer: ee.Reducer.sum(),
#         kernel: kernel250m
#       })
#       .rename('sum_bldg_frac_count_250m');
#       
#       // Useful derived density:
#         // estimated buildings per km2 in 250 m moving window.
#       var windowAreaKm2 = Math.PI * Math.pow(windowRadius / 1000, 2);
#       
#       var density250m = count250m
#       .divide(windowAreaKm2)
#       .rename('bldg_density_km2_250m');
#       
#       // Metric 2a:
#         // Mean building_presence in 250 m moving window.
#       var presenceMean250m = presence100m
#       .reduceNeighborhood({
#         reducer: ee.Reducer.mean(),
#         kernel: kernel250m
#       })
#       .rename('mean_presence_250m');
#       
#       // Metric 2b:
#         // High-percentile building_presence in 250 m moving window.
#       // This is useful where mean presence gets diluted by open spaces.
#       var presenceP90_250m = presence100m
#       .reduceNeighborhood({
#         reducer: ee.Reducer.percentile([90]),
#         kernel: kernel250m
#       })
#       .rename('p90_presence_250m');
#       
#       // Metric 3:
#         // Mean building_height where building presence is high.
#       var heightMean250m = height100m
#       .reduceNeighborhood({
#         reducer: ee.Reducer.mean(),
#         kernel: kernel250m
#       })
#       .rename('mean_height_presence_gt_0p5_250m');
#       
#       // Metric 4:
#         // Sum of building_fractional_count * building_height in 250 m window.
#       var volumeProxy250m = volumeProxy100m
#       .reduceNeighborhood({
#         reducer: ee.Reducer.sum(),
#         kernel: kernel250m
#       })
#       .rename('sum_bldg_count_x_height_250m');
#       
#       // ---------------------------------------------------------------
#         // Combine metrics into one compact multi-band image
#       // ---------------------------------------------------------------
#         
#         var metrics250m = ee.Image.cat([
#           count250m,
#           density250m,
#           presenceMean250m,
#           presenceP90_250m,
#           heightMean250m,
#           volumeProxy250m
#         ])
#       .clip(aoi)
#       .float();
#       
#       // ---------------------------------------------------------------
#         // Add map layers for inspection
#       // ---------------------------------------------------------------
#         
#         Map.addLayer(
#           density250m.clip(aoi),
#           {min: 0, max: 1000},
#           cityName + ' bldg density/km2, 250m'
#         );
#       
#       Map.addLayer(
#         presenceMean250m.clip(aoi),
#         {min: 0, max: 1},
#         cityName + ' mean presence, 250m'
#       );
#       
#       Map.addLayer(
#         heightMean250m.clip(aoi),
#         {min: 0, max: 20},
#         cityName + ' mean height, 250m'
#       );
#       
#       Map.addLayer(
#         volumeProxy250m.clip(aoi),
#         {min: 0, max: 5000},
#         cityName + ' volume proxy, 250m'
#       );
#       
#       // ---------------------------------------------------------------
#         // Export multi-band raster to Google Drive
#       // ---------------------------------------------------------------
#         
#         Export.image.toDrive({
#           image: metrics250m,
#           description: cityName + '_Google2p5D_metrics_250mWindow_' + year,
#           folder: exportFolder,
#           fileNamePrefix: cityName + '_Google2p5D_metrics_250mWindow_' + year,
#           region: aoi,
#           scale: outScale,
#           crs: crs,
#           maxPixels: 1e13
#         });
#       
#       return metrics250m;
#   }
# 
# 
# // -------------------------------------------------------------------
#   // 4. Run for each city
# // -------------------------------------------------------------------
#   
#   print('Cities asset:', cities);
# print('First feature:', cities.first());
# 
# Map.centerObject(cities, 7);
# Map.addLayer(cities, {}, 'WSF Tracker raster extents');
# 
# processCity('Juba');
# processCity('Kigali');
# processCity('Johannesburg');


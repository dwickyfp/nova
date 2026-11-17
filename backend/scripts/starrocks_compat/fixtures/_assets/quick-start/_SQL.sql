-- quickstart.crashdata and quickstart.weatherdata (schemas from _DDL.mdx), a few rows around the hours the page prints.
CREATE DATABASE IF NOT EXISTS quickstart;
CREATE TABLE quickstart.crashdata (
    CRASH_DATE DATETIME,
    BOROUGH STRING,
    ZIP_CODE STRING,
    LATITUDE INT,
    LONGITUDE INT,
    LOCATION STRING,
    ON_STREET_NAME STRING,
    CROSS_STREET_NAME STRING,
    OFF_STREET_NAME STRING,
    CONTRIBUTING_FACTOR_VEHICLE_1 STRING,
    CONTRIBUTING_FACTOR_VEHICLE_2 STRING,
    COLLISION_ID INT,
    VEHICLE_TYPE_CODE_1 STRING,
    VEHICLE_TYPE_CODE_2 STRING
) DUPLICATE KEY(CRASH_DATE) DISTRIBUTED BY HASH(COLLISION_ID) BUCKETS 1
PROPERTIES ("replication_num" = "1");
CREATE TABLE quickstart.weatherdata (
    DATE DATETIME,
    NAME STRING,
    HourlyDewPointTemperature STRING,
    HourlyDryBulbTemperature STRING,
    HourlyPrecipitation STRING,
    HourlyPresentWeatherType STRING,
    HourlyPressureChange STRING,
    HourlyPressureTendency STRING,
    HourlyRelativeHumidity STRING,
    HourlySkyConditions STRING,
    HourlyVisibility STRING,
    HourlyWetBulbTemperature STRING,
    HourlyWindDirection STRING,
    HourlyWindGustSpeed STRING,
    HourlyWindSpeed STRING
) DUPLICATE KEY(DATE) DISTRIBUTED BY RANDOM BUCKETS 1
PROPERTIES ("replication_num" = "1");
INSERT INTO quickstart.crashdata (CRASH_DATE, BOROUGH, ZIP_CODE, COLLISION_ID) VALUES
    ('2014-01-06 08:12:00', 'BROOKLYN', '11201', 1),
    ('2014-01-06 08:40:00', 'QUEENS', '11368', 2),
    ('2014-01-07 09:05:00', 'MANHATTAN', '10001', 3),
    ('2014-02-03 08:15:00', 'BRONX', '10451', 4),
    ('2014-02-03 08:30:00', 'BROOKLYN', '11215', 5),
    ('2014-02-03 09:20:00', 'QUEENS', '11101', 6);
INSERT INTO quickstart.weatherdata (DATE, NAME, HourlyDryBulbTemperature, HourlyPrecipitation, HourlyVisibility) VALUES
    ('2014-01-06 08:51:00', 'LAGUARDIA AIRPORT, NY US', '40', '0.00', '10.00'),
    ('2014-01-07 09:51:00', 'LAGUARDIA AIRPORT, NY US', '8', '0.00', '10.00'),
    ('2014-02-03 08:51:00', 'LAGUARDIA AIRPORT, NY US', '32', '0.12', '0.25'),
    ('2014-02-03 09:51:00', 'LAGUARDIA AIRPORT, NY US', '32', '0.12', '0.25');

cmake_minimum_required(VERSION 3.15)
project(satellite_simulator CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Eclipse Paho MQTT C++ (pulls in Paho MQTT C automatically)
find_package(PahoMqttCpp REQUIRED)

add_executable(satellite_simulator src/satellite_simulator.cpp)
target_link_libraries(satellite_simulator PRIVATE PahoMqttCpp::paho-mqttpp3)

set_target_properties(satellite_simulator PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin)

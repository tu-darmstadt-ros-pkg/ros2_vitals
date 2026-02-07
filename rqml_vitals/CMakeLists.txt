cmake_minimum_required(VERSION 3.15)
project(rqml_vitals)

find_package(ament_cmake REQUIRED)

# Install QML files
install(DIRECTORY qml DESTINATION share/${PROJECT_NAME})

# Register the plugin resource
ament_index_register_resource(rqml_plugin CONTENT_FILE rqml_plugins.yaml.in)

ament_package()

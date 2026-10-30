cmake_minimum_required(VERSION 3.15)
project(rqml_vitals)

find_package(ament_cmake REQUIRED)

# Install QML files
install(DIRECTORY qml DESTINATION share/${PROJECT_NAME})

# Register the plugin resource
ament_index_register_resource(rqml_plugin CONTENT_FILE rqml_plugins.yaml.in)

if(BUILD_TESTING)
  find_package(Qt6 REQUIRED COMPONENTS Core Quick QuickTest)
  set(CMAKE_AUTOMOC ON)
  find_package(qml6_ros2_plugin REQUIRED)
  find_package(rqml_core REQUIRED)

  # The QtTest QML module is not pulled in by qt6-declarative-dev on every distribution (e.g. noble)
  # and has no rosdep key, so skip the QML tests instead of failing when it is missing.
  find_path(
    QTTEST_QML_MODULE_DIR QtTest/qmldir
    HINTS "${QT6_INSTALL_PREFIX}/${QT6_INSTALL_QML}"
    PATHS "/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}/qt6/qml"
  )
  if(NOT QTTEST_QML_MODULE_DIR)
    message(WARNING "QtTest QML module not found (install qml6-module-qttest), skipping QML tests")
  else()
    # Build mock Ros2 module in the build tree.
    # Overrides the Ros2 module so the installed qml6_ros2_plugin is not loaded. Mock C++ types
    # (Subscription, ServiceClient, ...) are registered in the test harness (test_plugins.cpp),
    # which is shared with rqml_default_plugins.
    set(MOCK_ROS2_DIR "${CMAKE_CURRENT_BINARY_DIR}/test_mocks/Ros2")
    file(MAKE_DIRECTORY "${MOCK_ROS2_DIR}")
    file(GLOB _MOCK_FILES "${CMAKE_CURRENT_SOURCE_DIR}/test/mocks/Ros2/*")
    foreach(mock_file ${_MOCK_FILES})
      get_filename_component(_fname "${mock_file}" NAME)
      file(CREATE_LINK
              "${mock_file}" "${MOCK_ROS2_DIR}/${_fname}"
              COPY_ON_ERROR SYMBOLIC)
    endforeach()

    add_executable(test_plugins
      test/test_plugins.cpp
      test/mock_ros2.cpp
      test/mock_classes.cpp
    )
    target_compile_definitions(test_plugins
      PRIVATE
      QUICK_TEST_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/test"
      RQML_CORE_QML_DIR="${rqml_core_DIR}/.."
    )
    target_link_libraries(test_plugins
      PRIVATE
      Qt6::QuickTest
      Qt6::Qml
      Qt6::Quick
      qml6_ros2_plugin::qml6_ros2_plugin
    )
    set(TEST_RESULT_DIR "${CMAKE_BINARY_DIR}/test_results/${PROJECT_NAME}")
    ament_add_test(
      test_plugins
      COMMAND $<TARGET_FILE:test_plugins>
          -import "${CMAKE_CURRENT_BINARY_DIR}/test_mocks"
          -import "${CMAKE_CURRENT_SOURCE_DIR}/qml"
          -junitxml
          -o "${TEST_RESULT_DIR}/test_plugins.xml"
      ENV QT_QPA_PLATFORM=offscreen
      RESULT_FILE "${TEST_RESULT_DIR}/test_plugins.xml"
      TIMEOUT 300
    )
  endif()
endif()

ament_package()

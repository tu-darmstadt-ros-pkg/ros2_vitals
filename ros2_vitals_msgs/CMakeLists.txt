cmake_minimum_required(VERSION 3.15)
project(ros2_vitals_msgs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/GpuStatus.msg"
  "msg/NetworkInterface.msg"
  "msg/DiskStatus.msg"
  "msg/ProcessStatus.msg"
  "msg/SystemStatus.msg"
  "srv/KillProcess.srv"
  DEPENDENCIES builtin_interfaces
)

ament_package()

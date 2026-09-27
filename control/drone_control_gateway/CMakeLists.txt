cmake_minimum_required(VERSION 3.8)
project(drone_control_gateway)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(px4_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Intent.msg"
  DEPENDENCIES builtin_interfaces
)

add_library(intent_mapper src/intent_mapper.cpp)
target_compile_features(intent_mapper PUBLIC cxx_std_17)
target_include_directories(intent_mapper PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

add_library(demo_safety_limiter src/demo_safety_limiter.cpp)
target_compile_features(demo_safety_limiter PUBLIC cxx_std_17)
target_include_directories(demo_safety_limiter PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)

add_executable(control_gateway_node src/control_gateway_node.cpp)
target_compile_features(control_gateway_node PUBLIC cxx_std_17)
target_link_libraries(control_gateway_node intent_mapper demo_safety_limiter)
ament_target_dependencies(control_gateway_node px4_msgs rclcpp)
rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} rosidl_typesupport_cpp)
target_link_libraries(control_gateway_node "${cpp_typesupport_target}")

install(TARGETS intent_mapper demo_safety_limiter control_gateway_node
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY config launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_intent_mapper test/test_intent_mapper.cpp)
  target_link_libraries(test_intent_mapper intent_mapper)
  target_include_directories(test_intent_mapper PRIVATE include)
  ament_add_gtest(test_demo_safety_limiter test/test_demo_safety_limiter.cpp)
  target_link_libraries(test_demo_safety_limiter demo_safety_limiter intent_mapper)
  target_include_directories(test_demo_safety_limiter PRIVATE include)
endif()

ament_export_include_directories(include)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
